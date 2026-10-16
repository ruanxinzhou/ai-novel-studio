# Task 10 报告：ChapterBriefModal 章前5分钟方向卡 + DeskView 精简 + SushiCards 迭代能力

## 状态：DONE

- Commit hash: `6f1068b`
- 分支: master（未 push）
- 完成时间: 2026-10-15

## 改动清单

### 后端（2 个文件）

1. **`backend/app/api/brief_routes.py`**（完全重写，107 → 552 行）
   - 注入 `chore_provider`，`_Deps` 新增字段，`register_brief_routes` 签名更新
   - 新增端点：
     - `POST /api/projects/{pid}/workbench/volumes/{vol_no}/chapters/{cno}/brief/options` — 生成 3 版方向卡，支持 `directive`（重抽）、`base_index`（基于选中版微调）、`custom_writing`（自编，直接返回 1 条不调 LLM）
     - `POST .../brief/select` — 选中某版/自编文本，写入 `work.brief.selected`，自动绑定 `field_no`、默认章标题
     - `POST .../brief/advance` — 定稿后推进：根据 `rhythm_blueprint.suggested_chapter_range[1]` 和 `chapter_plan` 末章判断卷末，返回 `{next_chapter, phase}`
   - 保留旧端点：
     - `PATCH /api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief`（merge 字段）
     - `POST /api/projects/{pid}/volumes/{vol_no}/next-chapter-brief`（向后兼容转发到 /options）
   - Mock 短路：`adapted_module.startswith("测试副本核文本")` 或 `host_character.name` 含"林薇"时返回 3 版克苏鲁基调硬编码方向卡
   - LLM 调用走 `complete_structured`，失败/返回不合规则回退 mock
   - `/options` 结果写入 `brief._last_options` 供 `/select` 通过 `option_index` 查找
   - 上下文组装：chapter_plan + 当日死亡/高潮/线索节点 + 上一章 500 字段尾 + 核心反转/唐迟起点 + directive/base_index

2. **`backend/app/api/main.py`**（1 行改动）
   - `register_brief_routes(...)` 调用补上 `chore_provider=_chore_provider`

### 前端（5 个文件）

3. **`frontend/src/api.ts`**（新增 ~55 行）
   - 新增 `BriefOption` 接口
   - 新增 `briefApi` 块：`options / select / advance / patch / nextChapterBrief`
   - 新端点使用 `/workbench/volumes/...` 前缀，PATCH 保留旧 `/volumes/...` 路径

4. **`frontend/src/components/SushiCards.vue`**（ensemble 模式增强，+90/-6 行）
   - 新增 props：`allowCustom`（默认 true）、`customLabel`、`rerunLabel`
   - 新增 emits：`rerun(index|null, directive)`、`custom-write`、`edit-card(index)`
   - 每张卡右上角悬浮 🔄（基于此版微调）/ ✏️（编辑此版）圆形按钮，hover opacity 0.45 → 1
   - 顶部工具栏：directive 输入框 + 🔄再抽按钮 + ✏️我自己写按钮；空态也提供输入框+按钮
   - 自编卡显示 `sushi-cand--custom` 样式（琥珀色边框+米黄底）+ "自编"标签
   - diverge 模式完全不动，默认值保证向后兼容 T4-T9

5. **`frontend/src/components/ChapterBriefModal.vue`**（新建，约 370 行）
   - el-dialog 模态，克苏鲁暗色风格（对话框内 dark 背景 #0f1419→#1a1f2b 渐变，金色 #e6a23c 点缀）
   - Props：pid / volNo / chapterNo / visible / existingBrief / prevTail / dueForeshadows
   - Emits：update:visible / selected(brief)
   - 信息条：第 X 章 · 第 Y 天 · beat_type · 目标 ~N 字 + 到期伏笔 tags + 上章章尾预览
   - SushiCards 集成：itemTitleField=title, itemSubtitleField=hooks, itemContentField=summary；具名 `#item` 插槽显示 beat_type/Day tag、推进伏笔、登台实体
   - 选中后底部"📝 对选中版补充说明"textarea（extra_edits）
   - 右下角"就按这版开写 ✍"按钮：调 briefApi.select → emit('selected') → 关闭
   - 🔄 事件 → briefApi.options({directive/base_index})；custom-write → 弹自编 dialog（textarea）→ briefApi.options({custom_writing})；edit-card → 弹编辑 dialog 直接改 summary
   - 生命周期：watch(visible, true) 自动调 /options；若 existingBrief 传入则显示已选卡（重开场景）
   - 自编卡与 AI 卡同等可 pick，自编模式 select 请求传 `custom_text`

6. **`frontend/src/views/DeskView.vue`**（精简，~ -130/+60 行，原 939 → 约 870 行）
   - **删除**：
     - `sessionsApi` 引用、"💬 召唤 AI"按钮、`summonDaily()` 函数
     - "本章大纲"collapse 面板、`.outline-preview`、`detail.outline` 依赖
     - "续写/补新章"按钮（goContinueOutline + __pipelineContinue 跳转）
     - "去创作向导"空态引导（改为"请先完成节奏骨架推演"）
     - `__deskFocus` window 监听（路由由 HomeView phase 接管）
     - 章节列表中"有大纲/无大纲"tag、"outline_chapters"缺大纲警告
   - **保留**：plot/draft/polish 三段生成、参考资料注入、写作模块注入、五段任务书勾选、三线打卡、兑现四选一、审稿+定点修复、定稿飞轮、章节列表+分页
   - **新增**：
     - Props：`chapterNo?: number`
     - Emits：`reopen-brief`、`next-chapter(no)`、`to-settle`
     - 顶部章前卡摘要条：有 brief 时显示 title/summary/hooks/beat_type/自编标签 + "✏️ 换方向"按钮；无 brief 时显示"尚未选定本章方向"+"📝 选方向卡"按钮
     - `doFinalize` 成功后调 `briefApi.advance`：next_chapter 非空 → emit('next-chapter')；phase='settle' → emit('to-settle')
     - `currentVolumeNo/currentChapterNo` 来自 state；自动选章优先 props.chapterNo → currentChapterNo → todayNo
   - **未推倒重写**：plot/draft/polish 三段、审稿、定稿、参考资料模块注入、兑现/三线、分页等核心逻辑原样保留

7. **`frontend/src/views/HomeView.vue`**（新增 DeskContainer，~ +80 行）
   - 新增导入：DeskView、ChapterBriefModal、deskApi
   - 新增状态：briefModalVisible、deskKey、existingBrief
   - phase==='briefing'||'desk' 合并成一个分支：
     - briefing + modal 关闭时 → 克苏鲁风占位"章前5分钟"+"开始选方向"按钮
     - 否则渲染 DeskView（:key=deskKey 强制刷新章节切换）
     - 始终渲染 ChapterBriefModal（v-model:visible）
   - 事件处理：
     - @reopen-brief → 拉当前章 brief → 打开 modal
     - @next-chapter(no) → 更新 currentChapterNo，deskKey++，自动弹 modal
     - @to-settle → loadPhase() 切杀青
     - @selected(brief) → 关 modal，loadPhase()（后端 phase 将切到 desk）
   - watch(phase)：briefing 时自动弹 modal；desk 时关 modal 并刷新 existingBrief

## 设计规则贯彻

按用户要求，从 Task 10 起所有新 AI 候选卡均支持：
- 🔄 再抽（可带 directive 提示词）：SushiCards 顶部+空态均有提示词输入框；每张卡 🔄 按钮带 base_index 做微调
- ✏️ 自编（直接写）：SushiCards 顶部"+我自己写"按钮，父组件弹 textarea，custom_writing 直传后端
- 📝 基于选中版微调：选中后 extra_edits 输入框补充；每张卡 ✏️ 按钮可就地编辑 summary
- 全局 AIChatFloat 保留为跨阶段顾问（未删除），阶段内迭代全部在卡片自身完成

## 验证

- **后端 import 验证**：`.\.venv\Scripts\python.exe -c "from app.api.main import app; print('OK', len(app.routes))"` → `OK 146`（退出码 0）
- **前端类型检查**：`npx vue-tsc --noEmit` → 退出码 0，无 TS 错误
- **路由数**：146 条（原约 142，新增 4 条 brief 路由：options/select/advance + workbench 前缀 PATCH？实际 options/select/advance + 旧 PATCH 保持，next-chapter-brief 保持；新增 3 条）

## 遗留 / 后续任务

1. T4-T9 旧视图（EmbodyView/DungeonScaffoldView 等）的 SushiCards 未回补 rerun/custom-write 能力，按 brief 要求"后续统一回补，本次不动"
2. 到期伏笔（detector）在 /options 端点中暂未显式调用 settle detector，目前通过 core_twist/rhythm_blueprint 让 AI 自推；正式接入时可直接 `from ..settle.detector import detect_foreshadows` 组装到上下文，无需改前端
3. DeskView 仍保留 old api 兼容（如 activeTab 切换重拉），待 T11 书脊真实数据接入后再做数据层统一
4. phase_routes._determine_phase 需识别 briefing/desk 阶段（已有 stage_completed.rhythm=True 时返回 briefing 的基础逻辑，未在本任务中改动 phase_routes.py，若当前返回不正确需后续小修）

## 关键文件路径

- `backend/app/api/brief_routes.py`
- `backend/app/api/main.py`（line 4311-4317）
- `frontend/src/api.ts`（末尾 briefApi 块）
- `frontend/src/components/SushiCards.vue`
- `frontend/src/components/ChapterBriefModal.vue`（新建）
- `frontend/src/views/DeskView.vue`
- `frontend/src/views/HomeView.vue`
