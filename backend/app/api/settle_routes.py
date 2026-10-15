"""杀青（settle）端点：伏笔检测 → 归账 apply → finalize 完成。

挂载方式：main.py 末尾调用
    from .settle_routes import register_settle_routes
    register_settle_routes(app, require_project=..., volumes_for=..., desk_for=..., entities_for=...)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.settle.detector import detect_foreshadows


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    entities_for: Callable[[str], Any]


_deps: _Deps | None = None


def register_settle_routes(
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

    router = APIRouter()
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/settle/detect",
        _detect, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/settle/apply",
        _apply, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/settle/finalize",
        _finalize, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("settle_routes 未调用 register_settle_routes 初始化")
    return _deps


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- 请求体模型 ----

class _ForeshadowDecision(BaseModel):
    id: str
    action: str             # "pay" | "drop" | "carry"
    note: str = ""


class _SettleApplyIn(BaseModel):
    foreshadow_decisions: list[_ForeshadowDecision] = []
    contamination_delta: int = 0
    kill_record_line: str = ""
    crafts: list = []
    echoes: list = []
    god_marks: list = []
    brought_items: list = []
    bonds_delta: list = []
    field_count_inc: int = 1


# ---- 处理函数 ----

def _detect(pid: str, vol_no: int) -> dict:
    """GET 检测本卷未回收伏笔。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    es = d.entities_for(pid)
    desk = d.desk_for(pid)
    foreshadows = detect_foreshadows(es, desk, field_no=v.field_no)
    return {"ok": True, "volume_no": vol_no, "foreshadows": foreshadows}


def _apply(pid: str, vol_no: int, body: _SettleApplyIn) -> dict:
    """POST 应用杀青归账：更新伏笔状态 + 写实体 diff + 写 v.settle。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")

    es = d.entities_for(pid)
    desk = d.desk_for(pid)

    # 找本卷最大章号（绑定到 field_no 的章）
    last_chapter_no = 0
    for no in desk.all_nos():
        ch = desk.get(no)
        if ch and ch.field_no == v.field_no:
            if no > last_chapter_no:
                last_chapter_no = no

    # 1. 更新伏笔状态
    decisions_out = []
    for dec in body.foreshadow_decisions:
        try:
            if dec.action == "pay":
                es.update(dec.id, status="paid",
                          state_attrs={"payoff_chapter": str(last_chapter_no) if last_chapter_no else ""})
            elif dec.action == "drop":
                es.update(dec.id, status="dropped")
            elif dec.action == "carry":
                pass  # 保留 planted
            else:
                # 未知 action，忽略但不报错
                pass
        except ValueError:
            # 实体不存在则跳过
            pass
        decisions_out.append({"id": dec.id, "action": dec.action, "note": dec.note})

    # 2. 应用 settle diff 到主角状态
    diff = {
        "contamination_delta": body.contamination_delta,
        "kill_record_line": body.kill_record_line,
        "crafts": body.crafts,
        "echoes": body.echoes,
        "god_marks": body.god_marks,
        "brought_items": body.brought_items,
        "bonds_delta": body.bonds_delta,
        "field_count_inc": body.field_count_inc,
    }
    try:
        summary = es.apply_settle_diff(diff, field_no=v.field_no)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"归账 diff 失败：{e}")

    # 3. 写 v.settle
    v.settle = {
        "applied_at": _now(),
        "foreshadow_decisions": decisions_out,
        "diff_summary": summary,
        "final_chapter_no": last_chapter_no,
    }
    vs.save(v)

    return {"ok": True, "settle": v.settle}


def _finalize(pid: str, vol_no: int) -> dict:
    """POST 杀青定稿：dungeon 标 settle 完成进入 interlude 引导；interlude 标 interlude_plan 完成。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    if not v.settle:
        raise HTTPException(status_code=422, detail="尚未 apply 归账，不能 finalize")

    if v.volume_kind == "dungeon":
        v.settle["finalized_at"] = _now()
        if not isinstance(v.stage_completed, dict):
            v.stage_completed = {}
        v.stage_completed["settle"] = True
    elif v.volume_kind == "interlude":
        v.settle["finalized_at"] = _now()
        v.interlude_plan = {"finalized_at": _now(), "settle": v.settle}

    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}
