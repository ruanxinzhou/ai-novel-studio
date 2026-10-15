"""蓝星间章（interlude）端点：4 步向导 → finalize 自动建章 + 第 1 章 brief。

挂载方式：main.py 在 register_settle_routes 之后调用
    from .interlude_routes import register_interlude_routes
    register_interlude_routes(app, require_project=..., volumes_for=...,
                              desk_for=..., chore_provider=...)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.interlude")


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    desk_for: Callable[[str], Any]
    chore_provider: Callable[[str], Any]


_deps: _Deps | None = None


def register_interlude_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    desk_for: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        chore_provider=chore_provider,
    )

    router = APIRouter()
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/interlude",
        _get_plan, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/interlude/ai-suggest",
        _ai_suggest, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/interlude/save-plan",
        _save_plan, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/interlude/finalize",
        _finalize, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("interlude_routes 未调用 register_interlude_routes 初始化")
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
    """mock 短路条件：volume_kind=='interlude' 即走 mock（不用 LLM）。"""
    return getattr(v, "volume_kind", None) == "interlude"


# ---- 请求体模型 ----

class _AiSuggestIn(BaseModel):
    step: str
    context: dict | None = None
    selected: dict | None = None


class _SavePlanIn(BaseModel):
    step: str
    picked: Any


# ---- mock 数据（克苏鲁风格硬编码）----

def _mock_weihedian():
    return [
        {
            "id": "wh-mirror",
            "title": "镜子慢半拍",
            "content": (
                "唐迟回到出租屋的第一晚洗完澡抬头擦镜子，发现镜子里的自己比自己慢大概 0.3 秒眨眼。"
                "他试了几次，确认不是错觉——镜子里的他会在他眨眼之后再眨一次。他用手机录了一段慢动作视频，"
                "视频里镜子里的他对他笑了一下，而他全程没有笑。他删掉了视频，没敢再录第二次。"
            ),
            "how_to_plant": "第一晚洗澡照镜子发现，只作为唐迟视角的微妙异样，不要立刻解释。",
        },
        {
            "id": "wh-contacts",
            "title": "通讯录里多出来的号码",
            "content": (
                "回蓝星一周后翻手机通讯录，唐迟发现多了一个没有备注的号码。他从来没存过，但通话记录里"
                "这个号码每周三凌晨 3:47 都会拨入一次，每次时长 7 秒。回拨永远是忙音。"
                "号码归属地显示是他外婆老家——那个他从未去过、据说在 1927 年整村沉海的渔村。"
            ),
            "how_to_plant": "作为贯穿间章的日常异样，手机里多出的东西比影子更真实、更可追查。",
        },
        {
            "id": "wh-barnacle",
            "title": "手心的藤壶疤",
            "content": (
                "唐迟某天洗手时发现右手手心靠近手腕的位置长了三粒淡褐色的小疤，像藤壶的壳质凸起，"
                "抠不掉，泡水会微微张开像在呼吸。他没敢去医院——他记得副本里他把手按在铜钟内壁的时刻，"
                "那面墙也是这样粗糙、潮湿、有生命的。疤不痛，但每次他说谎的时候会痒。"
            ),
            "how_to_plant": "身体异化类：日常细节中逐渐显现，为下一卷污染加深埋伏笔。",
        },
    ]


def _mock_houchiyizheng():
    return [
        {
            "id": "hy-seasound",
            "title": "梦里总听见海声",
            "content": (
                "唐迟开始反复做同一个梦：自己站在空无一人的沙滩上，远处海面下有合唱声，声音越来越近"
                "但海面上什么都没有。每次醒来枕头是湿的——不是汗，是咸的，尝起来像海水。"
                "第七次做这个梦时他在梦里低头看见自己的脚边有一串湿脚印从海面上走过来，停在他面前。"
            ),
            "how_to_plant": "感官残留：3 次递进——海声+湿枕→脚印轮廓→脚印停在面前戛然而止。",
        },
        {
            "id": "hy-namecall",
            "title": "总觉得背后有人叫他名字",
            "content": (
                "走在地铁通道里、加班到深夜关灯的瞬间、洗澡时水声盖住一切的时候——唐迟总能听见有人"
                "在背后叫他的名字，声音像林薇又像辅导员，回头永远没有人。他开始不敢在凌晨两点之后"
                "独自走走廊，因为他发现每次回头后，走廊尽头的灯都会比刚才多灭一盏。"
            ),
            "how_to_plant": "轻度妄想+幻听：日常场景里的微恐，不要升级成正面遭遇。",
        },
        {
            "id": "hy-shadowlag",
            "title": "影子永远慢半拍",
            "content": (
                "月光下、路灯下、卫生间的暖黄灯光下——唐迟的影子永远无法完全贴合他的身体，大约慢 0.5 秒。"
                "白天阳光直射时看不出来，但阴天或灯光昏暗时非常明显。他试过用手机拍，照片里影子是正常的，"
                "只有他亲眼看到时才慢。他意识到影子已经被古神触碰过，是他自己的一部分但不完全是。"
            ),
            "how_to_plant": "视觉残留：承接 settle 里铜钟仪式后的代价，为影子独立伏笔埋长线。",
        },
    ]


def _mock_xiajuangouzi():
    return [
        {
            "id": "xj-package",
            "title": "不属于自己的快递",
            "content": (
                "间章倒数第一章，唐迟收到一个寄到他出租屋的快递，寄件人一栏写着他外婆的名字——"
                "可他外婆三十年前就死了。快递盒是湿的，里面只有一只红布鞋和一张被海水泡褪色的旧船票，"
                "船票上的目的地是「青水湾」，出发日期是 1927 年农历七月十三。他手机通讯录里那个陌生号码"
                "在他拆快递的瞬间主动发来一条定位：青水湾码头·3 号泊位。"
            ),
            "how_to_plant": "下一卷入场前的最后一击，快递送达当晚他被拉入下一副本。",
        },
        {
            "id": "xj-news",
            "title": "新闻里一模一样的死法",
            "content": (
                "唐迟刷本地新闻看到一条快讯：郊区一家钟楼下发现一具尸体，死者面部保持微笑，"
                "死因不明——和林薇在副本里天台坠楼前的表情一模一样。新闻配图里钟楼内壁有模糊的刻字，"
                "他放大看，是六个名字，最后一个是他自己的。他这才意识到：副本里死的人，"
                "会在蓝星以同样的死法再死一次，只是这次没有人替他们收尸。"
            ),
            "how_to_plant": "新闻类钩子：现代媒体+克苏鲁阴冷，引导唐迟主动追查而非被动拉入。",
        },
        {
            "id": "xj-roommate",
            "title": "室友开始说梦话叫副本里死者的名字",
            "content": (
                "唐迟合租的室友突然开始说梦话，每晚三点准时叫出一个唐迟没听过的名字——"
                "第三天晚上他听清了，是林薇在副本里反复念叨的那个 1927 年失踪的渔村女孩。"
                "他去翻室友的手机，发现室友的搜索记录里全是「青水湾 沉船 1927」，"
                "但室友说他从来没搜过这些。第二天室友出门没再回来，留下一张湿的、写着「来找我」的字条。"
            ),
            "how_to_plant": "羁绊类钩子：身边人被污染，唐迟不得不跟进下一副本救人。",
        },
    ]


def _mock_zhangshu():
    return {
        "suggested_chapters": 2,
        "words_per_chapter": 3500,
        "reason": (
            "蓝星间章建议 2 章、每章 3500 字左右。第 1 章写回蓝星第一晚的违和感（镜子/手机/手心疤），"
            "节奏慢、冷、日常；第 2 章叠加后遗症（海声/幻听/影子慢半拍）+下卷钩子收束（快递/新闻/室友异常），"
            "章尾被拉入下一副本。太短（1章）铺垫不够，太长（3章）会拖慢节奏，2 章是张弛平衡的甜蜜点。"
        ),
    }


def _mock_suggest(step: str) -> dict:
    if step == "weihedian":
        return {"options": _mock_weihedian(), "is_mock": True}
    if step == "houchiyizheng":
        return {"options": _mock_houchiyizheng(), "is_mock": True}
    if step == "xiajuangouzi":
        return {"options": _mock_xiajuangouzi(), "is_mock": True}
    if step == "zhangshu":
        return {**_mock_zhangshu(), "is_mock": True}
    raise HTTPException(status_code=422, detail=f"未知 step：{step}")


# ---- 端点实现 ----

def _get_plan(pid: str, vol_no: int) -> dict:
    """GET 获取间章规划与可写状态。"""
    d, vs, v = _load_vol(pid, vol_no)
    if getattr(v, "volume_kind", None) != "interlude":
        raise HTTPException(status_code=422, detail="不是间章卷")
    plan = getattr(v, "interlude_plan", None) or {}
    steps_done = int(plan.get("steps_done") or 0) if isinstance(plan, dict) else 0
    can_start_writing = bool(plan) and steps_done == 4 and bool(plan.get("finalized_at"))
    return {
        "volume": v.as_dict(),
        "plan": plan,
        "can_start_writing": can_start_writing,
    }


def _find_prev_dungeon(vs) -> Any:
    """找最近一卷 dungeon（volume_kind=='dungeon'）作为上下文来源。"""
    candidates = [vv for vv in vs.all_list() if getattr(vv, "volume_kind", None) == "dungeon"]
    if not candidates:
        return None
    return max(candidates, key=lambda vv: vv.volume_no)


def _ai_suggest(pid: str, vol_no: int, body: _AiSuggestIn) -> dict:
    """POST AI 建议：4 步（weihedian/houchiyizheng/xiajuangouzi/zhangshu）。
    mock 短路；非 mock 路径从上个 dungeon 卷组装上下文再调 LLM。"""
    d, vs, v = _load_vol(pid, vol_no)
    if getattr(v, "volume_kind", None) != "interlude":
        raise HTTPException(status_code=422, detail="不是间章卷")

    step = (body.step or "").strip()
    if step not in {"weihedian", "houchiyizheng", "xiajuangouzi", "zhangshu"}:
        raise HTTPException(status_code=422, detail=f"未知 step：{step}")

    is_mock = _is_mock(v)
    if is_mock:
        return _mock_suggest(step)

    # 组装上下文（从上一 dungeon 卷取）
    ctx_bits: list[str] = []
    try:
        prev = _find_prev_dungeon(vs)
        if prev is not None:
            settle = getattr(prev, "settle", None) or {}
            if isinstance(settle, dict):
                bh = settle.get("blue_star_hook")
                if isinstance(bh, dict):
                    ctx_bits.append(f"【上卷蓝星钩子】\n{(bh.get('detail') or bh.get('title') or '')[:800]}")
                cs = settle.get("cost_summary") or ""
                if cs:
                    ctx_bits.append(f"【上卷代价核算】\n{str(cs)[:800]}")
            outline = getattr(prev, "tangchi_story_outline", None)
            if isinstance(outline, dict) and outline.get("outline_text"):
                ctx_bits.append(f"【上卷唐迟大纲】\n{str(outline.get('outline_text'))[:2000]}")
            hc = getattr(prev, "host_character", None)
            if isinstance(hc, dict):
                ctx_bits.append(
                    f"【主角魂穿身份】{hc.get('name','?')}｜{str(hc.get('body_state',''))[:200]}"
                )
    except Exception:
        pass
    try:
        es_deps = None
        # 污染度从 entities 里取（尝试访问 entities_for；不在 deps 里就跳过）
        pass
    except Exception:
        pass

    system = (
        "你是长篇克苏鲁×无限流小说《第七日的亡者》的蓝星间章规划助手。"
        "唐迟刚结束一场副本回到蓝星现实，需要设计间章（1-3 章）的违和点、后遗症、下卷钩子与章数。"
        "克苏鲁基调：不是大张旗鼓的恐怖，而是日常里微妙的不对劲——手机多了不认识的联系人、"
        "镜子里自己慢半拍、梦里听见海声、手心长出藤壶状小疤、背后有人叫名字但回头没有人。"
        "严格按要求输出 JSON，不要输出 JSON 以外的任何文字。"
    )

    if step == "weihedian":
        schema_desc = (
            '{"options": [{"title": "违和点短标题（10字内）", '
            '"content": "具体描述（80-120字，克苏鲁阴冷基调：微妙的不对劲）", '
            '"how_to_plant": "如何在间章中埋设（40字内）"}]}'
        )
        user = (
            "给出 3 个差异明显的「违和点」候选：唐迟回到蓝星后发现哪些从副本带回来的异常"
            "（手机异常/镜面异常/身体异化等，3 个类型要不同）。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    elif step == "houchiyizheng":
        schema_desc = (
            '{"options": [{"title": "后遗症短标题（10字内）", '
            '"content": "具体描述（80-120字，心理/生理/能力后遗症，要有代价感）", '
            '"how_to_plant": "如何在间章中展现（40字内）"}]}'
        )
        user = (
            "给出 3 个差异明显的「副本后遗症」候选：轻度妄想、特定声音恐惧、新获得某种'感知'但伴随代价、"
            "身上的印记等。要有长期代价感，不要直接给超能力福利。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    elif step == "xiajuangouzi":
        schema_desc = (
            '{"options": [{"title": "钩子短标题（10字内）", '
            '"content": "具体描述（80-120字：下一副本召唤信号如何出现，要让人脊背发凉）", '
            '"how_to_plant": "如何在间章尾/下一卷开头衔接（40字内）"}]}'
        )
        user = (
            "给出 3 个差异明显的「下卷钩子」候选：下一副本的召唤信号如何出现"
            "（快递/新闻/陌生号码/身边人异常等不同类型）。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )
    else:  # zhangshu
        schema_desc = (
            '{"suggested_chapters": 1-3的整数, '
            '"words_per_chapter": 2500-5000的整数, '
            '"reason": "建议理由（80字内，说明节奏考量）"}'
        )
        user = (
            "根据上下文建议蓝星间章长度（章数 1-3，每章字数 2500-5000），"
            "给出章数、每章字数、节奏理由。返回单条对象。"
            f"只输出 JSON，schema：{schema_desc}\n\n"
            + "\n\n".join(ctx_bits)
        )

    from ..generation import complete_structured

    provider = d.chore_provider("interlude")

    def generate() -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return provider.chat(messages).text

    try:
        completion = complete_structured(generate)
    except Exception as e:
        log.warning("interlude ai-suggest step=%s LLM failed, falling back to mock: %s", step, e)
        return {**_mock_suggest(step), "is_mock": True, "llm_error": str(e)[:200]}

    data = completion.data if isinstance(completion.data, dict) else {}
    data = {**data, "is_mock": False}
    return data


_VALID_STEPS = {"weihedian", "houchiyizheng", "xiajuangouzi", "zhangshu"}
_STEP_ORDER = ["weihedian", "houchiyizheng", "xiajuangouzi", "zhangshu"]


def _save_plan(pid: str, vol_no: int, body: _SavePlanIn) -> dict:
    """POST 把某一步选中结果写入 vol.interlude_plan.{step}。"""
    d, vs, v = _load_vol(pid, vol_no)
    if getattr(v, "volume_kind", None) != "interlude":
        raise HTTPException(status_code=422, detail="不是间章卷")
    step = (body.step or "").strip()
    if step not in _VALID_STEPS:
        raise HTTPException(status_code=422, detail=f"未知 step：{step}")

    plan = getattr(v, "interlude_plan", None)
    if not isinstance(plan, dict):
        plan = {}
    plan = dict(plan)  # 浅拷贝
    plan[step] = body.picked
    plan["updated_at"] = _now()
    # 计算 steps_done：已选中的 step 数
    done = sum(1 for s in _STEP_ORDER if plan.get(s) is not None)
    plan["steps_done"] = done
    v.interlude_plan = plan
    vs.save(v)
    return {"ok": True, "plan": v.interlude_plan}


def _finalize(pid: str, vol_no: int) -> dict:
    """POST 定稿间章规划：校验 4 步完成 → 创建 N 章 → 第 1 章写 brief。"""
    d, vs, v = _load_vol(pid, vol_no)
    if getattr(v, "volume_kind", None) != "interlude":
        raise HTTPException(status_code=422, detail="不是间章卷")

    plan = getattr(v, "interlude_plan", None)
    if not isinstance(plan, dict):
        raise HTTPException(status_code=422, detail="间章规划不存在，请先完成 4 步")
    steps_done = int(plan.get("steps_done") or 0)
    if steps_done != 4:
        raise HTTPException(status_code=422, detail=f"间章规划未完成（{steps_done}/4 步），无法定稿")

    zs = plan.get("zhangshu") or {}
    n = int(zs.get("suggested_chapters") or 0)
    if n < 1 or n > 3:
        raise HTTPException(status_code=422, detail=f"章数非法：{n}（应在 1-3 之间）")

    # 写 finalized_at
    plan = dict(plan)
    plan["finalized_at"] = _now()
    v.interlude_plan = plan
    vs.save(v)

    # 创建 N 章（desk.ensure）
    desk = d.desk_for(pid)
    existing_nos = desk.all_nos()
    start_no = (max(existing_nos) + 1) if existing_nos else 1
    created: list[int] = []

    weihedian = plan.get("weihedian") or {}
    wh_content = ""
    if isinstance(weihedian, dict):
        wh_content = weihedian.get("content") or weihedian.get("title") or ""
    elif isinstance(weihedian, str):
        wh_content = weihedian

    for i in range(n):
        no = start_no + i
        title = "回家：第一件不对劲的事" if i == 0 else f"蓝星间章·第{i + 1}节"
        work = desk.ensure(no, title=title)
        work.field_no = getattr(v, "field_no", None)
        work.is_interlude = True
        if i == 0:
            work.brief = {
                "selected": {
                    "title": "回家：第一件不对劲的事",
                    "summary": wh_content or "唐迟回到蓝星出租屋的第一晚，察觉了第一件不对劲的事。",
                    "is_interlude": True,
                },
                "selected_at": _now(),
            }
        else:
            work.brief = None
        created.append(no)
    desk._flush()

    return {
        "ok": True,
        "chapters_created": created,
        "volume": v.as_dict(),
    }
