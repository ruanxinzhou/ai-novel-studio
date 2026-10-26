# 设计：实体档案结构化填充 + 方向卡起承转合

日期：2026-10-23
范围：novel-studio 后端 `app/entities/vault_plan.py`、`app/api/brief_routes.py`，前端 `SettingsDrawer.vue`、`DeskView.vue`

---

## 1. 问题诊断

### 1.1 实体库只有名字（数据在，但没落进去）

用户现象：章节骨架锁定后去实体库看，`周朔` 只有名字，简介是「电话」，细节档案空。

实测根因——后端数据齐全，是**落库时丢字段**：

`vol.adapted_module` 里 `周朔` 的完整档案：

```json
// renamed_cast —— 有身份定位
{"original": "陈建国", "new_name": "周朔", "role": "县局刑侦中队长"}

// cast —— 有全部人物卡字段
{"name": "周朔", "role": "本地刑侦负责人",
 "occupation": "雾汀县公安局刑侦中队长", "age": "46",
 "appearance": "...", "personality": "...", "motivation": "...",
 "secret": "...", "key_skills": {...}}
```

但 `seed_from_volume_plan` 的三条落库路径各自丢了不同的信息：

| 落库路径 | 代码位置 | 传入的 summary | 结果 |
|---|---|---|---|
| `chapter_plan.characters_on_stage` | `vault_plan.py:375` | 括注内容 | `周朔（电话）` → summary = 「电话」 |
| `adapted_module.renamed_cast` | `vault_plan.py:437` | `original`（旧名） | summary = 「陈建国」 |
| `adapted_module.cast` | — | **从未调用** | 职业/年龄/外貌/性格/动机/秘密全丢 |

实测数据佐证（144 个实体中 107 个来自 `volume_plan`）：

```
character | 陆骁      | summary='' | details=''
character | 邵广勤    | summary='电话回声' | details=''
character | 周朔      | summary='电话'     | details=''
location  | 渡口旅社客房 | summary='' | details=''
```

107 个 volume_plan 实体里，summary 要么为空，要么是括注碎片，没有一个有 `details`。

### 1.2 起承转合：模型完全能理解，缺口在方向卡层

**已有的（能用）**：`rhythm_blueprint.chapter_plan[*].plot_beats = {qi, cheng, zhuan, he}`，
后端 `rhythm_routes._sel_from_item` 已经把它写进 `brief.selected`，
前端 `DeskView.vue:1064-1085` 已经渲染成四个色块。

**缺口**：`brief_routes._BRIEF_SYSTEM` 的输出 schema 里**没有 `plot_beats` 字段**，
方向卡只输出 `summary`（80-120 字一整段）+ `hooks`。
`rhythm` 层塞进去的 `plot_beats` 在作者重新选方向卡时就被冲掉了。

所以「起承转合」这个结构本身没问题——模型理解得很好，
问题是**方向卡层没有这个字段，导致它无法被作者编辑和分叉**。

### 1.3 附带发现：方向卡 summary 与 plot_beats 语义重复

`rhythm` 层的 `summary` 就是 `focus`（一句话 10-30 字），
和 `plot_beats` 是同一层信息的两种粒度。方向卡层的 `summary` 却是 80-120 字的整段。
三版方向卡的差异目前只能体现在这一整段里，作者没法逐段比较。

---

## 2. 设计

### 2.1 实体档案结构化填充

**目标**：`volume_plan` 来源的实体，summary 是一句身份定位，details 是结构化人物档案。

**数据源优先级**（先到先得，同一实体取信息最全的来源）：

| 优先级 | 来源 | 提供字段 |
|---|---|---|
| 1 | `adapted_module.cast[]` | role, occupation, age, gender, appearance, personality, motivation, secret, key_skills |
| 2 | `adapted_module.renamed_cast[]` | role（旧名 → 新名映射） |
| 3 | `adapted_module.player_cards[]` | desc, summary, role |
| 4 | `vol.host_character` | role, body_state, original_story_position, seventh_day_death, hidden_death_cause, struggle_hook, info_gap_hint |
| 5 | `chapter_plan.characters_on_stage` 括注 | 仅括注文本（如「电话」），作最后兜底 |

**产出格式**：

```
summary（简介，一句话 ≤40 字）
  = 「{name}，{occupation or role}。」若两者都无则用括注或空。

details（细节档案，多行）
  身份：雾汀县公安局刑侦中队长
  年龄：46
  外貌：{appearance}
  性格：{personality}
  动机：{motivation}
  秘密：{secret}
  技能：{key_skills 压缩成「侦查74/意志57/…」}
```

宿主角色额外追加：
```
  身体状态：{body_state}
  原剧本位置：{original_story_position}
  第七日死状：{seventh_day_death}
  隐藏死因：{hidden_death_cause}
  挣扎钩子：{struggle_hook}
```

**关键约束：不臆造**。字段在源数据里没有就不写这一行，绝不用占位文案填充。

**幂等与不覆盖**：
- 已存在的实体 → **只补 `details` 里缺失的行**，不覆盖作者改过的内容
- `summary` 为空或仍是碎片（长度 < 4 或等于括注原文）时才覆盖
- 作者手改过的实体（`source == "manual"`）完全不动

**新增函数**（`vault_plan.py`）：

```python
def _build_profile(name, *, cast_card, renamed, player_card, host, note) -> tuple[str, str]:
    """返回 (summary, details)。所有字段只从传入的结构化数据取，没有就不写。"""
```

### 2.2 落库顺序调整

当前 `seed_from_volume_plan` 的执行顺序是：chapter_plan → 详设节点 → 宿主 → player_cards/renamed_cast。
问题：**chapter_plan 先跑，把「周朔（电话）」这个碎片 summary 写死了**，
后面即使拿到完整 cast 档案，因为「已存在」也不会再更新。

改成**先建完整档案，再补出场章**：

```
1. 先扫 cast / renamed_cast / player_cards / host_character  → 建档（带完整档案）
2. 再扫 chapter_plan / 详设节点 → 补 appearances（不碰档案）
```

这样 `周朔` 第一次建档就带完整信息，chapter_plan 只贡献出场章号。

### 2.3 方向卡起承转合

**后端 `brief_routes.py`**：

1. `_BRIEF_SYSTEM` 的 schema 加 `plot_beats` 字段：

```json
"plot_beats": {
  "qi": "起：开场状态/触发事件（30-60字）",
  "cheng": "承：信息展开/冲突升级（40-80字）",
  "zhuan": "转：异常升级/认知反转（40-80字）",
  "he": "合：收尾状态/悬念落点（30-60字）"
}
```

2. 上下文里注入 rhythm 层的 `plot_beats` 作为**参考骨架**，并说明「可以调整，但要保持四段各自的功能」：

```
===== 本章节奏骨架参考（可调整，但要保持四段功能不变）=====
起：...
承：...
转：...
合：...
```

3. `_brief_select` 保存时确保 `plot_beats` 透传（`selected` 是整个 dict 拷贝，天然透传，
但需要兜底：从 `_chapter_plan_for` 取 rhythm 的 plot_beats 补上，防止模型漏字段）。

4. `summary` 语义调整：从「80-120 字整段」降为「一句话定位（20-40 字）」，
   让 summary 和 plot_beats 不再重复。

**前端 `DeskView.vue`**：

- 方向卡展示区已有 plot_beats 四色块渲染（rhythm 来源）
- 需确认：方向卡来源的 selected 也走同一段渲染（`currentBriefSelected.plot_beats`），
  实际代码已经兼容，无需改动
- **新增**：方向卡选择弹窗里也要显示四段，让作者在选之前就能比较

### 2.4 章节细纲消费 plot_beats

章细纲层（`plot`）生成时，需要把方向卡的四段作为约束注入。
当前 `plot` 层拿的是 `work.brief.selected`（含 `summary`），
改成同时注入 `plot_beats`，让章细纲按四段展开场景。

这一项依赖 plot 层 prompt 的现有结构，需要在实现时确认注入点，
本次先保证数据通路打通（brief.selected.plot_beats 有值）。

---

## 3. 影响面

| 文件 | 改动 |
|---|---|
| `backend/app/entities/vault_plan.py` | 新增 `_build_profile`；调整落库顺序；改写 `_seed_character` 签名 |
| `backend/app/api/brief_routes.py` | `_BRIEF_SYSTEM` schema 加字段；上下文注入 rhythm plot_beats；`_brief_select` 兜底 |
| `backend/app/api/rhythm_routes.py` | 无需改（调用点不变） |
| `frontend/src/views/DeskView.vue` | 方向卡选择弹窗展示四段 |
| `frontend/src/components/SettingsDrawer.vue` | 实体详情档案排版（结构化多行更好读） |
| `backend/tests/test_vault_profile.py` | 新增：档案提取、幂等、不覆盖、顺序 |

---

## 4. 验证标准

1. 对卷 `20261014-c6c432` 重新执行卷级落库后，实体 `周朔` 的
   summary = 「周朔，雾汀县公安局刑侦中队长。」，
   details 包含身份/年龄/性格/动机等条目，且 details 非空
2. 实体 `陆骁`（宿主）的 details 包含「身体状态」「第七日死状」
3. 已存在且作者改过的实体，手动调用落库后档案不变
4. 方向卡 `/options` 返回的每一条 option 含 4 个非空 plot_beats 字段
5. 方向卡选版后 `brief.selected.plot_beats` 四段非空
6. 前端方向卡弹窗显示四段；章台右侧方向卡摘要显示四段
7. 后端全量测试基线不恶化（当前 39 failed / 577 passed）

---

## 5. 不做的事

- 不调 LLM 补全档案：全部字段从已有结构化数据取，零 token 成本
- 不自动写声音档案（习惯动作/口癖）：那是作者的决定权，保持现状
- 不改 `beat_type`（hook/sell/engine/climax/cooldown）体系：它管章节功能，
  plot_beats 管章内节奏，两者正交
- 不给 foreshadow 加 details：伏笔没有「人物档案」这种结构，
  保持 summary 描述 + plant/payback 章号追踪
