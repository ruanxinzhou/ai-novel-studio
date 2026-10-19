# Task 4 Brief: REST API（desk_routes + mainline_routes + 统一建卷接口）

## Goal
新建两个 router 文件（desk_routes.py、mainline_routes.py），并在 phase_routes.py 加一个统一建卷接口替代现有的两个零散接口；最后在 main.py 注册。

## Files
- CREATE `backend/app/api/desk_routes.py`
- CREATE `backend/app/api/mainline_routes.py`
- MODIFY `backend/app/api/phase_routes.py`（新增统一 POST /volumes，保留旧两个接口兼容）
- MODIFY `backend/app/api/main.py`（注册新 router，传 _entities_for 依赖）

## Conventions（参照现有 brief_routes.py 的 register_xxx_routes 模式）
- 每个 router 文件用 `register_xxx_routes(app, *, require_project, volumes_for, desk_for, entities_for=None, chore_provider=None)` 工厂函数
- 内部用 `@dataclass class _Deps` 存依赖，全局 `_deps`，`_must_deps()` 获取
- 用 `APIRouter()` + `router.add_api_route(...)` 注册路径（和 phase_routes 一致）
- 错误抛 HTTPException(400/404)

## 1) desk_routes.py

挂载点 prefix：`/api/projects/{pid}/desk` 和 `/api/projects/{pid}/workbench/desk`（spine 接口用前者）。

### Endpoints

**A. POST `/api/projects/{pid}/desk/insert`**
- Body (pydantic):
  ```python
  class _InsertIn(BaseModel):
      after_no: int = 0            # 0=插到最前
      title: str = "新章节"
      field_no: int | None = None
      chapter_kind: str = "dungeon"
      day: int | None = None
  ```
- Logic:
  - require_project(pid)
  - vs = volumes_for(pid); desk = desk_for(pid); es = entities_for(pid)
  - 若 field_no 为 None：
    - 若 chapter_kind=="mainline"：field_no = 第一个 volume_kind=="mainline" 的卷号；若没有 mainline 卷抛 400
    - 若 chapter_kind=="interlude"：field_no = 最新卷的卷号（简化处理，前端会传）
    - 否则（dungeon）：field_no = 最新 dungeon 卷号
  - new_no = desk.insert_after(after_no, title=title, field_no=field_no, chapter_kind=chapter_kind)
  - 如果 chapter_kind=="dungeon" and day is not None: desk.assign_field(new_no, field_no=field_no, day=day)
  - elif chapter_kind=="interlude": desk.assign_field(new_no, field_no=field_no, is_interlude=True)
  - else (mainline): desk.get(new_no).field_no = field_no ; desk.get(new_no).chapter_kind = "mainline" ; desk._flush()
  - entities_for(pid).shift_chapter_refs(after_no+1, +1) if after_no != 0 else entities_for(pid).shift_chapter_refs(1, +1)
  - 注意：desk.insert_after 内部已经 renumber 了 desk 章节号，es.shift_chapter_refs 也必须一致偏移。
  - 返回 {"ok": True, "chapter_no": new_no, "chapter": desk.get(new_no).as_dict()}

**B. DELETE `/api/projects/{pid}/desk/{no}?force=false`**
- Logic:
  - desk.delete(no, force=force)
  - entities_for(pid).shift_chapter_refs(no+1, -1)  # 删后，后续章节号前移
  - desk.renumber(no+1, -1)  # desk 里后续章号前移
  - 返回 {"ok": True}

**C. PATCH `/api/projects/{pid}/desk/{no}`**
- Body: `_PatchIn(title: str|None=None, field_no: int|None=None, chapter_kind: str|None=None, day: int|None=None)`
- Logic:
  - 调 desk.move(no, new_field_no=field_no, new_chapter_kind=chapter_kind, new_day=day) —— 但现有 move 签名可能不支持 new_day；如果不支持就在 patch 里直接改字段：先 desk.ensure(no)，逐个 setattr（title/field_no/chapter_kind/day），注意 chapter_kind 同步 is_interlude，最后 _flush
  - 返回 {"ok": True, "chapter": desk.get(no).as_dict()}

**D. POST `/api/projects/{pid}/desk/reorder`**
- Body: `_ReorderIn(orders: list[dict])`
- 返回 501：`raise HTTPException(501, "下一版本支持拖拽改序")`

**E. GET `/api/projects/{pid}/desk/spine`**
- 返回：
  ```json
  {
    "volumes": [ {volume_no, field_no, title, volume_kind, start_chapter_no, stage_completed}, ... ],
    "chapters": [ {no, title, field_no, chapter_kind, day, is_interlude, finalized_at, payoff, word_count?}, ... ]
  }
  ```
- volumes 来自 vs.all_list()，每个调 .as_dict() 取字段
- chapters 遍历 desk.all_sorted()，每条取 c.as_dict()；额外计算 word_count = len((c.polish.get("text") or c.draft.get("text") or c.plot.get("text") or ""))
- 章列表按 no 升序

### 依赖
register 函数需要：require_project, volumes_for, desk_for, entities_for。

## 2) mainline_routes.py

挂载点：`/api/projects/{pid}/volumes/{vol_no}/mainline-outline`

### Endpoint

**POST `/api/projects/{pid}/volumes/{vol_no}/mainline-outline`**
- Body: `_OutlineIn(directive: str="", chapter_count: int=2)`（chapter_count 默认 2，即蓝星两章）
- Logic:
  - 校验卷存在且 volume_kind=="mainline"，否则 400
  - 组装上下文：
    - 从项目的 book 设定取主角（唐迟）基础信息——读 book_dir（project_dir(pid)/"book"）下 8 张卡的 mc 卡（如果存在），可以简化：从 book_store = book_for(pid) 或直接读 project_dir 下 "book/mc.json" 或 "book/tangchi.json"（看 book_cards 是啥）。如果读不到就给默认：主角唐迟，程序员/理性思维、蓝星普通打工人、不出现编程黑话。
    - 加上 directive（若有）
    - 强调：第1章=蓝星日常+讣告钩子（不穿越），第2章=讣告应验+穿越坠落（睁眼陌生脸喊宿主名）；每章4500字；不许编程黑话。
  - 用 chore_provider("mainline-outline") 调 LLM，system prompt 用下面的 _MAINLINE_OUTLINE_SYSTEM，user 为组装后的上下文。
  - 用 complete_structured 解析，失败则回退 mock（给出 2 章默认大纲）。
  - 返回 JSON：{"ok": True, "outlines": [{chapter:1, title, day:0, summary, hooks}, {chapter:2, title, day:0, summary, hooks}]}
  - 注意：这里 chapter 返回卷内相对号（1,2），不做 start_chapter_no 偏移（偏移在 Task 5 rhythm 阶段做）。

### _MAINLINE_OUTLINE_SYSTEM 内容
```
你是《第七日的亡者》蓝星开篇卷的章纲设计师。本卷共 2 章，day=0（尚未进入副本），主角叫唐迟——一个理性、冷静、善归纳、对规则边界敏感的普通程序员，但他的理性底色体现为"先观察再判断、按逻辑推演因果"，禁止在叙事/心理/章纲里出现任何程序员职业比喻或黑话（如 debug/Bug/栈/日志/变量/指针/越权/溢出/短路/重定向/死锁/卡bug 等）。

【硬约束】
- 第1章（hook / 300字具体动作切入）：蓝星都市日常，从唐迟一个具体动作切入（加班/地铁/外卖/快递/便利店/加班夜宵等生活化场景）；1500字内完成主角亮相（理性/观察力/普通人质感）；3000字铺日常+不对劲（收到一份讣告/死讯/自己的名字出现在讣告栏上/快递到付一个空骨灰盒等合理钩子）；章末讣告送达。严禁穿越。每章目标4500字。
- 第2章（sell）：讣告甩不掉（手机/报纸/同事口耳相传多重应验）+第一桩应验事件发生+自救路径全堵+穿越瞬间（具体感官锚点：冷/湿/咸/锈味/有人喊他一个陌生名字），章末睁眼陌生脸喊宿主名。严禁编程黑话。每章目标4500字。
- 两章节奏：第1章慢热立人，第2章加速坠落。
- 不许出现任何副本/NPC/克苏鲁神明/魂穿宿主姓名——这些在副本卷才出现。
- 钩子必须有后劲：读者看完必须想知道"是谁给他发了讣告""穿越后他变成了谁"。

严格以 JSON 返回，不要输出 JSON 之外的任何文字。返回格式：
{
  "outlines": [
    {"chapter": 1, "title": "……5~10字短标题……", "day": 0, "summary": "……100~150字本章纲要（含开篇动作/主角亮相/不对劲铺陈/章末讣告钩子）……", "hooks": "……20~40字章末钩子……"},
    {"chapter": 2, "title": "……5~10字短标题……", "day": 0, "summary": "……100~150字本章纲要（讣告应验+自救失败+穿越瞬间感官锚点+章末睁眼陌生脸）……", "hooks": "……20~40字章末钩子……"}
  ]
}
```

### 依赖
register 函数需要：require_project, volumes_for, project_dir, chore_provider。（如果 book 信息不好取，简化：从 project_dir/"book" 读 mc.json，如果不存在就用硬编码默认主角信息；不要纠结 book 结构。）

## 3) phase_routes.py 修改

保留现有的 `_create_dungeon_volume` 和 `_create_interlude_volume` 以兼容前端（前端 Task 10 之前不会调新接口，但保留不删）；新增统一接口：

- 新增 pydantic model：
  ```python
  class _CreateVolumeIn(BaseModel):
      kind: str = "dungeon"   # mainline | dungeon | interlude
      title: str = ""
      start_chapter_no: int | None = None
  ```
- 新增路由：`POST /api/projects/{pid}/volumes`
- Handler 逻辑：
  - 校验 kind ∈ {"mainline","dungeon","interlude"}，否则 400
  - vs = volumes_for(pid); desk = desk_for(pid)
  - 若 start_chapter_no is None：start_chapter_no = desk.next_available_no()
  - v = vs.create_new_volume(title=title, volume_kind=kind, start_chapter_no=start_chapter_no)
  - 返回 {"ok": True, "volume_no": v.volume_no, "volume": v.as_dict(), "phase": _determine_phase(pid)}
- 在 register_phase_routes 的 router.add_api_route 里加这条路由（方法 POST，路径 `/api/projects/{pid}/volumes`）

## 4) main.py 修改

- 在 register_phase_routes 调用之后，加：
  ```python
  from .desk_routes import register_desk_routes
  register_desk_routes(
      app,
      require_project=_require_project,
      volumes_for=_volumes_for,
      desk_for=_desk_for,
      entities_for=_entities_for,
  )
  from .mainline_routes import register_mainline_routes
  register_mainline_routes(
      app,
      require_project=_require_project,
      volumes_for=_volumes_for,
      project_dir=_project_dir,
      chore_provider=_chore_provider,
  )
  ```
- 注意：phase_routes.register 的签名目前不需要 entities_for，但新增的 POST /volumes 要算 start_chapter_no=desk.next_available_no()，desk_for 已经在 phase_routes 的 _Deps 里了，不需要新增依赖。

## Workflow
1. 读 phase_routes.py 和现有一个 router 文件（如 brief_routes.py / rhythm_routes.py）确认模式。
2. 建 desk_routes.py，实现 register_desk_routes + 5 个 endpoint。
3. 建 mainline_routes.py，实现 register_mainline_routes + mainline-outline endpoint。system prompt 直接写在文件里（类似 brief_routes._BRIEF_SYSTEM）。mainline-outline 的上下文组装：读 project_dir(pid)/"book" 下所有 .json 文件，拼接 key+前200字给 LLM 作为世界观/主角基础；如果 book 目录不存在就用极简默认。
4. 在 phase_routes.py 加 _CreateVolumeIn + POST /volumes handler + 路由注册。
5. 在 main.py 末尾 register 两个新 router。
6. **不写单测**（Task 4 是胶水层，冒烟验证通过即可——下一个 Task 11 重启后端再 curl 验证）。
7. 语法检查：`cd backend ; python -m py_compile app/api/main.py app/api/desk_routes.py app/api/mainline_routes.py app/api/phase_routes.py` 必须无错。
8. 跑快速导入校验：`cd backend ; python -c "from app.api.main import app; print('routes:', len(app.routes))"` 必须能成功导入（不启动 uvicorn，只是 import 验证注册无错）。
9. 提交：
   - `git add backend/app/api/desk_routes.py backend/app/api/mainline_routes.py backend/app/api/phase_routes.py backend/app/api/main.py`
   - `git commit -m "feat(api): 章节管理 REST + mainline 出章纲 + 统一建卷接口"`

## Important constraints
- desk.insert_after 内部已经 renumber desk，但还需要 es.shift_chapter_refs 同步偏移实体库章号
- 删除章：先 desk.delete，再 es.shift_chapter_refs(no+1, -1)，再 desk.renumber(no+1, -1)
- 所有时间戳格式统一 `YYYYMMDD-HHMMSS`
- desk.move 当前签名 `move(no, *, new_field_no=None, new_chapter_kind=None, new_day=None)` 如果没有 new_day 参数，你需要在 Task 2 基础上加 new_day 支持（在 store.py 里简单加一个 new_day 参数即可，在 move 方法里处理 day 字段和 is_interlude）
- GET /api/projects/{pid}/desk/spine 返回的 volumes 包含 start_chapter_no
- 不要重启后端、不要跑前端
- 不要改其他文件
