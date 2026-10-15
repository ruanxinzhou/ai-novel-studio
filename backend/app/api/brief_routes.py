"""章前 5 分钟卡（brief）端点：单章 PATCH + 下一章方向卡桩。

挂载方式：main.py 末尾调用
    from .brief_routes import register_brief_routes
    register_brief_routes(app, require_project=..., volumes_for=..., desk_for=...)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]


_deps: _Deps | None = None


def register_brief_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
    )

    router = APIRouter()
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief",
        _patch_brief, methods=["PATCH"],
    )
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/next-chapter-brief",
        _next_chapter_brief, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("brief_routes 未调用 register_brief_routes 初始化")
    return _deps


# ---- 请求体 ----

class _NextBriefIn(BaseModel):
    prev_chapter_no: int | None = None


# ---- 处理函数 ----

def _patch_brief(pid: str, vol_no: int, cno: int, body: dict) -> dict:
    """PATCH 单章章前卡；body 是任意 brief 字段 JSON，merge 到现有 brief。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = d.desk_for(pid)
    work = desk.ensure(cno)
    if work.brief is None or not isinstance(work.brief, dict):
        work.brief = {}
    # 合并 body 字段
    if isinstance(body, dict):
        for k, val in body.items():
            work.brief[k] = val
    # 绑定 field_no（若还没绑）
    if work.field_no is None:
        work.field_no = v.field_no
    desk._flush()
    return {"ok": True, "brief": work.brief}


def _next_chapter_brief(pid: str, vol_no: int, body: _NextBriefIn) -> dict:
    """POST 下一章 AI 方向卡 3 张——当前为静态桩，不调用 LLM。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return {
        "status": "construction",
        "message": "章前三张方向卡将在后续任务接入 LLM，当前为桩",
        "options": [
            {"id": "a", "title": "顺承推进", "summary": "沿上一章钩子继续"},
            {"id": "b", "title": "视角切换", "summary": "切到 NPC 视角制造悬念"},
            {"id": "c", "title": "时间跳跃", "summary": "跳过几个小时，直接到冲突点"},
        ],
    }
