# 副本跑团 · 失败可见性（第一步）

- 日期：2026-10-22
- 状态：已批准，待实施
- 范围：`pipeline/coc_simulator.py` 失败路径 + `api/main.py` + `frontend/src/views/CocSimView.vue`
- 上游依据：`6ad86bdbd53abcde5e3fd6e5/docs/superpowers/specs/2026-10-22-dungeon-workflow-readability-design.md` §2.1 / §5.1 / §5.2

## 零、这一步在做什么

上游设计把「修静默 catch」列为第1 步，理由是「不修，后续全是空的」。
**本 step 保留这个判断，但修正它的执行内容**——见第一节。

只做一件事：**让跑团失败在界面上明显可见，作者不用读内容就能看出来。**
不做终止机制重构（beat /预算 / 剧情收束信号），那是第二步。

## 一、必须先纠正的前提

上游设计 §2.1 断言「42 场里有 16 场 `final_summary` 是 Provider 报错文本」，
并据此认为需要「修Provider 崩溃」。

**核实结论：这条诊断已过时，照着改会改错地方。**

| 上游断言 | 实测（2026-10-22） |
|---|---|
| 42 场中 1 场有内容、16 场报错文本、25 场空 | **0 场成功**：35 场空 + 7 场错误文本 |
| 错误是 `MiniMax 网络错误` | 15 场是 `AttributeError: CONTEXT`，仅 1 场是 Provider 网络错误 |

`AttributeError: CONTEXT` 抛在 `coc_simulator.py:246` 的
`MaterialFamily.CONTEXT`——枚举成员当时不存在，异常发生在材料装配阶段，
**一个token 都没进 LLM**，所以 `turns=[]`。

**它已在 10-18 22:10 被 `d1fa5af` 修掉。** 失败数据全部生成于那之前；
之后的 13 个项目 26 个会话零失败。

所以第一步的真实价值不是「修 bug」，而是：

1. **别把已修的 bug 再修一遍**；
2. **让下一次失败时作者能看见**——这才是可用性底线。

## 二、当前失败路径的三处「看起来像成功」

### 2.1 `_run_one_session` 把异常写进 final_summary（1252-1266）

```python
except Exception as e:
    raw = f"（本场压真相失败：{type(e).__name__}: {e}）\n\n```\n{tb}\n```"
    state.final_summary = raw# ← 错误文本占据了「梗概」的位置
```

前端 `CocSimView.vue:585-586` 用 `final_summary` 作为卡片正文，
所以作者看到的是「一份有内容的梗概」，内容是 traceback。
**这是最有害的一处**：它把失败伪装成了成功产物。

### 2.2 0 回合退出也写进 final_summary（1235-1245）

```python
if state.error and autorun_result.get("reason") == "error" and len(state.turns) == 0:
    state.final_summary = f"（本场仿真未能启动：{state.error}）"
```

同样的问题：写在 `final_summary` 里而不是 `error` 里。

### 2.3 线程级异常伪造一张「候选卡」（1377-1399）

```python
except Exception as e:
    err_card = {..., "final_summary": f"ERROR: {type(e).__name__}: {e}", ...}
```

卡片形状与成功卡完全一致，只有 `one_liner` 里带一句「本场执行失败」。

## 三、设计

### 3.1 状态用 `status` 表达，不用内容表达

`CocRunState.status` 已有 `failed` 之外的取值（`coc_run.py:219`：
`pending/running/paused/awaiting_author/finalized/aborted`）。
**新增 `failed`**，与 `paused` 区分：

- `paused`：跑过、被作者叫停，可以续跑
- `failed`：出错，作者需要决定重跑还是放弃

### 3.2 三处改动

**改动 1 · `_run_one_session` 的 finalize 失败（1252-1266）**

```python
except Exception as e:
    state.status = "failed"
    state.error = f"{type(e).__name__}: {e}"
    state.final_summary = ""              # ← 关键：不再往梗概位写错误
    state.final_day_breakdown = []
    state.survivors = _collect_survivors(state)
```

**改动 2 · 0 回合退出（1235-1245）**

同样把文本从 `final_summary` 挪到 `error`，并置 `status = "failed"`。

**改动 3 · 线程级异常（1377-1399）**

`err_card` 增加 `status: "failed"` / `failed_stage: "thread"`，
`final_summary` 保持空串，`error` 带类型与消息。

### 3.3 卡片统一带 `run_status`

`_summary_card_from_state` 增加：

```python
"run_status": "ok" if (not state.error and state.status != "failed") else "failed",
"fail_stage": "",# 由调用方按场景填
```

前端据此决定显示什么。**不依赖 `final_summary` 是否有内容来判断成败**——
这是本 step 的核心：成败是独立字段，不是内容的副产品。

### 3.4 前端三态视觉区分

`CocSimView.vue` 的候选卡：

| run_status | 显示 |
|---|---|
| `ok` | 现状不变 |
| `failed` | 红色边框 +「本场失败」角标 + 失败原因 + 重跑按钮 |

失败卡的`item-content-field` 改为显示错误摘要而不是空梗概。

## 四、不做

- **不碰 `autorun_until_break` 的终止逻辑。** `pair_limit` / `max_turns` /
  `story_phase` 全部留到第二步。现在改它们会让「失败可见」这件事无法单独验证。
- **不修 Provider 稳定性。** 单场网络错误重跑即可，不需要代码改动。
- **不自动重跑。** 作者决定重跑还是放弃——副业场景下自动重跑会烧 token。
- **不改 `_check_termination`。** 同上。

## 五、测试策略

| # | 测试 | 期望 |
|---|---|---|
| 1 | finalize 抛异常 → status=failed | 状态正确 |
| 2 | finalize 抛异常 → final_summary 为空 | 不再伪装成梗概 |
| 3 | finalize 抛异常 → error 带类型与消息 | 可诊断 |
| 4 | 0 回合退出 → status=failed | 状态正确 |
| 5 | 0 回合退出 → final_summary 为空 | 不再伪装 |
| 6 | 线程异常 → 卡片带 run_status=failed | 可区分 |
| 7 | 正常跑完 → run_status=ok | 不误判 |
| 8 | 有 error 但跑完了 → 仍是 ok | 不误判 |
| 9 | 前端状态映射：failed → 红色态 | 视觉可辨 |
| 10 | 回归基线 | 39 failed / 464 passed |

## 六、实施注意

1. **`coc_run.py:219` 的 status 注释要同步更新**，加上 `failed`。
2. **`EmbodyView.vue:635-636` 读 `final_summary`**——清空后那里会显示空。
   这是**正确的**（失败的场不该有梗概），但要确认 UI 不会因此崩。
3. **`CocSimView.vue:56` 的 `final_summaryOf()`** 同理。
4. `state.error` 已有 `intervene()` 会清空（`coc_simulator.py:548`），
   所以作者介入后重跑会自动恢复——这个行为要保留。