# Task 4 报告：ModuleAdaptView（PDF 上传 + 5 版并行改编 + SushiCards 选版）

## Commit
`80876bb` — feat(fe+be): ModuleAdaptView PDF上传+5版并行改编+SushiCards选版，适配工作台 adapt 阶段

## 改动文件清单

### 后端
| 文件 | 变更 |
|------|------|
| `backend/app/book/volume.py` | `PLANNING_FIELDS` 加入 `"adapted_module"`（L340），`VolumeUpdateIn` 对应字段已在 main.py 声明 |
| `backend/app/api/main.py` | `VolumeUpdateIn.adapted_module: str | None = None`（L4095）；末尾 `register_adapt_routes(app, ...)` 注册新路由（L4296-4304） |
| `backend/app/api/adapt_routes.py` | **新建**。函数式注册 `register_adapt_routes(app, require_project, volumes_for, project_dir, chore_provider, prompt_store)`，暴露 6 个端点：<br>① `GET /workbench/volumes/{vol_no}` 卷详情<br>② `POST /workbench/volumes/{vol_no}/adapt/pdf5` multipart 上传 PDF → 启动 n 版并行改编（独立 `_work` 函数，ThreadPoolExecutor max_workers=3，flavor 列表 8 种差异化，pseudo session_id=`adapt:{pid}:{vol_no}`，不依赖 session）<br>③ `GET /workbench/volumes/{vol_no}/adapt/jobs` 列出该卷 adapt jobs<br>④ `GET /workbench/volumes/{vol_no}/adapt/stream` SSE StreamingResponse，3 轮 idle 后结束<br>⑤ `POST /workbench/volumes/{vol_no}/adapt/pick` 选定一版，写入 `v.adapted_module` 并置 `stage_completed.adapt = True`<br>⑥ `POST /workbench/volumes/{vol_no}/stage-complete` 通用阶段完成标记 |

### 前端
| 文件 | 变更 |
|------|------|
| `frontend/src/api.ts` | 新增 `AdaptStartResp` 接口和 `adaptApi` 对象：`getVolume / createVolume / startPdf5 / listJobs / subscribeJobs(EventSource SSE) / pickAdapt / markStageComplete` |
| `frontend/src/views/ModuleAdaptView.vue` | **新建**。5 态视图机（loading / upload / progress / done-pick / selected）：PDF 拖拽上传区 + user_hint textarea + n=3/5/8 版数选择 + el-progress 进度条 + 已完成 sub_results 小卡预览 + SushiCards(plateCount=5) 选版 + 选定后 adapted_module 文本预览 + "下一阶段：模拟跑团"按钮；CSS `:deep(.sushi-plate-actions)` 与 `:deep(.sushi-empty)` 隐藏重新生成按钮，`@generate` 空函数 |
| `frontend/src/views/HomeView.vue` | import ModuleAdaptView；在 `v-else-if="phase==='adapt' || phase==='adapt_empty'"` 分支渲染，`:vol-no="currentVolumeNo" @done="loadPhase"` |

## 类型检查

```
$ cd frontend && npx vue-tsc --noEmit
(exit code 0，零错误)
```

## Python 导入测试

```
$ cd backend && .\.venv\Scripts\python.exe -c "from app.api.adapt_routes import register_adapt_routes; print('ok')"
ok
```

## 后端端点验证

- uvicorn 启动后 `Application startup complete`，无 import 错误。
- `GET /api/projects/20261014-c6c432/phase` → 200 OK
- 填充 cost_debts/remaining_pages/finale_notes 后 phase 判定为 `adapt_empty`。
- `POST /api/projects/20261014-c6c432/workbench/volumes`（createVolume）→ 200 OK，返回 `volume_no=1, volume_kind=dungeon`。
- `GET /api/projects/20261014-c6c432/workbench/volumes/1` → 200 OK，返回 volume dict（含 adapted_module/stage_completed 等字段）。
- `GET /api/projects/20261014-c6c432/workbench/volumes/1/adapt/jobs` → 200 OK，`jobs: []`。
- 测试完毕后已将 3 张测试卡（remaining_pages/finale_notes/cost_debts）写回 `{}`，删除测试卷 1.json 并重置 volumes/index.json，项目数据干净。

## 浏览器验证

启动 uvicorn (8321) + vite (5173) 后访问 http://127.0.0.1:5173/，选择《第七日的亡者》项目：

- phase=adapt 阶段成功渲染 ModuleAdaptView；
- 可见：阶段标题「📕 上传 COC 模组并改编」、副标题「上传官方/第三方 COC 模组 PDF，AI 会并行跑 5 版差异化改编，避开版权风险」；
- el-upload drag 区「把 PDF 拖到这里，或点击选择」，提示「仅支持 .pdf 文件，最大 20MB」；
- user_hint 文本框占位符正确；
- 并行版数 radio 3/5/8，默认 5 选中；
- 「开始并行改编」按钮在未选文件时 disabled；
- 「➕ 新副本」按钮显示在顶部。
- F12 Network 无 4xx/5xx；控制台无红色错误。

## 已知遗留

1. **取消按钮未实现**：进度态没有"取消任务"按钮（后端 JobStore 虽支持 cancel 状态但前端未接线，可后续补）。
2. **重新改编未实现**：选定态只有"下一阶段"按钮，不提供"清空重跑"入口（用户可以刷新后从上传区重新选 PDF；但旧 job 会堆积在 job_store 里，进程内 store 重启即清，暂无影响）。
3. **adapted_module 文本未做 AI 微调**：pick 端点直接存 `adapted_premise` 原文，`extra_edits` 字段预留但未拼入（Task 6 通用 sushi 重构后再接入 AI 微调）。
4. **pdf5 实际改编依赖 LLM**：本次仅做代码 + UI + 链路验证，未实际上传 PDF 触发 AI 改编（避免消耗 token + 时间）；后台任务 `_work` 函数逻辑抄自 jobs_routes._start_pdf5，flavor 列表 / ThreadPoolExecutor / append_sub_result 均一致。
