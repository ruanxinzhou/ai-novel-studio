"""工作台阶段判定 API：GET /phase + workbench 创建卷端点。

挂载方式：在 main.py 末尾调用
    from .phase_routes import register_phase_routes
    register_phase_routes(app, deps...)
即可。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---- 依赖注入容器（由 main.py 注册时传入）----

@dataclass
class _Deps:
    require_project: Callable
    project_dir: Callable[[str], Path]
    book_for: Callable[[str], Any]
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    book_cards: tuple


_deps: _Deps | None = None


def register_phase_routes(
    app,
    *,
    require_project: Callable,
    project_dir: Callable[[str], Path],
    book_for: Callable[[str], Any],
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
    book_cards: tuple,
) -> None:
    """注册工作台阶段路由到传入的 FastAPI app 上。"""
    global _deps
    _deps = _Deps(
        require_project=require_project,
        project_dir=project_dir,
        book_for=book_for,
        volumes_for=volumes_for,
        desk_for=desk_for,
        book_cards=book_cards,
    )

    router = APIRouter()

    router.add_api_route(
        "/api/projects/{pid}/phase",
        _get_phase, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes",
        _create_dungeon_volume, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/interlude",
        _create_interlude_volume, methods=["POST"],
    )

    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("phase_routes 未调用 register_phase_routes 初始化")
    return _deps


# ---- 请求体模型 ----

class _DungeonVolumeIn(BaseModel):
    title: str = ""


# ---- 阶段判定核心逻辑 ----

def _determine_phase(pid: str) -> dict:
    """根据项目数据判定当前工作台阶段。返回标准 phase 响应 dict。"""
    d = _must_deps()

    project_dir = d.project_dir(pid)

    # 1. setup：8 张卡任意缺失或 < 10 字节
    book_dir = project_dir / "book"
    cards_ok = True
    for card in d.book_cards:
        card_path = book_dir / f"{card}.json"
        if not card_path.exists() or card_path.stat().st_size < 10:
            cards_ok = False
            break
    if not cards_ok:
        return {
            "phase": "setup",
            "volume_no": None,
            "volume_kind": None,
            "chapter_no": None,
            "is_last_chapter": False,
        }

    # 2. 没有任何 volume → adapt_empty
    vs = d.volumes_for(pid)
    vols = vs.all_list()
    if not vols:
        return {
            "phase": "adapt_empty",
            "volume_no": None,
            "volume_kind": None,
            "chapter_no": None,
            "is_last_chapter": False,
        }

    last_vol = max(vols, key=lambda v: v.volume_no)
    desk = d.desk_for(pid)
    all_chapter_nos = desk.all_nos()

    # 3. 杀青判定：最后一卷有 chapters，所有章 finalized，settle 为 None
    if all_chapter_nos and last_vol.settle is None:
        all_finalized = all(bool(desk.get(no) and desk.get(no).finalized_at) for no in all_chapter_nos)
        # 注意：这里 chapters 指 desk 中所有章；更严格地应检查绑定到该卷的章，
        # 但现有数据通过 field_no 绑定，desk 里的章用 field_no 关联卷。
        # 简化：检查 desk 中所有章（因新工作流只有最新卷有未完成章）
        if all_finalized:
            return {
                "phase": "settle",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
            }

    # 4. 间章判定
    if last_vol.volume_kind == "interlude" and last_vol.settle is None:
        # 有未定稿章 → desk
        unfinished = [no for no in all_chapter_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
        if unfinished:
            earliest = min(unfinished)
            max_ch = max(all_chapter_nos) if all_chapter_nos else 0
            return {
                "phase": "desk",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
            }
        # interlude_plan 存在即间章完成；否则 phase='interlude'
        if not last_vol.interlude_plan:
            return {
                "phase": "interlude",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
            }

    # 5. 日更：存在未定稿章
    unfinished = [no for no in all_chapter_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
    if unfinished:
        earliest = min(unfinished)
        max_ch = max(all_chapter_nos) if all_chapter_nos else 0
        ch = desk.get(earliest)
        # 间章（is_interlude）直接进 desk，不经过章前 5 分钟
        if ch and ch.is_interlude:
            return {
                "phase": "desk",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
            }
        # 章前 5 分钟卡：未定稿章没有 brief.selected → 先到 briefing 阶段
        if ch and not (ch.brief and isinstance(ch.brief, dict) and ch.brief.get("selected")):
            return {
                "phase": "briefing",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
            }
        return {
            "phase": "desk",
            "volume_no": last_vol.volume_no,
            "volume_kind": last_vol.volume_kind,
            "chapter_no": earliest,
            "is_last_chapter": (earliest == max_ch),
        }

    # 6. 按 stage_completed 顺序找第一个 False
    stage_order = ["adapt", "simulate", "embody", "scaffold", "rhythm"]
    sc = last_vol.stage_completed or {}
    for stage_key in stage_order:
        if not sc.get(stage_key, False):
            return {
                "phase": stage_key,
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
            }

    # 7. 都 True → briefing
    return {
        "phase": "briefing",
        "volume_no": last_vol.volume_no,
        "volume_kind": last_vol.volume_kind,
        "chapter_no": None,
        "is_last_chapter": False,
    }


# ---- 路由处理函数 ----

def _get_phase(pid: str) -> dict:
    """返回当前项目工作台阶段。"""
    d = _must_deps()
    d.require_project(pid)
    return _determine_phase(pid)


def _create_dungeon_volume(pid: str, body: _DungeonVolumeIn) -> dict:
    """创建新副本卷（dungeon）。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    title = body.title.strip() if body.title else ""
    v = vs.create_new_volume(title=title, volume_kind="dungeon")
    return {"ok": True, "volume_no": v.volume_no, "volume": v.as_dict()}


def _create_interlude_volume(pid: str) -> dict:
    """创建新间章卷（interlude）。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.create_new_volume(title="蓝星间章", volume_kind="interlude")
    return {"ok": True, "volume_no": v.volume_no, "volume": v.as_dict()}
