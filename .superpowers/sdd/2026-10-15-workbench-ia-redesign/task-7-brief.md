# Task 7 Brief：EmbodyView（魂穿 4 步）+ CocSimView 标签修正

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

Dev server 应在跑（5173/8321）。不在跑则启动（`cd backend; .\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload --port 8321` 和 `cd frontend; npm run dev`），**不要重启在跑的**。

## 创意约束（重要，用户亲自指出）
1. **克苏鲁底色**：没有"好运"，所有人都在挣扎求生。3 场跑团的标签必须改成：
   - `全员殒落线`（原"同组PL·好运线"——无人生还，SAN 集体崩盘）
   - `一人独活线`（原"同组PL·厄运线"——活下一个带疯了的人）
   - `真相污染线`（原"异组PL·反转线"——有人看到真相变成了怪物的一部分）
   在 CocSimView 和后端 simulate_routes mock labels 里统一改。
2. **魂穿对象不能是好运主角**：唐迟是被奈亚扔来看戏的，必须魂穿到**将死/已死/被诅咒/被卷进事件无法脱身**的倒霉角色身上（开场就死的调查员、中了诅咒的 NPC、被替换的尸体、SAN 归零的疯人）。EmbodyView 第一步推荐的候选必须符合这个基调；后端 LLM prompt 也要强调"推荐最惨/最绝望/最具挣扎张力的角色"。

---

## 后端改动

### 1. 修正 CocSimView/Simulate routes 的 3 场标签
**文件**：`backend/app/api/simulate_routes.py`
- 找到 labels 数组（`["同组PL·好运线", "同组PL·厄运线", "异组PL·反转线"]`）改为：
  ```python
  labels = ["全员殒落线", "一人独活线", "真相污染线"]
  ```
- mock 短路里的 label 和 one_liner 也相应改：
  - "全员殒落线" / "测试1线 one-liner" → "无人生还，SAN 集体崩盘的绝望团"
  - "一人独活线" → "只剩一个带疯的人带出真相碎片"
  - "真相污染线" → "有人窥见真相后变成了它的一部分"

**文件**：`frontend/src/views/CocSimView.vue`
- 如果前端也硬编码了 labels，同步改为新的三个（找一下"好运线/厄运线/反转线"等字样，改掉）

### 2. 新建 `backend/app/api/embody_routes.py`
函数式注册，导出 `register_embody_routes(app, *, require_project, volumes_for, chore_provider)`

端点（全部 `/api/projects/{pid}/workbench/volumes/{vol_no}/embody/...`）：

#### 2a. GET `/candidates`（推荐魂穿候选角色）
- 校验 v.simulated_run 存在（否则 422 "请先完成跑团仿真"）
- 用 chore_provider("embody") 调 LLM，基于 v.adapted_module + v.simulated_run.full_log 抽取 3-5 个魂穿候选。
- **Mock 短路**：如果 adapted_module 以 "测试副本核文本" 开头（测试数据特征），直接 sleep(1.5) 返回 3 个 mock 候选：
  ```json
  [
    {"id": "c1", "name": "林薇（小报记者·第三日死者）", "role": "调查员", "death_day": 3, "death_cause": "在旧档案室看到不该看的照片，SAN 归零跳楼", "struggle_hook": "死前已经发现了医院夜晚的秘密，但没来得及告诉任何人；唐迟穿来正好卡在她跳楼前 10 分钟", "body_state": "即将坠楼", "info_gap_hint": "她死前拍下的最后一张照片在相机里，唐迟要在奈亚收走前找到"},
    {"id": "c2", "name": "韩医生（精神科主治·被替换者）", "role": "NPC/关键嫌疑人", "death_day": null, "death_cause": null, "struggle_hook": "真正的韩医生在第二日深夜被替换，现在这个是怪物伪装的；唐迟穿来后发现自己的身体已经三天没睡过觉，手腕上有自己不记得的伤疤", "body_state": "已被替换·自己不知道", "info_gap_hint": "唐迟能察觉身体记忆里有不属于自己的动作，但分不清哪段是韩医生哪段是怪物"},
    {"id": "c3", "name": "老陈（夜班护工·第七日幸存者）", "role": "调查员", "death_day": 7, "death_cause": "活过了七天但在第七夜看到了'它'，出院第二天在蓝星家中以相同姿势上吊", "struggle_hook": "他以为自己逃掉了，但奈亚的标记已经打在他身上；唐迟穿来是他出院前的最后一夜", "body_state": "SAN 见底·即将自缢", "info_gap_hint": "老陈口袋里有一张写满乱码的纸条，是他在清醒时刻下的破局关键"}
  ]
  ```
- 真实 LLM 路径（非测试）：
  - system prompt：你是《第七日的亡者》的魂穿设计助手。基于副本核文本和选定跑团记录，推荐 3-5 个最适合唐迟魂穿的角色。
  - 核心规则：
    * 克苏鲁底色，没有"好运"
    * 魂穿对象必须是将死/已死/被诅咒/被替换/SAN 归零/被卷进事件无法脱身的角色，**禁止推荐健康活跃的主角**
    * 每个角色要附 struggle_hook（唐迟穿来为什么有戏）、info_gap_hint（信息差在哪里制造悬念）
    * 优先推荐"死得最惨但留了最多线索"的角色
  - 输出严格 JSON 数组，字段：id/name/role/death_day/death_cause/struggle_hook/body_state/info_gap_hint
  - 用 complete_structured 包一下（参考 jobs_routes outline5 的写法），失败时返回 3 个兜底（用上面的 mock 变种）。
- 返回 `{"candidates": [...]}`

这个端点不走 JobStore/SSE，因为只有一次 LLM 调用、2-5 秒，前端用 await 即可（加 loading）。

#### 2b. POST `/entry-points`（给定魂穿角色，推荐 3 个魂穿时机/切入点）
请求体：`{"candidate_id": str, "candidate": {...}}`（传 candidate 完整对象，后端再调用 LLM）
- 返回 `{"entry_points": [{"id":..., "timing":..., "tension":..., "why_good":...}]}`（3 个切入点，例如"跳楼前 10 分钟"/"刚被替换的第一夜"/"看到真相的那一秒"）
- Mock 短路：candidate.name 含"林薇"返回 3 个时机 ["跳楼前10分钟"/"档案室锁门前"/"相机快门按下的瞬间"]；其他返回通用 3 个
- 不走 JobStore

#### 2c. POST `/stances`（给定角色+时机，推荐 3 种初始立场/求生姿态）
请求体：`{"candidate": {...}, "entry_point": {...}}`
- 返回 `{"stances": [{"id":..., "label":..., "description":...}]}`
- 立场举例："伪装不知道，先活着"/"主动调查，找真相"/"逃跑，但每次都被拉回来"/"装疯，混入怪物中间"/"献祭别人保自己"——必须都是**挣扎求生**姿态，没有"轻松通关"
- Mock 短路返回 3 个

#### 2d. POST `/generate-outline`（前 3 步确定后，5 版并行生成 tangchi_story_outline）
请求体：`{"candidate": {...}, "entry_point": {...}, "stance": {...}, "user_directive": str = "", "n": int = 5}`
- 走 JobStore，session_id=`embody:{pid}:{vol_no}`，kind="embody_outline5"
- 参考 jobs_routes._start_outline5 的 ThreadPoolExecutor 并行逻辑，跑 n=5 版唐迟故事大纲
- System prompt 复用 _OUTLINE5_SYSTEM（从 jobs_routes 抄一份到本文件 top-level 常量，或者直接把 jobs_routes 里的常量 import——**直接抄一份到本文件，不要 import jobs_routes 私有符号**），在末尾加：
  * 魂穿角色：{candidate.name}（{candidate.body_state}）
  * 切入点：{entry_point.timing}
  * 初始立场：{stance.label}
  * 唐迟是被奈亚扔进来的旁观者/求生者，不是天命主角
  * 七日死期要清晰（Day1-7），每天唐迟的信息差变化要体现
- 5 个 pacing_seeds 用克苏鲁基调："步步紧逼·每夜都死人"/"伪装求生·身边都是怪物"/"记忆污染·分不清自己是谁"/"逃不掉的轮回·死亡只是开始"/"最后一天反转·幸存者即祭品"
- 返回 `{"ok": True, "job": job.as_dict(), "job_id": job.job_id}`
- SSE / jobs list 端点（2e, 2f）：

#### 2e. GET `/jobs` → `{"jobs": [job.as_dict()...]}`（session_id=`embody:{pid}:{vol_no}`）
#### 2f. GET `/stream` → SSE（和 adapt/simulate 风格一致，3 轮 idle 断开）

#### 2g. POST `/pick-outline`（选定一版 tangchi_story_outline，落锤 embody 阶段）
请求体：`{"job_id": str, "pick_index": int, "candidate": {...}, "entry_point": {...}, "stance": {...}}`
- 校验 job done，outline = outlines[pick_index]
- `v.host_character = body.candidate`（dict）
- `v.entry_point = body.entry_point`（dict）
- `v.info_gap = {"hint": body.candidate.get("info_gap_hint", "")}`（dict）
- `v.stance = body.stance.get("label", "")`（str）
- `v.tangchi_story_outline = outline_to_text(outline)` （把 outline dict 拼成可读文本字符串：title/one_liner/pacing/tangchi_entry/Day1..Day7/climax/blue_star_hook）
- `v.stage_completed["embody"] = True`
- `vs.save(v)`
- 返回 `{"ok": True, "volume": v.as_dict()}`

outline_to_text 辅助函数：
```python
def outline_to_text(o: dict) -> str:
    lines = []
    if o.get('title'): lines.append(f"【{o['title']}】")
    if o.get('one_liner'): lines.append(o['one_liner'])
    if o.get('pacing'): lines.append(f"节奏：{o['pacing']}")
    if o.get('tangchi_entry'): lines.append(f"\n唐迟切入点：{o['tangchi_entry']}")
    db = o.get('day_breakdown') or []
    for d in db:
        if isinstance(d, dict):
            lines.append(f"\nDay{d.get('day','?')}：{d.get('summary','')}")
        else:
            lines.append(str(d))
    if o.get('climax'): lines.append(f"\n高潮：{o['climax']}")
    if o.get('blue_star_hook'): lines.append(f"\n蓝星小钩子：{o['blue_star_hook']}")
    return "\n".join(lines)
```

### 3. PLANNING_FIELDS 支持新字段
**文件**：`backend/app/book/volume.py`
- PLANNING_FIELDS 加 `"host_character"`, `"entry_point"`, `"info_gap"`, `"stance"`, `"tangchi_story_outline"`
- 这些字段都已有默认值 None/""，from_dict 里有 .get()，直接加进 PLANNING_FIELDS 即可让 PATCH /volumes/{vol_no} 支持修改（虽然本任务主要用 pick-outline 端点写）

**文件**：`backend/app/api/main.py`
- VolumeUpdateIn 加对应字段（dict | None / str | None）

### 4. 注册路由
**文件**：`backend/app/api/main.py` 末尾：
```python
from .embody_routes import register_embody_routes
register_embody_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    chore_provider=_chore_provider,
)
```

### 5. phase_routes 无需改动
stage_order 下一步就是 embody，simulate=true 自动走 embody。

---

## 前端改动

### 6. 新建 `frontend/src/views/EmbodyView.vue`

4 步向导（el-steps）：

#### 6a. 顶部
- 标题"👻 魂穿设计"
- 副标题"奈亚把唐迟扔进副本里——不是当主角，是来求生的。选一个最绝望、最有挣扎张力的身体。"
- el-steps :active="step" finish-status="success"（4 步：选身体 / 选切入点 / 选立场 / 生成大纲）
- 如果 v.host_character && v.tangchi_story_outline 已存在（embody 完成），显示"✓ 唐迟已穿入：{host_character.name}" + 大纲预览 + "下一阶段：副本详设"按钮 + "重设"按钮（清空 4 字段 + tangchi_story_outline 回到 step=0）

#### 6b. Step 0：选身体
- 进入页面时自动 GET `/embody/candidates`（loading 态），拿到 candidates 列表
- 用 `<SushiCards mode="ensemble">` 展示 3-5 个候选角色：
  - item-title-field="name"
  - item-subtitle-field="body_state"（"即将坠楼"/"已被替换·自己不知道"/"SAN见底·即将自缢"）
  - item-content-field="struggle_hook"
  - item-badge-field="role"
  - #item 插槽展示 death_day / death_cause / info_gap_hint
- 选中后（pickedId=candidate.id），"下一步"按钮激活
- 点"下一步"：存 candidate 到 state，step=1

#### 6c. Step 1：选切入点
- 切到 step=1 时自动 POST `/embody/entry-points`（loading）
- SushiCards ensemble 展示 3 个时机（timing 作 title，tension 作 subtitle，why_good 作 content）
- 选定后"下一步"激活，step=2

#### 6d. Step 2：选立场
- 切到 step=2 时自动 POST `/embody/stances`（loading）
- SushiCards ensemble 3 个 stance（label 作 title，description 作 content，无 badge）
- 下方 user_directive 输入框（"额外要求：例如要更冷硬派/加黑色幽默/唐迟保留更多蓝星程序员技能"）
- 选定立场后"🎲 生成 5 版唐迟大纲"按钮激活

#### 6e. Step 3：生成 + 选大纲
- 点按钮 POST `/embody/generate-outline`，订阅 SSE（embodyApi.subscribeJobs）
- 进度条+3 版到 5 版完成进度（和 adapt/simulate 一致）
- 完成后 SushiCards ensemble 展示 5 版大纲：
  - item-title-field="title"
  - item-subtitle-field="pacing"
  - item-content-field="one_liner"
  - item-badge-field="flavor"
  - #item 插槽：显示 tangchi_entry 段落（绿底左边框）+ day_breakdown 前 3 天（折叠更多）+ climax 预览
- "就定这版唐迟故事线"按钮 → POST pick-outline
- 成功后显示完成态，"下一阶段：副本详设"→ emit('done') 让 HomeView reloadPhase

#### 6f. state
```ts
const step = ref(0)
const candidate = ref<any>(null)
const entryPoint = ref<any>(null)
const stance = ref<any>(null)
const userDirective = ref('')
const outlineJob = ref<any>(null)
const outlines = ref<any[]>([])
const pickedOutlineId = ref<string|null>(null)
```

### 7. api.ts 扩展
新增 `embodyApi`：
- `getVolume(pid, volNo)` 复用 adaptApi.getVolume（或共用 adaptApi）
- `candidates(pid, volNo)` → GET
- `entryPoints(pid, volNo, candidate)` → POST
- `stances(pid, volNo, candidate, entryPoint)` → POST
- `generateOutline(pid, volNo, body)` → POST
- `listJobs(pid, volNo)` → GET
- `subscribeJobs(pid, volNo, onEvent)` → EventSource /stream
- `pickOutline(pid, volNo, body)` → POST pick-outline
- `patchVolume(pid, volNo, body)` 复用 adaptApi.patchVolume（重设时清空 host_character/entry_point/info_gap/stance/tangchi_story_outline 为 null）

### 8. CocSimView 前端标签同步改
**文件**：`frontend/src/views/CocSimView.vue`
- Grep 一下"好运|厄运|反转"字样，把 labels/文案替换成新三标签：
  - "同组PL·好运线" → "全员殒落线"
  - "同组PL·厄运线" → "一人独活线"
  - "异组PL·反转线" → "真相污染线"
- 如果没有硬编码（完全从后端 labels 取），就不用改。

### 9. 注册 EmbodyView 到 HomeView
**文件**：`frontend/src/views/HomeView.vue`
- import EmbodyView from './EmbodyView.vue'
- 加分支：`<EmbodyView v-else-if="phase==='embody'" :vol-no="currentVolumeNo" @done="loadPhase" />`

### 10. 验证
- vue-tsc 零错误
- Python import：`from app.api.embody_routes import register_embody_routes`
- 浏览器验证：
  - 造测试数据（填满 book 8 卡 + 卷1 + adapted_module="测试副本核文本：..." + simulated_run 设为 mock pick-one 结果 {mode:"pick_one", picked_index:0, label:"全员殒落线", one_liner:"...", full_log:"Day1...Day7...", selected_variant:{...}} + stage_completed.adapt=true + stage_completed.simulate=true）→ GET /phase 返回 embody
  - 访问页面看到 EmbodyView Step 0，自动加载 candidates（mock 短路，1.5 秒后出 3 个候选）
  - 走 4 步：选林薇 → 选"跳楼前10分钟"时机 → 选"伪装不知道"立场 → 生成大纲（mock 路径也要短路：如果 candidate.name 含"林薇"或 adapted_module 以"测试副本核文本"开头，sleep(2) 返回 5 个 mock outline，不走 LLM）→ 选大纲 → 完成态出现"下一阶段"按钮
  - 验证完成后清理测试数据
- **mock 短路要在 embody_routes.generate-outline 端点也加**：如果 v.adapted_module 以"测试副本核文本"开头或 body.candidate.name 含"林薇"，直接构造 5 个 mock outline 返回（不调 LLM），方便前端验证流程。每个 mock outline 结构符合 _OUTLINE5_SYSTEM 输出格式（title/one_liner/pacing/tangchi_entry/day_breakdown[{day,summary}]/climax/blue_star_hook/foreshadow_planted）。

### 11. Commit
单一 commit：
```
feat(fe+be): EmbodyView 魂穿4步（选身体/切入点/立场/5版唐迟大纲），修正跑团三标签为克苏鲁基调
```

### 12. 报告
`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-7-report.md`：
- commit hash
- 改动文件
- vue-tsc 输出
- Python import 测试
- 浏览器 e2e（mock 路径走完 4 步→embody 完成→phase 推进到 scaffold）
- 标签修正说明（列出 3 个新标签）
- 遗留点：真实 LLM 路径需要 LLM key/credits，本任务验证走 mock；真实 prompt 在生产环境会抽取垂死角色

---

## 重要提醒
- **3 场跑团标签改名是硬要求**（用户亲自指出"好运线"是反克苏鲁的），必须后端前端同步改
- **魂穿候选必须是将死/已死/被诅咒的倒霉角色**，LLM prompt 里要明确禁止推荐健康活跃主角；mock 候选 3 人分别对应"即将坠楼"/"已被替换"/"SAN 见底自缢"
- 不重启在跑 dev server
- ElementPlus 全量注册，scoped CSS，中文字案自然带克苏鲁味（"魂穿""求生""挣扎""代价""污染""奈亚看戏"）
- 不删旧文件
- 新增的 5 版大纲生成走 JobStore+SSE，和 adapt/simulate 模式一致
- complete_structured 的调用参考 jobs_routes._start_outline5 的 _run_one 写法
- 不确定就 Read 文件
