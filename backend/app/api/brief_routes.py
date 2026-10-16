"""章前 5 分钟卡（brief）端点：方向卡 /options、选中 /select、推进 /advance、PATCH 字段。

挂载方式：main.py 末尾调用
    from .brief_routes import register_brief_routes
    register_brief_routes(app, require_project=..., volumes_for=..., desk_for=..., chore_provider=...)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.brief")

_BRIEF_SYSTEM = """你是一名克苏鲁风格无限流小说的章前方向卡设计师，正在为《第七日的亡者》某卷某章做"章前5分钟方向卡"推演。
前序产物（副本核、宿主、魂穿、详设6卡、节奏蓝图chapter_plan）已锁定，上一章结尾与到期伏笔作为上下文中给出。
请给出 3 版差异化的本章写作方向卡。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。返回格式：

{
  "options": [
    {
      "id": "a",
      "title": "……5~10字本章短标题……",
      "summary": "……80~120字本章方向描述……",
      "hooks": "……20~40字章尾钩子（读者看完本章最后一段最想知道什么）……",
      "day": 1,
      "beat_type": "hook",
      "foreshadow_advance": ["伏笔1", "伏笔2"],
      "entities_on_stage": ["角色名", "地点名"]
    },
    {"id": "b", ...},
    {"id": "c", ...}
  ]
}

字段约束：
- options 必须恰好 3 张，id 分别为 "a"/"b"/"c"。
- title 5~10 字，中文，克苏鲁阴冷基调，强调悬念/压迫/异化。
- summary 80~120 字，写清楚：开篇场景、推进点、冲突/异常、收束点。
- hooks 20~40 字，章尾钩子（留疑/反转/惊吓）。
- day 必须等于输入 day。
- beat_type 必须是 hook/sell/engine/climax/cooldown 之一，与输入一致。
- foreshadow_advance 是本章要推进的伏笔名列表（可以空数组）。
- entities_on_stage 是本章出场的实体（角色/地点/物品），3~6 个。
- 三版要有明显差异：一版偏顺承推进、一版偏视角/信息差、一版偏冲击反转；不要三版雷同。
"""


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    chore_provider: Callable


_deps: _Deps | None = None


def register_brief_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
    chore_provider: Callable,
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        chore_provider=chore_provider,
    )

    router = APIRouter(tags=["brief"])
    # 新 workbench 前缀
    wb_prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/chapters/{cno}/brief"
    # 旧 PATCH 路径保留（/volumes/ 而不是 /workbench/volumes/）
    old_prefix = "/api/projects/{pid}/volumes/{vol_no}/chapters/{cno}/brief"

    router.add_api_route(f"{wb_prefix}/options", _brief_options, methods=["POST"])
    router.add_api_route(f"{wb_prefix}/select", _brief_select, methods=["POST"])
    router.add_api_route(f"{wb_prefix}/advance", _brief_advance, methods=["POST"])
    router.add_api_route(old_prefix, _patch_brief, methods=["PATCH"])
    # 向后兼容：旧 /next-chapter-brief 转发到 /brief/options（cno 从 body.prev_chapter_no+1 或默认 1 推导）
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/next-chapter-brief",
        _legacy_next_chapter_brief, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("brief_routes 未调用 register_brief_routes 初始化")
    return _deps


# ---- 请求体 ----

class _BriefOptionsIn(BaseModel):
    directive: str | None = None
    base_index: int | None = None
    custom_writing: str | None = None


class _BriefSelectIn(BaseModel):
    option_index: int = -1
    extra_edits: str | None = None
    custom_text: str | None = None


class _BriefAdvanceIn(BaseModel):
    finalized: bool = False


class _LegacyNextBriefIn(BaseModel):
    prev_chapter_no: int | None = None


# ---- Mock 判定（与 rhythm/scaffold 一致）----

def _is_mock(vol) -> bool:
    try:
        am = vol.adapted_module or ""
        if isinstance(am, str) and am.startswith("测试副本核文本"):
            return True
        hc = vol.host_character or {}
        if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
            return True
    except Exception:
        return False
    return False


def _mock_options(cno: int, plan: dict | None) -> dict:
    """返回 3 版克苏鲁基调方向卡（基于 chapter_plan 兜底）。"""
    day = (plan or {}).get("day") or 1
    beat = (plan or {}).get("beat_type") or "engine"
    focus = (plan or {}).get("focus") or "诡异事件推进"
    words = (plan or {}).get("words") or 6500
    beat_label = {"hook": "黄金钩子", "sell": "卖设定", "engine": "主线推进",
                  "climax": "高潮爆发", "cooldown": "余韵收束"}.get(beat, beat)
    titles = [
        f"第{cno}章·顺承推进",
        f"第{cno}章·视角错位",
        f"第{cno}章·惊骇突转",
    ]
    summaries = [
        f"开场紧接前章余波，唐迟在日常场景里察觉{focus[:6]}的细小异样——空气中的咸腥味、熟悉人脸上一闪而过的陌生表情。逐步查证时规则以极克制的方式显形，章尾留一个半遮半掩的具象异象作为钩子，整体稳扎稳打铺陈{beat_label}节奏。",
        f"本章从 NPC 侧（宿管/同学/保安）视角切入，先呈现他们眼中的不对劲，再切回唐迟——读者比主角早半拍察觉{focus[:8]}的异样但看不清全貌。利用信息差制造认知悬疑，章尾在唐迟转身时给出一个他尚未看到的惊悚画面，将悬念拉满。",
        f"开头看似风平浪静，中段突然引爆{focus[:8]}相关的血腥异象——尸体/水渍/童谣以违反物理的方式出现，唐迟被迫做第一次主动求生抉择（代价已开始累积）。节奏陡升，章尾以点名式预告/倒计时数字显形作为爆点收尾，承接高潮。",
    ]
    hooks_opts = [
        f"唐迟低头时看见自己的影子比他慢了半拍——而且影子在笑。",
        f"熄灯后走廊传来宿管的拖鞋声，但宿管中午就已经上吊了。",
        f"手机屏幕亮起：47 个未接来电，全是他自己的号码。",
    ]
    entities_pool = ["唐迟", "林薇", "苏雅", "周凯", "刘保安", "宿管张姨", "辅导员", "校史馆", "宿舍楼", "海边"]
    return {
        "options": [
            {
                "id": "a", "title": titles[0], "summary": summaries[0],
                "hooks": hooks_opts[0], "day": day, "beat_type": beat,
                "foreshadow_advance": ["七次刻名", "影子先行"],
                "entities_on_stage": entities_pool[:4],
            },
            {
                "id": "b", "title": titles[1], "summary": summaries[1],
                "hooks": hooks_opts[1], "day": day, "beat_type": beat,
                "foreshadow_advance": ["童谣血字", "辅导员的鳃"],
                "entities_on_stage": entities_pool[2:6],
            },
            {
                "id": "c", "title": titles[2], "summary": summaries[2],
                "hooks": hooks_opts[2], "day": day, "beat_type": beat,
                "foreshadow_advance": ["无脚合唱", "海床点名"],
                "entities_on_stage": [entities_pool[0], entities_pool[2], entities_pool[6], entities_pool[7]],
            },
        ],
        "day": day,
        "chapter_words_estimate": int(words) if isinstance(words, (int, float)) else 6500,
        "is_mock": True,
    }


# ---- 辅助 ----

def _chapter_plan_for(vol, cno: int) -> dict | None:
    bp = getattr(vol, "rhythm_blueprint", None) or {}
    if not isinstance(bp, dict):
        return None
    plan_list = bp.get("chapter_plan") or []
    for p in plan_list:
        if isinstance(p, dict) and int(p.get("chapter") or 0) == cno:
            return p
    return None


def _nodes_for_day(vol, attr: str, day: int) -> list[dict]:
    items = getattr(vol, attr, None) or []
    out = []
    for n in items:
        if not isinstance(n, dict):
            continue
        try:
            if int(n.get("day") or 0) == day:
                out.append(n)
        except (TypeError, ValueError):
            continue
    return out


def _prev_tail(desk, cno: int) -> str:
    from app.wizard.desk import work_text
    for no in sorted((n for n in desk.all_nos() if n < cno), reverse=True):
        for stage in ("polish", "draft"):
            t = work_text(desk.get(no), stage)
            if t:
                return t[-500:]
    return ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- 处理函数 ----

def _patch_brief(pid: str, vol_no: int, cno: int, body: dict = Body(...)) -> dict:
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
    if isinstance(body, dict):
        for k, val in body.items():
            work.brief[k] = val
    if work.field_no is None:
        work.field_no = vol_no
    desk._flush()
    return {"ok": True, "brief": work.brief}


def _brief_options(
    pid: str,
    vol_no: int,
    cno: int,
    body: _BriefOptionsIn = Body(...),
) -> dict:
    """POST 章前方向卡 3 版：支持重抽（directive）、基于选中版微调（base_index）、自编（custom_writing）。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = deps.desk_for(pid)
    work = desk.ensure(cno)
    if work.field_no is None:
        work.field_no = vol_no
        desk._flush()

    plan = _chapter_plan_for(vol, cno)
    day = int((plan or {}).get("day") or (work.day or 1))
    words_estimate = int((plan or {}).get("words") or 6500)
    beat_type = (plan or {}).get("beat_type") or "engine"

    # 1) 自编模式：直接返回 1 条 custom 卡，不调 LLM
    if body.custom_writing and body.custom_writing.strip():
        cw = body.custom_writing.strip()
        title_line = cw.splitlines()[0].strip()[:20] if cw.strip() else "我自己写的"
        custom_opt = {
            "id": f"custom-{int(datetime.now().timestamp())}",
            "title": title_line or "我自己写的",
            "summary": cw,
            "hooks": "",
            "day": day,
            "beat_type": beat_type,
            "foreshadow_advance": [],
            "entities_on_stage": [],
            "is_custom": True,
        }
        # 写入 custom_draft 暂存 + _last_options
        if not isinstance(work.brief, dict):
            work.brief = {}
        work.brief["custom_draft"] = cw
        work.brief["_last_options"] = [custom_opt]
        desk._flush()
        return {
            "options": [custom_opt],
            "day": day,
            "chapter_words_estimate": words_estimate,
            "is_mock": False,
        }

    # 2) Mock 短路
    is_mock = _is_mock(vol)
    if is_mock:
        data = _mock_options(cno, plan)
    else:
        # 3) 真 LLM 调用
        from ..generation import complete_structured
        parts: list[str] = []
        parts.append("===== 节奏蓝图·本章规划 =====")
        parts.append(json.dumps(plan or {}, ensure_ascii=False))

        if day:
            deaths = _nodes_for_day(vol, "death_nodes", day)
            climaxes = _nodes_for_day(vol, "climax_nodes", day)
            clues = _nodes_for_day(vol, "clue_nodes", day)
            parts.append(f"\n===== 第{day}天节点 =====")
            parts.append(f"死亡节点：{json.dumps(deaths, ensure_ascii=False)}")
            parts.append(f"高潮节点：{json.dumps(climaxes, ensure_ascii=False)}")
            parts.append(f"线索节点：{json.dumps(clues, ensure_ascii=False)}")

        prev = _prev_tail(desk, cno)
        if prev:
            parts.append("\n===== 上一章章尾（滚动锚点·500字）=====")
            parts.append(prev)

        cards = vol.scaffold_cards or {}
        def _card_text(key: str) -> str:
            entry = cards.get(key) or {}
            return str(entry.get("text") or "")
        parts.append("\n===== 锁定核心设定（摘要）=====")
        parts.append(f"核心反转：{_card_text('core_twist')[:800]}")
        parts.append(f"唐迟起点：{_card_text('tangchi_start')[:500]}")

        if body.directive and body.directive.strip():
            parts.append(f"\n===== 作者指令（重抽/微调方向）=====")
            parts.append(body.directive.strip())
        if body.base_index is not None and body.base_index >= 0:
            parts.append(f"\n===== 基于已选版本微调 =====")
            parts.append(f"请以第 {body.base_index + 1} 版为基础做出差异化微调，保留核心方向但换切入角度/冲突点/钩子，不要完全复制。")

        context = "\n".join(parts)
        def _call() -> str:
            return deps.chore_provider("brief-options").chat([
                {"role": "system", "content": _BRIEF_SYSTEM},
                {"role": "user", "content": context + "\n\n请严格按 system 给定 JSON schema 返回 3 版方向卡 options 数组，不要输出任何其他文字。"},
            ]).text

        try:
            comp = complete_structured(_call, max_regenerations=2)
            data0 = comp.data if isinstance(comp.data, dict) else {}
        except Exception as e:
            log.warning("brief LLM 失败，回退 mock：%s", e)
            data0 = _mock_options(cno, plan)

        opts0 = data0.get("options") if isinstance(data0, dict) else None
        if not isinstance(opts0, list) or len(opts0) < 3:
            log.warning("brief LLM 返回 options 不合规，回退 mock")
            data0 = _mock_options(cno, plan)
        # 归一 id 为 a/b/c
        opts = data0.get("options") or []
        for i, key in enumerate(("a", "b", "c")):
            if i < len(opts) and isinstance(opts[i], dict):
                opts[i]["id"] = key
                # 补 day / beat_type 默认
                if not opts[i].get("day"):
                    opts[i]["day"] = day
                if not opts[i].get("beat_type"):
                    opts[i]["beat_type"] = beat_type
        data = {
            "options": opts[:3],
            "day": day,
            "chapter_words_estimate": words_estimate,
            "is_mock": False,
        }

    # 保存 _last_options 供 select 通过 option_index 查找
    if not isinstance(work.brief, dict):
        work.brief = {}
    work.brief["_last_options"] = data.get("options") or []
    desk._flush()

    return data


def _brief_select(
    pid: str,
    vol_no: int,
    cno: int,
    body: _BriefSelectIn = Body(...),
) -> dict:
    """POST 选中某一版方向卡，写入 work.brief.selected。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = deps.desk_for(pid)
    work = desk.ensure(cno)
    if work.field_no is None:
        work.field_no = vol_no

    selected: dict
    is_custom = False
    if body.custom_text and body.custom_text.strip():
        ct = body.custom_text.strip()
        title_line = ct.splitlines()[0].strip()[:20] if ct.strip() else "自编方向"
        plan_for_day = _chapter_plan_for(vol, cno)
        custom_day = (plan_for_day or {}).get("day") if plan_for_day else None
        selected = {
            "id": "custom",
            "title": title_line,
            "summary": ct,
            "hooks": "",
            "day": custom_day or work.day or 1,
            "beat_type": "engine",
            "foreshadow_advance": [],
            "entities_on_stage": [],
            "is_custom": True,
        }
        is_custom = True
    else:
        # 取出当前保存的 last_options？——简化：前端传 option_index 时，由前端自己持有 options；
        # 但后端无法从无状态读取，这里要求前端 select 请求同时带 option 对象？不——前端选中后
        # 会直接把选中的 option 对象回传吗？我们用 body 无法获得，因此改为：前端在 select 时把
        # selected 对象一并传？看 brief 中规定 body = { option_index, extra_edits?, custom_text? }。
        # 后端没有存 options，所以这里基于 last_options（暂存到 brief._last_options）是个选择，
        # 但是为了避免复杂化，我们让 select 端点额外接收 "selected" 字段可选；如果没有则尝试从
        # brief._last_options 取。
        # 简化策略：前端会传 extra_edits，但 selected 文本需要。我们采用方案：让 select 请求
        # 额外接受 option 对象——但 brief 没规定，我们扩展支持一个 selected 字段是冗余的。
        # 最佳折中：在 /options 返回时不持久化，但这里要求前端必须传 selected 字典，放在
        # extra_edits 同层的 selected 字段里。我们兼容两种：如果 body 带 selected dict 就用，
        # 否则构造一个占位（title="已选方向", summary=extra_edits）。
        # —— 改为：前端在 select 时把选中 option 通过额外字段 "selected_option" 传过来，这样最稳。
        # 但为兼容 brief 文档定义的 body，我们这里用 option_index 从 work.brief._last_options 取。
        last_opts = []
        if isinstance(work.brief, dict):
            last_opts = work.brief.get("_last_options") or []
        idx = body.option_index
        if 0 <= idx < len(last_opts) and isinstance(last_opts[idx], dict):
            selected = dict(last_opts[idx])
        else:
            # 兜底：用 extra_edits 作为方向文本
            selected = {
                "id": f"opt-{idx}" if idx >= 0 else "manual",
                "title": f"第{cno}章方向",
                "summary": (body.extra_edits or "").strip() or "（请补充本章方向）",
                "hooks": "",
                "day": work.day,
                "beat_type": "engine",
                "foreshadow_advance": [],
                "entities_on_stage": [],
            }

    if body.extra_edits and body.extra_edits.strip():
        selected["extra_edits"] = body.extra_edits.strip()

    brief_obj = {
        "selected": selected,
        "selected_at": _now(),
        "extra_edits": (body.extra_edits or "").strip(),
        "chapter_no": cno,
        "day": selected.get("day"),
        "is_custom": bool(is_custom or selected.get("is_custom")),
    }
    work.brief = brief_obj
    work.day = selected.get("day") if isinstance(selected.get("day"), int) else work.day
    # 默认 title
    if not work.title:
        work.title = f"第{cno}章 {selected.get('title') or ''}".strip()
    desk._flush()
    return {"ok": True, "brief": work.brief, "chapter_no": cno}


def _brief_advance(
    pid: str,
    vol_no: int,
    cno: int,
    body: _BriefAdvanceIn = Body(...),
) -> dict:
    """POST 定稿后推进：判断是否到卷末，决定下一章/杀青。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = deps.desk_for(pid)

    bp = getattr(vol, "rhythm_blueprint", None) or {}
    rng = (bp.get("suggested_chapter_range") or [0, 0]) if isinstance(bp, dict) else [0, 0]
    max_chapters = int(rng[1] or 0) if len(rng) >= 2 else 0

    # 当前 desk 中最大章号
    all_nos = desk.all_nos()
    cur_max = max(all_nos) if all_nos else 0

    at_end = False
    if max_chapters > 0 and cur_max >= max_chapters:
        at_end = True
    # 额外判断：如果 chapter_plan 最后一章 <= cno，也算卷末
    plan_list = bp.get("chapter_plan") if isinstance(bp, dict) else []
    if isinstance(plan_list, list) and plan_list:
        plan_max = max((int(p.get("chapter") or 0) for p in plan_list if isinstance(p, dict)), default=0)
        if plan_max > 0 and cno >= plan_max:
            at_end = True

    if body.finalized:
        if at_end:
            # 卷末：进入杀青
            sc = dict(getattr(vol, "stage_completed", None) or {})
            sc["briefing"] = True
            sc["desk"] = True
            try:
                vs.update_planning(vol_no, stage_completed=sc)
            except Exception:
                pass
            return {"next_chapter": None, "phase": "settle"}
        else:
            # 下一章：ensure 创建，绑定 field_no
            new_work = desk.ensure(cno + 1)
            new_work.field_no = vol_no
            desk._flush()
            return {"next_chapter": cno + 1, "phase": "briefing"}
    return {"next_chapter": None, "phase": "desk"}


def _legacy_next_chapter_brief(
    pid: str,
    vol_no: int,
    body: _LegacyNextBriefIn = Body(...),
) -> dict:
    """向后兼容：旧 POST /next-chapter-brief → 转发到 /brief/options。"""
    deps = _must_deps()
    deps.require_project(pid)
    vs = deps.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    desk = deps.desk_for(pid)
    # 推断下一章号
    prev = body.prev_chapter_no
    if prev is None:
        all_nos = desk.all_nos()
        prev = max(all_nos) if all_nos else 0
    cno = (prev or 0) + 1
    desk.ensure(cno)
    return _brief_options(pid, vol_no, cno, _BriefOptionsIn())
