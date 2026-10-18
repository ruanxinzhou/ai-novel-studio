"""工作台 embody 阶段（唐迟魂穿）路由：
选身体（candidates）→ 选切入点（entry-points）→ 选立场（stances）
→ 5 版大纲并行生成（generate-outline, JobStore + SSE）→ 选版落盘（pick-outline）。

挂载方式：main.py 末尾调用
    from .embody_routes import register_embody_routes
    register_embody_routes(app, require_project=..., volumes_for=..., chore_provider=...)
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    chore_provider: Callable[[str], Any]


_deps: _Deps | None = None


def register_embody_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        chore_provider=chore_provider,
    )

    router = APIRouter()
    prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/embody"
    router.add_api_route(f"{prefix}/candidates", _list_candidates, methods=["GET"])
    router.add_api_route(f"{prefix}/entry-points", _gen_entry_points, methods=["POST"])
    router.add_api_route(f"{prefix}/stances", _gen_stances, methods=["POST"])
    router.add_api_route(f"{prefix}/generate-outline", _start_outline5, methods=["POST"])
    router.add_api_route(f"{prefix}/jobs", _list_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/stream", _stream_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/pick-outline", _pick_outline, methods=["POST"])
    router.add_api_route(f"{prefix}/back-to-simulate", _back_to_simulate, methods=["POST"])
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("embody_routes 未调用 register_embody_routes 初始化")
    return _deps


# ---- 请求体 ----

class _CandidateRef(BaseModel):
    candidate_id: str | None = None
    candidate: dict | None = None


class _EntryPointsIn(_CandidateRef):
    user_directive: str = ""


class _StancesIn(BaseModel):
    candidate: dict | None = None
    entry_point: dict | None = None
    user_directive: str = ""


class _GenerateOutlineIn(BaseModel):
    candidate: dict | None = None
    entry_point: dict | None = None
    stance: dict | None = None
    user_directive: str = ""
    n: int = 5


class _PickOutlineIn(BaseModel):
    job_id: str = ""
    pick_index: int = -1
    candidate: dict | None = None
    entry_point: dict | None = None
    stance: dict | None = None
    custom_data: dict | None = None  # 自定义大纲数据，存在则跳过 job 校验


# ---- 帮助函数 ----

def _session_id(pid: str, vol_no: int) -> str:
    return f"embody:{pid}:{vol_no}"


def _get_volume_or_404(pid: str, vol_no: int):
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return vs, v


def _is_mock(adapted_module: str | None, candidate: dict | None) -> bool:
    if adapted_module and adapted_module.startswith("测试副本核文本"):
        return True
    if candidate and isinstance(candidate.get("name"), str) and "林薇" in candidate["name"]:
        return True
    return False


# ========== v2.0 三折死因链真 LLM Prompt ==========

_CANDIDATES_SYSTEM = """你是《第七日的亡者》的魂穿宿主策划师。
在这部小说中，主角唐迟进入每个克苏鲁副本时，可以魂穿成剧本里的【任意一个角色】——绝不局限于调查员玩家位！可以是宿管、保安、校医、死者的室友、被诅咒的边缘 NPC、嫌疑人、甚至邪教准备的祭品容器。

【双轨时间线·核心铁律——务必在脑中分清】
1. 副本（COC模组改编 + 跑团推演）是一个**独立完整的故事**，它有自己的时间线——可能是几小时（午夜惊魂）、几天、几个月、几年、几代人不等。副本里的事件按自己的逻辑推进，**副本本身不要求有七日结构，死者也不一定死在副本结尾**。
2. 唐迟个人的「七天倒计时」是他魂穿那一刻才启动的求生锚：他睁眼 = T-7，讣告预告此人在**他自己的第七天（T-0）**会死于某种凄惨死法。这个「第七日死状」是**这个角色在原剧本中的命运节点**——它可能落在副本故事的中段、高潮、或者原剧本所谓的结尾，取决于他穿入时点离该角色命定死劫还有多远。
3. 两个时间线并行：副本事件按原剧本走，唐迟在里面活一天算一天，到 T-0 那天死劫必然降临。

唐迟的核心主线：开局通过讣告得知这具身体「自己第七日必死」的凄惨死状（果），但他失去了原主记忆，必须在七天内查清「为什么这个角色在原剧本里会死」（因），并在 T-0 截断死因求生。

请基于给定的【副本改编稿】和【跑团推演记录】，挑选出 5 个最具悬疑张力与求生戏份的魂穿宿主候选（必须包含调查员 PL 与倒霉 NPC/祭品/边缘知情者的混合，不要全是调查员）。
每个候选必须有清晰的「第七日死状（果）」和「作者预览的真实死因（因）」，二者要有距离，给 Day4-5（唐迟入戏第4-5天）反转留空间。
每个候选还必须说清：唐迟穿入时，原剧本的故事正在什么节点、离这个角色命定死劫在原剧本时间上有多远。

严格输出 JSON（不要输出 JSON 之外任何文字，不要 Markdown 包裹）：
{
  "candidates": [
    {
      "id": "c1",
      "name": "角色姓名",
      "role": "剧本中的具体身份（必须以「调查员 / 关键NPC / 边缘知情者 / 祭品容器 / 嫌疑人」开头标注类型，再接角色职业/关系）",
      "age": 年龄数字或null,
      "body_state": "当前身体的异常状态（倒计时/已死借壳/记忆缺失/局部异化/被替身在扮演 等）",
      "original_story_position": "唐迟穿入这一刻，原剧本自身的故事正处于哪个阶段/事件？（如「副本中段·调查员已经撞破校医院地下室秘密当晚」「副本高潮前20分钟·钟楼献祭仪式即将开始」「副本结尾·调查员以为自己已经逃出生天」「副本开头·第一名死者还没出现」等；30-60字）",
      "seventh_day_death": "讣告预告该角色在【唐迟自己的第七日（T-0）】的具体死状（30-60字，阴冷具象，禁止空泛形容词；这是唐迟视角倒计时的终点，不是副本的第七天）",
      "hidden_death_cause": "【作者预览】为什么这具身体在原剧本中注定会死/遭遇毁灭性危机？（原主在穿入时点前隐瞒的罪孽/契约/替身真相，40-80字，死因必须长在原主自己身上，不能是「坏人杀了无辜受害者」）",
      "struggle_hook": "唐迟穿入后如何利用该身份在调查员与怪谈之间周旋求生（40-80字）",
      "info_gap_hint": "唐迟开局完全不知道、必须先从自己身上或房间里查起的核心盲区（30-60字）",
      "san_loss": "原主当前 SAN 剩余估计（如「已掉35/99」「SAN已归零」「剩4/99」）",
      "death_countdown": "穿入时点离【唐迟自己T-0死劫】的紧迫度（如「还剩6天」「天台外悬命10秒」「身份被揭穿即死」「距离仪式启动只剩20分钟」等）",
      "tag": "一句话标签（如「倒计时型」「身份诡计型」「SAN崩盘型」「替死容器型」「借刀调查员型」）"
    }
  ]
}"""


_ENTRY_POINTS_SYSTEM = """你是《第七日的亡者》的切入点设计师。
唐迟已魂穿进选定角色体内。请基于副本背景与角色身份，设计 4 个不同的「睁眼第一秒切入点」方案。

【双轨时间线提醒】
- 每个切入点要明确：原剧本的故事此刻正走到哪里（副本时间锚点），这个节点距离该角色命定死劫（=唐迟 T-0）在原剧本时间上还剩多少剧情量。
- 切入点可以落在原剧本的任何位置：故事起点、中段、终局前20分钟、甚至主角原主已经死过一次的诈尸点——但不管落点在哪里，唐迟自己的 T-7→T-0 七天倒计时**从他睁眼这一刻正式启动**。
- 副本可能本身只有几小时剧情，也可能跨越几个月；唐迟的七天是他的求生锚，不要求副本原剧本正好七天。

每个切入点要明确唐迟当时的身体姿态、眼前正发生什么、他第一秒要做什么、副本此刻正处于哪个事件、以及他【不知道但必须尽快查】的关键信息差是什么。
切入点的差异要体现在：信息差深度、伪装难度、开场紧迫度、他在原剧本时间线中的位置、以及他是否已经被原剧本调查员盯上。

严格输出 JSON：
{
  "entry_points": [
    {
      "id": "e1",
      "name": "切入点短标题（8字内，如「天台悬命」「解剖室苏醒」「值班室绳圈」）",
      "description": "唐迟睁眼瞬间的身体状态、眼前场景、第一秒的危机动作（60-100字，要有画面感）",
      "original_timeline_anchor": "此刻原剧本故事正在哪个事件/阶段？距离这个角色命定死劫在原剧本时间上还有多少剧情量？（如「副本中段·林薇已查到地下室秘密被推下楼瞬间」「副本高潮前20分钟·钟楼献祭仪式开场」；30-60字）",
      "info_gap": "他此刻完全不知道、必须在几小时内搞清楚的核心盲区（30-60字，要具体）",
      "opening_pressure": "开场紧迫度标签（「命悬一线」「身份伪装」「暗处观察」「记忆崩盘」 等）",
      "first_lead": "他睁眼后第一个可摸到/看到/听到的线索（用于第一章开场钩子，30字内）"
    }
  ]
}
不要输出 JSON 之外的任何文字。"""


_STANCES_SYSTEM = """你是《第七日的亡者》的求生姿态设计师。
基于唐迟魂穿的角色身份与切入点，设计 4 种不同的求生姿态/破局策略。姿态的差异体现在：对原剧本调查员的态度（借刀/躲/主动误导）、对自身死因追查的激进程度（藏着查/公开查/反向利用怪谈查）、以及愿意付出的代价量级。
每种姿态都要体现「程序员式逆向思维」的底色（把怪谈规则当代码、把死因当bug来拆解）。

【双轨时间线提醒】
- 姿态设计要兼顾「唐迟自己 T-7→T-0 七天倒计时的紧迫度」和「原剧本事件的自然推进速度」。如果副本原剧本只有几小时剧情（比如终局切入），姿态必须是快节奏的紧迫求生；如果副本原剧本跨度几个月，姿态可以有更长线的伪装、渗透、布局。
- 但无论副本原剧本多长，唐迟自己第七日（T-0）的死劫都必然降临——姿态的策略节奏要围绕这个铁锚展开。

严格输出 JSON：
{
  "stances": [
    {
      "id": "s1",
      "name": "姿态名称（8字内，如「装到底·暗中解剖」「借刀PL·探路石」「主动献祭·反向契约」）",
      "description": "唐迟以这个姿态行动的核心思路、行事风格与七天求生策略路径（60-120字，要兼顾原剧本事件推进节奏）",
      "cost_willingness": "愿意付出什么代价（SAN/身体部位/人际关系/出卖谁）30字内",
      "trust_level": "对原剧本调查员的信任策略（30字内，如「一个都不信，只借手」「装队友真卧底」「挑一个最蠢的当替死鬼」）",
      "risk_tag": "主要风险标签（身份暴露/污染加深/反噬原主因果/被怪谈标记）"
    }
  ]
}
不要输出 JSON 之外的任何文字。"""


_OUTLINE5_SYSTEM = """你是《第七日的亡者》长篇小说专职总纲设计师。
主角唐迟（带着程序员逆向思维的穿越者）已魂穿进选定的倒霉角色体内。他开局只知道讣告上预告的「自己第七日（T-0）死状（果）」，不知道「这具身体为什么在原剧本里会死（因）」。
请基于给定的【副本骨架】【跑团走向】【魂穿宿主】【切入点与立场】，产出一版唐迟视角的七天求生追查死因破局大纲。

========== 【双轨时间线·核心铁律——大纲必须体现】==========
1. 副本原剧本时间线是独立完整的：它有自己的事件推进节奏（可以是几小时、几天、几个月、几代人），**副本原剧本本身不要求有七天结构**。大纲里要写清「唐迟入戏第 N 天，原剧本故事正在哪个事件/时点」，让读者同时感知两条时间线在并行。
2. day_breakdown 中的 day=1..7 指的是「唐迟入戏第 1..7 天（他自己的 T-7+1 → T-0）」，**绝对不是原剧本的 Day1-Day7**。
3. 如果切入点落在原剧本终局前20分钟，那么唐迟的 7 天求生 = 在原剧本终局场景硬扛 20 分钟 → 然后原剧本所谓的「结局」之后还有 6 天多的剧情要原创；如果切入点落在原剧本开头，那么原剧本的 7 天剧情 ≠ 唐迟的 7 天，而是他在原剧本事件流中一边参与一边逆向破局。
4. 三折死因链（Day1-3 假死因/ Day4-5 真相反转/ Day6-7 卡Bug破局）是唐迟**调查破局的弧线**，不是原剧本的结构。

========== 七日死因三折逆向链（硬约束，每版必须严格遵循）==========
【第一折·表象死因与假自救（入戏第1~3天 / T-6~T-4）】：唐迟应付身份伪装危机，通过搜查原主遗物与观察前两日其他死者的死法，归纳出「第一版表面死因假说（红鲱鱼）」，并按此采取自救行动——假说必须看似合理但治标不治本，让读者也信以为真。
【第二折·原主黑历史与死因大反转（入戏第4~5天 / T-3~T-2）】：严格遵循第一版假说的旁人依然惨死！唐迟挖出原主隐藏最深的秘密（罪孽/契约/替身/借壳），发现这具身体在原剧本中必死的真正底层因果——死因必须长在原主自己的过去/契约/身体构造上，不能是「坏人杀了无辜受害者」。
【第三折·程序员卡Bug截断死因（入戏第6~7天 / T-1~T-0）】：唐迟自己 T-0 的死劫如期降临，唐迟不靠武力硬刚，而是利用怪谈杀人规则与讣告判定条件之间的逻辑漏洞（死锁/替死指针移交/判定边界溢出/条件短路/身份契约重定向）截断死因，并付出对等代价（SAN/身体异化/残影/人际关系债务）活下来；破局必须是对规则的「合规越权」——规则没说不允许，但规则的本意绝不是这个。

========== 输出 JSON（严格 schema，不要输出 JSON 以外任何文字）==========
{
  "title": "12字内标题，定这一版的节奏/气质",
  "one_liner": "一句话梗概（40字内）",
  "pacing": "节奏标签（慢热铺陈/反转密集/开场高能/步步紧逼/伪装潜伏/记忆污染 等）",
  "original_module_timeline_note": "一句话说明：这个副本原剧本本身的时间跨度是多长？唐迟入戏切入点落在哪里？（如「原剧本跨度约3天·入戏点是副本中段第二夜林薇坠楼前10秒」「原剧本跨度仅2小时·入戏点是钟楼献祭仪式开场·之后全靠原创延展」；30-80字）",
  "tangchi_entry": "唐迟替换谁、开场身份、睁眼第一秒状态、T-0讣告死法预告、以及此刻原剧本正在哪个事件（80-150字）",
  "day_breakdown": [
    {"day":1,"summary":"入戏第1天梗概80-150字（必须落在第一折·假死因假说建立期，要写清当日死人/线索/唐迟的误判、以及原剧本今天推进到了哪个事件）","original_timeline_anchor":"原剧本今天走到哪个事件/时点（20-40字）","fate_layer_focus":"doomed|diverted|escalated（当日主导的因果锁类型）","false_hypothesis":"唐迟今天归纳出的假死因假说（若是第4-7天则填''）","real_cause_progress":"今日距离真死因揭穿的推进（第1-3天填误导，第4-5天填揭穿，第6-7天填截断）"},
    {"day":2,"summary":"...","original_timeline_anchor":"...","fate_layer_focus":"...","false_hypothesis":"...","real_cause_progress":"..."},
    {"day":3,"summary":"入戏第3天结尾必须让唐迟以为自己的自救即将成功（假象高点）","original_timeline_anchor":"...","fate_layer_focus":"diverted","false_hypothesis":"...","real_cause_progress":"..."},
    {"day":4,"summary":"入戏第4天：按假假说行事的旁人惨死，假说崩塌","original_timeline_anchor":"...","fate_layer_focus":"escalated","false_hypothesis":"","real_cause_progress":"开始摸到原主黑历史"},
    {"day":5,"summary":"入戏第5天：揭穿原主真正死因（底牌揭示）","original_timeline_anchor":"...","fate_layer_focus":"doomed","false_hypothesis":"","real_cause_progress":"真死因揭穿，唐迟知道自己为什么T-0必死"},
    {"day":6,"summary":"入戏第6天：寻找规则漏洞、准备截杀方案、付出预告代价","original_timeline_anchor":"...","fate_layer_focus":"diverted","false_hypothesis":"","real_cause_progress":"开始搭截断死因的逻辑漏洞"},
    {"day":7,"summary":"入戏第7天（T-0）高潮：死劫降临，唐迟卡Bug破局，付出对等代价活下/惨胜","original_timeline_anchor":"原剧本此刻是终点/之后/或者根本还没到终点","fate_layer_focus":"escalated","false_hypothesis":"","real_cause_progress":"死因截断，代价落地"}
  ],
  "false_cause_red_herring": "第1-3天误导唐迟与读者的「表面死因假说」（40-80字）",
  "core_twist": "原主在原剧本中必死的真正底层因果（80-150字，必须是原主自身的罪孽/契约/替身真相）",
  "rule_exploit": "唐迟T-0卡规则漏洞截断死因的具体机制（60-120字，必须是程序员思维下的合规越权，不能是硬刚/感化/神力）",
  "climax": "T-0 高潮戏具体场面与破局过程（100-200字）",
  "cost_cap": "T-0 必须付出的对等代价（50-100字，SAN/异化/残影/人际关系债务，不可减免）",
  "blue_star_hook": "杀青后带回什么蓝星小钩子（40-60字）",
  "foreshadow_planted": ["3-5条长线伏笔，供 scaffold clue_nodes 与未来卷参考"]
}"""


def _call_llm_json(system_prompt: str, user_prompt: str, fallback, tag: str = "embody") -> dict:
    """统一 LLM 结构化调用，失败回退 fallback。"""
    from ..generation import complete_structured
    log = logging.getLogger("api.embody")
    d = _must_deps()
    try:
        def _call():
            return d.chore_provider(tag).chat([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]).text
        comp = complete_structured(_call, max_regenerations=2)
        data = comp.data if isinstance(comp.data, dict) else {}
        if not data:
            return fallback
        return data
    except Exception as e:
        log.warning("embody %s LLM 失败，回退 fallback：%s", tag, e)
        return fallback


def _build_embody_context(v, candidate: dict | None = None, entry_point: dict | None = None,
                           stance: dict | None = None, user_directive: str = "") -> str:
    """构造给 LLM 的上下文（副本骨架+跑团+已选魂穿要素）。"""
    parts: list[str] = []
    adapted = v.adapted_module or ""
    parts.append("===== 副本骨架（改编稿）=====")
    parts.append(adapted[:4000] if isinstance(adapted, str) else json.dumps(adapted, ensure_ascii=False)[:4000])
    sim = v.simulated_run or {}
    sel_variant = sim.get("selected_variant") if isinstance(sim, dict) else None
    if isinstance(sel_variant, dict):
        parts.append("\n===== 选定跑团走向 =====")
        parts.append(f"标签：{sel_variant.get('label','')}")
        parts.append(f"一句话：{sel_variant.get('one_liner','')}")
        death_table = sel_variant.get("death_table") or sel_variant.get("character_deaths")
        if isinstance(death_table, list):
            parts.append("\n《全角色在原剧本中的死因对照表》（作者上帝视角，用来挑魂穿候选；day 指跑团模拟出的原剧本时点，不是唐迟的倒计时）：")
            for row in death_table:
                if isinstance(row, dict):
                    parts.append(f"- 时点{row.get('day','?')}｜{row.get('who','?')}｜死法：{row.get('how','')}｜触发规则：{row.get('rule','')}")
        fl = sel_variant.get("full_log") or ""
        if isinstance(fl, str) and fl:
            parts.append(f"\n跑团日志节选：\n{fl[:2500]}")
    if candidate:
        parts.append("\n===== 已选魂穿宿主 =====")
        parts.append(json.dumps(candidate, ensure_ascii=False))
    if entry_point:
        parts.append("\n===== 已选切入点 =====")
        parts.append(json.dumps(entry_point, ensure_ascii=False))
    if stance:
        parts.append("\n===== 已选求生姿态 =====")
        parts.append(json.dumps(stance, ensure_ascii=False))
    if user_directive.strip():
        parts.append(f"\n===== 作者额外要求 =====\n{user_directive.strip()}")
    return "\n".join(parts)


def _outline_to_text(o: dict) -> str:
    """把一版大纲 JSON 转成可读纯文本（落盘/显示用）。"""
    lines = []
    title = o.get("title") or "（无标题）"
    lines.append(f"# {title}")
    if o.get("pacing"):
        lines.append(f"节奏：{o['pacing']}")
    if o.get("one_liner"):
        lines.append(f"一句话：{o['one_liner']}")
    if o.get("tangchi_entry"):
        lines.append(f"\n魂穿设定：{o['tangchi_entry']}")
    dbs = o.get("day_breakdown") or []
    if dbs:
        if o.get("original_module_timeline_note"):
            lines.append(f"\n原剧本时间线说明：{o['original_module_timeline_note']}")
        lines.append("\n## 唐迟七日求生流程（入戏第1~7天 / T-6 → T-0）")
        for d_item in dbs:
            if isinstance(d_item, dict):
                day = d_item.get("day", "?")
                s = d_item.get("summary", "")
                anchor = d_item.get("original_timeline_anchor", "")
                anchor_str = f"  〔原剧本：{anchor}〕" if anchor else ""
                lines.append(f"- 入戏第{day}天（T-{7-int(day) if isinstance(day,int) else '?'}）：{s}{anchor_str}")
    if o.get("climax"):
        lines.append(f"\n## T-0 第七日高潮\n{o['climax']}")
    if o.get("blue_star_hook"):
        lines.append(f"\n## 杀青蓝星钩\n{o['blue_star_hook']}")
    fp = o.get("foreshadow_planted") or []
    if fp:
        lines.append("\n## 长线伏笔")
        for f_item in fp:
            lines.append(f"- {f_item}")
    return "\n".join(lines)


# ---- 1. 魂穿候选 ----

def _list_candidates(pid: str, vol_no: int):
    vs, v = _get_volume_or_404(pid, vol_no)
    adapted = v.adapted_module or ""
    if _is_mock(adapted, None):
        time.sleep(0.8)
        return {"candidates": _MOCK_CANDIDATES}

    ctx = _build_embody_context(v)
    user_hint = (
        "请基于以上副本骨架与跑团记录，挑出 5 个最具悬疑张力的魂穿宿主候选，"
        "必须混合调查员 PL、关键 NPC、边缘知情者、祭品容器、嫌疑人几类；"
        "每个候选必须有清晰的第七日死状（果）和作者预览的真实死因（因）。"
    )
    fallback = {"candidates": _MOCK_CANDIDATES}
    data = _call_llm_json(_CANDIDATES_SYSTEM, ctx + "\n\n" + user_hint, fallback, tag="candidates")
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list) or not cands:
        cands = _MOCK_CANDIDATES
    # 保证每个候选有 id
    for i, c in enumerate(cands):
        if isinstance(c, dict) and not c.get("id"):
            c["id"] = f"c{i+1}"
    return {"candidates": cands}


# ---- 2. 切入点 ----

def _gen_entry_points(pid: str, vol_no: int, body: _EntryPointsIn):
    vs, v = _get_volume_or_404(pid, vol_no)
    candidate = body.candidate or {}
    if _is_mock(v.adapted_module, candidate):
        time.sleep(0.8)
        return {"entry_points": _mock_entry_points_for(candidate)}

    ctx = _build_embody_context(v, candidate=candidate, user_directive=body.user_directive or "")
    user_hint = (
        f"魂穿宿主为：{candidate.get('name','')}（{candidate.get('role','')}）。"
        "请基于其身份与副本背景，设计 4 个差异化的「睁眼第一秒切入点」方案，"
        "每个方案要明确身体姿态、眼前场景、第一秒危机动作、核心信息差与第一个可摸到的线索。"
    )
    fallback = {"entry_points": _mock_entry_points_for(candidate)}
    data = _call_llm_json(_ENTRY_POINTS_SYSTEM, ctx + "\n\n" + user_hint, fallback, tag="entry_points")
    eps = data.get("entry_points") if isinstance(data, dict) else None
    if not isinstance(eps, list) or not eps:
        eps = _mock_entry_points_for(candidate)
    for i, e in enumerate(eps):
        if isinstance(e, dict) and not e.get("id"):
            e["id"] = f"e{i+1}"
    return {"entry_points": eps}


# ---- 3. 立场 ----

def _gen_stances(pid: str, vol_no: int, body: _StancesIn):
    vs, v = _get_volume_or_404(pid, vol_no)
    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    if _is_mock(v.adapted_module, candidate):
        time.sleep(0.8)
        return {"stances": _mock_stances_for(candidate, entry_point)}

    ctx = _build_embody_context(v, candidate=candidate, entry_point=entry_point,
                                user_directive=body.user_directive or "")
    user_hint = (
        f"魂穿宿主：{candidate.get('name','')}；切入点：{entry_point.get('name','')}。"
        "请基于此角色身份与切入点紧迫度，设计 4 种差异化的求生姿态/破局策略，"
        "每种姿态必须体现程序员逆向思维底色，差异体现在对调查员的态度、查死因激进程度、愿付代价量级。"
    )
    fallback = {"stances": _mock_stances_for(candidate, entry_point)}
    data = _call_llm_json(_STANCES_SYSTEM, ctx + "\n\n" + user_hint, fallback, tag="stances")
    ss = data.get("stances") if isinstance(data, dict) else None
    if not isinstance(ss, list) or not ss:
        ss = _mock_stances_for(candidate, entry_point)
    for i, s in enumerate(ss):
        if isinstance(s, dict) and not s.get("id"):
            s["id"] = f"s{i+1}"
    return {"stances": ss}


# ---- 4. 启动 5 版大纲生成 ----


def _start_outline5(pid: str, vol_no: int, body: _GenerateOutlineIn):
    from ..jobs import job_store, run_in_background
    from ..generation import complete_structured
    log = logging.getLogger("api.embody")
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    stance = body.stance or {}
    user_dir = (body.user_directive or "").strip()
    n = max(2, min(8, int(body.n or 5)))

    job = job_store.create(sid, "embody_outline5", meta={
        "phase": "embody", "n": n,
        "candidate_name": candidate.get("name", ""),
    })

    context_text = _build_embody_context(
        v, candidate=candidate, entry_point=entry_point, stance=stance,
        user_directive=user_dir,
    )

    pacing_seeds = [
        "步步紧逼·每夜都死人",
        "伪装求生·身边都是怪物",
        "记忆污染·分不清自己是谁",
        "逃不掉的轮回·死亡只是开始",
        "最后一天反转·幸存者即祭品",
    ]

    def _work(j, update):
        # mock 短路：测试副本核文本 或 候选名含「林薇」→ 不调 LLM，2 秒返回
        if _is_mock(v.adapted_module, candidate):
            update(progress=5, message="测试模式：mock 生成 5 版大纲…")
            time.sleep(2)
            outs = []
            for i in range(n):
                flavor = pacing_seeds[i % len(pacing_seeds)]
                o = _mock_outline(i, flavor, candidate, entry_point, stance)
                outs.append(o)
                job_store.append_sub_result(job.job_id, o)
                update(progress=10 + int((i + 1) / n * 85),
                       message=f"已生成 {i+1}/{n} 版大纲（mock）")
            update(progress=100, message=f"完成 {n} 版大纲（mock）",
                   result={"outlines": outs})
            return {"outlines": outs}

        # 真实 LLM 路径
        d2 = _must_deps()
        results = [None] * n

        def _run_one(i: int):
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n} 版大纲生成中…",
                   progress=5 + int((i / n) * 85))
            flavor = pacing_seeds[i % len(pacing_seeds)]
            messages = [
                {"role": "system", "content": _OUTLINE5_SYSTEM},
                {"role": "user", "content": context_text
                    + f"\n\n【本版节奏定向】请按「{flavor}」的节奏与克苏鲁基调来写这一版大纲，"
                      f"让这一版和其他版有明显差别。必须严格遵守魂穿四要素约束，"
                      f"唐迟是{flavor}这一版节奏下的求生者，不是全知主角。"},
            ]
            try:
                def _call():
                    return d2.chore_provider("outline").chat(messages).text
                comp = complete_structured(_call, max_regenerations=2)
                data = comp.data if isinstance(comp.data, dict) else {}
                data.setdefault("title", f"大纲第{i+1}版·{flavor}")
                data.setdefault("pacing", flavor)
                data.setdefault("one_liner", "")
                data["index"] = i
                data["flavor"] = flavor
                return i, data
            except Exception as e:
                log.warning("embody outline5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e),
                           "title": f"大纲第{i+1}版（失败）", "flavor": flavor}

        with ThreadPoolExecutor(max_workers=min(n, 3)) as ex:
            futures = {ex.submit(_run_one, i): i for i in range(n)}
            done_cnt = 0
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                if r and not r.get("error"):
                    job_store.append_sub_result(job.job_id, r)
                done_cnt += 1
                update(progress=5 + int((done_cnt / n) * 90),
                       message=f"已完成 {done_cnt}/{n} 版大纲")
        success = [r for r in results if r and not r.get("error")]
        if not success:
            raise RuntimeError("大纲全部失败")
        update(progress=100, message=f"完成 {len(success)}/{n} 版大纲",
               result={"outlines": success})

    run_in_background(job_store, job.job_id, _work)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}


# ---- 5. 列 jobs ----

def _list_jobs(pid: str, vol_no: int):
    from ..jobs.store import job_store
    _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)
    jobs = job_store.list_session(sid)
    return {"jobs": [j.as_dict() for j in jobs]}


# ---- 6. SSE 流 ----

def _stream_jobs(pid: str, vol_no: int):
    from ..jobs.store import job_store, jobs_to_sse_event_list
    _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

    def gen():
        idle_rounds = 0
        while True:
            jobs = job_store.list_session(sid)
            yield jobs_to_sse_event_list(jobs)
            any_active = any(j.status in ("pending", "running") for j in jobs)
            if not any_active:
                idle_rounds += 1
                if idle_rounds >= 3:
                    yield f"event: job_idle\ndata: {json.dumps({'ok': True}, ensure_ascii=False)}\n\n"
                    return
            else:
                idle_rounds = 0
            time.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- 7. 选版落盘 ----

def _pick_outline(pid: str, vol_no: int, body: _PickOutlineIn):
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    stance = body.stance or {}

    # 自定义数据分支：跳过 job 校验，直接用 custom_data 作为选中大纲
    if body.custom_data:
        picked = body.custom_data
    else:
        job = job_store.get(body.job_id)
        if job is None or job.session_id != sid:
            raise HTTPException(status_code=404, detail="job 不存在或不属于该卷")
        outlines = (job.result or {}).get("outlines") or []
        idx = int(body.pick_index)
        if idx < 0 or idx >= len(outlines):
            raise HTTPException(status_code=422, detail=f"pick_index 越界（共 {len(outlines)} 版）")
        picked = outlines[idx]

    # 计算信息差（info_gap）：从切入点里取
    info_gap = entry_point.get("info_gap") or entry_point.get("description") or ""

    outline_text = _outline_to_text(picked)

    v.host_character = candidate
    v.entry_point = entry_point
    v.info_gap = info_gap
    v.stance = stance
    v.tangchi_story_outline = {
        **picked,
        "job_id": body.job_id,
        "outline_text": outline_text,
    }
    v.stage_completed["embody"] = True
    vs.save(v)
    return {"ok": True, "volume": v.as_dict(), "picked": picked}


def _back_to_simulate(pid: str, vol_no: int) -> dict:
    """从魂穿阶段返回跑团阶段：重置 stage_completed.embody 及后续阶段为 False，不清空跑团结果。"""
    vs, v = _get_volume_or_404(pid, vol_no)
    sc = dict(v.stage_completed or {})
    # 保留 simulate 之前的完成状态；只重置 embody 及后续
    for k in ("embody", "scaffold", "rhythm", "briefing", "desk", "settle", "interlude"):
        sc[k] = False
    v.stage_completed = sc
    # 清空魂穿阶段的产出，避免回到魂穿时看到旧选择
    v.host_character = None
    v.entry_point = None
    v.info_gap = None
    v.stance = None
    v.tangchi_story_outline = None
    v.deviation_points = []
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


# ============================================================
# Mock 数据
# ============================================================

_MOCK_CANDIDATES = [
    {
        "id": "linwei",
        "name": "林薇",
        "role": "女大学生/PL队友/调查员",
        "age": 21,
        "body_state": "即将坠楼——身体已经翻过天台护栏，指尖只剩最后半寸抓着水泥沿",
        "original_story_position": "副本中段·林薇当晚查到校医院地下室秘密后被「它」推下楼；原剧本中她在此刻必死",
        "seventh_day_death": "唐迟自己T-0的死法：从六楼天台坠落，摔在雨棚上弹起再砸地，四肢反向弯折但眼睛还睁着盯着楼梯口",
        "hidden_death_cause": "林薇三天前在地下室镜前替室友念了三遍名字，签下了替身契约；七天后她的影子会取代她爬出镜子，本体必须在第七夜摔死让位",
        "struggle_hook": "她在入戏第3天晚上查到了校医院地下室的秘密，被「它」推下了楼。唐迟就在她摔下去前10秒穿进来——只要手一松就死；但抓紧爬回去，那个「它」就在天台门口等她。",
        "info_gap_hint": "林薇口袋里有张被汗浸湿的便利贴写着「不要看镜子」，但她已经不记得自己是替谁念过名字",
        "san_loss": "已掉 35/99",
        "death_countdown": "10 秒",
        "tag": "倒计时型",
    },
    {
        "id": "han",
        "name": "韩医生",
        "role": "校医院校医/关键NPC",
        "age": 47,
        "body_state": "已被替换——真正的韩医生三天前就死在解剖室冰柜里，现在这具身体是「它」披着人皮在扮演他，他自己还不知道",
        "original_story_position": "副本中段偏前·韩医生被替换后第二日值班；原剧本中他将在第七夜被镜中本体「蜕皮」取代，彻底消失",
        "seventh_day_death": "唐迟T-0：白大褂下整张人皮从左肩缝线处开始蜕皮，露出下面鳞片与复眼的「它」，而真正的韩医生意识将被封在镜子里永世看自己被取代",
        "hidden_death_cause": "韩医生三年前为救病重的女儿与地下室的东西签了契约——用自己的影子换女儿命；影子在第七日长出自我，要彻底吃掉本体完成替换",
        "struggle_hook": "他/它已经习惯了用韩医生的脸给学生开安眠药、给调查的人打「镇静剂」。唐迟穿进来时，韩医生正在诊室写病历，左手无名指已经开始蜕皮露出下面的鳞片。",
        "info_gap_hint": "办公桌抽屉深处有一叠三年前的处方单，签名是韩医生自己但笔迹完全不同；女儿病历上的痊愈日期和契约日期是同一天",
        "san_loss": "原主 SAN 已归零（影子SAN=0，本体残留意识SAN=8/99）",
        "death_countdown": "蜕皮已开始·剩6天",
        "tag": "身份诡计型",
    },
    {
        "id": "laochen",
        "name": "老陈",
        "role": "学校门卫/边缘知情者/NPC",
        "age": 62,
        "body_state": "SAN 见底——七年里他看了三次不该看的东西，今晚是第七夜，他已经把尼龙绳挂上了值班室房梁",
        "original_story_position": "副本尾声·老陈第七次守夜即将自缢；原剧本中他吊死自己后，「它」才能借他的钥匙打开宿舍楼门带走所有学生",
        "seventh_day_death": "唐迟T-0：尼龙绳勒断颈椎的瞬间，他看见自己脚边打开的门里涌出十二个穿旧校服的孩子，而最后一个孩子长着七年前自己的脸",
        "hidden_death_cause": "七年前老陈喝醉开过后门，放进了十三个不该进来的东西；规则是「开门者必须在第七年第七夜自缢偿命，否则门永远关不上，全宿舍的人都会被带走」",
        "struggle_hook": "老陈知道每晚 11:47 宿舍楼后门会多出来第十三阶台阶；知道七年前失踪的四个学生其实没有走；他想吊死自己逃掉，因为「它」许诺过：自缢的人不用当容器。唐迟穿入时他正踩着椅子把脖子往绳圈里套。",
        "info_gap_hint": "七年门卫日志里夹着一张失踪学生合影，背面有老陈自己的笔迹写着「别开门」，但他七年前还是开了；日志最后一页的字迹开始变成小孩的",
        "san_loss": "剩 4/99",
        "death_countdown": "椅子已经在晃·T-0前几秒",
        "tag": "SAN 崩盘型",
    },
]


def _mock_entry_points_for(candidate: dict) -> list[dict]:
    name = candidate.get("name", "")
    if "林薇" in name:
        return [
            {
                "id": "lw_pre_fall",
                "name": "坠楼前 10 秒",
                "description": "唐迟穿入时林薇还抓着护栏，下面是六楼高度。他/她完全没读过林薇之前三天查到的线索，口袋里只有一张被汗浸湿的、写着「不要看镜子」的便利贴。",
                "original_timeline_anchor": "副本中段·第三夜林薇查到地下室秘密后被推下楼瞬间；这是原剧本中林薇命定死劫发生的那一刻",
                "info_gap": "对事件零认知，只知道自己马上要死；必须先活下来才能开始查。",
                "opening_pressure": "命悬一线",
                "first_lead": "口袋里被汗浸湿的便利贴「不要看镜子」",
            },
            {
                "id": "lw_morning_after",
                "name": "诈尸次日·病床醒来",
                "description": "林薇已经摔下去了，但唐迟醒在医院病床上——所有人都以为她只是骨折住院。只有唐迟知道：昨晚摔死的那个「林薇」，和现在躺在病床上的自己，不是同一个「回来」的东西。",
                "original_timeline_anchor": "副本中段·第三夜林薇死亡之后的清晨——原剧本中林薇应该已经死了，但镜子里的替身还没完全爬出",
                "info_gap": "知道自己死过一次，但不知道是谁/什么把自己塞回这具身体；身体上多出了自己不知道的旧伤痕。",
                "opening_pressure": "身份伪装",
                "first_lead": "手腕上一圈被谁握过的淤青",
            },
            {
                "id": "lw_final_night",
                "name": "终局切入·钟楼顶",
                "description": "唐迟直接穿入林薇在原剧本终局被献祭的瞬间——仪式已经开始，其他队友大部分已经死了或疯了，他/她手里只有林薇三天前就藏好的一把手术刀，而面前那个东西正用林薇妈妈的脸在笑。",
                "original_timeline_anchor": "原剧本高潮·第七夜钟楼献祭仪式开场·T-0前20分钟；唐迟必须在20分钟内反推三天剧情卡Bug破局",
                "info_gap": "一无所知地被丢进终局，必须在20分钟内从尸体/残页/疯话里反推七天发生了什么并破局。",
                "opening_pressure": "命悬一线",
                "first_lead": "手术刀柄上刻着的不是林薇的名字",
            },
        ]
    if "韩" in name:
        return [
            {
                "id": "han_clinic_day2",
                "name": "白班·第一个病人",
                "description": "穿入时韩医生正在诊室坐诊，第一个病人就是个说「我肚子里有东西在动」的女生。唐迟还没搞清楚自己已经不是人了，左手的蜕皮在白大褂袖子里发痒。",
                "original_timeline_anchor": "副本中段偏前·韩医生被替换后第二日下午坐诊；离他蜕皮T-0还有5天",
                "info_gap": "不知道自己是替换物，不知道医院地下室有什么；但周围同事似乎都「认识」他并对他有某种隐秘的期待。",
                "opening_pressure": "暗处观察",
                "first_lead": "第一个女生肚子上的鳞片抓痕",
            },
            {
                "id": "han_basement_door",
                "name": "夜班·地下室门口",
                "description": "唐迟穿着白大褂站在通往地下室的楼梯口，钥匙已经插进锁孔。身体原主（那个取代韩医生的东西）每晚都下来「喂食」，唐迟接盘了这具身体的全套肌肉记忆，却完全不知道门后面是什么。",
                "original_timeline_anchor": "副本中段·第五日夜班·原剧本中这是韩医生影子喂食的第三夜",
                "info_gap": "继承了韩医生的日常动作/钥匙/排班表，但没有任何关于「自己是什么」的记忆；门一旦打开就再也关不上。",
                "opening_pressure": "暗处观察",
                "first_lead": "钥匙串里多出来的一把黑色钥匙",
            },
            {
                "id": "han_exposed",
                "name": "午后·被学生指认",
                "description": "一个调查到一半的 PL 学生当着唐迟的面说：「你不是韩医生。」唐迟必须在三秒内决定：是当场灭口（完成身体原主未完成的事），还是赌一把承认自己也是被卷进来的、与对方合作。",
                "original_timeline_anchor": "副本后段·第六日下午·学生PL开始怀疑校医身份；T-1天",
                "info_gap": "知道自己不是韩医生但不知道该假装到什么程度；身份即将崩解，倒计时开始。",
                "opening_pressure": "身份伪装",
                "first_lead": "学生手里那本被撕掉第七页的病历本",
            },
        ]
    # 老陈
    return [
        {
            "id": "lc_on_stool",
            "name": "绳圈套脖·椅子在晃",
            "description": "唐迟穿入时老陈正踩在椅子上，脖子已经套进绳圈，只差把椅子踢开。他必须立刻自救，但自救动作必须慢——因为值班室窗外，那个「它」正在看着，等着他死。",
            "original_timeline_anchor": "原剧本尾声·七年之约第七夜老陈即将自缢；T-0就在几秒之后",
            "info_gap": "继承了老陈脑子里的7年值守记忆碎片，全是一闪而过的恐怖画面：多出来的台阶、不存在的404宿舍、井里的头发；但碎片顺序是乱的。",
            "opening_pressure": "命悬一线",
            "first_lead": "绳圈上除了自己脖子之外还有一圈更小的勒痕",
        },
        {
            "id": "lc_gate_midnight",
            "name": "午夜·开门放人",
            "description": "唐迟穿入老陈的时候，有人/有什么东西正在敲门卫室的窗户让他开门。老陈的手已经在拉门栓了，但唐迟隐约知道——老陈七年前开过一次门，放进来的就是它。这一次是同一「批」的，还是另一「批」？",
            "original_timeline_anchor": "副本中段偏后·第三夜0点·「它」来叫门；离老陈原剧本自缢还有几天",
            "info_gap": "知道开门的规矩但不知道门外是谁；知道11:47的台阶禁忌但不知道为什么不能数到十三。",
            "opening_pressure": "暗处观察",
            "first_lead": "窗户玻璃上贴着的是一张小孩的手掌印",
        },
        {
            "id": "lc_final_shift",
            "name": "换班·接班人没来",
            "description": "第七天老陈本应在凌晨交班，但接班人一直没来。唐迟必须死守门卫室到天亮，或者烧掉七年的门卫日志让「它」失去凭据——但烧掉日志意味着老陈七年前的罪证也没了。",
            "original_timeline_anchor": "原剧本尾声·第七夜凌晨老陈换班节点·T-0天亮前",
            "info_gap": "知道全部规则碎片，但不知道哪一条是真正能活命的；知道自己是容器候选之一，但不知道容器装的是什么。",
            "opening_pressure": "步步紧逼",
            "first_lead": "门卫日志最后一页是小孩的字写着「陈叔我们来接你了」",
        },
    ]


def _mock_stances_for(candidate: dict, entry_point: dict) -> list[dict]:
    return [
        {
            "id": "save_all",
            "name": "能救一个是一个·救场者",
            "description": "唐迟不接受宿命——他要用自己七天内拿到的信息，尽可能把队友/NPC 从死局里拽出来。代价：每救一个人，SAN 多掉一份；救到最后可能一个都救不下。",
            "cost": "SAN 代价重，同伴伤亡风险转嫁给自己",
            "tendency": "利他/燃向/悲壮",
        },
        {
            "id": "lone_wolf",
            "name": "自己先活下来·独狼",
            "description": "唐迟清楚这具身体的原主是个将死之人——他/她首要目标是保证自己七天后还能喘气。必要时可以牺牲 NPC、可以躲、可以装作没看见。但克苏鲁副本里独善其身的人，最后往往发现自己活下来的「自己」已经不是自己。",
            "cost": "内心崩塌，可能触发身份污染结局",
            "tendency": "利己/冷硬/黑暗",
        },
        {
            "id": "truth_seeker",
            "name": "死也要知道真相·追真相者",
            "description": "唐迟决定把整件事挖到底——它是什么？七年前发生了什么？自己为什么穿进这具身体？追真相会比逃跑 SAN 掉得更快，但只有看清真相才能在第七日找到真正的破局点。",
            "cost": "SAN 崩盘最快，但可能发现真正破局法",
            "tendency": "理性/癫狂/揭秘",
        },
    ]


def _mock_outline(idx: int, flavor: str, candidate: dict, entry_point: dict, stance: dict) -> dict:
    name = candidate.get("name", "?")
    cname = candidate.get("name", "")
    ep_name = entry_point.get("name", "")
    ep_anchor = entry_point.get("original_timeline_anchor") or "原剧本中段节点"
    st_name = stance.get("name", "")
    titles = [
        f"{cname}的第十三次坠落",
        f"披着{cname}皮的人",
        "第七夜值班日志",
        "她摔下去之后",
        "门卫室的绳圈",
    ]
    oneliners = [
        f"唐迟穿进即将坠楼的{cname}，从{ep_name}开始，以「{st_name}」的立场在七天求生期内一边活命一边追凶，最后发现——那个推她下楼的，就是七天后的自己。",
        f"穿成{cname}的唐迟在{ep_name}节点接盘了一具正在被替换的身体，他必须以「{st_name}」的姿态撑过自己T-0，才能在照镜子时不看见另一个东西。",
        f"老陈门卫室的绳圈已经在晃，唐迟从{ep_name}开始七天守夜，以「{st_name}」的立场面对多出来的第十三阶台阶，最后发现台阶通向的是自己的记忆。",
        f"唐迟在{cname}已经摔死的次日清晨醒来，医院病床上的这具身体开始蜕皮，他以「{st_name}」的立场重返学校，追查到T-0钟楼献祭。",
        f"以「{st_name}」为信条，唐迟在{cname}的身体里从{ep_name}硬扛到T-0，最后活下来的只有一个——但活下来的那个人，瞳孔是竖的。",
    ]
    pacings = [
        "步步紧逼·每夜都死人",
        "伪装求生·身边都是怪物",
        "记忆污染·分不清自己是谁",
        "逃不掉的轮回·死亡只是开始",
        "最后一天反转·幸存者即祭品",
    ]
    # 根据入戏第N天，写双轨梗概 + 原剧本锚点
    module_anchor_phases = {
        1: f"原剧本此刻：{ep_anchor}，唐迟刚接盘，原剧本事件开始从他视角偏移",
        2: "原剧本此刻：第一夜/第一日过后，身边人开始按原剧本轨迹陆续出事",
        3: "原剧本此刻：原剧本核心线索节点临近（地下室/绳圈/钟楼前夜）",
        4: "原剧本此刻：按原剧本轨迹应该死的人真的死了，甚至死在唐迟面前",
        5: "原剧本此刻：真相浮现，原主隐藏的罪孽/契约即将被撞破",
        6: "原剧本此刻：可能已经过了原剧本所谓的「结局」，也可能原剧本结局正在发生",
        7: "原剧本此刻：T-0死劫降临——不管原剧本有没有到结局，唐迟的七天已经到",
    }
    days = []
    for d in range(1, 8):
        fate_map = {1:"diverted",2:"diverted",3:"diverted",4:"escalated",5:"doomed",6:"diverted",7:"escalated"}
        false_h_map = {
            1: f"最初假死因：「是{('镜子里的替身' if '林薇' in cname or '韩' in cname else '七年前的孩子们')}在杀人，只要毁掉镜子/烧掉日志就能活」",
            2: f"假死因进一步成型：「只要躲过今晚/不去地下室/不开门就没事」",
            3: "唐迟按假假说布置了自救方案，以为自己即将成功",
            4: "",5:"",6:"",7:"",
        }
        rcp_map = {
            1:"误导——唐迟被表象带偏",
            2:"误导——继续往红鲱鱼方向查",
            3:"误导高点——假假说看起来即将验证",
            4:"假说崩塌——按假方案行事的人惨死，唐迟开始意识到不对",
            5:"真死因揭穿——挖出原主黑历史，知道自己为什么T-0必死",
            6:"开始搭卡Bug截断死因的逻辑漏洞",
            7:"死因截断——T-0死劫降临，卡Bug付出代价活下",
        }
        days.append({
            "day": d,
            "summary": (
                f"入戏第{d}天（T-{7-d}）：以{cname}的身份活动，{('继续调查身边异常' if d < 4 else '线索开始收束' if d < 6 else '真相层层揭开')}。"
                f"当天发生{('第一起诡异事件' if d == 1 else '唐迟以为自救即将见效' if d == 3 else '按假假说行事的人惨死' if d == 4 else '第一次正面遭遇真相反转' if d == 5 else 'T-0死劫降临' if d == 7 else '卡Bug准备')}，"
                f"唐迟 SAN 掉 {d*4}，身体原主的残留记忆闪现「{('不要照镜子' if d%2 else '别数台阶')}」。"
            ),
            "original_timeline_anchor": module_anchor_phases.get(d, ""),
            "fate_layer_focus": fate_map[d],
            "false_hypothesis": false_h_map[d],
            "real_cause_progress": rcp_map[d],
        })
    climax = (
        f"T-0第七夜，唐迟以{st_name}的姿态站在{('天台/校医院地下室/门卫室/钟楼')[(idx)%4]}——"
        f"七天里死掉的同伴、替换过的NPC、消失的记忆在这一刻同时汇聚。"
        f"他终于看清：{cname}的死不是意外，是整局仪式/契约的钥匙；而自己在{ep_name}穿入的那一刻，就已经成为了它的下一个容器候选。"
        f"唐迟利用怪谈判定规则的边界漏洞（替死指针移交/契约重定向）把死劫转移回规则源点，"
        f"破局的代价是——（留白，由作者定）。"
    )
    blue_star = f"唐迟从副本里带出了一张七年前失踪名单的残角，背面写着自己的字迹：「别再当门卫。」"
    foreshadows = [
        f"{cname}身上出现的来历不明的伤痕——长线指向卷二/卷三替换物的传染机制",
        "十三阶台阶/镜子反射的隐喻——长线指向「蓝星/副本」的空间折叠",
        f"唐迟穿入瞬间看到的「它」的脸——长线指向 BOSS 真身",
        f"{st_name}立场留下的心理创伤——长线影响后续副本的 SAN 上限",
    ]
    return {
        "index": idx,
        "title": titles[idx % len(titles)],
        "one_liner": oneliners[idx % len(oneliners)],
        "pacing": flavor or pacings[idx % len(pacings)],
        "original_module_timeline_note": f"原剧本跨度约一周·入戏点：{ep_anchor}",
        "tangchi_entry": f"唐迟在{ep_name}（{ep_anchor}）魂穿{cname}，以「{st_name}」为行动立场；身体原主正处于{candidate.get('body_state','危机状态')}，开局即死局。T-0讣告死法：{candidate.get('seventh_day_death','（待展开）')}",
        "day_breakdown": days,
        "false_cause_red_herring": "假死因：「只要毁掉镜子/烧掉日志/躲过今晚就能活」——看似合理但治标不治本的自救假说",
        "core_twist": candidate.get("hidden_death_cause","原主三年前/七年前与怪谈签下契约，用自己的影子/性命换了某样东西，影子/债在T-0来收账；死因长在原主自己身上，不是被坏人杀"),
        "rule_exploit": "程序员式卡Bug：利用怪谈规则里「契约签订者必须亲自死/替身必须经原主同意/开门者必须是清醒者」等判定边界，通过把契约转嫁给规则本身/让影子自己签契约/让门识别出唐迟不是原主而失效，合规越截断死因",
        "climax": climax,
        "cost_cap": "T-0代价：SAN掉落20+、身体局部异化（如左手永久保留鳞片/眼睛里有小孩的倒影/多出一段不属于自己的记忆）、或欠下新的人际债务，不可减免",
        "blue_star_hook": blue_star,
        "foreshadow_planted": foreshadows,
        "flavor": flavor,
    }
