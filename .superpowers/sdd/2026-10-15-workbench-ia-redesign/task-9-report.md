# Task 9: RhythmView 节奏骨架 — 交付报告

## 状态
**DONE**

## Commit
- Hash: `005690c`
- Message: `feat(fe+be): RhythmView 节奏骨架（7天强度曲线+密度分析+章节分配建议+告警）`
- 分支: `master`
- 未 push（按要求）

## 文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/api/rhythm_routes.py` | 新增 | 节奏骨架后端路由（POST/analyze、POST/finalize、GET/），mock 短路、density_map 本地聚合、complete_structured LLM 调用、缺字段回退 mock |
| `backend/app/book/volume.py` | 修改 | `VolumeStore.update_planning` 的 `planning_fields` 与 `_allow_falsey` 集合中新增 `"rhythm_blueprint"`，确保空 dict / dict 值能正常落盘 |
| `backend/app/api/main.py` | 修改 | 在 `register_scaffold_routes(...)` 之后新增 rhythm 模块的 import + register 调用，注入相同的 3 个 deps（require_project / volumes_for / chore_provider） |
| `frontend/src/api.ts` | 修改 | 追加 `RhythmBeat / RhythmWarning / RhythmChapterPlan / RhythmBlueprint / DensityMap` 五个 TS interface；追加 `rhythmApi`（analyze / get / finalize，无 SSE、无 Job） |
| `frontend/src/views/RhythmView.vue` | 新增 | 单页视图：props=volNo，emits=done；暗色克苏鲁基调 |
| `frontend/src/views/HomeView.vue` | 修改 | 新增 `import RhythmView`；在 `<DungeonScaffoldView v-else-if="phase==='scaffold'">` 之后插入 `<RhythmView v-else-if="phase==='rhythm'" :vol-no="currentVolumeNo" @done="loadPhase" />` |

## 后端端点
- `POST /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm/analyze`
  - 前置校验：scaffold 6 卡必须全部 locked（stage_completed.scaffold=True 或每张卡都有 locked_at），否则 422
  - density_map 纯本地按 death_nodes / climax_nodes / clue_nodes 的 `day` 字段聚合（7 天 → {death, climax, clue, total}）
  - 单次 complete_structured 调用生成 blueprint；LLM 异常或缺字段回退 mock
  - Mock 短路：`adapted_module` 以 "测试副本核文本" 开头 或 `host_character.name` 含 "林薇"
  - 写入 `vol.rhythm_blueprint`；返回 `{ok, blueprint, density_map, is_mock}`
- `POST /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm/finalize`
  - 校验 blueprint 非空 → `stage_completed.rhythm = True`，阶段流转到 briefing
  - 返回 `{ok, stage_completed, volume}`
- `GET /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm`
  - 返回 `{volume, blueprint, density_map}`（每次重算 density_map 供前端渲染）

## 前端视图要点
- **顶部**：标题"节奏骨架 · 第七日"+ 副标题 + 右侧"AI 推演节奏"主按钮（loading 文案切换）。
- **7 天强度曲线**：纯 CSS flex 布局，`.rhythm-bar-wrap` 固定高 180px，`.rhythm-bar` 自底向上线性渐变 `linear-gradient(to top, #409EFF, #F56C6C)`（蓝→红），柱侧用 emoji/字符标注节点（💀 死亡 / ★ 高潮 / ● 线索）。
- **密度条**：7 列堆叠条，death 红 / climax 橙 / clue 蓝，max 按当日 total 归一，顶部显示总数。
- **节奏四段占比**：4 个小卡片，setup/build/climax/cooldown 各自着色（蓝/金/红/灰），显示百分比 + 进度条。
- **告警区**：`warnings` 空时用 `el-result type="success"` 展示"节奏分布均衡"；非空时逐条 `el-alert`，按 dearth(cluster/front_load/back_load) 给 tag 颜色与"真空/过载/前重/后重"标签。
- **章节分配表**：`el-table` 展示 chapter / day / focus / words / beat_type；beat_type 用 el-tag 着色（hook=蓝、sell=绿、engine=青、climax=红、cooldown=灰）；表格下方显示 `notes`（节奏注记），左侧金色边条。
- **底部**："采纳此节奏方案，进入章前规划"主按钮，finalize 成功后 `emit('done')`；未 analyze 时 disabled。
- **生命周期**：`onMounted` + `watch(volNo)` 调 `rhythmApi.get()`，已有 blueprint 直接展示；错误用 `ElMessage.error`。
- **样式**：scoped CSS，复用 EmbodyView 的克苏鲁暗色基调（#1f1d2b 深背景系、#d4b48a 金色、#c44848 血色主按钮、#e6e0d4 正文）；响应式（小屏 flex-wrap）。

## 验证结果
1. **后端 import 验证**：
   ```
   cd backend
   .\.venv\Scripts\python.exe -c "from app.api.main import app; print('OK', len(app.routes))"
   → OK 146
   ```
   进程 exit code 0，无 import 错误。
2. **路由注册验证**：通过 `app.openapi()['paths']` 过滤 rhythm，三条路径均已注册：
   ```
   /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm/analyze  POST
   /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm/finalize POST
   /api/projects/{pid}/workbench/volumes/{vol_no}/rhythm         GET
   ```
3. **未启动 dev server**（按要求）。前端代码遵循现有 Vue 3 + Element Plus + TS 模式，未引入任何新依赖。

## 已知遗留问题
- 无阻塞问题。
- 小提示：density_map 在 JSON 中键为字符串 "1"~"7"（对象键必然是字符串），前端 TS 中 `DensityMap` 声明为 `[day: number]` 索引，运行时访问 `densityMap[d]`（d 为 number）会被 JS 自动转字符串，行为正确；若追求严格类型可后续改成 `Record<string, ...>`，但与 brief 给出的 interface 一致，本次不改。
- 前端可视化采用纯 CSS（未引入 echarts），完全符合 brief；在极窄屏下节奏柱会出现横向滚动（已加 `overflow-x: auto`）。
- 告警文案全部由 LLM 生成，mock 数据包含 3 条示例告警（dearth day4 / cluster day5 / front_load）用于 UI 演示。

## 后续
- 用户可启动前后端 dev server 实际点击"AI 推演节奏"→"采纳"验证端到端流程。
- 进入章前规划（briefing）阶段后，HomeView 需要 briefing 视图（不在本任务范围）。
