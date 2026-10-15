# Task 5 报告：CocSimView 工作台 simulate 阶段

- **任务编号**：Task 5
- **完成时间**：2026-10-15
- **Commit**：`36b0e4d`
- **Commit Message**：`feat(fe+be): CocSimView 3场并行COC跑团+单选/拼贴合成，适配工作台 simulate 阶段`

---

## 一、改动文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/book/volume.py` | 修改 | `PLANNING_FIELDS` 新增 `"simulated_run"`；`update_planning` 对该字段开放 `None` 透传以支持清空重跑 |
| `backend/app/api/main.py` | 修改 | `VolumeUpdateIn` 新增 `simulated_run: dict \| None = None`；`volumes_update` 检测 `model_fields_set` 保留显式 `None`；末尾 `register_simulate_routes(...)` 注入 8 个依赖 |
| `backend/app/api/simulate_routes.py` | **新建** | 5 个端点 + 后台任务 + mock 短路 + 机械拼贴，共 ~465 行 |
| `frontend/src/api.ts` | 修改 | 新增 `patchVolume` 到 `adaptApi`；新增 `simulateApi`（getVolume / resetRun / startSim / listJobs / subscribeJobs / pickOne / collage）和 `SimStartBody`/`SimVariant`/`SimCollageSelection` 类型 |
| `frontend/src/views/CocSimView.vue` | **新建** | simulate 阶段主视图，4 态 UI（loading/ready/running/result+拼贴/completed），约 440 行（script 220 + template 140 + style 80） |
| `frontend/src/views/HomeView.vue` | 修改 | `import CocSimView`；在 `ModuleAdaptView` 分支后加 `<CocSimView v-else-if="phase==='simulate'" :vol-no="currentVolumeNo" @done="loadPhase" />` |

---

## 二、后端端点一览

`register_simulate_routes(app, require_project=..., volumes_for=..., project_dir=..., book_for=..., fields_for=..., coc_run_for=..., field_run_for=..., chore_provider=...)` 注册以下路由：

| Method | URL | 功能 |
| --- | --- | --- |
| POST | `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/start` | 启动 3 场并行 COC 跑团；`max_turns_per_session<=1` 时 sleep(2)+0.3s*3 返回 3 个 mock variant，不调 LLM；否则走真实 `run_coc_parallel_batch` |
| GET | `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/jobs` | 返回该 session 的所有 job |
| GET | `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/stream` | SSE：`job_update` / `job_update_batch` / `job_idle`；连续 3 轮 idle 后断开 |
| POST | `/.../simulate/pick-one` | 校验 job 存在且 done，构造 `main_record{mode:'pick_one', selected_index, selected_variant, one_liner, full_log, summary}`，写 `v.simulated_run` 并置 `stage_completed.simulate=True` |
| POST | `/.../simulate/collage` | 机械字符串拼贴：基线 + 每个 section `----- {段名}（取自 {label}）-----` + 备注；不调 LLM；写 `main_record{mode:'collage', base_index, selections, user_note, summary, full_log, selected_variant, collage}` |

mock variant 结构严格按 brief：

```python
{"label": labels[i], "index": i,
 "one_liner": f"测试{i+1}线 one-liner",
 "flavor": "mock",
 "full_log": f"这是{i+1}线的完整跑团日志 mock...\nDay1 开场...（{labels[i]}）\n..."}
```

`labels = ["同组PL·好运线", "同组PL·厄运线", "异组PL·反转线"]`；`session_id = f"sim:{pid}:{vol_no}"`；`player_cards` 不足 2 张时自动补"林觉"和"赵魁"默认卡（照抄 jobs_routes 逻辑）。

---

## 三、前端 UI 说明

`CocSimView.vue`（`<script setup lang="ts">`，scoped CSS，ElementPlus 全量注册）：

- **准备态（ready）**：标题 + 文案说明 + `user_directive` 多行输入 + `el-collapse` 高级选项（`maxPairs` slider 4-10、`maxTurns` slider 1-40、`tangchiReplace` radio 不替换/PL1/PL2/PL3）+ 主按钮"🎲 开跑 3 场并行 COC"
- **跑团中（running）**：`el-progress` 百分比 + 进度消息 + 3 张并列 `el-card` 实时显示已完成 variant 的 `one_liner`
- **结果态（result）**：3 张大卡（label/`el-tag flavor`/one_liner/`JSON.stringify(...).slice(0,800)` 原始数据预览/"选这一场当主线"按钮）+ 下方拼贴卡（基线 `el-radio-group` × 3 + 6 个 section `el-select`：开场/第一个死亡/第二个死亡/高潮/结局/NPC 命运，三选项：好运线/厄运线/反转线 + 每段备注 textarea + 作者备注 textarea + "🧩 合成主跑团记录"按钮）
- **已完成（completed）**：`el-tag` 显示模式（单选/拼贴）+ summary + `el-scrollbar` 中 `full_log.slice(0,1500)` + "下一阶段：魂穿设计 →" 按钮 + "重跑"按钮
- 顶部若已有 `simulated_run` 则显示"上一次仿真结果"横幅并提供"重新跑一次"按钮
- `onMounted` 加载卷；打开 SSE 订阅，`onUnmounted` 关闭 SSE；SSE onmessage 更新 job/progress/variants
- 中文字案自然，样式 scoped，主色 `#6e4a8e`，卡片间距/hover/阴影统一

---

## 四、验证结果

### 1. vue-tsc 类型检查

命令：`frontend\node_modules\.bin\vue-tsc.cmd --noEmit`

结果：**exit code 0，零错误**（无任何输出）。

### 2. Python import 测试

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -c "from app.api.simulate_routes import register_simulate_routes; print('ok')"
```

结果：输出 `ok`，零异常。

### 3. 后端端点联调（curl/Invoke-WebRequest）

- `POST .../simulate/start` (max_turns_per_session=1) → 返回 `{ok:true, job_id, job{status:'running', progress:5}}`
- 等待 4s 后 `GET .../simulate/jobs` → `job0 status=done progress=100 msg='3 场全部完成（mock）' sub_count=3 variants count:3`
- `GET .../simulate/stream` → `Content-Type: text/event-stream; charset=utf-8`，正确下发 `event: job_update\ndata:{...3个mock variant...}`
- `POST .../simulate/pick-one {job_id,pick_index:1}` → 200，`GET /phase` 返回 `{"phase":"embody",...}`，阶段正确推进
- `POST .../simulate/collage {job_id, collage:{base_index:0, selections:[{section:'opening',from_index:1,...},{section:'climax',from_index:2,...}], user_note:'...'}}` → 200，`simulated_run.mode=='collage'`，`full_log` 含基线 + 开场段（取自厄运线，含备注）+ 高潮段（取自反转线，含备注）+ 作者备注，机械拼接无误
- `PATCH .../volumes/1 {simulated_run:null}` → 200，`simulated_run` 字段被置 null（Pydantic `model_fields_set` 方案生效）

### 4. 浏览器端到端验证

- 访问 `http://localhost:5173/`，选择项目 `20261014-c6c432`（造测试数据后 phase=simulate）
- 首屏：`🎲 COC 并行跑团仿真 / 3 场并行（同组 PL 好运/厄运线 + 异组 PL 反转线），挑一场作为主线或拼贴多场精华 / 高级选项 / 🎲 开跑 3 场并行 COC`——准备态 UI 完整渲染，控制台无红错
- 通过浏览器 console 调 `startSim(..., max_turns_per_session=1)` 触发 mock，刷新页面（组件 `onMounted` 自动 `listJobs` 恢复结果）
- 结果页文本（摘录）：

```
同组PL·好运线  mock  测试1线 one-liner  原始数据预览  选这一场当主线
同组PL·厄运线  mock  测试2线 one-liner  原始数据预览  选这一场当主线
异组PL·反转线  mock  测试3线 one-liner  原始数据预览  选这一场当主线

🧩 或：拼贴合成主跑团记录
拼贴模式：从 3 场中各挑你喜欢的元素，机械合并为一份主跑团记录...
主线骨架：[好运线/厄运线/反转线 radio]
开场 / 第一个死亡 / 第二个死亡 / 高潮 / 结局 / NPC 命运 [每段下拉 好运/厄运/反转]
🧩 合成主跑团记录
```

- 点击第二张卡"选这一场当主线" → ElMessage 提示"已选定本场为主跑团记录，进入下一阶段"，页面切换到"当前阶段：魂穿设计"占位页，`phase` 推进到 embody，端到端闭环通过
- 截图保存在 workspace：`screenshot-1792061455909.png`（准备态）、`screenshot-1792061565597.png`（结果+拼贴态）

### 5. 测试数据清理

按 brief 要求清理：

- `book/cost_debts.json`、`book/finale_notes.json`、`book/remaining_pages.json` 写回 `{}`
- 删除 `volumes/1.json`
- `volumes/index.json` 恢复为 `{"volumes":[]}`

---

## 五、关键实现要点

1. **清空重跑支持**：Pydantic 默认 `exclude_none=True` 无法区分"字段缺省"与"显式传 None"。`VolumeUpdateIn` 不改校验逻辑，在 handler 里检测 `if body.simulated_run is None and "simulated_run" in body.model_fields_set: kwargs["simulated_run"] = None`，配合 `VolumeStore.update_planning` 对 `simulated_run` 字段开 `val is not None or k=="simulated_run"` 的特例放行 None，实现 PATCH 清空。
2. **mock 短路**：`_work()` 函数开头判断 `max_turns<=1`，直接 `time.sleep(2)` + 3 次 `append_sub_result + sleep(0.3)`，返回 3 个 mock variant；`max_turns>=2` 走真实 `build_2same_1different_configs` + `run_coc_parallel_batch`，progress 算法与 `jobs_routes` 一致（`40 + int(...60 * (pair_idx/maxPairs + 0.5*ev/maxPairs))`）。
3. **SSE**：复用 `adapt_routes` 的 idle 3 轮断开模式（`_STREAM_IDLE_ROUNDS=3`），事件名 `job_update` / `job_update_batch` / `job_idle`，前端 EventSource 分别 addEventListener。
4. **拼贴机械拼接**：`_variant_snippet` 取 `full_log[:800]` 或 `log_tail` 兜底，每段以 `----- {中文名}（取自 {label}）-----` 包裹，备注和作者备注单独成段，纯字符串 concat，不调用 LLM。
5. **拼贴结构**：后端要求 body 形如 `{job_id, collage:{base_index, selections:[{section,from_index,note}], user_note}}`，前端 api.ts `collage()` 类型和 CocSimView 的 `onCollage()` 严格对齐，`selections` 按 6 个 SECTIONS 顺序批量生成（未选段默认 from=base_index）。

---

## 六、遗留点 / 后续迭代建议

1. 拼贴目前"机械合并"只做 800 字 snippet 拼接，brief 说明"智能 AI 合成后续迭代"。后续可增加"AI 智能缝合"按钮，把 merged 结构喂给 LLM 生成连贯主线。
2. `player_cards` 仍沿用 `jobs_routes` 里的"不足 2 张补林觉/赵魁"逻辑，simulate 阶段后续可考虑支持从 BookSetup 里读主角卡做动态 PL 替换。
3. CocSimView 的"重跑"按钮目前调用 `resetRun()` 清空 simulated_run 后 reload 页面；后续可优化为原地回到 ready 态而不刷新。
4. 结果态的"原始数据预览"使用 `JSON.stringify(v).slice(0,800)`，后续可结构化展示关键字段（investigators/deaths/sanity 等）。
