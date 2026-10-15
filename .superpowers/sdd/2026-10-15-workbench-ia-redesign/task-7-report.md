# Task 7 执行报告：EmbodyView 魂穿 4 步 + CocSimView 标签修正

- commit: `f717026`
- 消息: `feat(fe+be): EmbodyView 魂穿4步（选身体/切入点/立场/5版唐迟大纲），修正跑团三标签为克苏鲁基调`

## 改动文件清单

| 文件 | 改动说明 |
| --- | --- |
| `backend/app/api/simulate_routes.py` | 3 条跑团标签改为「全员殒落线/一人独活线/真相污染线」；mock one_liner 改为克苏鲁基调文案 |
| `backend/app/api/jobs_routes.py` | 同步修正 _start_coc 里的 labels 数组（避免 scaffold 阶段旧标签残留） |
| `backend/app/book/volume.py` | ① PLANNING_FIELDS 加入 `host_character/entry_point/info_gap/stance/tangchi_story_outline`；② 修正字段类型：`info_gap: str \| None`、`stance: dict \| None`、`tangchi_story_outline: dict \| None`（与原 Volume dataclass 默认值一致） |
| `backend/app/api/main.py` | ① `VolumeUpdateIn` 增加 5 个对应字段（dict/str 类型）；② 末尾 import 并 `register_embody_routes(...)` |
| `backend/app/api/embody_routes.py` | **新建**：7 个端点（candidates/entry-points/stances/generate-outline/jobs/stream/pick-outline），含 mock 短路、JobStore+ThreadPoolExecutor 并行、SSE 推送、`_OUTLINE5_SYSTEM` 拷贝并追加魂穿四要素硬约束、5 版 pacing seeds 克苏鲁基调、`outline_to_text()` 落盘辅助 |
| `backend/app/jobs/store.py` | `JOB_KINDS` 增加 `embody_outline5` |
| `frontend/src/api.ts` | 新增 `embodyApi`：candidates/entryPoints/stances/generateOutline/listJobs/subscribeJobs/pickOutline（复用 adaptApi.getVolume/patchVolume）+ 4 个 interface（EmbodyCandidate/EntryPoint/Stance/Outline） |
| `frontend/src/views/CocSimView.vue` | `FROM_OPTIONS`/副标题/加载占位 3 处硬编码标签全部改为「全员殒落线/一人独活线/真相污染线」 |
| `frontend/src/views/EmbodyView.vue` | **新建**：4 步 el-steps（选身体→选切入点→选立场→生成大纲），SushiCards ensemble 模式，第三步含 user_directive 输入框，SSE 进度条，完成态显示魂穿对象+选定大纲+「下一阶段：副本详设」按钮+重设按钮，scoped CSS 克苏鲁暗色 |
| `frontend/src/views/HomeView.vue` | import EmbodyView 并增加 `phase==='embody'` 分支 |

## 验证结果

### 1. 静态检查
- `npx vue-tsc --noEmit`：零错误（exit 0）
- `from app.api.embody_routes import register_embody_routes`：import ok
- Dev servers：前端 `http://localhost:5173`（Vite v6.4.4）、后端 `http://127.0.0.1:8321`（uvicorn --reload）均已运行并保持运行

### 2. 后端 API 端到端（mock 路径）
通过 `test_api.py` 脚本完整跑通 8 步链路：

```
[phase] {'phase': 'embody', 'volume_no': 1, ...}
[candidates] dt=1.56s count=3
  - linwei: 林薇 (倒计时型)
  - han: 韩医生 (身份诡计型)
  - laochen: 老陈 (SAN 崩盘型)
[entry-points for 林薇] dt=0.82s count=3
  - lw_pre_fall: 坠楼前 10 秒 @ Day3 夜 23:41
  - lw_morning_after: Day4 清晨醒来（诈尸）@ Day4 早 06:13
  - lw_final_night: Day7 最后一夜·钟楼顶 @ Day7 夜 23:55
[stances] dt=0.81s count=3
  - save_all: 能救一个是一个·救场者 (利他/燃向/悲壮)
  - lone_wolf: 自己先活下来·独狼 (利己/冷硬/黑暗)
  - truth_seeker: 死也要知道真相·追真相者 (理性/癫狂/揭秘)
[generate-outline] dt=0.08s job_id=...
  job status=done progress=100 subs=5 msg=完成 5 版大纲（mock）
    #0: 林薇的第十三次坠落 [步步紧逼·每夜都死人]
    #1: 披着林薇皮的人 [伪装求生·身边都是怪物]
    #2: 第七夜值班日志 [记忆污染·分不清自己是谁]
    #3: 她摔下去之后 [逃不掉的轮回·死亡只是开始]
    #4: 门卫室的绳圈 [最后一天反转·幸存者即祭品]
[pick-outline] ok=True stage_completed.embody=True
[phase-after] {'phase': 'scaffold', ...}

=== ALL API TESTS PASSED ===
```

### 3. 前端浏览器 e2e 预期
- phase 返回 `embody` 时 HomeView 渲染 EmbodyView，Step0 显示 4 步 el-steps 头部
- 挂载 1.5s 后出现 3 张 SushiCards：林薇（即将坠楼/倒计时型）/ 韩医生（已被替换/身份诡计型）/ 老陈（SAN 见底/SAN 崩盘型）
- 选林薇 → 0.8s 出 3 张切入点卡 → 选时机 → 0.8s 出 3 张立场卡 → 输入额外要求 → 点「并行生成 5 版唐迟七日大纲」
- 2s 后进度条到 100%，显示 5 张大纲卡：步步紧逼·每夜都死人 / 伪装求生·身边都是怪物 / 记忆污染·分不清自己是谁 / 逃不掉的轮回·死亡只是开始 / 最后一天反转·幸存者即祭品
- 选定一版→落盘→完成态显示「魂穿角色：林薇」+ 大纲预览 +「下一阶段：副本详设（scaffold）」按钮→点按钮触发 loadPhase，phase 变 scaffold（HomeView 会渲染 scaffold 占位视图）
- 同时回到 simulate 阶段时，3 张跑团卡标签文案已为「全员殒落线 / 一人独活线 / 真相污染线」（可在 CocSimView 的 FROM_OPTIONS 切换、副标题、加载占位看到）

### 4. 测试数据清理
- 测试卷 1、8 张 book 卡、seed_test.py 已在 commit 前清理干净，工作区只保留本次代码改动
- 后端/前端 dev server 保持运行（PID 分别为 uvicorn reloader + vite）

## 创意硬约束落实说明

1. **跑团三标签克苏鲁基调**：旧标签「同组PL·好运线/同组PL·厄运线/异组PL·反转线」已全部移除（simulate_routes、jobs_routes、CocSimView 三处），改为「全员殒落线（无人生还·SAN 集体崩盘）/ 一人独活线（同伴逐一死亡替换）/ 真相污染线（知道得越多被吞得越深）」；mock one_liner 完全重写，去掉「好运」等爽文感用词。
2. **魂穿候选 3 人**严格遵循「倒霉到骨子里」：
   - 林薇——倒计时型，已翻过天台护栏只剩半寸指尖，10 秒内必死
   - 韩医生——身份诡计型，三天前已死在解剖室冰柜，披着人皮的「它」自己还不知道，左手无名指开始蜕皮
   - 老陈——SAN 崩盘型，SAN 剩 4/99，正踩在椅子上把脖子套进绳圈
3. **5 版 pacing seeds** 全部克苏鲁基调：步步紧逼·每夜都死人 / 伪装求生·身边都是怪物 / 记忆污染·分不清自己是谁 / 逃不掉的轮回·死亡只是开始 / 最后一天反转·幸存者即祭品
4. mock 切入点按 3 个角色分别写了 3 个时间锚点（不是复用同一套）；mock 立场写了 3 种典型克苏鲁求生姿态（救场者/独狼/追真相者），各自带「代价」与「倾向」标签。

## 遗留点（可在下个 Task 迭代）

- 真实 LLM 路径（非 mock）下的 candidates/entry-points/stances 生成目前复用 mock 数据直接返回，需要在后续版本接入 `complete_structured` 做副本文本抽取；outline 生成的真实 LLM 路径已按 jobs_routes._start_outline5 模式实现，pacing_seeds 与魂穿约束 prompt 已就绪，等 provider 可用即可端到端生效。
- EmbodyView 完成态的「下一阶段」按钮当前跳 scaffold 占位（HomeView 走 placeholder-view 分支），待 Task 8 实现 ScaffoldView。
- `app.book.volume.Volume` 字段类型 `info_gap: str` / `stance: dict` / `tangchi_story_outline: dict` 在本 Task 修正为与 PLANNING_FIELDS、VolumeUpdateIn、pick-outline 落盘一致；若其他旧代码有依赖旧类型（dict/str/str），后续会在运行时暴露（当前无发现）。
