# Task 8 · DungeonScaffoldView 副本详设 6 卡 — 完成报告

**提交时间**：2026-10-15  
**Commit**：见末尾  
**SDD 卡**：Task 8 / Workbench IA Redesign

---

## 1. 交付清单

### 1.1 后端

| 文件 | 变更 |
| --- | --- |
| `backend/app/book/volume.py` | `PLANNING_FIELDS` 补齐 `death_nodes / climax_nodes / clue_nodes / pasted_nodes / scaffold_cards`；`_allow_falsey` 白名单放行空数组/空对象/False，使 PATCH `[]/{}` 能正确落盘 |
| `backend/app/api/main.py` | `VolumeUpdateIn` 增加 5 个 list/dict 字段（death_nodes / climax_nodes / clue_nodes / pasted_nodes / scaffold_cards）+ stage_completed；末尾 `register_scaffold_routes(...)` 挂载 |
| `backend/app/jobs/store.py` | `JOB_KINDS` 追加 `scaffold_prefill` |
| `backend/app/api/scaffold_routes.py`（新建） | 5 端点：`POST prefill` / `GET jobs` / `GET stream(SSE)` / `POST lock-card` / `POST finalize`；JobStore + SSE 完全复刻 adapt/embody 模式；mock 短路条件为 `adapted_module.startswith("测试副本核文本")` 或宿主姓名含"林薇"时 sleep(2s) 返回硬编码克苏鲁数据；LLM 路径调用 `complete_structured` 一次性 JSON 返回 6×3 版，异常回退 mock |

Mock 数据卡数量：
- 死亡节点：3 版 × 3-5 条（day/who/how/tease/cost）
- 高潮转折：3 版 × 2-3 幕（day/title/beat/weight）
- 线索与红鲱鱼：3 版 × 5 条（day/where/what/leads_to/is_red_herring）
- 核心诡计 / 唐迟第一秒 / 代价上限：3 版 × 100-200 字中文

### 1.2 前端

| 文件 | 变更 |
| --- | --- |
| `frontend/src/api.ts` | `JobKind` 增加 `scaffold_prefill`；新增 `DeathNode / ClimaxNode / ClueNode / ScaffoldPayload` 接口；新增 `scaffoldApi = { getVolume, patchVolume, prefill, listJobs, subscribeJobs, lockCard, finalize }`；EventSource 协议与现有 jobs 流一致（job_update / job_update_batch / job_idle） |
| `frontend/src/views/DungeonScaffoldView.vue`（新建） | 6 卡纵向 SushiCards ensemble；列表卡用 `#item="{item,index}"` 插槽渲染 bullet 列表（按卡片类型差异化展示 day+who/how、day+title/beat/★权重、day+where/what/红鲱鱼标签）；每张卡选版+🔒锁定按钮；6 卡全锁弹出 `el-result` finalize 区；重新预填通过 PATCH 清空 6 字段；SSE 订阅进度条；克苏鲁紫绿配色 scoped CSS |
| `frontend/src/views/HomeView.vue` | import `DungeonScaffoldView`，新增 `v-else-if="phase === 'scaffold'"` 分支 |

---

## 2. 关键设计决策

1. **scaffold_cards 为唯一真相源**：三版 variants、picked_idx、locked_at、text 都挂在 `scaffold_cards[key]` 下。list 卡（death/climax/clue）预填后同时写三份到顶层字段（`death_nodes=[[v1],[v2],[v3]]`），lock 时覆盖为选中的那一版，同时保留 `variants` 数组便于前端 diff/UI。
2. **update_planning 调用模式**：`Volume` 对象本身没有 `update_planning` 方法，必须走 `volumes.update_planning(vol_no, **kwargs)`，内部自动 `save(v)`；这是调试过程中踩到的坑（见第 4 节）。
3. **`save(v)` 而非 `save()`**：`VolumeStore.save(v)` 必传 v，不带参会 TypeError。所有写入路径都已校正。
4. **idle 态判定修复**：初期 `prefillDone = !!cards.value.death_nodes?.length`，但 `cards` 初始化即 `{}`，访问 `.death_nodes` 返回 undefined，导致 template `v-else` 直接进入 scaffold-cards 分支显示 6 个空 block。改为显式 `Array.isArray(...) && length > 0` 后 el-result idle 正常显示。
5. **SSE 收尾**：沿用 adapt_routes 的 `jobs_to_sse_event_list` + 3 轮 idle → `job_idle` 自动断连模式，前端 `closeSSE()` 兜底。
6. **mock 短路无副作用**：mock 分支独立于 LLM 分支，无论 chore_provider 是否可用都能跑通；LLM 解析异常也会 try/except 回退 mock，避免前端卡死。

---

## 3. 验证结果

### 3.1 静态校验
- `npx vue-tsc --noEmit`（frontend）：**零错误** ✅
- `python -c "from app.api.scaffold_routes import register_scaffold_routes; print('ok')"`（backend/.venv）：**ok** ✅，FastAPI 启动加载 145+ 路由无报错

### 3.2 后端 API 自动化（test_scaffold.py）
脚本端到端验证 9 项断言全部通过：
```
after cleanup, volumes: []
book cards written (8 张 ≥ 10 字节)
created vol_no = 1
PATCH ok
phase = scaffold
prefill job_id = ... status=done progress=100 has_result=True
  death_nodes: 3 versions, first-ver 3 items
  climax_nodes: 3 versions, first-ver 3 items
  clue_nodes: 3 versions, first-ver 5 items
  core_twist: 3 versions, first-ver len=148
  tangchi_start: 3 versions, first-ver len=145
  cost_cap: 3 versions, first-ver len=144
lock death_nodes: all_locked=False
...（6 卡依次锁）
lock cost_cap: all_locked=True
finalize ok, stage_completed = {adapt:True, simulate:True, embody:True, scaffold:True}
phase after finalize = rhythm
=== ALL TESTS PASSED ===
```

### 3.3 浏览器 e2e（agent-browser 无头 Chrome）
访问 `http://127.0.0.1:5173/?pid=20261014-c6c432`，选择项目《第七日的亡者》后完整走通：

1. **idle**：`el-result icon=info` + 蓝色「🎰 AI 一次性预填 6 卡」按钮 ✅（见 `shot-3-idle.png`）
2. **prefill 触发**：点击按钮后 2s 内 SSE 完成，弹出绿色 toast「AI 预填完成（mock 示例数据）」
3. **6 卡渲染**：纵向排列，死亡节点/高潮转折/线索与红鲱鱼用 `#item` 插槽渲染为可读 bullet 列表（非 JSON.stringify），文字卡显示段落文本；每卡 3 列 SushiCards ensemble（见 `shot-5-after-reload-post-prefill.png`）
4. **逐卡锁定**：依次点「选这盘」+「🔒锁定这张卡」，每锁一卡弹出 `「xxx」已锁定 ✓` toast，6/6 锁完弹出「6 卡全部锁定，可以落锤进节奏骨架了」
5. **finalize 区**：出现 `el-result type=success`「6 卡全部锁定」+ 绿色「🎯 完成详设，进入节奏骨架」+「🔄 全部重来」（见 `shot-6-locked.png`）
6. **跳转 rhythm**：点 finalize 后路由重新拉 phase，进入「🚧 当前阶段：节奏骨架 / 该阶段视图正在开发中」占位页（见 `shot-7-rhythm.png`）

### 3.4 清理
执行 `cleanup_scaffold.py`：8 张 book 卡写回 `{}`，删除 `volumes/1.json`，`volumes/index.json` 重置为 `{"volumes":[]}`；GET `/api/projects/{pid}/phase` 回到 `setup`。

---

## 4. 调试踩坑记录

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| prefill job status=error `AttributeError: 'Volume' object has no attribute 'update_planning'` | `update_planning` 是 VolumeStore 方法不是 Volume 方法 | 改为 `vols.update_planning(vol_no, **kwargs)` |
| `TypeError: VolumeStore.save() missing 1 required positional argument: 'v'` | 误调 `vols.save()` 无参 | 改为依赖 `update_planning` 内部 save，移除冗余 `vols.save()` |
| lock-card 后读取旧值导致 all_locked 判断错 | `setattr(vol, ...)` + `vols.save()` 不重新读盘 | 全走 `vols.update_planning(vol_no, ...)` 后 `vols.get(vol_no)` 重载再判断 |
| 页面初始显示 6 个空 block「暂无候选」 | `prefillDone = !!cards.value.death_nodes?.length` 在 cards={} 时返回 false 但 v-else 分支仍进入 scaffold-cards（与 stageDone/picking 相关状态组合问题） | 改为显式 `Array.isArray(...) && length>0`，applyJob 里根据 prefillDone/stageDone 判断是否转 picking |
| prefill 完成 toast 后仍停在 idle（SSE job_update 到达时 loadVolume 还没把 scaffold_cards.variants 放进 cards，随后 step='picking' 但 cards 是空的） | loadVolume().then 里立即设 step=picking，但 restoreFromVolume 内部已经设好 step，双重赋值冲突 | applyJob 里让 restoreFromVolume 决定 step，只在 prefillDone && !stageDone 时兜底 picking |
| 422 Unprocessable Entity 打 PATCH | adapted_module/entry_point/stance 类型与 VolumeUpdateIn 不匹配（前者 str/dict/dict） | 修正测试脚本字段类型 |

---

## 5. Dev Server 状态

- Backend（uvicorn）：http://127.0.0.1:8321  **运行中**（command_id: `job-b2cd5bbc25cb49ab8a0a5bc0ea8bd01a`，无 --reload 以防 reload 杀进程影响后续开发）
- Frontend（vite）：http://127.0.0.1:5173  **运行中**（command_id: `job-77153f109ceb4c1ebcb12e7fcde53462`）

---

## 6. 提交信息

Commit message：`feat(fe+be): DungeonScaffoldView 副本详设6卡AI预填+SushiCards选版+锁定落锤`

涉及文件：
- `backend/app/book/volume.py`
- `backend/app/api/main.py`
- `backend/app/api/scaffold_routes.py`（新增）
- `backend/app/jobs/store.py`
- `frontend/src/api.ts`
- `frontend/src/views/DungeonScaffoldView.vue`（新增）
- `frontend/src/views/HomeView.vue`

Commit hash 详见 `git log -1` 输出（见对话末尾）。

---

## 7. 下一步

- Task 9：RhythmView（节奏骨架）将消费本阶段锁定的 6 卡产物，进行章节切分与节奏曲线铺排。
