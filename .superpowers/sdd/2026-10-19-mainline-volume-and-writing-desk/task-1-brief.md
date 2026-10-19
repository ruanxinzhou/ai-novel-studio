# Task 1 Brief: Volume/ChapterWork 数据模型扩展

## Goal
给 `Volume` 加 `start_chapter_no: int = 1`，扩展 `volume_kind` 取值到 `{"mainline","dungeon","interlude"}`；给 `ChapterWork` 加 `chapter_kind: str = "dungeon"`，并处理老数据兼容（is_interlude→interlude 回填）；扩展 `VolumeStore.create_new_volume` 签名支持 `start_chapter_no` 参数 + mainline 默认标题"蓝星开篇"。

## Files to modify
- `backend/app/book/volume.py`
  - Line 80: `volume_kind: str = "dungeon" # 'dungeon' | 'interlude'` — 注释改为 `'mainline' | 'dungeon' | 'interlude'`
  - 在 `volume_kind` 字段下新增 `start_chapter_no: int = 1`
  - `from_dict()` 里新增 `start_chapter_no=int(d.get("start_chapter_no", 1) or 1)`；`volume_kind` 如果老数据缺 field，默认 "dungeon"；如果出现老数据的非法值（如不是三者之一），回退 "dungeon"
  - `create_new_volume` 签名扩展为 `create_new_volume(self, *, title: str = "", volume_kind: str = "dungeon", start_chapter_no: int | None = None) -> Volume`：
      - 当 start_chapter_no 为 None 时，若是 dungeon/interlude 卷默认值=1（保持兼容），mainline 卷默认=1
      - 当 title 为空时：mainline→"蓝星开篇"，interlude→"蓝星间章"，dungeon→f"卷{next_no}"（保留现有逻辑）
      - 构造 Volume 时传入 start_chapter_no
  - `_flush` 写入 index.json 时把 `start_chapter_no` 也加进去（在 `volume_kind` 同一块 entries.append 里），这样前端 spine 能拿到
  - **验证值合法性**：如果 volume_kind 不在 {"mainline","dungeon","interlude"}，默认回退 "dungeon"
- `backend/app/wizard/store.py`
  - ChapterWork (line 202) 加字段 `chapter_kind: str = "dungeon"`，位置放在 `is_interlude` 上方（和 field_no/day 一组）
  - `from_dict` (line 228) 的回填逻辑：
      - 先读 d.get("chapter_kind")
      - 如果 chapter_kind 存在且在 {"mainline","dungeon","interlude"} 之一，用它；
      - 否则如果 d.get("is_interlude") 为真，设为 "interlude"；
      - 否则默认 "dungeon"
      - 同时**根据 chapter_kind 同步 is_interlude**：chapter_kind=="interlude" 时 is_interlude=True，其他 False（这样老代码读 is_interlude 仍然正确）
  - 其他 DeskStore 方法暂不修改（留给 Task 2）

## Tests to add (backend/tests/test_wizard.py)
在文件末尾追加一个 `TestChapterKindAndVolumeStart` 类，包含 4 个测试：

1. `test_volume_default_kind_is_dungeon`：调用 `create_new_volume()` 无参数，断言 volume_kind=="dungeon"，start_chapter_no==1，title 以"卷"开头
2. `test_create_mainline_volume`：`create_new_volume(volume_kind="mainline", start_chapter_no=1)`，断言 volume_kind=="mainline", start_chapter_no==1, title=="蓝星开篇"
3. `test_chapter_work_default_kind_dungeon`：`ChapterWork(no=1)` 断言 chapter_kind=="dungeon"，is_interlude==False
4. `test_chapter_work_from_dict_backcompat`：`ChapterWork.from_dict({"no":1,"title":"x","is_interlude":True})` 断言 chapter_kind=="interlude"，is_interlude==True；再测 `{"no":1,"title":"x","chapter_kind":"mainline"}` 断言 chapter_kind=="mainline", is_interlude==False

## Workflow
1. 先**追加测试**（用 TDD：先写测试，确认 FAIL）
2. 再改 `volume.py` 和 `store.py`
3. 跑测试：`cd backend ; $env:PATH='C:\Program Files\Git\cmd;'+$env:PATH; python -m pytest tests/test_wizard.py::TestChapterKindAndVolumeStart -v`
4. 确认 4 个测试全 PASS；再跑 `python -m pytest tests/test_wizard.py -v` 确保无回归
5. git add + commit：`feat(volume): 新增 mainline volume_kind + start_chapter_no + ChapterWork.chapter_kind`

## Important constraints
- 不要修改任何其他文件
- 不要动 DeskStore 的 ensure/record 等方法（留给 Task 2）
- 不要引入新的 import（除非 dataclass 已有）
- Python 代码类型注解用 `from __future__ import annotations` 风格（文件已有）
- ChapterWork.from_dict 中 strands 白名单 `STRANDS` 已在文件顶部定义，不必关心
- 确保老数据兼容：is_interlude 字段必须保留，不能删除
- 不要重启后端，不要跑前端
