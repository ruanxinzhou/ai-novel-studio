# Task 5 Brief：CocSimView（多场并行 COC 跑团 + 结果对比 + 拼贴篮 + 合成主记录）

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

Vite 已在 5173、uvicorn 已在 8321。**不要重启 dev server**。

## 目标
adapt 完成后进入 simulate 阶段。用户需要启动 3 场并行 COC 跑团（2 同 1 异：同组 PL 好运/厄运线 + 异组 PL 反转线），实时看到每场进度；完成后对比 3 场走向，**可以单选一场做为主记录，也可以进入"拼贴"模式从 3 场中各挑选喜欢的片段/设定/死法，合成一份主跑团记录**。合成结果写入 `volume.simulated_run`（dict），`stage_completed.simulate = True`。

---

## 后端改动

### 1. 新建 `backend/app/api/simulate_routes.py`
用与其他 *_routes.py 一致的 register 函数模式。

**需要从 main.py 注入的依赖**（看 register_job_routes / register_settle_routes 等调用处的参数名，用同名变量传）：
- `require_project`, `volumes_for`, `project_dir`, `fields_for`, `book_for`, `coc_run_for`, `field_run_for`（这些都在 main.py 里是现成的 _xxx helper）
- `chore_provider` → `_chore_provider`

导出：`register_simulate_routes(app, *, require_project, volumes_for, project_dir, book_for, fields_for, coc_run_for, field_run_for, chore_provider)`

**端点**（全部挂在 `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/...` 下，加上复用的 volume GET 已在 adapt_routes 里提供）：

#### 1a. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/start`
请求体：`{"user_directive": str = "", "max_pairs_per_session": int = 6, "max_turns_per_session": int = 20, "tangchi_replace_idx": int | null = null}`

逻辑（**改写自** `jobs_routes._start_coc`，但**不依赖 session**，直接操作 volume）：

1. `require_project(pid)`；取 `vs=volumes_for(pid); v=vs.get(vol_no)`；校验 v 存在且 v.adapted_module 非空（否则 422 "请先完成 PDF 改编"）。
2. 构造伪 source dict：`src = {"adapted_premise": v.adapted_module, "title": v.title, "player_cards": v.simulated_run.get("player_cards", []) if v.simulated_run else []}`
3. player_cards 不足 2 张时，用 coc_run.make_player_card 补默认（照抄 jobs_routes：林觉/赵魁两张）。
4. `from ..pipeline.coc_simulator import build_2same_1different_configs, run_coc_parallel_batch`
5. `player_sets, variant_configs = build_2same_1different_configs(player_cards, tangchi_replace_idx=body.tangchi_replace_idx)`
6. `book_store=book_for(pid); field_store=fields_for(pid); field_run_store=field_run_for(pid); coc_store=coc_run_for(pid); field_no = v.field_no`（v.field_no 在 create_new_volume 时设置为等于 volume_no）
7. adapt_parsed 字典（照抄 jobs_routes：vid/title/world_premise=adapted_module/hook/entry_mood）
8. 创建 job：`job = job_store.create(session_id=f"sim:{pid}:{vol_no}", kind="coc_batch", meta={"phase":"simulate","total_sessions":3,"flavors":[vc.get("flavor") for vc in variant_configs],"volume_no":vol_no})`
9. labels = ["同组PL·好运线", "同组PL·厄运线", "异组PL·反转线"]，session_state/progress_lock/_on_event 完全照抄 jobs_routes._start_coc 的实现（用于进度百分比更新）
10. _work 函数内部：用 `d2.chore_provider("coc")` 做 runner 工厂；调 `run_coc_parallel_batch(...)` 传所有参数（max_workers=3）；循环打 label/index；append_sub_result；job.update(progress=100, message="3场全部完成", result={"variants":variants})
11. `run_in_background(job_store, job.job_id, _work)`
12. 返回 `{"ok": True, "job": job.as_dict(), "job_id": job.job_id}`

**注意**：jobs_routes._start_coc 里的 _work 闭包用 d2._must_deps()，但我们是独立路由，不需要 d2 中间件对象——直接用闭包里已经捕获的 book_store/field_store/.../chore_provider 就行。runner 工厂要在线程内调用 chore_provider("coc")（不要在主线程里建 provider，每个线程独立）。

#### 1b. GET `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/jobs`
- 返回 `{"jobs": [j.as_dict() for j in job_store.list_session(f"sim:{pid}:{vol_no}")]}`

#### 1c. GET `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/stream`（SSE）
- 同 adapt_routes 的 stream 实现，session_id 换成 `f"sim:{pid}:{vol_no}"`，3 轮 idle 后断开。

#### 1d. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/pick-one`
请求体：`{"job_id": str, "pick_index": int}`
- 校验 job 存在且 kind=="coc_batch"，status=="done"。
- `variants = job.result["variants"]; picked = variants[pick_index]`
- 构造主跑团记录：
  ```python
  main_record = {
      "mode": "pick_one",
      "picked_index": pick_index,
      "label": picked.get("label", ""),
      "summary": picked.get("one_liner", ""),
      "full_log": picked.get("full_log", "") or json.dumps(picked, ensure_ascii=False),
      "selected_variant": picked,
      "collage": None,
  }
  ```
- `v.simulated_run = main_record`
- `v.stage_completed["simulate"] = True`
- `vs.save(v)`
- 返回 `{"ok": True, "volume": v.as_dict()}`

#### 1e. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/collage`（拼贴合成）
请求体：
```json
{
  "job_id": "...",
  "collage": {
    "base_index": 0,            // 主线用哪一场
    "selections": [             // 各片段从哪场选
      {"section": "opening",  "from_index": 0, "note": "用好运线的开场氛围"},
      {"section": "climax",   "from_index": 2, "note": "反转线高潮更惊喜"},
      {"section": "death_1",  "from_index": 1, "note": "厄运线第一个死亡更有冲击力"},
      {"section": "death_2",  "from_index": 0, "note": ""},
      {"section": "ending",   "from_index": 2, "note": ""},
      {"section": "npc_fates","from_index": 1, "note": ""}
    ],
    "user_note": "想保留..."
  }
}
```
- 校验 job 存在 done。
- 取出 variants；base = variants[collage.base_index]。
- **本任务不做 AI 合成**（留给未来），只做机械合并：主记录 = base 的 full_log 作为主骨架，selections 里每段附加 "【取自{label}】" 标记，最后附上 user_note。生成 merged_log 字符串：
  ```
  ==== 拼贴主跑团记录 ====
  基线：{base.label}
  基调：{base.one_liner}

  ----- 开场（{selections[i].label}）-----
  {the selected variant's first section or full_log first 500 chars}

  ...依次按 selections 顺序每段打标...

  ----- 作者备注 -----
  {user_note}
  ```
- 简化做法：因为 variants 结构复杂（每个 variant 是 coc_simulator 产出的 dict），如果 full_log 不存在，就把整个 variant JSON 字符串化拼进去。**目标是跑通流程，不追求精细分 section**。实际实现时：遍历 selections，每个取 `variant.get('one_liner','')` + `variant.get('log_tail','')`（或 json.dumps 前 800 字符），每段加 【取自X线】 头。
- 构造主记录：
  ```python
  main_record = {
      "mode": "collage",
      "base_index": collage.base_index,
      "selections": collage.selections,
      "user_note": collage.user_note,
      "summary": f"拼贴（基线{collage.base_index+1}，取{len(collage.selections)}段）",
      "full_log": merged_log,
      "selected_variant": variants[collage.base_index],
      "collage": collage,
  }
  ```
- `v.simulated_run = main_record`
- `v.stage_completed["simulate"] = True`
- `vs.save(v)`
- 返回 `{"ok": True, "volume": v.as_dict()}`

### 2. 注册新路由
**文件**：`backend/app/api/main.py` 末尾，在 register_adapt_routes 之后：
```python
from .simulate_routes import register_simulate_routes
register_simulate_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    project_dir=_project_dir,
    book_for=_book_for,
    fields_for=_fields_for,
    coc_run_for=_coc_run_for,
    field_run_for=_field_run_for,
    chore_provider=_chore_provider,
)
```
**注意**：`_coc_run_for`、`_field_run_for` 这两个 helper 是否存在？先 Grep main.py 确认。如果没有（可能是 `_coc_for` 或其他名字），读一下 main.py 里 register_job_routes 前面的 helper 定义块，找到 coc store 和 field_run store 的获取函数名再用。不要猜，不确定就 Read/Grep 确认。

### 3. phase_routes 无需改动
simulate 是 stage_order 的第二步，只要 adapted 完成且 stage_completed.simulate=False，自然会返回 phase=simulate。已有判定逻辑正确。

---

## 前端改动

### 4. 新建 `frontend/src/views/CocSimView.vue`

UI 分三个态：`准备态 / 跑团中 / 结果对比 + 拼贴篮`

#### 4a. 顶部
- 标题"🎲 COC 并行跑团仿真"
- 副标题"3 场并行（同组 PL 好运/厄运线 + 异组 PL 反转线），挑一场作为主线或拼贴多场精华"
- 如果 volume.simulated_run 已存在（已完成过），显示"✓ 已选定跑团走向：{summary}"+"重跑"按钮（重跑清空 v.simulated_run，调 PATCH volumes 把 simulated_run 设为 null —— PATCH 端点需要支持 simulated_run 字段！见 4.8）

#### 4b. 准备态
- user_directive 输入框（textarea 3 行，"可选：对本场跑团的特别指令，例如'多死人''让记者死在 Day3'..."）
- 折叠选项区（el-collapse）：
  - max_pairs_per_session（slider 4-10，默认 6）
  - max_turns_per_session（slider 10-40，默认 20）
  - tangchi_replace_idx（radio: 不替换=0/1/2/3，默认 null=不替换）
- 主按钮"🎲 开跑 3 场并行 COC"→ 调 start，进入跑团中态

#### 4c. 跑团中态
- 订阅 SSE（simulateApi.subscribeJobs）
- 显示整体进度条 el-progress :percentage="job.progress"
- 消息行：{{ job.message }}
- 3 张卡片并列（el-row :gutter=16，el-col :span=8），每张卡显示：
  - 标签（好运线/厄运线/反转线）
  - 如果有 sub_results 中该场的数据，显示 one_liner；否则显示"等待开场..."
- 不可交互，等 done。

#### 4d. 结果对比态（job.done）
- 3 张结果大卡（el-row :gutter=16，每卡 el-col :span=8）：
  - 卡片顶部：label 标签 + flavor
  - 卡片中部：one_liner（加粗）
  - 卡片下部：如果有 day_breakdown / deaths / ending 等字段，折叠展示（el-collapse）。考虑到 variants 可能字段复杂，先用 JSON 前 500 字符预览即可（`<pre class="log-preview">{{ JSON.stringify(variant, null, 2).slice(0,800) }}</pre>`）。后续再优化。
  - 底部按钮：
    - "选这一场当主线" → pick-one
    - "加入拼贴篮" → toggle 该场的 selected_sections（简单做法：点击后把整场标记为备选，切换一个 local collageSelection 数组）

- 下方"或：拼贴合成"区域（el-card 浅色）：
  - 说明："拼贴模式：从 3 场中各挑你喜欢的元素，AI 会合成一份主跑团记录（机械合并，AI 合成后续迭代）"
  - 基线选择（el-radio-group）：选哪场作为主线骨架
  - 6 个 section 下拉（el-select）：开场/第一个死亡/第二个死亡/关键线索/高潮/结局，每个下拉可选"从好运线/厄运线/反转线取"
  - 备注 textarea
  - "🧩 合成主跑团记录"按钮 → 调 collage 端点
- 选完/合成完后，刷新 volume 并 emit('done') 让 HomeView reloadPhase。

#### 4e. 已完成态（volume.simulated_run 存在）
- 显示模式标签（单选/拼贴）+ summary
- pre 展示 full_log 前 1500 字（max-height 400px el-scrollbar）
- "下一阶段：魂穿唐迟"按钮 → emit('done')
- "重跑"按钮 → 清空 v.simulated_run 回到准备态（调 PATCH volumes {simulated_run: null}）

### 5. api.ts 扩展
在 api.ts 里加 simulateApi（类似 adaptApi 的结构）：
- `getVolume` 复用 adaptApi.getVolume（不用重复定义，前端 import 时共用）
- `startSim(pid, volNo, body)` → POST `/projects/${pid}/workbench/volumes/${volNo}/simulate/start`
- `listJobs(pid, volNo)` → GET `.../simulate/jobs`
- `subscribeJobs(pid, volNo, onEvent)` → EventSource 连 `.../simulate/stream`
- `pickOne(pid, volNo, jobId, pickIndex)` → POST `.../simulate/pick-one`
- `collage(pid, volNo, body)` → POST `.../simulate/collage`

### 6. PATCH volumes 支持 simulated_run 清空
**后端**：
- `backend/app/book/volume.py`：PLANNING_FIELDS 加 "simulated_run"（和 adapted_module 一样）
- `backend/app/api/main.py`：VolumeUpdateIn 加 `simulated_run: dict | None = None`

这样前端 PATCH `{simulated_run: null}` 能清空重跑。

### 7. 注册 CocSimView 到 HomeView
**文件**：`frontend/src/views/HomeView.vue`
- import CocSimView
- 加分支：`<CocSimView v-else-if="phase==='simulate'" :vol-no="currentVolumeNo" @done="loadPhase" />`

### 8. 验证
- vue-tsc 零错误
- 后端 Python import 测试：`from app.api.simulate_routes import register_simulate_routes`
- curl 验证端点注册：`GET /api/projects/20261014-c6c432/workbench/volumes/1/simulate/jobs` 应返回 200 + jobs:[]（如果没有卷 1 就返回 404，说明路由注册成功但没数据）
- 浏览器验证：
  - 为了让 phase 能走到 simulate，需要手动给某个卷设置 adapted_module 并 stage_completed.adapt=true。用 Python 直接写：
    ```python
    # 给 volumes/index.json 创建一个卷 1，adapted_module="测试副本核文本", stage_completed.adapt=true
    # 或者用 curl 调 POST /workbench/volumes 创建卷，再 PATCH 设置 adapted_module 和 stage_completed.adapt=true（但 stage_completed 不能通过 PATCH 改，需要调 /stage-complete/adapt）
    ```
  - 简单做法：用 adaptApi 走一遍正常流程（如果有 PDF 最好），或者在 backend 目录下写个临时 python 脚本直接造一个 volume 数据，让 phase=simulate。**实施者自己决定最简路径**。
  - 浏览器刷新后应看到 CocSimView 的准备态界面；F12 无红错。
- **注意**：真正跑 COC 仿真会消耗 LLM 调用且耗时较长（3 场各 20 回合），**不要在验证时真的跑完整 3 场**（会耗 token 且超时）。验证时：
  - 只验证界面渲染和 SSE 连接
  - 如果要验证后端逻辑，可以临时把 max_turns_per_session 调小（通过前端 slider 调到 1），但仍可能跑 30+ 秒；或者让实施者做一个"模拟完成"的测试路径（后端 _work 里如果 max_turns=1 直接返回 mock variants，别真调 LLM）。**建议**：实施者在 _work 开头加一个短路：如果 body.max_turns_per_session <= 1，直接 sleep 2 秒模拟后构造 3 个假 variants 返回（含 label/one_liner/full_log），方便前端验证"完成-选版-拼贴"链路。**重要**：这个短路仅在 max_turns<=1 时生效，正常参数（>=2）走真实 run_coc_parallel_batch。这样既不破坏真实流程，又方便测试。

### 9. Commit
单一 commit：
```
feat(fe+be): CocSimView 3场并行COC跑团+单选/拼贴合成，适配工作台 simulate 阶段
```

### 10. 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-5-report.md`：
- commit hash
- 改动文件清单
- vue-tsc 输出
- 后端 import 测试
- curl 验证 jobs 端点
- 浏览器验证（用 max_turns=1 mock 路径走完整流程，截图描述或文字描述 UI 状态）
- 遗留点（真跑团会消耗较多 token，AI 智能拼贴合成本任务未做，用机械字符串拼接代替）

---

## 重要提醒
- 不要重启 dev server
- coc_run_for 和 field_run_for 的 helper 名不确定就 Read main.py 确认
- variants 字段结构复杂，前端预览先用 JSON 字符串截取，不要尝试完美结构化展示（那是 Task 9 节奏/Task 8 scaffold 的事）
- max_turns<=1 的短路 mock 只用于前端测试，真实流程（>=2）必须走 run_coc_parallel_batch
- 不要删旧文件（SessionView 等）
- 样式 scoped，中文字案自然
- 不确定就 Read 文件
