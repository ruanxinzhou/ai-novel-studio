# 副本跑团第二步 · beat 引擎与通稿层

- 日期：2026-10-22
- 状态：已批准，待实施
- 范围：`pipeline/coc_simulator.py` + `fields/coc_run.py` + `frontend/src/views/CocSimView.vue`
- 上游依据：`6ad86bdbd53abcde5e3fd6e5/.../2026-10-22-dungeon-workflow-readability-design.md` §2.5 / §4.1 / §4.2
- 前置：第一步（失败可见性）已落地

## 零、这一步在做什么

把跑团从「跑够 6 次循环就砍」改成「**故事讲完了才停**」。

四件事耦合很紧，必须一起改完再测：

1. 单位从「回合对」改为「beat」（一次完整因果循环）
2. KP 双输出：结构化裁决记录 + 降级后的叙事描写
3. `story_phase` 收束信号 → 终止 reason 区分「讲完了」/「被砍了」
4. 跑团通稿层：8000–12000 字连续叙事

## 一、必须先纠正的两条前提

上游设计 §4.1.4 与 §4.1.1 引用了**不存在的字段**。照着改会直接 KeyError。

### 1.1 `adapt_parsed.seven_day_arc` / `key_clue` 不存在

上游预算公式 `beat_budget = acts × 4 + clues × 2 + 8` 依赖这两个字段。
全仓 grep：**这两个键只在 `prompts.py` 的一行提示词文本里出现，
从未在任何数据结构的 schema 里定义过**。`init_run` 实际写入 `setup_meta` 的只有：

```
title / world_premise / hook / obsession_power / obsession_cost
```

**所以本 step 不实现那个公式。** 改为：

- 预算来源改成**可推断的真实信号**：`len(cast)`（NPC 卡司人数）+ `len(player_cards)`（调查员人数）+ 模组文本长度分档
- 公式与系数在第一个副本跑完后用实测数据校准（见第七节）
- 在此之前用**保守的固定档位**，并允许作者在 UI 覆盖

### 1.2 `current_day` 不是纯污染，部分地方有真实用途

上游说「`current_day` 是假变量，在三处污染 prompt」。核实后：

- **该删的**：`_players_status_line` 的「阶段N/7」（159/168 行）、
  KP prompt 的「阶段 4-6 必须推进反转」（272 行）、PL 的「Day{current_day}」（385 行）
- **该留的**：`detect_day` 从 KP 文本里抓「第N天」用于 `day_breakdown` 分段；
  `state.final_day_breakdown` 与前端「按天分段」展示依赖它

所以本 step **改名不删字段**：`current_day` → 语义改为「故事内时间标记」，
prompt 里不再出现「阶段N/7」这种伪进度，改用「第N beat」。

## 二、beat 单位

```python
BEAT_MIN = 8       # 下限：任何模组至少 8 个 beat
BEAT_MAX = 60      # 上限：防止失控烧 token
```

一个 beat = 一次完整循环：

```
PL 试探动作 → KP 裁决（得到什么信息/结果）→ 剧情状态变化 → PL 面对新情况再试探
```

`steps_done` 的语义从「回合对」改为「beat」，一个 beat = 一次
`step_keeper + step_investigator`，所以数值上与现在的 `steps_done` 一致——
**改名不改行为**，好处是可以灰度：先只加 `story_phase` 收束，
预算放大到 24，观察一轮再校准。

## 三、KP 双输出

### 3.1 输出格式

每次裁决输出 JSON + 叙事：

```json
{
  "beat": 7,
  "kp_verdict": {"result": "成功|失败|部分成功", "mechanism": "裁决依据"},
  "information_gained": "本轮获得的具体信息",
  "world_state_change": "世界/剧情因此发生的变化",
  "known_now": ["此刻所有调查员已知的事"],
  "unknown_now": ["还没查清的事"],
  "next_pressure": "新的压力/悬念",
  "story_phase": "展开中|转折点|已收束"
}
```

外加 200–400 字叙事描写（从现在的 800–1200 降级）。

### 3.2 为什么要 known_now / unknown_now

当前线索记在 `state.clues_found`，但**下一轮 KP prompt 不拿它当约束**，
导致 PL 会在已查清的事上重复试探。这两个字段必须**回注到下一轮 prompt**。

实现：`state.beats[]` 存结构化记录，`_keeper_materials_and_x` 里加一条
`coc:beats` 材料，带上最近 3 个 beat 的 `known_now`/`unknown_now`。

### 3.3 story_phase 双轨制

- **常规**：每 5 个 beat 输出一次，避免每轮都填字段分散 KP 注意力
- **豁免**：KP 感觉剧情即将收束时可随时主动输出

判定：以显式输出为准，留空则继承上一轮并按「展开中」处理。

### 3.4 解析容错

LLM 不保证输出合法 JSON。解析失败时：

- `story_phase` 按「展开中」处理（不猜）
- 其他字段留空，叙事描写照常使用
- **不抛异常**——一次解析失败不该让整场跑团挂掉

## 四、终止判定

```python
def _check_termination(state, last_phase):
    if _all_players_down(state):        return "dead"
    if last_phase == "已收束":           return "story_complete"
    if last_phase == "转折点" and consecutive_transition >= 2:
                                        return "transition_done"
    if state.beats_done >= state.beat_budget: return "hard_cap"
    if len(state.turns) >= state.max_turns:   return "max_turns"
    return None
```

### 4.1 reason 语义必须区分

| reason | 含义 | 作者动作 |
|---|---|---|
| `story_complete` | 故事讲完了 | **可直接用** |
| `transition_done` | 转折已走完 | 可用 |
| `hard_cap` | 撞上 beat 预算被截断 | **看未完成标记，决定是否加预算重跑** |
| `max_turns` | 撞上消息条数上限 | 同上 |
| `dead` / `insane` | 团灭 | 正常结局 |

**`hard_cap` 的产出必须明写「本线在第 N beat 被截断，剧情未完成」**，
不能假装讲完了。这是本 step 最重要的产品改动。

### 4.2 保留 max_turns

上游 §4.1.5 说「`max_turns` 从 `_check_termination` 移除」。**不采纳**：
它是消息条数硬顶，防单次跑团无限烧 token。`beat_budget` 是语义预算，
两者角色不同，都留着。`beat_budget` 先撞（它是更紧的那个）。

## 五、通稿层

输入：全部 beat 结构化记录 + 叙事描写拼接
输出：8000–12000 字连续故事

硬约束（三条都是作者明确要求，不是我的发挥）：

1. 人物首次出场写全名+身份，之后用姓名，**禁止用姓氏代替**
2. 不出现 DayN 标记，用故事内时间表述
3. 连续叙事，不用条目/清单体；不出现骰子术语、SAN/HP、KP/PL 字样

### 5.1 结构化记录是骨架约束

通稿必须覆盖每一拍的信息增量——**不能凭空添加 beat 里没有的情节，
也不能遗漏**。这是「因果链完整性」的技术保证。

### 5.2 与现有 `coc_outcome` 的关系

现有 `_finalize_session_inline`（1088 行）已经是「日志 → 梗概」，
但只产出 1200 字左右、且是「第N幕」结构体。

**通稿层是它的超集**，不是替换：`final_summary` 保持现有格式（下游
`EmbodyView`/`day_breakdown` 在读），新增 `full_narrative` 字段承载通稿。
两个都产出，各服务各的下游。

## 六、不做

- **不删 `current_day` 字段**（见 §1.2）
- **不实现上游的 `acts × 4 + clues × 2 + 8` 公式**（依赖不存在的字段）
- **不自动重跑** beat 超预算的场——作者决定
- **不动锚点层与魂穿层**（上游第三、四步，本 step 之后）
- **不并行化** beat 记录的处理

## 七、待实测校准的参数

| 参数 | 首版取值 | 校准方式 |
|---|---|---|
| beat 预算档位 | 见 §2 | 对比 `story_complete` 时实际消耗的 beat 数 |
| `story_phase` 输出间隔 | 每 5 beat | 观察转折点是否常落在非 5 倍数 |
| 叙事描写长度 | 200–400 字 | 观察是否足够支撑通稿 |
| 通稿篇幅 | 8000–12000 字 | 作者确认是否够当创作底本 |

**校准时的判断准则**：若实际消耗远低于预算，说明 KP 收束过早，
应上调**终止灵敏度**而非砍预算——不要把「故事讲太快」误判成「预算给多了」。

## 八、测试策略

| # | 测试 | 期望 |
|---|---|---|
| 1 | beat 计数 = keeper+investigator 配对数 | 一致 |
| 2 | `story_phase=已收束` → reason=story_complete | 提前停 |
| 3 | `story_phase=转折点`×2 → transition_done | 转折后停 |
| 4 | 撞预算 → reason=hard_cap | 不是 story_complete |
| 5 | hard_cap 产出含「被截断」字样 | 不假装讲完 |
| 6 | JSON 解析失败不抛异常 | 容错 |
| 7 | 解析失败时 story_phase 按展开中 | 不猜 |
| 8 | 留空 story_phase 继承上一轮 | 双轨制 |
| 9 | 转折点豁免：非 5 倍数也能输出 | 不延迟 |
| 10 | beats 回注下一轮 prompt | known_now 可见 |
| 11 | 通稿含人名规则 | 全名+禁姓氏 |
| 12 | 通稿无 DayN | 正则检查 |
| 13 | `final_summary` 保持旧格式 | 不破坏下游 |
| 14 | `full_narrative` 独立字段 | 下游可分别取 |
| 15 | 回归基线 | 39 failed / 485 passed |