"""旧项目数据迁移脚本：把旧 fields/sessions/volumes 数据结构迁移到新工作台 11 阶段状态机可识别的结构。

用法：
    python -m backend.scripts.migrate_legacy_project [--pid 20261014-c6c432] [--dry-run] [--force]

默认：
    - pid  = 20261014-c6c432（《第七日的亡者》老项目）
    - 执行前自动把项目目录备份到 {pid}.bak.{timestamp}/
    - 幂等保护：volumes/index.json 非空则拒绝执行（--force 强制）
    - --dry-run 只打印计划，不写盘

迁移后期望 phase = "adapt"（或 adapt_empty，取决于 adapted_module 是否为 None / stage_completed.adapt 为 False）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 确保以 backend/ 为 sys.path 根（与 conftest.py 保持一致：支持 `from app.xxx import ...`）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.book.volume import Volume, VolumeStore, _now  # noqa: E402
from app.book.profile import DEFAULT_PREMISE  # noqa: E402


def _dict_card_default(card: str) -> dict:
    """返回 dict 类书卡的默认值（延迟导入避免循环依赖）。"""
    if card == "premise":
        return dict(DEFAULT_PREMISE)
    if card == "tangchi":
        from app.book.tangchi import DEFAULT_TANGCHI
        return dict(DEFAULT_TANGCHI)
    if card == "endgame":
        from app.book.endgame import DEFAULT_ENDGAME
        return dict(DEFAULT_ENDGAME)
    if card == "seven_day_pulse":
        from app.book.pulse import DEFAULT_PULSE
        return {"day_beats": DEFAULT_PULSE}
    if card == "craft":
        from app.book.craft import CRAFT_SKILLS, DEFAULT_GOLDEN3
        return {
            "skills": CRAFT_SKILLS,
            "golden3": DEFAULT_GOLDEN3,
            "book_golden3_notes": [],
            "volume_golden3_notes": [],
        }
    raise ValueError(f"not a dict card: {card}")


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------

def _project_dir(pid: str) -> Path:
    return _BACKEND_ROOT / "data" / "projects" / pid


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict | list, *, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] would write {path} ({len(json.dumps(data, ensure_ascii=False))} chars)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    print(f"  wrote {path}")


# ------------------------------------------------------------
# 1. 备份
# ------------------------------------------------------------

def backup_project(pid: str, *, dry_run: bool) -> Path:
    src = _project_dir(pid)
    if not src.exists():
        raise FileNotFoundError(f"项目目录不存在：{src}")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.parent / f"{pid}.bak.{ts}"
    if dry_run:
        print(f"[dry-run] would backup {src} -> {dst}")
        return dst
    if dst.exists():
        # 同一秒内重名：追加后缀
        dst = src.parent / f"{pid}.bak.{ts}-{datetime.now().microsecond}"
    shutil.copytree(src, dst)
    print(f"[backup] {src} -> {dst}")
    return dst


# ------------------------------------------------------------
# 2. Book 卡：删除空 {} 占位文件，让 BookStore 首次读时自动落 DEFAULT
# ------------------------------------------------------------

# 必须处理的 8 张卡（与 BookStore.CARDS 对齐）
_BOOK_CARDS = (
    "premise", "tangchi", "endgame", "cost_debts",
    "remaining_pages", "finale_notes", "seven_day_pulse", "craft",
)

# 这 3 张是 append-only 流水账卡，BookStore._default() 返回 [] 序列化后只有 3 字节，
# 不足 phase_routes 判定的 10 字节门槛。迁移时需要手动写入带空白填充的合法空数组，
# 一旦流水线追加第一条记录，文件会永久 >10 字节，填充自动失效。
_LIST_CARDS = frozenset(("cost_debts", "remaining_pages", "finale_notes"))

# 12 字节的合法 JSON 空数组（内含 10 个换行符作为空白），json.loads 解析结果为 []
_PADDED_EMPTY_LIST = "[\n\n\n\n\n\n\n\n\n]\n"


def migrate_book_cards(pid: str, *, dry_run: bool) -> list[str]:
    """处理 book/ 下 8 张卡：
    - dict 类卡（premise/tangchi/endgame/seven_day_pulse/craft）：删除空占位，
      让 BookStore._read 在首次访问时自动写入 DEFAULT_*（均 >10 字节）。
    - list 类卡（cost_debts/remaining_pages/finale_notes）：删除空占位后
      手动写入带填充的合法空数组，确保文件 ≥10 字节、能被 json.loads 解析为 []，
      并让 BookStore.cost_debts() 等访问器返回空 list。
    返回被处理的文件名列表。
    """
    book_dir = _project_dir(pid) / "book"
    handled: list[str] = []
    for card in _BOOK_CARDS:
        p = book_dir / f"{card}.json"
        is_empty = False
        if p.exists():
            size = p.stat().st_size
            try:
                raw = p.read_text(encoding="utf-8").strip()
            except OSError:
                raw = ""
            is_empty = (size < 10) and raw in ("{}", "[]", "")
        else:
            # 文件不存在：dict 类卡 BookStore 会自动建，list 类卡需手动写填充
            is_empty = True

        if not is_empty:
            if p.exists():
                print(f"  skip non-empty book card {p.name} (size={p.stat().st_size})")
            else:
                print(f"  skip (unexpected) missing book card {p.name}")
            continue

        if card in _LIST_CARDS:
            if dry_run:
                print(f"  [dry-run] would write padded empty list -> {p.name}")
            else:
                # 直接写带填充的空数组（BookStore._read 能正常 json.loads 为 []）
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(_PADDED_EMPTY_LIST, encoding="utf-8")
                print(f"  wrote padded empty list -> {p.name} (size={p.stat().st_size})")
            handled.append(p.name)
        else:
            # dict 类卡：如果文件为空/是 {} 占位/不存在，直接写 BookStore 默认值
            # （phase 判定直接 stat 读文件大小，必须物理落盘 ≥10 字节，不能依赖 API 惰性触发）
            default_data = _dict_card_default(card)
            if dry_run:
                action = "write default" if not p.exists() else "replace empty with default"
                print(f"  [dry-run] would {action} -> {p.name} ({len(json.dumps(default_data, ensure_ascii=False))} chars)")
            else:
                if p.exists():
                    p.unlink()
                _write_json(p, default_data, dry_run=False)
                print(f"  wrote default dict card -> {p.name} (size={p.stat().st_size})")
            handled.append(p.name)
    return handled


# ------------------------------------------------------------
# 3. Protagonist：entities.json 已经是新结构（顶层含 protagonist 字段）
#    字段名 erosion_level / contamination_level 在新版都存在，
#    只需要确保数组字段齐全。
# ------------------------------------------------------------

_REQUIRED_PROTAGONIST_LISTS = (
    "held_items", "active_crafts", "known_truths", "key_relations",
    "dead_echoes", "god_marks", "brought_items", "blue_star_bonds",
    "kill_record",
)


def migrate_protagonist(pid: str, *, dry_run: bool) -> bool:
    """校验 entities.json，必要时为 protagonist 补空数组字段（缺则补，不覆盖已有）。
    返回 True 表示做了变更。
    """
    ep = _project_dir(pid) / "entities.json"
    if not ep.exists():
        print(f"  [warn] entities.json not found at {ep}, skip protagonist migration")
        return False
    data = _read_json(ep)
    p = data.get("protagonist")
    if not isinstance(p, dict):
        print("  [warn] entities.json has no protagonist dict, will insert empty ProtagonistState")
        p = {}
        data["protagonist"] = p
    changed = False
    for key in _REQUIRED_PROTAGONIST_LISTS:
        if key not in p or not isinstance(p[key], list):
            p[key] = []
            changed = True
    # 顶层必需段
    for key in ("entities", "changes", "relations", "pending_diffs", "applied_diffs"):
        if key not in data:
            data[key] = []
            changed = True
    if changed:
        _write_json(ep, data, dry_run=dry_run)
    else:
        print("  protagonist already well-formed, no change needed")
    return changed


# ------------------------------------------------------------
# 4. Volumes 核心迁移
# ------------------------------------------------------------

def migrate_volumes(pid: str, *, dry_run: bool, force: bool) -> list[Volume]:
    """从 fields/index.json + fields/{no}.json 生成 volumes/index.json + volumes/{no}.json。
    返回创建的 Volume 列表。
    """
    proj = _project_dir(pid)
    fields_index_path = proj / "fields" / "index.json"
    volumes_dir = proj / "volumes"
    volumes_index_path = volumes_dir / "index.json"

    # 幂等保护
    existing_idx = _read_json(volumes_index_path)
    if existing_idx.get("volumes"):
        msg = (f"volumes/index.json 已有 {len(existing_idx['volumes'])} 个卷，"
               "为避免覆盖已迁移数据，默认退出；加 --force 可强制覆盖。")
        if not force:
            raise SystemExit(f"[abort] {msg}")
        print(f"  [warn] {msg} --force 指定，将覆盖 volumes/")

    fields_idx = _read_json(fields_index_path)
    field_entries = fields_idx.get("fields", []) or []
    if not field_entries:
        print("  [warn] fields/index.json 里没有 fields，跳过 volumes 迁移")
        return []

    created: list[Volume] = []
    now = _now()

    # 先构造所有 Volume 对象（dry-run 时不落盘，这里直接用内存对象模拟）
    volumes_index_entries = []
    for fe in field_entries:
        no = int(fe.get("no", 0) or 0)
        if no <= 0:
            continue
        # 读 field 详情
        fdata = _read_json(proj / "fields" / f"{no}.json")
        title = fe.get("title") or fdata.get("title") or f"第{no}卷"
        world_type = fe.get("world_type") or fdata.get("world_type") or ""
        core = fdata.get("core") or None
        field_created_at = fe.get("created_at") or fdata.get("created_at") or now

        v = Volume(
            volume_no=no,
            field_no=no,
            title=title,
            volume_kind="dungeon",
            tone_arc=world_type,
            adapted_module=core if core else None,
            host_character=None,
            entry_point=None,
            stance=None,
            tangchi_story_outline=None,
            simulated_run=None,
            rhythm_blueprint=None,
            scaffold_cards={},
            settle=None,
            interlude_plan=None,
            death_nodes=[],
            climax_nodes=[],
            clue_nodes=[],
            pasted_nodes=[],
            deviation_points=[],
            foreshadows_to_plant=[],
            bonds_to_deepen=[],
            gods_to_hint=[],
            satisfaction_beats=[],
            planned_interludes=3,
            contamination_goal=0,
            endgame_layer_hints={str(i): "未开始" for i in range(1, 8)},
            stage_completed={
                "setup": True,
                "adapt": False,
                "simulate": False,
                "embody": False,
                "scaffold": False,
                "rhythm": False,
            },
            created_at=field_created_at,
            updated_at=now,
        )
        created.append(v)
        volumes_index_entries.append({
            "volume_no": v.volume_no,
            "field_no": v.field_no,
            "title": v.title,
            "tone_arc": v.tone_arc,
            "contamination_goal": v.contamination_goal,
            "planned_interludes": v.planned_interludes,
            "volume_kind": v.volume_kind,
        })

    if dry_run:
        print(f"  [dry-run] would create {len(created)} volume(s):")
        for v in created:
            print(f"    - volume_no={v.volume_no} field_no={v.field_no} "
                  f"title={v.title!r} kind={v.volume_kind} adapted_module={v.adapted_module!r}")
        print(f"  [dry-run] would write volumes/index.json with {len(volumes_index_entries)} entries")
        return created

    # 真正写盘：复用 VolumeStore 的 _flush 逻辑以保证索引格式与运行时一致
    volumes_dir.mkdir(parents=True, exist_ok=True)
    # 先清掉旧的 volumes/{no}.json（force 场景）
    for f in volumes_dir.glob("*.json"):
        if f.name == "index.json":
            continue
        f.unlink()
    vs = VolumeStore(volumes_dir)
    # 重置索引（VolumeStore 初始化会读 index，可能有残留；直接清空后 _flush 会重建）
    for v in created:
        vs._flush(v)
    print(f"  wrote {len(created)} volume file(s) into {volumes_dir}")
    return created


# ------------------------------------------------------------
# 5. Desk：不存在 / 空 {} 即可，DeskStore 首次读自动初始化，这里不动
# ------------------------------------------------------------

def ensure_desk(pid: str, *, dry_run: bool) -> None:
    desk_path = _project_dir(pid) / "desk.json"
    if desk_path.exists():
        print(f"  desk.json exists ({desk_path.stat().st_size} bytes), leave as-is")
        return
    if dry_run:
        print(f"  [dry-run] desk.json not present, DeskStore will auto-init on first read")
    else:
        print(f"  desk.json not present, DeskStore will auto-init on first read (no file created)")


# ------------------------------------------------------------
# 6. 校验
# ------------------------------------------------------------

def verify(pid: str) -> dict:
    """迁移后校验：index 长度、volume 字段、Volume.from_dict 反序列化、phase 判定。
    返回校验结果 dict。
    """
    result: dict = {"ok": True, "errors": [], "warnings": [], "volume": None, "phase": None}
    proj = _project_dir(pid)
    volumes_dir = proj / "volumes"
    idx_path = volumes_dir / "index.json"

    # index 长度
    idx = _read_json(idx_path)
    vols = idx.get("volumes", [])
    if len(vols) < 1:
        result["ok"] = False
        result["errors"].append("volumes/index.json 中 volumes 列表为空")
        return result

    # 读 1.json
    v1_path = volumes_dir / "1.json"
    if not v1_path.exists():
        result["ok"] = False
        result["errors"].append("volumes/1.json 不存在")
        return result
    v1_data = _read_json(v1_path)
    for key, expected in (("field_no", 1), ("volume_no", 1), ("volume_kind", "dungeon")):
        if v1_data.get(key) != expected:
            result["ok"] = False
            result["errors"].append(f"volumes/1.json {key}={v1_data.get(key)!r} != {expected!r}")
    sc = v1_data.get("stage_completed") or {}
    if sc.get("setup") is not True:
        result["ok"] = False
        result["errors"].append("stage_completed.setup != True")

    # Volume.from_dict
    try:
        v = Volume.from_dict(v1_data)
        result["volume"] = v.as_dict()
    except Exception as e:
        result["ok"] = False
        result["errors"].append(f"Volume.from_dict 抛异常：{e}")
        return result

    # Phase 判定（直接复用 phase_routes._determine_phase）
    try:
        from fastapi.testclient import TestClient
        from app.api.main import app
        client = TestClient(app)
        resp = client.get(f"/api/projects/{pid}/phase")
        if resp.status_code == 200:
            result["phase"] = resp.json()
            # book 卡已通过 BookStore._read 自动填充（触发默认值）
        else:
            result["warnings"].append(f"phase endpoint status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        result["warnings"].append(f"phase 接口调用失败（可启动 backend 后再验）：{e}")

    return result


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旧项目迁移到工作台数据结构")
    parser.add_argument("--pid", default="20261014-c6c432", help="项目 ID（默认 20261014-c6c432）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划不写盘")
    parser.add_argument("--force", action="store_true", help="volumes 已存在也强制执行")
    args = parser.parse_args(argv)

    pid = args.pid
    dry_run = args.dry_run
    force = args.force

    print(f"=== Migrate legacy project: pid={pid} dry_run={dry_run} force={force} ===")
    proj = _project_dir(pid)
    if not proj.exists():
        print(f"[fatal] 项目目录不存在：{proj}")
        return 2

    # 1. 备份
    backup_path = backup_project(pid, dry_run=dry_run)

    # 2. Book 卡（空占位删掉让 BookStore 接管默认值）
    print("\n[1/4] migrate_book_cards")
    book_handled = migrate_book_cards(pid, dry_run=dry_run)
    print(f"  book cards handled: {book_handled}")

    # 3. Protagonist（补字段）
    print("\n[2/4] migrate_protagonist")
    migrate_protagonist(pid, dry_run=dry_run)

    # 4. Volumes（核心）
    print("\n[3/4] migrate_volumes")
    created_vols = migrate_volumes(pid, dry_run=dry_run, force=force)

    # 5. Desk（不创建文件）
    print("\n[4/4] ensure_desk")
    ensure_desk(pid, dry_run=dry_run)

    # Summary
    print("\n=== Summary ===")
    print(f"  backup path   : {backup_path}")
    print(f"  book cards    : removed {len(book_handled)} empty placeholders "
          f"({', '.join(book_handled) if book_handled else 'none'})")
    print(f"  volumes       : created {len(created_vols)}")
    for v in created_vols:
        print(f"    vol#{v.volume_no} field#{v.field_no} {v.title!r} "
              f"kind={v.volume_kind} setup={v.stage_completed.get('setup')}")

    # Verify（dry-run 不校验写盘结果）
    if not dry_run:
        print("\n=== Verify ===")
        vr = verify(pid)
        print(f"  verify ok     : {vr['ok']}")
        if vr["errors"]:
            for e in vr["errors"]:
                print(f"  [error] {e}")
        if vr["warnings"]:
            for w in vr["warnings"]:
                print(f"  [warn ] {w}")
        if vr["phase"]:
            print(f"  phase         : {vr['phase']}")
        print("\n下一步：打开前端进入项目即可从『剧本改编』阶段开始（adapt / adapt_empty）。")
    else:
        print("\n[dry-run] 未写盘；去掉 --dry-run 执行真实迁移。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
