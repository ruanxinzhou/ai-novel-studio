# Task 4 Brief：ModuleAdaptView（上传 COC 模组 PDF + pdf5 并行改编 + 5 盘回转寿司选版）

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

Vite 已在 5173、uvicorn 已在 8321。**不要重启 dev server**。

## 目标
让用户在 adapt / adapt_empty 阶段可以上传 COC 模组 PDF，触发 5 版并行改编后台任务，进度用 SSE 实时推送，改编完成后用 SushiCards 风格展示 5 个版本（不是深色 VariantPicker），选完后写入 `volume.adapted_module` 并把 `stage_completed.adapt = true`，前端自动刷新 phase 进入下一阶段（simulate）。

## 设计决策（重要）
- **不复用旧 session kindle 路径**：新建一个独立的 workbench adapt 路由，完全不依赖 session（这样不需要先创建 session），直接对 volume 操作。
- **pdf5 任务结果存储**：仍然用进程内 JobStore（`backend/app/jobs/store.py`）单例，用 pseudo session_id = `f"adapt:{pid}:{vol_no}"`，复用现有 run_in_background 机制。
- **5 版改编 UI**：直接用现有 SushiCards 组件（`plateCount={5}`），把 pdf5 返回的 5 个 adaptations 转换成 Candidate 格式（card_key='adapt_module', content=adapted_premise, twist=flavor, rationale=one_liner + 改动词条摘要）。不用 VariantPicker。
- **不做寿司生成/编辑**：Task 4 只做"PDF 上传→并行改编→5 选 1→落锤进 simulate"，选定后直接写入 volume.adapted_module（全文），不进入 SushiCards 的编辑态（因为 pdf5 产出的是副本核文本，通常直接用；AI 微调留到 Task 6 通用寿司重构后再接入）。但仍用 SushiCards 的"候选展示"样式（不开启锁定/编辑态），自己处理 pick 逻辑。
- **如果 phase=adapt_empty**（还没创建卷）：前端先自动调 `POST /workbench/volumes` 创建 dungeon 卷，然后把用户带入刚创建卷的 adapt 阶段（页面 reloadPhase 即可）。

---

## 后端改动

### 1. 扩展 volumes PATCH 支持保存 adapted_module
**文件**：`backend/app/book/volume.py`
- 在 `PLANNING_FIELDS` 集合里加入 `"adapted_module"`（第 330 行附近）。这样现有 `PATCH /api/projects/{pid}/volumes/{vol_no}` 就能通过 body `{adapted_module: "..."}` 保存（VolumeUpdateIn 也需要加字段）。

**文件**：`backend/app/api/main.py`
- 在 `VolumeUpdateIn`（约 L4147）增加字段：`adapted_module: str | None = None`
- 然后 stage_completed 也需要通过 PATCH 保存吗？目前 PATCH 只能改 PLANNING_FIELDS 里的字段。建议：**新增一个端点** `POST /api/projects/{pid}/workbench/volumes/{vol_no}/stage-complete`，body `{stage: "adapt"|"simulate"|...}`，把 `v.stage_completed[stage] = True; vs.save(v)`。这样更语义化，比让前端 PATCH stage_completed 清晰。可以放在新建的 adapt_routes.py 里。

### 2. 新建 `backend/app/api/adapt_routes.py`
用和 phase_routes.py/settle_routes.py 一致的 register 函数模式，导出 `register_adapt_routes(app, *, require_project, volumes_for, project_dir, chore_provider, prompt_store)`。

**依赖说明**：
- `chore_provider: Callable[[str], Provider]` 和 `prompt_store: Any`：需要从 jobs_routes 拿吗？不要，直接从 main.py 里注入——看 main.py 里 register_job_routes 调用处，已经传入 chore_provider 和 prompt_store，同样传入 register_adapt_routes 即可。

端点（全部在 `/api/projects/{pid}/workbench/volumes/{vol_no}/...` 下，加上一个 vol detail GET）：

#### 2a. GET `/api/projects/{pid}/workbench/volumes/{vol_no}`
新增（现有 main.py 只有 list 和 latest，没有按 no 查）：
- `v = vs.get(vol_no)`，找不到返回 404。
- 返回 `{"volume": v.as_dict()}`

#### 2b. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/pdf5`
multipart/form-data，字段：`file: UploadFile`、`user_hint: str = Form("")`、`n: int = Form(5)`。

逻辑（参考 `jobs_routes._start_pdf5`，但不依赖 session）：
1. `require_project(pid)`；校验 vol 存在（`vs = volumes_for(pid); v = vs.get(vol_no)`；若 v 为 None 或 v.volume_kind != 'dungeon' → 404/422）。
2. 校验 PDF：后缀 .pdf；大小 ≤ 20MB。
3. `extract_pdf_text(pdf_bytes)` 抽文本，长度 < 100 抛 422。
4. 创建 job：`job = job_store.create(session_id=f"adapt:{pid}:{vol_no}", kind="pdf_adapt5", meta={"filename":file.filename, "n":n, "volume_no":vol_no})`。注意 JOB_KINDS 里已有 "pdf_adapt5"，直接用即可。
5. 启动后台任务（`run_in_background`），_work 函数直接抄 jobs_routes 的 _start_pdf5 里的 _work 逻辑（flavor 列表、ThreadPoolExecutor max_workers=min(n,3)、每线程 prov = chore_provider("sushi")、r = adapt_pdf_source(prov, ps, pdf_text, user_hint=hint)、append_sub_result 每个成功结果）。但有两点不同：
   - 不写 s.add_message（没有 session），只更新 job。
   - 最终 result 仍是 `{"adaptations": success, "errors": errors}`（和 pdf5 一致）。
6. 返回 `{"ok": True, "job_id": job.job_id, "job": job.as_dict()}`。

#### 2c. GET `/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/jobs`
- 返回 `{"jobs": [job.as_dict() for job in job_store.list_session(f"adapt:{pid}:{vol_no}")]}`。

#### 2d. GET `/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/stream`（SSE）
- 仿照 jobs_routes._stream_jobs 写法（用 StreamingResponse，media_type="text/event-stream"，每秒 yield 一次 job_update_batch，3 轮 idle 后结束）。可以直接参考 jobs_routes 的 SSE 实现，把 session_id 换成 `adapt:{pid}:{vol_no}`。
- 不需要 cancel 和 pick 端点（选版由下面 2e 处理）。

#### 2e. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/pick`
请求体：`{"job_id": str, "pick_index": int, "extra_edits": str = ""}`
- 校验 job 存在（job_store.get(job_id)）；job.kind == "pdf_adapt5"；status == "done"。
- 从 `job.result["adaptations"][pick_index]` 取 picked dict。
- 文本 = picked["adapted_premise"]，如果 body.extra_edits 非空，简单拼一个提示追加到末尾？**不要拼**（AI 微调留到后续），直接用 adapted_premise 原文。
- 写入 `v.adapted_module = adapted_premise`（str）。
- 写入 `v.stage_completed["adapt"] = True`。
- 如果 v.title 还是"卷N"默认标题，尝试从 extra 里取；否则不动（标题由用户手动改或前端从 flavor 生成一个"XXX风格副本"的建议值存到 v.title 也可以；本任务不处理，保持原样）。
- `vs.save(v)`。
- 返回 `{"ok": True, "volume": v.as_dict()}`。

#### 2f. POST `/api/projects/{pid}/workbench/volumes/{vol_no}/stage-complete`
请求体：`{"stage": str}`
- 校验 stage 在 {"adapt","simulate","embody","scaffold","rhythm","settle"} 白名单里。
- `v.stage_completed[stage] = True`
- `vs.save(v)`
- 返回 `{"ok": True, "volume": v.as_dict()}`

这个端点通用，后续 Task 5-9 各阶段完成都会调用。注意：2e 的 pick 端点已经自己设置 adapt=True，2f 是给其他阶段用的；可以保留（adapt 阶段也允许前端单独调 2f 标记，但建议 2e 内部自己做，不依赖前端二次调用）。

### 3. 注册新路由
**文件**：`backend/app/api/main.py`
- 在文件末尾（register_brief_routes 之后）新增：
  ```python
  from .adapt_routes import register_adapt_routes
  register_adapt_routes(
      app,
      require_project=_require_project,
      volumes_for=_volumes_for,
      project_dir=_project_dir,
      chore_provider=_chore_provider,
      prompt_store=_prompt_store,
  )
  ```
- 需要读一下 main.py 末尾 register_job_routes 那一行，确认参数名（chore_provider 和 prompt_store 传给 register_job_routes 时用的是哪个局部变量）。用同名变量传即可。**如果有疑问，直接读文件确认，不要猜。**

### 4. 更新 phase_routes 让 adapt 阶段判据正确
**文件**：`backend/app/api/phase_routes.py`
- 当前 stage_order 第一步就是 adapt（只要 stage_completed.adapt=False 就返 phase='adapt'），这是对的。但要确认：**adapt_empty 的判据是"没有任何 volume"**。Task 3 的 adapt_empty 引导页点"创建新副本卷"会创建卷，创建后 stage_completed.adapt 默认是 False，所以 phase 会跳成 adapt。逻辑已通，不需要改判定逻辑。**但**adapt_empty 引导页上的"上传模组 PDF 直接开卷"快捷流程如果前端直接先建卷再传 PDF，也是走 phase=adapt。保持当前判定即可，phase_routes.py 无需改动。

---

## 前端改动

### 5. 新建 `frontend/src/views/ModuleAdaptView.vue`

UI 结构（垂直布局，从上到下）：

1. **阶段标题条**：
   - 标题"📕 上传 COC 模组并改编"
   - 副标题"上传官方/第三方 COC 模组 PDF，AI 会并行跑 5 版差异化改编，避开版权风险"

2. **PDF 上传区**（初始状态，没上传没跑 job）：
   - el-upload drag 区域（accept=".pdf", :auto-upload="false", :on-change="onFilePick", :show-file-list="false"）
   - 拖拽提示"把 PDF 拖到这里，或点击选择"
   - 下方：user_hint 输入框（el-input type="textarea", :rows="2", placeholder="可选：对改编方向的补充说明（例如想要更阴冷、想结合程序员 oncall 主题...）"）
   - 下方：n 选择（el-radio-group，选项 3/5/8，默认 5）
   - 主按钮"开始并行改编"（type="primary", :disabled="!file", @click="startPdf5"）
   - 注意：el-upload 用 :http-request 自定义上传（直接调 startPdf5 走我们自己的端点），不要用默认 action。

3. **进度态**（job 开始后到完成前）：
   - el-progress :percentage="job.progress" :status="job.status==='error'?'exception':undefined"
   - 文案："{{ job.message }}"
   - 已完成 N/n 版时，横向展示已完成的版本卡片（小卡，只显示 flavor + one_liner，用 sub_results 实时渲染）
   - 按钮："取消"（调用 cancel 端点；本任务**不实现 cancel**，简单放一个"取消（暂不可用）"灰掉按钮或省略）

4. **5 版结果对比**（job.status === 'done'）：
   - 用 SushiCards 组件展示 5 版：
     ```html
     <SushiCards
       cardKey="adapt_module"
       cardLabel="改编版本"
       :candidates="candidates"
       :editing="null"
       :messages="[]"
       axisLabel="5 版差异化改编"
       phase="adapt"
       :plateCount="5"
       :isActive="true"
       :lockedText="null"
       @pick="onPickAdapt"
     />
     ```
   - 把 adaptations 转成 candidates 数组：
     ```ts
     candidates.value = adaptations.map((a, idx) => ({
       id: `adapt-${idx}`,
       phase: 'adapt',
       card_key: 'adapt_module',
       content: a.adapted_premise,
       axis_label: a.flavor,
       axis_dimension: 'flavor',
       rationale: (a.changed_elements||[]).slice(0,3).join('；') || a.one_liner,
       twist: a.flavor,
       created_at: new Date().toISOString(),
     }))
     ```
   - SushiCards 的 @pick 会传 candidateId，拿 idx 后调 pick 端点。
   - 注意：SushiCards 默认会显示"上N盘/换一拨"按钮并 emit generate，我们不需要重新生成（用户要重新跑就点页面上的"重新上传 PDF"按钮），所以通过 `:isActive="false"` 来禁用？但 isActive=false 可能整张卡不可点击。**更简单的办法**：在 onGenerate 回调里什么都不做（空函数），让"换一拨"按钮点了无效；或者用 CSS 隐藏 generate 按钮（推荐：给 ModuleAdaptView 加 `:deep(.sushi-generate-bar) { display: none }`）。
   - 选完后 onPickAdapt：调 pick 端点，成功后触发父容器刷新 phase（emit('done') 或调用注入的 refresh 函数）。

5. **已选定 adapted_module**（volume.adapted_module 非空且 stage_completed.adapt===true 时）：
   - 显示"✓ 改编版本已选定"的绿色 alert
   - 展示 adapted_module 文本预览（el-scrollbar，max-height 300px，pre-wrap 样式）
   - 按钮"重新改编"（点击清空 v.adapted_module 并重置状态，调 PATCH vol？或者直接回到上传态但不清空——本任务不实现"重新改编"，只展示已选定状态，提供"下一阶段：模拟跑团"按钮即可，点了 emit('done') 让 HomeView 刷新 phase 跳到 simulate）。

6. **加载逻辑**：
   - onMounted 时：
     - 如果 props.volNo 有值：
       - 查 GET `/api/projects/{pid}/volumes/{volNo}` 拿 volume 数据（现有 PATCH 端点是 `/api/projects/{pid}/volumes/{vol_no}`，GET 端点也存在——volumes detail 端点在 main.py 里搜一下）
       - 如果 volume.adapted_module 已存在 → 显示"已选定"态
       - 否则查 GET `/api/projects/{pid}/workbench/volumes/{volNo}/adapt/jobs` 看有没有进行中/刚完成的 job，有则订阅 SSE 继续显示进度/结果
     - 没有 volNo（adapt_empty 阶段）→ 显示"创建第一卷+上传 PDF"引导：先调 POST /workbench/volumes 创建卷，然后进入同一页面。

### 6. 新 api.ts 方法
在 `frontend/src/api.ts` 里添加 adaptApi（不要和现有 api 对象混，单独 export）：

```ts
export const adaptApi = {
  // 注意：现有 main.py 没有 GET /volumes/{vol_no}，新端点 GET /workbench/volumes/{vol_no} 已在 2a 加
  getVolume: (pid: string, volNo: number) =>
    req<{volume: any}>(`/api/projects/${pid}/workbench/volumes/${volNo}`),
  startPdf5: (pid: string, volNo: number, file: File, userHint: string, n: number) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('user_hint', userHint)
    fd.append('n', String(n))
    return req<{ok: boolean; job_id: string; job: Job}>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/adapt/pdf5`,
      { method: 'POST', body: fd },
    )
  },
  listJobs: (pid: string, volNo: number) =>
    req<{jobs: Job[]}>(`/api/projects/${pid}/workbench/volumes/${volNo}/adapt/jobs`),
  pickAdapt: (pid: string, volNo: number, jobId: string, pickIndex: number, extraEdits = '') =>
    req<{ok: boolean; volume: any}>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/adapt/pick`,
      { method: 'POST', body: JSON.stringify({job_id: jobId, pick_index: pickIndex, extra_edits: extraEdits}) },
    ),
  markStageComplete: (pid: string, volNo: number, stage: string) =>
    req<{ok: boolean; volume: any}>(
      `/api/projects/${pid}/workbench/volumes/${volNo}/stage-complete`,
      { method: 'POST', body: JSON.stringify({stage}) },
    ),
  }
}
```

SSE 订阅：仿照 api.ts 里已有的 `jobsApi.subscribe(pid, sid, onEvent)` 实现一个 `adaptApi.subscribeJobs(pid, volNo, onEvent)`，EventSource 指向 `/api/projects/{pid}/workbench/volumes/{volNo}/adapt/stream`，监听 `job_update` 和 `job_update_batch` 事件，解析 JSON 回调。**不要**用 jobsApi.subscribe（它硬编码 sessions 路径）。先读 api.ts 里 jobsApi.subscribe 的实现照抄，改 URL 即可。

### 7. SSE 端点实现注意
后端 adapt stream 端点：参考 jobs_routes._stream_jobs 写法，但要注意：
- 不能用 `from .jobs_routes import _stream_jobs`（那是 session 绑定的），自己在 adapt_routes.py 写一个生成器。
- 用 StreamingResponse，media_type="text/event-stream"，headers 里设 Cache-Control: no-cache, Connection: keep-alive, X-Accel-Buffering: no。
- 循环：取 jobs = job_store.list_session(f"adapt:{pid}:{vol_no}"), yield jobs_to_sse_event_list(jobs)，若没有 running 状态 job，idle_rounds += 1；idle_rounds >= 3 break；time.sleep(1.0)。
- 注意要 `from ..jobs.store import job_store, jobs_to_sse_event_list`。
- **不要** import jobs_routes 内部函数，直接用 store 的公开 API。

### 8. 把 ModuleAdaptView 注册到 HomeView
**文件**：`frontend/src/views/HomeView.vue`
- import ModuleAdaptView from './ModuleAdaptView.vue'
- 在路由分支里加：
  ```html
  <ModuleAdaptView v-else-if="phase==='adapt' || phase==='adapt_empty'" :vol-no="currentVolumeNo" @done="loadPhase" />
  ```
  这样 adapt 和 adapt_empty 都走 ModuleAdaptView。adapt_empty 时 volNo=0，组件内部自己创建卷。

### 9. 验证
- 类型检查：`cd frontend; npx vue-tsc --noEmit`
- 重启 uvicorn 吗？不需要（uvicorn --reload 会自动加载新文件）。但 adapt_routes 在 main.py import 时就注册了，所以新文件存盘后热重载会生效。
- 浏览器测试路径：
  1. 选一个 book 8 卡已填满的测试项目（可以用 20261014-c6c432，但它只有 5/8 卡，会卡在 setup——测试时**先用一个临时方案**：
     - 可以选 20261014-c6c432 先点击 BookSetupView 的"完成开书设定"——它会 emit done 但 phase 判定还是 setup（因为卡没填满），所以进不到 adapt。
     - **为了浏览器验证**：先用代码或 curl 手动给 20261014-c6c432 把缺的 3 张卡（remaining_pages, finale_notes, seven_day_pulse？）填成非空 JSON，或者创建一个新的测试项目。
     - 简单做法：跑一个 python 命令把 3 张空卡填成 `{"test":1}`（大小>10 字节），这样 phase 判定能走到 adapt_empty/adapt。
  2. 如果不想改测试项目数据，**只做代码级验证**：vue-tsc 通过，vite 无编译错误，uvicorn 无 import 错误，路由能正确响应 OPTIONS/404（没有 volume 时 404 是预期的）。

### 10. Commit
单一 commit：
```
feat(fe+be): ModuleAdaptView PDF上传+5版并行改编+SushiCards选版，适配工作台 adapt 阶段
```

### 11. 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-4-report.md`：
- commit hash
- 改动文件清单
- vue-tsc 输出
- uvicorn 热重载无错误（或 curl 验证新端点存在）
- 浏览器验证（如果测试项目数据允许，描述实际交互效果；否则写"浏览器 F12 无错误，路由组件渲染正常"）
- 已知遗留（取消按钮未实现、重新改编未实现、adapted_module 文本未做 AI 微调）

---

## 注意
- 后端严格用 register_adapt_routes 函数式注册，和 phase_routes/settle_routes/brief_routes 风格一致，不要用 APIRouter 全局 store。
- pdf5 _work 函数不要复用 jobs_routes._start_pdf5（因为那个函数内部 _get_session 会检查 session.current_phase=="kindle"），要在 adapt_routes.py 里写一个独立的 _work 函数，逻辑一样但跳过 session 校验。可以直接抄代码，不要 DRY。
- SushiCards 的 @generate 可以空实现并 CSS 隐藏按钮，不要为了这个重构 SushiCards（Task 6 再做通用化）。
- 不要动 SessionView/VariantPicker/WizardView 等旧文件。
- 中文字案自然。样式 scoped。
- 不要重启 dev server。
- 遇到任何不确定的东西（main.py 里 chore_provider/prompt_store 的变量名、volume detail 端点是否存在等），先 Read 文件确认再写。
