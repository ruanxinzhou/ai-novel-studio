# 设计：原剧本注入 + 魂穿记忆碎片 + 跨章连贯（前2后2）

日期：2026-10-23
范围：novel-studio 后端 `app/book/volume.py`、`app/api/simulate_routes.py`、`app/api/embody_routes.py`、`app/entities/store.py`、`app/entities/memory_gen.py`（新）、`app/context/ledger.py`、`app/wizard/desk.py`、`app/api/main.py`，前端 `src/views/DeskView.vue`、`src/api.ts`、`src/components/VolumeStagePanel.vue`

---

## 1. 问题诊断（实测数据）

### 1.1 「选中的那个跑团实况故事」在哪、有没有被注入

用户要的是「主角魂穿过去之前的原剧情」。项目 `20261014-c6c432` 第 2 卷实测：

```
volumes/2.json → simulated_run
  mode = "pick_one"，label = "钟声里的第七个守巷人"
  full_log            18321 字   ← 三段拼接，用 ===== 分隔
  selected_variant    38 个字段
    full_narrative    13532 字   ← 通稿，原剧本完整故事
    final_summary      1534 字   ← 小说化梗概，含「第1幕」～「第7幕」标记
    transcript         3171 字   ← 跑团实况对话日志
    day_breakdown      7 条      ← day 1..7，每条 {day, summary}
    storyboard        27 拍      ← 每拍有 scene/pl_action/pl_hypothesis/pl_cost
```

所以「原文」有三份可用形态，最完整的是 `full_narrative`。`full_log` 是三段的拼接：

| 段 | 字数 | 性质 |
|---|---|---|
| 跑团完整日志（守密人/调查员对话） | 3205 | 机制细节，机械 |
| 小说化梗概（原剧本经过） | 1561 | 结构化 7 幕 |
| 通稿（本场完整故事） | 13532 | 文学化，原文 |

### 1.2 五个断点

**断点一：注入代码读的键名永远不匹配。**
`desk.py:877-882`：

```python
sr = vol.simulated_run or {}
if isinstance(sr, dict):
    runs = sr.get("runs") or sr.get("simulations") or []   # ← 实际数据里没有这两个键
    if runs:
        picked = runs[0] ...
        arena_lines.append(f"【跑团模拟（参考）】\n{picked.get('summary') or picked.get('outcome')}")
```

`simulated_run` 的实际键是 `mode / picked_index / label / summary / full_log / selected_variant / collage`。`runs` 和 `simulations` 从来不存在，所以这段是**死代码，永不执行**。副本章的竞技场材料里从来没有任何原剧本内容。

**断点二：「小说化梗概」分裂三处，没有单一权威字段。**
`adapted_module.adapted_premise`（1033 字，副本核文本）、`full_log` 第 2 段（1561 字，小说化梗概）、`selected_variant.final_summary`（1534 字，小说化梗概）。三份内容高度重叠但字段名都不指向「梗概」，任何一处改动都不会同步另外两处。

**断点三：角色前史记忆无处存放。**
`Entity`（`store.py:322-345`）字段：`id / type / name / summary / details / status / source / appearances / created_at / updated_at / state_attrs / state_log / voiceprint / last_advanced_ch / advance_count`。**没有记忆字段**。

- `details` 是唯一能放长文本的地方（`_build_profile` 在 `vault_plan.py:254` 写入「身体状态/原剧本位置/第七日死状/隐藏死因/挣扎钩子/信息差」六行），但 `details` **从不注入 prompt**——`ledger.py:801-830` 的实体快照只取 `name + summary + appearances`。
- `state_attrs` 注入了，但 `ledger.py:244-266` 的 `_knowledge_seg` **只读 `knows` / `blinded_to` 两个键**，其余键静默丢弃。
- `host_character`（实测 13 个字段：`id/name/role/age/body_state/original_story_position/seventh_day_death/hidden_death_cause/struggle_hook/info_gap_hint/san_loss/death_countdown/tag`）也没有记忆字段。
- `desk.py:714` 读 `hc.get("known_info") or hc.get("memory") or hc.get("backstory")`——这三个键在 `host_character` 里**从不存在**，是防御性死代码，恒为空串。

结果：唐迟「附在这具身体上之前，这具身体经历过什么」，模型完全看不到。

**断点四：跨章连贯只有往后看 1 章的 500 字。**
- `desk.py:282-289` `previous_tail` → 上章结尾 500 字（`CONTINUATION_TAIL_CHARS`）
- `ledger.py:415-425` `_opening_commission` → 上章结尾 400 字（`OPENING_TAIL_CHARS`）
- `ledger.py:51-70` `_plot_digest` → 全部已写章的 plot，每章截 160 字

`_plot_digest` 虽然列了全部已写章，但每章 160 字对「接住上一章的状态变化」不够；且**完全没有后 chapters 的方向**——模型不知道下一章要往哪走，容易把这章写成孤立单元，或者提前把后面的爆点写掉。

**断点五：`storyboard` 的 27 拍没有 day 字段。**
实测 `storyboard[*].day` 全是 `None`，只有 `beat` 是连续序号。所以分拍无法直接按天筛给某一章用。

---

## 2. 设计

三个改动，彼此独立可验证。

### 2.1 `Volume.origin_novel`：原剧本归一化字段

在 `Volume` 上新增一个字段，把三份形态收成一份权威结构：

```python
origin_novel: dict | None = None
```

结构：

```python
{
  "full_text":   str,          # 通稿原文（= selected_variant.full_narrative）
  "summary":     str,          # 小说化梗概（= selected_variant.final_summary）
  "transcript":  str,          # 跑团实况对话（= selected_variant.transcript）
  "acts": [                    # 7 幕，按 day 归位
    {"day": 1, "act": 1, "text": "第1幕：黑红纸帖在雨夜抵达钟楼。…"},
    …
  ],
  "days": [                    # 原剧本逐日推进
    {"day": 1, "summary": "黑红纸帖在雨夜抵达钟楼。…"},
    …
  ],
  "one_liner":   str,          # selected_variant.one_liner
  "survivors":   list[str],
  "built_at":    str,
  "built_from":  str,          # "pick_one" | "custom"
}
```

**归一化时机**：`_pick_one`（`simulate_routes.py:582-597`）和 `_save_custom_variant`（同文件 607-620）写 `v.simulated_run` 的同一处，紧接着调 `build_origin_novel(picked, mode)` 落 `v.origin_novel`。这样选版那一刻数据就齐了，不依赖运行期解析。

**旧数据兜底**：`origin_novel_of(vol)` 读 `vol.origin_novel`；为空时从 `vol.simulated_run.selected_variant` **惰性重建**（同样的 `build_origin_novel`，`built_from="lazy"`）。所以 20261014-c6c432 第 2 卷不需要重跑跑团就能立刻用上。这是必须做的——已有项目不能因为升级就失效。

**`acts` 的来源**：`final_summary` 里的 `第N幕：` 标记（实测 7 处，正则 `第\s*(\d+)\s*幕`）。若某份稿没有幕标记（短稿、fallback 稿），`acts` 允许为空，此时注入只走 `days`，不报错。

**为什么不直接把 `full_narrative` 整段塞进 prompt**：
13532 字中文 ≈ 40 KB UTF-8。上下文总预算 200,000 字节（`assembler.py` / `engine.py:246`），整段占 20%。它每章都要注入，且会和「实体快照 + 任务书 + 写作模块 + 描写记忆」抢同一份预算。折中见 2.3。

### 2.2 `Entity.memories`：魂穿记忆碎片（按 Day 解锁）

**数据结构**（`store.py`，与 `Voiceprint` 同级的新 dataclass）：

```python
@dataclass
class MemoryFragment:
    id: str
    text: str            # 记忆内容，60-200 字，第一人称或第三人称均可
    recovers_at: int     # 唐迟入戏第几天想起（1..7）
    revealed_at: int = 0 # 实际在正文里被写出来的章号；0=还没写出来
    source: str = ""     # 溯源：来自 host_character 哪个字段 / 通稿哪一幕
    note: str = ""       # 作者批注
```

`Entity.memories: list[MemoryFragment] = field(default_factory=list)`，`__post_init__` 里做归一化（与 `voiceprint` 同样处理，防旧 JSON 塞裸 dict），`as_dict` / `as_summary` 带上，`_entity_from_legacy` 加 `d.setdefault("memories", [])`。

**为什么必须按 Day 解锁**：唐迟是**带着别人的记忆碎片 gradually 醒过来**的。如果第 1 章就把 7 条记忆全给模型，「原剧本第 7 幕零号归位」这种底牌会直接漏进第 1 章的正文。这是硬约束。

**双闸门筛选规则**：

```python
def _memory_seg(e, ch_day: int | None) -> str:
    gate = e.revealed_at or 0          # 已写出的按实际章号
    day  = ch_day or 99
    out = [m for m in e.memories
           if (m.revealed_at and m.revealed_at <= ch_no)   # 已在正文出现
             or (not m.revealed_at and m.recovers_at <= day)]  # 按计划解锁
```

即：**已落地的以实际章号为准，没落地的以计划解锁日为准。** 这样作者手动改过 `revealed_at` 就能精确控制某条记忆在哪一章真正被读者看到。

**注入位置**：`ledger.py` 的 `_knowledge_seg` 旁边新增 `_memory_seg`，接进两处实体清单——`ledger.py:801-826`（开章任务书 ④ 本章人物快照）和 `ledger.py:153` 的 `_voiceprint_material` 同级的位置。放在这里而不是新建 material，是因为它必须**逐角色**贴在角色名后面（像 `_knowledge_seg` 那样），而不是一段集中的列表——集中列表模型会当成背景资料，贴在角色行尾它才会当成「这个角色现在记得什么」。

**生成入口**：新文件 `app/entities/memory_gen.py`，与 `voiceprint_gen.py` 同一套设计（AI 出草稿 → 作者逐条勾选 → 走既有 PUT 落库，端点本身不写库）。

- 输入：该角色 `entity.as_dict()` 全量（身份/年龄/性格/动机/秘密/身体状态/第七日死状/隐藏死因/信息差…）+ 本卷 `origin_novel.summary` + `origin_novel.full_text[:6000]`
- 输出：5-8 条 `{text, recovers_at, source}`，`recovers_at` 必须在 1..7
- 铁律（同 voiceprint_gen）：**只从已有档案和原文推导，不臆造**。唐迟的程序员身份、宿主的姓名死状都不能被改写。`recovers_at` 大的碎片应该指向原文后半段（底牌类），小的指向开篇就成立的事实（身体状态类）。

**API**（`main.py`，紧邻现有 `/voiceprints/{eid}/draft`）：

```
POST /api/projects/{pid}/entities/{eid}/memories/draft   → 生成草稿（不写库）
PUT  /api/projects/{pid}/entities/{eid}/memories          → 落库（body.memories 全量覆盖）
```

`EntityUpdateIn` 加 `memories: list[dict] | None = None`，`store.update` 里走合并/覆盖语义与 `state_attrs` 一致。

**触发时机**：魂穿阶段选版落盘（`embody_routes._pick_outline:636` 写 `v.host_character`）之后，作者在卷面板点「生成记忆碎片」。不自动跑——一是 LLM 调用要花钱，二是宿主实体的 Entity 行可能还没建（`seed_from_volume_plan` 的时序），自动跑容易对不上实体。前端做成和「生成声音档案」同款按钮 + 同款预览弹窗（逐条勾选采用，可编辑解锁日）。

### 2.3 原剧本注入材料

在 `desk.py` 的 `ch_kind == "dungeon"` 分支里，紧接现有 `arena_lines` 之后，追加一段独立的 `origin_lines`，最终作为**独立 Material**（`source_id=f"volume:v{n}:origin"`，`family=REQUIRED`）而不是塞进 `arena_lines`——独立 material 才能在装配器里被单独计量和审计。

内容按四块组装（`build_origin_material(vol, ch_day)`，新函数放 `context/ledger.py` 便于复用和单测）：

```
【原剧本·唐迟魂穿之前的完整故事】
一句话：{one_liner}

■ 小说化梗概（7 幕，原剧本的骨架，全文）
{full final_summary，1534 字，不截断}

■ 通稿原文节选（前 {N} 字 + 后 {M} 字）
{head}
…
{（中间略）}
…
{tail}

■ 原剧本逐日推进（第 {d-1} ~ {d+2} 天窗口）
- 第1天：…
- 第2天：…
- 第3天：…            ← 本章对应日
- 第4天：…            ← 未来预告，只作方向，不要在本章写死

■ 这是唐迟「已经知道」的底牌
本章写作时：原剧本里【今天】正在发生 {today_hint}；
【后两天】会发生 {future_hints}——可以用作唐迟的先知感与行动动机，
但绝不能在本章把后两天的事件原样写出来（提前泄底），
只能写成他隐约预感、打算阻止、或者被人误导。
```

**预算控制**：`ORIGIN_NARRATIVE_HEAD = 1500`、`ORIGIN_NARRATIVE_TAIL = 1500`，合计 3000 字 ≈ 9 KB，占预算 4.5%。加上 `final_summary` 1534 字，整块约 4.5 KB… 实测约 13 KB / 6.5%。两个常量提到模块顶部，作者要全文可以调大；调 0 就退化成只给梗概 + 逐日推进（最省模式）。

**为什么 head + tail 而不是整段或只 head**：原剧本的开头（世界建立、第一个异常）和结尾（零号归位、守巷人接班）是唐迟最需要「预先知道」的两块；中段是机制细节，逐日推进那 7 条 150 字的摘要已经覆盖。

**修死代码**：`desk.py:877-882` 那段读 `runs`/`simulations` 的代码**删掉**，换成上面的 `build_origin_material`。留着只会误导下一个读代码的人以为还有别的跑团数据源。

### 2.4 跨章连贯：前 2 后 2

新函数 `_continuity_window_material(desk, volume, chapter, ch_day)`，产出**独立 REQUIRED material**（`source_id="state:continuity-window"`），在 `desk.py:963` 任务书注入之后紧跟着 append。

**前 2 章（已发生，必须接住）**：
取 `chapter-2 / chapter-1` 两章的 `plot` 全文（不截 160 字，各限 1500 字）。`_plot_digest` 保留原样（它承担全书脉络），这里补的是**细节层**。跳章时（中间章未写）自动往前顺延取两个**实际写过**的章，并在标题里说明跳过了几章。

**后 2 章（即将发生，只能当方向）**：
从 `volume.rhythm_blueprint.chapter_plan` 里筛 `chapter ∈ [chapter+1, chapter+2]`，每条渲染：

```
第 {n} 章《{title}》（Day {day}｜{beat_type}）
  方向：{focus}
  起：{plot_beats.qi}
  承：{plot_beats.cheng}
  转：{plot_beats.zhuan}
  合：{plot_beats.he}
  必埋伏笔：{foreshadow_seeds}
```

实测 `chapter_plan` 18 条覆盖第 3-20 章，`day` 字段齐全（`[1,1,2,3,3,4,4,5,5,5,6,6,7,7,7,7,7,7]`），起承转合四段俱全，直接可用。

数据缺失时（非副本章 / 无 blueprint / 超出计划范围）这段直接不出现，不编造占位。蓝星章（`mainline`）和间章（`interlude`）没有 `rhythm_blueprint`，此时「后 2 章」退化为从 `o_map`（大纲层）取后两章标题 + 首句——大纲层一定有，因为 `run_chapter_stage:905` 已经强制要求本章大纲存在。

**接缝规则显式写进材料头**（这是这一段真正的价值，光给数据模型不一定照做）：

```
【跨章接缝·前 2 后 2】
写这一章时必须守住三条：
1. 接住：前两章已经发生的事实在本章仍然成立——人还在哪、伤还在不在、
   谁已经知道了什么、谁还不知道。上一章结尾的钩子要在本章前段有回应。
2. 不越：后两章的方向只能作为唐迟的「预感 / 计划 / 被误导」的来源，
   不能把后面两章的事件写进本章（提前泄底 = 后面两章作废）。
3. 留口：本章结尾要能被下一章的开头接上——
   所以本章收尾应落在「变化」上，并给下一章留一个可执行的未决动作。
```

---

## 3. 改动清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `app/book/volume.py` | `Volume.origin_novel` 字段 + `build_origin_novel()` + `origin_novel_of()`（惰性兜底） |
| 2 | `app/api/simulate_routes.py` | `_pick_one` / `_save_custom_variant` 落 `origin_novel` |
| 3 | `app/entities/store.py` | `MemoryFragment` dataclass；`Entity.memories` + `__post_init__` 归一化 + `as_dict`/`as_summary`；`_entity_from_legacy` setdefault；`set_memories()` |
| 4 | `app/entities/memory_gen.py`（新） | `generate_draft()` / `FIELD_SHAPE` / `diff_against_current()`，照抄 `voiceprint_gen` 的三铁律结构 |
| 5 | `app/api/main.py` | `POST .../entities/{eid}/memories/draft`、`PUT .../entities/{eid}/memories`；`EntityUpdateIn.memories` |
| 6 | `app/context/ledger.py` | `build_origin_material()`；`_continuity_window_material()`；`_memory_seg()`；接进实体快照（801-826）与开章任务书新段 |
| 7 | `app/wizard/desk.py` | 删 877-882 死代码；`arena` 之后 append `origin` material；任务书之后 append `continuity-window` material |
| 8 | `frontend/src/api.ts` | `MemoryFragment` 类型；`DeskVolume.origin_novel`；draft/put memories API |
| 9 | `frontend/src/components/VolumeStagePanel.vue` | 魂穿阶段加「生成记忆碎片」按钮 + 预览弹窗（逐条勾选、可改解锁日） |

---

## 4. 预算校验

单章副本章新增注入量（实测第 2 卷数据）：

| 材料 | 字数 | 字节 | 占 200K |
|---|---|---|---|
| `origin`（梗概 1534 + 通稿 3000 + 逐日 ~1050 + 说明） | ~5800 | ~17 KB | 8.6% |
| `continuity-window`（前2章 plot 3000 + 后2章起承转合 ~1800 + 规则 ~400） | ~5200 | ~15 KB | 7.8% |
| `memories`（主角 + 在场角色，逐角色 ~200 字 × 5） | ~1000 | ~3 KB | 1.5% |
| **合计** | | **~35 KB** | **~18%** |

剩余 82% 给写作模块、任务书、描写记忆、已写剧情、文风卡、正文近况。这个比例可接受。若实测超预算，第一刀砍 `ORIGIN_NARRATIVE_HEAD/TAIL`（3000 → 0，退化成只给梗概 + 逐日推进，省 9 KB），第二刀砍前 2 章 plot 上限（1500 → 800）。两个旋钮都在 `ledger.py` 模块顶部。

---

## 5. 测试

| 测试 | 断言 |
|---|---|
| `test_origin_novel.py::test_lazy_rebuild_from_volume2` | 用真实 `volumes/2.json` 调 `origin_novel_of`，断言 `full_text` 13532 字、`acts` 7 条、`days` 7 条 |
| `test_origin_novel.py::test_acts_missing_markers` | `final_summary` 无幕标记时 `acts == []` 且不抛异常 |
| `test_origin_novel.py::test_summary_falls_back` | 无 `final_summary` 时 `summary` 退到 `full_log` 第 2 段；再退到 `""` |
| `test_memory_fragments.py::test_day_gate` | `recovers_at=5` 的碎片在 `ch_day=3` 不出现、`ch_day=5` 出现 |
| `test_memory_fragments.py::test_revealed_overrides_plan` | `revealed_at=2` 的碎片在 `ch_day=5` 仍出现（已落地不回收） |
| `test_memory_fragments.py::test_legacy_entity_loads` | 旧 JSON（无 `memories` 键）经 `EntityStore` 加载后 `memories == []` |
| `test_continuity_window.py::test_two_before_two_after` | 第 8 章 → 前两章是 6/7（取全文），后两章是 9/10（来自 blueprint，含 qi/cheng/zhuan/he） |
| `test_continuity_window.py::test_skip_chapter` | 第 8 章但第 7 章未写 → 前两章取 6/5，且标题说明跳章 |
| `test_continuity_window.py::test_no_blueprint_returns_none` | 无 `rhythm_blueprint` 的卷返回 `None`，不抛异常 |
| `test_origin_material_budget.py` | `build_origin_material` 输出 < 20000 字节 |

回归：`test_state_ledger.py`、`test_chapter_checklist.py`、`test_spec3_consistency.py`、`test_spec4_write_gate.py` 必须全绿（这几个直接断言任务书内容与实体注入形态）。

---

## 6. 明确不做

- **不做自动连贯性校验**（方案 C）。章节生成后跑一遍「主角位置 vs 记忆解锁日 / 伏笔是否已埋 / 时间线是否倒退」，不合格重生成。收益大但要新写校验器且 LLM 调用翻倍，单独排期。
- **不做 `storyboard` 分拍注入**。27 拍没有 `day` 字段，无法按章筛；且逐日推进（`day_breakdown`）已覆盖同样信息、字数少 10 倍。要用分拍得先给 `storyboard[*]` 补 day（改 `coc_simulator.py` 的产出 schema），不在本次范围。
- **不改 `adapted_module.adapted_premise`**。它是副本核文本（1033 字，源材料层），和 `origin_novel.summary`（原剧本梗概，产出层）语义不同，不合并。
- **不让记忆碎片自动生成**。同 2.2：LLM 成本 + 实体时序 + 作者要能改解锁日。