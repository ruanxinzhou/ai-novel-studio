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
    router.add_api_route(f"{prefix}/candidates", _list_candidates, methods=["POST"])  # 支持 reroll 传 user_directive
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


def _is_mock(adapted_module, candidate: dict | None) -> bool:
    from ..book.volume import adapted_premise_of
    premise = adapted_premise_of(adapted_module) if adapted_module is not None else ""
    if isinstance(premise, str) and premise.startswith("测试副本核文本"):
        return True
    if candidate and isinstance(candidate.get("name"), str) and "林薇" in candidate["name"]:
        return True
    return False


# ========== v2.0 三折死因链真 LLM Prompt ==========

_CANDIDATES_SYSTEM = """你是《第七日的亡者》的魂穿宿主策划师。
在这部小说中，主角唐迟进入每个克苏鲁副本时，可以魂穿成剧本里的【任意一个角色】——绝不局限于调查员玩家位！可以是宿管、保安、校医、死者的室友、被诅咒的边缘 NPC、嫌疑人、甚至邪教准备的祭品容器。

【双轨时间线·核心铁律——务必在脑中分清】
1. 副本（COC模组改编 + 跑团推演）是一个**独立完整的故事**，它有自己的时间线——可能是几小时（午夜惊魂）、几天、几个月、几年、几代人不等。副本里的事件按自己的逻辑推进，**副本本身不要求有七日结构，死者也不一定死在副本结尾**。
2. 唐迟个人的「七天倒计时」是他魂穿那一刻才启动的求生锚：他睁眼 = T-7（离他自己的死劫还有整整七天），讣告预告此人在**他自己的第七天（T-0）**会死于某种凄惨死法。注意：穿入时点**距离这个角色命定死劫正好还有七天**，不一定是"死亡瞬间"——穿入时阴谋可能还在酝酿期、诅咒可能刚露头、仪式还没开始、替死鬼还没被盯上，也可能死劫的锁链已经悄悄套上脖子；具体处于什么阶段要看角色命格。
3. 两个时间线并行：副本事件按原剧本走，唐迟在里面活一天算一天，到 T-0 那天死劫必然降临。穿入点（T-7）是宿主死劫的七天前，因此它自然对应原剧本里死劫发生节点往前推七天的位置——可能落在副本开头、中段、或接近宿主死劫的前夜，但绝不会落在宿主死亡/崩溃当下或终局前几分钟；跟副本自身跨度没有强制关系。

唐迟的核心主线：开局通过讣告得知这具身体「自己第七日必死」的凄惨死状（果），但他失去了原主记忆，必须在七天内查清「为什么这个角色在原剧本里七天后会死」（因），并在 T-0 截断死因求生。

请基于给定的【副本改编稿】和【跑团推演记录】，挑选出 5 个最具悬疑张力与求生戏份的魂穿宿主候选（必须包含调查员 PL 与倒霉 NPC/祭品/边缘知情者的混合，不要全是调查员）。
每个候选必须满足：唐迟穿入这一刻，距离这个角色在原剧本里的命定死劫**正好还有七天**（T-6→T-0）——因此穿入时点可能落在阴谋酝酿期、诅咒初现阶段、秘密被撞破前、仪式前一周、甚至看似风平浪静的日常里，而**不是**角色死亡或崩溃的瞬间。
每个候选必须有清晰的「第七日死状（果）」和「作者预览的真实死因（因）」，二者要有距离，给 Day4-5（唐迟入戏第4-5天）反转留空间。
每个候选还必须说清：唐迟穿入时，原剧本的故事正在什么节点、七天后这个角色命定死劫发生在原剧本的哪一段剧情。

严格输出 JSON（不要输出 JSON 之外任何文字，不要 Markdown 包裹）：
{
  "candidates": [
    {
      "id": "c1",
      "name": "角色姓名",
      "role": "剧本中的具体身份（必须以「调查员 / 关键NPC / 边缘知情者 / 祭品容器 / 嫌疑人」开头标注类型，再接角色职业/关系）",
      "age": 年龄数字或null,
      "body_state": "当前身体的异常状态（距离死劫还有七天，此刻可能看起来一切正常，也可能已出现早期征兆：莫名发冷/做重复噩梦/身上长出朱红线/收到无邮票讣告/被什么东西盯上但尚未发动 等）",
      "original_story_position": "唐迟穿入这一刻，原剧本自身的故事正处于哪个阶段/事件？（如「副本开头·小镇接连收到无邮戳讣告当夜」「副本中段·梁泊安已经哄骗原主滴血求财但血绒胎妖还未孵化」「副本中段·对门美甲师姜采薇第一次上门送纸花」「看似风平浪静的日常——原主以为自己只是帮朋友一个小忙」等；30-80字。不要写「死亡瞬间」「被推下楼瞬间」之类终局场景）",
      "seventh_day_death": "讣告预告该角色在【唐迟自己的第七日（T-0）】的具体死状（30-60字，阴冷具象，禁止空泛形容词；这是唐迟视角倒计时的终点，不是副本的第七天）",
      "hidden_death_cause": "【作者预览】为什么这具身体在原剧本中注定会死/遭遇毁灭性危机？（原主在穿入时点前隐瞒的罪孽/契约/替身真相，40-80字，死因必须长在原主自己身上，不能是「坏人杀了无辜受害者」）",
      "struggle_hook": "唐迟穿入后如何利用该身份在调查员与怪谈之间周旋求生（40-80字）",
      "info_gap_hint": "唐迟开局完全不知道、必须先从自己身上或房间里查起的核心盲区（30-60字）",
      "san_loss": "原主当前 SAN 剩余估计（如「已掉35/99」「SAN已归零」「剩4/99」）",
      "death_countdown": "穿入时点离【唐迟自己T-0死劫】的紧迫度——固定为「还剩七天」，但要描述此刻剧情上的张力（如「死劫预兆刚显·还剩七天布局」「诅咒初缠·还剩七天」「契约刚签·还剩七天」「看似日常·阴谋在暗处发酵七天」「被盯上但自己还没察觉·还剩七天」）",
      "tag": "一句话标签（如「倒计时型」「身份诡计型」「SAN崩盘型」「替死容器型」「借刀调查员型」）"
    }
  ]
}"""


_ENTRY_POINTS_SYSTEM = """你是《第七日的亡者》的切入点设计师。
唐迟已魂穿进选定角色体内，睁眼这一刻距离TA在原剧本里的命定死劫正好还有七天（T-7）。请基于副本背景与角色身份，设计 4 个不同的「睁眼第一秒切入点」方案。

【双轨时间线提醒】
- 每个切入点要明确：原剧本的故事此刻正走到哪里（副本时间锚点），以及七天后这个角色命定死劫发生在原剧本的哪一段剧情。
- 切入点可以落在原剧本的任何阶段：副本开头、阴谋酝酿期、诅咒初现阶段、秘密撞破前一天、仪式前一周、看似风平浪静的日常——但**绝对不要**放在「角色死亡/崩溃的瞬间」或终局前几分钟（那不是穿入点，是结局）。不管落点在哪里，唐迟自己的 T-7→T-0 七天倒计时**从他睁眼这一刻正式启动**。
- 副本可能本身只有几小时剧情，也可能跨越几个月；唐迟的七天是他的求生锚，不要求副本原剧本正好七天。如果穿入点落在副本早期，那么唐迟可以用七天时间慢慢渗透调查；如果穿入点落在副本事件密集期，他必须边跟副本事件周旋边倒计时求生。

每个切入点要明确唐迟当时的身体姿态、眼前正发生什么、他第一秒要做什么、副本此刻正处于哪个事件、以及他【不知道但必须尽快查】的关键信息差是什么。
切入点的差异要体现在：信息差深度、伪装难度、开场紧迫度、他在原剧本时间线中的位置（阴谋酝酿期/诅咒初缠/秘密即将暴露/仪式倒计时启动）、以及他是否已经被原剧本调查员盯上。

严格输出 JSON：
{
  "entry_points": [
    {
      "id": "e1",
      "name": "切入点短标题（8字内，如「讣告抵门」「镜里多一个人」「纸花初至」「夜班来电」）",
      "description": "唐迟睁眼瞬间的身体状态、眼前场景、第一秒的危机动作（60-100字，要有画面感；此刻距死劫还有七天，场景可以是看似日常但暗藏诡异的瞬间，不要写成终局/死亡/崩溃时刻）",
      "original_timeline_anchor": "此刻原剧本故事正在哪个事件/阶段？七天后这个角色命定死劫在原剧本时间上落在哪个节点？（如「副本开头·第一封无邮戳讣告送到鸣衡书印馆当夜；七天后原主会在十一层印刷机前被截运牒索命」「副本中段·梁泊安刚哄原主在仿造胎珠上滴了血，血绒胎妖尚未孵化；七天后原主会在废厂地下室被饲主当成替身献祭」；40-80字）",
      "info_gap": "他此刻完全不知道、必须在几小时内搞清楚的核心盲区（30-60字，要具体）",
      "opening_pressure": "开场紧迫度标签（「阴谋酝酿·看似日常」「诅咒初缠·初兆已显」「秘密将破·危机四伏」「仪式前夜·倒计时启动」等）",
      "first_lead": "他睁眼后第一个可摸到/看到/听到的线索（用于第一章开场钩子，30字内）"
    }
  ]
}
不要输出 JSON 之外的任何文字。"""


_STANCES_SYSTEM = """你是《第七日的亡者》的求生姿态设计师。
基于唐迟魂穿的角色身份与切入点，设计 4 种不同的求生姿态/破局策略。姿态的差异体现在：对原剧本调查员的态度（借刀/躲/主动误导）、对自身死因追查的激进程度（藏着查/公开查/反向利用怪谈查）、以及愿意付出的代价量级。
每种姿态都要体现「程序员式逆向思维」的底色（把怪谈规则当代码、把死因当bug来拆解）。

【双轨时间线提醒】
- 姿态设计要兼顾「唐迟自己 T-7→T-0 七天倒计时的紧迫度」和「原剧本事件的自然推进速度」。如果穿入点落在副本开头/阴谋酝酿期，姿态可以有长线伪装、渗透、布局的空间；如果穿入点落在副本事件密集期，姿态必须兼顾副本事件应对与七天内死因追查。
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
3. 穿入点（T-7）距离该角色命定死劫正好七天，这七天 = 唐迟自己的 T-6→T-0 倒计时，而不是原剧本的七天。如果穿入点落在副本早期（如阴谋酝酿期/诅咒初现阶段），原剧本事件会在唐迟七天内按自己节奏推进，唐迟一边参与一边逆向破局；如果穿入点落在副本事件密集段，他必须同时处理副本事件和死因追查。
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
    from ..book.volume import adapted_premise_of, adapted_cast_of
    parts: list[str] = []
    premise_text = adapted_premise_of(v.adapted_module)
    cast_cards = adapted_cast_of(v.adapted_module)
    parts.append("===== 副本骨架（改编稿·副本核文本）=====")
    parts.append((premise_text or "")[:4000])
    if cast_cards:
        parts.append("\n===== 剧本 NPC/KPC 卡司（魂穿候选的人物池之一部分）=====")
        for c in cast_cards:
            parts.append(
                f"- {c.get('name','?')}（{c.get('role','')}；{c.get('occupation','')}）"
                f"｜动机：{c.get('motivation','')}｜秘密：{c.get('secret','')}"
                f"｜当前状态：{c.get('current_state','')}"
            )
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

def _list_candidates(pid: str, vol_no: int, body: dict | None = None):
    vs, v = _get_volume_or_404(pid, vol_no)
    adapted = v.adapted_module or ""
    user_directive = ""
    if isinstance(body, dict):
        user_directive = (body.get("user_directive") or "").strip()
    if _is_mock(adapted, None):
        time.sleep(0.8)
        return {"candidates": _MOCK_CANDIDATES}

    ctx = _build_embody_context(v, user_directive=user_directive)
    user_hint = (
        "请基于以上副本骨架与跑团记录，挑出 5 个最具悬疑张力的魂穿宿主候选，"
        "必须混合调查员 PL、关键 NPC、边缘知情者、祭品容器、嫌疑人几类；"
        "每个候选必须有清晰的第七日死状（果）和作者预览的真实死因（因）。"
    )
    if user_directive:
        user_hint += f"\n\n===== 作者额外要求（打捞候选时必须优先遵守）=====\n{user_directive}"
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
    if isinstance(v.stage_completed, dict) and "_view" in v.stage_completed:
        v.stage_completed.pop("_view", None)
    vs.save(v)
    return {"ok": True, "volume": v.as_dict(), "picked": picked}


def _back_to_simulate(pid: str, vol_no: int) -> dict:
    """从魂穿阶段返回跑团阶段：
    - 不清空跑团结果（simulated_run 保留，方便重选盘/查看梗概）
    - 保留 stage_completed.simulate=True（跑团已完成）
    - 写入 stage_completed._view = "simulate"，让 /phase 路由返回 simulate 阶段
    - 清空魂穿阶段的产出，避免回到魂穿时看到旧选择
    """
    vs, v = _get_volume_or_404(pid, vol_no)
    sc = dict(v.stage_completed or {})
    sc["_view"] = "simulate"
    v.stage_completed = sc
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
        "body_state": "看似一切正常——刚替室友在镜前念完三遍名字回到宿舍，夜里开始做反复的坠楼噩梦，右肩多了一道自己不记得的擦伤",
        "original_story_position": "副本开头·林薇刚替室友在地下室镜前念完名字回到宿舍当夜；七天后她会在查到地下室秘密时被「它」推下六楼天台摔死",
        "seventh_day_death": "唐迟自己T-0的死法：从六楼天台坠落，摔在雨棚上弹起再砸地，四肢反向弯折但眼睛还睁着盯着楼梯口",
        "hidden_death_cause": "林薇三天前在地下室镜前替室友念了三遍名字，签下了替身契约；七天后她的影子会取代她爬出镜子，本体必须在第七夜摔死让位——而室友其实早就是替身了",
        "struggle_hook": "唐迟睁眼时林薇正躺在宿舍上铺，手机屏幕停在室友刚发来的「谢谢啦，明天请你喝奶茶」。表面是普通大学夜晚，可卫生间镜子里自己的倒影慢了半拍。",
        "info_gap_hint": "林薇完全不记得自己替谁念过名字，也不知道右肩那道擦伤是怎么来的；便利贴「不要看镜子」夹在镜子边的专业书里，她以为是室友留的",
        "san_loss": "原满值 99，当前轻微侵蚀（已掉 5/99）",
        "death_countdown": "诅咒初缠·还剩七天",
        "tag": "替身契约型",
    },
    {
        "id": "han",
        "name": "韩医生",
        "role": "校医院校医/关键NPC",
        "age": 47,
        "body_state": "契约刚签满三年整——早上醒来左手无名指有一处细小蜕皮，他自己以为只是换季皮肤干燥；女儿三年前的「痊愈出院日」正好就是今天",
        "original_story_position": "副本中段偏前·韩医生签契约满三年当日清晨坐诊；七天后他的影子将在白大褂下完成蜕皮长出鳞片复眼的「它」，本体意识被封入镜子永世看自己被取代",
        "seventh_day_death": "唐迟T-0：白大褂下整张人皮从左肩缝线处开始蜕皮，露出下面鳞片与复眼的「它」，而真正的韩医生意识将被封在镜子里永世看自己被取代",
        "hidden_death_cause": "韩医生三年前为救病重的女儿与地下室的东西签了契约——用自己的影子换女儿命；影子在第七日长出自我，要彻底吃掉本体完成替换",
        "struggle_hook": "唐迟穿进来时，韩医生正在诊室写当日第一个病人的病历，左手无名指那片细小蜕皮在白大褂袖子里发痒；第一个挂诊号的女生进门就说「医生，我肚子里有东西在动」。",
        "info_gap_hint": "办公桌抽屉深处有一叠三年前的处方单，签名是韩医生自己但笔迹完全不同；女儿病历上的痊愈日期和契约日期是同一天",
        "san_loss": "原主 SAN 剩余 62/99（蜕皮刚开始，本体意识尚清醒）",
        "death_countdown": "契约到期·还剩七天",
        "tag": "身份诡计型",
    },
    {
        "id": "laochen",
        "name": "老陈",
        "role": "学校门卫/边缘知情者/NPC",
        "age": 62,
        "body_state": "七年值守还差最后七天——今晚是第七年的第一夜，他值班室桌上多了一封没邮票的讣告写着自己的名字，他以为是谁恶作剧",
        "original_story_position": "副本开头·七年之约第七年第一夜老陈值门卫班；七天后他必须在值班室自缢偿命，否则宿舍楼里十三个东西会涌出带走所有学生",
        "seventh_day_death": "唐迟T-0：尼龙绳勒断颈椎的瞬间，他看见自己脚边打开的门里涌出十二个穿旧校服的孩子，而最后一个孩子长着七年前自己的脸",
        "hidden_death_cause": "七年前老陈喝醉开过后门，放进了十三个不该进来的东西；规则是「开门者必须在第七年第七夜自缢偿命，否则门永远关不上，全宿舍的人都会被带走」",
        "struggle_hook": "唐迟穿入时老陈正坐在门卫室暖炉边喝热水，桌上那封讣告墨迹还没干；窗外操场方向传来小孩跑过的声音，但现在是凌晨一点，宿舍早锁门了。",
        "info_gap_hint": "七年门卫日志里夹着一张失踪学生合影，背面有老陈自己的笔迹写着「别开门」，但他七年前还是开了；日志最后一页的字迹开始变成小孩的",
        "san_loss": "剩 48/99（七年里三次撞见，掉了大半但还撑着）",
        "death_countdown": "七年之约·还剩七天",
        "tag": "SAN 崩盘型",
    },
]


def _mock_entry_points_for(candidate: dict) -> list[dict]:
    name = candidate.get("name", "")
    if "林薇" in name:
        return [
            {
                "id": "lw_nightmare_wake",
                "name": "噩梦惊醒·上铺",
                "description": "唐迟在林薇宿舍上铺惊醒，浑身冷汗——梦里反复是自己从六楼摔下去的画面。下铺室友背对着他/她坐着梳头，梳子划过头发的声音慢得不对劲；手机显示凌晨 3:17，屏幕上是室友两分钟前发的「睡了吗」，但下铺那个人分明还没睡。",
                "original_timeline_anchor": "副本开头·林薇替室友念完名字的第二夜；七天后她会在天台上被自己的替身推下六楼——此刻替身还在镜子里慢慢长出来",
                "info_gap": "完全不知道自己替人念过名字；右肩那道擦伤怎么来的、镜子边便利贴谁写的、梦里反复出现的坠落感意味着什么——一概不知。",
                "opening_pressure": "诅咒初缠·初兆已显",
                "first_lead": "卫生间镜子里的倒影比自己慢半拍眨眼",
            },
            {
                "id": "lw_library_note",
                "name": "图书馆·夹页纸条",
                "description": "唐迟穿入时林薇正在图书馆专业书区翻校史，书页里夹着一张没有署名的纸条：「今晚别回宿舍，你替的那个人已经回不去了」。桌对面坐着一个不认识的女生在盯着他/她笑，而那个女生手里的校刊封面上印着七年前失踪名单，第三名就是她自己。",
                "original_timeline_anchor": "副本中段偏前·念名后第三日林薇开始查校史；七天后摔死，此刻她还没把替身契约和校医院地下室联系起来",
                "info_gap": "不知道「替的那个人」指的是室友还是自己；不知道校医院地下室镜子的存在；纸条是谁留的、为什么帮他/她。",
                "opening_pressure": "阴谋酝酿·看似日常",
                "first_lead": "纸条背面用淡铅笔画着一面镜子，镜里人影多了一个",
            },
            {
                "id": "lw_clinic_visit",
                "name": "校医院·第一次就诊",
                "description": "唐迟跟着室友第一次到校医院看「反复噩梦」，韩医生坐诊时看林薇的眼神不对——他认识林薇的「症状」，因为三年前他自己也签过同一份契约。韩医生低头写处方时，左手无名指那片细小蜕皮落在了处方单上，他迅速用病历盖住。",
                "original_timeline_anchor": "副本中段·念名后第四日林薇到校医院就诊；七天摔死大限还有三天，但真正的替身即将在今夜第一次尝试爬出镜子",
                "info_gap": "不知道韩医生也是契约者；不知道自己噩梦不是压力而是替身即将成型；处方单上开的安眠药其实是镇魂剂。",
                "opening_pressure": "秘密将破·危机四伏",
                "first_lead": "韩医生处方单角那片带血的细小蜕皮",
            },
        ]
    if "韩" in name:
        return [
            {
                "id": "han_first_patient",
                "name": "早班·第一个病人",
                "description": "穿入时韩医生正在诊室准备坐诊，第一个挂诊号的女生进门就说「医生，我肚子里有东西在动」。唐迟还没搞清楚自己为什么三年前会为女儿签那个东西，左手无名指那片蜕皮在白大褂袖子里发痒。",
                "original_timeline_anchor": "副本中段偏前·契约满三年当日清晨；七天后影子将完成蜕皮取代本体——此刻影子还在皮肤下慢慢长出鳞片",
                "info_gap": "不知道自己已经和影子共存了三年；不知道医院地下室锁着什么；不知道女儿的「痊愈」是用什么换来的。",
                "opening_pressure": "诅咒初缠·初兆已显",
                "first_lead": "第一个女生肚子上的鳞片抓痕",
            },
            {
                "id": "han_basement_key",
                "name": "夜班·钥匙串里多一把",
                "description": "唐迟穿着白大褂在夜班整理病历，抬头发现钥匙串里多了一把从没见过的黑色钥匙，钥匙柄刻着一个极小的「替」字。身体原主每晚都用这把钥匙下地下室「喂食」，唐迟接盘了全套肌肉记忆，却完全不知道门后面是什么。",
                "original_timeline_anchor": "副本中段·第五日夜班；七天蜕皮大限还有两天，地下室里零号胎妖已经感知到本体换了「灵魂」",
                "info_gap": "继承了韩医生的日常动作/钥匙/排班表，但没有任何关于「自己三年前签过什么」的记忆；门一旦打开就再也关不上。",
                "opening_pressure": "秘密将破·危机四伏",
                "first_lead": "钥匙串里那把多出来的黑色钥匙",
            },
            {
                "id": "han_daughter_visit",
                "name": "探视日·女儿来送便当",
                "description": "唐迟穿入时正好遇到韩医生女儿放学送便当到诊室——女儿看起来完全健康，但便当盒里有一小碗唐迟不认识的暗红色酱汁，女儿盯着他/她无名指那片蜕皮说「爸爸，今天它动了」。",
                "original_timeline_anchor": "副本中段·契约满三年第三日；七天蜕皮大限还有四天，但女儿其实三年前就被零号胎妖标记过，是契约的「质押物」",
                "info_gap": "不知道女儿能看见影子；不知道女儿为什么每晚会梦游到地下室门口；便当盒里的暗红色酱汁是什么。",
                "opening_pressure": "阴谋酝酿·看似日常",
                "first_lead": "女儿便当盒里那碗暗红色酱汁里浮着一片细小鳞片",
            },
        ]
    # 老陈
    return [
        {
            "id": "lc_first_notice",
            "name": "头一夜·桌上讣告",
            "description": "唐迟穿入时老陈正坐在门卫室暖炉边喝热水，桌上多了一封没邮票的讣告，墨迹未干，收信人是自己。窗外操场方向传来小孩跑过的声音，但现在是凌晨一点，宿舍早锁门了。",
            "original_timeline_anchor": "副本开头·七年之约第七年第一夜老陈值门卫班；七天后他必须在值班室自缢偿命，否则宿舍楼门会被打开",
            "info_gap": "讣告谁放的、七年前自己开过后门到底放进来了什么、七年值守日志最后几页为什么开始变成小孩的字。",
            "opening_pressure": "诅咒初缠·初兆已显",
            "first_lead": "讣告折痕里夹着一小撮小孩的湿头发",
        },
        {
            "id": "lc_steps_at_midnight",
            "name": "11:47·第十三阶台阶",
            "description": "唐迟穿入老陈的时候，值班室挂钟正好指向 11:47，宿舍后门方向传来有人上楼的脚步声——一步、两步……按规矩他不能数到十三，但身体原主数了七年，今晚唐迟必须决定要不要接下去数。",
            "original_timeline_anchor": "副本中段偏后·第三夜0点·「它」来叫门测试老陈；七天自缢大限还有四天，若他不应门，宿舍楼的人会先死一批",
            "info_gap": "知道开门的规矩但不知道门外是谁；知道11:47的台阶禁忌但不知道为什么不能数到十三；自己七年前那夜到底开没开门。",
            "opening_pressure": "秘密将破·危机四伏",
            "first_lead": "窗户玻璃上贴着的是一张小孩的手掌印",
        },
        {
            "id": "lc_logbook_last_page",
            "name": "翻日志·小孩字",
            "description": "第七天前倒数第二夜，唐迟翻到七年门卫日志最后一页——上面是小孩歪歪扭扭的笔迹写着「陈叔我们来接你了」，落款是十三个名字，其中一个是七年前失踪的学生、一个是老陈自己。",
            "original_timeline_anchor": "副本后段·第六夜凌晨；七天自缢大限还剩最后一天，老陈必须在天亮前决定是自缢偿命、还是烧掉日志让规则失效——但烧掉日志意味着七年前罪证也没了。",
            "info_gap": "知道全部规则碎片，但不知道哪一条是真正能活命的；知道自己是容器候选之一，但不知道容器装的是什么。",
            "opening_pressure": "仪式前夜·倒计时启动",
            "first_lead": "日志里夹着的失踪学生合影背面多了一个新名字——今天值白班的宿管阿姨",
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
        "她第七天后",
        "门卫室的第十三阶",
    ]
    oneliners = [
        f"唐迟在{ep_name}穿入{cname}的身体——此刻距离她命定的坠楼之死正好还有七天。以「{st_name}」的立场在七天倒计时里一边活命一边追凶，最后发现——那个七天后会推她下楼的，就是被死劫锁定的自己。",
        f"穿成{cname}的唐迟在{ep_name}节点接盘了一具契约即将到期的身体（左手无名指已经开始蜕皮），他必须以「{st_name}」的姿态撑过七天T-0，才能在照镜子时不看见另一个东西。",
        f"老陈在第七年值守的第一夜桌上多了封无邮票讣告，唐迟从{ep_name}开始七天守夜，以「{st_name}」的立场面对多出来的第十三阶台阶，最后发现台阶通向的是自己被封印的记忆。",
        f"唐迟在{cname}反复做坠楼噩梦的那一夜醒来，枕边有张自己没写过的夹页纸条。他以「{st_name}」的立场重返学校，用七天追查到T-0钟楼献祭的真相。",
        f"以「{st_name}」为信条，唐迟在{cname}的身体里从{ep_name}（看似日常却已落入死劫轨道）一路走到T-0，最后活下来的只有一个——但活下来的那个人，瞳孔是竖的。",
    ]
    pacings = [
        "阴谋酝酿·第一夜初兆已显",
        "诅咒升温·身边人开始不对劲",
        "伪装求生·身边都是疑似之物",
        "记忆污染·分不清自己是谁",
        "逃不掉的倒计时·第七日反转",
    ]
    # 根据入戏第N天，写双轨梗概 + 原剧本锚点
    # 注意：穿入点始终是 T-7（死劫前七天），原剧本事件按自己节奏往前推
    module_anchor_phases = {
        1: f"原剧本此刻：{ep_anchor}，唐迟刚接盘，身边异象尚处在「苗头」阶段，原剧本事件开始从他视角发生细微偏移",
        2: "原剧本此刻：第一夜/第一日过后，第一个明确的凶兆落地（讣告/镜中异象/纸花/噩梦成真），身边人开始按原剧本轨迹陆续被卷入",
        3: "原剧本此刻：原剧本核心线索节点开始浮现（地下室/契约记录/钟楼旧档/七年前名单），唐迟的自救假假说成型",
        4: "原剧本此刻：按原剧本轨迹应该「出事」的人真的出事了，甚至死在唐迟面前——假假说第一次被打脸",
        5: "原剧本此刻：真相浮现，原主隐藏的罪孽/契约/被替换的秘密即将被撞破",
        6: "原剧本此刻：如果原剧本跨度较短，可能已经接近原剧本所谓的「结局」；如果跨度较长，原剧本仍在推进但唐迟的七天已到大决战前夜",
        7: "原剧本此刻：T-0死劫降临——不管原剧本有没有到它自己的结局，唐迟七天倒计时已到，必须正面硬接死劫",
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
