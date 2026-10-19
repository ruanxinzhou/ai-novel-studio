# Task 7 Brief: phase 判定（mainline-waiting/mainline-brief/writing-desk）+ 一键迁移脚本

## Goal
1. 修改 phase_routes.py `_determine_phase`：在"没有任何 volume"之后加 mainline 判定；新增 writing-desk 视图覆盖。
2. 新建迁移脚本 `backend/scripts/migrate_add_mainline.py`：给已有项目补建蓝星开篇卷，把原第1章变第3章。

## Files
- MODIFY `backend/app/api/phase_routes.py`
- CREATE `backend/scripts/migrate_add_mainline.py`
- MODIFY `backend/scripts/__init__.py`（如果需要，可以为空）

## Part 1: phase_routes.py 修改

### Current structure（已读）
- _determine_phase(pid, target_volume_no=None) 在 line 86。
- 段 1（line 96-129）：setup 检查
- 段 2（line 131-142）：没有任何 volume → adapt_empty
- line 144: `last_vol = max(vols, key=lambda v:v.volume_no)`
- 段 3（line 171-192）：view_override 分支
- line 194: `desk = d.desk_for(pid)`
- 段 4+：target_ch_nos 计算 / is_reviewing / 各阶段判定

### Changes

#### A. 在"2. 没有任何 volume → adapt_empty"（line 131-142）之后插入 mainline 判定

把 line 144 的 `last_vol = max(...)` 之前加入：

```python
# ---- mainline 判定 ----
mainline_vols = [v for v in vols if v.volume_kind == "mainline"]
desk = d.desk_for(pid)   # 提前拿 desk
if not mainline_vols:
    return {
        "phase": "mainline-waiting",
        "volume_no": None, "volume_kind": None, "chapter_no": None,
        "is_last_chapter": False, "is_reviewing": False,
    }
mv = mainline_vols[0]  # 只看第一个 mainline 卷
ml_ch_nos = []
for no in sorted(desk.all_nos()):
    ch = desk.get(no)
    if ch and ch.field_no == mv.volume_no:
        ml_ch_nos.append(no)
ml_finalized = sum(1 for n in ml_ch_nos if desk.get(n) and desk.get(n).finalized_at)
if ml_finalized < 2:
    # 找第一个未写完的章
    unfinished = [n for n in ml_ch_nos if not (desk.get(n) and desk.get(n).finalized_at)]
    cno = min(unfinished) if unfinished else (max(ml_ch_nos) + 1 if ml_ch_nos else mv.start_chapter_no)
    if not desk.get(cno):
        desk.ensure(cno, title="", field_no=mv.volume_no, chapter_kind="mainline")
    return {
        "phase": "mainline-brief",
        "volume_no": mv.volume_no,
        "volume_kind": "mainline",
        "chapter_no": cno,
        "is_last_chapter": (cno == mv.start_chapter_no + 1),
        "is_reviewing": False,
    }
# mainline 已写完 2 章：继续走后续（dungeon 卷判定），让 last_vol = max(vols) 选择最新卷推进
```

注意：
- 需要把 `desk = d.desk_for(pid)` 从 line 194 上移到这里，避免后面重复。后面 line 194 的原 desk 赋值删掉或保留都可以（desk() 应该是幂等返回同一实例）。
- 如果没有 ml_ch_nos（mainline 卷刚创建，还没有章），自动 ensure(start_chapter_no) 创建第 1 章，chapter_kind="mainline"。
- 如果 ml_finalized < 2：返回 mainline-brief，cno 是第一个未定稿的章。is_last_chapter 当 cno == start_chapter_no+1（第2章）时为 True。
- 如果 ml_finalized >= 2：不 return，继续走到后续 last_vol/阶段判定（这样会按最新的 dungeon 卷推进，符合预期）。

#### B. 把 line 194 `desk = d.desk_for(pid)` 删掉（已上移），避免未定义错误——保留也可以，幂等。

#### C. 在 view_override 的 _VIEW_TO_PHASE 里加 "writing-desk"

在 line 175-183 的 `_VIEW_TO_PHASE = { ... }` 字典里加：
```python
"writing-desk": "writing-desk",
```

在 view_override 判定之前（line 184 之前）加一个优先分支：
```python
# writing-desk 视图覆盖（全局自由写作台）
view_override = (sc.get("_view") if isinstance(sc, dict) else None)
if view_override == "writing-desk":
    return {
        "phase": "writing-desk",
        "volume_no": target_vol.volume_no if 'target_vol' in locals() else None,
        "volume_kind": None, "chapter_no": None,
        "is_last_chapter": False, "is_reviewing": False,
    }
```
注意 target_vol 在这一段之前还没定义（在 line 161 `target_vol = last_vol`），所以需要调整顺序：把 writing-desk 判定**放到 view_override 段的最前**，此时 target_vol/last_vol 已经定义了（last_vol 在段 2 之后、target_vol 在 is_reviewing 判定里）。

更稳妥：把 writing-desk 的判定放到 `_determine_phase` 函数最前（setup 检查之后），用专门参数控制——但前端 Task 10 用的是本地 freeView 状态而不是 _view 覆盖。所以简单做法：**在 _VIEW_TO_PHASE 加 "writing-desk" 键即可**，不必额外加优先分支——因为 view_override 如果是 "writing-desk"，就走 line 184 的 if 分支返回 {phase:"writing-desk", ...}。

Wait，看现有代码 line 184-192：如果 view_override in _VIEW_TO_PHASE，直接 return 对应 phase。所以只要在 _VIEW_TO_PHASE 加一行 `"writing-desk": "writing-desk"` 就够了，**不用额外分支**。

但问题是：writing-desk 是"全局"的，不需要 target_vol。现有返回结构会带 volume_no=target_vol.volume_no，这其实没关系——前端 Task 10 看 phase=="writing-desk" 就渲染 WritingDeskView，忽略 volume_no。

所以简化：**只加一行到 _VIEW_TO_PHASE**。

#### D. 但是 plan 说"_view="writing-desk"覆盖返回 phase="writing-desk""，而前端 Task 10 实际用的是本地 freeView。后端只要让 `/api/.../back-to-adapt` 风格的接口能写 _view="writing-desk" 就行——不过本版不做那个接口（前端本地切），所以这个 phase 判定主要是防御性的。_VIEW_TO_PHASE 加一行即可。

## Part 2: 迁移脚本

新建 `backend/scripts/migrate_add_mainline.py`：

```python
"""给已有项目一键补建蓝星开篇卷（CLI）。

用法：
    cd backend
    python -m app.scripts.migrate_add_mainline <pid>
    # 或
    python -m app.scripts.migrate_add_mainline <pid> --dry-run

幂等：若已存在 volume_kind=="mainline" 的卷则打印跳过。
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加 backend 到 path（python -m 方式运行时不需要，但加了保险）
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from app import config as _config  # noqa: E402
from app.projects import ProjectStore  # noqa: E402
from app.book.volume import VolumeStore  # noqa: E402
from app.wizard.store import DeskStore  # noqa: E402
from app.entities.store import EntityStore  # noqa: E402


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def migrate(pid: str, dry_run: bool = False) -> dict:
    projects_dir = _config.REPO / "backend" / "data" / "projects"
    proj_dir = projects_dir / pid
    if not proj_dir.exists():
        raise SystemExit(f"项目 {pid} 不存在：{proj_dir}")

    ps = ProjectStore(projects_dir)
    if not ps.get(pid):
        raise SystemExit(f"项目 {pid} 未在 ProjectStore 注册")

    vs = VolumeStore(proj_dir / "volumes")
    desk = DeskStore(proj_dir / "desk.json")
    es = EntityStore(proj_dir / "entities.json")

    # 幂等检查
    existing_mainline = [v for v in vs.all_list() if v.volume_kind == "mainline"]
    if existing_mainline:
        print(f"[skip] 已存在 mainline 卷（卷号 {existing_mainline[0].volume_no}），跳过迁移。")
        return {"skipped": True, "mainline_vol_no": existing_mainline[0].volume_no}

    # 备份
    bak_dir = proj_dir / f"migrate-mainline.bak.{_ts()}"
    bak_dir.mkdir(parents=True, exist_ok=True)
    print(f"[backup] 备份到 {bak_dir}")
    for fn in ("desk.json", "entities.json"):
        src = proj_dir / fn
        if src.exists():
            shutil.copy2(src, bak_dir / fn)
    vols_dir = proj_dir / "volumes"
    if vols_dir.exists():
        shutil.copytree(vols_dir, bak_dir / "volumes")

    if dry_run:
        print("[dry-run] 不做实际修改，退出。")
        return {"dry_run": True, "bak_dir": str(bak_dir)}

    # 1. 创建 mainline 卷
    mv = vs.create_new_volume(title="蓝星开篇", volume_kind="mainline", start_chapter_no=1)
    print(f"[create] 蓝星开篇卷：volume_no={mv.volume_no}, start_chapter_no={mv.start_chapter_no}")

    # 2. desk renumber：所有 >=1 的章号 +2
    all_nos = desk.all_nos()
    if all_nos:
        min_no = min(all_nos)
        desk.renumber(min_no, +2)
        print(f"[renumber-desk] 原第{min_no}~{max(all_nos)}章 → 第{min_no+2}~{max(all_nos)+2}章")

    # 3. entities shift
    if all_nos:
        es.shift_chapter_refs(min(all_nos), +2)
        print(f"[shift-entities] 实体库章号引用 +2")

    # 4. 遍历已有 dungeon/interlude 卷，把 rhythm_blueprint.chapter_plan[*].chapter 全 +2，start_chapter_no 也 +2
    for v in vs.all_list():
        if v.volume_kind == "mainline":
            continue
        # 更新 start_chapter_no
        v.start_chapter_no = (v.start_chapter_no or 1) + 2
        bp = v.rhythm_blueprint
        if isinstance(bp, dict):
            plan = bp.get("chapter_plan")
            if isinstance(plan, list):
                for p in plan:
                    if isinstance(p, dict) and isinstance(p.get("chapter"), int):
                        p["chapter"] = p["chapter"] + 2
        vs.save(v)
        print(f"[shift-vol] 卷{v.volume_no} start_chapter_no→{v.start_chapter_no}, rhythm_blueprint 章号+2")

    # 5. 给 mainline 卷加两个空章（第1/2章蓝星）
    desk.ensure(mv.start_chapter_no, "第1章", field_no=mv.volume_no, chapter_kind="mainline")
    desk.ensure(mv.start_chapter_no + 1, "第2章", field_no=mv.volume_no, chapter_kind="mainline")
    print(f"[ensure-mainline-ch] 创建第 {mv.start_chapter_no}、{mv.start_chapter_no+1} 章空章（蓝星开篇）")

    return {
        "ok": True,
        "mainline_vol_no": mv.volume_no,
        "bak_dir": str(bak_dir),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pid", help="项目 ID（目录名）")
    ap.add_argument("--dry-run", action="store_true", help="只备份不修改")
    args = ap.parse_args()
    result = migrate(args.pid, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

### Notes
- EntityStore 构造器接收什么 path？读 EntityStore.__init__ 确认。如果 EntityStore 路径不是 `proj_dir/"entities.json"` 而是其他（比如 `proj_dir/"entities"/...`），根据实际调整。
- DeskStore 路径确认为 `proj_dir/"desk.json"`（从代码 line 262 可见）。
- VolumeStore 路径为 `proj_dir/"volumes"`（从 volume.py line 264 可见）。
- VolumeStore.save() 方法会 _flush；vs.create_new_volume 已保存。
- es.shift_chapter_refs 内部会 _flush，desk.renumber 不 _flush（Task 2 里是纯内存操作）——需要显式调 `desk._flush()` 落盘。
- vs.save(v) 会更新卷文件。

### Verify
- 语法检查：`cd backend ; python -m py_compile app/scripts/migrate_add_mainline.py`（如果路径错了调整；注意 scripts 目录在 backend/scripts/，所以包路径应该是 `app.scripts.migrate_add_mainline`，前提是 backend 是 PYTHONPATH 根）
- 帮助：`cd backend ; python -m app.scripts.migrate_add_mainline --help`

### 不实际跑迁移（留给 Task 11 端到端验证）
本 Task 只写脚本并验证语法，不实际对现有项目跑——Task 11 会重启后端后用真实项目测试。

## Workflow
1. 读 phase_routes.py _determine_phase 完整结构（line 86-493）。
2. 按 Part 1 修改：
   - 在"没有任何 volume→adapt_empty"之后、`last_vol = max(...)` 之前插入 mainline 判定块（注意把 `desk = d.desk_for(pid)` 提前到这里，后面 194 行的 desk 赋值可以保留幂等）
   - 在 _VIEW_TO_PHASE 加 `"writing-desk": "writing-desk"`
3. 读 EntityStore.__init__ 确认路径参数。
4. 新建 scripts/migrate_add_mainline.py（内容如上）。
5. 检查 backend/scripts/__init__.py 是否存在（不存在则新建空文件）。
6. py_compile 检查：`cd backend ; python -m py_compile app/api/phase_routes.py app/scripts/migrate_add_mainline.py`
7. import 验证：`python -c "from app.api.phase_routes import _determine_phase; print('ok')"`
8. 跑：`python -m pytest tests/test_wizard.py tests/test_entities.py -q`
9. 跑迁移脚本 help：`python -m app.scripts.migrate_add_mainline --help`（cd backend）
10. 提交：`feat(phase): mainline-waiting/brief 阶段判定 + writing-desk 相位 + 一键补建开篇卷迁移脚本`

## Important constraints
- 不要改动其他文件
- 不重启后端，不跑前端
- 不实际对真实项目跑迁移（只跑 --help 和 --dry-run 验证脚本可执行）
- phase 返回结构保持和现有一致：{phase, volume_no, volume_kind, chapter_no, is_last_chapter, is_reviewing}
- mainline 卷刚创建还没有章时，自动 ensure 空第 1 章
