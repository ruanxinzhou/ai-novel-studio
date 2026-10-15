---
kb_scenario: NEW:workbench_ia
kb_subdomain: frontend/ui
title: 写作间工作台重构 —— 单工作台+按阶段推进+模组→改编→跑团→魂穿创作流水线
date: 2026-10-15
status: approved
---

# 写作间工作台重构设计

## 背景与问题

当前 UI 由「今日章台 / 副本对话 / 书与定稿」三个并列 Tab + 「更多···」下拉 9 项组成。暴露的问题：

1. **并列 Tab 迫使作者判断"该点哪个"**，但实际写作是串行流水线——多 Tab 并列是伪结构。
2. **旧 session 门控把副本规划伪装成聊天**，没有明确的"工作台步骤"；真正的产物产出过程（改编/跑团/魂穿）被埋在对话里。
3. **核心创意（魂穿）没有显式 UI**——唐迟穿到谁身上、失去什么线索、作为调查员还是改写者，这是每卷最关键的决策，却只在 scaffold 的 tangchi_start 文本框里一笔带过。
4. **Wizard③"一键生成全书大纲"违背"按卷按章逐步推进"的创作现实**，且与用户"每卷以上传 COC 模组为原料"的工作方式冲突。
5. **冗余**：大纲生成 3 处、审稿 2 处、采纳 3 处、"按要求调整"3 处、参考注入 2 处、两个"N 选 1"组件并存。
6. **杀青时机错位**：旧代码把杀青归账放在"开新卷的 settle_up"，导致第一卷进来要面对不存在的上卷归账；且杀青没有强制伏笔检测。
7. **蓝星间章被低估**：间章是蓝星主线（煤球/苏晚/张阿姨/林知夏/带出物在蓝星的影响）的推进章节，被设计成杀青面板里的可选尾声是错的，应成为独立阶段。
8. **"桩"按钮误导**：walkthrough/schedule/daily_summon 三处是 stub（硬编码假数据/空实现），其中 plan_chapters 硬编码返回 Day1-7+间章共 9 章模板，直接造成"每卷固定 7 章"的误解。

## 作者真实创作流水线（根本依据）

用户的创作不是从零编副本，而是**用现成的 COC 跑团模组作为原料**，经五层加工变成《第七日的亡者》的一卷。

```
[原料] COC 模组 PDF（用户上传，克苏鲁跑团完整剧本）
   ↓ 第一层：改编
AI 把原剧本改造成贴合《第七日》世界观+唐迟当前状态的版本，并行多版差异化供选
   ↓ 第二层：模拟跑团
AI 作为 KP 并行跑多场 COC（默认 3 场），跑出完整故事线（谁死/疯/胜/真相揭露），
   标注关键命运节点；用户选 1 场为主线，可从其他场"借"单个节点/死法/场景拼贴
   ↓ 第三层：魂穿介入（全书核心创意）
  - 选宿主角色（原调查员 / 注定死的 NPC / 反派关联者）
  - 时间回到七天前（默认 Day1 早上）
  - 信息差：唐迟知道自己是唐迟+穿越者+知道副本机制，但失去宿主记忆和线索
  - 立场：顺命调查员 / 逆天改命者 / 旁观者收网者
  → 产出：以唐迟为主角、融合本书世界观的副本剧情大纲（含偏移点清单）
   ↓ 第四层：卷内详设
补齐讣告卡/关键实体/真相与反转/唐迟起点/代价上限/伏笔设计
   ↓ 第五层：节奏骨架+章前 5 分钟
AI 和作者聊节奏（不开章列表），每章在动笔前才聊"这一章写什么"
   ↓ 第六层：章台日更
带齐所有上下文进章台，三段微循环→审稿→定稿
   ↓ 末章定稿
杀青面板（伏笔检测→处置→实体去向→污染结算→基调→归账）
   ↓
🌙 蓝星间章阶段（蓝星主线推进章节）
   ↓
自动建下一卷
```

**设计哲学**：
- 每阶段打开时 AI 主动给方向（基于已有上下文出 3-5 个候选），不给空输入框等作者从零填；作者随时可以说"我自己写"切换到空白。
- 所有产物是"当前想法"（soft anchor）而非"锁定蓝图"——按钮不叫"落锤/锁定"，叫"这版先这样，往下"；随时可回前序阶段改，改时系统提示哪些下游产物可能需要重新确认。
- 章细纲不一次性出全卷，采用**章前 5 分钟**机制：上一章定稿后 AI 才和你聊下一章方向，写到哪聊到哪。
- 不做"AI 无模组原创副本"的兜底路径——必须上传模组才能开新卷，避免 AI 编原料降低质量。

## 目标

- 唯一主路径：打开页面就在"今天该做的事"上，不需要判断点哪个 Tab。
- 新信息架构：书脊+主工作区+⚙️抽屉+💬浮动 AI；阶段自动切换。
- 副本规划真实反映五层加工流水线（模组→改编→跑团→魂穿→详设→节奏→章前5分钟→日更）。
- 魂穿阶段显式化（穿谁/时机/信息差/立场四步决策）。
- 杀青面板放在本卷末章定稿后强制弹出，步骤 0 是"未回收伏笔自动检测"。
- 蓝星间章升格为独立阶段（特殊卷），走章台微循环，承担蓝星主线推进。
- 章前 5 分钟替代一次性章细纲，每章动笔前才聊，保持"从无到有"的创作真实感。
- 非日常工具收进抽屉；章台精简只保留写作必需。
- 桩按钮要么实装要么显式标"建设中"灰掉，plan_chapters 硬编码 9 章模板直接删除。

## 非目标

- 不重写后端已有核心逻辑（三段生成/审稿/定稿飞轮/sushi 回转/pdf5/coc_batch），只做前端重组+少量后端接口补充。
- 不做移动端适配。
- 不改变章台三段微循环（剧情→正文→润色）的核心交互。
- 本次不实装 walkthrough/daily_summon 桩的真实 LLM 逻辑（标建设中，后续迭代）。

## 信息架构

### 整体骨架

```
┌────────────────────────────────────────────────────────────┐
│ 《第七日》· 第N卷《卷名》· 第X章          污染:NN%      ⚙️ │ ← 顶部状态条
├──────────────┬─────────────────────────────────────────────┤
│              │                                             │
│  书脊导航    │              主工作区（自动切阶段）           │
│  (常驻左栏)  │                                             │
│              │  setup    → BookSetupView（开书设定一次性）   │
│  ● 书设定     │  adapt    → ModuleAdaptView（剧本导入改编）  │
│  ├ 卷一 ✓    │  simulate → CocSimView（模拟跑团）           │
│  │ ├ 间章一🌙│  embody   → EmbodyView（魂穿设计）           │
│  ├ 卷二 ✏️   │  scaffold → DungeonScaffoldView（卷内详设）  │
│  │ ├ 1✓      │  rhythm   → RhythmView（节奏骨架）           │
│  │ ├ 2✓      │  desk     → DeskView（章台+章前5分钟）       │
│  │ └ 3▶      │  settle   → SettleView（杀青面板）           │
│  ├ 🌙间章二  │  interlude→ InterludeView（蓝星间章规划）    │
│  └ ➕新副本  │                                             │
│              │                                             │
├──────────────┴─────────────────────────────────────────────┤
│                                         💬 AI（浮动气泡）   │
└────────────────────────────────────────────────────────────┘
```

三个核心改动：

1. **砍掉 el-tabs**。App.vue 不再渲染三个并列 Tab，改为"书脊+主工作区"单页布局。
2. **自动阶段判定**按优先级：
   - 开书 8 卡未完成 → `setup`
   - 有卷末章刚定稿且未走杀青 → `settle`
   - 有未定稿且有章前卡的章 → `desk`（定位到最早未定稿章）
   - 有进行中但未走完的副本阶段 → 对应的 adapt/simulate/embody/scaffold/rhythm
   - 本卷杀青完成且间章未做完 → `interlude`
   - 都没有 → 显示"上传模组开新卷"引导卡（不做 AI 原创兜底）
3. **阶段切换时自动定位**：书脊高亮当前节点，主工作区渲染对应视图。

### 书脊（左栏常驻）

- 根节点：书名（点回顶部/开书设定）
- 卷节点：🔒已定稿 / 🟡连载中 / 🔵规划中
- 间章节点：🌙 蓝星间章（作为独立分组挂在对应副本卷后）
- 章节点：✓已定稿 / ✏️撰写中 / ▶今日章 / 📋已规划
- 未规划到的章不显示具体标题，只显示"后续待规划…"
- 卷节点右侧：污染度小色条；有未处置项显示⚠️角标

### 顶部状态条

- 左：书名 · 面包屑（卷 / 章 / 当前阶段小徽标）
- 中：污染度进度条
- 右：`⚙️` 按钮

### ⚙️ 右抽屉分组

| 分组 | 内容 |
|---|---|
| 📖 书稿 | 层版本回看/回滚、导出 Obsidian Vault |
| 🗂 实体库 | 角色/地点/道具/伏笔 CRUD + 主角档案 |
| 📚 资料 | 范文语料、已上传模组库、导入参考书 |
| 📝 写作规范 | 10 craft 模块、黄金三章规则（默认全开） |
| 🧪 高级 | Prompt 工坊、历史会话查阅 |
| 📂 项目 | 新建/切换/删除项目 |

### 💬 AI 浮动气泡

- 右下角圆形按钮，快捷键 `Ctrl+/`，侧拉 1/3 屏宽。
- 自动携带当前阶段完整上下文（复用现有 `_build_chat_context_block`）。
- 快捷动作按钮按阶段变化（规划期：给灵感/更克苏鲁一点/换个钩子；日更期：剧情打不通/对话不自然/钩子不够狠/开头 300 字要动作；杀青期：帮我找伏笔回收点；间章期：蓝星日常怎么违和）。
- 历史按"书→卷→章"归档，不按会话列表。
- 支持"把这段对话的想法落到当前卡片"一键写入当前阶段的产物字段。

## 阶段详述

### 0. setup · 开书设定（一次性）

8 个折叠 section 对应 8 张 book profile 卡：核心设定（含讣告五铁律/规则揭示节奏）/ 唐迟状态 / 长线伏笔与终局 / 代价与欠账 / 剩余页码 / 七日脉冲 / 终局注记 / 写作宪法（10 craft 模块+双层黄金三章）。

- 每节默认折叠，已填数据的节显示 ✓
- 每节 AI 可"帮我扩写"→出草稿→作者采纳；作者也可直接编辑
- 首次升级进入时，若已通过 API 填入数据（如 20261014-c6c432 已填 premise/tangchi/craft），自动识别为已采纳，不强制重填
- 底部"完成开书设定，开始第一卷"→进入 adapt 阶段（第一卷无需杀青归账前置）

### 1. adapt · 剧本导入与改编

**这是新卷的第一步，主入口是上传 COC 模组 PDF**（无模组不能开新卷，不做 AI 原创兜底）。

- 顶部提示"上传本卷 COC 跑团剧本来开新卷"
- PDF 上传区 → 调 `POST /source-pdf` → 触发 `pdf5` 长任务
- pdf5：8 种风味池（民俗阴冷/规则冷峻/诡异日常/科幻未知/心理诡计/古风玄幻/同人改设/黑色幽默）并行 adapt，取前 5，通过 SSE 推 5 版改编结果（每版 400-700 字，差异化维度：基调/视角/恐怖来源/节奏）
- 5 版结果以回转寿司 5 盘展示（sushi-cards-5 布局）
- 标准寿司交互：换一拨 / 从空白自己写 / 选这盘→编辑态（AI 改一版直接应用 / 出建议先看看 / 返回重选 / 就定这版）
- 对话区可输入自然语言要求（"更阴冷一点"/"和程序员 oncall 结合"），AI 按要求再出 5 盘
- 产物：`field.adapted_module`（改编后的模组文本）+ session.outputs.adapted
- 选完"这版先这样，往下"→simulate

> 注：原 kindle_diverge（3 场 COC 仿真"旧"路径）按钮移除；coc_batch 移到 simulate 阶段作为主操作。

### 2. simulate · 模拟跑团

基于改编后的模组，AI 作为 KP 并行跑多场 COC，产出完整故事线。

- 进入阶段默认自动跑 3 场（可点"再多跑几场"追加，每场都真实调 coc_simulator）
- 每场跑完以一张卡片展示摘要：
  - 故事梗概（300 字）
  - 关键命运节点（哪些 NPC 在哪天死亡/发疯/决战）
  - 结局（团灭/惨胜/封印/揭穿真相）
  - 唐迟若介入会遇到的精彩瞬间
- 卡片底部两个按钮：**"选这场作为主线"** / **"收藏节点"**（把单个节点/死法/场景收进"拼贴篮"）
- 右侧栏"拼贴篮"展示已收藏的其他场节点，支持拖入主线对应位置替换
- 选定主线+拼贴后，AI 合成一份**主跑团记录**（`simulated_run`），包含：
  - 7 天时间线（场景/地点/事件）
  - NPC 命运表（每个关键 NPC 发生什么、死亡节点）
  - 关键线索分布（哪些线索在哪天被发现）
  - 真凶揭露时刻
  - 决战场景
  - 原剧本结局
  - 自动标注：关键死亡节点（death_nodes）、决战节点（climax_nodes）、线索节点（clue_nodes）
- 如果对某场不满意，可"重新跑这一种子变体"
- 产物：`field.simulated_run` + `field.death_nodes` + `field.climax_nodes` + `field.clue_nodes`
- "这版先这样，往下"→embody

### 3. embody · 魂穿设计（核心新增阶段）

这是每卷最关键的决策点——唐迟穿谁、回去什么时候、知道什么/不知道什么、以什么立场介入。4 个子步，每个都由 AI 基于前序产物主动出候选，不是空表单。

**3a. 穿谁（host_character）**
- AI 基于跑团结果列出可穿候选：
  - 原 PC 调查员位（顺命探索路线）
  - 注定死的 NPC（改命路线）
  - 反派/邪物关联者（高难度路线）
  - 边缘角色（旁观收网路线）
- 每个候选一张卡，标注：身份/初始位置/原剧本命运/穿去后的信息差/死亡风险/戏剧张力
- 回转寿司 5 盘（按"张力+风险"排序），支持换一拨/自定
- 产物：`field.host_character`（name/role/original_fate/starting_position/risk）

**3b. 穿回去的时机（entry_point）**
- 默认 Day1 早上（剧本正式开始，符合"时间回到七天前、失去所有线索"）
- AI 给 3 个其他可能时机（Day0 深夜/第一个事件发生前一小时/某个关键时刻前）并说明利弊
- 选默认或其他
- 产物：`field.entry_point`（day/hook/what_he_sees_first）

**3c. 信息差（info_gap）**
- 默认规则：唐迟知道自己是唐迟（穿越者）+程序员思维+性格+知道副本/讣告/七日归位机制（按长线揭示节奏）；**失去宿主的记忆、人际关系、线索**（睁眼不知道自己叫什么、旁边的人是谁、为什么在这里）
- AI 展示"唐迟睁眼第一幕"镜头（在哪/谁在喊他/手里有什么/需要立刻伪装的身份）
- 可勾选特典（比如宿主身上有一张看不懂的字条/有一个未接来电/身体上有莫名伤痕）
- 产物：`field.info_gap`（knowns/unknowns/opening_shot）

**3d. 立场（stance）**
- AI 基于宿主+时机+唐迟当前污染度/性格，给 3 个立场选项：
  - **顺命调查员**：顺原剧情走，用程序员思维比原调查员做得更好，目标存活
  - **逆天改命者**：穿到注定死的角色身上试图改命，剧情会大幅偏移
  - **旁观者收网者**：穿到边缘角色，主要观察/收残页/探真相，不深度介入
- 每个立场附：可能的剧情偏移方向/风险/长线收益
- 选 1 个/改/自定
- 产物：`field.stance`

四步完成后，AI **自动生成魂穿后的副本剧情大纲** `field.tangchi_story_outline`：
- 不是原跑团照搬，而是基于立场+信息差推演"唐迟介入后故事怎么走"
- 标注：按原剧本走的段落 / 偏移点（deviation_points：哪些节点唐迟会让剧情转向、转向哪、可能的后果）
- 标注：开卷三章必过点（Day1 动作开场 300 字、唐迟 1500 字内亮相、首胜+长线钩子在第 3 章末）
- 作者可改/加/删，确认后推进
- 产物：`field.tangchi_story_outline` + `field.deviation_points[]`

### 4. scaffold · 卷内详设

在魂穿大纲基础上补齐 6 张卡（原 7 张卡合并/精简，因为真相/反转在魂穿大纲里已自然产出，这里是校准）。每张卡默认已由 AI 基于前序产物预填，作者校准即可，不是从零填：

| # | 卡 | 交互 | 预填来源 |
|---|---|---|---|
| ① | 讣告卡 obituary_card | 回转寿司 3 盘（diverge），200-350 字 | 基于 host+entry_point 自动生成 3 版送达方式 |
| ② | 关键实体 entity_card | ensemble 回转寿司（一次 3 个互补角色），可反复追加 | 跑团结果里的 NPC/怪物自动列出，AI 补 3 个功能互补 |
| ③ | 真相与核心反转 truth_outline+core_twist | AI 基于魂穿大纲出 3 版骨架，选/改/自填；文本框编辑 | tangchi_story_outline 里的 truth/deviation 自动填入 |
| ④ | 唐迟起点 tangchi_start+代价上限 cost_cap | AI 建议，文本框校准，滑动条调代价上限 | 3a-3d 产物自动回填；cost_cap 基于宿主风险+当前污染度建议 |
| ⑤ | 伏笔设计 foreshadows | AI 列出本卷建议新埋 N 个+回收旧 Y 个，勾选/编辑 | truth_outline+endgame 长线伏笔库比对生成 |
| ⑥ | 唐迟尺度校准 tangchi_scale | 基于本卷恐怖度+污染度建议，文本框校准 | 当前 tangchi 尺度+stance 推导 |

每张卡独立"这版先这样"，6 张全过完才能推进。右侧保留 coc_batch/outline5 灵感按钮（结果只存 artifacts 参考，不自动落盘）。

### 5. rhythm · 节奏骨架

**不做章列表、不做章数硬编码**——只和 AI 聊节奏比例和关键节点位置。

- AI 基于 tangchi_story_outline + deviation_points + 体量（小/中/大副本）建议：
  - 总字数区间（如 2-3 万字/4-5 万字/6-8 万字）
  - 节奏比例：开卷 25%（钩住+建立+第一个小胜）/ 中段 45%（探索+死亡+线索累积）/ 高潮结局 30%（决战+归位）
  - 关键节点在哪个进度位置：第一个死亡/唐迟发现真相/决战/结局
  - 建议章数区间（如 8-10 章），但这只是参考不锁定
- 作者用自然语言调整："节奏再紧一点"/"Day4 就给第一个人死"/"结局多留一章余韵"
- AI 同步更新节奏曲线可视化
- **不生成 chapter_plan 列表**
- 产物：`field.rhythm_blueprint`（pacing_ratio + key_beats_progress + suggested_length + suggested_chapter_count_range）
- "这版先这样，往下"→自动触发前 3 章的章前 5 分钟（开卷黄金三章必须提前规划），规划完进入章台写第 1 章

### 6. 章前 5 分钟 + desk · 日更章台

#### 章前 5 分钟（替代一次性章细纲）

- 触发时机：节奏骨架完成后自动触发前 3 章；之后每章定稿后自动触发下一章
- 弹出小面板（不是独立页面），AI 基于所有上下文（节奏+上一章结尾+伏笔状态+人物状态+已写内容+黄金三章校验）出**3 个这章方向卡**：
  - 每张卡：章名建议/Day/剧情摘要 100 字/推进线/兑现级别/章尾钩子一句话/情绪弧光
- 作者：选一个 / 取 A 开头混 B 结尾 / 自然语言"让唐迟更怂一点"让 AI 再出 3 个 / 我自己写跳过
- 选定后生成**章前规划卡**（5 字段预填可改：剧情摘要/推进线/兑现级别/章尾钩子/结构位置+剧情时间 Day）
- 章前规划卡写入该章的 chapter.brief，章台加载
- 每一章都是动笔前才聊清楚写什么，永远是"写到哪聊到哪"

#### 章台（DeskView，精简版）

对现有 DeskView 精简：

**去掉**：左栏章节列表（书脊承担）、"参考资料注入"折叠、"写作模块注入"折叠、"五段开章任务书"checkbox（默认永远开）、"💬 召唤 AI"按钮（改右下角气泡）、分页。

**保留**：三段卡（剧情/正文/润色，指令框+生成/重新生成+按要求调整+保存修改+流式 textarea+思考过程）、审稿卡（运行审稿/定点修复）、定稿卡（兑现单选+飞轮结果）。

**新增/改动**：
- 章头条：第 N 章 · 章名 · （副本内第 X 天）· 「今日目标」标；末章时右侧提示"这是本卷末章，定稿后进入杀青"
- 章前规划卡（章头条下方，默认折叠可展开）：显示 5 字段，可微调（微调只作用于本章，不改 field 级的原规划）
- 开卷前三章黄标"开卷黄金三章第 N 章"，章前卡里显示 craft_checkpoints 必过点（300 字动作开场/MC 1500 字内亮相/第 3 章首胜+长线钩子）
- 审稿 7 项检查加第 8 项**「规则揭示节奏」**（检查是否出现 NPC 讲解式 exposition/设定倾倒）
- 三段卡"生成"按钮：若指令框空，AI 基于章前卡+上一段结尾自动出；支持点"给我 3 个走向"（小型回转寿司）
- 正文段新增**"人物长出新东西了"**标记按钮：点了从这段提取实体/伏笔/性格变动候选，一键回写实体库/伏笔
- 偏离剧情段时提示"要不要更新剧情段/顺着人物继续走"，不是报错
- 末章定稿弹确认"这是本卷末章，定稿后进入杀青面板"→切到 settle

**craft 注入**：三段生成时按 BOOK_PHASE_SKILLS（书级开章）或 VOLUME_PHASE_SKILLS（卷内章）自动选 craft 规则，开卷前三章强制注入 opening 完整规则+黄金三章 hook 校验。不暴露 UI 勾选。

### 7. settle · 杀青面板（末章定稿后强制弹出）

步骤顺序锁定，前一步未完成下一步 disabled：

| 序 | 名称 | 交互 |
|---|---|---|
| 0 | 🔍 **本卷未回收伏笔自动检测** | 进入自动触发或点"开始检测"；AI 扫本卷全部已定稿正文+伏笔实体库（type=foreshadow, status=planted），输出清单，每条：伏笔内容/种植章节/卷内出现次数/回收状态（✅已回收/⚠️提了但没收/❌完全没提）/AI 回收建议/「去看原文」跳转；每条有"已回收（AI 漏判）"一键标 paid |
| 1 | 伏笔处置 | ⚠️/❌ 每条单选：「本卷定点补回收」（定点修复编辑器，AI 生 1-3 段补回收文字，写回对应章节 polish）/「带到下一卷」（status 保持 planted 加 carry_to_next tag）/「丢弃」（status=dropped）；批量按钮：⚠️全带到下卷 / ❌全丢弃 |
| 2 | 实体去向 | 本卷出场 NPC/道具，每条单选：杀青（死/消失/安息）/带入下卷/间章交代 |
| 3 | 污染度/理智结算 | AI 根据本卷事件+stance+关键死亡节点建议污染度增量+唐迟当前理智，作者可改；展示累计污染度 |
| 4 | 杀青基调 | 回转寿司 3 盘（冷收/余味/刀糖），400-600 字；支持刀糖比滑块/改/自写。若作者想直接进间章不写长残页，可"只写 300 字以内卷尾小镜头"短模式 |
| 5 | 杀青确认 | 展示处置汇总（伏笔回收/带入/丢弃 X 条；实体 N 杀青 M 带入；污染+N；基调已选）；点「杀青，进入蓝星间章」→写回全部（entities+protagonist.contamination+volume.settle+volume.outputs.wrapup）+本卷标 settled+自动在书脊插入🌙间章分组+切到 interlude |

**后端新增接口**：
- `POST /api/projects/{pid}/volumes/{vid}/settle/detect_foreshadows`
- `POST /api/projects/{pid}/volumes/{vid}/settle/apply`（伏笔/实体/污染处置批量写回）
- `POST /api/projects/{pid}/volumes/{vid}/settle/finalize`（标记 settled，创建间章骨架）

### 8. interlude · 🌙 蓝星间章（蓝星主线推进阶段）

间章是**独立的主线推进阶段**，不是杀青附属。每卷杀青后默认进入（某卷唐迟未回蓝星可点"跳过间章直接开下一卷"）。间章作为"特殊卷"处理（volume 加 volume_kind='interlude'），有自己的 4 步规划，之后进入章台日更走同样的三段微循环。

| 步 | 名称 | 交互 |
|---|---|---|
| ① | 属性面板变更可视化 | AI 把杀青归账 diff 转成**具体的日常镜头**建议（不只是数据列表）：污染+8 但唐迟察觉不到，煤球会有反应；手艺"初窥"了 X，唐迟可能在超市看到某个东西突然本能反应；残影 Y 会在他闭眼时闪过；带出物 Z 在他包里/口袋里/冰箱上出现。每条标注"唐迟是否察觉/身边人谁会察觉"，作者勾选要在本间章里体现的 |
| ② | 蓝星回响选择 | AI 基于归账结果+蓝星羁绊状态+长线伏笔，主动给 **3 个蓝星开头钩子**（回转寿司小卡形式），围绕不同蓝星线：煤球异常/苏晚 ICU/张阿姨咸菜/林知夏读书会/同事 oncall/带出物干扰/残影第一次发作/警察调查线。作者选 1-2 条线（不贪多），每线选一个触发点 |
| ③ | 间章章细纲 | 和副本章一样用章前 5 分钟生成，但节奏骨架是"短章"（通常 1-2 章，也可 3 章），不强制黄金三章校验。最后一章结尾必须留**下一副本引子**（下一封讣告的前兆） |
| ④ | 下一副本讣告预告 | 回转寿司 3 个送达镜头小卡（手机弹窗/门缝/张阿姨递给他/煤球叼回来/苏晚桌上看到自己名字...），选 1 个/改/自定；作为间章最后场景 |

规划完锁定→进入章台日更（同样的三段微循环+审稿+定稿）。间章所有章定完稿→自动新建下一卷副本骨架→切到 adapt 阶段上传新模组。

间章在书脊上用 🌙 标识，和副本卷视觉区分。审稿规则对间章生效，且加特别检查项"蓝星日常是否自然/有违和感但不直白解释"。

### 状态机

```
setup ──(8卡采纳)──→ adapt(上传模组) ──→ simulate(跑团) ──→ embody(魂穿4步)
   ↓
scaffold(6卡详设) ──→ rhythm(节奏骨架) ──→ 章前5分钟(前3章) ──→ desk(第1章)
   ↓
desk(第N章定稿) ──(非末章)──→ 章前5分钟(下一章) ──→ desk(下一章)
desk(末章定稿) ──→ settle(杀青6步)
   ↓
[判定]回蓝星？ ──是──→ interlude(间章4步+日更) ──→ 新卷 adapt
              └─否──→ 新卷 adapt
```

所有阶段状态从后端数据推导，不依赖前端本地状态，刷新不丢位置。

## 文件变更计划

### 新增文件

**前端视图**：
- `frontend/src/views/HomeView.vue`：新根容器（状态条+书脊+抽屉+阶段路由），替代 App.vue 的 el-tabs
- `frontend/src/views/BookSetupView.vue`：开书设定 8 节
- `frontend/src/views/ModuleAdaptView.vue`：模组导入+pdf5+5 盘回转寿司
- `frontend/src/views/CocSimView.vue`：模拟跑团（多场并行+主线选择+拼贴篮）
- `frontend/src/views/EmbodyView.vue`：魂穿设计 4 步
- `frontend/src/views/DungeonScaffoldView.vue`：卷内详设 6 卡
- `frontend/src/views/RhythmView.vue`：节奏骨架
- `frontend/src/views/SettleView.vue`：杀青面板（含伏笔检测）
- `frontend/src/views/InterludeView.vue`：蓝星间章规划

**前端组件**：
- `frontend/src/components/SpineNav.vue`：书脊左栏
- `frontend/src/components/StatusBar.vue`：顶部状态条
- `frontend/src/components/SettingsDrawer.vue`：⚙️右抽屉
- `frontend/src/components/AIChatFloat.vue`：AI 浮动气泡
- `frontend/src/components/ChapterBriefModal.vue`：章前 5 分钟弹窗
- `frontend/src/components/PreChapterCard.vue`：章前规划卡
- `frontend/src/components/SushiCards.vue`：重构为通用回转寿司（支持 3/5 盘、diverge/ensemble、可配置卡片 schema）
- `frontend/src/components/SimRunCard.vue`：单场跑团结果卡+收藏到拼贴篮

**后端**：
- `backend/app/api/settle_routes.py`：杀青相关接口
- `backend/app/settle/detector.py`：伏笔自动检测
- `backend/app/volume.py`：Volume 加字段 `volume_kind: 'dungeon'|'interlude'`、`adapted_module`、`simulated_run`、`host_character`、`entry_point`、`info_gap`、`stance`、`tangchi_story_outline`、`deviation_points`、`rhythm_blueprint`、`death_nodes`、`climax_nodes`、`clue_nodes`（合并之前 outputs 散落字段到 volume，field 概念逐步吸收进 volume）

### 大幅修改

- `frontend/src/App.vue`：删 el-tabs，改渲染 HomeView；项目选择器留顶栏
- `frontend/src/views/DeskView.vue`：去左栏/折叠区/召唤 AI 按钮/五段任务书 checkbox；加 PreChapterCard；章末判定+跳杀青；加"人物长出新东西"按钮；偏离提示非模态化
- `frontend/src/api.ts`：阶段 PHASE_TITLES/PHASE_META 重映射到新 8 阶段（去掉 settle_up/walkthrough/schedule/daily_write 旧阶段命名，改用 adapt/simulate/embody/scaffold/rhythm/desk/settle/interlude；daily_write 作为章台内部子状态不是顶级阶段）

### 迁移使用（内容搬家）

- `WizardView.vue` ①②→BookSetupView；③④⑤⑥删除
- `SessionView.vue` 回转寿司交互模式→通用 SushiCards；会话主体→AIChatFloat；阶段门控状态机→新阶段状态机（不再基于 session.current_phase，而是 volume 上的字段推导）
- `ProjectView.vue`（版本/导出）→SettingsDrawer"📖 书稿"
- `EntitiesView.vue`→SettingsDrawer"🗂 实体库"，Settle 步骤 2 复用
- `PipelineView.vue` 删除（大纲生成入口并入 adapt/simulate/rhythm，批量任务收抽屉"🧪 高级"）
- `MarkersView.vue` 删除（必现标记并入实体库伏笔状态）
- `SkillsView.vue`→SettingsDrawer"📝 写作规范"
- `CorpusView.vue`/`ImportView.vue`→SettingsDrawer"📚 资料"
- `WorkshopView.vue`→SettingsDrawer"🧪 高级"
- `ProjectsView.vue`→SettingsDrawer"📂 项目"，项目选择器留顶栏
- `VariantPicker.vue` 删除（合并进通用 SushiCards）

### 最终删除（迁移完成后）

- `frontend/src/views/WizardView.vue`
- `frontend/src/views/PipelineView.vue`
- `frontend/src/views/MarkersView.vue`
- `frontend/src/views/WorkshopView.vue`
- `frontend/src/views/SkillsView.vue`
- `frontend/src/views/CorpusView.vue`
- `frontend/src/views/ImportView.vue`
- `frontend/src/views/ProjectsView.vue`
- `frontend/src/views/SessionView.vue`
- `frontend/src/components/VariantPicker.vue`

最终 views/：HomeView / BookSetupView / ModuleAdaptView / CocSimView / EmbodyView / DungeonScaffoldView / RhythmView / DeskView / SettleView / InterludeView（共 10 个）。

### 后端接口调整

**新增**：
- `GET /api/projects/{pid}/phase` 返回当前阶段+定位信息（供启动时判定）
- `POST /api/projects/{pid}/volumes` 创建新卷副本
- `POST /api/projects/{pid}/volumes/{vid}/interlude` 创建间章
- `POST /api/projects/{pid}/volumes/{vid}/settle/detect_foreshadows`
- `POST /api/projects/{pid}/volumes/{vid}/settle/apply`
- `POST /api/projects/{pid}/volumes/{vid}/settle/finalize`
- `PATCH /api/projects/{pid}/volumes/{vid}/brief` 更新章前卡
- `POST /api/projects/{pid}/volumes/{vid}/next-chapter-brief` 触发章前 5 分钟生成下一章方向

**调整**：
- `deskApi.runStream` 默认 craft 注入改为后端按阶段+craft_checkpoints 自动选择，前端不再传 craft_override
- reviewer 审稿新增第 8 项"规则揭示节奏"
- session 门控从"phase 推进"改为"基于 volume 字段推导阶段"，sessions 表保留作为对话历史，不再承担阶段状态机职责
- pdf5 长任务接口保留，端点从 kindle 阶段移到 adapt 阶段
- coc_batch 接口保留，从 scaffold 右栏移到 simulate 阶段主操作
- kindle_diverge/plan_chapters/daily_summon/walkthrough_step 四个桩接口标记 deprecated：plan_chapters 删除（不再返回 9 章硬模板），其余三个返回"建设中"提示

**保留**：deskApi.saveText/review/fix/finalize、sessionsApi.send/create/list（供 AIChatFloat 使用）、entitiesApi.*、book cards GET/PATCH、pdf 上传/source-pdf/pdf5 长任务、coc_batch 长任务。

## 桩处理

本次 UI 重构明确处理三个桩：
1. **plan_chapters（硬编码 9 章）**：**直接删除**，不返回假数据；节奏阶段不生成章列表
2. **walkthrough_step（朗读下一段）**：按钮保留但标"建设中"灰掉，本次不接 LLM；未来实装为"逐章细纲朗读模式"
3. **daily_summon（召唤 3 方向）**：移除原硬编码按钮；同类功能由章前 5 分钟和 AIChatFloat 承担

## 错误/边界

- **刷新页面**：阶段从后端推导，刷新不丢位置
- **已有项目升级（如 20261014-c6c432）**：
  - 已有 book json 数据 → BookSetupView 已填节显示 ✓，可直接进入
  - 已存在 volume/field 但没走完 kindle/scaffold/schedule → Volume 新字段为空，自动判定为"adapt 阶段已完成（core_pitch 视作 adapted_module 兜底）→ 进入 simulate 阶段"；弹提示"旧项目升级：需要重新跑模拟跑团和魂穿设计才能继续"；不丢已写内容
  - 已有 chapters → 若有已定稿章则书脊展示；若无章细纲则进入 rhythm 阶段
- **多开 tab**：不做协作，最后写入为准
- **伏笔检测误判**：每条都有"已回收（AI 漏判）"一键标 paid
- **末章判定错误**：章前卡允许手动勾选"这是本卷末章"覆盖
- **跑团结果都不满意**：支持"再多跑 N 场"，可指定参数（更惨/更克苏鲁/少死人等）
- **魂穿候选里想穿的角色没列出来**：支持"我想穿 XXX"自填宿主

## 验收标准

1. 打开 http://localhost:5173/ 不再见三个并列 Tab；顶部状态条+左侧书脊+主工作区+⚙️+💬气泡。
2. 首次升级进入《第七日的亡者》：已有 book 数据识别为 setup 完成；若有旧 volume 则提示需补跑团/魂穿。
3. 新卷必须上传 PDF 模组才能开始；不上传不解锁下一步（无 AI 原创兜底）。
4. pdf5 跑完显示 5 盘回转寿司；可选/改/自写；选完推进到 simulate。
5. simulate 默认自动跑 3 场；每场卡可"选为主线"/"收藏节点"；拼贴篮可拖入替换；合成主跑团记录含 death_nodes/climax_nodes。
6. embody 4 步子步清晰：穿谁（5 候选）/时机/信息差/立场；每步 AI 主动出候选不给空框；四步完 AI 自动生成 tangchi_story_outline 含 deviation_points。
7. scaffold 6 卡每张都有预填值，不是空 textarea；前两张有回转寿司，后四张是 AI 预填+校准。
8. rhythm 阶段不生成章列表，只产出节奏比例+关键节点位置+字数/章数区间建议。
9. 章前 5 分钟在节奏完成后自动触发前 3 章；每章定稿后自动触发下一章；3 个方向卡可选/改/自写。
10. 章台无左栏/无折叠区/无召唤 AI 按钮/无五段任务书 checkbox；开卷前三章有黄标；审稿 8 项检查。
11. 末章定稿后强制进入杀青面板；步骤 0 自动/点开始检测；步骤 1 有未处置项时步骤 5 disabled；"去看原文"可跳转。
12. 杀青完成后自动建🌙间章分组进入 interlude；间章 4 步+章台日更；间章完稿后新建下一卷进 adapt。
13. 跳过间章选项可用（某卷唐迟未回蓝星）。
14. ⚙️抽屉 6 分组可打开；实体库可编辑；版本可回看。
15. 💬浮动气泡任意阶段可用，上下文随阶段切换。
16. 旧 Tab/旧视图不在 DOM 中（不是隐藏）。
17. 7 天死期 ≠ 7 章：章数不固定；每章"剧情时间 Day"独立字段；高潮可跨多章；开卷三章校验看的是"开卷第 N 章"而非"副本第 N 天"。
18. plan_chapters 不再返回硬编码 9 章模板；walkthrough/daily_summon 桩按钮灰掉标建设中。

## 不在本次范围（后续迭代）

- walkthrough"朗读下一段"实装（逐章朗读细纲生成）
- daily_summon 实装（章内召唤真实方向建议）
- 卷间节奏视图（跨卷节奏/污染度曲线可视化）
- 间章 Markdown 富文本（先用 textarea）
- EPUB/长图导出（保留 Obsidian Vault 导出）
- 快捷键/手机适配
