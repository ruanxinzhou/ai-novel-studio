# 设计：后续章骨架重排 + 知情表（角色层 + 读者层）+ 分档字数与场数

日期：2026-10-26
范围：novel-studio 后端 `app/book/volume.py`、`app/api/rhythm_routes.py`、`app/api/brief_routes.py`、`app/api/main.py`、`app/generation/quality.py`、`app/context/ledger.py`、`app/review/book_checks.py`、`app/entities/vault_plan.py`、`app/knowledge/`（新），前端 `src/views/DeskView.vue`、`src/views/WritingDeskView.vue`、`src/api.ts`

---

## 1. 问题诊断

### 1.1 作者要求的两件事，能力已经存在但作者看不见

项目里写正文时**已经在注入**原剧本和知情边界，只是没有出口给作者看。

**原剧本已经在注入**：`context/ledger.py:498` 的 `build_origin_material()` 每次写正文都装配四块——

```
■ 小说化梗概（7 幕骨架）    ■ 通稿原文节选（头尾两段）
■ 原剧本逐日推进（本章日 ±1 天，含未来 2 天作方向）    ■ 使用纪律
```

数据源是 `book/volume.py:140` 的 `origin_novel_of(vol)`。实测项目 `20261014-c6c432` 第 2 卷：

```
volumes/2.json → origin_novel = {}          ← 空的！
simulated_run.selected_variant               ← 数据其实在这里
  final_summary   1534 字（7 幕）
  full_narrative 13532 字（通稿）
  day_breakdown      7 条
  transcript       3171 字
```

`origin_novel_of()` 会在读取时从 `selected_variant` **惰性重建**（纯字段搬运，零 LLM 零 token）。所以数据齐了，缺的只是**展示入口**。

**知情边界已经在注入**：`context/ledger.py:303` 的 `_knowledge_seg()` 会给每个角色行尾追加「（已知：…；不知情：…）」，`review/book_checks.py` 会拿正文去撞 `blinded_to` 声明的边界。但：

```
entities.json → 181 个实体 → 26 个 character
               → state_attrs.knows 键存在的有 0 个
               → state_attrs.blinded_to 键存在的有 0 个
               → 26 个角色里 state_attrs 为空 dict 的有 21 个
```

schema 设计好了（`entities/store.py:482` 的 `ENTITY_ATTRS["character"]`），**但 26 个角色无一填充**。两条链路都在空转。

### 1.2 骨架是双轨的，改一处不够

章节生成走的是**双轨时间线**：

| 轨 | 数据 | 作用 |
|---|---|---|
| 卷级节奏骨架 | `rhythm_blueprint.chapter_plan`（18 项） | 整卷节奏规划，卷级产物 |
| 章级方向卡 | `ChapterWork.brief.selected` | **正文生成的权威输入** |

`wizard/desk.py:952` 的 plot 层：优先读 `brief.selected`（`_brief_outline_text`），读不到才回退 `outline_map`。`brief.selected` 优先级更高。

所以「调整后续章细纲」必须同时改两层，否则改了卷级骨架但章级方向卡没变，生成出来还是老样子。

### 1.3 场数与字数：结构上被混为一谈

`chapter_plan[*].plot_beats = {qi, cheng, zhuan, he}` 四段，当前被当成**一章的四个场景**。实际上起承转合是**一个场景的内部结构**。

实测 `volumes/2.json` 第 2 卷的 18 章：

```
beat_type 分布: climax 9, engine 5, cooldown 2, hook 1, sell 1
day 分布:       day1×2 day2×1 day3×2 day4×2 day5×3 day6×2 day7×6
suggested_chapter_range: [18, 20]    suggested_length: 81000
每章 words: 4500（全部一样）        总计 81000
```

9 章 climax 挤在 day6-7（各 3 章），这是七日倒计时后段的必然收束，但**每章都 4500 字**、每章都按四段骨架铺满，导致 climax 章的信息密度和 hook 章一样——这就是作者说的「每章信息做得那么复杂」。

### 1.4 字数硬上限 3000 与已定稿数据冲突

```
第1章 定稿 6766 字
第2章 定稿 4994 字
第3章 定稿 6227 字
```

若所有章一律 ≤3000 字，81000 字的卷要拉成 **27 章**（+50%），且 climax 章也被砍到 3000 以内——高潮被削弱。

### 1.5 骨架改了之后实体库会账实分离

`entities/vault_plan.py:486` 的 `seed_from_volume_plan()` 扫 `chapter_plan` 建档。但它是**只增不减**的：骨架里不再出现的实体不会被处理，伏笔状态不回退。

作者明确要求：骨架更新后，什么实体和剧情线细节不在了、什么角色知道了什么信息，都要同步更新。

### 1.6 读者知情 ≠ 角色知情

作者要求新增「读者知情表」：读者这个时间段知道过哪些信息、哪些是读者知道主角不知道、哪些是读者知道反派不知道。

这里有个结构问题：**「读者知道、主角也知道」不是信息差**，只是复述。单列一列没有意义。所以读者层不是一张平行的 `knows`/`blinded_to` 表，而是**信息 × 持有者**的二维矩阵。

---

## 2. 设计

### 2.1 知情表：按章存储，角色层 + 读者层

#### 2.1.1 为什么不能塞进 `entity.state_attrs`

`state_attrs["knows"]` 是**全局单值**。第 7 章知道的信息，到第 12 章就该变——塞进全局字段必然互相覆盖。项目现有的记忆碎片机制（`entities/store.py:367` 的 `MemoryFragment`）已经给出了正确答案：**带闸门的内容单独存**。

#### 2.1.2 存储结构

新建 `backend/app/knowledge/store.py`，项目级 `knowledge.json`：

```json
{
  "chapters": {
    "7": {
      "day": 3,
      "characters": {
        "character-0-唐迟": {
          "knows": ["黑胆珠遇陶露会发热", "寄件人就是陆骁"],
          "blinded_to": ["献名仪式"],
          "knows_detail": ["唐迟已确认寄件人就是陆骁"]
        }
      },
      "reader": {
        "known": ["黑胆珠遇陶露会发热", "寄件人就是陆骁", "唐迟在替陆骁顶罪"],
        "misbelieved": ["唐迟以为替帖能彻底断契约"],
        "unknown": ["陆骁已供第四次血"],
        "gaps": ["阮衡知道献名仪式的全部步骤"]
      }
    }
  }
}
```

**`knows` / `blinded_to` 存关键词，`*_detail` 存完整描述**。

原因：`check_knowledge_boundary()` 靠关键词撞正文（`blinded_to` 必须是 2-12 字、能被正文命中的词），而作者在折叠窗里要看的「阮衡知道献名仪式的全部步骤」是完整句。两个用途不能共用一个字段——共用则检查永远命中不了，不共用则检查无法工作。

`*_detail` 只给作者看和注入任务书，`knows` / `blinded_to` 只给检查和判定。

四类读者信息的用途各不相同：

| 字段 | 含义 | 用途 |
|---|---|---|
| `known` | 读者已确知的信息 | 检查正文有没有**提前泄露**底牌 |
| `misbelieved` | 读者正在相信但为假的认知 | 检查正文有没有跟读者认知**冲突** |
| `unknown` | 读者尚不知道的底牌 | **悬念清单**：写作时别把底牌写光 |
| `gaps` | 读者不知、但某角色知道的信息 | 下一章的紧张感来源，也是反转落点 |

`gaps` 与角色层的 `blinded_to` 是同一份事实的两种视角：`gaps` 从读者看（"读者被瞒着什么"），`blinded_to` 从角色看（"这个角色被瞒着什么"）。

#### 2.1.3 自动推导：草稿 → 作者确认 → 落库

照抄 `entities/memory_gen.py` 已有的范式（它有 `diff_against_current`，作者手写过的默认不勾）。

推导输入四份：

```
1. 原剧本逐日推进（origin_novel.days）
2. 新骨架本章起承转合（chapter_plan[*].plot_beats + scenes）
3. 已写定稿正文（让模型知道唐迟已经发现了什么）
4. 角色档案（summary / details / state_attrs）
```

输出每章每角色一组 `knows` / `blinded_to`，加一份 `reader` 四类。

**失败模式**：凭空推断会把正确信息也判成越界。所以：

- `knows` 和 `blinded_to` 两条都要填。`generation/gate.py:217` 的 `_knowledge_risks` 已经在查「声明了不知情却没声明已知」这种**零误判**的数据不完整——正好复用作校验。
- 作者不确认的条目不落库。
- 推导失败时返回 `{"_error": ...}`，不抛异常阻断作者操作（与 `memory_gen.generate_draft` 一致）。
- `knows` / `blinded_to` 推导成**关键词**（2-12 字），完整描述进 `*_detail`。推导 prompt 里要写明这个约束，否则模型会给完整句，检查链路接回去也是空转。

#### 2.1.4 反向接回正文链路

建完不能只当展示用。现在两条链路在等这份数据：

| 链路 | 现状 | 改法 |
|---|---|---|
| `ledger.py:303` `_knowledge_seg()` | 读全局 `state_attrs`，恒为空 | 先查按章表（按本章章号），查不到再退回旧字段 |
| `book_checks.py:373` `check_knowledge_boundary()` | 同上，空转 | 同上，按章取边界 |

这样知情表才真正起作用：写正文时模型看到「（已知：…；不知情：…）」，审稿时正文撞 `blinded_to` 会被报出来。

**一处需要注意的判定前提**：`check_knowledge_boundary()` 的三个判定条件里，「关键词 K 出现在正文中」这一条要求 `blinded_to` 的**原文**能被正文命中。但按章知情表里存的是结构化条目（如「阮衡知道献名仪式的全部步骤」），直接拿去撞正文会**一个都命中不了**——因为正文不会写「献名仪式的全部步骤」这七个字。

所以按章表的 `blinded_to` 条目要拆成两段：

```json
"blinded_to": ["献名仪式"],
"blinded_detail": "阮衡知道献名仪式的全部步骤"
```

`blinded_to` 存**可被正文命中的关键词**（2-12 字，走 `book_checks._knowledge_keywords()` 的现有切分规则），`blinded_detail` 存完整描述只给作者看。这个拆分不是新增约束，是现有检查逻辑的硬前提——不拆，知情表接回去也是空转。

### 2.2 功能 2：折叠信息窗

位置：`frontend/src/views/DeskView.vue:1221` 现有的 `el-collapse` 里加**第三个折叠项**，与「参考资料注入」「写作模块注入」并列。标题「原剧本·本章视角」。

新增只读端点 `GET /api/projects/{pid}/desk/{chapter}/origin-context`，一次性返回三块 + 本章 `day`。

**为什么不塞进现有 `desk_chapter` 响应**：`main.py:2351` 的 `desk_chapter` 响应已经很重（各阶段全文 + 大纲），而这个数据只在展开折叠窗时才需要，按需拉取。

#### 2.2.1 面板一：原剧本详细剧情

数据源 `origin_novel_of(vol)`（惰性重建，零成本）。三个可切换视图：

| 视图 | 内容 | 实测体量 |
|---|---|---|
| 梗概 | `summary`，含「第1幕」～「第7幕」标记 | 1534 字 |
| 逐日推进 | `days[]`，按本章 `day` 高亮，其余折叠 | 7 条 |
| 通稿节选 | `full_text` 头尾两段（与注入侧一致） | 13532 字全文取头尾 |

逐日推进的窗口半径对齐 `build_origin_material()` 的 `ORIGIN_DAY_WINDOW`（±1 天），保证**作者看到的和模型看到的是同一份**。

#### 2.2.2 面板二：原剧情发展方向

数据源 `vol.tangchi_story_outline`。实测字段齐全：

| 字段 | 字数 | 内容 |
|---|---|---|
| `outline_text` | 2439 | 整条故事线 |
| `core_twist` | ~200 | 真凶是谁、怎么收 |
| `false_cause_red_herring` | — | 假因果 |
| `rule_exploit` | — | 规则怎么被用 |
| `climax` | — | 终局 |

下面挂 `day_breakdown[7]` 表格，每行一天：

```
day | original_timeline_anchor（原剧本时间锚） | fate_layer_focus
    | false_hypothesis（唐迟这天以为的） | real_cause_progress（真因推进到哪）
```

本章那天高亮。

#### 2.2.3 面板三：本时点知情

三个子块：

- **角色层**：表格，角色 / 已知 / 不知情。数据来自 `knowledge.json` 按章表。
- **读者层**：表格，读者已知 / 误以为 / 不知 / 读者被瞒（gaps）。
- **剧情线细节**：本章涉及的 plotline 实体 + 其 `state_attrs["plan_notes_chN"]`。

### 2.3 功能 1：后续章骨架重排

#### 2.3.1 入口

全局写作台左栏（`WritingDeskView.vue`）每卷头部加「�� 重排后续骨架」按钮 → 弹窗。

弹窗内容：

1. 作者提示输入框
2. 只读展示：已定稿章清单（锁定）、当前 18 章骨架摘要
3. 「重排」按钮 → 后端调用 → 返回**逐章 diff 面板**（改前 / 改后 左右对照）
4. 作者逐章勾选接受 → 「接受并写库」

#### 2.3.2 重算方式：分段全卷重算 + 逐章 diff 确认

按 `day` 分 3 段（day1-3 / 4-5 / 6-7）各调一次模型：

- **段内整段重排**：模型看到整段所有章，重排连贯性最好。
- **段间传衔接约束**：上一段的收尾状态作为下一段的输入。
- **单次调用不爆 token**：18 章全字段一次输出容易被截断。

为什么不选另外两种：

| 方案 | 否决理由 |
|---|---|
| 一次性重算到底 | 18 章全字段一次输出易超长被截断；模型看不到「哪些章必须保持不动」的边界 |
| 两段式（先改骨架再逐章重抽方向卡） | 要跑十几次单章调用，等待久；且节奏骨架与方向卡两层分离，会产生「节奏说 A、方向卡说 B」的中间态 |

#### 2.3.3 保护机制（三条，缺一不可）

| 保护 | 做法 | 理由 |
|---|---|---|
| 已定稿章锁定 | `finalized_at` 非空的章不参与重算，只作为上下文喂给模型 | 作者已投入的劳动不能被改 |
| 作者手写章保护 | `brief.is_custom = True` 的章在 diff 面板单独标「你手写的，默认不改」，可手动勾选 | ch1/2/5 是手写的 |
| 骨架快照 | 改前把 `chapter_plan` 存进 `volumes/{no}.json` 的 `chapter_plan_backups`，保留最近 3 次，配「回滚上一次骨架」 | 重排是破坏性操作，必须可逆 |

**额外风险**：rhythm 阶段 `stage_completed` 已锁 True。骨架被改写时打 `chapter_plan_rewritten_at` 标记，rhythm finalize 时检测到就提示「骨架已被改写，重跑会覆盖，是否继续」——而不是无声覆盖。

#### 2.3.4 写入路径

接受后写两层：

```
rhythm_blueprint.chapter_plan   ← 卷级骨架（rhythm_blueprint 加 scenes 字段见 2.4）
ChapterWork.brief.selected      ← 章级方向卡（重建受影响章）
```

重建 `brief.selected` 时保留章级 `plot_beats`（整章起承转合），带上新的 `scenes`，并保留作者填过的 `extra_edits`（除非该章的骨架变化让 extra_edits 失效——此时在 diff 面板标出提醒）。

### 2.4 分档字数与场数

#### 2.4.1 字数分档

`chapter_plan[*].words` 不再一律 4500，按 `beat_type` 分档：

| beat_type | 场数 | 字数区间 | 门禁下限 | 门禁上限 |
|---|---|---|---|---|
| hook / sell | 2-3 | 3000-3500 | 2400 | 3900 |
| engine | 2-3 | 3000-3800 | 2400 | 4200 |
| climax | 3-4 | 3500-4500 | 2800 | 5000 |
| cooldown | 2 | 2500-3000 | 2000 | 3400 |

**为什么不是一刀 3000**：climax 章是陆骁收网、红线结算这类场景，压到 3000 字读者没时间被吓到。hook 章 3000 字正合适。

**卷内总章数**：18 → 24 左右，总字数仍 81000 上下。`day` 分配按 `fate_layer` 密度重排，但 `tangchi_story_outline.day_breakdown`（7 天）不动——**卷的七日结构不能破**。

#### 2.4.2 场数落到数据结构

`chapter_plan[*]` 增加 `scenes[]`：

```json
{
  "chapter": 8,
  "day": 4,
  "beat_type": "climax",
  "words": 4000,
  "title": "罪身入局",
  "focus": "唐迟主动承认寄帖，把警方的调查矛头引向阮衡诊所",
  "plot_beats": { "qi": "…", "cheng": "…", "zhuan": "…", "he": "…" },
  "scenes": [
    {
      "name": "警局自首",
      "goal": "用真名换取随行查验资格",
      "resistance": "周朔不接受主动投案",
      "beats": { "qi": "…", "cheng": "…", "zhuan": "…", "he": "…" },
      "sensory": "手铐的金属凉意压在腕骨上",
      "ending": "周朔宣布私自离城视为畏罪"
    }
  ],
  "characters_on_stage": ["唐迟", "周朔", "何淮", "阮衡", "陆骁"],
  "foreshadow_seeds": [],
  "foreshadow_payback": [],
  "sensory_hook": "…",
  "time_window": "入戏第4天",
  "location": "雾汀县公安局"
}
```

旧 `plot_beats` 保留在章层（整章起承转合），新 `scenes` 进场景层，每场自带四段起承转合。`brief.selected` 同步带 `scenes`，这样章细纲生成时模型知道「本章分几场、每场干什么」。

#### 2.4.3 门禁调整

`generation/quality.py`：

| 位置 | 现状 | 改后 |
|---|---|---|
| `hard_gate()` 字数检查（L120-126） | `target_chars ±30%/±80%` 单一区间 | 按 `beat_type` 取分档区间 |
| plot 层场数（L220） | 只判 `< 2` 为 P0 | 加「场数 > beat_type 上限」为 **P1**（不能硬卡 climax 的 4 场） |
| plot 场次标记识别（L208-210） | 已兼容「第N场」「场N」「场景N」 | 沿用 |

字数门禁的 `target_chars` 来源：现在 `engine.py` 的 `PipelineSpec` 是**按 stage 的固定值**（draft 4800 / polish 5000），不含章级分档。要让分档生效，得让章级 `words` 覆盖 stage 默认值——这是本设计对 `engine.py` 的唯一改动点。

### 2.5 实体库对账（骨架改后同步）

作者明确要求：骨架更新后，什么实体和剧情线细节不在了、什么角色知道了什么信息，都得同步更新。

**原则：软标记，不硬删。**

| 情况 | 处理 | 理由 |
|---|---|---|
| 新增角色/地点/物品/剧情线/伏笔 | 建档，走现有 `seed_from_volume_plan` | 新骨架出现的东西必须建档 |
| 新骨架不再出现、且只在重算范围内出场过 | `state_attrs["plan_removed_at"] = 章号`；从 `appearances` 摘掉该范围内章号 | 实体库是资产，角色可能在新骨架外的其他卷还会出现 |
| 伏笔 | **不回退 `planted` 状态**，只标记「将来不再推进」 | 已埋的伏笔不能当没埋过，硬删会让已写章节正文与账本对不上 |
| 剧情线细节 | 写进 plotline 实体的 `state_attrs["plan_notes_chN"]` | 让「每场干什么」可查 |
| 角色知情 | 骨架改完重新推导，写进按章知情表 | 骨架变了，谁知道什么也得变 |

新建 `app/entities/vault_plan.py` 的 `reconcile_with_plan()` 函数，承担这个对账。

---

## 3. 数据流

### 3.1 骨架重排

```
作者提示
  → POST /brief/replan（按 day 分 3 段调模型）
  → 返回逐章 diff（不写库）
  → 作者逐章勾选
  → POST /brief/replan/apply
      ├─ 写 rhythm_blueprint.chapter_plan（含 scenes）
      ├─ 重建受影响章 brief.selected
      ├─ entities.reconcile_with_plan()   软标记对账
      ├─ knowledge 按章重新推导（草稿态）
      └─ 打 chapter_plan_rewritten_at 标记
```

### 3.2 知情表

```
原剧本逐日推进 + 新骨架本章起承转合 + 已写定稿正文 + 角色档案
  → POST /knowledge/draft（模型推导）
  → 返回草稿（不写库）
  → 作者逐条勾选
  → POST /knowledge/apply
      → 写 knowledge.json
      → 后续写正文时 ledger._knowledge_seg() 自动注入
      → 审稿时 book_checks 按章取边界
```

### 3.3 折叠信息窗

```
展开折叠项
  → GET /desk/{chapter}/origin-context
      ├─ origin_novel_of(vol)     惰性重建，零 token
      ├─ tangchi_story_outline    字段齐全
      └─ knowledge.json 按章表    角色层 + 读者层
```

---

## 4. 端点清单

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/projects/{pid}/desk/{chapter}/origin-context` | 折叠信息窗三块数据 |
| POST | `/api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan` | 重排后续章骨架（返回 diff，不写库） |
| POST | `/api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan/apply` | 接受 diff，写骨架 + 重建方向卡 + 对账实体库 |
| POST | `/api/projects/{pid}/workbench/volumes/{vol_no}/brief/replan/restore` | 回滚上一次骨架 |
| POST | `/api/projects/{pid}/knowledge/draft` | 知情表草稿推导（角色层 + 读者层） |
| POST | `/api/projects/{pid}/knowledge/apply` | 落库知情表 |
| GET | `/api/projects/{pid}/knowledge` | 读知情表全量（编辑页用） |

`replan` 是**卷级**操作（一次重排后续多章），所以三个端点都挂在 `/volumes/{vol_no}/brief/` 下，不带 `chapters/{cno}`——与已有的 `brief/options`、`brief/select`（章级）区分开。

---

## 5. 错误处理

| 场景 | 处理 |
|---|---|
| 模型调用失败 | 返回 `{"_error": "..."}`，不写库，不阻断作者操作 |
| 知情推导失败 | 同上；草稿区显示「生成失败，手动填写即可」 |
| 体积过滤后章数变化 | `brief.selected` 重建时按新章号映射，章号变化要在 diff 面板显式标出 |
| 实体对账时实体不存在 | 跳过并记入报告，不抛异常 |
| 回滚时快照已满 3 次 | 丢弃最旧的一次 |
| `chapter_plan` 为空（卷没跑过 rhythm） | 端点返回 422「请先推演节奏蓝图」 |

---

## 6. 测试策略

新建 `backend/tests/test_skeleton_replan.py`：

| 用例 | 断言 |
|---|---|
| 已定稿章不参与重排 | diff 里不含已定稿章 |
| 作者手写章默认不勾选 | diff 面板标记为 custom |
| 骨架改后 `brief.selected` 重建 | 受影响章的 selected 含新 `scenes` |
| 实体对账：软标记不删实体 | `plan_removed_at` 写入，实体仍在库 |
| 实体对账：伏笔不回退状态 | `planted` 仍是 `planted` |
| 字数分档门禁 | climax 章 4500 字通过；engine 章 4500 字触发 P1 |
| 场数上限 | climax 章 4 场通过；engine 章 5 场触发 P1 |
| 知情表按章隔离 | 第 7 章的 knows 不影响第 12 章 |
| 读者层四类齐全 | `known/misbelieved/unknown/gaps` 都有值 |
| `blinded_to` 存关键词 | 推导结果里每条 `blinded_to` 长度在 [2, 12]，否则检查链路空转 |
| `ledger._knowledge_seg()` 优先读按章表 | 注入文本含本章的已知/不知情 |
| `book_checks` 按章取边界 | 第 7 章的 `blinded_to` 不误报第 12 章 |
| 回滚 | 骨架恢复到快照内容 |

---

## 7. 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/book/volume.py` | `chapter_plan` 支持 `scenes[]`；新增 `chapter_plan_backups` / `chapter_plan_rewritten_at` 字段 |
| `backend/app/api/rhythm_routes.py` | `chapter_plan` schema 加 `scenes`；字数分档校验；finalize 检测改写标记 |
| `backend/app/api/brief_routes.py` | `brief.selected` 透传 `scenes`；新增 `replan` / `replan/apply` / `replan/restore` 端点 |
| `backend/app/api/main.py` | 新增 `GET /desk/{chapter}/origin-context` |
| `backend/app/generation/quality.py` | 字数门禁改分档；plot 层加场数上限 P1 |
| `backend/app/pipeline/engine.py` | 章级 `words` 覆盖 stage 默认 `target_chars` |
| `backend/app/context/ledger.py` | `_knowledge_seg()` 优先读按章知情表（含读者层） |
| `backend/app/review/book_checks.py` | 知情边界检查按章取 |
| `backend/app/entities/vault_plan.py` | 新增 `reconcile_with_plan()`（软标记对账） |
| `backend/app/knowledge/store.py`（新） | 按章知情表存储（角色层 + 读者层） |
| `backend/app/knowledge/gen.py`（新） | 知情表自动推导（草稿 → 确认 → 落库） |
| `frontend/src/views/DeskView.vue` | 折叠信息窗第三项（面板一二三）；场数/字数展示 |
| `frontend/src/views/WritingDeskView.vue` | 「重排后续骨架」按钮 + 逐章 diff 弹窗 |
| `frontend/src/api.ts` | 新端点的 TS 封装 |

---

## 8. 已知边界

1. **已定稿的 ch1-3 保留原样**。三章合计 17987 字，超出新的分档区间。这是作者既成事实，不在本次重排范围内。
2. **本卷 18 章 → 24 章会改变章号**。ch1-3 章号不变，ch4 之后顺延。`brief.selected` 按