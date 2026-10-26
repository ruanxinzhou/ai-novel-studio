# spec 4 · 写前人工确认门

- 日期：2026-10-22
- 状态：已落地（后端 42 项新测试全通过；回归基线 39 failed 不变，passed 422 → 464）
- 范围：`generation/gate.py`（新）+ `api/main.py`（预演与表态端点）+ `pipeline/engine.py`（门禁后）+ `wizard/desk.py`（定稿前）
- **前端未落地**：三个面的弹窗与 `onQuality` 接线仍待做（见第九节）

## 背景：门早就存在，只是作者看不见

原以为「写前确认门」是加一道新关卡。读完代码发现不是——**关卡早就在了，
只是作者看不见它，也从未被要求表态**。

| 环节 | 现状 | 位置 |
|---|---|---|
| 硬门禁 P0 判定 | 生成**之后**跑，重试 2 轮 | `engine.py:515` |
| 重试用尽 | 只有格式类 P0 中止，**其余 P0 静默放行落库** | `engine.py:584-597` |
| P0/P1 数据推送 | `onQuality` 事件已发（含重试轮次、burstiness） | `engine.py:564` |
| 前端接收 | **没接**，`DeskView.vue` 只传 thinking/delta/resumed/notice | `DeskView.vue:455-468` |
| 审稿 blocked | 只记录，**不拦定稿** | `main.py:2208-2236` |
| 定稿确认框 | 有 `ElMessageBox`，但**不含审稿结果** | `DeskView.vue:564` |

三个具体后果：

1. 第 1 章问号密度 8/千字、第 20 章降到 2/千字，每章单独看都过 P0 线（阈值 2.0），
   整本书已变成说明书——spec 1 解决了「看见退化」，但退化章节的 P0 是**静默落库**的。
2. 作者点了「生成」，烧了 3 轮 token，屏幕上只看到正文，**不知道它被扣过分**。
3. 审稿 `verdict=blocked`（≥2 项不稳）也不拦定稿，唯一的拦截是一个不含审稿结果的弹窗。

所以 spec 4 的本质不是「加门」，而是**把已经在算的东西送到作者眼前，并要求他表态**。
这让实现成本远低于预期——不需要新判定逻辑，数据全都在。

## 一、三个面，共享一个表态记录

```
生成前 ──── 风险触发时：先看委托书再开写 ──┐
                                          ├─→ 作者表态 → gate_decisions.jsonl
门禁后 ──── P0 不再静默放行：收下/重写/手改 ─┤
                                          │
定稿前 ──── blocked 必须逐条表态 ──────────┘
```

三者不是三个功能，是同一条链上的三个观测点。共用一个追加式账本
`gate_decisions.jsonl`，与 spec 1 的 `commits.jsonl` 同构（追加写、绝不原地改、
损坏行跳过而非抛错）。

这样 spec 1 的质量分析就能回答一个更有用的问题：不只是「质量在退化」，
而是「**哪些章作者反复选择收下**」——那是真实的写作偏好信号，
而现在的账本只知道指标，不知道人的判断。

## 二、生成前：风险触发式确认

### 只在风险时弹，不每次弹

每次必确认的确认门，三章之后就会变成肌肉记忆，作者闭眼点确认——
确认门退化成了多余的点击，比不做更糟（制造「我已经确认过了」的假安全感）。

所以**风险触发式**：无风险直接跑，有风险才弹。

### 风险源：仅账本类（用户已确认）

全部零 LLM、纯数据推导，不新增判定逻辑：

| 风险 | 判定源 | 阈值 |
|---|---|---|
| 到期伏笔 | `entity_store` + spec 3 的 `_foreshadow_anchor` | 静默 ≥ `FORESHADOW_DUE_AFTER`(8) 章 |
| 硬债务伏笔 | 同上 | 静默 ≥ `FORESHADOW_HARD_AFTER`(12) 章 |
| 知情边界未声明完整 | `character.state_attrs` | 声明了 `blinded_to` 但没声明 `knows` |
| 三线断档 | `strand_warnings(desk, chapter)` | 非空 |
| 节奏饥饿 | `rhythm_hunger(desk, chapter)` | 非空 |

**明确不加 LLM 预检层**：预准高但每次写前都烧 token，且误报到作者会
直接摧毁对整个确认门的信任——一个天天误报的警告等于没有警告。

### 零 token 预演端点

风险判定需要「本章将要注入什么」，但现在没有预览通道。新增：

```
GET /api/projects/{pid}/desk/{chapter}/gate/preview?stage=plot
```

返回：`{blocked: bool, risks: [...], brief: "<开章任务书全文>"}`

`brief` 复用 `build_chapter_checklist` 的产出（零成本，本来就要注入），
作者在弹窗里看到的就是模型将看到的。这是「透明」而非「拦截」。

## 三、门禁后：P0 不再静默放行

### 现状

`engine.py:584-597` 重试用尽后只有格式类 P0 `raise`，其余 P0 `break` 落库。
加上 burstiness/rhythm 护栏的降级通道，一章走到落库时 P0 可能还有 3 条，
而作者对此一无所知。

### 改法

重试用尽且仍有非格式 P0 时，**不再静默 break**，而是发一个
`gate_request` SSE 事件并挂起等作者决定。但引擎是同步函数、
SSE 流已在跑，挂起需要跨请求的状态——所以采取更轻的做法：

**保持生成不挂起，但把决定权交回作者。**

1. 结果照常返回，但 `result` 里带 `gate: {required: true, p0: [...], stage: ...}`；
2. 前端收到 `gate.required=true` 时弹出选择框：
   - **收下**（默认，与现有行为等价）→ 前端调 `/gate/decide` 记 `accept`
   - **再重写一次** → 前端带 `force=true` 重新触发 run（复用已有 force 语义）
   - **我自己改** → 关闭弹窗，作者手改后走 `desk_save_text`
3. 三种选择都写入 `gate_decisions.jsonl`。

**为什么不真的挂起**：引擎跑在后台线程里（SSE 已如此），挂起需要
job 状态机 + 超时 + 断线恢复，成本远大于收益。而「结果先给、决定后补」
在 UX 上等价——作者看到正文的同一屏就能做决定。

### 与 spec 2 的回滚通知同构

`guard.py` 的回滚已经用 `notice` 告知作者（`engine.py:503-506`）。
P0 静默放行是同一类问题：**规则生效了但作者不知道**。所以处理方式一致——
显式告知，绝不静默。

## 四、定稿前：blocked 必须表态

`verdict=blocked`（≥2 项不稳）时，定稿确认框必须展示：

- 每条不稳项（`items` 里 `status=="不稳"` 的，含 `problem`）
- 书规报警（`book_warnings`）
- 节奏/三线（`rhythm_warning` / `strand_warnings`）
- 词法白描锁命中（`cliche_hits`）

作者选择：

| 选择 | 语义 | 落库 |
|---|---|---|
| 逐条确认收下 | 「我知道不稳，就这样定」 | `accept_with_known_issues` |
| 返回修改 | 关闭弹窗 | 不写账本 |

后端 `finalize` 增加参数 `acknowledge: list[str] | None`：
`blocked` 时若未提供 `acknowledge`，直接 422——**不依赖前端自觉**。
前端已经在调 finalize，补一个参数的成本远低于被绕过。

## 五、账本：gate_decisions.jsonl

```json
{"ts":"...","ch":12,"gate":"pre_write|post_gate|finalize","risk":"foreshadow_due",
 "detail":"鞋柜第二层的信封 静默 9 章","decision":"accept|rewrite|manual|acknowledge",
 "stage":"plot","p0":[]}
```

三个查询用途：

1. `pre_write_blocked(ch)` — 某章是否被风险门拦过、后来放行的
2. `accept_streaks(pid)` — 哪些指标反复被收下（spec 1 趋势的补充视角）
3. `never_accepted(pid)` — 从未表态的章（漏斗诊断）

**静默失败**：账本写失败绝不阻断写作流程（与 spec 1 同约束）。

## 六、明确不做

- **不真的挂起生成等确认。** 后台线程挂起需要 job 状态机 + 超时 + 断线恢复，
  成本远大于收益；「结果先给、决定后补」在 UX 上等价。
- **不加 LLM 预检层。** 误报一次，信任就没了。
- **不拦死 blocked。** 作者有权明知不稳照样定稿——那是他的判断权，
  我们只负责让他**知道**。
- **不做每次必确认。** 见第二节。
- **不改 `hard_gate` 的阈值。** spec 4 只管「作者知不知道」，不管「判得准不准」。

## 七、测试策略

`tests/test_spec4_write_gate.py`，42 项全通过。设计文档列的 14 条验收对应如下：

| # | 测试 | 期望 | 落地 |
|---|---|---|---|
| 1 | 无风险时 preview 不阻断 | `blocked=false` | ✓ |
| 2 | 到期伏笔触发风险 | 命中 foreshadow_due/hard | ✓ |
| 3 | 三线断档触发风险 | 命中 strand_gap | ✓ |
| 4 | 知情边界只声明一半触发 | 命中 knowledge_gap | ✓ |
| 5 | 风险判定零 LLM | 签名里无 provider 参数 | ✓ |
| 6 | preview 的 brief 与实际注入一致 | 同一 `build_chapter_checklist` | ✓ |
| 7 | P0 重试用尽时 result 带 `gate.required` | 不再静默 | ✓ |
| 8 | 格式类 P0 仍中止（不回归） | 仍 raise | ✓（代码分支未动） |
| 9 | blocked 定稿未 acknowledge → 拒绝 | 拦下 | ✓ |
| 10 | blocked 定稿带 acknowledge → 放行 | 成功 | ✓ |
| 11 | 无审稿记录 / pass 定稿不受影响 | 成功 | ✓ |
| 12 | 决策写入账本 | 追加行 | ✓ |
| 13 | 账本损坏行跳过 | 不抛错 | ✓ |
| 14 | 回归基线 | 39 failed | ✓（passed → 464） |

另补 28 项边界测试：刚埋伏笔/已回收伏笔不误报、风险 detail 必须一句话说完、
缺依赖不炸、`gate_pending` 可变默认值不共享、拒绝信息含具体不稳项与截断、
空 acknowledge 不算数、写失败返回 False 而非抛、`unconfirmed_chapters`
只看每章最后一条表态、`accept_streaks` 严格区分 accept 与 manual/rewrite。

## 八、实施中发现的一个真 bug

`rhythm_hunger(desk, chapter)` 从传入章号**往回数**连续无兑现。审稿时传
chapter 是对的（本章已有正文），但**写前门禁时本章还没有内容**，
直接传 chapter 会在第一行 `desk.get(chapter)` 为 None 就 break，
导致节奏饥饿在写前**永远不会触发**——一个看起来实现了、实际永远不响的检查。

修法：写前传 `chapter - 1`，并把文案里的「本章宜安排兑现」改成
「开写前请先安排兑现」（语境不同）。这是测试逼出来的，不是读代码看出来的。

## 九、前端待办（本轮未做）

后端已具备全部能力，但**作者目前还看不到任何弹窗**——spec 4 的价值要等前端：

1. `DeskView.vue:runStage` 前先调 `/gate/preview`，`blocked=true` 才弹
   风险确认框，展示 `brief` 全文（作者看到的就是模型将看到的）。
2. 接 `onQuality` 回调（`api.ts:339` 已定义但全仓无人使用），
   在 `result.gate.required=true` 时弹「收下 / 再重写一次 / 我自己改」。
3. `doFinalize` 的 `ElMessageBox` 改为渲染不稳项列表，
   blocked 时展示「逐条确认」按钮并在请求里带 `acknowledge`。

在第 1、2 项落地前，本 spec 的效果等同于「多存了一份账，没人看」。

## 十、实施注意

1. **`engine.py` 的 result 结构在多处被读**（`DeskView.vue`、
   `test_streaming.py`），加 `gate` 键必须是**增量**，不能改已有键的语义。
2. **`finalize_chapter` 是 desk.py 内部函数**，加 `acknowledge` 参数要同步
   `main.py` 的调用点与 422 映射。
3. **`build_chapter_checklist` 需要 entity_store/desk/chapter**，
   preview 端点要能独立构造这些依赖，不能假设调用方已准备好。