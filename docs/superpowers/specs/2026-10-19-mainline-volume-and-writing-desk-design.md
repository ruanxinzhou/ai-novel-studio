---
title: "蓝星开篇卷 + 全局写作台 + 4000-5000字/章"
date: 2026-10-19
status: draft
kb_scenario: field_pipeline_complete_flow
kb_subdomain: writing_workflow
---

# 蓝星开篇卷 + 全局写作台 + 字数统一

## 背景

当前 novel-studio 的创作向导从"剧本改编"起步，默认第一章就是唐迟魂穿入副本的 Day 1（T-7 醒来）。这直接跳过了网文的"黄金三章"结构：主角在蓝星的日常亮相、讣告送达的钩子、讣告应验与穿越坠落——这些是读者认识主角、建立代入感的关键两章。

另外两个连带问题：
1. 作者没有"自由写作入口"——所有章节只能由向导在 brief→desk 阶段自动 `ensure` 出来，不能手动新建/插入/删除/改序，想手动补写蓝星开篇章或调整章序没有入口；
2. 章字数目标之前是 8000 字/章，作者希望改为 4000-5000 字/章（更贴近网文常规更新体量）。

本次改造三个目标并行：
1. **黄金三章结构落地**：第 1-2 章是蓝星开篇（引入主角/讣告/穿越），第 3 章起进入第一副本卷；
2. **新增"主线卷"与"全局写作台"**：让作者随时能手动创建/编辑/调序章节，跨卷访问实体库与已定稿内容；
3. **字数统一 4000-5000 字/章**：生成预算、节奏蓝图、章前卡全链路对齐。

## 决策记录（brainstorm 阶段已和作者确认）

- **方案 A 选定**：蓝星开篇独立成卷，`volume_kind` 新增 `mainline` 类型，与 dungeon/interlude 平级；
- **方案 A+ 选定**：左侧 SpineNav 常驻一个"✎ 写作台"入口，全局章列表 + 右侧复用现有 DeskView 编辑器，支持新建/插入/删除/改序/改归属；
- **字数**：每章目标 4000-5000，plot/draft/polish/rhythm/brief 全链路对齐。

---

## 一、数据模型改动

### 1.1 Volume 新增字段（`backend/app/book/volume.py`）

```python
class Volume:
    volume_no: int
    field_no: int
    title: str
    volume_kind: Literal["mainline", "dungeon", "interlude"]  # 新增 "mainline"
    start_chapter_no: int = 1   # 新增：本卷起始全局章号；rhythm/brief 用它做偏移
    # ...其余字段保持不变
```

- `mainline`：蓝星开篇/蓝星主线卷。轻量向导，不走 adapt/simulate/embody/scaffold/rhythm 长流水线；
- `dungeon`：副本卷。维持现有完整向导，不变；
- `interlude`：蓝星间章卷。维持现有 4 步向导，不变。

`start_chapter_no` 新建卷时默认 = 当前 `desk.json` 最大章号 + 1；允许创建时调整，但一旦本卷已有章节就锁死（避免改号造成引用错位）。

### 1.2 ChapterWork 新增字段（`backend/app/wizard/store.py`）

现有 ChapterWork 已足够（`no / field_no / day / is_interlude / title / plot / draft / polish / review / finalized_at / brief / scenes / strands / payoff`），无需新增字段。但需要把 `is_interlude` 的语义扩展（或改用 `chapter_kind`）区分 mainline/dungeon/interlude 章，推荐做法：

```python
# 保留 is_interlude 兼容老数据；新增 chapter_kind 作为权威字段
chapter_kind: Literal["mainline", "dungeon", "interlude"] = "dungeon"
# is_interlude 作为派生属性：@property return self.chapter_kind == "interlude"
```

数据兼容：老数据加载时如果没有 `chapter_kind`，按 `field_no` 反查 Volume 的 `volume_kind` 填充。

### 1.3 DeskStore 新增章节管理方法（`backend/app/wizard/store.py`）

```python
def ensure(self, no: int, *, title: str = "", field_no: int | None = None,
           chapter_kind: str = "dungeon") -> ChapterWork: ...  # 扩展签名

def insert_after(self, after_no: int, *, title: str, field_no: int,
                 chapter_kind: str = "dungeon") -> int:
    """在 after_no 之后插入新章，后续章号全部 +1 顺延，返回新章号。"""

def delete(self, no: int) -> None:
    """删除章，后续章号 -1 顺延；已定稿章需二次确认（前端做确认，后端只拦 final 状态）。"""

def renumber(self, from_no: int, delta: int) -> None:
    """从 from_no 开始的所有章号 += delta；内部工具方法，insert/delete 调用。"""

def move(self, no: int, *, new_field_no: int | None = None,
         new_no: int | None = None) -> None:
    """改章归属卷或章位置；改位置时处理 renumber。"""

def rename(self, no: int, title: str) -> None: ...

def list_by_field(self, field_no: int) -> list[ChapterWork]: ...
def all_sorted(self) -> list[ChapterWork]: ...
def next_available_no(self) -> int: ...
```

所有写操作前必须做 `desk.json.bak.TIMESTAMP` 备份（现有 ensure 路径已备份，insert/delete/move 也走相同备份策略）。

### 1.4 章节引用同步更新

章号变化（insert/delete/move 导致 renumber）时，以下引用必须同步更新：

| 引用位置 | 更新规则 |
|---|---|
| `desk.json: chapters` 字典 key | renumber |
| `ChapterWork.brief` 内部没有章号引用 | 不需要改 |
| `volumes/{n}.json: rhythm_blueprint.chapter_plan[*].chapter` | 全局偏移（对受影响卷做 chapter += delta） |
| `volumes/{n}.json: settle` 没有章号硬引用 | 不需要改 |
| `entities.json: changes[*].chapter` | 全局偏移 |
| `entities.json: foreshadows[*].planted_chapter / paid_chapter` | 全局偏移 |
| `markers.json`（如引用章号） | 全局偏移 |

提供一个 `_shift_chapter_refs(stores, from_no, delta)` 工具函数统一处理。

---

## 二、后端 API 新增/修改

### 2.1 新建卷接口（修改现有"新副本"接口）

现有新建卷（SpineNav "+ 新副本"按钮）接口扩展：

```
POST /api/projects/{pid}/volumes
body: {
  "kind": "mainline" | "dungeon" | "interlude",
  "title": str,
  "start_chapter_no"?: int  // 默认自动计算
}
```

返回新建卷信息 + 进入 phase 的首个阶段（mainline 卷直接返回 `phase: "mainline-brief"`）。

### 2.2 章节管理新接口

```
POST   /api/projects/{pid}/desk/insert
  body: { after_no: int, title: str, field_no: int, chapter_kind: str, with_ai_brief?: bool }
  返回: { new_chapter_no, chapter: ChapterWork }

DELETE /api/projects/{pid}/desk/{no}
  query: force=false   // force=true 时允许删已定稿章（前端二次确认后才传）
  返回: { ok: true }

PATCH  /api/projects/{pid}/desk/{no}
  body: { title?: str, field_no?: int, chapter_kind?: str, day?: int|null }
  返回: { chapter: ChapterWork }

POST   /api/projects/{pid}/desk/reorder
  body: { orders: [{no: int, new_no: int}] }   // 批量改序，按顺序执行 renumber
  返回: { ok: true }

GET    /api/projects/{pid}/desk/spine
  返回: { volumes: [...], chapters: ChapterWork[] }  // 全量章列表+卷列表（SpineNav/写作台使用）
```

### 2.3 mainline 卷专用接口

`mainline` 卷不走 adapt→simulate→embody→scaffold→rhythm 长流水线，只需要一个轻量章纲接口：

```
POST /api/projects/{pid}/volumes/{vol_no}/mainline-outline
  body: { directive?: str, chapter_count?: int = 2 }  // 默认出 2 章（黄金三章前两章/第三张是副本卷）
  返回: {
    outlines: [
      { chapter_title: str, focus: str, words: 4500, beat_type: "hook"|"sell" }
    ]
  }
```

LLM 基于书级硬卡 + 作者 directive 给出 n 章小纲。作者选中后，前端调 desk/insert 逐章 ensure。

### 2.4 写作时材料注入分支（`backend/app/wizard/desk.py:run_chapter_stage`）

在拼装 REQUIRED/FINALIZED 材料前，先读当前章 `field_no → volume.volume_kind`：

- **mainline 章**：注入
  - 书级硬卡（book:hardcards）
  - 写作模块（skill:xxx），按 mainline 场景替换（不注入 coc_horror，换成 daily_uncanny / character_presence 等日常向模块；具体模块在本次实现里用现有的 infinite_loop_unit + obsession_cost，coc_horror 只在 dungeon/interlude 注入）
  - 唐迟主角卡（主角理性底色、穿越前身份性格、初始 SAN/污染度、蓝星状态）
  - 实体账本 state:entities（只列蓝星已登场实体）
  - 全书已定稿摘要 state:plot-draft-digest（跨卷）
  - 上一章尾 6000 字 state:draft-tail（跨卷连续）
  - 开章任务书 build_chapter_checklist（本章方向卡/钩子/登场实体）
  - **不注入**：volume:v{n}:arena（副本核/NPC 卡司/魂穿宿主/Day beat/死亡节点/线索节点/详设 6 卡）——这些是副本卷专属，蓝星章不能看见
  - draft 阶段仍然带上一章结尾 500 字滚动锚点

- **dungeon 章**：维持现有注入逻辑不变。

- **interlude 章**：强化现有逻辑，不要注入副本 NPC 卡司/死亡节点；保留杀青残页/侵蚀度/蓝星钩子。具体在实现里细化。

### 2.5 phase 判定调整（`backend/app/api/phase_routes.py:_determine_phase`）

新增阶段：

- `mainline-waiting`：当前项目还没有任何 mainline 卷，或 mainline 卷章数 < 2（即蓝星开篇章未写完）。引导作者先建开篇卷并写满 1-2 章。
- `mainline-brief`：mainline 卷选中了/正在写某章（phase 直接跳到 desk 写作界面，但左侧卷焦点在 mainline 卷）。

判定顺序：setup 完成 → 若无 mainline 卷或 mainline 章数 < 2 则 `mainline-waiting` → 否则按现有逻辑找第一个未完成的 dungeon/interlude 卷进入对应阶段。

作者强制手动导航（写作台/点击 SpineNav 卷/直接 URL 进入）不受 phase 门控限制。

### 2.6 advance 推进（`backend/app/api/brief_routes.py:_brief_advance`）

- 定稿一章后判断：若当前章 field_no 所属卷是 mainline 且该卷章数 < 预期（mainline 卷默认写 2 章，作者可以手动加章），则继续在本卷 ensure(cno+1)；
- 若 mainline 卷章数达到预期且这是开篇 mainline，则 advance 返回 `{next_chapter: 下个 dungeon 卷的 start_chapter_no, phase: "briefing"}`，自然过渡到副本卷第一张方向卡；
- dungeon/interlude 卷逻辑维持不变。

### 2.7 字数对齐（已部分完成，本次扫尾）

| 位置 | 目标值 | 状态 |
|---|---|---|
| `engine.py: SPECS["plot"].target_chars` | 1800 | 已改 |
| `engine.py: SPECS["draft"].target_chars` | 4800 | 已改 |
| `engine.py: SPECS["polish"].target_chars` | 5000 | 已改 |
| `prompts.py: stages["plot"]` 指令 | 1500-1900字骨架，正文扩到4000-5000 | 已改 |
| `prompts.py: stages["draft"]` 指令 | 目标4000-5000字、不短于4000、不超过5000 | 已改 |
| `rhythm_routes._RHYTHM_SYSTEM` words 字段 | 4000-5000 | 本次改（上次改了一半） |
| `rhythm_routes._RHYTHM_SYSTEM suggested_length` | 章数 ×4500 | 本次改 |
| `rhythm_routes._RHYTHM_SYSTEM suggested_chapter_range` | 重算（含蓝星 2 章） | 本次改 |
| `brief_routes` mock 默认 words | 6500 → 4500 | 本次改 |
| `craft.py DEFAULT_GOLDEN3` | 第1-2章蓝星/第3章入本 | 已改 |

另外：rhythm LLM 输出的 chapter_plan 章号是**卷内相对号**（1..N），后端在落盘前统一加上 `start_chapter_no - 1` 偏移为全局章号。LLM 侧 prompt 明确告知"chapter 字段填卷内相对章号，从1开始"。

---

## 三、前端改动

### 3.1 左侧 SpineNav（`frontend/src/components/SpineNav.vue`）

- 顶部（在卷列表之前）新增常驻按钮「✎ 写作台」，点击进入 WritingDeskView；
- "+ 新副本"按钮改造为"+ 新建卷"下拉菜单：
  - 📖 蓝星开篇/主线（mainline）
  - 👹 副本卷（dungeon）
  - 🌌 蓝星间章（interlude）
- 卷分组按 volume_kind 显示不同图标和颜色标签：
  - mainline: 📖 蓝色标签「主线」
  - dungeon: 👹 紫色标签「副本」
  - interlude: 🌌 青色标签「间章」
- 新建 mainline 卷时若已有 dungeon 卷在写，弹提示"建议把蓝星开篇放在副本前面，是否自动迁移？"（迁移逻辑见 §五）。

### 3.2 新增 NewVolumeDialog（`frontend/src/components/NewVolumeDialog.vue`）

弹窗组件，包含：
- 卷类型三选一卡片（带说明）
- 卷标题输入
- 「起始章号」（默认自动计算，高级选项里展开可改）
- 确认/取消

### 3.3 新增 WritingDeskView（`frontend/src/views/WritingDeskView.vue`）

布局：
```
┌─ SpineNav ─┬────────────────────────────────────┐
│  ✎ 写作台  │  顶栏：当前卷名 [+ 新建章] [≡ 重排]  │
│ (高亮)     ├─────────────┬──────────────────────┤
│ 📖 开篇卷  │ 章列表      │ 章编辑器（复用        │
│  1. 讣告   │ (按卷分组)   │ DeskView 右侧编辑    │
│  2. 穿巷   │             │ 区，plot→draft→      │
│ 👹 第一卷  │ 选中态高亮   │ polish→review 全套） │
│  3. 盐仓醒 │             │                      │
│  4. ...    │             │                      │
└────────────┴─────────────┴──────────────────────┘
```

- **左侧章列表**：
  - 按卷分组，每组卷标题旁有「+ 加一章」按钮；
  - 每章行：章号+标题、状态 tag（空/有剧情/有正文/已定稿）、字数、hover 时显示 ✎ 改名 / 📋 改归属 / ✕ 删除 图标；
  - 支持拖拽排序（用 vuedraggable 或原生）；拖拽后调用 reorder API。

- **右侧编辑器**：直接复用 DeskView 右侧编辑区（拆出一个 `ChapterEditor.vue` 子组件共享，DeskView 和 WritingDeskView 都引用它），包含：
  - 章标题编辑
  - 章前方向卡（如果是 dungeon 章加载 brief，mainline 章可以没有方向卡，或提供一个简化的「本章意图」输入框）
  - plot / draft / polish tab 切换
  - AI 生成按钮（带 directive 输入）
  - 手动编辑/保存
  - 三线打卡/兑现规模
  - 审稿/修复/定稿按钮
  - 上一章/下一章快速跳转

### 3.4 DeskView 重构（`frontend/src/views/DeskView.vue`）

- 把右侧编辑器核心（章标题+三tab+AI按钮+手动编辑+工具栏）抽成独立组件 `ChapterEditor.vue`；
- DeskView 保留「向导上下文」部分：PrevStepPanel（上一步结果）、阶段指示、上下章导航（受 phase 门控，只显示当前卷/当前章附近）；
- WritingDeskView 不挂 PrevStepPanel 和阶段门控，只放 ChapterEditor。

### 3.5 HomeView 阶段路由（`frontend/src/views/HomeView.vue`）

- `v-if` 链新增 `mainline-waiting` 阶段，对应一个简单的引导页 `MainlineLandingView.vue`（或直接内联）：提示"先写蓝星开篇"，提供「📖 新建蓝星开篇卷」按钮；
- `mainline-brief` 阶段直接渲染 WritingDeskView（自动定位到 mainline 卷当前章）；
- 顶栏 StatusBar 对应显示"蓝星开篇"状态 tag；
- 现有阶段（adapt/simulate/embody/scaffold/rhythm/briefing/desk/settle/interlude）维持不变；
- SpineNav 的「✎ 写作台」按钮切换 phase 为一个特殊值 `"writing-desk"`，HomeView 此时直接渲染 WritingDeskView（不被 phase 判定覆盖）。

### 3.6 API 层（`frontend/src/api.ts`）

新增封装：
- `createVolume(pid, {kind, title, start_chapter_no?})`
- `deskInsert(pid, {after_no, title, field_no, chapter_kind, with_ai_brief?})`
- `deskDelete(pid, no, {force?})`
- `deskPatch(pid, no, {title?, field_no?, chapter_kind?, day?})`
- `deskReorder(pid, orders)`
- `deskSpine(pid)` → 全量卷+章列表
- `mainlineOutline(pid, volNo, {directive?, chapter_count?})`

---

## 四、mainline 卷向导流程

mainline 卷流程（轻量）：

1. **新建 mainline 卷**（默认 2 章，可改章数）；
2. **卷设定**：复用 book_setup 的书级硬卡（已有 BookSetupView，mainline 卷不重复收集，直接读 book_store）；
3. **出章纲**（mainline-outline 接口）：LLM 出 3 版小纲，作者选定或自定义；
4. **进入写作台**：选定小纲后，系统自动 ensure 对应章节，作者直接在 WritingDeskView 里写；
5. **写完一章点"定稿"**：advance 自动进入下一章；mainline 章定稿时不做副本那种"节点抽取/死亡判定"，只做基础实体抽取+伏笔登记；
6. **mainline 卷写完**：advance 自动跳到下一阶段（第一副本卷的 briefing，如果副本卷已存在）；如果还没副本卷，弹"📖 现在开始创建第一个副本卷"引导。

---

## 五、现有项目过渡方案

作者当前的项目（20261014-c6c432）已经在第一副本卷的 briefing 阶段，第 1 章方向卡已经生成，但这个"第 1 章"实际上应该是副本卷第 1 章 = 全局第 3 章。

提供一个**一键迁移**（在检测到项目有 dungeon 卷但没有 mainline 卷、且已有章时自动弹提示）：

1. 迁移前自动备份 `desk.json` / `entities.json` / `volumes/*.json` 到 `*.bak.mainline.TIMESTAMP/`；
2. 创建 mainline 卷（volume_no 取当前最小可用号，卷号排在所有 dungeon 卷之前；`start_chapter_no = 1`）；
3. 把现有所有章号从 N 改为 N+2（renumber delta=+2）；
4. 更新所有引用（rhythm_blueprint.chapter_plan、entities.changes.chapter、foreshadows.planted/paid_chapter、markers）；
5. 给 mainline 卷 ensure 出空的第 1、2 章（标题占位"第1章""第2章"，无 draft/plot），field_no 绑定到 mainline 卷；
6. 设置第一副本卷的 `start_chapter_no = 3`；
7. 阶段判定跳到 mainline-waiting，作者直接进写作台写第 1-2 章；
8. 返回 `{migrated: true, backup_dir: "..."}`。

作者也可以选择「不迁移，我自己手动加开篇卷」，则不做 renumber，后续新建的 mainline 卷会被插到副本卷之后（章号接在后面）——这是允许的但不推荐，UI 上标记为"非推荐顺序"提示。

---

## 六、字数统一（全链路核对清单）

实现时逐条核对：

- [x] `engine.py SPECS["plot"].target_chars = 1800`
- [x] `engine.py SPECS["draft"].target_chars = 4800`（±400 浮动 → 4400-5200，LLM 指令明确卡死 4000-5000）
- [x] `engine.py SPECS["polish"].target_chars = 5000`
- [x] `prompts.py: stages.plot` 指令 1500-1900 字骨架
- [x] `prompts.py: stages.draft` 指令 4000-5000 字，宁足勿灌水
- [ ] `rhythm_routes._RHYTHM_SYSTEM`：
  - [ ] chapter_plan[*].words 约束 4000-5000（原 4000-9000）
  - [ ] suggested_length 改为章数 × 4500（±20%）
  - [ ] suggested_chapter_range 重新估算（副本卷 12-18 章 + 蓝星 2 章 = 14-20，dungeon 卷单独的范围 12-18）
  - [ ] chapter 字段明确为卷内相对章号（prompt 里说清楚）
  - [ ] chapter_plan 前两条必须是 chapter=1/2 且 day=0 蓝星开篇（之前改到一半的部分补完）
  - [ ] 后端落盘时做 start_chapter_no 偏移
- [ ] `brief_routes.py` mock/默认 words 值从 6500 改为 4500
- [ ] `scaffold_routes.py` / 其他文件里残留的 8000/6500/7000 等默认字数 grep 一遍统一改
- [ ] 前端 DeskView 的字数统计/章卡片字数显示阈值按 4500 调整（若有「过短」「过长」告警阈值也改）

---

## 七、测试与验收

- 后端单元测试：
  - `test_wizard.py` 新增 insert_after / delete / renumber / move 的测试用例，验证章号顺延和引用同步；
  - `test_workbench_state_machine.py` 增加 mainline 卷的 phase 判定测试；
  - 注入分支测试：构造 mainline/dungeon/interlude 三种章，检查 context.assembler 输出的 material keys 不包含不该出现的副本材料；
  - 迁移脚本测试：构造一个已在 briefing 阶段的项目，跑迁移，验证章号/引用全部正确偏移 2。
- 前端自测：
  - 新建 mainline 卷 → 出章纲 → 写第 1 章 → 定稿后自动到第 2 章 → 写完第 2 章定稿后自动跳到第一副本卷 briefing；
  - 写作台里新建/插入/删除/改名/拖拽排序/改归属全流程走通；
  - 在副本卷中间插入一章蓝星章（虽然不推荐但功能要支持），验证章号顺延和实体引用同步；
  - AI 写章时蓝星章不会出现副本 NPC/副本场景内容。
- 端到端验收（作者操作）：
  - 在现有项目上跑迁移 → 看到第 1-2 章空章，副本卷原第 1 章变成第 3 章；
  - 手动或 AI 辅助写第 1 章（蓝星日常+讣告，4000-5000字）；
  - 写第 2 章（讣告应验+穿越坠落）；
  - 继续写副本卷第 3 章起，上下文能接到第 2 章结尾（醒来瞬间），实体账/上一章锚点正确。

---

## 八、交付与部署

- 所有改动在一个 feature 分支？不，沿用现有 master 直接提交（项目习惯）；
- 提交拆成以下 commit（方便回滚）：
  1. `feat(volume): 新增 mainline 卷 kind + start_chapter_no 字段 + chapter_kind`
  2. feat(desk-store): insert/delete/renumber/move 章节管理方法
  3. feat(api): 章节管理 REST + desk/spine 接口 + mainline-outline
  4. feat(desk-context): 按 volume_kind 分支注入材料
  5. feat(phase/rhythm/brief): phase 判定 + rhythm 章号偏移 + 字数统一 + advance 对 mainline 的处理
  6. feat(frontend): SpineNav「✎ 写作台」+ NewVolumeDialog + WritingDeskView + ChapterEditor 拆分
  7. feat(migrate): 现有项目一键补建开篇卷的迁移脚本
  8. fix(wordcount): 全链路字数统一到 4000-5000（扫尾）
- 后端重启 + 前端 vite 热更新即可，不需要数据格式硬迁移（老数据 chapter_kind 回填做在 DeskStore 加载时）。

---

## 九、不做什么（scope 控制）

- 不做"多人协同/版本历史对比"；
- 不做"章的多分支时间线"（what-if 线）；
- 不做"自由模式开关"（写作台就是自由模式，不需要额外开关；向导推进照旧）；
- 不重构现有 dungeon/interlude 向导流程；
- 不修改实体库/伏笔库的数据结构（继续项目级单文件跨卷共享）；
- 不重做评审/润色/fix 工具链；
- 不做"蓝星章 → 副本章的伏笔自动映射"（靠作者手工埋 + 实体抽取自动捕获，足够）。
