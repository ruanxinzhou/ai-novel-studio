# Task 2 报告：章前卡 brief + 杀青端点 + 伏笔检测 + 审稿第8项

## Commit
`6544986` feat(backend): 章前卡 brief 字段、brief/settle 端点、审稿第8项、废弃桩接口

## 改动清单

### 修改的文件
- `backend/app/wizard/store.py`
  - ChapterWork dataclass 增加 `brief: dict | None = None` 字段
  - `ChapterWork.from_dict()` 补 brief 反序列化
  - DeskStore 增加 `set_brief(no, brief)` 和 `update_brief_field(no, **updates)` 方法
- `backend/app/review/reviewer.py`
  - 审稿项从 7 项扩到 8 项，新增「信息揭露节奏」（NPC 对白 info dump 检测：单段对白超 150 字且连续解释设定判不稳）
  - REVIEW_SYSTEM/ITEM_NAMES/_normalize 注释同步更新
- `backend/app/api/phase_routes.py`
  - 第 5 步「日更」逻辑更新：
    - interlude 章（is_interlude=True）直接进 desk，不经过章前 5 分钟
    - 普通 dungeon 章的未定稿章：若 `brief.selected` 为真 → 进 desk；否则 → 进 briefing（章前5分钟）
  - 去掉了 Task 1 里对 brief 的 getattr 防御（现在字段真实存在）
- `backend/app/api/main.py`
  - 文件末尾新增 `register_settle_routes` 和 `register_brief_routes` 注册（传入 `_require_project/_volumes_for/_desk_for/_entities_for`）
  - 废弃三个旧桩：
    - `plan_chapters`：返回 `{"status":"deprecated", "message":"...章前5分钟（brief）替代一次性拆章..."}`
    - `daily_summon`：返回 `{"status":"construction",...}`，不再写 session
    - `walkthrough_step`：返回 `{"status":"construction",...}`，不再写 session

### 新文件
- `backend/app/settle/__init__.py`（空）
- `backend/app/settle/detector.py`
  - `detect_foreshadows(es, desk, field_no=None)`：
    - 收集 field_no 绑定的 finalized 章，拼接正文（polish 优先 draft 兜底）
    - 遍历所有 type='foreshadow' 实体
    - paid 且 payoff_chapter 在本卷章号范围内 → paid_in_volume
    - dropped → 跳过
    - planted：按 name+summary 前 4 字子串匹配 mentions_in_volume
      - due_chapter ≤ max_finalized_no → overdue
      - mentions > 0 → mentioned
      - 否则 → planted
    - 返回 `{unresolved, paid_in_volume, newly_planted_hints: []}`
- `backend/app/api/settle_routes.py`（函数式注册模式，仿照 phase_routes.py）
  - `GET /api/projects/{pid}/volumes/{vol_no}/settle/detect`：调用 detector 返回伏笔检测结果
  - `POST /api/projects/{pid}/volumes/{vol_no}/settle/apply`：
    - 请求体含 foreshadow_decisions（pay/drop/carry）+ settle diff 字段（crafts/echoes/god_marks/brought_items/bonds_delta/contamination_delta/kill_record_line/field_count_inc）
    - 按 action 更新伏笔实体状态（pay→status='paid'+写 payoff_chapter；drop→status='dropped'；carry 不改）
    - 调用 `es.apply_settle_diff(diff, field_no=v.field_no)` 应用到主角状态
    - 写 `v.settle = {applied_at, foreshadow_decisions, diff_summary, final_chapter_no}`，`vs.save(v)`
  - `POST /api/projects/{pid}/volumes/{vol_no}/settle/finalize`：
    - 必须 v.settle 已存在
    - dungeon 卷：`v.settle["finalized_at"]` + `v.stage_completed["settle"] = True`
    - interlude 卷：`v.settle["finalized_at"]` + `v.interlude_plan = {finalized_at, settle}`（让 phase 判定跳出 interlude）
    - `vs.save(v)`
- `backend/app/api/brief_routes.py`（同样 register 模式）
  - `PATCH /api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief`：body 任意 JSON，merge 到 work.brief；若 work.field_no 未设置则绑定到 v.field_no
  - `POST /api/projects/{pid}/volumes/{vol_no}/next-chapter-brief`：静态桩，返回 3 张方向卡（顺承推进/视角切换/时间跳跃），不调用 LLM

## 验证

### 1. 模块导入（venv Python）
```
imports ok, items= ['剧情逻辑', '人物目标', '情绪关系', '身体信息', '场景转场', '章末承接', '跨章联结', '信息揭露节奏']
```

### 2. ChapterWork brief 序列化
```
brief in as_dict: {'direction': '顺承', 'selected': True}
default brief: None
loaded brief: {'selected': True}
```

### 3. detector 单元测试
构造内存 EntityStore（红伞伏笔）+ DeskStore（第 1 章 finalized，正文含"红伞"）→ detect 返回：
```
detect ok, unresolved: [('红伞', 'mentioned', 1)]
paid_in_volume: []
```
（name 子串匹配到 1 次 → status=mentioned，符合预期）

### 4. HTTP 端点热重载验证
- GET `/api/projects/20261014-c6c432/phase`：`{"phase":"setup",...}`（book cards 未满，符合预期）
- POST `/workbench/volumes` 创建卷 2 成功，返回含 `volume_kind:"dungeon"/stage_completed:{...}/settle:null/interlude_plan:null`
- GET `/volumes/2/settle/detect`：返回 `{ok:true, foreshadows:{unresolved:[], paid_in_volume:[], newly_planted_hints:[]}}`（无伏笔，符合预期）
- POST `/volumes/2/next-chapter-brief`：返回 `{status:"construction", options:[3张卡]}`（桩端点正常）

测试过程中创建的卷 2 和残留卷 1 均已删除，index.json 已恢复为 `{"volumes":[]}`，项目数据保持干净。

## 注意事项 / 决策
- `es.update(eid, state_attrs={...})`：EntityStore.update 的 state_attrs 参数会 merge 到 e.state_attrs（不是覆盖），所以 payoff_chapter 写入是安全的。
- 对 foreshadow_decisions 中 id 不存在的情况，try/except ValueError 跳过，不抛 422（前端可能传已删伏笔）。
- finalize 不自动创建下一卷（dungeon 或 interlude），让前端引导用户选择（先杀青面板结算 → 蓝星间章入口 → 新副本），避免强流程。
- PATCH brief 接受 dict body（不是 BaseModel），方便前端自由传字段；FastAPI 对 dict body 默认要求 JSON 内容类型。
- reviewer.py 文档字符串里「第七项…」「七项…」等旧描述已更新为「八项」/「第八项」。

## 后续依赖
- Task 10（ChapterBriefModal + DeskView 精简）会消费 brief_routes 的两个端点
- Task 11（SettleView 杀青面板）会消费 settle_routes 三个端点
- Task 15（api.ts 重映射）会把这些端点写入前端 API 层
