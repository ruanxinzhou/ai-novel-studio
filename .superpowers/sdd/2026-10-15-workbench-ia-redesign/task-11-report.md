# Task 11 报告：SettleView 杀青 6 步面板

## 概览

| 项 | 值 |
|---|---|
| 任务 | T11 · SettleView 杀青 6 步面板 |
| 提交信息 | `feat(fe+be): SettleView 杀青6步（伏笔清点/实体污染/代价/蓝星钩子/下卷预告/落锤）` |
| 后端验证 | `from app.api.main import app` → OK，146 routes；url_path_for 全部 5 条 settle 路由可解析；TestClient 返回 422/404 符合路由存在预期（demo 项目不存在→404，ai-suggest POST→422） |
| 前端验证 | `npx vue-tsc --noEmit` exit 0，零类型错误 |

## 修改文件

### 后端

1. **`backend/app/api/settle_routes.py`**（完全重写，约 600 行）
   - `register_settle_routes` 签名新增 `chore_provider: Callable[[str], Any]`，注入 `_Deps`。
   - 保留原有 `detect/apply/finalize` 路径不变（`/api/projects/{pid}/volumes/{vol_no}/settle/...`）。
   - 新增两个端点：
     - `GET /settle/overview` → `_overview`：聚合 `volume.as_dict()` + `detect_foreshadows()` 伏笔列表 + 本卷章数/字数/死亡数 + 当前污染 + is_mock 标志。字数用 `work_text(ch, 'polish') or work_text(ch, 'draft')`，按 field_no 过滤本卷章节。
     - `POST /settle/ai-suggest` → `_ai_suggest`：统一入口，`step ∈ {entity_check, cost, blue_star_hook, next_volume_hint}`。先判断 `_is_mock(v)`（`adapted_module.startswith("测试副本核文本")` 或 `host_character.name 含 "林薇"`）短路返回硬编码克苏鲁 mock；否则从 scaffold_cards（core_twist/cost_cap）、death_nodes、climax_nodes、tangchi_story_outline、主角状态、最后一章尾部（1200 字）组装上下文，构造按 step 不同的 JSON schema 提示，调 `complete_structured(generate)`（`generate = chore_provider("settle")`）。任何异常 fallback 到 mock 并附带 `llm_error`。
   - Mock 数据：
     - entity_checks：3 条（林薇 dead/辅导员 corrupted/唐迟的影子 awakened），全部带 san_delta/note 字段对齐前端 `SettleEntityCheck`。
     - cost：cost_summary + suggested_contamination_delta=3 + san_loss_note + new_echo + kill_add。
     - blue_star_hook：3 条（通讯录号码/梦里海声/镜子眨眼），id/title/detail/summary 对齐 `SettleBlueStarHook`。
     - next_volume_hint：3 条（末班地铁青水湾/外婆筒子楼/医院 3 号病房），id/title/entry_point/info_gap/summary 对齐 `SettleNextVolumeHint`。
   - 扩 `_apply`：请求体新增 `entity_updates: list[_EntityUpdate]`、`cost_summary: str`、`blue_star_hook: dict|None`、`next_volume_hint: dict|None`。处理时：
     - 遍历 entity_updates 调 `es.update(id, status=...)`（按状态）；累加 `extra_contam_from_san += max(0, -san_delta)`。
     - 最终污染 = `body.contamination_delta + extra_contam_from_san`，合并进 diff 后 `es.apply_settle_diff`。
     - 写 `v.settle = { applied_at, foreshadow_decisions, entity_updates, cost_summary, blue_star_hook, next_volume_hint, diff_summary, final_chapter_no, contamination_delta }` 并 `vs.save(v)`。
   - 扩 `_finalize`：在现有基础上返回值追加 `next_action_options: ["interlude", "next_dungeon"]`，供前端落锤后展示按钮。

2. **`backend/app/api/main.py`**
   - 在 `register_settle_routes(...)` 调用处补 `chore_provider=_chore_provider`（一行 diff）。

### 前端

3. **`frontend/src/api.ts`**（末尾追加 ~110 行）
   - 新增 TypeScript 接口：`SettleForeshadow`、`SettleEntityCheck`、`SettleCostSummary`、`SettleBlueStarHook`、`SettleNextVolumeHint`、`SettleOverview`、`SettleEntityUpdate`、`SettleApplyBody`。
   - 新增 `settleApi` 对象，7 个方法：`overview`、`detect`、`aiSuggest(step, context)`、`apply`、`finalize`、`createInterlude`（POST `/workbench/volumes/interlude`）、`createNextDungeon(title)`（POST `/workbench/volumes`）。

4. **`frontend/src/views/SettleView.vue`**（新建 ~750 行）
   - `<script setup lang="ts">` 6 步向导状态机 `step: 0..5`，对应 stepLabels `['伏笔清点','实体污染检查','代价核算','蓝星钩子','下卷预告','落锤']`。
   - **Step 0 伏笔清点**：`el-table` 列伏笔，每行 3 个 `el-button`（兑现/丢弃/顺延），"兑现"时展开 `el-input textarea` 写 resolution。
   - **Step 1 实体污染检查**：进页自动调 `aiSuggest('entity_check')`，`el-table` 每行一个采纳开关 + SAN 数值输入 + 备注输入；采纳的条目汇总为 `adoptedEntityUpdates`。
   - **Step 2 代价核算**：进页自动调 `aiSuggest('cost')`，展示 cost_summary 卡片（代价总览/SAN/新回响/KILL 加成），`el-input-number` 可手动覆盖 contamination_delta，实时显示当前污染→新污染。
   - **Step 3 蓝星钩子**：`SushiCards mode="ensemble" :plate-count="3" :allow-custom="true" :allow-regenerate="true"`，三版候选；`@pick` 选中；`@rerun` 带 directive 重抽；`@custom-write` 通过 `ElMessageBox.prompt` 两步采集 title+detail 自编并插入列表头；`@edit-card` 通过 prompt 就地微调 detail。
   - **Step 4 下卷预告**：同 SushiCards ensemble 三盘，字段映射 title/entry_point/info_gap/summary；自拟走三步 prompt（title/entry/info_gap）；选中时自动回填 nextDungeonTitle。
   - **Step 5 落锤**：汇总卡展示伏笔处置统计、实体更新条数、污染变化、钩子/下卷名；"确认落锤"按钮先调 apply 再调 finalize；finalize 后通过 `el-result` 显示 success，并提供两个后续动作按钮："🌙 先写蓝星间章"（`settleApi.createInterlude`）、"⚔ 直接开下一副本"（`settleApi.createNextDungeon(title)` 带 el-input 标题）。两个动作完成后 `emit('done')` → HomeView 调 `loadPhase` 刷新阶段。
   - 暗色克苏鲁主题色板（参考 EmbodyView/DungeonScaffoldView）：背景 radial-gradient `#2a241d → #1e1a16 → #0f0c0a`，主文字 `#e6e0d4`，副文字 `#a89a84`，点缀色 `#c4894e`，警示橙 `#d97a56`。el-table 主题色穿透覆盖为暗底橙头。
   - 所有 AI 建议均为短任务，无 SSE；按钮 loading 态绑定。

5. **`frontend/src/views/HomeView.vue`**
   - `import SettleView from './SettleView.vue'`。
   - 在 DeskView 容器之后、占位提示之前插入：
     ```html
     <SettleView
       v-else-if="phase === 'settle'"
       :vol-no="currentVolumeNo"
       @done="loadPhase"
     />
     ```
   - PHASE_LABELS 已有 `settle: '杀青'`，无需改动。

## 关键设计决策

1. **不破坏已有 API**：detect/apply/finalize 三条路径和路径参数保持不变，仅扩请求体字段和 finalize 返回字段。
2. **Mock 短路位置**：`_is_mock` 仅在 `_ai_suggest` 里短路，其他端点（overview/detect/apply/finalize）照常走真实数据链路，避免 overview 返回空数据让 UI 进不去。
3. **SushiCards ensemble 模式**：蓝星钩子/下卷预告两页全部开启 `allowCustom=true` 与 `allowRegenerate=true`，贯彻 T10 确立的"重抽/自编/微调"三原则；自编和编辑都通过 `ElMessageBox.prompt` 采集（而非页面内联输入），保持 6 步向导的节奏紧凑。
4. **污染折算**：实体检查里 san_delta 负值（SAN 损失）按绝对值累加到 contamination_delta，最终污染 = 作者手动值 + Σ|SAN 损失|，符合 brief 中"SAN 损失应等价折算污染"的语义。
5. **apply 与 finalize 分离**：点"确认落锤"会先 apply（写所有决议）再 finalize（卷状态翻页）；apply 失败时 finalize 不会执行，保证状态一致。
6. **后续动作**：finalize 后根据 `v.volume_kind` 给出 next_action_options，前端直接提供两个按钮分别调 phase_routes.py 中已有的 `/workbench/volumes/interlude` 和 `/workbench/volumes` 端点，不新建路由。

## 验证命令 & 结果

```powershell
# 后端
> .\.venv\Scripts\python.exe -c "from app.api.main import app; print('OK', len(app.routes))"
OK 146

# 前端
> npx vue-tsc --noEmit
(exit code 0, 无输出)
```

另外用 FastAPI TestClient 手动验证：
- `GET /api/projects/demo/volumes/1/settle/overview` → 404 `项目不存在：demo`（路由存在，require_project 正常拦截）
- `POST /api/projects/demo/volumes/1/settle/ai-suggest` → 422（路由存在，空 body 触发 pydantic 校验）
- `app.url_path_for('_overview', pid='demo', vol_no=1)` 等 5 个名字全部成功反解路径

## 未做（留给后续任务）

- HomeView 第 45 行 `// TODO: Task 11 接 protagonist API 读取真实污染值`——StatusBar 污染读数仍走本地 ref，本任务未改动 StatusBar 数据流（overview 里已经返回 current_contamination，但污染值实时同步给 StatusBar 属于跨组件状态接入，超出单视图范围，留给后续 polish）。
- interlude 阶段视图（PHASE_LABELS 已有 interlue 标签，但模板里仍走 placeholder-view）——本任务"落锤"→createInterlude 后 loadPhase 会切到 interlude 阶段，对应视图由后续间章任务负责。
