# Task 2 Brief: DeskStore 章节管理方法

## Goal
在 DeskStore 上实现章节 CRUD + renumber 工具 + backup 保护，支持写作台自由管理章节。

## Files to modify
- `backend/app/wizard/store.py`（DeskStore 类）
- `backend/tests/test_wizard.py`（追加 `TestDeskStoreChapterManagement` 测试类）

## Interfaces to produce

在 DeskStore 类里新增/修改以下方法（保持已有方法行为不变，向后兼容）：

1. **修改 `ensure` 签名**：
   ```python
   def ensure(self, no: int, title: str = "", *, field_no: int | None = None,
              chapter_kind: str | None = None) -> ChapterWork
   ```
   - 若章不存在，创建 ChapterWork(no=no, field_no=field_no, chapter_kind=chapter_kind or "dungeon")
   - 若章已存在：title 非空且原 title 空时才覆盖（保留原逻辑）；**field_no 非 None 时覆盖**；chapter_kind 非 None 且不是默认 "dungeon" 覆盖已有值（避免"dungeon"默认值把已设置的 mainline/interlude 抹掉）。具体：如果传入的 chapter_kind 不为 None，且该章当前 chapter_kind 是默认 "dungeon" 或传入值非 "dungeon"，则更新；更简单稳妥：仅当传入 chapter_kind 是 {"mainline","dungeon","interlude"} 之一，才赋值覆盖。最后同步 is_interlude = (chapter_kind=="interlude")。
   - 注意：旧调用 `ensure(no)` 和 `ensure(no, "标题")` 必须继续工作（title 是位置参数）。

2. **`_backup()`**（私有）：
   - 调用 shutil.copy2 把 `self.path` 复制到 `self.path.with_suffix(self.path.suffix + f".bak.{_now_ts()}")`
   - 其中 _now_ts 用 datetime.now().strftime("%Y%m%d-%H%M%S")，避免文件名里有空格/冒号
   - 若 self.path 不存在（全新项目），跳过备份，不抛错
   - 需要在文件顶部 `import shutil`

3. **`max_no() -> int`**：返回现有最大章号，无章返回 0
4. **`next_available_no() -> int`**：返回 max_no()+1
5. **`all_sorted() -> list[ChapterWork]`**：按 no 升序返回所有章
6. **`list_by_field(field_no: int | None) -> list[ChapterWork]`**：返回 field_no 匹配的章列表，按 no 升序；field_no=None 时返回未归属章
7. **`rename(no: int, title: str) -> ChapterWork`**：改名，找不到章抛 ValueError；调用 _backup 后改，再 _flush
8. **`renumber(from_no: int, delta: int) -> None`**：内部工具。把 no >= from_no 的所有章号 += delta。delta>0 时必须从大到小改键避免覆盖；delta<0 从小到大。**只改 _chapters dict 的 key 和 ChapterWork.no 字段**，不碰文件（调用方会 _flush）。改完如果章号 <=0 抛 ValueError。调用前不 _backup（由外层调用方做）。
9. **`insert_after(after_no: int, *, title: str, field_no: int | None = None, chapter_kind: str = "dungeon") -> int`**：
   - after_no=0 表示插到最前；否则插到 after_no 之后
   - 流程：_backup → 如果 after_no==0：renumber(1, +1)，新 no=1；否则：renumber(after_no+1, +1)，新 no=after_no+1 → chapters[new_no] = ChapterWork(no=new_no, title=title, field_no=field_no, chapter_kind=chapter_kind or "dungeon")，同步 is_interlude → _flush → 返回 new_no
10. **`delete(no: int, *, force: bool = False) -> None`**：
    - _backup
    - 若章不存在抛 ValueError
    - 若章已定稿（finalized_at 非空）且 force=False 抛 ValueError("第 N 章已定稿，需 force=True 才能删除")
    - 删键；**本版不自动压缩空洞**（由 API 层决定是否 renumber+shift entities）
    - _flush
11. **`move(no: int, *, new_field_no: int | None = None, new_chapter_kind: str | None = None, new_day: int | None = None) -> ChapterWork`**：
    - 改章的归属卷/类型/天；找不到章抛 ValueError
    - new_chapter_kind 必须合法（否则忽略/抛错——选择抛 ValueError）
    - 若 new_chapter_kind="interlude" 自动设 is_interlude=True, day=None；其他 kind 设 is_interlude=False，day 保持（除非 new_day 显式传入）
    - _backup → 改字段 → _flush

## Tests（TDD：先写测，确认 FAIL，再实现）

在 `test_wizard.py` 末尾追加 `TestDeskStoreChapterManagement` 类（使用 tmp_path 构造 DeskStore）：

```python
class TestDeskStoreChapterManagement:
    def _make(self, tmp_path):
        return DeskStore(tmp_path / "desk.json")

    def test_ensure_sets_field_no_and_kind(self, tmp_path):
        d = self._make(tmp_path)
        c = d.ensure(1, "第1章", field_no=1, chapter_kind="mainline")
        assert c.field_no == 1
        assert c.chapter_kind == "mainline"
        assert c.is_interlude is False
        # 再次 ensure 传 dungeon 默认值不应覆盖已设置的 mainline
        c2 = d.ensure(1, "第1章", field_no=1)
        assert c2.chapter_kind == "mainline"

    def test_insert_after_basic(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "a", field_no=1)
        d.ensure(2, "c", field_no=1)
        new_no = d.insert_after(1, title="b", field_no=1, chapter_kind="dungeon")
        assert new_no == 2
        nos = d.all_nos()
        assert nos == [1, 2, 3]
        assert [d.get(n).title for n in nos] == ["a", "b", "c"]
        assert d.get(3).field_no == 1  # 原 2 章被 renumber 到 3，field_no 保留

    def test_insert_at_beginning(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "a", field_no=1)
        new_no = d.insert_after(0, title="zero", field_no=1, chapter_kind="mainline")
        assert new_no == 1
        assert [d.get(n).title for n in d.all_nos()] == ["zero", "a"]
        assert d.get(1).chapter_kind == "mainline"

    def test_delete_shifts_nothing_in_this_version(self, tmp_path):
        # 本版 delete 不压缩空洞
        d = self._make(tmp_path)
        d.ensure(1, "a")
        d.ensure(2, "b")
        d.record(2, "plot", "some plot text")
        d.ensure(3, "c")
        d.delete(2)
        assert d.get(2) is None
        assert d.all_nos() == [1, 3]

    def test_rename(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "原标题")
        d.rename(1, "新标题")
        assert d.get(1).title == "新标题"

    def test_move_change_field_and_kind(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "x", field_no=1, chapter_kind="dungeon", day=2)  # 注意 ensure 当前签名可能不接 day，若不接就 ensure 后 assign_field
        # 如果 ensure 不支持 day 参数，改用：
        # d.ensure(1, "x", field_no=1, chapter_kind="dungeon")
        # d.assign_field(1, field_no=1, day=2)
        d.move(1, new_field_no=2, new_chapter_kind="interlude")
        c = d.get(1)
        assert c.field_no == 2
        assert c.chapter_kind == "interlude"
        assert c.is_interlude is True
        assert c.day is None  # interlude 自动清 day

    def test_list_by_field(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "a", field_no=1)
        d.ensure(2, "b", field_no=2)
        d.ensure(3, "c", field_no=1)
        assert [c.no for c in d.list_by_field(1)] == [1, 3]
        assert [c.no for c in d.list_by_field(2)] == [2]

    def test_max_no_and_next_available(self, tmp_path):
        d = self._make(tmp_path)
        assert d.max_no() == 0
        assert d.next_available_no() == 1
        d.ensure(5, "x")
        assert d.max_no() == 5
        assert d.next_available_no() == 6

    def test_delete_finalized_requires_force(self, tmp_path):
        d = self._make(tmp_path)
        d.ensure(1, "x")
        d.record(1, "draft", "正文内容")
        d.mark_finalized(1)
        import pytest
        with pytest.raises(ValueError):
            d.delete(1)
        d.delete(1, force=True)
        assert d.get(1) is None

    def test_backup_created_on_mutation(self, tmp_path):
        # insert_after/delete/rename/move 都会 _backup；验证备份文件存在
        d = self._make(tmp_path)
        d.ensure(1, "a")
        d.rename(1, "a2")
        bak = list(tmp_path.glob("desk.json.bak.*"))
        assert len(bak) >= 1
```

**关于 ensure 签名和 day 参数**：目前 `ensure(no, title="")`，新签名把 `field_no, chapter_kind` 做成 keyword-only。测试里 `test_move` 如果 ensure 不接受 day 参数，就先 ensure 再调用 `assign_field(1, field_no=1, day=2)`。

**注意**：
- `record()` 里会调 `self.ensure(no, title)`，新签名必须保持这个调用成立（title 是第二个位置参数没问题）
- `assign_field` 当前会设 `is_interlude/day`，新 chapter_kind 字段要保持同步——在 `assign_field` 里加一行：如果 is_interlude=True 则 work.chapter_kind="interlude"；否则 work.chapter_kind="dungeon"（day 1-7 的副本章）。这样 assign_field 也能维护 chapter_kind 一致性。

## Workflow
1. 先**追加测试**
2. 跑测试 `cd backend ; python -m pytest tests/test_wizard.py::TestDeskStoreChapterManagement -v`，确认 FAIL
3. 改 `store.py`：
   - 顶部加 `import shutil`
   - 加 `_now_ts()` 辅助函数（或直接用 inline）
   - 修改 ensure 签名和逻辑
   - 修改 assign_field 同步 chapter_kind
   - 依次加 _backup / max_no / next_available_no / all_sorted / list_by_field / rename / renumber / insert_after / delete / move
4. 跑测试 `TestDeskStoreChapterManagement` → 全 PASS
5. 跑全量 `python -m pytest tests/test_wizard.py -v` → 全部 PASS（可能因为 ensure 签名变动影响其他测试，需要修复）
6. 提交：`feat(desk-store): 章节管理 insert_after/delete/renumber/move/rename + 备份`

## Important constraints
- 不要修改其他文件
- 备份文件名格式：`desk.json.bak.YYYYMMDD-HHMMSS`
- renumber 必须注意方向避免 key 覆盖
- is_interlude 字段保留，和 chapter_kind 双向同步
- 不要重启后端、不要跑前端
- 若遇到 ensure 签名改动导致 record/assign_field 等内部调用报错，一并修复
