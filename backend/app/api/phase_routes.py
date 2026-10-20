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

from fastapi import APIRouter, Body, HTTPException, Query
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
    # 注意：POST /api/projects/{pid}/volumes 统一在 main.py 的 volumes_create_next 中处理（已扩展支持 kind/start_chapter_no），
    # 这里不再重复注册，避免路由顺序导致老实现拦截 kind=mainline 的请求。

    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("phase_routes 未调用 register_phase_routes 初始化")
    return _deps


# ---- 请求体模型 ----

class _DungeonVolumeIn(BaseModel):
    title: str = ""


class _CreateVolumeIn(BaseModel):
    kind: str = "dungeon"   # mainline | dungeon | interlude
    title: str = ""
    start_chapter_no: int | None = None


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

    # 2. 拿卷列表与 desk
    vs = d.volumes_for(pid)
    vols = vs.all_list()
    desk = d.desk_for(pid)

    # 3. mainline 判定（蓝星开篇前置）—— 没有任何卷或没有 mainline 卷时，都是 mainline-waiting
    mainline_vols = [v for v in vols if v.volume_kind == "mainline"]
    if not mainline_vols:
        return {
            "phase": "mainline-waiting",
            "volume_no": None, "volume_kind": None, "chapter_no": None,
            "is_last_chapter": False, "is_reviewing": False,
        }
    mv = mainline_vols[0]  # 只看第一个 mainline 卷
    # 主线章节必须按 chapter_kind == "mainline" 识别；field_no 可能和 dungeon 卷号冲突（dungeon 卷 1 的 field_no=1 但不是主线）
    ml_ch_nos: list[int] = []
    for no in sorted(desk.all_nos()):
        ch = desk.get(no)
        if ch and ch.chapter_kind == "mainline":
            ml_ch_nos.append(no)
    ml_finalized = sum(1 for n in ml_ch_nos if desk.get(n) and desk.get(n).finalized_at)
    # 主线未写完时，若用户显式点选了非 mainline 卷（去规划新副本/间章），允许跳过去做规划，不强绑主线
    user_switching_away = (
        target_volume_no is not None
        and not any(v.volume_no == target_volume_no and v.volume_kind == "mainline" for v in vols)
    )
    if ml_finalized < 2 and not user_switching_away:
        # 找第一个未写完的章
        unfinished = [n for n in ml_ch_nos if not (desk.get(n) and desk.get(n).finalized_at)]
        if unfinished:
            cno = min(unfinished)
        elif ml_ch_nos:
            cno = max(ml_ch_nos) + 1
            if cno > mv.start_chapter_no + 1:
                cno = mv.start_chapter_no  # 不超过主线 2 章，回到未完成的首章
        else:
            cno = mv.start_chapter_no
        if not desk.get(cno):
            # 主线 2 章使用固定标题
            ch_title = "蓝星日常·收到讣告" if cno == mv.start_chapter_no else "讣告应验·穿越坠落"
            desk.ensure(cno, title=ch_title, field_no=mv.volume_no, chapter_kind="mainline")
        # 检查当前章是否已选方向卡：已选 → 直接进 desk；未选 → 停在 mainline-brief 让用户选方向
        ch = desk.get(cno)
        brief_sel = bool(ch and isinstance(getattr(ch, "brief", None), dict) and ch.brief.get("selected"))
        return {
            "phase": "desk" if brief_sel else "mainline-brief",
            "volume_no": mv.volume_no,
            "volume_kind": "mainline",
            "chapter_no": cno,
            "is_last_chapter": (cno == mv.start_chapter_no + 1),
            "is_reviewing": False,
        }
    # mainline 已写完 2 章，或用户显式点选了非 mainline 卷做预规划：继续走后续（dungeon 卷判定）

    last_vol = max(vols, key=lambda v: v.volume_no)

    # ---- 判定 target_vol 是否"真正完结"（进入只读回看）----
    # 完结条件：
    #   dungeon 卷：settle 不为 None（已杀青归账）
    #   interlude 卷：interlude_plan.finalized_at 存在（间章已定稿）
    # 未完结的卷即使不是 last_vol，也允许继续编辑（不是只读回看），
    # 这样用户创建新卷后仍可返回旧卷推进到跑团/魂穿等阶段。
    def _is_volume_settled(v) -> bool:
        if v.settle is not None:
            return True
        if v.volume_kind == "interlude":
            return bool((v.interlude_plan or {}).get("finalized_at"))
        return False

    # ---- 查看旧卷模式：target_volume_no 存在、!= last_vol、且该卷已完结 ----
    is_reviewing = False
    target_vol = last_vol
    if target_volume_no is not None and target_volume_no != last_vol.volume_no:
        matched = [v for v in vols if v.volume_no == target_volume_no]
        if not matched:
            raise HTTPException(404, f"卷 {target_volume_no} 不存在")
        target_vol = matched[0]
        if _is_volume_settled(target_vol):
            is_reviewing = True
        # 未完结旧卷：is_reviewing 保持 False，走正常编辑流程

    # ---- 阶段视图覆盖：允许用户从后续阶段"返回查看/编辑"某个已完成阶段 ----
    # 合法覆盖值：simulate / adapt / embody / scaffold / rhythm / briefing / desk
    sc = target_vol.stage_completed or {}
    view_override = (sc.get("_view") if isinstance(sc, dict) else None)
    _VIEW_TO_PHASE = {
        "adapt": "adapt",
        "simulate": "simulate",
        "embody": "embody",
        "scaffold": "scaffold",
        "rhythm": "rhythm",
        "briefing": "briefing",
        "desk": "desk",
        "writing-desk": "writing-desk",
    }
    if isinstance(view_override, str) and view_override in _VIEW_TO_PHASE:
        return {
            "phase": _VIEW_TO_PHASE[view_override],
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": None,
            "is_last_chapter": False,
            "is_reviewing": False,
        }

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
        # 所有规划阶段已完成但尚无章节 → 按 chapter_plan 批量建章
        start_no = int(getattr(target_vol, "start_chapter_no", 1) or 1)
        bp = target_vol.rhythm_blueprint or {}
        plan = bp.get("chapter_plan") if isinstance(bp, dict) else []
        is_interlude_v = (target_vol.volume_kind == "interlude")
        ch_kind_v = "interlude" if is_interlude_v else target_vol.volume_kind
        field_no_v = target_vol.field_no if getattr(target_vol, "field_no", None) is not None else target_vol.volume_no
        beat_label = {"hook": "黄金钩子", "sell": "卖设定", "engine": "主线推进",
                      "climax": "高潮爆发", "cooldown": "余韵收束"}
        created_nos: list[int] = []
        from datetime import datetime
        _now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        vol_hooks_v = bp.get("volume_hooks") if isinstance(bp, dict) else {}
        if not isinstance(vol_hooks_v, dict):
            vol_hooks_v = {}
        plan_items = plan if isinstance(plan, list) else []
        # 如果没有 plan（老数据/异常），至少建 start_no 第一章
        if not plan_items:
            plan_items = [{"chapter": 1, "day": 1, "focus": "", "words": 4500, "beat_type": "engine"}]
        total_v = len(plan_items)
        for idx, item in enumerate(plan_items):
            if not isinstance(item, dict):
                continue
            try:
                rel = int(item.get("chapter") or 0)
            except (TypeError, ValueError):
                continue
            if rel <= 0:
                continue
            gno = rel - 1 + start_no
            w = desk.ensure(gno)
            w.field_no = field_no_v
            w.chapter_kind = ch_kind_v
            w.is_interlude = is_interlude_v
            try:
                w.day = int(item.get("day") or w.day or 1)
            except (TypeError, ValueError):
                pass
            focus = (item.get("focus") or "").strip()
            words = int(item.get("words") or 4500)
            beat = (item.get("beat_type") or "engine")
            title_in = (item.get("title") or "").strip()
            if title_in and not w.title:
                w.title = title_in
            beats_v = item.get("plot_beats") if isinstance(item.get("plot_beats"), dict) else {}
            chars_v = item.get("characters_on_stage") or []
            if not isinstance(chars_v, list):
                chars_v = []
            seeds_v = item.get("foreshadow_seeds") or []
            if not isinstance(seeds_v, list):
                seeds_v = []
            payback_v = item.get("foreshadow_payback") or []
            if not isinstance(payback_v, list):
                payback_v = []
            try:
                day_v = int(item.get("day") or 1)
            except (TypeError, ValueError):
                day_v = 1
            already_b = w.brief if isinstance(w.brief, dict) else None
            if not (already_b and already_b.get("selected")):
                sel: dict = {
                    "id": f"plan-{gno}",
                    "title": title_in or f"第{rel}节·{beat_label.get(beat, beat)}",
                    "summary": focus or f"第{rel}节按节奏蓝图推进（{beat_label.get(beat, beat)}）",
                    "hooks": "",
                    "day": day_v,
                    "beat_type": beat,
                    "foreshadow_advance": [str(s) for s in payback_v if s],
                    "entities_on_stage": [str(c) for c in chars_v if c],
                    "source": "rhythm",
                    "chapter_words_target": words,
                    "time_window": str(item.get("time_window") or f"入戏第{day_v}天"),
                    "location": str(item.get("location") or ""),
                    "plot_beats": {
                        "qi": str(beats_v.get("qi") or ""),
                        "cheng": str(beats_v.get("cheng") or ""),
                        "zhuan": str(beats_v.get("zhuan") or ""),
                        "he": str(beats_v.get("he") or ""),
                    },
                    "foreshadow_seeds": [str(s) for s in seeds_v if s],
                    "sensory_hook": str(item.get("sensory_hook") or ""),
                    "chapter_title": title_in,
                }
                if rel == 1 and vol_hooks_v.get("opening_hook"):
                    sel["volume_opening_hook"] = str(vol_hooks_v["opening_hook"])
                if idx == total_v - 1 and vol_hooks_v.get("ending_hook"):
                    sel["volume_ending_hook"] = str(vol_hooks_v["ending_hook"])
                w.brief = {
                    "selected": sel,
                    "selected_at": _now_str,
                    "chapter_no": gno,
                    "day": day_v,
                    "chapter_words_target": words,
                    "source": "rhythm",
                }
            created_nos.append(gno)
        desk._flush()
        first_no = min(created_nos) if created_nos else start_no
        return {
            "phase": "desk",
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": first_no,
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

    # ---- 非回看：当前工作卷流程（target_vol 可能是最新卷，也可能是未完结旧卷）----
    all_chapter_nos = target_ch_nos  # 只看当前卷的章
    # 3. 杀青判定：当前卷所有章 finalized，settle 为 None。
    if all_chapter_nos and target_vol.settle is None:
        all_finalized = all(bool(desk.get(no) and desk.get(no).finalized_at) for no in all_chapter_nos)
        sc = target_vol.stage_completed or {}
        bp = target_vol.rhythm_blueprint or {}
        plan_list = bp.get('chapter_plan') if isinstance(bp, dict) else []
        plan_max = max((int(p.get('chapter') or 0) for p in (plan_list or []) if isinstance(p, dict)), default=0)
        rng = (bp.get('suggested_chapter_range') or [0, 0]) if isinstance(bp, dict) else [0, 0]
        range_max_rel = int(rng[1] or 0) if len(rng) >= 2 else 0
        start_ch_no = int(getattr(target_vol, 'start_chapter_no', 1) or 1)
        range_max_abs = (start_ch_no + range_max_rel - 1) if range_max_rel > 0 else 0
        cur_max_ch = max(all_chapter_nos) if all_chapter_nos else 0
        interlude_plan = target_vol.interlude_plan or {}
        interlude_expected = 0
        if target_vol.volume_kind == 'interlude':
            zp = interlude_plan.get('zhangshu') or {}
            interlude_expected = int(zp.get('suggested_chapters') or 0)
        reached_plan_end = (
            bool(sc.get('briefing') and sc.get('desk'))
            or (plan_max > 0 and cur_max_ch >= plan_max)
            or (range_max_abs > 0 and cur_max_ch >= range_max_abs)
            or (target_vol.volume_kind == 'interlude' and interlude_expected > 0 and len(all_chapter_nos) >= interlude_expected)
        )
        end_forced = bool(sc.get('end_forced'))
        end_suppressed = bool(sc.get('end_suppressed'))
        if end_forced and all_finalized:
            return {
                "phase": "settle",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": False,
            }
        if end_suppressed:
            if all_finalized:
                return {
                    "phase": "briefing",
                    "volume_no": target_vol.volume_no,
                    "volume_kind": target_vol.volume_kind,
                    "chapter_no": cur_max_ch,
                    "is_last_chapter": True,
                    "is_reviewing": False,
                    "end_suppressed": True,
                }
        elif all_finalized and reached_plan_end:
            return {
                "phase": "settle",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": None,
                "is_last_chapter": False,
                "is_reviewing": False,
            }
        if all_finalized and not reached_plan_end:
            return {
                "phase": "briefing",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": cur_max_ch,
                "is_last_chapter": True,
                "is_reviewing": False,
            }

    # 4. 间章判定（仅当前卷）
    if target_vol.volume_kind == "interlude" and target_vol.settle is None:
        unfinished = [no for no in all_chapter_nos if not bool(desk.get(no) and desk.get(no).finalized_at)]
        if unfinished:
            earliest = min(unfinished)
            max_ch = max(all_chapter_nos) if all_chapter_nos else 0
            return {
                "phase": "desk",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": False,
            }
        if not target_vol.interlude_plan or not target_vol.interlude_plan.get("finalized_at"):
            return {
                "phase": "interlude",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
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
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": False,
            }
        if ch and not (ch.brief and isinstance(ch.brief, dict) and ch.brief.get("selected")):
            return {
                "phase": "briefing",
                "volume_no": target_vol.volume_no,
                "volume_kind": target_vol.volume_kind,
                "chapter_no": earliest,
                "is_last_chapter": (earliest == max_ch),
                "is_reviewing": False,
            }
        return {
            "phase": "desk",
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": earliest,
            "is_last_chapter": (earliest == max_ch),
            "is_reviewing": False,
        }

    # 6. 按 stage_completed 顺序找第一个 False
    stage_order = ["adapt", "simulate", "embody", "scaffold", "rhythm"]
    sc = target_vol.stage_completed or {}
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

    # 7. 都 True → briefing；若当前卷还没有任何章，先创建第一章
    if not all_chapter_nos:
        start_no = int(getattr(target_vol, "start_chapter_no", 1) or 1)
        w = desk.ensure(start_no)
        w.field_no = target_vol.field_no if getattr(target_vol, "field_no", None) is not None else target_vol.volume_no
        w.chapter_kind = "interlude" if target_vol.volume_kind == "interlude" else target_vol.volume_kind
        w.is_interlude = (target_vol.volume_kind == "interlude")
        desk._flush()
        all_chapter_nos = target_ch_nos  # 刷新
        return {
            "phase": "briefing",
            "volume_no": target_vol.volume_no,
            "volume_kind": target_vol.volume_kind,
            "chapter_no": start_no,
            "is_last_chapter": False,
            "is_reviewing": False,
        }
    return {
        "phase": "briefing",
        "volume_no": target_vol.volume_no,
        "volume_kind": target_vol.volume_kind,
        "chapter_no": min(all_chapter_nos),
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
    desk = d.desk_for(pid)
    title = body.title.strip() if body.title else ""
    v = vs.create_new_volume(title=title, volume_kind="dungeon", start_chapter_no=desk.next_available_no())
    return {"ok": True, "volume_no": v.volume_no, "volume": v.as_dict()}


def _create_interlude_volume(pid: str) -> dict:
    """创建新间章卷（interlude）。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    desk = d.desk_for(pid)
    v = vs.create_new_volume(title="蓝星间章", volume_kind="interlude", start_chapter_no=desk.next_available_no())
    return {"ok": True, "volume_no": v.volume_no, "volume": v.as_dict()}


def _create_volume(pid: str, body: _CreateVolumeIn = Body(...)) -> dict:
    """统一建卷接口：支持 mainline / dungeon / interlude 三种 kind。"""
    d = _must_deps()
    d.require_project(pid)
    kind = (body.kind or "dungeon").strip().lower()
    if kind not in ("mainline", "dungeon", "interlude"):
        raise HTTPException(400, f"非法的卷 kind：{kind}（只支持 mainline/dungeon/interlude）")
    vs = d.volumes_for(pid)
    desk = d.desk_for(pid)
    title = (body.title or "").strip()
    if not title:
        if kind == "mainline":
            title = "蓝星开篇"
        elif kind == "interlude":
            title = "蓝星间章"
        else:
            title = ""
    start_chapter_no = body.start_chapter_no
    if start_chapter_no is None:
        start_chapter_no = desk.next_available_no()
    v = vs.create_new_volume(title=title, volume_kind=kind, start_chapter_no=start_chapter_no)
    return {
        "ok": True,
        "volume_no": v.volume_no,
        "volume": v.as_dict(),
        "phase": _determine_phase(pid),
    }
