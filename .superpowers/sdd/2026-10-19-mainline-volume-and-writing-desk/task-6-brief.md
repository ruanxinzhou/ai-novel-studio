# Task 6 Brief: 写作材料按 chapter_kind 分支注入

## Goal
修改 `backend/app/wizard/desk.py` 的 `run_chapter_stage` 函数：
1. 蓝星章（chapter_kind=="mainline"）不注入副本竞技场材料（arena），改为注入主线状态材料（主角状态+上章尾+未回收伏笔）
2. 间章（chapter_kind=="interlude"）也不注入竞技场，改为注入间章材料
3. 副本章（chapter_kind=="dungeon"）保持现状
4. 写作模块选择：mainline 章不注入 coc_horror（保留 obsession_cost + infinite_loop_unit）

## Files
- MODIFY `backend/app/wizard/desk.py`
- ADD test（可选，最小验证）

## Analysis of existing code（从代码中读到）
- auto_modules 判定在 line 324-346：目前用 `is_interlude` 和 `field_no is not None` 判断；要改用 `chapter_kind`。
- arena 注入在 line 348-547：触发条件 `volume_store is not None and getattr(work, "field_no", None) is not None`，需要加上 chapter_kind=="dungeon" 守卫；其他 kind 走新分支。
- run_chapter_stage 已经接收 entity_store 参数（line 298、line 595），可以直接用。
- previous_tail 函数已在 wizard __init__ 里导入（看 line 71 `previous_tail`）。
- book_store.render_all() 已经注入所有书级硬卡（包括主角设定），但主线章需要更精简的"主角状态+上章尾+未回收伏笔"额外 material。

## Detailed changes

### 1. auto_modules 按 chapter_kind 分支（替换 line 324-329 附近）

```python
if book_store is not None:
    auto_modules: list[str] = ["obsession_cost"]  # 所有章节都要代价意识
    ch_kind = getattr(work, "chapter_kind", None) or ("interlude" if getattr(work, "is_interlude", False) else "dungeon")
    if ch_kind == "interlude":
        auto_modules.append("interlude_relief")
    elif ch_kind == "dungeon":
        auto_modules.append("coc_horror")
        if stage == "plot":
            auto_modules.append("infinite_loop_unit")
    elif ch_kind == "mainline":
        # 蓝星章：无 coc_horror，但 plot 阶段保留无限循环单元（唐迟理性/规则敏感）
        if stage == "plot":
            auto_modules.append("infinite_loop_unit")
    # 下面 already/use_digest 循环保持不变
```

### 2. arena 注入分支（替换 line 347-547 的大 if 块开头）

把 line 348 的判断：
```python
if volume_store is not None and getattr(work, "field_no", None) is not None:
```
改成：
```python
ch_kind = getattr(work, "chapter_kind", None) or ("interlude" if getattr(work, "is_interlude", False) else "dungeon")
if volume_store is not None and getattr(work, "field_no", None) is not None:
    vol = volume_store.get(int(work.field_no))
    if vol is None:
        pass  # 卷不存在，跳过
    elif ch_kind == "dungeon":
        # ---- 原有竞技场注入逻辑（line 351-544 的 arena_lines 拼装）----
        # （保持所有现有代码不变，只是缩进一层）
        arena_lines = []
        ...  # 原 line 352-544 的所有拼装代码
        if arena_lines:
            content = ...
            materials.append(Material(source_id=f"volume:v{vol.volume_no}:arena", ...))
    elif ch_kind == "mainline":
        _inject_mainline_context(materials, vol, chapter, work, entity_store, desk, stage)
    elif ch_kind == "interlude":
        _inject_interlude_context(materials, vol, entity_store, stage)
```

具体重构方式：
1. 把 `vol = volume_store.get(int(work.field_no))` 提前到 if 块开头（原来在 line 350）。
2. 把原 arena_lines 整个大段（line 351-544）包到 `if vol is not None and ch_kind == "dungeon":` 里。
3. 在模块顶层（run_chapter_stage 函数之外）新增两个 helper 函数。

### 3. 新增 `_inject_mainline_context` 函数（文件内顶层）

```python
def _inject_mainline_context(materials: list, vol, chapter: int, work, entity_store, desk, stage: str):
    """蓝星主线章注入：主角状态 + 上一章尾 + 未回收伏笔前10。不注入任何副本相关材料。"""
    lines: list[str] = []
    lines.append("【本卷类型】蓝星开篇卷（day=0，尚未进入任何副本）")
    lines.append(f"【本章定位】第{chapter}章（全书第{chapter}章）——{work.title or ''}")
    lines.append("【写作红线】")
    lines.append("- 场景限定在蓝星现代都市（写字楼/出租屋/地铁/便利店/医院/街道等），不许出现任何副本/克苏鲁神明/魂穿宿主/NPC")
    lines.append("- 主角唐迟：理性、冷静、善归纳、对规则边界敏感的程序员；但禁止出现任何编程职业黑话（debug/Bug/栈/日志/变量/指针/越权/溢出/短路/重定向/死锁/卡bug 等）")
    lines.append("- 第1章不许穿越；第2章末才穿越坠落")

    # 主角状态（唐迟）
    if entity_store is not None:
        try:
            proto = entity_store.protagonist
            if proto is not None:
                state_bits = []
                if proto.current_location:
                    state_bits.append(f"当前位置：{proto.current_location}")
                if proto.contamination_level:
                    state_bits.append(f"累积污染度：{proto.contamination_level}")
                bb = proto.blue_star_bonds or []
                if bb:
                    bond_lines = [f"- {b.get('name','?')}（{b.get('relation','')}，深度{b.get('depth','')}）{b.get('note','')}" for b in bb[:5]]
                    state_bits.append("蓝星羁绊：\n" + "\n".join(bond_lines))
                if state_bits:
                    lines.append("【唐迟当前状态】\n" + "\n".join(state_bits))
        except Exception:
            pass

    # 上一章尾（1500字）
    try:
        from . import previous_tail
        if chapter > 1 and desk is not None:
            tail = previous_tail(desk, chapter - 1, tail_chars=1500)
            if tail:
                lines.append(f"【上一章末尾（第{chapter-1}章，供衔接）】\n{tail}")
    except Exception:
        pass

    # 未回收伏笔前 10 条
    if entity_store is not None:
        try:
            unpaids = [e for e in entity_store.entities() if e.type == "foreshadow" and e.status == "planted"]
            if unpaids:
                bits = []
                for e in unpaids[:10]:
                    plant = e.state_attrs.get("plant_chapter", "?")
                    bits.append(f"- {e.name}（埋于第{plant}章）：{e.summary}")
                lines.append("【未回收伏笔（前10）】\n" + "\n".join(bits))
        except Exception:
            pass

    content = "\n\n".join(l for l in lines if l and l.strip())
    materials.append(Material(
        source_id=f"volume:v{vol.volume_no}:mainline",
        family=MaterialFamily.FINALIZED,
        content=content,
        title=f"蓝星主线·第{vol.volume_no}卷{('（' + (vol.title or '') + '）') if vol.title else ''}",
    ))
```

### 4. 新增 `_inject_interlude_context` 函数

```python
def _inject_interlude_context(materials: list, vol, entity_store, stage: str):
    """蓝星间章注入：杀青残页 + 侵蚀度 + 蓝星钩子。"""
    lines: list[str] = []
    lines.append("【本卷类型】蓝星间章（两场副本之间的现实缓冲）")
    # 上一场杀青归账
    settle = vol.settle or {}
    if settle:
        summary = settle.get("summary") or settle.get("text") or ""
        if summary:
            lines.append(f"【上一场杀青归账摘要】\n{str(summary)[:800]}")
    # 唐迟状态
    if entity_store is not None:
        try:
            proto = entity_store.protagonist
            if proto is not None:
                bits = []
                bits.append(f"累积污染度：{proto.contamination_level}")
                if proto.current_location:
                    bits.append(f"当前位置：{proto.current_location}")
                bb = proto.blue_star_bonds or []
                if bb:
                    bits.append("蓝星羁绊：" + "、".join(b.get("name","?") for b in bb[:3]))
                if proto.brought_items:
                    bits.append("带回复本物品：" + "、".join(i.get("name","?") for i in proto.brought_items[:5]))
                lines.append("【唐迟状态】\n" + "\n".join(f"- {b}" for b in bits))
        except Exception:
            pass
    content = "\n\n".join(l for l in lines if l and l.strip())
    materials.append(Material(
        source_id=f"volume:v{vol.volume_no}:interlude",
        family=MaterialFamily.FINALIZED,
        content=content,
        title=f"蓝星间章·第{vol.volume_no}卷",
    ))
```

### 5. entity_store 方法检查
helper 里用 `entity_store.entities()` 返回所有 Entity 列表；需要确认 entity_store 暴露的方法名。常见方法名可能是 `list_entities()` / `all()` / 直接 `list(self._entities.values())`。读 entity_store 确认；如果没有 entities() 方法就用 `list_entities()` 或者直接用 `entity_store._entities.values()`（如果是内部属性）或其他真实方法。**读代码确认后使用正确方法**。

同样 `entity_store.protagonist` 属性需要确认是直接 `.protagonist` 还是方法 `get_protagonist()`。

如果 `previous_tail` 不是一个函数而是其他形式，读 wizard/__init__.py 确认它的签名；如果它已经被 import 了（line 71 `from ..wizard import ... previous_tail`），直接用。

### 6. Test（最小）

在 test_context_assembler.py 或 test_wizard.py 加一个简单单测？其实 run_chapter_stage 调用 LLM 难测，这里跳过测试；改为：
- py_compile 检查
- 快速 import 验证：`python -c "from app.wizard.desk import run_chapter_stage, _inject_mainline_context, _inject_interlude_context; print('OK')"`
- 跑现有回归 `python -m pytest tests/test_wizard.py tests/test_state_ledger.py tests/test_context_assembler.py -q` 确保没破坏现有逻辑。

## Workflow
1. 读 desk.py 完整 run_chapter_stage（line 284-547）确认缩进和结构。
2. 读 entity store 确认 entities 列表方法名和 protagonist 访问方式：`grep -n "def " backend/app/entities/store.py | head -40` 看公共方法；protagonist 在 store.py line 228+ 是 ProtagonistState 类型，EntityStore 里它存在 self.protagonist 还是方法？
3. 读 wizard/__init__.py 确认 previous_tail 是否可直接导入。
4. 在 desk.py 顶部 run_chapter_stage 之前定义两个 helper 函数 _inject_mainline_context 和 _inject_interlude_context。
5. 改 run_chapter_stage：
   - auto_modules 判定改按 ch_kind 分支
   - volume_store 注入块把原 arena 逻辑包到 ch_kind=="dungeon" 里，elif mainline/interlude 调 helper
6. py_compile：`python -m py_compile app/wizard/desk.py`
7. import 验证：`python -c "from app.wizard.desk import run_chapter_stage; print('import ok')"`
8. 快速回归：`python -m pytest tests/test_wizard.py tests/test_state_ledger.py tests/test_context_assembler.py -q`
9. 提交：`feat(desk-context): 按 chapter_kind 分支注入材料，mainline 不注入副本竞技场`

## Important constraints
- 不要改动其他文件
- 不要重启后端、不跑前端
- helper 函数里所有 entity_store/book_store/desk 访问必须包 try/except，失败不阻塞主流程（保持和 arena 注入一致的容错风格：line 545 `except Exception: pass`）
- 蓝星章红线必须显式写"不许出现编程黑话"，和前面 embody/stages 里的约束一致
- 不要在 mainline 材料里出现 adapted_premise/host_character/entry_point/death_nodes 等任何副本字段
