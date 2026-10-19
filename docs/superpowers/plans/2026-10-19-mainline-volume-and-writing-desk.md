# 蓝星开篇卷 + 全局写作台 + 4000-5000字/章 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让《第七日的亡者》第 1-2 章完成蓝星主角亮相+讣告钩子+穿越坠落，第 3 章起进入第一副本卷；提供全局「✎ 写作台」自由管理/写作章节；每章字数统一到 4000-5000 字。

**Architecture:** 扩展现有三级结构（书→卷→章），新增 `volume_kind="mainline"`（蓝星开篇/主线卷）；DeskStore 新增章节 CRUD + renumber；后端注入按 chapter_kind 分支；SpineNav 常驻写作台入口，把 DeskView 编辑核心拆成 ChapterEditor 共享；rhythm 章号用 `start_chapter_no` 偏移；提供一键迁移脚本给已有项目补建开篇卷。

**Tech Stack:** FastAPI + Vue 3 + Element Plus + 现有 SSE pipeline；不引入新 npm 依赖。

## Global Constraints

- `volume_kind ∈ {"mainline","dungeon","interlude"}`；老数据无此字段按 "dungeon" 兼容
- 章号全局连续递增；`Volume.start_chapter_no` 是本卷起始全局章号（默认1）
- 每章 target_chars：plot=1800 / draft=4800 / polish=5000；LLM 指令明确"4000-5000 字，不短于4000，不超过5000"
- rhythm LLM 输出 chapter 为**卷内相对章号**（1..N），后端落盘前加 `start_chapter_no - 1` 偏移
- 所有写 desk/entities/volumes 操作必须先做 `.bak.TIMESTAMP` 备份
- 蓝星章（chapter_kind="mainline"）禁止注入副本核/NPC卡司/魂穿宿主/Day beat/死亡节点/线索节点/详设6卡等副本竞技场材料
- 老数据无 chapter_kind 字段时，按 field_no→volume.volume_kind 回填；is_interlude 字段保留兼容
- 前端不引入新 npm 包；拖拽改序在本版先返回 501（按钮置灰），下个版本再实现

---

## 文件结构

**新建：**
- `backend/app/api/desk_routes.py` — 章节 CRUD REST
- `backend/app/api/mainline_routes.py` — mainline 卷出章纲 API
- `backend/scripts/migrate_add_mainline.py` — 一键补建开篇卷 CLI
- `frontend/src/views/WritingDeskView.vue` — 全局写作台
- `frontend/src/views/MainlineLandingView.vue` — mainline 引导页
- `frontend/src/components/ChapterEditor.vue` — 从 DeskView 拆出的章编辑器
- `frontend/src/components/NewVolumeDialog.vue` — 新建卷三选一弹窗

**修改：**
- `backend/app/book/volume.py` — volume_kind 加 "mainline"、加 start_chapter_no
- `backend/app/wizard/store.py` — ChapterWork 加 chapter_kind；DeskStore 加 insert_after/delete/renumber/move/rename/list_by_field/max_no/next_available_no/all_sorted/_backup
- `backend/app/entities/store.py` — 加 shift_chapter_refs 工具
- `backend/app/api/main.py` — 注册新 router
- `backend/app/api/phase_routes.py` — 判定加 mainline-waiting/mainline-brief/writing-desk；加 POST 创建卷接口
- `backend/app/api/rhythm_routes.py` — prompt 补完 day=0 蓝星章、words 4000-5000、start_chapter_no 偏移
- `backend/app/api/brief_routes.py` — advance 识别 mainline 卷、mock words 改 4500
- `backend/app/wizard/desk.py` — run_chapter_stage 按 chapter_kind 分支注入材料
- `backend/app/pipeline/engine.py` — 已改（确认 plot=1800/draft=4800/polish=5000）
- `backend/app/pipeline/prompts.py` — 已改（plot/draft 指令字数 4000-5000）
- `frontend/src/api.ts` — 新接口封装
- `frontend/src/components/SpineNav.vue` — 加 ✎写作台、+新建卷下拉
- `frontend/src/views/DeskView.vue` — 拆出编辑核心到 ChapterEditor
- `frontend/src/views/HomeView.vue` — phase 渲染加 mainline-waiting/mainline-brief/writing-desk

---

### Task 1: Volume / ChapterWork 数据模型扩展

**Files:**
- Modify: `backend/app/book/volume.py`
- Modify: `backend/app/wizard/store.py`
- Test: `backend/tests/test_wizard.py`

**Interfaces:**
- Produces:
  - `Volume.volume_kind: Literal["mainline","dungeon","interlude"]` 默认 "dungeon"
  - `Volume.start_chapter_no: int = 1`
  - `ChapterWork.chapter_kind: Literal["mainline","dungeon","interlude"] = "dungeon"`
  - `VolumeStore.create_new_volume(title, volume_kind, start_chapter_no=None)` 扩展签名
- Steps:
  - [ ] 在 test_wizard.py 末尾追加 `TestChapterKindAndVolumeStart` 测试类：
        - test_volume_default_kind_is_dungeon：新建 volume，断言 volume_kind=="dungeon"，start_chapter_no==1
        - test_create_mainline_volume：volume_kind="mainline", start_chapter_no=1，断言两字段正确
        - test_chapter_work_default_kind_dungeon：ChapterWork(no=1).chapter_kind == "dungeon"，is_interlude == False
        - test_chapter_work_from_dict_backcompat：老数据 dict `{"no":1,"title":"x","is_interlude":True}` from_dict 后 chapter_kind=="interlude"
  - [ ] `pytest tests/test_wizard.py::TestChapterKindAndVolumeStart -v`，预期 FAIL
  - [ ] 改 volume.py：在 volume_kind 字段下加 `start_chapter_no: int = 1`；from_dict 读 start_chapter_no；扩展 create_new_volume 签名支持 start_chapter_no 参数，volume_kind=="mainline" 时默认 title 取"蓝星开篇"
  - [ ] 改 store.py：ChapterWork 加 `chapter_kind: str = "dungeon"` 字段；from_dict 根据 chapter_kind/is_interlude 回填（老 is_interlude=True 老数据映射到 chapter_kind="interlude"；缺失字段默认 "dungeon"）；is_interlude 字段保留兼容
  - [ ] 跑测试，全部 PASS
  - [ ] `git commit -m "feat(volume): 新增 mainline volume_kind + start_chapter_no + ChapterWork.chapter_kind"`

---

### Task 2: DeskStore 章节管理方法

**Files:**
- Modify: `backend/app/wizard/store.py`
- Test: `backend/tests/test_wizard.py`

**Interfaces:**
- Produces:
  - `DeskStore.ensure(no, *, title="", field_no=None, chapter_kind=None)` 扩展签名
  - `DeskStore.insert_after(after_no, *, title, field_no, chapter_kind="dungeon") -> int`（after_no=0 插到最前）
  - `DeskStore.delete(no, *, force=False)`（已定稿章需 force=True）
  - `DeskStore.renumber(from_no, delta)` 内部工具
  - `DeskStore.move(no, *, new_field_no=None, new_chapter_kind=None)`
  - `DeskStore.rename(no, title)`
  - `DeskStore.max_no() -> int` / `next_available_no() -> int`
  - `DeskStore.all_sorted() -> list[ChapterWork]`
  - `DeskStore.list_by_field(field_no) -> list[ChapterWork]`
  - `DeskStore._backup()`：每次变更前打 bak
- Steps:
  - [ ] 在 test_wizard.py 追加 `TestDeskStoreChapterManagement` 类：
        - test_ensure_sets_field_no_and_kind：ensure(1, field_no=1, chapter_kind="mainline") 断言字段正确
        - test_insert_after_basic：先 ensure(1,"a") ensure(2,"c")，insert_after(1,"b") 后 all_nos 为 [1,2,3]，标题顺序 a/b/c，原 2 章保留 field_no=2
        - test_insert_at_beginning：after_no=0 插到最前
        - test_delete_shifts_subsequent：ensure 1/2/3；record(2,"plot",...)；delete(2) 后 get(2) is None；本版 delete 不自动压缩空洞（由 API 层决定是否 renumber）
        - test_rename、test_move_change_field、test_list_by_field、test_max_no_and_next_available、test_delete_finalized_requires_force
  - [ ] 跑测试确认 FAIL
  - [ ] 在 store.py 实现：
        - 替换 ensure 为支持 field_no/chapter_kind 的新签名（仅空章接受默认覆盖，已设置的 kind 不被 dungeon 默认值覆写）
        - 新增 max_no / next_available_no / all_sorted / list_by_field / rename
        - 新增 _backup 做 shutil.copy2 到 `.bak.TIMESTAMP`
        - renumber 从 from_no 起所有章号 += delta（delta>0 从大到小改键避免覆盖；delta<0 从小到大）
        - insert_after 先 backup；after_no=0 则 renumber(1,+1) 后插 1；否则 renumber(after_no+1,+1) 后插 after_no+1
        - delete 先 backup；已定稿非 force 抛 ValueError；删键不自动压缩
        - move 改 field_no/chapter_kind，自动维护 is_interlude 同步
  - [ ] 跑测试 PASS
  - [ ] `pytest tests/test_wizard.py -v` 确认无回归
  - [ ] `git commit -m "feat(desk-store): 章节管理 insert_after/delete/renumber/move/rename + 备份"`

---

### Task 3: 实体库章号偏移工具

**Files:**
- Modify: `backend/app/entities/store.py`
- Test: `backend/tests/test_entities.py`

**Interfaces:**
- Produces: `EntityStore.shift_chapter_refs(from_no: int, delta: int) -> None`，更新 foreshadows.planted_chapter / paid_chapter 和 changes[*].chapter；禁止出现章号 ≤ 0。
- Steps:
  - [ ] 先读 `entities/store.py` 和 `entities/__init__.py` 确认 Foreshadow/StateChange 真实字段名（可能是 planted_at/paid_at 而非 planted_chapter；根据实际字段调整）
  - [ ] 在 test_entities.py 追加 `TestShiftChapterRefs`：
        - test_shift_foreshadow_and_changes：准备两条伏笔（一章号1、一章号2已在章3兑现）和一条章2的state change；shift(2, +2) 后章号1不变，章号2→4，章号3→5，change.chapter→4
        - test_shift_no_cross_to_zero：shift(1,-1) 抛 ValueError
  - [ ] 跑测试 FAIL
  - [ ] 实现 shift_chapter_refs：内部 `_shift(v)` 把 >=from_no 的 int += delta，结果 <=0 抛 ValueError；遍历 foreshadows 和 changes 批量改；先备份再 _flush
  - [ ] 跑测试 PASS
  - [ ] `git commit -m "feat(entities): shift_chapter_refs 章号偏移工具"`

---

### Task 4: 章节 REST API + mainline 出章纲 + 新建卷接口

**Files:**
- Create: `backend/app/api/desk_routes.py`
- Create: `backend/app/api/mainline_routes.py`
- Modify: `backend/app/api/main.py`（挂载 router）
- Modify: `backend/app/api/phase_routes.py`（POST /volumes 新建卷接口）

**Interfaces:**
- Produces REST:
  - `POST /api/projects/{pid}/volumes`  body={kind,title,start_chapter_no?}
  - `POST /api/projects/{pid}/desk/insert`  body={after_no,title,field_no,chapter_kind,with_ai_brief?}
  - `DELETE /api/projects/{pid}/desk/{no}?force=false`（删完后自动压缩：entities.shift + desk.renumber）
  - `PATCH /api/projects/{pid}/desk/{no}`  body={title?,field_no?,chapter_kind?,day?}
  - `POST /api/projects/{pid}/desk/reorder`  body={orders:[{no,new_no}]}（本版返回 501）
  - `GET  /api/projects/{pid}/desk/spine` 返回 {volumes, chapters}
  - `POST /api/projects/{pid}/volumes/{vol_no}/mainline-outline`  body={directive?,chapter_count?}
- Steps:
  - [ ] 建 `desk_routes.py`：APIRouter prefix `/api/projects/{pid}`；实现 insert/delete/patch/reorder/spine。insert 调 entities.shift + desk.insert_after；delete 调 desk.delete + entities.shift(no+1,-1) + desk.renumber(no+1,-1)；patch 支持 title/field_no/chapter_kind/day（day=None 可清除）
  - [ ] 建 `mainline_routes.py`：_MAINLINE_OUTLINE_SYSTEM 明确第1章 hook/day=0 蓝星、第2章 sell/day=0 穿越、不许编程黑话、每章4500字；mainline_outline 调 chore_provider("sushi") + complete_structured 出 outlines
  - [ ] main.py 注册两个 router
  - [ ] phase_routes.py 加 CreateVolumeBody(pydantic) 和 POST `/api/projects/{pid}/volumes`，vs.create_new_volume + 自动算 start_chapter_no=desk.next_available_no()，返回新卷 + 当前 phase
  - [ ] 启动后端用 curl 冒烟：GET spine 返回数据、POST volumes 创建 mainline 卷返回正确 phase
  - [ ] `git commit -m "feat(api): 章节管理 REST + mainline 出章纲 + 新建卷三选一接口"`

---

### Task 5: rhythm 字数统一 + day=0 蓝星章 + 章号偏移 + brief advance

**Files:**
- Modify: `backend/app/api/rhythm_routes.py`
- Modify: `backend/app/api/brief_routes.py`
- Test: 新增最小单测验证偏移

**Interfaces:**
- Produces:
  - _RHYTHM_SYSTEM 补完：【全书开篇结构·硬约束】第1-2章蓝星 day=0、第3章起 day=1~7；words 4000-5000；suggested_length 章数×4500；chapter 字段声明是卷内相对号
  - rhythm 落盘前把 chapter_plan[*].chapter += (v.start_chapter_no - 1) 偏移为全局号
  - brief_routes mock 默认 words 改 4500
  - _brief_advance：mainline 卷写完第 2 章（或达到 mainline_expected）后跳转到第一个 dungeon 卷的 briefing（若无 dungeon 卷则 phase=adapt_empty）
- Steps:
  - [ ] 替换 _RHYTHM_SYSTEM：按 spec §2（本计划文件首部"架构"段）和 spec §6 字数核对清单修改
  - [ ] 在 rhythm 结果写入 v.rhythm_blueprint 之前加 offset = (v.start_chapter_no or 1) - 1；遍历 chapter_plan[*].chapter += offset
  - [ ] grep brief_routes.py 的 6500/7000/8000/9000 全部改为 4500
  - [ ] 在 _brief_advance 内判断 vol.volume_kind == "mainline"：若本卷章数 >= 2 则找下个 dungeon 卷（vs.all_list 里 volume_kind=="dungeon" 且 stage_completed.get("rhythm") 的最小 vol_no），返回 {next_chapter: next_dungeon.start_chapter_no, phase:"briefing", volume_no: next_dungeon.volume_no}；否则按原逻辑 ensure(cno+1)
  - [ ] 写最小单测：构造 v.start_chapter_no=3，LLM 返回 chapter_plan=[{chapter:1},{chapter:2}]，落盘后变成 3、4
  - [ ] `pytest tests/` 相关测试 PASS
  - [ ] `git commit -m "feat(rhythm/brief): 字数4000-5000、day=0蓝星章、start_chapter_no偏移、mainline advance"`

---

### Task 6: 写作材料按 chapter_kind 分支注入

**Files:**
- Modify: `backend/app/wizard/desk.py`

**Interfaces:**
- Produces:
  - chapter_kind=="mainline" 时不注入 `volume:v{n}:arena`（副本核/NPC/Day beat/死亡节点/线索/详设），改为注入 `volume:v{n}:mainline`（主角状态+上章尾+未回收伏笔）
  - chapter_kind=="interlude" 时不注入副本竞技场，改为注入 `volume:v{n}:interlude`（杀青残页+侵蚀度+蓝星钩子）
  - chapter_kind=="dungeon" 维持现状
  - 写作模块选择：mainline 章不注入 coc_horror（只注入 infinite_loop_unit + obsession_cost）

- Steps:
  - [ ] 在 run_chapter_stage 内定位 arena 材料拼装位置（grep `adapted_premise` 或 `"volume:v"`）
  - [ ] 先读当前 desk.get(chapter_no) 和 target_vol.volume_kind，得出 ch_kind
  - [ ] 包一层 if ch_kind=="dungeon" 才加 arena 材料；elif "mainline" 加 mainline 材料；else interlude 加 interlude 材料
  - [ ] 实现 `_mainline_context_text(book_store, entity_store, desk, chapter_no)`：返回【蓝星主线·当前状态】文本，包含唐迟 SAN/污染度/位置/上一章尾1500字/未回收伏笔前10条
  - [ ] 实现 `_interlude_context_text(vol, entity_store)`：返回蓝星间章状态（前 dungeon 卷 settle 残页 + 污染度 + 蓝星钩子）
  - [ ] 在写作模块集合选择位置加判断：mainline 章 skill_modules 列表去掉 coc_horror
  - [ ] 写最小测试：构造 mainline 章 + 已填充副本的 dungeon 卷，调用 assembler 组装材料，断言材料 key 集合不包含 `volume:v{n}:arena`，且包含 `volume:v{n}:mainline`
  - [ ] `pytest tests/test_state_ledger.py tests/test_context_assembler.py -v` PASS
  - [ ] `git commit -m "feat(desk-context): 按 chapter_kind 分支注入材料，mainline 不注入副本竞技场"`

---

### Task 7: phase 判定 + writing-desk 相位 + 迁移脚本

**Files:**
- Modify: `backend/app/api/phase_routes.py`
- Create: `backend/scripts/migrate_add_mainline.py`

**Interfaces:**
- Produces:
  - phase 新增 `mainline-waiting`（无 mainline 卷或 mainline 已写章 < 2）
  - phase 新增 `mainline-brief`（mainline 卷有未写的章）
  - `_view="writing-desk"` 覆盖，返回 phase="writing-desk"
  - CLI `python -m app.scripts.migrate_add_mainline <pid>` 一键迁移

- Steps:
  - [ ] 在 phase_routes.py "2. 没有任何 volume" 判断后、`last_vol = max(...)` 之前插入 mainline 判定：查 vs.all_list() 里 volume_kind=="mainline" 的卷；若无，返回 mainline-waiting；若有，统计该卷 field_no 下已写（draft.text 或 finalized_at）章数；若 <2，找第一个未写章 cno，返回 mainline-brief + volume_kind="mainline" + chapter_no=cno
  - [ ] 把 `desk = d.desk_for(pid)` 提前到这一段，避免后面重复
  - [ ] 在 _VIEW_TO_PHASE 加 `"writing-desk": "writing-desk"`；在覆盖判断前加优先分支：若 view_override == "writing-desk" 直接返回 {phase:"writing-desk"}
  - [ ] 建 `scripts/migrate_add_mainline.py`：备份 desk/entities/volumes 到 `migrate-mainline.bak.TS/`；若已有 mainline 卷则跳过；vs.create_new_volume(title="蓝星开篇", volume_kind="mainline", start_chapter_no=1)；desk.renumber(min_no, +2)；entities.shift_chapter_refs(1, +2)；遍历所有 dungeon/interlude 卷，若有 rhythm_blueprint.chapter_plan 则把 chapter 全 +=2，并把 start_chapter_no +=2；desk.ensure(1, "第1章", field_no=mv.volume_no, chapter_kind="mainline")；desk.ensure(2, "第2章", field_no=mv.volume_no, chapter_kind="mainline")；flush 全部
  - [ ] 找一个测试项目（或当前 pid）跑一次迁移，验证：新卷创建、原第1章变成第3章、rhythm_blueprint 章号+2、entities foreshadow+changes+2、mainline 下空 1/2 章
  - [ ] 再跑一次迁移脚本，应打印"已存在 mainline 卷，跳过"（幂等）
  - [ ] `git commit -m "feat(phase): mainline-waiting/brief 阶段判定 + writing-desk 相位 + 一键补建开篇卷迁移脚本"`

---

### Task 8: 字数统一扫尾

**Files:**
- Modify: `backend/app/pipeline/engine.py`
- Modify: `backend/app/pipeline/prompts.py`
- Modify: 后端 grep 出来残留的 6500/7000/8000/9000
- Modify: `frontend/src/views/DeskView.vue` 若有字数过短/过长告警阈值

- Steps:
  - [ ] 确认 engine.py SPECS：plot=1800 / draft=4800 / polish=5000（当前 session 已改）
  - [ ] 确认 prompts.py stages：plot 1500-1900字骨架，draft 4000-5000字（当前 session 已改）
  - [ ] `grep -rn "6500\|7000\|8000\|9000" backend/app --include="*.py" | grep -v test | grep -v __pycache__` 把残留默认值改成 4500 或对应 spec 值（重点 brief_routes、rhythm_routes mock、scaffold_routes 若有字数引用）
  - [ ] `grep -n "字数\|过短\|过长" frontend/src/views/DeskView.vue frontend/src/components/*.vue`，阈值调整：过短 <3500 提示，过长 >5500 提示
  - [ ] `pytest backend/tests/test_pipeline.py -v` 通过
  - [ ] `git commit -m "fix(wordcount): 全链路字数统一到 4000-5000/章"`

---

### Task 9: 前端 ChapterEditor 拆分 + WritingDeskView + MainlineLandingView + NewVolumeDialog

**Files:**
- Modify: `frontend/src/views/DeskView.vue`（拆编辑核心）
- Create: `frontend/src/components/ChapterEditor.vue`
- Create: `frontend/src/views/WritingDeskView.vue`
- Create: `frontend/src/views/MainlineLandingView.vue`
- Create: `frontend/src/components/NewVolumeDialog.vue`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces:
  - `ChapterEditor` props: `{projectId: string, chapterNo: number|null, volumeKind?: string}`，emits `@chapterChanged(no)`
  - `WritingDeskView`：左侧按卷分组章列表（每章 hover 显示改名/改归属/删除），"+ 新建章"下拉（追加/当前章前插入），右侧 ChapterEditor
  - `MainlineLandingView`：大标题"先写蓝星开篇"+「📖 新建蓝星开篇卷」按钮
  - `NewVolumeDialog`：三选一卡片（📖主线/👹副本/🌌间章）+ 标题输入 + 确认
  - api.ts 新增 deskApi.{insert,delete,patch,reorder,spine} 和 volumeApi.{create,mainlineOutline}

- Steps:
  - [ ] 在 api.ts 加 deskApi 和 volumeApi 两个对象（封装 insert/delete/patch/reorder/spine/create/mainlineOutline，用现有 get/post/patch/del 工具函数）
  - [ ] 新建 ChapterEditor.vue：把 DeskView.vue 里"章标题编辑 + plot/draft/polish tab + AI 生成按钮 + directive 输入 + 手动编辑 el-input(textarea) + 保存 + 三线打卡 + 兑现规模 + 审稿/修复/定稿 + 字数统计"整块复制出来；props 接收 projectId/chapterNo/volumeKind；watch chapterNo 变化时加载章数据；所有现有行为（AI 流式、finalize、review）保持不变
  - [ ] DeskView.vue 里把移走的整块替换为 `<ChapterEditor :project-id="projectId" :chapter-no="currentChapter" :volume-kind="currentVolumeKind" @chapter-changed="reloadChapter" />`；保留 PrevStepPanel 和阶段指示/上下章导航
  - [ ] 新建 NewVolumeDialog.vue：用 el-dialog + el-radio-group 三张卡片（📖蓝星开篇/主线、👹副本卷、🌌蓝星间章），每张卡片有 3-5 行说明文字；标题 el-input；确认时调 volumeApi.create
  - [ ] 新建 MainlineLandingView.vue：居中卡片，文案"黄金三章：先在蓝星把主角亮出来"+ 说明"第 1-2 章在蓝星，第 3 章起进入副本"，一个"📖 新建蓝星开篇卷"按钮触发 NewVolumeDialog（kind=mainline）
  - [ ] 新建 WritingDeskView.vue：
        - el-container 布局，左侧 280px el-aside，右侧 el-main
        - 左侧顶栏："✎ 写作台"标题 + "+ 新建章"下拉（append/before-current）；当前卷/章显示
        - 按卷分组章列表：每个卷折叠分组（el-collapse），卷头显示卷 icon+kind 标签+卷名+「+加一章」按钮；每章一行（章号+标题+状态小圆点+字数），hover 显示 ✎ 改名/📋 改归属/✕ 删除；点击章选中，右侧加载 ChapterEditor
        - 选中态高亮；章列表数据通过 deskApi.spine() 加载，变更后刷新
        - 右侧直接放 `<ChapterEditor>`，不传 volumeKind（ChapterEditor 自己按 chapter 数据决定）
        - 新建章按钮：弹 el-dialog 输入标题+选归属卷（默认当前选中章的卷）+选 chapter_kind（根据目标卷 kind 自动推断，可覆盖）
        - 「重排」按钮暂时置灰+tooltip「下个版本支持拖拽改序」（接口返回 501）
  - [ ] `git commit -m "feat(frontend): ChapterEditor 拆分 + WritingDeskView 全局写作台 + MainlineLanding + NewVolumeDialog"`

---

### Task 10: SpineNav / HomeView 接入

**Files:**
- Modify: `frontend/src/components/SpineNav.vue`
- Modify: `frontend/src/views/HomeView.vue`
- Modify: `frontend/src/components/StatusBar.vue`（若需要显示 mainline 状态）

**Interfaces:**
- Produces:
  - SpineNav 顶部常驻「✎ 写作台」按钮；"+ 新副本"改为"+ 新建卷"下拉触发 NewVolumeDialog
  - HomeView 的 v-if 链新增 mainline-waiting → MainlineLandingView、mainline-brief → WritingDeskView、writing-desk → WritingDeskView
  - 用户点 ✎写作台 时，HomeView 调 API 写 `_view: "writing-desk"` 覆盖（类似现有 back-to-adapt 逻辑）或直接本地切换（更简单：HomeView 本地维护 `freeView = "writing-desk" | null`，点按钮设置 freeView，HomeView 根据 freeView 优先渲染 WritingDeskView）

- Steps:
  - [ ] SpineNav 顶部（卷列表上方）加 `<el-button text @click="$emit('goWritingDesk')">✎ 写作台</el-button>`
  - [ ] SpineNav 把现有「+ 新副本」按钮替换为「+ 新建卷」按钮，点击 emit `openNewVolumeDialog`
  - [ ] HomeView 维护一个 `freeView` ref（初始 null）；SpineNav @go-writing-desk 设置 freeView="writing-desk"；@open-new-volume 设置 newVolumeDialogVisible=true；放一个 `<NewVolumeDialog v-model="newVolumeDialogVisible" ... />`
  - [ ] HomeView template 顶部判断：`v-if="freeView==='writing-desk'"` 渲染 WritingDeskView（并传入 projectId 和 selectedChapter=null），否则按原 phase 渲染
  - [ ] 在原 phase v-if 链里加：`v-else-if="phase==='mainline-waiting'"` 渲染 MainlineLandingView；`v-else-if="phase==='mainline-brief' || phase==='writing-desk'"` 渲染 WritingDeskView（自动定位到 phase 返回的 chapter_no）
  - [ ] 验证：从 setup 完成后进入，看到 MainlineLandingView；点「新建蓝星开篇卷」创建 mainline 卷后跳到写作台看到空的第 1 章；写完第 1 章定稿自动到第 2 章；写完第 2 章定稿后跳到第一副本卷 briefing（若副本卷已存在）或 adapt_empty（引导建副本）
  - [ ] 验证：任何时点点 ✎写作台 都能进入全项目章列表，能在任意章上继续写/插入/删除
  - [ ] `git commit -m "feat(frontend): SpineNav 常驻写作台+新建卷、HomeView 接 mainline/writing-desk 相位"`

---

### Task 11: 重启 + 端到端验证 + push

- Steps:
  - [ ] `cd backend ; python -m py_compile app/api/main.py app/api/desk_routes.py app/api/mainline_routes.py app/wizard/desk.py app/api/phase_routes.py app/api/rhythm_routes.py app/api/brief_routes.py app/wizard/store.py app/book/volume.py app/entities/store.py` 全部无 SyntaxError
  - [ ] `cd backend ; pytest -x -q` 通过（允许个别非相关测试失败但新功能测试必须全过）
  - [ ] 杀旧 uvicorn，启动新后端 `python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8321`
  - [ ] 前端 `cd frontend ; npm run dev`（若未运行）
  - [ ] 浏览器端到端验证：
        1. 打开当前项目，刷新后应看到 MainlineLandingView（因为原项目没有 mainline 卷）
        2. 点「新建蓝星开篇卷」→ 弹出 NewVolumeDialog → 选📖蓝星开篇 → 确认
        3. 若想用自动迁移补到原项目前面，调用迁移脚本：`python -m app.scripts.migrate_add_mainline <pid>`；这会把原第1章变成第3章并插空1/2章
        4. 进写作台，能看到第 1 章空章（蓝星）、第 2 章空章（蓝星）、原第1章→第3章（已有的 brief 选卡仍在）
        5. 在第 1 章让 AI 出方向卡/写正文，验证生成内容不出现副本 NPC/Day beat 等副本材料，字数落在 4000-5000 区间
        6. 定稿第 1 章后自动到第 2 章
  - [ ] 提交本地所有未提交改动
  - [ ] `git push origin master`

---

## 自检覆盖

- Spec §1 数据模型（volume_kind/start_chapter_no/chapter_kind）→ Task 1
- Spec §2.1 新建卷接口 → Task 4
- Spec §2.2 章节管理 REST → Task 4
- Spec §2.3 mainline-outline → Task 4
- Spec §2.4 注入分支 → Task 6
- Spec §2.5 phase 判定 → Task 7
- Spec §2.6 advance 支持 mainline → Task 5
- Spec §3.1 SpineNav 写作台入口+新建卷下拉 → Task 10
- Spec §3.2 NewVolumeDialog → Task 9
- Spec §3.3 WritingDeskView 布局+能力 → Task 9
- Spec §3.4 DeskView/ChapterEditor 拆分 → Task 9
- Spec §3.5 HomeView 路由 → Task 10
- Spec §4 mainline 轻量流程 → Task 4（outline）+ Task 5（advance）+ Task 9（UI）
- Spec §5 现有项目迁移 → Task 7
- Spec §6 字数 4000-5000 全链路 → Task 8（Task 1-5 局部改）
- Spec §7 测试 → 每个 Task 自带单测
- Spec §9 scope 控制：未实现 reorder 拖拽（返回 501，按钮置灰），下个版本补
