# Task 2 Brief：章前卡 brief + 杀青端点 + 伏笔检测 + 审稿第8项

## 背景
这是工作台重构的第二个后端任务。Task 1（commit 4dc9bbd）已完成 Volume 数据结构扩展 + 阶段判定 API + 创建卷端点。本任务补充：章前卡（brief）字段、杀青（settle）端点、伏笔自动检测（detector）、审稿第 8 项规则，并废弃旧 9 章硬编码模板桩。

## 项目路径
`C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio`

**在该目录下工作。** backend 启动在 `http://127.0.0.1:8321`，前端在 `http://localhost:5173`。不要重启已运行的 dev server——FastAPI 热重载会自动加载代码变更。

## 提交规范
- 单一 commit，message：`feat(backend): 章前卡 brief 字段、brief/settle 端点、审稿第8项、废弃桩接口`
- commit 后在 `.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-2-report.md` 写验证报告。

---

## 实现要点（按序）

### 1. ChapterWork 增加 `brief` 字段
**文件**：`backend/app/wizard/store.py`

ChapterWork dataclass（约 L202）增加一个字段：
```python
brief: dict | None = None  # 章前5分钟卡：{direction, hooks, carryover, entities_present} 或 AI 3 张方向卡选中结果
```

- 在 `ChapterWork.as_dict()` 中无需额外处理，`asdict` 会自动包含（已确认 scenes 有特殊处理，brief 不需要）。
- 在 `ChapterWork.from_dict()` 的 `cls(...)` 调用中增加：`brief=(d.get("brief") if isinstance(d.get("brief"), dict) else None)`
- 在 DeskStore 增加一个方法（与 `record_review` 风格一致）：
  ```python
  def set_brief(self, no: int, brief: dict | None) -> ChapterWork:
      work = self.ensure(no)
      work.brief = brief if brief else None
      self._flush()
      return work
  ```
- 同时为 DeskStore 增加一个 `update_brief_field(no, **updates)` 方便 PATCH 单个字段，但优先保证 `set_brief` 可用。

### 2. 创建伏笔检测器
**新文件**：`backend/app/settle/detector.py`（目录若不存在则 `__init__.py` 一并创建，`__init__.py` 可以为空文件）

导出一个函数：
```python
def detect_foreshadows(es: EntityStore, desk: DeskStore, field_no: int | None = None) -> dict:
    """
    检测已 planted 但本卷正文未提及/未回收的伏笔，返回：
    {
      "unresolved": [{"id", "name", "summary", "plant_chapter", "due_chapter", "intensity", "mentions_in_volume": int, "status"}],
      "paid_in_volume": [{"id", "name", "payoff_chapter"}],
      "newly_planted_hints": [{"name", "hint"}]  # 预留，本次返回空列表
    }
    plant_chapter / due_chapter / intensity 从 e.state_attrs 读取（ENTITY_ATTRS 里 foreshadow 类型定义了这三个 key）。
    mentions_in_volume：统计该伏笔的 name/summary 关键词在本卷所有 finalized 章的 draft/polish 文本里出现次数（简单子串匹配，大小写不敏感；同时匹配 name 和 summary 的前4字作为关键词）。
    field_no 传入时只统计 field_no 绑定章（chapter.field_no == field_no），否则统计所有 finalized 章。
    status 取值："planted"（未提及）|"mentioned"（出现过但未 paid）|"overdue"（due_chapter 已过仍 planted）。
    判定 overdue：due_chapter 能解析为 int，且 <= max_finalized_chapter_in_volume，且 e.status == "planted"。
    paid_in_volume：本卷中 status 变成 "paid" 的伏笔（即 e.status == "paid" 且 payoff_chapter 在本卷章号范围内；从 e.state_attrs["payoff_chapter"] 读）。
    """
```

注意：
- Entity 对象属性：`e.id`, `e.type`, `e.name`, `e.summary`, `e.status`, `e.state_attrs: dict`, `e.appearances: list[int]`。不要使用 plan 里假设的 `e.planted_chapter`，用 `e.state_attrs.get("plant_chapter")`。
- 章节正文来源：`ch = desk.get(no)`，文本取 `(ch.draft or {}).get("text", "")` 与 `(ch.polish or {}).get("text", "")` 拼接（polish 优先，draft 作为 fallback；如果两者都有，取 polish）。
- 只遍历 `bool(ch.finalized_at)` 为 True 的章。
- mentions_in_volume 用简单的子串匹配即可，不要上正则。

### 3. 创建 settle 路由
**新文件**：`backend/app/api/settle_routes.py`

仿照 `phase_routes.py` 的 dataclass 注入模式（不要用 APIRouter 全局依赖）。导出 `register_settle_routes(app, *, require_project, volumes_for, desk_for, entities_for, book_dir_for)`。

依赖说明：
- `volumes_for(pid) -> VolumeStore`
- `desk_for(pid) -> DeskStore`
- `entities_for(pid) -> EntityStore`
- `book_dir_for(pid) -> Path`（project_dir 即可，命名为 book_dir_for 或 project_dir 都可，签名里保持 project_dir 也行；我们用 `(project_dir / "book")` 取书卡目录——但本任务其实不需要 book_dir，可省略；你自己决定，能拿到 VolumeStore 所在的 project 根目录即可用于给 Volume._flush 用，但 VolumeStore 已经自己管理路径，所以其实只需要前 4 个依赖）
  - **简化**：依赖只需要 `require_project, volumes_for, desk_for, entities_for` 四个。

端点（全部 `/api/projects/{pid}/volumes/{vol_no}/settle/...`）：

#### 3a. POST `/api/projects/{pid}/volumes/{vol_no}/settle/detect`
- 校验：`require_project(pid)`，`vs = volumes_for(pid)`，`v = vs.get(vol_no)`（没有则 404）。
- 调用 `detect_foreshadows(entities_for(pid), desk_for(pid), field_no=vol_no)`。
- 返回 `{"ok": True, "volume_no": vol_no, "foreshadows": <detector 返回 dict>}`。
- 这个端点是 GET 语义也行但用 POST 方便将来扩展（传过滤参数）；用 GET 也可以，建议 GET 更 RESTful。**用 GET**。

#### 3b. POST `/api/projects/{pid}/volumes/{vol_no}/settle/apply`
请求体：
```json
{
  "foreshadow_decisions": [{"id": "foreshadow-0-xxx", "action": "pay"|"drop"|"carry", "note": ""}],
  "contamination_delta": 0,
  "kill_record_line": "",
  "crafts": [...], "echoes": [...], "god_marks": [...], "brought_items": [...], "bonds_delta": [...],
  "field_count_inc": 1
}
```
逻辑：
1. `es = entities_for(pid)`；遍历 foreshadow_decisions，按 action 更新实体：
   - "pay"：`es.update(eid, status="paid")` 并把 `state_attrs["payoff_chapter"] = str(last_chapter_no)`（last_chapter_no 是 desk 绑定到该 field_no 的最大章号；没有则空字符串）。
   - "drop"：`es.update(eid, status="dropped")`。
   - "carry"：不改（保留 planted 带到下一卷）。
2. 把 `crafts/echoes/god_marks/brought_items/bonds_delta/contamination_delta/kill_record_line/field_count_inc` 打包成一个 diff dict，调用 `es.apply_settle_diff(diff, field_no=vol_no)`（EntityStore 里已有这个方法，L798 处，直接调用即可）。
3. 把 foreshadow_decisions 和 apply_settle_diff 的返回摘要存到 `v.settle`（dict），字段：
   ```python
   v.settle = {
     "applied_at": _now(),
     "foreshadow_decisions": body.foreshadow_decisions,
     "diff_summary": summary,  # apply_settle_diff 的返回
     "final_chapter_no": last_chapter_no,
   }
   ```
4. `vs.save(v)`（VolumeStore 公开方法，L276，调用内部 _flush 并更新 index + cache）持久化。不要调用 `vs._flush()` 私有方法——用 `vs.save(v)`。
5. 返回 `{"ok": True, "settle": v.settle}`。

#### 3c. POST `/api/projects/{pid}/volumes/{vol_no}/settle/finalize`
- 校验：settle 必须已存在（`v.settle` 非 None），否则 422。
- 如果当前卷 volume_kind == "dungeon"：
  - 在 v.settle 里加 `"finalized_at": _now()`。
  - 设置 `v.stage_completed["settle"] = True`（stage_completed 字典里加这个 key）。
  - 不自动创建 interlude 卷（前端 SettleView 完成后会跳转到 interlude 入口，用户点"回到蓝星"再创建间章卷——这是 Task 11/12 前端任务，本端点只标记 settle 完成）。
  - 设计调整：**杀青 finalize 后，下一步是间章（interlude phase）还是直接新卷（adapt_empty）？** 按设计流程：副本杀青 → 蓝星间章（用户选择是否写）→ 新副本。如果用户选择不写间章直接开新卷，前端会调用 workbench/volumes 创建新 dungeon 卷。所以 finalize 只标记本卷 settle 完成即可，不自动创建下一卷。
- 如果 volume_kind == "interlude"：
  - 设置 `v.interlude_plan = v.settle`（把 settle 作为 interlude_plan 复用——或者直接在 v.settle 里加 finalized_at，让 phase 判定逻辑识别间章已完成。注意 phase_routes.py 里当前 interlude 完成判据是 `last_vol.interlude_plan` 为真值。请让 interlude finalize 时同时写 `v.interlude_plan = {"finalized_at": _now(), "settle": v.settle}`，这样 phase 判定会因为 interlude_plan 真值而跳出 interlude 阶段，进入下一个 dungeon 卷的 adapt_empty（因为没有新 dungeon 卷创建，所以 adapt_empty 是对的——前端可引导用户创建新 dungeon 卷）。
- `vs.save(v)` 持久化。
- 返回 `{"ok": True, "volume": v.as_dict()}`。

### 4. 创建 brief 路由
**新文件**：`backend/app/api/brief_routes.py`

同样用 register 函数模式。导出 `register_brief_routes(app, *, require_project, volumes_for, desk_for)`。

端点：

#### 4a. PATCH `/api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief`
请求体：`{...briefFields}`（任意 brief 字段 JSON 对象）。
逻辑：
- 校验 pid、cno 存在。
- `desk = desk_for(pid)`，`work = desk.ensure(cno)`。
- 若 `work.brief` 是 None 或非 dict，初始化为空 dict。
- `work.brief.update(body)`（body 为 dict，直接 merge）。
- `desk._flush()`。
- 返回 `{"ok": True, "brief": work.brief}`。
- 同时把 work.brief 同步给 field_no：`work.field_no = vol_no`（如果原来为 None 就设置上；已设置则保留）。

#### 4b. POST `/api/projects/{pid}/volumes/{vol_no}/next-chapter-brief`
**桩实现**（标记建设中）。
请求体：`{"prev_chapter_no": int}`（可选）。
返回：
```json
{
  "status": "construction",
  "message": "章前三张方向卡将在后续任务接入 LLM，当前为桩",
  "options": [
    {"id": "a", "title": "顺承推进", "summary": "沿上一章钩子继续"},
    {"id": "b", "title": "视角切换", "summary": "切到 NPC 视角制造悬念"},
    {"id": "c", "title": "时间跳跃", "summary": "跳过几个小时，直接到冲突点"}
  ]
}
```
不调用 LLM，不写 session。纯静态桩，供前端占位。

### 5. 审稿第 8 项 rule_reveal_pacing
**文件**：`backend/app/review/reviewer.py`

修改 REVIEW_SYSTEM 常量：把"七项"改成"八项"，新增第八项 `"信息揭露节奏"`，检查项说明：`NPC 对白是否出现大段世界观/真相倾倒（info dump），单段对白超过 150 字且连续解释设定视为不稳。`

修改 ITEM_NAMES 常量增加 `"信息揭露节奏"`。

其他 normalize / verdict 逻辑会自动适配（_normalize 按 ITEM_NAMES 顺序遍历，缺项自动补不稳）。BLOCK_THRESHOLD 保持 2。

### 6. 注册新路由
**文件**：`backend/app/api/main.py`

在文件末尾（Task 1 加的 register_phase_routes 调用之后）加：
```python
from .settle_routes import register_settle_routes
from .brief_routes import register_brief_routes
register_settle_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
    entities_for=_entities_for,
)
register_brief_routes(
    app,
    require_project=_require_project,
    volumes_for=_volumes_for,
    desk_for=_desk_for,
)
```

### 7. 废弃旧桩
**文件**：`backend/app/api/main.py`

- L3531-3567 的 `plan_chapters` action：**彻底替换**为返回 deprecated 响应，不再生成 9 章硬编码模板。替换为：
  ```python
  if body.action == "plan_chapters":
      return {"status": "deprecated", "message": "plan_chapters 已废弃，章前5分钟（brief）替代一次性拆章；请通过 /workbench 新流程逐章生成 brief"}
  ```
  不再写 session，不再 s.add_message。
- L3505-3518 `daily_summon`：改为返回 `{"status": "construction", ...}` 形式，和 next-chapter-brief 一致；不写 session。
- L3520-3529 `walkthrough_step`：同样改为 `{"status": "construction", "message": "走查朗读建设中"}`，不写 session。

注意：这三个桩在替换后，要确认不再依赖 s/ss 变量——直接 return 即可，不要引用 s。

### 8. 更新 phase_routes.py 的 brief 判定
**文件**：`backend/app/api/phase_routes.py`

Task 1 里 _determine_phase 对 brief 的判断用了 `getattr(ch, "brief", None)` 防御。现在 brief 字段真实存在了，更新那段逻辑：

第 5 步（日更 desk）改为：
```python
# 5. 日更：存在未定稿章
unfinished = [no for no in all_chapter_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
if unfinished:
    earliest = min(unfinished)
    max_ch = max(all_chapter_nos) if all_chapter_nos else 0
    ch = desk.get(earliest)
    # 章前5分钟卡：未定稿章没有 brief → 先到 briefing 阶段
    if ch and not (ch.brief and isinstance(ch.brief, dict) and ch.brief.get("selected")):
        # 没有选好方向卡 → briefing 阶段
        return {
            "phase": "briefing",
            "volume_no": last_vol.volume_no,
            "volume_kind": last_vol.volume_kind,
            "chapter_no": earliest,
            "is_last_chapter": (earliest == max_ch),
        }
    return {
        "phase": "desk",
        ...
    }
```
说明：brief 里 `selected: true` 表示作者已选定方向卡并填好章前卡，这时才进入 desk；否则停在 briefing（章前5分钟）让作者选方向。interlude 卷的章直接进 desk（不经过章前5分钟），因为间章短，节奏走另一套。如果 ch.is_interlude 为 True，跳过 brief 检查直接进 desk。

调整后请仔细阅读整个 _determine_phase，确保 phase 优先级顺序符合：setup → adapt_empty → settle（last_vol 所有章 finalized 且 settle 为 None）→ interlude desk/briefing/interlude plan → dungeon desk（有 brief.selected）/ briefing（无 brief.selected）→ stage_completed 查找 → briefing（兜底）。

---

## 验证步骤

1. 启动 Python 导入测试：
   ```powershell
   cd C:\Users\ZS\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac86b5c085f1c53d1b31b49\novel-studio
   python -c "from backend.app.wizard.store import ChapterWork, DeskStore; from backend.app.settle.detector import detect_foreshadows; from backend.app.api.settle_routes import register_settle_routes; from backend.app.api.brief_routes import register_brief_routes; from backend.app.review.reviewer import ITEM_NAMES; print('imports ok, items=', ITEM_NAMES)"
   ```
   预期输出：`imports ok, items= ['剧情逻辑', '人物目标', '情绪关系', '身体信息', '场景转场', '章末承接', '跨章联结', '信息揭露节奏']`

2. ChapterWork 序列化测试：
   ```powershell
   python -c "from backend.app.wizard.store import ChapterWork; import json; c=ChapterWork(no=1); c.brief={'direction':'顺承','selected':True}; d=c.as_dict(); print('brief in as_dict:', d.get('brief')); c2=ChapterWork.from_dict({'no':2}); print('default brief:', c2.brief); c3=ChapterWork.from_dict({'no':3,'brief':{'selected':True}}); print('loaded brief:', c3.brief)"
   ```

3. 用 curl 或 Python 测试 API 端点（uvicorn 应该已在 8321 运行）：
   ```powershell
   # GET phase（旧项目 20261014-c6c432 应该还是 setup，因为 Task 1 验证过）
   Invoke-RestMethod http://127.0.0.1:8321/api/projects/20261014-c6c432/phase
   # deprecated plan_chapters
   Invoke-RestMethod -Method POST -Uri http://127.0.0.1:8321/api/projects/20261014-c6c432/sessions/test -ContentType 'application/json' -Body '{"action":"plan_chapters"}'
   # 桩端点 next-chapter-brief（先创建一个卷和章才能测；可以跳过，因为会 404/422，主要看代码不报错）
   ```

4. detector 基础测试（构造内存对象验证不崩溃）：
   ```powershell
   python -c "
   from backend.app.entities.store import EntityStore
   from backend.app.wizard.store import DeskStore, ChapterWork
   from backend.app.settle.detector import detect_foreshadows
   import tempfile, pathlib, json
   tmp = pathlib.Path(tempfile.mkdtemp())
   es = EntityStore(tmp/'entities.json')
   es.add('foreshadow','红伞','神秘的红伞', chapter=1)
   desk = DeskStore(tmp/'desk.json')
   ch = desk.ensure(1,'第一章'); ch.field_no=1
   ch.draft={'text':'唐迟打着一把红伞走在街上。','run_id':'','updated_at':''}
   ch.finalized_at='2026-10-15'
   desk._flush()
   r = detect_foreshadows(es, desk, field_no=1)
   print('detect ok:', 'unresolved count:', len(r['unresolved']), [(f['name'], f['status']) for f in r['unresolved']])
   "
   ```

## 交付物

1. 修改的文件：
   - `backend/app/wizard/store.py`（ChapterWork.brief + DeskStore.set_brief）
   - `backend/app/review/reviewer.py`（第八项）
   - `backend/app/api/phase_routes.py`（brief 判定逻辑更新）
   - `backend/app/api/main.py`（注册新路由 + 废弃三个桩）
2. 新文件：
   - `backend/app/settle/__init__.py`（空）
   - `backend/app/settle/detector.py`
   - `backend/app/api/settle_routes.py`
   - `backend/app/api/brief_routes.py`
3. 报告：`.superpowers/sdd/2026-10-15-workbench-ia-redesign/task-2-report.md`（含验证命令输出、commit hash）
4. 单一 git commit，message 按规范。

## 注意

- 不要动 Task 1 已经提交的 volume.py / phase_routes.py 中无关部分。
- 不要重启 dev server；改完直接测。如果 uvicorn 因为导入错误崩溃了，你可以重启：`cd backend; python -m uvicorn app.api.main:app --reload --port 8321`（先确认没有老进程占用，一般不杀，让热重载接管）。
- 不要删/改其他不相关文件。
- 不要使用 pdb/breakpoint。
- 所有中文文案保持自然，不要机翻腔。
- 如果遇到不确定的 API（如 VolumeStore 的 flush 方法名），Read 对应文件确认，不要猜。
