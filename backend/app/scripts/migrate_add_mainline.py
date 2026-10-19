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
from datetime import datetime
from pathlib import Path

# 确保以 backend/ 为 sys.path 根（python -m app.scripts.xxx 时 cwd 就是 backend，这里加保险）
_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # app/scripts/xxx.py -> parents[2] = backend/
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

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

    # 1. 创建 mainline 卷（create_new_volume 内部已 _flush 落盘）
    mv = vs.create_new_volume(title="蓝星开篇", volume_kind="mainline", start_chapter_no=1)
    print(f"[create] 蓝星开篇卷：volume_no={mv.volume_no}, start_chapter_no={mv.start_chapter_no}")

    # 2. desk renumber：所有 >=1 的章号 +2（仅改内存，不写盘）
    all_nos = desk.all_nos()
    if all_nos:
        min_no = min(all_nos)
        desk.renumber(min_no, +2)
        desk._flush()  # renumber 只改内存，必须显式落盘
        print(f"[renumber-desk] 原第{min_no}~{max(all_nos)}章 → 第{min_no+2}~{max(all_nos)+2}章")

    # 3. entities shift（shift_chapter_refs 内部已 _flush + 备份）
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
        vs.save(v)  # save() 内部 _flush
        print(f"[shift-vol] 卷{v.volume_no} start_chapter_no→{v.start_chapter_no}, rhythm_blueprint 章号+2")

    # 5. 给 mainline 卷加两个空章（第1/2章蓝星）—— ensure 内部自动 _flush
    desk.ensure(mv.start_chapter_no, "第1章", field_no=mv.volume_no, chapter_kind="mainline")
    desk.ensure(mv.start_chapter_no + 1, "第2章", field_no=mv.volume_no, chapter_kind="mainline")
    print(f"[ensure-mainline-ch] 创建第 {mv.start_chapter_no}、{mv.start_chapter_no+1} 章空章（蓝星开篇）")

    return {
        "ok": True,
        "mainline_vol_no": mv.volume_no,
        "bak_dir": str(bak_dir),
    }


def main():
    ap = argparse.ArgumentParser(
        description="给已有项目一键补建蓝星开篇卷（mainline），并将原第1章起所有章节号 +2。"
    )
    ap.add_argument("pid", help="项目 ID（data/projects/ 下的目录名）")
    ap.add_argument("--dry-run", action="store_true", help="只备份不修改，打印迁移计划")
    args = ap.parse_args()
    result = migrate(args.pid, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
