"""写作章台（desk）REST 端点：插入/删除/改字段/拖拽/书脊。

挂载方式：main.py 末尾调用
    from .desk_routes import register_desk_routes
    register_desk_routes(app, require_project=..., volumes_for=..., desk_for=..., entities_for=...)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    entities_for: Callable[[str], Any]


_deps: _Deps | None = None


def register_desk_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
    entities_for: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        entities_for=entities_for,
    )

    router = APIRouter(tags=["desk"])
    prefix = "/api/projects/{pid}/desk"
    wb_prefix = "/api/projects/{pid}/workbench/desk"

    # insert
    router.add_api_route(f"{prefix}/insert", _desk_insert, methods=["POST"])
    router.add_api_route(f"{wb_prefix}/insert", _desk_insert, methods=["POST"])
    # delete
    router.add_api_route(f"{prefix}/{{no}}", _desk_delete, methods=["DELETE"])
    router.add_api_route(f"{wb_prefix}/{{no}}", _desk_delete, methods=["DELETE"])
    # patch
    router.add_api_route(f"{prefix}/{{no}}", _desk_patch, methods=["PATCH"])
    router.add_api_route(f"{wb_prefix}/{{no}}", _desk_patch, methods=["PATCH"])
    # reorder
    router.add_api_route(f"{prefix}/reorder", _desk_reorder, methods=["POST"])
    router.add_api_route(f"{wb_prefix}/reorder", _desk_reorder, methods=["POST"])
    # spine（仅挂在原 prefix 下）
    router.add_api_route(f"{prefix}/spine", _desk_spine, methods=["GET"])

    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("desk_routes 未调用 register_desk_routes 初始化")
    return _deps


# ---- 请求体 ----

class _InsertIn(BaseModel):
    after_no: int = 0
    title: str = "新章节"
    field_no: int | None = None
    chapter_kind: str = "dungeon"
    day: int | None = None


class _PatchIn(BaseModel):
    title: str | None = None
    field_no: int | None = None
    chapter_kind: str | None = None
    day: int | None = None


class _ReorderIn(BaseModel):
    orders: list[dict] = []


# ---- handlers ----

def _desk_insert(pid: str, body: _InsertIn = Body(...)) -> dict:
    """POST /desk/insert —— 在 after_no 之后插入新章节。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    desk = d.desk_for(pid)
    es = d.entities_for(pid)

    after_no = int(body.after_no or 0)
    kind = body.chapter_kind or "dungeon"
    if kind not in ("mainline", "dungeon", "interlude"):
        raise HTTPException(400, f"未知 chapter_kind：{kind}")
    field_no = body.field_no

    if field_no is None:
        all_vols = vs.all_list()
        if kind == "mainline":
            ml = [v for v in all_vols if v.volume_kind == "mainline"]
            if not ml:
                raise HTTPException(400, "当前没有主线卷（mainline），无法插入主线章")
            field_no = ml[0].volume_no
        elif kind == "interlude":
            if not all_vols:
                raise HTTPException(400, "当前没有任何卷，无法插入间章")
            field_no = max(v.volume_no for v in all_vols)
        else:  # dungeon
            dun = [v for v in all_vols if v.volume_kind == "dungeon"]
            if not dun:
                raise HTTPException(400, "当前没有副本卷（dungeon），无法插入副本章")
            field_no = max(v.volume_no for v in dun)

    title = (body.title or "新章节").strip() or "新章节"
    try:
        new_no = desk.insert_after(after_no, title=title, field_no=field_no, chapter_kind=kind)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 根据 kind 二次绑定字段
    try:
        if kind == "dungeon" and body.day is not None:
            desk.assign_field(new_no, field_no=field_no, day=body.day)
        elif kind == "interlude":
            desk.assign_field(new_no, field_no=field_no, is_interlude=True)
        elif kind == "mainline":
            ch = desk.get(new_no)
            if ch is not None:
                ch.field_no = field_no
                ch.chapter_kind = "mainline"
                ch.is_interlude = False
                desk._flush()
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 实体库章号偏移：保持与 insert_after 内 renumber 一致
    try:
        if after_no == 0:
            es.shift_chapter_refs(1, +1)
        else:
            es.shift_chapter_refs(after_no + 1, +1)
    except ValueError as e:
        raise HTTPException(400, f"实体库偏移失败：{e}")

    ch = desk.get(new_no)
    return {"ok": True, "chapter_no": new_no, "chapter": ch.as_dict() if ch else None}


def _desk_delete(pid: str, no: int, force: bool = Query(False)) -> dict:
    """DELETE /desk/{no} —— 删除指定章节。"""
    d = _must_deps()
    d.require_project(pid)
    desk = d.desk_for(pid)
    es = d.entities_for(pid)

    try:
        desk.delete(no, force=bool(force))
    except ValueError as e:
        msg = str(e)
        if "已定稿" in msg:
            raise HTTPException(400, msg)
        raise HTTPException(404, msg)

    try:
        es.shift_chapter_refs(no + 1, -1)
    except ValueError as e:
        raise HTTPException(400, f"实体库偏移失败：{e}")

    desk.renumber(no + 1, -1)
    desk._flush()

    return {"ok": True}


def _desk_patch(pid: str, no: int, body: _PatchIn = Body(...)) -> dict:
    """PATCH /desk/{no} —— 修改章节元数据（title/field_no/chapter_kind/day）。"""
    d = _must_deps()
    d.require_project(pid)
    desk = d.desk_for(pid)

    ch = desk.get(no)
    if ch is None:
        raise HTTPException(404, f"第 {no} 章不存在")

    try:
        ch2 = desk.move(
            no,
            new_field_no=body.field_no,
            new_chapter_kind=body.chapter_kind,
            new_day=body.day,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    if body.title is not None:
        ch2.title = (body.title or "").strip()
        desk._flush()

    return {"ok": True, "chapter": ch2.as_dict()}


def _desk_reorder(pid: str, body: _ReorderIn = Body(...)) -> dict:
    """POST /desk/reorder —— 拖拽改序（下一版本支持）。"""
    _ = body
    d = _must_deps()
    d.require_project(pid)
    raise HTTPException(501, "下一版本支持拖拽改序")


def _desk_spine(pid: str) -> dict:
    """GET /desk/spine —— 返回书脊：所有卷 + 所有章节扁平列表。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    desk = d.desk_for(pid)

    volumes_out = []
    for v in vs.all_list():
        vd = v.as_dict()
        volumes_out.append({
            "volume_no": vd.get("volume_no"),
            "field_no": vd.get("field_no"),
            "title": vd.get("title"),
            "volume_kind": vd.get("volume_kind"),
            "start_chapter_no": vd.get("start_chapter_no"),
            "stage_completed": vd.get("stage_completed"),
        })

    chapters_out = []
    all_vols = vs.all_list()
    # 预先建立 chapter → volume_no 的归属映射：
    #   mainline   → 第一个 volume_kind=="mainline" 的卷
    #   interlude  → 按 field_no 匹配 volume_kind=="interlude" 的卷；找不到则取最后一个 interlude 卷
    #   dungeon    → field_no == volume_no
    ml_vol = next((v for v in all_vols if v.volume_kind == "mainline"), None)
    interlude_vols = [v for v in all_vols if v.volume_kind == "interlude"]
    field_to_interlude = {v.field_no: v.volume_no for v in interlude_vols if getattr(v, "field_no", None) is not None}
    last_interlude_no = interlude_vols[-1].volume_no if interlude_vols else None

    def _resolve_volume_no(ch) -> int | None:
        cd = ch.as_dict()
        kind = cd.get("chapter_kind") or ""
        if kind == "mainline":
            return ml_vol.volume_no if ml_vol else None
        if cd.get("is_interlude"):
            fn = cd.get("field_no")
            if fn is not None and fn in field_to_interlude:
                return field_to_interlude[fn]
            return last_interlude_no
        # dungeon / 默认：field_no 就是 volume_no
        return cd.get("field_no")

    for c in desk.all_sorted():
        cd = c.as_dict()
        text = (
            (cd.get("polish") or {}).get("text")
            or (cd.get("draft") or {}).get("text")
            or (cd.get("plot") or {}).get("text")
            or ""
        )
        chapters_out.append({
            "no": cd.get("no"),
            "title": cd.get("title"),
            "field_no": cd.get("field_no"),
            "volume_no": _resolve_volume_no(c),
            "chapter_kind": cd.get("chapter_kind"),
            "day": cd.get("day"),
            "is_interlude": cd.get("is_interlude"),
            "finalized_at": cd.get("finalized_at"),
            "payoff": cd.get("payoff"),
            "word_count": len(text or ""),
        })

    return {"volumes": volumes_out, "chapters": chapters_out}
