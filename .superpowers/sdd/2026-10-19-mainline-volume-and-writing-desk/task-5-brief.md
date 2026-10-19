# Task 5 Brief: rhythm 字数 + day=0 蓝星章 + start_chapter_no 偏移 + brief advance

## Goal
1. 改 _RHYTHM_SYSTEM：words 硬约束 4000-5000；明确 chapter 是卷内相对号；不再要求 day=1-7 全占（因为蓝星卷是 day=0）。实际上**rhythm 只服务 dungeon 卷**（mainline 不走 rhythm，mainline 章走 mainline-outline），所以 prompt 里不用提蓝星，只需保持对 dungeon 卷的 day=1-7 约束，但示例里的 words 改成 4500。
2. 改 _mock_blueprint：words 全改 4500；suggested_length = 章数*4500；suggested_chapter_range=[14,20]（14-20章）。
3. 在 rhythm analyze 返回的 blueprint 写入 volume 之前，做 `start_chapter_no` 偏移：`offset = (vol.start_chapter_no or 1) - 1`，遍历 chapter_plan[*].chapter += offset。**mock 也要偏移**。
4. 改 brief_routes.py：所有 fallback words（6500/7000/8000/9000）改为 4500；mock 三张方向卡的示例也用 4500；在 _brief_advance 里加 mainline 卷处理逻辑。
5. _brief_advance：当 vol.volume_kind=="mainline" 时：
   - 若 cno >= 2（蓝星 2 章写完）或 at_end 为 True（mainline 卷没有 rhythm_blueprint，所以 auto_at_end 可能 False，需要按 mainline_expected=2 判断）：
     - 找 vs.all_list() 里 volume_kind=="dungeon" 且 stage_completed.get("rhythm")==True 的最小 volume_no 的卷作为 next_dungeon
     - 若找到：返回 {"next_chapter": next_dungeon.start_chapter_no, "phase": "briefing", "volume_no": next_dungeon.volume_no, "at_end": True, ...}
     - 若没找到：返回 {"next_chapter": None, "phase": "adapt_empty", "at_end": True, ...}（引导用户创建副本卷）
   - 否则（mainline 还没写完 2 章）：原逻辑 ensure(cno+1)，设 chapter_kind="mainline", field_no=vol.volume_no
6. 在 desk.ensure(cno+1) 时，对 mainline 章也要设 chapter_kind="mainline" 和 field_no=vol.volume_no（原代码只设了 field_no=vol_no，没设 chapter_kind）。注意：这是 _brief_advance 的通用行为，对非 mainline 章也应该设 chapter_kind="dungeon"（day 按 plan 里的 day 推断）。简化处理：对 mainline 显式设 chapter_kind="mainline"，dungeon 章按现有逻辑（后面 Task 6 材料注入时根据 field_no 找 volume 判断 kind 也可以）。

## Files
- MODIFY `backend/app/api/rhythm_routes.py`
- MODIFY `backend/app/api/brief_routes.py`
- ADD test（可选，最小偏移单测）

## Detailed changes

### rhythm_routes.py

1. **_RHYTHM_SYSTEM 修改**：
   - line 42: `suggested_length` 示例改为 63000~90000（14~20章 × 4500）
   - line 43: `suggested_chapter_range` 示例改为 [14, 20]
   - line 45-46: chapter_plan 示例 words 改为 4500
   - line 56: suggested_chapter_range 约束改为 14~20 之间
   - line 57: "words（本章预估字数，整数 4000~5000，推荐 4500）"
   - 加一条新字段说明：`【关于章号】chapter_plan[*].chapter 从 1 开始递增，表示卷内相对章号，不要加全局偏移；后端落盘时会根据 Volume.start_chapter_no 自动转为全书章号。`
   - 把 "每章 4000-5000 字，不短于4000，不超过5000" 明确写入字段约束。

2. **_mock_blueprint 修改**：
   - suggested_chapter_range: [14, 20]
   - suggested_length: 14*4500=63000（即 63000）
   - 所有 chapter_plan[*].words 改为 4500
   - 加章到 14 章（保持），如果现在不足 14 章补到 14 章
   - 原有 chapter 字段从 1..N 保持卷内号

3. **偏移逻辑**：在 _rhythm_analyze 里（写入 v.rhythm_blueprint 之前，line 279 `vols.update_planning(vol_no, rhythm_blueprint=blueprint)` 之前）：
   ```python
   # 卷内相对号 → 全局章号偏移
   offset = (vol.start_chapter_no or 1) - 1
   if offset and isinstance(blueprint, dict):
       plan = blueprint.get("chapter_plan")
       if isinstance(plan, list):
           for p in plan:
               if isinstance(p, dict) and isinstance(p.get("chapter"), int):
                   p["chapter"] = p["chapter"] + offset
       # suggested_length/chapter_range 不偏移（卷内字数范围）
   ```
   注意：vol 变量是在 _rhythm_analyze 里的，查代码确认 vol 的获取位置（应该是 `vol = vols.get(vol_no)`）。offset 必须在写入 blueprint 之前做，而且 mock 也要做（mock 走同一个 blueprint 变量路径即可）。

### brief_routes.py

1. **word 统一 4500**：
   - line 167: `(plan or {}).get("words") or 4500`
   - line 208: fallback 6500→4500
   - line 307: `int((plan or {}).get("words") or 4500)`
   - grep 所有 6500/7000/8000/9000 都改成 4500（mock 数据里的 words 示例也改）

2. **_brief_advance 修改**：
   在 at_end 判断之前加 mainline 处理：
   ```python
   # ---- mainline 卷特殊处理：写完 2 章（或用户手动 force_end）跳到下一卷 ----
   if getattr(vol, "volume_kind", None) == "mainline":
       mainline_expected = 2
       mainline_chs = [n for n in desk.all_nos() if (ch := desk.get(n)) and ch.field_no == vol.volume_no]
       written_finalized = sum(1 for n in mainline_chs if desk.get(n) and desk.get(n).finalized_at)
       if body.finalized and (written_finalized >= mainline_expected or at_end):
           # 找第一个 dungeon 已 rhythm 通过的卷
           dungeon_vols = sorted(
               [v for v in vs.all_list()
                if v.volume_kind == "dungeon" and (v.stage_completed or {}).get("rhythm")],
               key=lambda v: v.volume_no,
           )
           sc_upd = dict(getattr(vol, "stage_completed", None) or {})
           sc_upd["briefing"] = True
           sc_upd["desk"] = True
           vs.update_planning(vol_no, stage_completed=sc_upd)
           if dungeon_vols:
               nd = dungeon_vols[0]
               return {"next_chapter": nd.start_chapter_no, "phase": "briefing",
                       "volume_no": nd.volume_no, "at_end": True,
                       "auto_at_end": True, "force_state": "next-volume"}
           else:
               return {"next_chapter": None, "phase": "adapt_empty",
                       "at_end": True, "auto_at_end": True, "force_state": "next-volume"}
       elif body.finalized:
           # mainline 还没写完 2 章，继续下一章
           new_no = cno + 1
           new_work = desk.ensure(new_no)
           new_work.field_no = vol.volume_no
           new_work.chapter_kind = "mainline"
           new_work.is_interlude = False
           desk._flush()
           return {"next_chapter": new_no, "phase": "briefing",
                   "at_end": False, "auto_at_end": False, "force_state": "mainline-continue"}
   ```
   把这段插入到 line 636 `if body.finalized:` 之前（在 at_end/force_state 判定之后、finalized 分支之前）。

3. **通用 chapter_kind 同步**：在 `desk.ensure(cno+1)` 创建 dungeon 章的地方（line 653 附近），加上 `new_work.chapter_kind = "dungeon"`，保持字段一致。

### Test（最小验证，可选但推荐）

在 `backend/tests/` 下建一个简单的 test_rhythm_offset.py 或加到 test_wizard.py 末尾：
```python
def test_rhythm_blueprint_chapter_offset():
    # 构造一个 vol，start_chapter_no=3，mock blueprint 里 chapter 1,2
    # 应用 offset 逻辑后 chapter 变 3,4
    from app.api.rhythm_routes import _mock_blueprint
    bp = _mock_blueprint()
    start_chapter_no = 3
    offset = start_chapter_no - 1
    for p in bp["chapter_plan"]:
        p["chapter"] = p["chapter"] + offset
    chapters = [p["chapter"] for p in bp["chapter_plan"]]
    assert chapters[0] == 3
    assert chapters[1] == 4
```
实际上更简单：直接测试 `_mock_blueprint` 所有 words==4500，range==[14,20]。

## Workflow
1. 读 rhythm_routes.py 和 brief_routes.py 完整相关段。
2. 改 rhythm_routes.py：
   - 改 _RHYTHM_SYSTEM 里的字数和章数说明（保持其他字段不变）
   - 改 _mock_blueprint：章数14章、words=4500、suggested_length=63000、suggested_chapter_range=[14,20]
   - 在 _rhythm_analyze 里 bp = data 之后、写入 update_planning 之前，加 start_chapter_no 偏移逻辑
3. 改 brief_routes.py：
   - 所有 6500/7000/8000/9000 默认值改成 4500
   - 在 _brief_advance 里加 mainline 卷处理块
   - 在 dungeon 创建新章处设 chapter_kind="dungeon"
4. 在 backend/tests/test_wizard.py 末尾加一个简单的 test_mock_blueprint_words 验证 mock words==4500。
5. 语法检查：`cd backend ; python -m py_compile app/api/rhythm_routes.py app/api/brief_routes.py`
6. 跑测试：`python -m pytest tests/test_wizard.py tests/test_entities.py -v -k "mock_blueprint or TestChapterKind or TestDeskStore or TestShift"` 必须全 PASS。
7. 快速导入验证：`python -c "from app.api.rhythm_routes import _mock_blueprint; bp=_mock_blueprint(); assert all(p['words']==4500 for p in bp['chapter_plan']); print('OK', bp['suggested_chapter_range'])"`
8. 提交：`feat(rhythm/brief): 字数4000-5000、start_chapter_no偏移、mainline advance`

## Important constraints
- rhythm 阶段不走 mainline 卷（mainline 没有 rhythm_blueprint，直接走 mainline-outline），所以 _RHYTHM_SYSTEM 不需要提蓝星，只要字数改成 4500。
- 偏移逻辑必须在 update_planning 写入之前做，这样 volume.rhythm_blueprint 里存的是全局章号。
- 不改其他文件；不重启后端；不跑前端。
- brief_routes 的 mock options 也要把 words 字段统一为 4500（如果 options 里有 words 字段的话，看 line 160-210 附近的 mock 数据）。
