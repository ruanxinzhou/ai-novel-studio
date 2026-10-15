# Task 8 Brief：DungeonScaffoldView（副本详设 6 卡 AI 预填 + SushiCards 选版 + 落锤）

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

Dev server 应在跑（5173/8321），不在跑则启动，不重启在跑的。

## 目标
魂穿（embody）完成后进入 scaffold 阶段。基于选定的唐迟故事大纲，AI 预填 6 张副本详设卡（死亡节点/高潮节点/线索节点/核心诡计/唐迟起点/代价上限），每卡 3 版候选用 SushiCards ensemble 展示，作者选完锁定；6 卡全部锁定后可进入节奏骨架阶段（rhythm）。

**注意**：Volume 字段里已有 `death_nodes/climax_nodes/clue_nodes/pasted_nodes`（list）和 `scaffold_cards`（dict，包含 obituary_card/entity_card/truth_outline/core_twist/tangchi_start/cost_cap/foreshadows/tangchi_scale）。本任务把两张结构合并使用：
- 结构化的"死亡节点/高潮节点/线索节点"3 张卡，最终值存到对应的 `death_nodes/climax_nodes/clue_nodes` list 字段
- 文本类"核心诡计/唐迟起点/代价上限"3 张卡，存到 `scaffold_cards.core_twist / tangchi_start / cost_cap`
- `scaffold_cards[cardKey]` 存 `{text, locked_at}` 格式
- 6 张卡全部有值且 locked 才标记 stage_completed.scaffold = true

---

## 后端改动

### 1. scaffold_cards/died_nodes/climax/clue PATCH 支持
**文件**：`backend/app/book/volume.py`
- PLANNING_FIELDS 已经有 scaffold_cards（Task 1 加过吗？检查下，没加就加上）；加 `death_nodes`, `climax_nodes`, `clue_nodes`, `pasted_nodes`

**文件**：`backend/app/api/main.py`
- VolumeUpdateIn 加对应字段（都是 list | None = None）

### 2. 新建 `backend/app/api/scaffold_routes.py`
函数式注册，导出 `register_scaffold_routes(app, *, require_project, volumes_for, chore_provider)`

端点：

#### 2a. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/scaffold/prefill`
一次性 AI 预填 6 卡。请求体：`{}`（不需要参数，后端自己从 v 拿 adapted_module/simulated_run/host_character/entry_point/info_gap/stance/tangchi_story_outline）。

逻辑：
- 校验 v.tangchi_story_outline 非空（否则 422 请先完成魂穿）
- 走 JobStore，session_id=`scaff:{pid}:{vol_no}`, kind="scaffold_prefill"
- _work 函数：用 ThreadPoolExecutor 并行调 LLM 为 6 张卡各出 3 版（总共 18 个 LLM 调用？**不要**，太费 token）。改成：单次 LLM 调用让它一次性输出 6 卡 × 3 版 = 18 项结构化 JSON。
- System prompt：你是《第七日的亡者》副本详设师。基于副本核文本+跑团走向+唐迟大纲+魂穿角色，给 6 张详设卡各出 3 版差异化候选。6 张卡：
  1. **死亡节点（death_nodes）**：3 版数组，每版是 [{day, who, how, tease, cost}]，3-5 个死亡/致命事件（克苏鲁基调：死法要有恐怖感，和SAN/代价挂钩）
  2. **高潮节点（climax_nodes）**：3 版数组，每版是 [{day, title, beat, weight:1-5}]，2-4 个高潮转折
  3. **线索节点（clue_nodes）**：3 版数组，每版是 [{day, where, what, leads_to, is_red_herring:bool}]，5-8 条线索/红鲱鱼
  4. **核心诡计（core_twist）**：3 版字符串（100-200字），这卷最大的反转/诡计是什么
  5. **唐迟起点（tangchi_start）**：3 版字符串（80-150字），唐迟穿入时的第一秒看到什么、身体状态、第一个危机是什么
  6. **代价上限（cost_cap）**：3 版字符串（80-150字），这卷结束时唐迟要付出的最大代价（SAN/身体/关系/记忆/蓝星后遗症），留白线下钩子用
- 输出严格 JSON 格式：
  ```json
  {
    "death_nodes": [[{day,who,how,tease,cost}], [{day,...}], [{day,...}]],
    "climax_nodes": [[{day,title,beat,weight}], [...], [...]],
    "clue_nodes": [[{day,where,what,leads_to,is_red_herring}], [...], [...]],
    "core_twist": ["...","...","..."],
    "tangchi_start": ["...","...","..."],
    "cost_cap": ["...","...","..."]
  }
  ```
- 用 complete_structured 解析（参考 jobs_routes outline5）。失败时返回 mock 数据。
- **Mock 短路**：如果 v.adapted_module 以"测试副本核文本"开头或 v.host_character?.name 含"林薇"，sleep(2) 返回硬编码 mock 数据（6 卡 × 3 版都要有，内容带克苏鲁味，死亡节点至少 3 个/版，高潮 2-3 个/版，线索 5 条/版）。
- 返回 `{"ok": True, "job": job.as_dict(), "job_id": job.job_id}`，job.result = {"cards": {...上述 JSON...}}

#### 2b. GET `/jobs` + 2c. GET `/stream`（SSE）
同 adapt/simulate/embody 模式，session_id=`scaff:{pid}:{vol_no}`，3 轮 idle 断开。

#### 2d. POST `/lock-card`
请求体：`{"card_key": "death_nodes"|"climax_nodes"|"clue_nodes"|"core_twist"|"tangchi_start"|"cost_cap", "pick_index": int, "text_or_list": any}`
- 校验 pref-fill job 存在且 done。
- 如果 card_key in {death_nodes, climax_nodes, clue_nodes}（列表卡）：从 prefill.result.cards[card_key][pick_index] 取数组 → `setattr(v, card_key, array)`
- 如果 card_key in {core_twist, tangchi_start, cost_cap}（文本卡）：取字符串 → `v.scaffold_cards[card_key] = {"text": string, "locked_at": now_iso()}`
- vs.save(v)
- 返回 `{"ok": True, "volume": v.as_dict(), "all_locked": bool}`（all_locked 是 6 卡是否全部锁定，给前端判断是否能下一阶段）

#### 2e. POST `/finalize`
请求体：`{}`
- 检查 6 卡都锁定：death_nodes/climax_nodes/clue_nodes 非空列表 + scaffold_cards 里 core_twist/tangchi_start/cost_cap 都有 text
- 不齐 → 422 "还有卡没锁定"
- `v.stage_completed["scaffold"] = True`
- vs.save(v)
- 返回 `{"ok": True, "volume": v.as_dict()}`

注意：不单独提供"解锁/改卡"端点（本任务范围）。错了就重新 prefill 覆盖（重 prefill 会清空 6 卡的选择，前端提示用户）。

### 3. 注册路由
**文件**：`backend/app/api/main.py` 末尾：
```python
from .scaffold_routes import register_scaffold_routes
register_scaffold_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    chore_provider=_chore_provider,
)
```

---

## 前端改动

### 4. 新建 `frontend/src/views/DungeonScaffoldView.vue`

UI 结构：

#### 4a. 顶部
- 标题"🏗️ 副本详设"
- 副标题"AI 基于唐迟大纲预填 6 张核心卡，每张选最合口味的一版锁定，6 卡齐了就进节奏设计"
- el-tag 显示已锁定张数：`{{ lockedCount }} / 6 已锁定`
- 如果 stage_completed.scaffold===true：显示"✓ 副本详设已完成"绿色 alert + 各卡预览 + "下一阶段：节奏骨架" + "重新预填"（调 PATCH 清空 6 卡+scaffold_cards 回到未锁定态）

#### 4b. 未预填态
- 大按钮"🎰 AI 一次性预填 6 卡（每卡 3 版差异化）"→ POST /scaffold/prefill，订阅 SSE
- 进度条 + 文案"AI 正在并行构思死亡/高潮/线索/诡计/起点/代价…"

#### 4c. 预填完成态（展示 6 卡）
用 6 个 SushiCards ensemble 实例纵向排列（每个卡一个 block）：

**3 张列表卡**（death_nodes/climax_nodes/clue_nodes）：
- 不直接用 SushiCards 展示列表项（因为 SushiCards 是"选一整个 item"），这里每版是一个数组，用定制卡片更清晰。但为了保持一致，仍然用 `<SushiCards mode="ensemble">`：
  - items = cards[cardKey].map((arr, i) => ({ id: i, version_label: `第${i+1}版`, preview: arr }))
  - 用 #item 插槽渲染数组内容：死亡节点渲染 day+who+how（3-5 行）、高潮渲染 day+title+beat+weight 星、线索渲染 day+where+what+是否红鲱鱼 tag
  - item-title-field="version_label"，item-content-field 留空用插槽
- 3 张文本卡（core_twist/tangchi_start/cost_cap）：直接用 SushiCards ensemble 字符串展示

每张卡下方：
- 未锁定：选完后"锁定这张卡"按钮（@click 调 lock-card）
- 已锁定：绿色"已锁定 ✓"+文本/列表预览（不可改）

#### 4d. 6 卡全锁后的 finalize 区
- el-result type="success" title="6 卡全部锁定" sub-title="卷内骨架就绪"
- "🎯 完成详设，进入节奏骨架"按钮 → POST /scaffold/finalize → emit('done')

#### 4e. SushiCards 配置映射
```ts
const CARD_DEFS = [
  { key: 'death_nodes',    label: '💀 死亡节点',    subtitle: '谁在哪天怎么死（3-5个/版）', kind: 'list', field: 'death_nodes' },
  { key: 'climax_nodes',   label: '🔥 高潮转折',    subtitle: '2-4 个关键爆点（带权重）',    kind: 'list', field: 'climax_nodes' },
  { key: 'clue_nodes',     label: '🔍 线索与红鲱鱼', subtitle: '5-8 条线索/误导',           kind: 'list', field: 'clue_nodes' },
  { key: 'core_twist',     label: '🌀 核心诡计',    subtitle: '这卷最大的反转（100-200字）', kind: 'text', field: 'core_twist' },
  { key: 'tangchi_start',  label: '⏱️ 唐迟第一秒',  subtitle: '穿入瞬间看到什么、第一个危机', kind: 'text', field: 'tangchi_start' },
  { key: 'cost_cap',       label: '💸 代价上限',    subtitle: '这卷唐迟要付的最大代价',       kind: 'text', field: 'cost_cap' },
]
```

#### 4f. state
```ts
const step = ref<'idle'|'prefilling'|'picking'|'done'>('idle')
const prefillJob = ref<any>(null)
const cards = ref<Record<string, any[]>>({})  // 6 个 key → 3 items 数组
const locked = ref<Record<string, {index:number, value:any}>>({})
```

lockCard(key, index)：
- 取 value：kind==='list' ? cards[key][index].preview : cards[key][index]
- 调 POST /scaffold/lock-card，成功后 locked[key] = {index, value}

### 5. api.ts 扩展
新增 `scaffoldApi`：
- `prefill(pid, volNo)` → POST prefill
- `listJobs(pid, volNo)` → GET jobs
- `subscribeJobs(pid, volNo, onEvent)` → SSE
- `lockCard(pid, volNo, key, pickIndex, value)` → POST lock-card
- `finalize(pid, volNo)` → POST finalize
- `getVolume/patchVolume` 复用 adaptApi

### 6. 注册到 HomeView
**文件**：`frontend/src/views/HomeView.vue`
- import DungeonScaffoldView
- 加分支：`<DungeonScaffoldView v-else-if="phase==='scaffold'" :vol-no="currentVolumeNo" @done="loadPhase" />`

### 7. 验证
- vue-tsc 零错误
- Python import：`from app.api.scaffold_routes import register_scaffold_routes`
- 浏览器验证：
  - 造测试数据：book 8 卡填 test + 卷1 有 adapted_module(以"测试副本核文本"开头)/simulated_run(mode=pick_one)/host_character={name:"林薇（小报记者·第三日死者）",...}/entry_point/info_gap/stance="伪装不知道"/tangchi_story_outline="【测试大纲】\nDay1...\nDay7..."(非空字符串即可)/stage_completed.adapt=true/simulate=true/embody=true → GET /phase 返回 scaffold
  - 访问页面看到 DungeonScaffoldView，点"AI预填6卡"→SSE进度→2s 后出 6 个 SushiCards 区块（每卡3版）
  - 每卡点"选这盘"+"锁定"，6 卡锁完出现 finalize 区
  - 点"完成详设"→跳 rhythm 占位
  - 清理测试数据
- **mock 短路必须完整**：6 卡 × 3 版都要返回合理数据（死亡节点每版3-5个带day/who/how/tease/cost；高潮2-3个带day/title/beat/weight；线索5条带day/where/what/leads_to/is_red_herring；文本卡每版100字左右克苏鲁味）

### 8. Commit
```
feat(fe+be): DungeonScaffoldView 副本详设6卡AI预填+SushiCards选版+锁定落锤
```

### 9. 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-8-report.md`

---

## 重要提醒
- 不重启在跑 server
- 6 卡 UI 要垂直排开（不是横向），每个 SushiCards 占一行，标题+3 列（3版）紧凑展示
- 列表卡的 #item 插槽要把数组渲染成易读的 bullet 列表（不要直接 JSON.stringify）
- ElementPlus 全量注册，scoped CSS
- 中文克苏鲁味文案
- complete_structured 解析失败要兜底 mock，不让前端卡着
- 不删旧文件
- 完成后 dev server 保持运行
