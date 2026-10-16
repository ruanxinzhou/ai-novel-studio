# Task 10: ChapterBriefModal（章前 5 分钟）+ DeskView 精简

## 重要设计决策（用户拍板，贯穿本任务及以后所有任务）
> "不能限制死——标签/卡片/方向必须支持重新抽卡/加提示词迭代/自由编辑。全局聊天浮窗不够，重抽/自编/微调必须在卡片自身完成。"

从本任务开始，所有 AI 候选卡（包括章前方向卡）都必须支持：
- 🔄 再抽一版（可带提示词 `directive`）
- ✏️ 我自己写（弹输入框直接写/覆盖）
- 📝 基于选中版微调（选中后弹编辑框，可加提示词让 AI 再改一版）
- 选版只是"当前倾向哪一版"，永远可以推翻重来

全局 AIChatFloat 保留为跨阶段顾问，**阶段内迭代操作直接在卡片上完成**。

本次任务先在 ChapterBriefModal 里贯彻这一模式；SushiCards ensemble 模式扩展（支持每卡 🔄/✏️ 按钮 + regenerate-card 事件）在本任务同步完成，以便后续 T11+ 直接用。之前 T4-T9 视图的回补放到 Final 前的 polish 提交统一处理。

## 后端扩展

### 1. `backend/app/api/brief_routes.py` 升级

现有桩端点 `POST /api/projects/{pid}/volumes/{vol_no}/next-chapter-brief` 返回静态 3 个选项。改为真实 LLM 调用 + 支持 reroll/custom。

需要新增依赖注入：`chore_provider`（和其他路由一致）。修改 register_brief_routes 签名加入 `chore_provider`，main.py 注册时补上。

**端点改造：**

#### `POST /api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief/options`（新）
- 请求体：`{ directive?: string, base_index?: number|null, custom_writing?: string|null }`
  - 什么都不传 → 生成 3 版方向卡
  - 传 directive → 基于 directive 重新抽 3 版
  - 传 base_index + directive → 基于第 base_index 版微调 3 版（新的 3 版不要完全一样，要有差异）
  - custom_writing 非空 → 直接返回"自编版"（不调 LLM）：返回 `[{id:"custom", title:"我自己写的", summary:custom_writing, is_custom:true}]`，同时写入 brief.custom_draft
- 逻辑：
  - 校验卷存在、desk.ensure(cno)
  - 如果是卷内第 1 章且 desk 里还没有 cno 这一章，自动 ensure 创建
  - 从 volume 读：rhythm_blueprint（找 chapter_plan 里 chapter==cno 那一项 → 得到 day/focus/beat_type/words）；death_nodes/climax_nodes/clue_nodes 中 day 匹配的项；上一章（cno-1）结尾（desk.get(cno-1).polish.text 最后 500 字，如果存在）
  - 调 detector（settle 模块）拿到当前到期伏笔列表（planted 或 overdue）
  - 调用 LLM 生成 3 版方向卡，返回 JSON：
    ```json
    {
      "options": [
        {"id":"a","title":"…5~10字…","summary":"…80~120字本章方向…","hooks":"…20~40字章尾钩子…","day":X,"beat_type":"hook/sell/engine/climax/cooldown","foreshadow_advance":["伏笔1","伏笔2"],"entities_on_stage":["角色名","地点名"]},
        {"id":"b",...},
        {"id":"c",...}
      ]
    }
    ```
  - 不需要 SSE（单次结构化返回，快）
  - **Mock 短路**：和其他模块一致，当 `vol.adapted_module 以"测试副本核文本"开头 或 host_character.name 含"林薇"` 时，不调 LLM，直接返回 3 版硬编码方向卡（克苏鲁基调，基于 day/focus 合理生成）。
  - 返回：`{ options, day, chapter_words_estimate, is_mock }`

#### `POST /api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief/select`（新，替代现有 PATCH 的主要入口）
- 请求体：`{ option_index: number, extra_edits?: string, custom_text?: string|null }`
  - option_index = 0/1/2：选中某一版方向卡
  - custom_text 非空：用自定义文本作为选中方向（is_custom=true 场景）
  - extra_edits：用户对选中卡的手动修改文本
- 写入 work.brief = `{selected: <选中对象 dict>, selected_at: 时间戳, extra_edits, chapter_no: cno, day, is_custom: bool}`
- 自动设置 work.field_no = v.field_no（卷绑定）
- 如果 chapter 是首次进入且 work.title 为空，用 `第{cno}章 {selected.title}` 作为默认 title
- 返回：`{ok: true, brief: work.brief, chapter_no: cno}`

#### `PATCH /api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief`（保留）
- 现有 PATCH 端点继续支持任意字段 merge，用于手动编辑 brief 字段。

#### 保留 `POST /next-chapter-brief` 但改成**向后兼容转发**到新 `/brief/options` 路径，不破坏现有调用。

### 2. Desk chapter 自动创建
- 首次进入 phase=rhythm 完成后、第一次进入 briefing 时（即当前卷 chapters 数为 0），由后端或前端自动创建 chapter 1：
  - 最简单做法：前端 RhythmView finalize 之后 loadPhase()；phase_routes._determine_phase 检查到 stage_completed.rhythm=True 且无 chapters，返回 phase='briefing'，chapter_no=1；desk.get(1) 会 ensure 创建。
  - 需要确认 DeskStore.ensure 是否会在 get() 时自动创建。读代码确认：wizard/store.py 里 DeskStore 应该有 ensure 方法。如果 get(1) 返回 None，在 brief/options 里调 desk.ensure(cno) 即可（已在上面设计）。

### 3. Chapter finalize 后判定逻辑
- 现有 deskApi.finalize 是通用的。新的"卷结束判定"在后端做：
  - finalize 后检查：如果 volume.rhythm_blueprint.suggested_chapter_range[1] >= desk.all_nos() 的 max，且最新 finalized 的章是该上限 → 那么下一个 chapter 不需要自动创建，让 phase 流转到 settle
  - 否则 → 自动创建下一章（desk.ensure(cno+1)），phase 流转到 briefing（新章的 brief 为空）
- **实现方式**：在现有 finalize 逻辑里加一段：如果请求里带 `auto_next=true`（默认 true），按上述规则处理。简单起见，在 `wizard/desk.py` 的 finalize_chapter 函数里或 brief_routes 里加一个"完成本章、准备下章"的辅助函数。
- 注：不要大改 finalize_chapter 本体，在 brief_routes 里加一个 POST `/chapters/{cno}/brief/advance` 端点：
  - body: `{finalized: true}`
  - finalized=true 时：根据 rhythm_blueprint 判断是否到卷末。没到 → desk.ensure(cno+1)，返回 {next_chapter: cno+1, phase:'briefing'}；到了 → 返回 {next_chapter: null, phase:'settle'}
  - 前端 finalize 成功后调这个端点决定下一步。

### 4. Detector 复用
brief/options 需要到期伏笔列表。直接 `from ..settle.detector import detect_foreshadows`（如果 detector 函数签名可用），或者调 settle_routes 的内部逻辑。如果 detector 需要太多上下文，简化做法：直接从 vol 和之前章节的 markers/foreshadow 字段聚合；或者直接在 prompt 里不带伏笔，让 AI 基于 core_twist/rhythm_blueprint 自己推，不阻塞。

### 5. PLANNING_FIELDS 无需新增字段（rhythm_blueprint 已加）。volume 不需要加新字段。

## 前端

### 1. SushiCards 组件增强（`frontend/src/components/SushiCards.vue`）

在 ensemble 模式下增加：

**新 props：**
- `allowCustom?: boolean`（默认 true）：显示"✏️ 自己写"按钮
- `customLabel?: string`（默认"我自己写一版"）
- `rerunLabel?: string`（默认"🔄 再抽一版"）

**新 emits：**
- `(e:'rerun', index:number|null, directive:string)`：index=null 是整盘重抽；index=数字是"基于这一版微调"
- `(e:'custom-write')`：作者要自己写一版（父组件弹输入框）
- `(e:'edit-card', index:number)`：作者要就地编辑某一版文本

**UI（ensemble 模式）：**
- 每张卡右上角悬浮显示两个小圆形按钮（hover 时更明显，opacity 0.5 → 1）：🔄（基于此版微调）和 ✏️（编辑此版）
- 卡片底部区域或整个 sushi-cards 工具栏追加：
  - "🔄 全部重抽"按钮（调 emit('rerun', null, directive)；旁边有小 input 可输入提示词，或点按钮弹 el-popover 输入提示词）
  - "+ 我自己写一版"按钮（emit('custom-write')），作者输入文本后作为一张新卡插入 items，id='custom-{timestamp}'，并可被 pick

**向后兼容**：diverge 模式逻辑完全不动；ensemble 新增 prop 默认值保证不破坏现有 T4-T9 视图。

### 2. 新建 `frontend/src/components/ChapterBriefModal.vue`

一个 el-dialog（模态），props：
- `pid: string`
- `volNo: number`
- `chapterNo: number`
- `visible: boolean`（v-model）

emits：
- `@update:visible`
- `@selected`（选中方向后触发，父组件刷新阶段）

**内容分两块：**
- 上方信息条：显示"第 X 章 · 第 Y 天 · {beat_type} · 目标 ~{words} 字"，以及上一章章尾（如果有）和到期伏笔 tags
- 下方 SushiCards（mode=ensemble, plateCount=3）展示 3 版方向卡
  - itemTitleField='title', itemSubtitleField='hooks', itemContentField='summary'
  - 每张卡底部额外显示 beat_type el-tag 和 foreshadow_advance small 文字
  - 顶部有 directive 输入框 + 🔄 重抽按钮
  - 底部有"+ 我自己写"按钮：点了弹 el-popover/el-dialog 的 el-input type="textarea"，写好后作为一张自定义卡插入
  - 选中某卡后，下方显示一个"对选中版补充修改"的 el-input（extra_edits）
  - 右下角"就按这版开写"el-button type="primary"：调 brief/select → emit('selected') → 关闭 modal

**生命周期：**
- watch(visible, true) 时：进入 modal 自动调 `/brief/options` 加载 3 版；加载中显示 el-skeleton；如果已有 brief.selected 则直接显示选中版并允许改选。
- rerun 事件：调 `/brief/options` 传 directive 或 base_index，刷新 items。
- custom-write 事件：弹输入框，确定后调 `/brief/options` 传 custom_writing，把返回的 1 条加到 items 或替换 items（自定义模式下 items=[custom_card]）。
- 错误 ElMessage.error。

### 3. DeskView.vue 精简

现有 DeskView 有 893 行，是完整的章台。**不要推倒重写**，保留核心的 plot/draft/polish 三段写作区 + 审稿区 + 定稿按钮，做以下精简：

**删减/隐藏：**
- 删掉顶部"章节大纲"面板（旧的章节大纲在新工作流里由 chapter_plan 替代，不再在章台里展示全局大纲）
- 删掉工具栏里"从会话启动"、"wizard"相关的按钮（如 sessionsApi 引用和新建会话按钮）
- 保留 payoff（兑现规模）、strands（三线打卡）——这两个是章末归账，章台还要用
- 保留 moduleOptions（写作模块选择）——写作时要用
- 保留 review + fixStream（审稿+定点修复）
- 保留 finalize 按钮，但 finalize 成功后：
  - 调 `/chapters/{cno}/brief/advance` (POST {finalized:true})
  - 如果返回 next_chapter 非空 → emit('next-chapter', next_chapter)，父组件 loadPhase → 会弹出 ChapterBriefModal
  - 如果返回 phase=='settle' → emit('to-settle')，父组件 loadPhase 切到杀青

**在 DeskView 顶部加一个"章前卡"状态条：**
- 显示当前章的 brief.selected?.title 和 .summary（只读预览），旁边一个"✏️ 换方向"小按钮，点了 emit('reopen-brief') 让父组件重新打开 ChapterBriefModal。

**DeskView 新增 emits：**
- `@reopen-brief`：重新打开章前卡
- `@next-chapter`：本章定稿、切到下一章
- `@to-settle`：本卷结束、进杀青

### 4. DeskView 的新父容器（HomeView 里）

在 HomeView 中，phase==='desk' 或 phase==='briefing' 时，渲染一个包裹层：
- briefModalVisible ref，默认 false
- onMounted 时如果 phase==='briefing' → briefModalVisible=true
- 渲染 `<DeskView ...>`（当 phase==='desk' 时；phase==='briefing' 时下章台还没内容可写，展示"请先在章前卡选定方向"的占位 + 打开 brief modal 按钮）
- 渲染 `<ChapterBriefModal v-model:visible="briefModalVisible" ...>`
- 监听 DeskView 的 @reopen-brief → briefModalVisible=true
- 监听 @next-chapter → loadPhase()
- 监听 @to-settle → loadPhase()
- 监听 ChapterBriefModal @selected → loadPhase()（会切到 desk）

## HomeView 注册

HomeView 里 phase==='rhythm' 的分支之后，不再给 briefing/desk 单独加 v-else-if，而是加一个 `v-else-if="phase==='briefing' || phase==='desk'"` 分支渲染 DeskContainer（上面描述的包裹层，包含 DeskView + ChapterBriefModal）。

DeskContainer 可以直接在 HomeView 里写，不必单独建文件。

## api.ts 扩展

给 brief 相关的 api 添加/扩展。可以新增 `briefApi`：
```ts
export interface BriefOption {
  id: string
  title: string
  summary: string
  hooks?: string
  day?: number
  beat_type?: string
  foreshadow_advance?: string[]
  entities_on_stage?: string[]
  is_custom?: boolean
}
export const briefApi = {
  options: (pid: string, volNo: number, cno: number, body: { directive?: string; base_index?: number | null; custom_writing?: string | null }) =>
    req<{ options: BriefOption[]; day: number; chapter_words_estimate: number; is_mock?: boolean }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/options`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  select: (pid: string, volNo: number, cno: number, body: { option_index: number; extra_edits?: string; custom_text?: string | null }) =>
    req<{ ok: boolean; brief: any; chapter_no: number }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/select`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  advance: (pid: string, volNo: number, cno: number) =>
    req<{ next_chapter: number | null; phase: string }>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/chapters/${cno}/brief/advance`,
      { method: 'POST', body: JSON.stringify({ finalized: true }) },
    ),
  patch: (pid: string, volNo: number, cno: number, body: Record<string, unknown>) =>
    req<{ ok: boolean; brief: any }>(
      `/api/projects/${pid}/volumes/${volNo}/chapters/${cno}/brief`,
      { method: 'PATCH', body: JSON.stringify(body) },
    ),
}
```

注意路径：新端点用 `/workbench/volumes/...` 前缀（和其他 workbench 路由一致），原 PATCH 端点保留 `/volumes/...` 旧路径兼容。

## 验收标准

1. 后端 `POST /workbench/volumes/{vol_no}/chapters/{cno}/brief/options` 在 mock 路径下返回 3 版方向卡（host_character.name 含"林薇"），可通过 directive/base_index/custom_writing 参数切换行为。
2. 后端 `/brief/select` 正确写入 chapter.brief.selected，chapter 首次自动创建。
3. 后端 `/brief/advance` 在章节数 < suggested_chapter_range[1] 时返回 next_chapter=cno+1，≥时返回 phase='settle'。
4. 后端 main.py register_brief_routes 调用已补上 chore_provider 参数，uvicorn import 不报错。
5. 前端 SushiCards ensemble 模式新增 rerun/custom-write/edit-card 能力，不破坏 diverge 模式。
6. 前端 ChapterBriefModal：打开时自动加载 3 版；支持重抽（带提示词）、自定义编写、就地编辑、选中+extra_edits 确认。
7. 前端 DeskView 精简后保留 plot/draft/polish+review+finalize 核心流程，顶部显示 brief 摘要+换方向按钮，finalize 后调 briefApi.advance。
8. 前端 HomeView 里 briefing/desk 阶段正确显示，brief modal 在 phase=briefing 时自动弹出，选完切到 desk；本章定稿后自动弹下一章 brief modal 或切到 settle。
9. 视觉风格：克苏鲁暗色，和其他视图一致。无 console.log 调试。
10. `cd backend; .\.venv\Scripts\python.exe -c "from app.api.main import app; print('OK', len(app.routes))"` 通过。

## Commit message
`feat(fe+be): ChapterBriefModal 章前5分钟方向卡（3版AI+重抽+自编+微调）+ DeskView 精简 + SushiCards 迭代能力`

## 报告文件
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-10-report.md`，写 DONE 状态、commit hash、改动文件、验证结果、遗留。

不要 git push。
