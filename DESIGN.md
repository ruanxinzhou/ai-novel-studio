# 《第七日的亡者》·写作间 设计说明

> **版本**：v1（工作台重构后，截至 2026-10-15，Task 1-8 完成）
> **状态**：active（后续 Task 9-16 推进中）
> **本文是项目的唯一权威设计文档。旧方向的设计（wizard 流程/m1 schema/初稿规划）已删除，不要再翻旧文档。**

---

## 一、这是什么

《第七日的亡者》·写作间是为网文作者打造的 **克苏鲁无限流长篇小说专属 AI 写作工作台**。

它不是通用 AI 写作工具，不是 ChatGPT 套壳，也不是传统的"大纲→章节"两级编辑器。它是为《第七日的亡者》这一部（以及同类型）小说打造的 **副本流水线**：

- 每一卷 = 一个 COC（克苏鲁的呼唤）副本
- 主角唐迟被奈亚拉托提普扔到不同副本里求生
- 每个副本有 7 天死期，7 天内必须破局
- 副本之间用蓝星间章衔接，带回代价和钩子
- 全书长线 = 唐迟不断在副本中挣扎求生、积累污染、试图逃掉但永远逃不掉

---

## 二、核心创意哲学（硬性约束）

这几条是作者本人拍板的铁律，所有 AI 提示词、UI 文案、交互设计都必须遵守，违反即错。

### 1. 克苏鲁底色，没有"好运"
- 所有人都在挣扎求生，没有轻松通关、没有天命主角
- 奈亚把唐迟扔进来是为了**看戏**，不是为了送金手指
- 死亡要有代价，生存要有污染，真相让人发疯

### 2. 魂穿是核心创作步骤
- 唐迟不是原创主角，每卷都是**魂穿**到副本里某个倒霉蛋身上
- 魂穿对象必须是将死/已死/被诅咒/被替换/SAN 归零的人
  - 即将跳楼的记者、被怪物替换自己不知道的医生、SAN 见底即将自缢的护工
- **禁止**推荐健康活跃的"主角"

### 3. AI 永远先给选项，不抛空表单
- 核心交互叫**回转寿司**（SushiCards）：AI 先出 N 个差异化版本并排摆好，作者选一盘
- 选完可以让 AI 再改一版、可以手动改、可以锁定落锤
- **禁止**一上来就让用户面对空白 textarea 自己想

### 4. 章前 5 分钟，不做一次性全卷大纲
- 开写每一章前用"章前 5 分钟"代替"先写全卷章节大纲"
- 每次只决定下一章的方向卡，写完定稿再决定下一章
- 避免"大纲写得很漂亮但写起来不香"的烂尾陷阱

### 5. 7 天死期 ≠ 7 章
- 故事 Day1-Day7 是**副本内时间**，可以用任意章节数呈现
- 某一天可能写 3 章，某一天半章带过
- story_day 和 chapter 序号是两套独立变量

### 6. 间章是蓝星主线，不是尾声
- 杀青后回到蓝星的间章是主线的一部分
- 写后遗症、关系变化、蓝星怪事、下卷钩子
- 间章 volume_kind='interlude'，走不同的 4 步流程（违和点/后遗症/钩子/下卷预告），不走 COC 跑团

### 7. 必须基于真实 COC 模组，不做 AI 原创副本
- 副本素材必须是用户上传的 COC 模组 PDF
- AI 做的是**改编**（换皮/改年代/融合作者主题/加唐迟视角），不是从 0 生成
- 防止世界观空心化，模组本身的谜题/NPC/死亡机制是骨架

---

## 三、创作流水线（11 个阶段）

每个副本（dungeon 卷）走完下面 1-9 步；蓝星间章走简化 4 步（interlude），杀青后自动衔接。

```
[开书设定 setup]
    ↓
[副本空态 adapt_empty] → 上传 COC 模组 PDF
    ↓
[剧本改编 adapt] → pdf5 并行 5 版差异化改编，选 1 版
    ↓
[模拟跑团 simulate] → 3 场并行 COC（全员殒落/一人独活/真相污染），单选或拼贴
    ↓
[魂穿设计 embody] → 4 步：穿谁 / 切入点 / 立场 / 5 版唐迟大纲
    ↓
[副本详设 scaffold] → AI 预填 6 卡（死亡/高潮/线索/诡计/起点/代价），每卡选版锁定
    ↓
[节奏骨架 rhythm] → Day1-7 节奏曲线、章节分配建议
    ↓
[章前 5 分钟 briefing] ⇄ 每章开写前弹方向卡
    ↓
[章台日更 desk] → 三段式写作（plot→draft→polish）+ 审稿 + 定稿
    ↓ 末章定稿后
[杀青 settle] → 伏笔回收核对/污染检查/代价核算/蓝星钩子
    ↓
[蓝星间章 interlude] → 4 步间章 → 创建下一副本卷 → 回 adapt
```

### 3.1 各阶段核心产出

| 阶段 | 核心产物 | 写入 Volume 字段 |
|------|---------|-----------------|
| setup | 8 张书卡（核心设定/唐迟状态/长线伏笔/代价/页码/七日脉冲/终局注记/写作宪法） | book/*.json（8 个文件） |
| adapt | 副本核文本 | adapted_module (str) |
| simulate | 主跑团记录（单选或拼贴） | simulated_run (dict) |
| embody | 魂穿角色 / 切入点 / 信息差 / 立场 / 唐迟大纲 | host_character, entry_point, info_gap, stance, tangchi_story_outline |
| scaffold | 死亡节点 / 高潮节点 / 线索节点 / 核心诡计 / 唐迟起点 / 代价上限 | death_nodes[], climax_nodes[], clue_nodes[], scaffold_cards{...} |
| rhythm | 节奏蓝图 | rhythm_blueprint (dict) |
| briefing | 本章方向卡 brief | ChapterWork.brief (dict) |
| desk | 每章 plot/draft/polish/定稿 | chapters/*.json |
| settle | 杀青结果（伏笔回收表/污染表/蓝星钩子） | settle (dict) |
| interlude | 蓝星违和/后遗症/下卷钩子 | interlude_plan (dict) |

### 3.2 软锚，不硬锁
- 阶段之间是**软引导**，不是硬锁
- 用户可以跳阶段、回头改、重做，前端只提示"你可能还没做 X"
- 唯一硬约束：写章节必须有 brief（章前 5 分钟）；杀青必须末章已定稿

---

## 四、关键交互模式

### 4.1 回转寿司（SushiCards）
工作台最核心的 UI 组件，替代所有"给我一个空白框自己写"的场景。

两种模式：
- **diverge 模式**：出 N 盘 → 选一盘 → 进编辑 → AI 改一版/手动改/锁定。用于章台单卡打磨（plot/draft 润色等）。
- **ensemble 模式**：通用 N 选 1 对比面板，支持 3/4/5/6+ 列自适应网格、字段可配、具名 `#item` 插槽、选中高亮。用于跨版本对比（改编 5 版/跑团 3 场/魂穿候选/6 卡详设等）。

规则：
- N 默认 3，长任务可以 5；不出超过 7 盘（认知过载）
- 每盘有**差异化中轴标签**（twist/flavor/pacing），不是随便 3 个相似版本
- "换一拨"按钮可以再出 N 盘
- 支持"从空白开始写"兜底路径

### 4.2 长任务 + SSE 进度
以下任务走后台 JobStore + SSE 实时推送：
- pdf5（PDF 5 版改编）
- coc_batch（3 场 COC 并行）
- embody_outline5（5 版唐迟大纲）
- scaffold_prefill（6 卡预填）

短任务（选切入点/选立场/锁定单卡）直接 await 返回，不走 SSE。

### 4.3 测试 Mock 短路
所有长任务端点都有 mock 短路，当检测到是测试数据（adapted_module 以"测试副本核文本"开头或魂穿角色含"林薇"）时，sleep 几秒返回硬编码的克苏鲁味 mock 数据，不调 LLM、不耗 token。方便前端开发和 e2e 测试。

### 4.4 UI 布局
```
┌─────────────────────────────────────────────────────┐
│ StatusBar  书名 · 卷N · 阶段标签 · 污染度 · ⚙ 齿轮  │
├──────┬──────────────────────────────────────────────┤
│      │                                              │
│ Spine│            主工作区（阶段视图）              │
│ Nav  │                                              │
│ 书脊 │                                              │
│      │                                              │
├──────┴──────────────────────────────────────────────┤
│                                           💬 AI浮动 │
└─────────────────────────────────────────────────────┘
                                    SettingsDrawer（右抽屉）
```

- **StatusBar 顶栏**：全局状态感知
- **SpineNav 左栏**：卷/章列表（书脊）
- **主工作区**：按当前 phase 渲染对应视图（BookSetup/ModuleAdapt/CocSim/Embody/DungeonScaffold/Rhythm/Desk/Settle/Interlude）
- **AIChatFloat 右下气泡**：阶段感知快捷指令（"更阴冷一点"/"再多跑几场"/"剧情打不通"）
- **SettingsDrawer 右抽屉**：设置、实体库、工坊、项目切换

---

## 五、技术架构

### 5.1 技术栈
- **后端**：Python 3 + FastAPI + uvicorn（:8321，--reload）
- **前端**：Vue 3 + Vite（:5173）+ TypeScript + Element Plus
- **AI 通道**：MiniMax-M3.1-Flash-Preview（默认）+ Trae 执行通道兜底
- **存储**：纯 JSON 文件（`data/projects/{pid}/` 下），不引入数据库
- **测试**：pytest（后端 329+ 用例）+ vue-tsc（前端类型检查）

### 5.2 目录结构
```
novel-studio/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI 路由（按阶段分文件）
│   │   │   ├── main.py       # 入口，注册所有 *_routes
│   │   │   ├── phase_routes.py     # GET /phase 状态机核心
│   │   │   ├── adapt_routes.py     # PDF 上传+pdf5+选版
│   │   │   ├── simulate_routes.py  # 3场COC并行+拼贴
│   │   │   ├── embody_routes.py    # 魂穿4步+唐迟大纲5版
│   │   │   ├── scaffold_routes.py  # 6卡预填+锁定
│   │   │   ├── brief_routes.py     # 章前5分钟方向卡
│   │   │   ├── settle_routes.py    # 杀青+伏笔检测
│   │   │   └── jobs_routes.py      # 旧 session 通道（保留，Task14 清理）
│   │   ├── book/             # 书卡 + 卷 Volume 存储
│   │   ├── wizard/           # 章台 ChapterWork + DeskStore
│   │   ├── entities/         # 实体库（角色/地点/道具/伏笔）
│   │   ├── jobs/             # JobStore 长任务管理
│   │   ├── pipeline/         # LLM 流水线（coc_simulator/settle/sushi）
│   │   ├── generation/       # 结构化输出+容错
│   │   ├── providers/        # MiniMax/Trae 多模型
│   │   ├── review/           # 审稿 8 项
│   │   ├── settle/detector.py# 伏笔回收检测
│   │   ├── fields/           # 旧 COC 字段跑团存储（保留供 coc_simulator 用）
│   │   ├── context/          # 写作上下文账本（ledger）
│   │   ├── skills/           # 14 个写作模块 digests
│   │   └── ...
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/       # 通用组件（SushiCards/StatusBar/SpineNav/...）
│       ├── views/            # 阶段视图（HomeView + 每个阶段一个 View）
│       ├── api.ts            # 所有 API 封装（按阶段分块 adaptApi/simulateApi/...）
│       ├── state.ts          # 全局响应式状态（currentProjectId/currentPhase/...）
│       ├── App.vue           # 项目门 + HomeView
│       └── main.ts
└── docs/                     # 文档（实施计划/specs）
```

### 5.3 后端路由约定
- 新工作台路由都用 `register_xxx_routes(app, ...)` **函数式注册**，注入已有 helper，避免循环 import
- 工作台统一前缀：`/api/projects/{pid}/workbench/volumes/{vol_no}/...`，避开旧 `/volumes` 端点
- 所有长任务用伪 session_id（`adapt:{pid}:{vol_no}` / `sim:{pid}:{vol_no}` 等），**不依赖旧 Session 对象**（旧 Session 保留给 wizard 通道，Task 14 删除）

### 5.4 Volume 数据模型
`backend/app/book/volume.py` 的 Volume dataclass 是工作台的核心数据载体：

```python
@dataclass
class Volume:
    volume_no: int
    field_no: int
    title: str
    created_at: str
    updated_at: str
    # ---- 工作台字段 ----
    volume_kind: str = 'dungeon'         # 'dungeon' | 'interlude'
    adapted_module: str | None = None    # 副本核文本（adapt 产出）
    simulated_run: dict | None = None    # 主跑团记录（simulate 产出）
    death_nodes: list = field(default_factory=list)       # 死亡节点
    climax_nodes: list = field(default_factory=list)      # 高潮节点
    clue_nodes: list = field(default_factory=list)        # 线索节点
    pasted_nodes: list = field(default_factory=list)      # 拼贴节点
    host_character: dict | None = None   # 魂穿角色
    entry_point: dict | None = None      # 魂穿切入点
    info_gap: dict | None = None         # 信息差
    stance: str | None = None            # 初始立场
    tangchi_story_outline: str | None = None  # 唐迟大纲文本
    deviation_points: list = field(default_factory=list)
    rhythm_blueprint: dict | None = None
    scaffold_cards: dict = field(default_factory=dict)   # {core_twist/tangchi_start/cost_cap: {text,locked_at}}
    settle: dict | None = None
    interlude_plan: dict | None = None
    stage_completed: dict = field(default_factory=lambda: {
        'setup':False,'adapt':False,'simulate':False,
        'embody':False,'scaffold':False,'rhythm':False,
    })
```

### 5.5 阶段判定（状态机）
`GET /api/projects/{pid}/phase` 是路由大脑，按以下优先级判定：
1. 8 张书卡未全 → `setup`
2. 没有任何 volume → `adapt_empty`
3. 末章已定稿 + settle 为空 → `settle`
4. 有 interlude 卷未完成 → `interlude`
5. 有 chapter 没有 brief.selected → `briefing`（章前5分钟）
6. 按 stage_order 找第一个未完成阶段（adapt→simulate→embody→scaffold→rhythm）→ 对应 phase
7. 都完成 + 有章节未完稿 → `desk`

返回值：
```json
{"phase":"embody","volume_no":1,"volume_kind":"dungeon","chapter_no":0,"is_last_chapter":false}
```

前端 HomeView 根据 phase 渲染对应 View，View 完成后 emit('done') → HomeView 重新拉 /phase → 自动切下一阶段。

---

## 六、创作流水线详解

### 6.1 开书设定（setup）
- 8 张书卡：核心设定（含讣告五铁律）/ 唐迟状态 / 长线伏笔与终局 / 代价与欠账 / 剩余页码 / 七日脉冲 / 终局注记 / 写作宪法
- BookSetupView 是折叠面板，卡片内容编辑复用旧接口（book_for 等）
- 8 卡都填过（每张 JSON 文件 >10 字节）才算 setup 完成

### 6.2 剧本改编（adapt）
- 上传 COC 模组 PDF（≤20MB）
- 后端调 `adapt_pdf_source`，5 个 flavor 差异化并行：换皮/诡计强化/程序员融合/黑暗/伪纪录片
- SSE 进度推送，完成后 5 盘寿司展示（每盘含 flavor/title/one_liner/adapted_premise/player_cards/changed_elements）
- 选 1 盘 → 写入 adapted_module → stage_completed.adapt=true

### 6.3 模拟跑团（simulate）
- 基于 adapted_module，build_2same_1different_configs 构造 3 组 PL 配置
- 并行跑 3 场 COC（ThreadPoolExecutor max_workers=3）：
  - **全员殒落线**：无人生还，SAN 集体崩盘
  - **一人独活线**：只剩一个带疯的人带出真相碎片
  - **真相污染线**：有人窥见真相后变成了它的一部分
- coc_simulator 内部是 keeper ↔ investigators 多轮对话，按 Day 推进
- 完成后两种选法：
  - **单选一场**当主线
  - **拼贴合成**：选一场为基线 + 6 段分别从不同场次取（开场/第一死/第二死/线索/高潮/结局）
- 拼贴目前是机械字符串拼接（带【取自X线】标记），AI 智能融合留作后续迭代
- 结果写入 simulated_run

### 6.4 魂穿设计（embody）
4 步向导，每步 SushiCards：
1. **选身体**：AI 基于跑团记录推荐 3-5 个魂穿候选（必须是将死/被替换/SAN 崩盘的倒霉蛋），每张卡附 struggle_hook（为什么有戏）、info_gap_hint（信息差悬念）
2. **选切入点**：3 个魂穿时机（跳楼前10分钟/锁门前/快门按下的瞬间）
3. **选立场**：3 种求生姿态（伪装不知道先活着/主动调查找真相/逃跑但每次被拉回来/装疯混进怪物中/献祭别人保自己）
4. **5版唐迟大纲**：ThreadPoolExecutor 并行生成 5 版 tangchi_story_outline，pacing seeds 用克苏鲁基调（步步紧逼每夜都死人/伪装求生身边都是怪物/记忆污染分不清自己是谁/逃不掉的轮回死亡只是开始/最后一天反转幸存者即祭品）。选 1 版落锤

### 6.5 副本详设（scaffold）
- 单次 LLM 调用一次性预填 6 卡 × 3 版（不走并行，省 token）
- 6 张卡：
  - **死亡节点**（list）：3-5 个死亡事件，字段 {day, who, how, tease, cost}
  - **高潮转折**（list）：2-4 个关键爆点，{day, title, beat, weight:1-5}
  - **线索与红鲱鱼**（list）：5-8 条线索/误导，{day, where, what, leads_to, is_red_herring}
  - **核心诡计**（text）：100-200 字，这卷最大的反转
  - **唐迟第一秒**（text）：穿入瞬间看到什么、第一个危机
  - **代价上限**（text）：这卷唐迟要付的最大代价（SAN/身体/关系/记忆/蓝星后遗症）
- 每张卡 3 版用 SushiCards ensemble 展示，逐卡选版锁定
- 6 卡全锁才能 finalize 进节奏

### 6.6 节奏骨架（rhythm，Task 9 待做）
- 基于 6 卡生成节奏蓝图：Day1-7 死亡/高潮/线索分布密度、建议章节数、节奏曲线
- 前端用 el-timeline / 简单条形图可视化
- 保存到 rhythm_blueprint

### 6.7 章前5分钟（briefing，Task 10 待做）
- 每章开写前弹 ChapterBriefModal
- AI 基于 rhythm_blueprint + 上章末尾 + 到期伏笔给出 3 个方向卡
- 选 1 个写入 ChapterWork.brief.selected
- 首次进 desk 时当前卷没章节 → 自动建 chapter 1 弹 brief

### 6.8 章台日更（desk，Task 10 待精简）
- 保留三段式写作（plot 纲要 → draft 草稿 → polish 润色）
- 审稿 8 项（含新增的信息揭露节奏 rule_reveal_pacing）
- 定稿（finalize）后自动同步实体库、检查伏笔回收、检查兑现节奏
- 章末判断是不是卷内最后一章（按 rhythm_blueprint.suggested_chapter_range），到了进 settle

### 6.9 杀青（settle，Task 11 待做）
- 复用 detector.py 做伏笔自动检测（planted/mentioned/overdue/paid）
- 6 步：伏笔清点 / 实体污染检查 / 代价核算 / 蓝星钩子埋设 / 下卷预告 / 杀青落锤
- finalize 后提示进间章或直接开下一卷

### 6.10 蓝星间章（interlude，Task 12 待做）
- volume_kind='interlude'，走 4 步：违和点 / 后遗症 / 下卷钩子 / 章节数（通常 1-3 章）
- 不跑 COC、不做魂穿，直接用 brief+desk 模式写
- 写完后自动创建下一 dungeon 卷，回到 adapt_empty

---

## 七、审稿与质量控制

审稿 8 项（`backend/app/review/reviewer.py`）：
1. plot_logic（卷目标兑现+欲望接力）
2. character_consistency（角色一致性）
3. volume_outline（卷目标兑现日程）
4. dialogue（对白）
5. consistency_review（跨章联结：接棒/吃书/到期伏笔）
6. chapter_ending（章末钩子）
7. coc_horror（克苏鲁恐怖感）
8. **rule_reveal_pacing**（信息揭露节奏，Task 2 新增，检查 NPC 对白 info dump）

写作辅助：
- 开章核对单：本章位置 / 实体快照 / 到期伏笔账龄（≥8 章必处理）
- 三线节奏（quest 主线/fire 冲突/bond 羁绊）：打卡 + 断档警告
- 兑现记录器：每章记 none/small/medium/climax 四级，连续 4 章无兑现提示节奏饥饿
- 乌贼密度告警：第 10 章后埋了 10+ 伏笔零回收时警告

---

## 八、废弃的设计方向（不要再翻出来）

以下是工作台重构前走过的弯路，已被明确否定：

- ❌ **三栏 Tab 布局（Wizard/Pipeline/Settings）**：改为书脊+主区+抽屉单工作台
- ❌ **AI 原创副本/空手套白狼**：必须上传 COC PDF
- ❌ **一次性全卷章节大纲**：改为章前 5 分钟逐章定方向
- ❌ **7 天 = 7 章的硬绑定**：story_day 和 chapter 分离
- ❌ **AI 先抛空表单让用户填**：永远先给回转寿司选项
- ❌ **"好运线/厄运线"爽文标签**：改为全员殒落/一人独活/真相污染
- ❌ **魂穿到健康活跃主角**：必须穿倒霉蛋
- ❌ **VariantPicker 深色 kindle UI**：改为 SushiCards ensemble 浅色卡片
- ❌ **旧 Session 驱动（必须先建 session 才能干活）**：改为按 volume 直接操作，JobStore 用伪 session_id

涉及这些方向的旧文件（m5-wizard-design / m1-schema / 初稿txt / Untitled）已经从仓库删除。

---

## 九、开发进度与路线

- **已完成（T1-T8）**：Volume 扩展+phase 判定 / brief+settle 端点 / 前端骨架 / ModuleAdaptView / CocSimView / SushiCards 通用化 / EmbodyView / DungeonScaffoldView
- **进行中 / 待做（T9-T16 + Final Review）**：
  - T9 RhythmView 节奏曲线
  - T10 ChapterBriefModal + DeskView 精简
  - T11 SettleView 杀青 6 步
  - T12 InterludeView 蓝星间章
  - T13 SettingsDrawer 内容填充（迁 ProjectView/EntitiesView/Corpus/Import/Skills）
  - T14 删除废弃视图（WizardView/PipelineView/VariantPicker/SessionView/...）
  - T15 api.ts PHASE_TITLES + SpineNav 真实数据 + e2e 状态机
  - T16 旧项目（20261014-c6c432）数据迁移
  - Final whole-branch review + PR

详细任务进度见 `.superpowers/sdd/2026-10-15-workbench-ia-redesign/PROGRESS-2026-10-15.md`。

实施计划和每个任务的 brief/report 在 `.superpowers/sdd/2026-10-15-workbench-ia-redesign/` 目录下。

---

## 十、运行

```bash
# 后端
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload --port 8321

# 前端
cd frontend
npm run dev
```

访问 http://localhost:5173/

配置：复制 `config.example.yaml` 为 `config.local.yaml`，填入 MiniMax API key。
