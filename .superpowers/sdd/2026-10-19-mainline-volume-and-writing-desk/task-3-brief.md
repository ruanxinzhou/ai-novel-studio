# Task 3 Brief: EntityStore.shift_chapter_refs

## Goal
给 EntityStore 加一个工具方法 `shift_chapter_refs(from_no: int, delta: int) -> None`，批量偏移所有"章号引用"，用于插入/删除章节时同步实体库（伏笔埋点/兑现章、state change 所在章、appearances、relation.since_chapter、protagonist.updated_from_chapter）。

## Real field names discovered (from reading store.py)
Foreshadow 的 plant_chapter/due_chapter/payoff_chapter 不是 Entity 上的 dataclass 字段，而是存在 `entity.state_attrs: dict[str,str]` 里（键就是这三个字符串，值是 str 类型的整数或空）。其他章号字段：
- `Entity.appearances: list[int]`
- `StateChange.chapter: int | None`
- `Relation.since_chapter: int | None`
- `ProtagonistState.updated_from_chapter: int | None`

## Files to modify
- `backend/app/entities/store.py`（EntityStore 类里加方法）
- `backend/tests/test_entities.py`（追加 `TestShiftChapterRefs` 类）

## Implementation

在 EntityStore 类合适位置（建议放在 `record_appearance` 附近或 `chapter_snapshot` 之前）新增：

```python
def shift_chapter_refs(self, from_no: int, delta: int) -> None:
    """把所有 >= from_no 的章号引用偏移 +delta（删除章时 delta=-1，插入时 +1）。
    先备份再写盘。偏移后若出现章号 <=0 抛 ValueError。
    """
    import shutil, time
    if self.path.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + f".bak.{ts}"))

    def _shift(v: int | None) -> int | None:
        if v is None:
            return None
        if not isinstance(v, int):
            return v
        if v >= from_no:
            nv = v + delta
            if nv <= 0:
                raise ValueError(f"shift_chapter_refs: chapter {v} would become {nv} (<=0)")
            return nv
        return v

    # 1. Entity.appearances + foreshadow state_attrs 三字段
    for e in self._entities.values():
        e.appearances = sorted({_shift(c) for c in e.appearances if isinstance(c, int)})
        if e.type == "foreshadow":
            for k in ("plant_chapter", "due_chapter", "payoff_chapter"):
                v = e.state_attrs.get(k)
                if v is None or v == "":
                    continue
                try:
                    iv = int(v)
                except (ValueError, TypeError):
                    continue
                nv = _shift(iv)
                e.state_attrs[k] = "" if nv is None else str(nv)

    # 2. StateChange.chapter
    for c in self._changes.values():
        c.chapter = _shift(c.chapter)

    # 3. Relation.since_chapter
    for r in self._relations.values():
        r.since_chapter = _shift(r.since_chapter)

    # 4. ProtagonistState.updated_from_chapter
    if self._protagonist is not None:
        self._protagonist.updated_from_chapter = _shift(self._protagonist.updated_from_chapter)

    self._flush()
```

注意：需要确认 EntityStore 里 entities/changes/relations/protagonist 的内部 dict 名字（`self._entities` / `self._changes` / `self._relations` / `self._protagonist`）。先读 store.py EntityStore.__init__ 和 _load 确认真实属性名，如果不同就按真实名字改。如果 foreshadow 不是通过 state_attrs 存的——grep 一下 store.py 看 plant_chapter 到底写在哪里（可能有个独立字段或者 meta dict）。根据上面 grep 结果，ENTITY_ATTRS 里明确列了这三个键是 foreshadow 的属性，因此就是在 state_attrs 里。

## Tests

在 `test_entities.py` 末尾追加 `TestShiftChapterRefs`：

1. `test_shift_foreshadow_and_changes_and_appearances`：
   - 用 tmp_path 建 EntityStore
   - 创建一个 foreshadow 实体：state_attrs={plant_chapter:"1", due_chapter:"3", payoff_chapter:"", intensity:"中"}，appearances=[1,3]
   - 创建一个普通 character 实体：appearances=[1,2,3]
   - 添加一条 StateChange(entity_id=character_id, attr="location", chapter=2)
   - 添加一条 Relation(from_id=..., to_id=..., rel_type="knows_about", since_chapter=2)
   - 设 protagonist.updated_from_chapter=2
   - 调用 shift_chapter_refs(2, +2)
   - 断言：
     - foreshadow plant_chapter="3"（1不变，<2），due_chapter="5"（3→5）
     - foreshadow appearances == [1,5]
     - character appearances == [1,4,5]
     - state change.chapter == 4
     - relation.since_chapter == 4
     - protagonist.updated_from_chapter == 4

2. `test_shift_no_cross_to_zero`：
   - 建 store，加一个 chapter=1 的 state change
   - with pytest.raises(ValueError): shift_chapter_refs(1, -1)
   - 数据不变（检查 change.chapter 仍然是 1；注意：因为 _backup 在前面已经执行，文件被备份但内存里如果中途抛错可能改了一半——你需要让 _shift 先整体检查一遍再修改，或者 try/except 回滚。简单做法：在遍历修改前先做一次"dry-run"验证所有结果 >0，若有 <=0 直接抛错不改动）。

## Workflow
1. 读 store.py EntityStore 类，确认 _entities/_changes/_relations/_protagonist 属性名和 _flush 方法是否存在（名字可能不同）。
2. 先写两个测试。
3. 跑测试 FAIL。
4. 实现 shift_chapter_refs。注意 dry-run 验证避免中途抛错导致内存状态半改：先遍历所有要改的值，用 _shift 计算结果，若有 <=0 则 raise；全部通过后再赋值。
5. 跑测试 PASS。
6. 跑 `python -m pytest tests/test_entities.py -v` 回归。
7. 跑 `python -m pytest tests/ -x -q --ignore=tests/test_providers.py --ignore=tests/test_sessions.py --ignore=tests/test_coc_simulator.py`（跳过已知环境相关失败）确认无新回归。
8. 提交：`feat(entities): shift_chapter_refs 章号偏移工具`。

## Important constraints
- 不要修改其他文件
- 备份文件格式：`entities.json.bak.YYYYMMDD-HHMMSS`（和 DeskStore._backup 一致）
- dry-run 先校验再修改，避免抛错时半改状态
- int 转换时 try/except 防御非法值
- 不要重启后端、不要跑前端
