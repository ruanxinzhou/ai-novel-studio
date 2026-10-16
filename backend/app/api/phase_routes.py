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

from fastapi import APIRouter, HTTPException, Query
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

def _determine_phase(pid: str, target_volume_no: int | None = None) -> dict:
    """根据项目数据判定工作台阶段。返回标准 phase 响应 dict。

    target_volume_no: 指定查看的卷号；为 None 时使用"最新卷"进行进度判定。
      查看旧卷时只读展示该卷已完成的阶段状态，不影响整体进度。
    """
    d = _must_deps()

    project_dir = d.project_dir(pid)

    # 1. setup：8 张卡必须都存在且 JSON 合法；列表型卡（cost_debts/remaining_pages/finale_notes）允许空 []
    book_dir = project_dir / "book"
    list_cards = {"cost_debts", "remaining_pages", "finale_notes"}
    cards_ok = True
    import json as _json
    for card in d.book_cards:
        card_path = book_dir / f"{card}.json"
        if not card_path.exists():
            cards_ok = False
            break
        try:
            data = _json.loads(card_path.read_text(encoding="utf-8"))
        except Exception:
            cards_ok = False
            break
        if card in list_cards:
            # 列表卡：必须是 list（允许空列表）
            if not isinstance(data, list):
                cards_ok = False
                break
        else:
            # 对象卡：必须是 dict 且至少有一个 key
            if not isinstance(data, dict) or len(data) == 0:
                cards_ok = False
                break
    if not cards_ok:
        return {
            "phase": "setup",
            "volume_no": None,
            "volume_kind": None,
            "chapter_no": None,
            "is_last_chapter": False,
            "is_reviewing": False,
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
            "is_reviewing": False,
        }

    last_vol = max(vols, key=lambda v: v.volume_no)

    # ---- 查看旧卷模式：target_volume_no 存在且 != last_vol ----
    is_reviewing = False
    target_vol = last_vol
    if target_volume_no is not None and target_volume_no != last_vol.volume_no:
        is_reviewing = True
        matched = [v for v in vols if v.volume_no == target_volume_no]
        if not matched:
            raise HTTPException(404, f"卷 {target_volume_no} 不存在")
        target_vol = matched[0]

    desk = d.desk_for(pid)

    # 过滤出属于该卷的章节：优先 field_no == vol_no；is_interlude 且未正确绑 field_no 的老数据归到最近 interlude 卷
    all_chapter_nos = sorted(desk.all_nos())
    target_ch_nos: list[int] = []
    for no in all_chapter_nos:
        ch = desk.get(no)
        if not ch:
            continue
        if ch.field_no == target_vol.volume_no:
            target_ch_nos.append(no)
    # 老数据兼容：interlude 章没绑 field_no 时，按 is_interlude + 卷顺序归组
    if target_vol.volume_kind == 'interlude' and not target_ch_nos:
        interlude_vols = sorted([v for v in vols if v.volume_kind == 'interlude'], key=lambda v: v.volume_no)
        idx = next((i for i, v in enumerate(interlude_vols) if v.volume_no == target_vol.volume_no), 0)
        interlude_chs = [n for n in all_chapter_nos if (ch2 := desk.get(n)) and ch2.is_interlude]
        prev = 0
        for v in interlude_vols[:idx]:
            zp = (v.interlude_plan or {}).get('zhangshu') or {}
            prev += int(zp.get('suggested_chapters') or 1)
        cur_zp = (target_vol.interlude_plan or {}).get('zhangshu') or {}
        cur_n = int(cur_zp.get('suggested_chapters') or max(1, len(interlude_chs) - prev))
        target_ch_nos = interlude_chs[prev:prev + cur_n]
    target_ch_nos.sort()

    # 没有绑定章 → 用阶段牌面判阶段
    if not target_ch_nos:
        sc = target_vol.stage_completed or {}
        if is_reviewing:
            # 旧卷回看：返回 *_review 只读态
            if target_vol.settle is not None:
                return {
                    "phase": "settle_review",
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": None,
                    "is_last_chapter": False,
                    "is_reviewing": True,
                }
            if target_vol.volume_kind == "interlude" and target_vol.interlude_plan and target_vol.interlude_plan.get("finalized_at"):
                return {
                    "phase": "interlude_review",
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": None,
                    "is_last_chapter": False,
                    "is_reviewing": True,
                }
            if target_vol.volume_kind == "interlude" and (not target_vol.interlude_plan or not target_vol.interlude_plan.get("finalized_at")):
                return {
                    "phase": "interlude_review",
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": None,
                    "is_last_chapter": False,
                    "is_reviewing": True,
                }
            # 倒序找第一个已完成阶段
            stage_order_rev = ["rhythm", "scaffold", "embody", "simulate", "adapt"]
            for stage_key in stage_order_rev:
                if sc.get(stage_key, False):
                    return {
                        "phase": f"{stage_key}_review",
                        "volume_no": target_vol.volume_no,
                        "volume_kind": target_vol.volume_kind,
                        "chapter_no": None,
                        "is_last_chapter": False,
                        "is_reviewing": True,
                    }
            return {
                "phase": "adapt_review",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": True,
            }
        # 最新卷（非回看）：走原逻辑
        # 已 settle
        if target_vol.settle is not None:
            return {
                "phase": "settle",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": False,
            }
        # 间章（已 finalize 但还没章的情况不会发生，保留防御）
        if target_vol.volume_kind == "interlude":
            if not target_vol.interlude_plan or not target_vol.interlude_plan.get("finalized_at"):
                return {
                    "phase": "interlude",
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": None,
                    "is_last_chapter": False,
                    "is_reviewing": False,
                }
        # 按 stage_order 找第一个未完成阶段
        stage_order = ["adapt", "simulate", "embody", "scaffold", "rhythm"]
        for stage_key in stage_order:
            if not sc.get(stage_key, False):
                return {
                    "phase": stage_key,
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": None,
                    "is_last_chapter": False,
                    "is_reviewing": False,
                }
        return {
            "phase": "briefing",
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": None,
            "is_last_chapter": False,
            "is_reviewing": False,
        }

    # ---- 有章的情况 ----
    # 查看旧卷：全部章都 finalized → 显示 desk_review（只读回看）；否则进 desk_review 展示未完结章
    if is_reviewing:
        # 若该卷已 settle → settle_review
        if target_vol.settle is not None:
            return {
                "phase": "settle_review",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": True,
            }
        unfinished = [no for no in target_ch_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
        if unfinished:
            earliest = min(unfinished)
            max_ch = max(target_ch_nos) if target_ch_nos else 0
            return {
                "phase": "desk_review",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": True,
            }
        # 全完结但未 settle → 说明还在 interlude 前的"已写未定稿"？看最后章
        ch_no = max(target_ch_nos)
        return {
            "phase": "desk_review",
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": ch_no,
            "is_last_chapter": True,
            "is_reviewing": True,
        }

    # ---- 最新卷：原流程（只针对 target_vol = last_vol 的情况）----
    all_chapter_nos = target_ch_nos  # 只看当前卷的章
    # 3. 杀青判定：当前卷所有章 finalized，settle 为 None。
    #    卷末判定：要么 advance 显式置 stage_completed.briefing+desk=True，
    #    要么本卷已写章数已达到 rhythm_blueprint 建议上限/章计划最大章号
    if all_chapter_nos and last_vol.settle is None:
        all_finalized = all(bool(desk.get(no) and desk.get(no).finalized_at) for no in all_chapter_nos)
        sc = last_vol.stage_completed or {}
        bp = last_vol.rhythm_blueprint or {}
        plan_list = bp.get('chapter_plan') if isinstance(bp, dict) else []
        plan_max = max((int(p.get('chapter') or 0) for p in (plan_list or []) if isinstance(p, dict)), default=0)
        rng = (bp.get('suggested_chapter_range') or [0, 0]) if isinstance(bp, dict) else [0, 0]
        range_max = int(rng[1] or 0) if len(rng) >= 2 else 0
        cur_max_ch = max(all_chapter_nos) if all_chapter_nos else 0
        # 间章：章数达到 zhangshu.suggested_chapters 即视为卷末
        interlude_plan = last_vol.interlude_plan or {}
        interlude_expected = 0
        if last_vol.volume_kind == 'interlude':
            zp = interlude_plan.get('zhangshu') or {}
            interlude_expected = int(zp.get('suggested_chapters') or 0)
        reached_plan_end = (
            bool(sc.get('briefing') and sc.get('desk'))
            or (plan_max > 0 and cur_max_ch >= plan_max)
            or (range_max > 0 and cur_max_ch >= range_max)
            or (last_vol.volume_kind == 'interlude' and interlude_expected > 0 and len(all_chapter_nos) >= interlude_expected)
        )
        if all_finalized and reached_plan_end:
            return {
                "phase": "settle",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": False,
            }
        if all_finalized and not reached_plan_end:
            return {
                "phase": "briefing",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": cur_max_ch,
                "is_last_chapter": True,
                "is_reviewing": False,
            }

    # 4. 间章判定（仅当前卷）
    if last_vol.volume_kind == "interlude" and last_vol.settle is None:
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
                "is_reviewing": False,
            }
        if not last_vol.interlude_plan or not last_vol.interlude_plan.get("finalized_at"):
            return {
                "phase": "interlude",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": False,
            }

    # 5. 日更：存在未定稿章
    unfinished = [no for no in all_chapter_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
    if unfinished:
        earliest = min(unfinished)
        max_ch = max(all_chapter_nos) if all_chapter_nos else 0
        ch = desk.get(earliest)
        if ch and ch.is_interlude:
            return {
                "phase": "desk",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": False,
            }
        if ch and not (ch.brief and isinstance(ch.brief, dict) and ch.brief.get("selected")):
            return {
                "phase": "briefing",
                "volume_no": last_vol.volume_no,
                "volume_kind": last_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": False,
            }
        return {
            "phase": "desk",
            "volume_no": last_vol.volume_no,
            "volume_kind": last_vol.volume_kind,
            "chapter_no": earliest,
            "is_last_chapter": (earliest == max_ch),
            "is_reviewing": False,
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
                "is_reviewing": False,
            }

    # 7. 都 True → briefing
    return {
        "phase": "briefing",
        "volume_no": last_vol.volume_no,
        "volume_kind": last_vol.volume_kind,
        "chapter_no": None,
        "is_last_chapter": False,
        "is_reviewing": False,
    }


# ---- 路由处理函数 ----

def _get_phase(
    pid: str,
    volume: int | None = Query(None, description="指定查看的卷号；不传则返回最新卷进度"),
) -> dict:
    """返回当前项目工作台阶段。"""
    d = _must_deps()
    d.require_project(pid)
    return _determine_phase(pid, target_volume_no=volume)


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
