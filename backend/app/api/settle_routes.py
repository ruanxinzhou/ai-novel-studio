"""杀青（settle）端点：伏笔检测 → 归账 apply → finalize 完成 + AI 建议。

挂载方式：main.py 末尾调用
    from .settle_routes import register_settle_routes
    register_settle_routes(app, require_project=..., volumes_for=..., desk_for=...,
                           entities_for=..., chore_provider=...)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.settle.detector import detect_foreshadows

log = logging.getLogger("api.settle")


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    entities_for: Callable[[str], Any]
    chore_provider: Callable[[str], Any]


_deps: _Deps | None = None


def register_settle_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
    entities_for: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        entities_for=entities_for,
        chore_provider=chore_provider,
    )

    router = APIRouter()
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/settle/overview",
        _overview, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/settle/ai-suggest",
        _ai_suggest, methods=["POST"],
    )
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


def _load_vol(pid: str, vol_no: int):
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return d, vs, v


def _is_mock(v) -> bool:
    """mock 短路条件：adapted_premise 以「测试副本核文本」开头 或 host_character.name 含「林薇」。"""
    from ..book.volume import adapted_premise_of
    premise = adapted_premise_of(getattr(v, "adapted_module", None))
    if isinstance(premise, str) and premise.startswith("测试副本核文本"):
        return True
    hc = getattr(v, "host_character", None)
    if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
        return True
    return False


# ---- 请求体模型 ----

class _ForeshadowDecision(BaseModel):
    id: str
    action: str             # "pay" | "drop" | "carry"
    note: str = ""


class _EntityUpdate(BaseModel):
    id: str
    status: str = ""
    san_delta: int = 0
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
    entity_updates: list[_EntityUpdate] = []
    cost_summary: str = ""
    blue_star_hook: dict | None = None
    next_volume_hint: dict | None = None


class _AiSuggestIn(BaseModel):
    step: str
    context: dict | None = None


# ---- 端点实现 ----

def _overview(pid: str, vol_no: int) -> dict:
    """GET 杀青 6 步初始数据聚合（纯本地数据，不调 AI）。"""
    d, vs, v = _load_vol(pid, vol_no)
    es = d.entities_for(pid)
    desk = d.desk_for(pid)
    foreshadows = detect_foreshadows(es, desk, field_no=v.field_no)

    # 统计本卷章数/字数/死亡数
    chapters_written = 0
    total_words = 0
    for no in desk.all_nos():
        ch = desk.get(no)
        if ch and ch.field_no == v.field_no:
            chapters_written += 1
            # 用 polished > draft 文字统计
            body = ""
            try:
                from ..wizard import work_text
                body = work_text(ch, "polish") or work_text(ch, "draft") or ""
            except Exception:
                pass
            total_words += len(body or "")

    death_count = len(getattr(v, "death_nodes", []) or [])
    try:
        current_contamination = int(getattr(es.protagonist, "contamination_level", 0) or 0)
    except Exception:
        current_contamination = 0

    is_mock = _is_mock(v)
    return {
        "volume": v.as_dict(),
        "foreshadows": foreshadows,
        "chapters_written": chapters_written,
        "total_words": total_words,
        "death_count": death_count,
        "current_contamination": current_contamination,
        "is_mock": is_mock,
    }


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


# ---- AI suggest mock 数据（克苏鲁风格硬编码）----

def _mock_entity_checks():
    return [
        {
            "entity_id": "linwei",
            "name": "林薇",
            "status_after": "dead",
            "san_delta": -2,
            "note": "Day5 天台坠楼事件中被自己的影子拖下楼，尸体无影子——她已经不是活着回来的那个林薇，必须标为死亡实体。",
        },
        {
            "entity_id": "fudaoyuan",
            "name": "辅导员",
            "status_after": "corrupted",
            "san_delta": -1,
            "note": "铜钟内壁的六次刻名显示辅导员是 1927 年海难唯一幸存者，靠仪式续命近百年，已不是人类。",
        },
        {
            "entity_id": "tangchi_shadow",
            "name": "唐迟的影子",
            "status_after": "awakened",
            "san_delta": 0,
            "note": "天台水渍里唐迟看到自己的影子在缓慢转身——影子已经被古神触碰过，是后续卷的关键伏笔实体。",
        },
    ]


_MOCK_COST = {
    "cost_summary": (
        "本副本唐迟的代价触达「存在」层面：第七日钟楼破局要求他主动拒绝合唱，亲手把残留人性的辅导员按入仪式阵眼。"
        "出本后他的影子永远无法完全贴合身体（月光下慢半拍），且记不起林薇和苏雅的名字——只记得她们的脸。"
        "污染度累积 +3，新增死者残影「林薇（记者）」，掌握手艺「铜钟铭文（初窥）」。"
    ),
    "suggested_contamination_delta": 3,
    "san_loss_note": (
        "理智创伤主要来自两处：一是看见林薇坠楼时的'不属于她的微笑'（-1 SAN），"
        "二是亲手杀死仍残留人性的辅导员（-2 SAN）。累计 -3，未达疯狂阈值，但长期轻度解离。"
    ),
    "new_echo": "林薇（小报记者）——执念是'把真相拍下来'，残影 fade_stage=初现，会在唐迟经过玻璃/水面时借倒影递相机。",
    "kill_add": "铜钟仪式后唐迟亲手把辅导员按入阵眼，kill_record 新增一行「辅导员·1927→第七日·仪式反噬」。",
}


def _mock_blue_star_hooks():
    raw = [
        {
            "id": "hook-contacts",
            "title": "通讯录里多出来的号码",
            "detail": (
                "唐迟回到蓝星一周后，翻手机通讯录发现多了一个没有备注的号码。"
                "他从来没存过，但通话记录里这个号码每周三凌晨 3:47 都会拨入一次，每次时长 7 秒。"
                "回拨永远是忙音。号码归属地显示是他外婆老家——那个他从未去过、据说在 1927 年整村沉海的渔村。"
            ),
            "summary": "下一卷（蓝星间章）开头刚洗完澡发现，作为贯穿间章的日常异样，不要立刻解释。",
        },
        {
            "id": "hook-sea",
            "title": "梦里总听见海声",
            "detail": (
                "唐迟回到蓝星后开始反复做同一个梦：自己站在空无一人的沙滩上，远处海面下有合唱声，"
                "声音越来越近但海面上什么都没有。每次醒来枕头是湿的——不是汗，是咸的，尝起来像海水。"
                "第七次做这个梦时，他在梦里低头看见自己的脚边有一串湿脚印从海面上走过来，停在他面前。"
            ),
            "summary": "间章日常片段穿插 3 次递进：海声+湿枕→脚印轮廓→脚印停在面前戛然而止，钩子留到下一副本入场。",
        },
        {
            "id": "hook-mirror",
            "title": "镜子里多眨了一下眼",
            "detail": (
                "唐迟某天刷牙抬头看镜子，发现镜子里的自己比自己慢了大概 0.3 秒眨眼。"
                "他试了几次，确认不是错觉——镜子里的他会在他眨眼之后再眨一次。"
                "他用手机录了一段慢动作视频，视频里镜子里的他对他笑了一下，而他全程没有笑。"
                "他删掉了视频。"
            ),
            "summary": "下一副本入场前的日常小片段，一句话带过，为影子独立伏笔埋长线。",
        },
    ]
    return raw


def _mock_next_hints():
    raw = [
        {
            "id": "next-metro",
            "title": "末班地铁·青水湾站",
            "entry_point": (
                "唐迟加班晚归坐上末班车，列车在广播里没有报过的「青水湾」站停车，"
                "上来的乘客全都没有脚。他低头看自己的鞋——鞋还在，但鞋尖是湿的。"
            ),
            "info_gap": (
                "通讯录里那个每周三凌晨拨入的号码归属地「青水湾」与这个不存在的地铁站同名。"
                "唐迟开局不知道青水湾站只拉「活人终点站」的客，也不知道下车的人再也回不到蓝星。"
            ),
            "summary": "中等·规则怪谈型副本，承接通讯录号码钩子。",
        },
        {
            "id": "next-grandma",
            "title": "外婆的旧筒子楼",
            "entry_point": (
                "唐迟收到外婆去世的消息回旧居收拾遗物，梅雨季节的老筒子楼每层楼道口都摆着一双湿透的红布鞋，"
                "整层楼没有住过女人。凌晨 2:47 门外有人喊他的名字，鞋尖开始缓缓转向他家门。"
            ),
            "info_gap": (
                "唐迟外婆老家 1927 年沉海，旧居的铜镜、油纸伞、红布鞋都是 1927 年遗物——上一个副本沉海的 47 人怨念通过血缘回流。"
                "他开局不知道喊他名字的是外婆还是沉海的「她」。"
            ),
            "summary": "困难·民俗阴冷型副本，承接海声/渔村钩子。",
        },
        {
            "id": "next-hospital",
            "title": "医院夜班·3 号病房",
            "entry_point": (
                "唐迟因长期失眠去医院做检查，临时替一位请假的朋友顶夜班，"
                "发现住院部 3 号病房每天熄灯后都会多出一个病人，病历上写着同一个名字，"
                "但脸每天换一张。他差点给那个病人开了处方——处方上的药第二天出现在他自己的医嘱里。"
            ),
            "info_gap": (
                "林薇残影在倒影里递相机的瞬间，相机屏幕上闪过一张 3 号病房的床头卡——"
                "他开局不知道 3 号病房的病人其实都是林薇生前「未写完的报道」里失踪的人。"
            ),
            "summary": "简单·日常规则型副本，承接林薇残影钩子。",
        },
    ]
    return raw


def _mock_suggest(step: str) -> dict:
    if step == "entity_check":
        return {"entity_checks": _mock_entity_checks(), "is_mock": True}
    if step == "cost":
        return {**_MOCK_COST, "is_mock": True}
    if step == "blue_star_hook":
        return {"hooks": _mock_blue_star_hooks(), "is_mock": True}
    if step == "next_volume_hint":
        return {"hints": _mock_next_hints(), "is_mock": True}
    raise HTTPException(status_code=422, detail=f"未知 step：{step}")


def _ai_suggest(pid: str, vol_no: int, body: _AiSuggestIn) -> dict:
    """POST AI 建议：4 个 step（entity_check/cost/blue_star_hook/next_volume_hint）。
    都走 complete_structured 单次调用（短任务，无 SSE）；mock 短路返回硬编码。"""
    d, vs, v = _load_vol(pid, vol_no)
    step = (body.step or "").strip()
    if step not in {"entity_check", "cost", "blue_star_hook", "next_volume_hint"}:
        raise HTTPException(status_code=422, detail=f"未知 step：{step}")

    is_mock = _is_mock(v)
    if is_mock:
        return _mock_suggest(step)

    # 组装上下文（纯读取，不抛错就用）
    ctx_bits: list[str] = []
    try:
        cards = getattr(v, "scaffold_cards", {}) or {}
        ct = (cards.get("core_twist") or {}).get("text") or ""
        cc = (cards.get("cost_cap") or {}).get("text") or ""
        if ct:
            ctx_bits.append(f"【副本核心反转】\n{ct}")
        if cc:
            ctx_bits.append(f"【代价上限】\n{cc}")
    except Exception:
        pass
    try:
        dns = getattr(v, "death_nodes", []) or []
        if dns:
            ctx_bits.append("【死亡节点】\n" + "\n".join(
                f"- Day{n.get('day','?')} {n.get('who','?')}：{n.get('how','')}"
                for n in dns[:8] if isinstance(n, dict)
            ))
    except Exception:
        pass
    try:
        cns = getattr(v, "climax_nodes", []) or []
        if cns:
            ctx_bits.append("【高潮节点】\n" + "\n".join(
                f"- Day{n.get('day','?')}《{n.get('title','?')}》：{n.get('beat','')}"
                for n in cns[:8] if isinstance(n, dict)
            ))
    except Exception:
        pass
    try:
        outline = getattr(v, "tangchi_story_outline", None)
        if isinstance(outline, dict) and outline:
            outline_text = outline.get("outline_text") or ""
            if outline_text:
                ctx_bits.append(f"【唐迟大纲】\n{outline_text[:3000]}")
    except Exception:
        pass
    try:
        es = d.entities_for(pid)
        p = es.protagonist
        ctx_bits.append(
            f"【主角状态】污染度 {p.contamination_level}/100｜已完成副本 {p.field_count} 场\n"
            f"蓝星羁绊：{'、'.join(b.get('name','?') for b in (p.blue_star_bonds or [])[:5])}"
        )
    except Exception:
        pass
    try:
        desk = d.desk_for(pid)
        last_chapter_no = 0
        last_body = ""
        from ..wizard import work_text
        for no in desk.all_nos():
            ch = desk.get(no)
            if ch and ch.field_no == v.field_no and no > last_chapter_no:
                last_chapter_no = no
                last_body = work_text(ch, "polish") or work_text(ch, "draft") or ""
        if last_body:
            ctx_bits.append(f"【本卷末章（第{last_chapter_no}章）尾 1200 字】\n{last_body[-1200:]}")
    except Exception:
        pass

    system = (
        "你是长篇克苏鲁×无限流小说《第七日的亡者》的杀青归账助手。"
        "作者刚写完一副本（dungeon 卷）所有章节，需要你协助清点伏笔、检查实体状态、核算代价、"
        "设计蓝星钩子（副本在蓝星现实里留下的细微残留痕迹）、预告下一副本。"
        "严格按要求输出 JSON，不要输出 JSON 以外的任何文字。"
    )

    if step == "entity_check":
        schema_desc = (
            '{"checks": [{"entity_name": "实体名", "current_status": "当前状态", '
            '"suggested_status": "建议改为", "reason": "原因（60字内）", '
            '"san_delta": 理智变化整数，负值扣理智}]}'
        )
        user = (
            "根据以下副本资料，列出本卷结束后状态需要修正的关键实体（死者/被污染者/觉醒的诡异实体），"
            f"给出建议状态和原因。只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    elif step == "cost":
        schema_desc = (
            '{"cost_summary": "代价核算一段话（150字内），描述唐迟本副本付出了什么", '
            '"suggested_contamination_delta": 0-5 的整数, '
            '"san_loss_note": "理智损耗说明（80字内）", '
            '"new_echo": "新增死者残影一段话（如有，没有留空）", '
            '"kill_add": "kill_record 新增条目（如有，没有留空）"}'
        )
        user = (
            "根据以下副本资料，核算本副本唐迟的总账：污染度增加多少、理智损耗多少、"
            f"是否新增死者残影/手艺/击杀记录。只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    elif step == "blue_star_hook":
        schema_desc = (
            '{"hooks": [{"title": "钩子短标题（10字内）", '
            '"content": "钩子具体内容（80-120字，克苏鲁基调：日常里残留的副本痕迹，要让人后背发凉但不夸张）", '
            '"how_to_plant": "下一卷（蓝星间章）如何埋设这个钩子（40字内）", '
            '"weight": 1-3 权重整数}]}'
        )
        user = (
            "给出 3 个差异明显的「蓝星钩子」候选：副本结束唐迟回到蓝星现实后，"
            "在日常生活里残留的、不属于蓝星的细微异样痕迹（类似手机多了不存在联系人、梦里听见海声、镜子里的自己慢半拍）。"
            "3 个钩子要有不同类型（物件异常/感官残留/身体异化），克苏鲁阴冷基调。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    else:  # next_volume_hint
        schema_desc = (
            '{"hints": [{"title": "下一副本小标题（10字内）", '
            '"tease": "开场钩子一段话（80字内，画面感强）", '
            '"connected_clue": "如何与本卷钩子/线索衔接（40字内）", '
            '"difficulty": "简单/中等/困难·类型标签"}]}'
        )
        user = (
            "给出 3 个差异明显的「下一副本预告」候选：唐迟将进入的下一场副本的地点、规则诡异、开场钩子，"
            "要能自然衔接本卷留下的蓝星钩子（比如通讯录陌生号码→接到陌生来电→被拉入副本）。"
            "3 个副本要有不同场景（现代日常怪谈/民俗/心理），克苏鲁基调。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )

    from ..generation import complete_structured

    provider = d.chore_provider("settle")

    def generate() -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return provider.chat(messages).text

    try:
        completion = complete_structured(generate)
    except Exception as e:
        log.warning("settle ai-suggest step=%s LLM failed, falling back to mock: %s", step, e)
        return {**_mock_suggest(step), "is_mock": True, "llm_error": str(e)[:200]}

    data = completion.data if isinstance(completion.data, dict) else {}
    # 补 is_mock=false
    data = {**data, "is_mock": False}
    return data


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
                pass
        except ValueError:
            pass
        decisions_out.append({"id": dec.id, "action": dec.action, "note": dec.note})

    # 2. 处理 entity_updates：更新实体状态 + 累加 san_delta 到 contamination_delta
    entity_updates_out = []
    extra_contam_from_san = 0
    for eu in body.entity_updates:
        try:
            upd_kwargs: dict[str, Any] = {}
            if eu.status:
                upd_kwargs["status"] = eu.status
            if upd_kwargs:
                es.update(eu.id, **upd_kwargs)
        except ValueError:
            pass
        if eu.san_delta:
            # san_delta 负值（理智下降）→ 污染增加其绝对值
            extra_contam_from_san += max(0, -int(eu.san_delta))
        entity_updates_out.append({
            "id": eu.id, "status": eu.status, "san_delta": eu.san_delta, "note": eu.note,
        })

    # 合并 contamination_delta（作者手动值 + 实体检查 san_delta 折算）
    final_contamination_delta = int(body.contamination_delta or 0) + extra_contam_from_san

    # 3. 应用 settle diff 到主角状态
    diff = {
        "contamination_delta": final_contamination_delta,
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

    # 4. 写 v.settle（含新增 4 字段）
    v.settle = {
        "applied_at": _now(),
        "foreshadow_decisions": decisions_out,
        "entity_updates": entity_updates_out,
        "cost_summary": body.cost_summary or "",
        "blue_star_hook": body.blue_star_hook or None,
        "next_volume_hint": body.next_volume_hint or None,
        "diff_summary": summary,
        "final_chapter_no": last_chapter_no,
        "contamination_delta": final_contamination_delta,
    }
    vs.save(v)

    return {"ok": True, "settle": v.settle}


def _finalize(pid: str, vol_no: int) -> dict:
    """POST 杀青定稿：dungeon 标 settle 完成进入 interlude 引导；interlude 标 interlude_plan 完成。
    返回 next_action_options 供前端选择「间章/下一副本」。"""
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
    return {
        "ok": True,
        "volume": v.as_dict(),
        "next_action_options": ["interlude", "next_dungeon"],
    }
