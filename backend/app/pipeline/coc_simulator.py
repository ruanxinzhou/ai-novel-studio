"""COC 多轮跑团仿真器（Phase 3 + 多 PL / NOVELIZED / 并行批量）。

【关于 DayN 语义——重要】
    本仿真器内部用 Day1-Day7 表示「故事推进的 7 个阶段序号」，
    不是原剧本的日历天。原副本时间线自由（数小时/数天/数月/数代均可），
    KP 在文本里按模组自身时间表述（"当晚"、"第三天凌晨"、"二十年后"等）；
    DayN 仅用于仿真器内部阶段控制和 finalize 分段输出，不要求模组强行凑 7 天。
    短模组可在 finalize 时只填前几段，后续 Day 留空。

核心循环：
    start(state) -> KP 开场
    loop:
        step_investigator(state) -> 当前行动 PL（基于 KP 最新描述）
        step_keeper(state)       -> KP 回应（基于 PL 行动）
        自动解析骰子/SAN/Day 推进；KP 回应后轮转到下一位 PL
        若达到终止条件（终局阶段收束 / 全员 HP<=0 / 全员 SAN=0 / max_turns）：
        切到 awaiting_author 或直接 finalize
    finalize(state) -> 调用 coc_outcome 层把完整日志压成分段真相摘要（最多 7 段）

新增能力（本文件新增/改造点）：
- 多 PL 支持：state.player_cards 支持 2-4 张卡，每个玩家独立 san/hp/mp；
  KP 回应后自动轮转到下一位存活玩家。
- 中等小说化笔调：KP/PL prompt 默认拼 NOVELIZED_*_PROMPT_SUFFIX，
  去除骰点/KP/PL 等元词汇，输出 300-600 / 200-400 字的小说化段落。
- 并行批量：run_coc_parallel_batch() 用 ThreadPoolExecutor 同时跑 3 场，
  配合 build_2same_1different_configs() 实现"2 同 PL + 1 异 PL"配置。

作者介入点（保持不变）：
- pause() 暂停自动循环
- intervene(text) 插入一条作者强制动作（代替 PL 或追加设定）
- set_state(patch) 手动改 SAN/HP/Day/clues（补扣/修正）

与流水线的关系：
- 替换原先"coc_outcome 单轮直出真相"为"先跑多轮 coc 仿真，再用 coc_outcome 压成真相"
- NodeArtifact 节点「coc_outcome」的输入从"选定 adapt_scenario"变成"CocRun 完整日志"
"""
from __future__ import annotations

import json
import random
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from app.context import Material, MaterialFamily
from app.fields.coc_run import (
    DEFAULT_PLAYER_CARD, CocRoll, CocRunState, CocRunStore, CocTurn,
    _init_players_state, _normalize_player_cards, detect_day,
    generate_variant_seed, make_player_card, parse_dice_rolls,
    render_variant_seed,
)
from app.fields.run_store import NodeArtifact
from app.pipeline import SPECS
from app.pipeline.coc_dice import (
    DiceCheck,
    DiceEngine,
    make_dice_engine,
    parse_bonus,
    skill_value_of,
)


# ============================================================
# Prompt 常量：中等小说化笔调
# ============================================================

NOVELIZED_KP_PROMPT_SUFFIX = (
    "你写的不是跑团战报，而是中等细度的小说化场景描述。"
    "不要在文本里出现'骰子'/检定术语/'roll'/'KP'/'PL'等元词汇，不要写数值。"
    "把骰子结果自然化进叙事：成功就是角色做到了，失败就是出了意外/被反制/看到了不该看的东西，"
    "SAN 损失直接写成角色感到一阵毛骨悚然/眼前恍惚/耳边响起低语/某段记忆松动等小说化表现。"
    "每次 KP 回合 300-600 字。"
)

# 原剧本阶段铁律：跑团推演的是模组原剧本，所有调查员都是原创人物（外乡人/本地人），
# 主角唐迟尚未魂穿入场，严禁出现"唐迟"这个名字，也严禁引入魂穿/附身/穿越/七天倒计时/T-0 等
# 只在后续魂穿（embody）阶段才启用的机制。
MODULE_STAGE_NEGATIVE_CONSTRAINT = (
    "\n\n# 原剧本阶段铁律（最高优先级）\n"
    "- 本场推演的是模组「原剧本」本身；所有调查员都是模组原创人物（外乡来客或本地人），"
    "主角「唐迟」尚未魂穿入场——文本中**绝对禁止出现「唐迟」这个名字**，也不要用任何「那个穿越者」「外来者」之类暗指魂穿者的说法。\n"
    "- 禁止引入魂穿/附身/穿越/重生/七天倒计时/T-0 死期/讣告残页/代价账/金手指等机制，这些属于后续写作阶段。\n"
    "- 副本按自身时间线推进（小时/天/月/年自由跨度），**不要**提「第七天」「七天」「倒计时」，不要强行凑 7 个日号。\n"
    "- 调查员的名字只能来自「调查员团队当前状态」中列出的人（PL 卡设定的姓名），不要自创新主角。\n"
)

# ---- 原剧本梗概 prompt（六要素完整，按幕分段） ----
SYNOPSIS_TASK_NOVEL = (
    "# 任务\n"
    "请把本场跑团日志整理成「原剧本真实事件经过」小说化梗概——这是后续所有写作阶段的剧情蓝本，"
    "必须写得像一篇完整的短篇小说，一次性讲清楚**时间、地点、主要人物、起因、经过、结果**六要素，"
    "让读者只读梗概就能知道"
    "「什么时间、在什么地方、一群什么人、因为什么卷入、一步步经历了什么、最后怎样」。\n\n"
    "**结构要求（严格遵守）：**\n"
    "1. 开头先用 1 段「故事背景」（不带 Day/幕 标记，150-250 字）明确交代：\n"
    "   - 时间（什么年代/什么季节/哪一天什么时段）、地点（城镇/场所/关键空间）；\n"
    "   - 主要调查员是谁、他们各自为何来到此地、彼此关系；\n"
    "   - 事件的起因（最初的异象/委托/讣告/诡异之物是什么，他们如何卷入）。\n"
    "2. 之后按故事自然推进分成若干关键阶段（**幕数由日志的实际推进阶段决定，不要凑固定数字**）：\n"
    "   - 每幕以「第N幕：」（N 为阶段序号，从 1 起连续递增，代表阶段序号，不是日历天）开头；\n"
    "   - 幕数应与本场日志里真实发生的推进阶段数相称——日志有多少个明显的推进阶段就写多少幕，"
    "日志只有 3-4 个阶段就写 3-4 幕，跑了十几个阶段就写十几幕；**不要为了凑 7 而合并，也不要为了拉长而拆分**；\n"
    "   - 紧接一段 150-400 字的纯叙事段落，具体写出在该阶段发生的事、调查员的行动与选择、NPC 的回应、重要线索/反转/代价；\n"
    "   - 故事中后段必须揭开本场核心反转，并回收至少 2 个前文伏笔；\n"
    "   - 末幕按本场结局倾向收束，明确写出最终结果（谁死/谁疯/谁活/真相如何/邪神或怪谈的结局），结尾 1 句定调基调。\n"
    "3. 最后补一段「结局总评」（200 字左右）：\n"
    "   - 第一行必须是单独一行【结局标签：XXX】（8-16 字，紧扣本场具体故事，例如「巷子里多了一把伞」「心锚永远留在那条巷」「所有人都成了它的眼睛」，不要用预设词、不要罗列名词）；\n"
    "   - 随后概括幸存者名单、各人付出的代价（死亡/疯狂/失忆/被诅咒等）、本场的主题基调。\n\n"
    "**硬性规则：**\n"
    "- 用模组自身的时间表述（「当晚」「第三天凌晨」「半个月后」等），**绝对不要**提「第七天」「七天」「倒计时」，不要硬凑 7 个日历天。\n"
    "- 不要出现骰子术语（1D100/大成功/大失败）、玩家术语、KP/PL 字样、SAN/HP 数值；\n"
    "- 不要写唐迟/魂穿/金手指/T-0/穿越者；\n"
    "- 人物第一次出场时写清姓名+身份，之后用姓名指代；\n"
    "- 叙事时态用第三人称过去式，保持小说笔调，不要用条目/清单体。"
)
SYNOPSIS_TASK_PLAIN = (
    "# 任务\n"
    "请把本场跑团日志整理成「原剧本真实事件经过」梗概（结构化非小说版）：\n"
    "- 先写「故事背景」段，交代时间/地点/主要人物/起因；\n"
    "- 之后按故事推进分幕，每幕以「第N幕：」开头（N 从 1 起连续递增），"
    "紧接 150-400 字叙事段，写明调查员行动、NPC 动向、重要线索/反转、各调查员的选择与代价；\n"
    "- 幕数由日志实际推进阶段数决定，不要凑固定数字；\n"
    "- 末幕写清最终结果（谁死/谁活/真相如何）；\n"
    "- 最后补 100-200 字「结局总评」，首行单独写【结局标签：XXX】。\n"
    "- 文本用模组自身时间表述，不要硬凑 7 天，不要出现骰子/KP/PL 术语，不要写唐迟/魂穿/金手指。"
)

NOVELIZED_PL_PROMPT_SUFFIX = (
    "你扮演当前行动的调查员，用第一人称或近距离第三人称写出他/她此刻的反应、判断、"
    "行动和一两句话对话（如果有同伴在）。不要出现游戏术语。200-400 字。"
)


# ---- beat 预算（第二步） ----
#
# 上游设计给的公式是 `acts × 4 + clues × 2 + 8`，依赖
# `adapt_parsed.seven_day_arc` 与 `adapt_parsed.key_clue`——
# 这两个键**在整个代码库里不存在**（只在prompts.py 的一行提示词文本里
# 出现过，从未进入任何 schema）。照着实现会直接 KeyError。
#
# 所以这里改用**真实可推断**的信号：NPC 卡司人数 + 调查员人数 + 模组文本长度。
# 系数是首版经验值，等第一个副本跑完拿到 story_complete 时的实际 beat 数再校准。
BEAT_MIN = 8
# BEAT_MAX 从 60 提到 120：真实跑团数据表明 60 拍仍经常不够收束，
# 而现在的诉求是「先把故事跑完整」。真正防止空转烧穿的不是这个上限，
# 而是 _NO_PROGRESS_LIMIT（连续空转即掐断）——那是语义保险丝，
# 比拍数上限精准得多。
BEAT_MAX = 120
_BEAT_PER_NPC = 5# 每名 NPC 大约需要的 beat
_BEAT_PER_PLAYER = 6       # 每名调查员（多人互动更费 beat）
_BEAT_TEXT_UNIT = 200# 每 200 字模组文本约 1 个 beat
_BEAT_BASE = 20


def estimate_beat_budget(adapt_parsed: dict | None,
                         player_cards: list | None = None) -> int:
    """估算本场的 beat 预算。

    不用上游的 acts/clues 公式（字段不存在），改用能拿到的真实信号。
    clamp 到 [BEAT_MIN, BEAT_MAX]，防小模组预算过小、大模组烧穿 token。

    **实测校准（2026-10-22）**：首个副本（雾汀镇·借运帖）三场全部在
    42 拍撞 hard_cap 收场，且 story_phase 停在「展开中」——故事没讲完。
    42 拍是估算公式算出来的，但对这个体量的模组明显不够。
    考虑到作者明确表示「可以暂时不考虑 token 消耗，要的是效率」，
    这里把系数整体上调，让预算更贴近「讲完一个故事」所需的拍数。
    """
    ap = adapt_parsed or {}
    cast = ap.get("cast") or []
    n_npc = len(cast) if isinstance(cast, list) else 0
    n_player = len(player_cards or []) or 1
    # 模组文本长度：用 premise + hook + world_premise 粗略衡量故事体量
    text_len = sum(len(str(ap.get(k) or "")) for k in ("premise", "hook", "world_premise"))
    raw = (_BEAT_BASE + n_npc * _BEAT_PER_NPC + n_player * _BEAT_PER_PLAYER
           + text_len // _BEAT_TEXT_UNIT)
    return max(BEAT_MIN, min(BEAT_MAX, raw))


# ============================================================
# 三条支线的差异化设定（方案 B：由 LLM 从副本核长出来）
# ============================================================
#
# **为什么不再用硬编码池子**：旧实现从 TWIST_POOL / FORESHADOW_POOL /
# ENTRANCE_HOOKS 和五种固定结局标签里随机抽。那些素材写死在代码里，
# 属于项目早期的世界观（唐迟/魂穿/巷子/伞），与当前副本核毫无关系——
# 作者会看到「黑暗结局：巷子里多了一把伞」这种莫名其妙的方向。
#
# 更实质的问题：预设结局**限制 KP**。KP 拿到「你这场必须险胜」的指令后
# 只能去凑那个框架，而不是让故事从副本核与 NPC 动机里自然长出来。
#
# 现在改为：开场前用一次 LLM 调用，读副本核 + NPC 卡司，现场生成三份
# 差异化设定。素材从剧本长出来，不从代码池子里抽。
#
# 分化的三个维度（与旧 seed 字段一一对应，保持下游零改动）：
#   core_twist        核心反转：这三条线各自的核心真相
#   entrance_hook     入场钩子：这一线怎么开始
#   ending_direction  结局走向：这三条线各自可能走向什么结局（**描述
#                     方向而非指定结局**，KP 仍可推翻——这是与旧实现
#                     最重要的区别）
#
# 保留的旧字段：
#   san_start_penalty / obsession_start_uses  这两个是**机制参数**不是
#     剧情素材，随机取值合理，且能制造真实的开局差异。

# LLM 生成失败时的兜底：只给机制参数，不给任何剧情素材。
# 宁可三条线完全同源（靠骰子+KP 自由发挥分化），也不要塞硬编码假素材。
def _fallback_branch_seed() -> dict:
    return {
        "flavor": "free",
        "core_twist": "",
        "planted_foreshadows": [],
        "entrance_hook": "",
        "ending_direction": "",
        "ending_label": "",
        "ending_bias": "",
        "tangchi_mood": "",
        "san_start_penalty": 0,
        "obsession_start_uses": 0,
        "source": "fallback",
    }


def _parse_branch_seeds(text: str) -> list[dict] | None:
    """从 coc_seed 的 LLM 输出里解析三条支线设定。

    返回 list[dict]，长度 = 要求的条数；解析不出返回 None（走兜底）。
    解析失败**不抛异常**：这一层失败不该让整个跑团起不来。
    """
    from ..providers.base import parse_json_loose
    data = parse_json_loose(text or "")
    if not isinstance(data, dict):
        return None
    branches = data.get("branches")
    if not isinstance(branches, list) or not branches:
        return None
    out: list[dict] = []
    for b in branches:
        if not isinstance(b, dict):
            continue
        seed = _fallback_branch_seed()
        seed["core_twist"] = str(b.get("core_twist") or "").strip()
        seed["entrance_hook"] = str(b.get("entrance_hook") or "").strip()
        seed["ending_direction"] = str(b.get("ending_direction") or "").strip()
        # ending_label 是**给作者看的短标签**，模型给什么就用什么，
        # 不做枚举映射——映射表本身就是「硬编码结局」的另一种形式。
        seed["ending_label"] = str(b.get("ending_label") or "").strip()
        seed["tangchi_mood"] = str(b.get("opening_mood") or "").strip()
        fs = b.get("planted_foreshadows")
        if isinstance(fs, list):
            seed["planted_foreshadows"] = [str(x).strip() for x in fs if str(x).strip()]
        seed["source"] = "llm"
        out.append(seed)
    return out or None


def generate_branch_seeds(
    runner,
    adapt_parsed: dict | None,
    *,
    n_branches: int = 3,
    player_sets_meta: list[dict] | None = None,
) -> list[dict]:
    """生成 n_branches 份差异化设定，全部基于当前副本核。

    失败时返回等长的兜底 seed（无剧情素材）——**不抛异常**，
    这一层是增强项，不该成为跑团的单点故障。
    """
    ap = adapt_parsed or {}
    premise = str(ap.get("world_premise") or ap.get("premise") or "").strip()
    if not premise:
        return [_fallback_branch_seed() for _ in range(n_branches)]

    cast = ap.get("cast") or []
    cast_lines = []
    for c in cast if isinstance(cast, list) else []:
        if not isinstance(c, dict):
            continue
        cast_lines.append(
            f"- {c.get('name','?')}（{c.get('occupation','')}）："
            f"动机 {c.get('motivation','')}；秘密 {c.get('secret','')}"
        )
    cast_block = "\n".join(cast_lines) or "（副本核未提供 NPC 卡司）"

    pl_block = ""
    if player_sets_meta:
        parts = []
        for i, meta in enumerate(player_sets_meta):
            cards = (meta or {}).get("investigators") or []
            names = "、".join(
                f"{c.get('name','?')}（{c.get('occupation','')}）"
                for c in cards if isinstance(c, dict)
            )
            if names:
                parts.append(f"- 第{i+1}线：{names}")
        pl_block = "\n".join(parts)

    x = (
        f"# 副本核\n\n{premise}\n\n"
        f"# 副本内NPC 卡司（名字与动机必须严格使用）\n\n{cast_block}\n\n"
        + (f"# 各线调查员（PL）组合\n\n{pl_block}\n\n" if pl_block else "")
        + f"# 本次任务\n\n"
        f"为这个副本设计 **{n_branches} 条差异显著的故事支线**，供三场并行跑团使用。\n\n"
        f"三条线必须满足：\n"
        f"1. **共用同一个副本核**（世界规则、核心NPC、终局真相一致），只在**调查路径与代价**上分叉。\n"
        f"2. 三条线的**核心反转要真正不同**——不能是同一个真相的三种说法。\n"
        f"3. 所有内容必须从上面的副本核与 NPC 卡司里长出来，"
        f"**不许引入副本核之外的设定**（不要凭空出现新的地点、器物、规则）。\n"
        f"4. NPC 的名字、职业、动机、秘密必须与卡司一致，不能改写身份。\n\n"
        f"【输出格式】**只输出一个 ```json 代码块，不要任何正文或解释。**\n"
        f"```json\n"
        # 下面这段刻意**不用 f-string**：JSON 里的 {} 会被当成插值起始，
        # 导致 SyntaxError: unterminated string literal。n 用普通拼接。
        '{\n  "branches": [\n'
        f'    // 重复 {n_branches} 个对象，每个代表一条支线\n'
        '    {\n'
        '      "branch_id": "A",\n'
        '      "core_twist": "这条线的核心真相：为什么这件事会发生。80-150字，'
        '必须能用副本核里已有的元素解释",\n'
        '      "entrance_hook": "这条线的开场切入点：调查员第一眼看到什么异常。30-60字",\n'
        '      "opening_mood": "这条线里调查员的整体心态倾向。20-40字",\n'
        '      "planted_foreshadows": ["2-3条要埋的伏笔，每条来自副本核里已有的物件/地点/人物细节"],\n'
        '      "ending_direction": "这条线可能走向的结局**倾向**（不是指定结局，KP 可以推翻）。40-80字",\n'
        '      "ending_label": "给作者看的结局短标签，6-12字"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        '```\n\n'
        f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )

    try:
        text = _run_layer_once(runner, "coc_seed", x, [], None)
        seeds = _parse_branch_seeds(text)
        if seeds:
            # 补齐到n_branches（模型给少了就复制兜底）
            while len(seeds) < n_branches:
                seeds.append(_fallback_branch_seed())
            return seeds[:n_branches]
    except Exception:
        pass
    return [_fallback_branch_seed() for _ in range(n_branches)]


# story_phase 枚举与输出间隔（双轨制，见设计文档 §3.3）
STORY_PHASES = ("展开中", "转折点", "已收束")
# 连续多少拍没有信息增量就掐断（第三步）。取 3：两拍是正常连击，
# 三拍说明 PL 已经在原地打转。
_NO_PROGRESS_LIMIT = 3
STORY_PHASE_EVERY = 5        # 常规：每 5 beat 显式输出一次
_TRANSITION_RUN_FOR_DONE = 2     # 连续 N 个「转折点」→ transition_done


def _should_ask_phase(beats_done: int) -> bool:
    """常规节拍：每 STORY_PHASE_EVERY 拍问一次。

    双轨制的「常规」侧——转折点豁免由 KP 自行决定是否提前输出，
    我们只在常规节点显式要求，避免每轮都填字段分散 KP 注意力。
    """
    return beats_done % STORY_PHASE_EVERY == 0


# 预算还剩多少拍时进入强制收束模式。
#
# **为什么需要**：只按预算硬砍会让故事停在半截——三场实测全部以
# hard_cap 收场，42 拍跑完 story_phase 仍是「展开中」，读者拿到的梗概
# 没有结局、像被砍断的日志。留出最后几拍明确告诉 KP「该收尾了」，
# 它就会把线索收拢、揭开反转、给出结局，故事才完整。
_CLOSING_BEATS = 6


def _closing_phase(beats_done: int, budget: int) -> bool:
    """当前是否处于「预算临期、必须收束」阶段。"""
    if not budget or budget <= 0:
        return False
    return (budget - beats_done) <= _CLOSING_BEATS


def _extract_json_obj(raw: str) -> dict:
    """从模型输出里抠出第一个完整 JSON 对象。

    **为什么不用正则 `\\{.*?\\}`**：非贪婪匹配在遇到**字符串值里的花括号**
    或**嵌套对象**时会提前截断，抠出来的片段不是合法 JSON，json.loads 直接抛错。
    更糟的是模型在 JSON 前后加了自己的解释文字（"以下是本拍的裁决："），
    或者输出被截断没有闭合围栏时，正则连匹配都匹配不上。

    这里改成**括号配平扫描**：跳过字符串字面量与转义，找到第一个
    深度归零的右花括号为止。这样嵌套对象、数组、字符串里的花括号都能正确处理。

    返回 {} 表示没找到（调用方按「解析失败」处理）。
    """
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    if not text.strip():
        return {}

    # 1) 先找 ```json 围栏，围栏内做配平；没有围栏就在全文里配平。
    fence = re.search(r"```json\s*(.*?)(?:```|\Z)", text, flags=re.S)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)

    for body in candidates:
        start = body.find("{")
        if start < 0:
            continue
        # **先清洗裸控制字符再扫描**：模型偶尔在 JSON 字符串值里塞未转义的
        # 换行/制表符。这会让配平扫描把字符串提前判为结束，后续引号全部错位，
        # 整段 JSON 抠不出来。所以清洗必须在扫描之前做。
        body = _escape_raw_control_chars(body)
        start = body.find("{")
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(body)):
            ch = body[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = body[start:i + 1]
                    obj = _try_load_json(blob)
                    if obj is not None:
                        return obj
                    break
    return {}


def _escape_raw_control_chars(text: str) -> str:
    """把 JSON 里未转义的控制字符转成合法转义序列。

    模型偶尔在 JSON 字符串值里塞入裸换行/制表符，这在标准 JSON 里非法，
    但语义完全可救。直接转义掉再解析，比直接放弃这一拍划算得多。

    **只处理真正的裸控制字符**（0x00-0x1f）：已经是合法转义序列的
    `\\n`（反斜杠 + 字母 n）不是裸控制字符，不会被重复处理。
    """
    return re.sub(
        r"[\x00-\x1f]",
        lambda m: "\\u%04x" % ord(m.group(0)),
        text,
    )


def _try_load_json(blob: str) -> dict | None:
    """尝试把一段文本解析成 dict，失败返回 None。"""
    try:
        obj = json.loads(blob)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def parse_pl_action(text: str) -> dict:
    """解析 PL 动作声明（第三步）。

    PL 只输出一个 JSON 块：action / motivation / hypothesis / cost。
    **解析失败绝不抛异常**——返回 {}，这一拍视为「行动不明」。

    注意：只要解析出了 action（哪怕只有这一个字段），就算有效——
    四个字段不必全齐，缺字段不该让整拍作废。
    """
    return _extract_json_obj(text)


def parse_kp_verdict(text: str) -> dict:
    """解析 KP 决策链输出（第三步）。

    KP 现在只输出一个 JSON 块（含 check.success_effect / check.failure_effect），
    不再有「纯叙事正文」那一半——画面展开交给通稿层。

    **解析失败绝不抛异常**：LLM 不保证输出合法 JSON，一次解析失败不该让
    整场跑团挂掉。失败时返回 {}，该拍不掷骰、不记 check。

    提取走 _extract_json_obj（括号配平），不用脆弱的非贪婪正则——
    旧实现正则匹配不到带解释文字或未闭合围栏的输出，
    实测 42 拍里有 19 拍（45%）因此静默作废。
    """
    return _extract_json_obj(text)


def _resolve_check(state: CocRunState, check_info: dict) -> DiceCheck:
    """执行一次真判定：用人物卡技能值 + 难度加成掷 1D100。

    **这是本模块的核心约束**：KP 只提供两套后果文本，走哪一套由这里的
    掷骰结果决定。KP 无法决定成败。
    """
    engine = _get_dice_engine(state)
    skill = str(check_info.get("skill") or "意志")
    difficulty = str(check_info.get("difficulty") or "困难")
    card = state.current_player_card() if hasattr(state, "current_player_card") else {}
    skill_val = skill_value_of(card, skill)
    bonus = parse_bonus(difficulty)
    beats_done = int(getattr(state, "beats_done", 0) or 0)
    return engine.roll(
        beat=beats_done + 1,
        skill=skill,
        skill_value=skill_val,
        difficulty=difficulty,
        player_idx=state.active_player_idx,
        player_name=state.current_player_name(),
        bonus=bonus,
    )


def _san_loss_for(check: DiceCheck) -> int:
    """失败时的 SAN 损失：按失败等级给固定档位（不掷骰，避免双重随机）。"""
    from app.pipeline.coc_dice import RESULT_FUMBLE, RESULT_EXTREME_FAIL, RESULT_HARD_FAIL
    if check.result == RESULT_FUMBLE:
        return 5
    if check.result == RESULT_EXTREME_FAIL:
        return 3
    if check.result == RESULT_HARD_FAIL:
        return 1
    return 0  # 普通失败不损失 SAN，只在 effect 里体现代价


def _render_verdict_text(scene: str, effect: str, check: DiceCheck | None) -> str:
    """把裁决渲染成回合正文（决策记录 + 场景描写）。

    **scene 必须保留原样**（不能截断）：它和 effect 合起来才是这一拍的全部
    信息增量。旧实现把 scene 直接放进正文还算完整，但 effect 与 scene
    都很短（各 20-40 字），42 拍拼起来只有 2 千多字——
    「跑了 42 轮怎么只有这么点内容」的根因就在这里：
    **每拍记的是裁决摘要，不是叙事**。
    真正的血肉由通稿层（coc_narrative）负责展开，本层保证的是因果链完整。
    """
    parts: list[str] = []
    if scene:
        parts.append(scene)
    if check is not None:
        parts.append(f"【{check.one_line()}】")
    if effect:
        parts.append(effect)
    return "\n".join(parts) if parts else "（本拍无有效裁决）"


def _get_dice_engine(state: CocRunState) -> DiceEngine:
    """取得（并在必要时初始化）本场的骰子引擎。

    引擎挂在 state 上，保证同一场跑团内骰子序列连续；seed 由场次派生，
    保证同场重跑得到完全相同的序列（可复盘）。
    """
    engine = getattr(state, "_dice_engine", None)
    if engine is None:
        extra = str((state.variant_seed or {}).get("seed", "") or "")
        engine = make_dice_engine(state.field_no, state.session_index, extra)
        try:
            # 挂到 state 上（不进 as_dict，避免 json 序列化一个不可序列化对象）
            object.__setattr__(state, "_dice_engine", engine)
        except Exception:
            pass
    return engine


def parse_kp_dual_output(text: str) -> tuple[dict, str]:
    """解析 KP 双输出：返回 (结构化裁决, 叙事描写)。

    LLM 不保证输出合法 JSON，所以**解析失败绝不抛异常**——一次解析失败
    不该让整场跑团挂掉。失败时返回 ({}, 原文)，叙事照常使用。

    格式约定：先输出一个 ```json 代码块，再输出纯叙事。
    代码块不存在时（非 novelized 模式、或模型没听话）退化为「全当叙事」。
    """
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    m = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.S)
    if not m:
        m = re.search(r"```\s*(\{.*?\})\s*```", raw, flags=re.S)
    if not m:
        return {}, raw.strip()
    try:
        obj = json.loads(m.group(1))
    except Exception:
        return {}, raw.strip()
    if not isinstance(obj, dict):
        return {}, raw.strip()
    narr = (raw[:m.start()] + raw[m.end():]).strip()
    return obj, (narr or raw.strip())


# ---- 终止判定 ----

def _all_players_down(state: CocRunState) -> bool:
    """多 PL 模式下：全员死亡或全员疯狂才判定团灭。"""
    ps = state.players_state
    if not ps:
        return state.hp <= 0 or state.san <= 0
    alive_sane = [
        p for p in ps
        if p.get("alive", True) and not p.get("insane", False) and p.get("hp", 0) > 0 and p.get("san", 0) > 0
    ]
    return len(alive_sane) == 0


def _check_termination(state: CocRunState, last_phase: str | None = None) -> str | None:
    """返回终止原因字符串，否则 None。副本按自身时间线推进，不做天数硬上限。

    **本函数最重要的产出不是「停不停」，而是「为什么停」**（第二步）。
    旧实现只有 dead/insane/max_turns 三种，作者无法区分「故事讲完了」
    和「被砍断了」——这两者对他的意义完全不同：前者可直接用，后者要加预算重跑。

    终止原因的语义（前端必须按这个区分显示）：
        story_complete   故事讲完了（KP 主动输出「已收束」）→ 可直接用
        transition_done  转折已走完（连续转折点）           → 可用
        dead / insane    团灭                → 正常结局
        hard_cap         撞上 beat 预算被截断 → 剧情未完成，需加预算重跑
        max_turns        撞上消息条数硬顶     → 同上

    保留 max_turns 是有意的（上游设计 §4.1.5 要求移除，**不采纳**）：
    它是单次跑团的 token 保险丝，与 beat_budget 的语义预算角色不同。
    beat_budget 通常先撞，所以 max_turns 是兜底。
    """
    # 剧情收束判定放在最前——「讲完了」比「撞上限」更值得优先报出来。
    phase = last_phase or getattr(state, "last_story_phase", "") or ""
    if phase == "已收束":
        return "story_complete"
    if (phase == "转折点"
            and getattr(state, "consecutive_transition", 0) >= _TRANSITION_RUN_FOR_DONE):
        return "transition_done"

    if _all_players_down(state):
        ps = state.players_state
        any_alive = any(p.get("hp", 0) > 0 for p in ps) if ps else state.hp > 0
        if not any_alive:
            return "dead"
        return "insane"

    # beat 预算：语义预算，撞上去 = 被砍断，必须与 story_complete 区分开
    budget = int(getattr(state, "beat_budget", 0) or 0)
    if budget and getattr(state, "beats_done", 0) >= budget:
        return "hard_cap"
    # 空转掐断（第三步）：连续 N 拍没有信息增量 = PL 在原地打转。
    # 这是决策链模拟最常见的空转形态——反复试探已确认的事，
    # 不掐断就会一路烧到 hard_cap。
    if int(getattr(state, "no_progress_streak", 0) or 0) >= _NO_PROGRESS_LIMIT:
        return "no_progress"
    if len(state.turns) >= state.max_turns:
        return "max_turns"
    return None


# ---- x_block 装配 ----

def _players_status_line(state: CocRunState) -> str:
    """在 x_block 里展示团队状态（小说化模式下仍保留数值用于 KP 参考，但不出现在最终叙事里）。

    **不再输出「阶段N/7」**（第二步）：跑团按自身时间线推进，没有 7 段，
    这个伪进度只会让 KP 收到自相矛盾的信号——它在第 6 拍还看到「阶段 1」，
    同时又被要求「中后段推进反转」。
    """
    ps = state.players_state
    if not ps:
        return "（单调查员模式）"
    parts = []
    for i, p in enumerate(ps):
        flag = ""
        if p.get("insane"):
            flag = "（疯狂）"
        elif not p.get("alive", True):
            flag = "（死亡）"
        parts.append(f"{p.get('name', f'PL{i+1}')} SAN{p.get('san','?')} HP{p.get('hp','?')}{flag}")
    return "；".join(parts)


def _beats_constraint_material(state: CocRunState) -> Material | None:
    """把最近的 beat 结构化裁决回注给 KP（第二步核心机制）。

    旧实现里线索只记在 `state.clues_found`，而 KP 下一轮的 prompt **不拿它当约束**，
    于是 PL 会在已经查清的事上重复试探。这条材料把「已查清/未查清」显式摊给 KP，
    让它自己避开重复线索、也别跳过快该发现的线索。
    """
    beats = getattr(state, "beats", None) or []
    if not beats:
        return None
    recent = beats[-3:]
    lines: list[str] = []
    for b in recent:
        n = b.get("beat", "?")
        got = b.get("information_gained") or ""
        known = b.get("known_now") or []
        unknown = b.get("unknown_now") or []
        if not (got or known or unknown):
            continue
        seg = [f"第 {n} 拍"]
        if got:
            seg.append(f"获得信息：{got}")
        if known:
            seg.append("已知：" + "；".join(str(x) for x in known[:4]))
        if unknown:
            seg.append("仍未查清：" + "；".join(str(x) for x in unknown[:4]))
        lines.append("- " + " | ".join(seg))
    if not lines:
        return None
    return Material(
        source_id="coc:beats", family=MaterialFamily.REQUIRED,
        content=(
            "【已发生的因果链（最近 3 拍）——这是你必须遵守的连续性约束】\n"
            + "\n".join(lines)
            + "\n\n- 上面「已知」的内容不要让 PL 再查一遍——重复发现同一条线索是致命的平庸。"
              "\n- 上面「仍未查清」的才是他们接下来该追的方向。"
              "\n- 你必须接着上一拍的因果往下推，不能重新开局、不能跳过已经发生的事。"
        ),
        title="因果链约束（最近几拍已经发生了什么）",
    )


def _keeper_materials_and_x(state: CocRunState, adapt_parsed: dict | None,
                            book_store, *, novelized: bool) -> tuple[list[Material], str]:
    """KP 回合的 materials + x_block。"""
    mats: list[Material] = []
    if book_store is not None:
        mats.append(Material(
            source_id="book:hardcards", family=MaterialFamily.REQUIRED,
            content=book_store.render_for_module_run(),
            title="书级硬卡（原剧本阶段：已剥离唐迟/魂穿/七天倒计时）",
        ))
    if adapt_parsed:
        mats.append(Material(
            source_id="coc:adapt", family=MaterialFamily.REQUIRED,
            content=json.dumps(adapt_parsed, ensure_ascii=False, indent=2),
            title="选定 COC 模组方案",
        ))
        # 把 NPC 卡司格式化成人读文本，单独塞一份材料，强化 KP "只能使用这些命名人物" 的约束
        cast_list = adapt_parsed.get("cast") or []
        if isinstance(cast_list, list) and cast_list:
            from ..pipeline.sushi_generator import _cast_to_text
            cast_text = _cast_to_text(cast_list)
            mats.append(Material(
                source_id="coc:cast", family=MaterialFamily.REQUIRED,
                content=(
                    "【剧本已命名人物（NPC/KPC 卡司）——KP 必须严格使用这些人物的姓名/身份/秘密/动机/关系来扮演，"
                    "不得为他们改名，不得新增其他有姓名、有戏份的核心人物。】\n\n"
                    + cast_text
                    + "\n\n【硬性要求】\n"
                    "- 上面这份名单是本副本里所有「有姓名的人物」。所有有名有姓的出场角色，必须来自这份名单；"
                    "姓名必须原样使用（含姓氏，不得简称/换名/谐音）。\n"
                    "- 你可以让他们提前登场、延后登场、以不同侧面出场，但不要改写他们的核心动机/秘密/未了执念/当前状态——"
                    "这些是这个故事能成立的骨架。\n"
                    "- 允许出现无姓名的背景路人（例如「便利店的另一个客人」「穿雨衣的过路人」「楼道里咳嗽的老人」），"
                    "但路人不得开口透露关键线索、不得抢走 NPC 卡司人物的戏份、不要给路人起名字。\n"
                    "- 绝对不允许从其他模组/影视/文学里借用知名人物，也不要用「林觉/赵魁/唐迟」等预设姓名。"
                ),
                title="剧本 NPC 卡司（KP 必须严格遵守）",
            ))
        # PL 调查员列表（本场玩家角色）
        player_meta = (state.variant_seed or {}).get("player_meta") or {}
        invs = player_meta.get("investigators") if isinstance(player_meta, dict) else []
        if isinstance(invs, list) and invs:
            pl_lines = []
            for i, c in enumerate(invs, 1):
                pl_lines.append(
                    f"{i}. {c.get('name','?')}（{c.get('occupation','')}，{c.get('gender','')}，{c.get('age','')}岁，"
                    f"{c.get('origin','外来')}）\n"
                    f"   - 外貌/气质：{c.get('appearance','')}\n"
                    f"   - 性格：{c.get('personality','')}\n"
                    f"   - 卷入理由：{c.get('reason_here','')}\n"
                    f"   - 个人秘密：{c.get('personal_secret','')}"
                )
            team_note = player_meta.get("team_composition_note", "") if isinstance(player_meta, dict) else ""
            mats.append(Material(
                source_id="coc:players_cards", family=MaterialFamily.REQUIRED,
                content=(
                    "【本场 PL 调查员（玩家角色）——这些是即将被卷入事件的人，KP 开场必须让他们以自己的理由"
                    "自然进入场景；所有 PL 的姓名/职业/性别/性格必须严格按下面写，不得改名/换身份】\n\n"
                    + "\n".join(pl_lines)
                    + (f"\n\n本场组合特点：{team_note}" if team_note else "")
                    + "\n\n【硬性要求】\n"
                    "- PL 是玩家角色，不是副本 NPC；他们原本不在 NPC 卡司里，是被事件卷入的外来者/路人。\n"
                    "- 开场 KP 必须安排每个 PL 以符合他们「卷入理由」的方式登场（不是所有人都从天而降，也不是所有人都互相认识）。\n"
                    "- 不要提前告诉 PL 事件真相，也不要让他们的个人秘密在开场就被其他人知道。"
                ),
                title="本场 PL 调查员卡（玩家角色，必须按此姓名/职业登场）",
            ))
    seed_text = render_variant_seed(state.variant_seed or {})
    if seed_text:
        mats.append(Material(
            source_id="coc:variant_seed", family=MaterialFamily.REQUIRED,
            content=seed_text,
            title=f"本场（候选#{state.session_index}）差异化种子",
        ))
    mats.append(Material(
        source_id="coc:players", family=MaterialFamily.CONTEXT,
        content=state.players_render(),
        title="调查员团队当前状态",
    ))
    # 因果链约束（第二步）：最近几拍已经查清/未查清的事，必须回注给 KP
    _beats_mat = _beats_constraint_material(state)
    if _beats_mat is not None:
        mats.append(_beats_mat)

    transcript = state.transcript(last_n=6) or "（开场，尚无日志）"

    if novelized:
        # 决策链格式（第三步）。核心变化：
        # 1. KP **不再写描写**，只给 check.success_effect / check.failure_effect 两套后果，
        #    由**代码掷骰**决定走哪一套——KP 不能决定成败。
        # 2. 叙事压到一句话（scene），画面展开交给通稿层。
        # 3. 输出长度从 300-400 字降到 ~120 字，单拍延迟大幅下降。
        beats_done = int(getattr(state, "beats_done", 0) or 0)
        budget = int(getattr(state, "beat_budget", 0) or 0)
        ask_phase = _should_ask_phase(beats_done)
        phase_line = (
            f'- "story_phase" 本拍**必填**：{"/".join(STORY_PHASES)}。'
            f"判断标准：{'核心反转已揭晓、正在收束' if ask_phase else '按当前实际进度如实填'}。\n"
            if ask_phase else
            '- "story_phase" 可以留空（默认按「展开中」处理）。'
            "**但如果你判断剧情此刻就该收束、或刚好过了转折点，请主动如实填写**——"
            "这决定跑团是自然讲完还是被硬砍。\n"
        )
        progress_line = (
            f"# 当前进度\n\n第 {beats_done + 1} 拍 / 预算 {budget} 拍\n\n"
            if budget else f"# 当前进度\n\n第 {beats_done + 1} 拍\n\n"
        )
        x = (
            f"{progress_line}"
            f"# 团队状态\n\n{_players_status_line(state)}\n\n"
            f"# 【人物纪律（最高优先级，违反即输出作废）】\n"
            f"- 所有「有姓名的出场人物」**必须**使用材料「剧本 NPC 卡司（KP 必须严格遵守）」中列出的姓名/身份/秘密/动机/关系，"
            f"姓名原样使用（含姓氏），不得改名/换姓/谐音/简称，不得杜撰新的命名人物。\n"
            f"- PL（调查员）的姓名/职业/外貌/性格/卷入理由/个人秘密**必须**严格使用材料「本场 PL 调查员卡」中的设定，"
            f"不得替他们换身份/换职业/换名字；PL 不是副本 NPC，他们按各自的卷入理由自然登场。\n"
            f"- 无姓名的背景路人可以出现，但路人不得开口透露关键线索、不要给路人起名字、不要让路人抢走卡司人物的戏份。\n"
            f"- 禁止出现「林觉/赵魁/唐迟」等预设姓名；禁止魂穿/穿越/重生/七天倒计时/T-0。\n\n"
            f"# KP 本轮任务（决策链模式）\n"
            f"你不写小说正文。你负责**裁决**：把当前调查员的行动判成结果，并记录信息增量。\n"
            f"当前轮到行动的调查员是：{state.current_player_name()}。\n\n"
            f"【最重要】**成败由系统掷骰决定，不由你决定。**\n"
            f"你只提供两套后果文本（成功时发生什么、失败时发生什么），系统掷出成功就取 success_effect，"
            f"掷出失败就取 failure_effect。**不要写结果、不要写「他成功了」这种既成事实、"
            f"不要在文本里透露掷骰点数或技能值**。\n\n"
            f"- \"scene\"：一句话场景定位（地点+正在发生什么），供后续写作用，不要展开成段落；\n"
            f"- \"check.skill\"：本次判定用到的技能名（从人物卡技能里选，没有就用「意志」）；\n"
            f"- \"check.difficulty\"：极难|困难|常规|容易 之一；\n"
            f"- \"check.success_effect\"：判定成功时，调查员**具体**发现了什么（要推进案情，不能是废话）；\n"
            f"- \"check.failure_effect\"：判定失败时，调查员**具体**损失了什么/被挡住了（要有代价，不能只是「什么也没发生」）；\n"
            f"- \"information_gained\"：把这次判定得到的信息浓缩成一句（可为空）；\n"
            f"- \"world_state_change\"：世界因此发生的具体变化；\n"
            f"- \"known_now\" / \"unknown_now\"：此刻全员已知 / 未知的事；\n"
            f"- \"next_pressure\"：新的压力或悬念。\n\n"
            f"副本按自身时间线推进（小时/天/月/年自由跨度），"
            f"用模组自然的时间表述，**不要写 DayN 标记**。"
            f"保留克苏鲁恐怖的未知感，不要一次掀开谜底；核心反转在合适的时机推进，不要草率掀底。\n\n"
            f"# 【输出格式（必须严格遵守）】\n"
            f"**只输出一个 ```json 代码块，不要输出任何正文、解释或前后缀。**\n\n"
            f"```json\n"
            f'{{\n  "beat": {beats_done + 1},\n'
            f'  "scene": "一句话场景定位",\n'
            f'  "check": {{\n'
            f'    "skill": "侦查",\n'
            f'    "difficulty": "困难",\n'
            f'    "success_effect": "成功时的具体发现",\n'
            f'    "failure_effect": "失败时的具体代价"\n'
            f'  }},\n'
            f'  "information_gained": "本拍得到的信息",\n'
            f'  "world_state_change": "剧情变化",\n'
            f'  "known_now": ["全员已知的事"],\n'
            f'  "unknown_now": ["还没查清的事"],\n'
            f'  "next_pressure": "新的压力/悬念",\n'
            f'  "story_phase": "展开中|转折点|已收束"\n'
            f"}}\n```\n\n"
            f"{phase_line}"
            f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    else:
        # 非小说化模式同样去掉「阶段N/7」——它是从伪进度继承来的错误信号。
        x = (
            f"# 当前进度\n\n第 {int(getattr(state, 'beats_done', 0) or 0) + 1} 拍"
            f"   SAN {state.san}   HP {state.hp}\n\n"
            f"# 最近回合\n\n{transcript}\n\n"
            f"# 【人物纪律（最高优先级）】\n"
            f"- 所有有姓名的人物必须来自材料「剧本 NPC 卡司」，姓名原样使用，不得杜撰命名人物；\n"
            f"- PL 的姓名/职业/秘密必须严格遵循材料「本场 PL 调查员卡」，不得改名换身份；\n"
            f"- 路人无名无姓、不得透露关键线索；禁止林觉/赵魁/唐迟等预设名，禁止魂穿/七天/T-0。\n\n"
            f"# KP 本轮任务\n"
            f"请扮演守密人，推进第{state.current_day}阶段的情节：\n"
            f"- 用第三人称小说笔调描写环境、NPC 行动、诡异征兆；\n"
            f"- 当需要判定时，在文中明确写出「投掷 XX：rolled/target 成功/失败」；\n"
            f"- SAN 检定失败时写明「失去 N SAN」；\n"
            f"- 必须保留克苏鲁恐怖的未知感与无力感，不要一次掀开谜底；\n"
            f"- 副本按自身时间线推进，不必凑 7 天；本场需要一个核心反转，在合适时机推进；\n"
            f"- 控制在 800-1200 字。"
            f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    return mats, x


def _investigator_x(state: CocRunState, last_keeper_text: str,
                    user_directive: str | None, *, novelized: bool) -> str:
    """PL 回合的 x_block。"""
    pname = state.current_player_name()
    pcard = state.current_player_card() or {}
    occ = pcard.get("occupation", "")
    gender = pcard.get("gender", "")
    age = pcard.get("age", "")
    appearance = pcard.get("appearance", "")
    personality = pcard.get("personality", "")
    reason_here = pcard.get("reason_here", "")
    personal_secret = pcard.get("personal_secret", "")
    origin = pcard.get("origin", "")
    skills = pcard.get("skills") or {}
    sk_str = "、".join(f"{k}{v}" for k, v in skills.items())
    mates = [ps.get("name", "") for i, ps in enumerate(state.players_state) if i != state.active_player_idx]
    mates_line = ""
    if mates:
        mates_line = f"同行伙伴：{'、'.join(m for m in mates if m)}。"

    # 为 PL LLM 提供完整的「我是谁」卡，包括外貌/性格/卷入理由/个人秘密——
    # 这是角色能演出「我自己的算盘」的关键；否则 PL 只会用通用调查员口吻反应。
    identity_block = (
        f"姓名：{pname}    身份：{occ}    "
        f"{'性别：' + gender + '    ' if gender else ''}"
        f"{'年龄：' + str(age) if age else ''}\n"
        f"出身/来历：{origin or '（未指定）'}\n"
        f"外貌/气质：{appearance or '（未指定，请按普通人印象补充）'}\n"
        f"性格：{personality or '（未指定，请按身份合理演绎）'}\n"
        f"你为什么会在这里：{reason_here or '（未指定，请按身份合理补一个合理理由）'}\n"
        f"你的个人秘密（此刻只有你自己知道）：{personal_secret or '（未指定，你可以临时给一个不破坏模组基调的小秘密）'}\n"
        f"关键技能：{sk_str or '通用技能'}\n"
    )

    if novelized:
        # 动作声明格式（第三步）。四个字段直接对应作者的四个要求：
        # 做什么动作 / 为什么 / 想验证什么 / 付出了什么。
        # 不再写心理描写——心理展开是通稿层的活。
        return (
            f"# 你的人物\n\n"
            f"{identity_block}\n"
            f"你是一个有自己缺点、算盘和秘密的普通人，不是英雄。{mates_line}\n"
            f"你此刻的选择应当符合你的性格、你的卷入理由和你藏着的秘密——你不是全知的，你会害怕、会犹豫、会自私。\n\n"
            f"# 刚才发生的事\n\n{last_keeper_text}\n\n"
            + (f"# 作者本节特别指示\n{user_directive}\n\n" if user_directive else "")
            + f"# 此刻轮到你（{pname}）行动\n\n"
            + f"【你不写小说正文，只做行动声明。】输出一个 JSON 块，四个字段：\n"
              f'- "action"：你**具体做什么**。一句话，动宾结构，要能被判定（例：「撬开井盖」「喊话但不下去」）。\n'
              f'  **必须是动作，不能是心理或评价**。\n'
              f'- "motivation"：你为什么这么做。一句话，写清你的算盘（怕什么/想活/想验证）。\n'
              f'- "hypothesis"：你想验证什么假设。一句话。想确认「井下有人」就直说。\n'
              f'- "cost"：你为此付出了什么 / 暴露了什么。一句话。要具体（「暴露了自己的存在」「同伴知道了我的秘密」）。\n\n'
              f"【硬约束】\n"
              f"- 已经查清的事不要重复试探（见下方已知/未知）；\n"
              f"- 不要写心理描写、不要写对白、不要写场景描写；\n"
              f"- 不要透露你不知道的信息（你不是全知的）；\n"
              f"- action 要能被 KP 判定——模糊的action（\"观察\"\"想办法\"）等于没做。\n\n"
              f"【输出格式】**只输出一个 ```json 代码块，不要任何正文或解释。**\n"
              f"```json\n"
              f'{{"action": "", "motivation": "", "hypothesis": "", "cost": ""}}\n'
              f"```\n\n"
              f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    return (
        f"# 当前行动调查员人物卡\n\n{identity_block}\n{state.player_card_render()}\n\n"
        f"# KP 本轮描述\n\n{last_keeper_text}\n\n"
        + (f"# 作者本节特别指示\n{user_directive}\n\n" if user_directive else "")
        + f"# 扮演 {pname}（{occ}）做出反应\n"
          "你是有自己缺点、算盘和秘密的普通人，不是英雄。输出：先写内心判断（观察到什么/怕什么/你藏着的秘密此刻是否被触动），"
          "再写具体行动（动作+对白+刻意避开什么），最后留一处破绽。控制 300-600 字。"
          f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )


# ---- 单次 LLM 回合 ----

def _run_layer_once(runner, layer: str, x_block: str, materials: list[Material],
                    on_event, directive: str | None = None) -> str:
    spec = SPECS[layer]
    result = runner.run_layer(
        spec, [(x_block, "")], materials=materials,
        user_directive=directive,
        on_event=on_event,
    )
    return result.y


# ---- 回合执行 ----

# ---- 通稿层（第二步）----
#
# final_summary（coc_outcome）保持原样不动：下游 EmbodyView / day_breakdown
# 在读它。通稿是它的**超集**，落在独立字段 full_narrative 上，各服务各下游。

# 这些标记是「桌游味」的机械残留，可以正则确定性清掉；
# 人物简称（姓氏代替全名）没有通用正则，只做检测不做改写——改写要靠 prompt。
_NARRATIVE_DAY_RE = re.compile(r"(?i)\bday\s*[-_]?\s*(\d{1,2})\b")
_NARRATIVE_CN_NUM = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
_NARRATIVE_BANNED_RE = re.compile(
    r"(?i)\b1d\d+\b|\b大成功\b|\b大失败\b|\bsan\s*\d*|\bhp\s*\d*"
    r"|【第\d+拍】|【第[一二三四五六七八九十]+幕】"
)
_NARRATIVE_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|【|\*{1,2})?\s*第\s*[0-9一二三四五六七八九十]+\s*"
    r"(?:拍|幕|阶段|章)\s*[:：]?\s*[:：]?\s*$",
    re.MULTILINE,
)
_TERMINOLOGY_RE = re.compile(
    r"(?i)\bKP\b|\bPL\b|\bPC\b|守密人|调查员卡|玩家|投掷|检定|判定结果"
)


def _cn_number(n: int) -> str:
    """1-31 的整数转中文（只够覆盖 DayN 的量级）。"""
    if n <= 10:
        return _NARRATIVE_CN_NUM[n] if n < 10 else "十"
    if n < 20:
        return "十" + _NARRATIVE_CN_NUM[n - 10]
    return _NARRATIVE_CN_NUM[n // 10] + "十" + (
        _NARRATIVE_CN_NUM[n % 10] if n % 10 else ""
    )


def sanitize_narrative(text: str) -> str:
    """把通稿里的桌游残留确定性清掉。

    只做**可逆性高的机械替换**：DayN → 故事内时间表述、删骰子/数值术语、
    删纯分段标记行。**不删任何叙事内容**——人物简称、因果断裂这类问题
    靠 prompt 约束 + `_narrative_violations` 检测，不在这里硬改。
    """
    if not text:
        return ""
    # DayN → 第N天（这一步把它变成故事内时间，后续 prompt 要求模型不再出现）
    text = _NARRATIVE_DAY_RE.sub(lambda m: f"第{_cn_number(int(m.group(1)))}天", text)
    # 纯分段标记行整行删掉（保留正文段落）
    text = _NARRATIVE_HEADING_RE.sub("", text)
    # 骰子/数值/术语词直接抹掉（不带数字的 SAN/HP 也在内）
    text = _NARRATIVE_BANNED_RE.sub("", text)
    text = _TERMINOLOGY_RE.sub("", text)
    # 清掉替换留下的空行堆叠
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def narrative_violations(text: str, cast_names: list[str]) -> list[str]:
    """检测无法机械修正的通稿问题（主要是人名规则）。

    只检测不改写：擅自把「周师傅」展开成「周成德」会引入原文没有的信息。
    返回人类可读的问题列表，空列表=通过。
    """
    out: list[str] = []
    if not text:
        return ["通稿为空"]
    body = re.sub(r"\s+", "", text)
    for name in cast_names:
        name = (name or "").strip()
        # 只对 2-3 字的中文名做姓氏检测，「王」这种单字名没有可靠的姓氏可拆
        if not (2 <= len(name) <= 3) or not re.fullmatch(r"[\u4e00-\u9fff]+", name):
            continue
        surname = name[0]
        if name in body:
            continue
        # 全名没出现，但姓氏单独出现了 → 很可能被写成了「老周」「周师傅」
        if surname in body:
            out.append(f"「{name}」全名未出现，疑似只用了姓氏「{surname}」代替")
    if _NARRATIVE_DAY_RE.search(text):
        out.append("仍存在 DayN 标记")
    if _NARRATIVE_BANNED_RE.search(text):
        out.append("仍存在骰子或 SAN/HP 数值术语")
    return out


def _narrative_x_block(state: CocRunState, adapt_parsed: dict | None,
                       book_store=None) -> str:
    """装配通稿层输入：结构化 beat 记录（骨架约束）+ 全量叙事描写（血肉）。"""
    # **必须直接用 state.beats**（理由同 step_beat）：`getattr(...) or []`
    # 在 beats 为空时返回临时列表，通稿层就拿不到任何一拍的信息增量，
    # 「必须一拍不落地讲完」这条硬约束直接失去依据。
    beats = state.beats or []
    # beats 缺失时用 storyboard 兜底：它记录了同样的字段（动作/动机/判定/
    # 信息增量/世界变化/已知/未知/遗留压力），只是名字不同。
    if not beats:
        sb = getattr(state, "storyboard", None) or []
        beats = [{
            "beat": b.get("beat"),
            "information_gained": b.get("information_gained"),
            "world_state_change": b.get("world_state_change"),
            "known_now": b.get("known_now"),
            "unknown_now": b.get("unknown_now"),
            "next_pressure": b.get("next_pressure"),
            "story_phase": b.get("story_phase"),
            "scene": b.get("scene"),
            "pl_action": b.get("pl_action"),
            "pl_motivation": b.get("pl_motivation"),
        } for b in sb if isinstance(b, dict)]
    beat_lines: list[str] = []
    for b in beats:
        no = b.get("beat", len(beat_lines) + 1)
        rows = [
            f"第{no}拍",
            f"  场景：{b.get('scene') or '（未记录）'}",
            f"  行动方做了什么：{b.get('pl_action') or '（未记录）'}",
            f"  行动动机：{b.get('pl_motivation') or '（未记录）'}",
            f"  判定结果：{b.get('check') or '（未记录）'}",
            f"  信息增量：{b.get('information_gained') or '（未记录）'}",
            f"  世界状态变化：{b.get('world_state_change') or '（未记录）'}",
            f"  已知：{'、'.join(b.get('known_now') or []) or '（无）'}",
            f"  未知：{'、'.join(b.get('unknown_now') or []) or '（无）'}",
            f"  遗留压力：{b.get('next_pressure') or '（无）'}",
            f"  阶段：{b.get('story_phase') or '展开中'}",
        ]
        beat_lines.append("\n".join(rows))
    beats_text = "\n\n".join(beat_lines) or "（本场没有记录到结构化裁决）"

    cast = (adapt_parsed or {}).get("cast") or []
    cast_desc = "\n".join(
        f"- {c.get('name', '?')}（{c.get('role') or c.get('identity') or '身份未记'}）"
        for c in cast if isinstance(c, dict)
    ) or "（模组未提供 NPC 列表）"

    players_desc = "、".join(
        f"{p.get('name', '?')}（{state.player_cards[i].get('occupation', '')}）"
        for i, p in enumerate(state.players_state)
    ) if state.players_state else f"{state.current_player_name()}"

    survivors = _collect_survivors(state)
    budget = int(getattr(state, "beat_budget", 0) or 0)
    return (
        f"# 选定模组方案\n\n{json.dumps(adapt_parsed, ensure_ascii=False, indent=2)}\n\n"
        f"# 人物名单（首出必须写全名+身份，之后用姓名，禁止只用姓氏）\n\n"
        f"## 调查员\n\n{players_desc}\n\n## 模组 NPC\n\n{cast_desc}\n\n"
        f"# 幸存者：{'、'.join(survivors) if survivors else '无人生还'}\n\n"
        f"# 结构化节拍记录（共 {len(beats)} 拍，语义预算 {budget}）\n\n{beats_text}\n\n"
        f"# 逐拍叙事描写（原始素材，可直接展开）\n\n{state.transcript()}\n\n"
        f"# 最终状态\n\n"
        f"已发现线索：{len(state.clues_found)} 条\n"
        f"叙事阶段：{getattr(state, 'last_story_phase', '') or '展开中'}\n\n"
        f"【必须一拍不落地讲完上面每一拍的信息增量；不许新增素材里没有的人物、地点、情节。】\n\n"
        f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )


def build_full_narrative(runner, state: CocRunState, adapt_parsed: dict | None,
                         book_store=None, on_event: Callable | None = None) -> str:
    """产出通稿（8000-12000 字连续故事），写回 state.full_narrative。

    **失败不抛异常**：通稿是增强层，失败时降级为「用叙事描写拼接的降级稿」，
    保证下游拿到的永远是一个非空字符串，而不是一个炸掉的 finalize。
    """
    if on_event:
        on_event("coc_narrative_start", {
            "beats": len(getattr(state, "beats", None) or []),
            "turns": len(state.turns),
        })
    x = _narrative_x_block(state, adapt_parsed, book_store)
    mats: list[Material] = []
    if book_store is not None:
        mats.append(Material(
            source_id="book:hardcards", family=MaterialFamily.REQUIRED,
            content=book_store.render_for_module_run(),
            title="书级硬卡（原剧本阶段：已剥离唐迟/魂穿/七天倒计时）",
        ))
    raw = ""
    err = ""
    try:
        spec = SPECS["coc_narrative"]
        result = runner.run_layer(spec, [(x, "")], materials=mats, on_event=on_event)
        raw = result.y or ""
    except Exception as e:  # 通稿失败不该拖垮整个 finalize
        err = f"{type(e).__name__}: {e}"

    if len(sanitize_narrative(raw)) < 800:
        # 降级：至少给下游一个可用的降级稿（拼叙事描写），并如实标注
        fallback = _fallback_narrative(state)
        state.full_narrative = fallback
        if on_event:
            on_event("coc_narrative_done", {
                "chars": len(fallback), "degraded": True,
                "error": err or "output_too_short",
            })
        return fallback

    out = sanitize_narrative(raw)
    state.full_narrative = out
    if on_event:
        on_event("coc_narrative_done", {
            "chars": len(out), "degraded": False,
            "violations": narrative_violations(out, _narrative_names(state, adapt_parsed)),
        })
    return out


def _narrative_names(state: CocRunState, adapt_parsed: dict | None) -> list[str]:
    names: list[str] = []
    for p in (state.player_cards or []):
        if isinstance(p, dict) and p.get("name"):
            names.append(str(p["name"]))
    for c in ((adapt_parsed or {}).get("cast") or []):
        if isinstance(c, dict) and c.get("name"):
            names.append(str(c["name"]))
    return names


def _fallback_narrative(state: CocRunState) -> str:
    """通稿生成失败时的降级稿：直接拼叙事描写，如实标注不完整。"""
    keeper = [t.text for t in state.turns if t.role == "keeper"]
    parts = [
        "（本场通稿生成失败，以下为未经组织的原始叙事素材拼接，不构成完整通稿。）\n"
    ]
    parts.extend(keeper)
    if not keeper:
        parts.append("（本场没有留下任何守密人叙事，无法生成通稿。）")
    return sanitize_narrative("\n\n".join(parts))


def _distribute_san_loss(state: CocRunState, total_loss: int) -> None:
    """把解析到的 SAN 损失扣给当前行动玩家（主要承受者）。"""
    if total_loss <= 0:
        return
    state.apply_san_loss_to_active(total_loss)


def _build_turn_message(state: CocRunState, role: str, turn_no: int,
                        text: str) -> str:
    """生成 on_event 用的简短中文描述。

    **不再输出 DayN**（第二步）：跑团按自身时间线推进，Day 标记是
    从伪进度里继承来的错误信号，还会顺着 SSE 流进前端提示。
    改成「第 N 拍」——拍数才是真实的进度单位。
    """
    head = (text or "").strip().replace("\n", " ")
    cut = re.split(r"[。！？!?]", head, maxsplit=1)[0]
    if len(cut) > 30:
        cut = cut[:30] + "…"
    beat_no = max(1, (turn_no + 1) // 2)      # 两个 turn = 一个 beat
    if role == "keeper":
        return f"第{state.session_index}场·第{beat_no}拍：{cut}"
    else:
        who = state.current_player_name()
        return f"第{state.session_index}场·第{beat_no}拍：{who}——{cut}"


def step_keeper(runner, state: CocRunState, *, adapt_parsed: dict | None,
                book_store=None, user_directive: str | None = None,
                on_event: Callable | None = None,
                novelized: bool | None = None) -> CocTurn:
    if state.next_role != "keeper":
        raise ValueError(f"当前 next_role={state.next_role}，不能执行 KP 回合")
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    mats, x = _keeper_materials_and_x(state, adapt_parsed, book_store, novelized=novel)
    if on_event:
        on_event("coc_turn_start", {"role": "keeper", "turn_no": len(state.turns) + 1})
    text = _run_layer_once(runner, "coc_keeper", x, mats, on_event, directive=user_directive)
    # 决策链解析（第三步）：KP 只输出一个 JSON 块。
    # 解析失败不抛异常——退化成「无 check 的空裁决」，这一拍不掷骰。
    verdict = parse_kp_verdict(text) if novel else {}
    scene = str(verdict.get("scene") or "").strip()

    # ---- 真判定：成败由代码掷骰，KP 不能决定 ----
    check_info = verdict.get("check") or {}
    dice_check = None
    effect = ""
    if check_info and (check_info.get("success_effect") or check_info.get("failure_effect")):
        dice_check = _resolve_check(state, check_info)
        effect = (check_info.get("success_effect") if dice_check.ok
                  else check_info.get("failure_effect")) or ""
        effect = str(effect)

    # 回合正文 = 场景定位 + 判定结果（一行式，不是小说段落）
    narrative = _render_verdict_text(scene, effect, dice_check)

    # SAN 损失：失败时按难度给固定值（不再让模型用正则"想"出来）
    san_loss_total = 0
    if dice_check is not None and not dice_check.ok:
        san_loss_total = _san_loss_for(dice_check)
        if san_loss_total:
            _distribute_san_loss(state, san_loss_total)

    # story_phase 双轨制：显式输出优先；留空则继承上一轮（首拍按展开中）。
    phase = str(verdict.get("story_phase") or "").strip()
    if phase not in STORY_PHASES:
        phase = ""
    if phase:
        state.last_story_phase = phase
    if phase == "转折点":
        state.consecutive_transition = int(state.consecutive_transition or 0) + 1
    elif phase:
        state.consecutive_transition = 0

    beats_done = int(getattr(state, "beats_done", 0) or 0)
    turn_meta = {
        "rolls": [dice_check.as_dict()] if dice_check else [],
        "san_loss": san_loss_total,
        "day": state.current_day,
        "story_phase": phase or state.last_story_phase or "展开中",
        "parsed": bool(verdict),
    }
    turn = state.add_turn("keeper", narrative, meta=turn_meta)
    if verdict:
        # 结构化裁决入库，下一拍回注给 KP 当连续性约束
        state.beats.append({
            "beat": verdict.get("beat") or (beats_done + 1),
            "scene": scene,
            "check": dice_check.as_dict() if dice_check else {},
            "effect": effect,
            "information_gained": str(verdict.get("information_gained") or ""),
            "world_state_change": str(verdict.get("world_state_change") or ""),
            "known_now": list(verdict.get("known_now") or []),
            "unknown_now": list(verdict.get("unknown_now") or []),
            "next_pressure": str(verdict.get("next_pressure") or ""),
            "story_phase": phase or state.last_story_phase or "展开中",
            "turn_no": turn.turn_no,
        })
    if dice_check is not None:
        state.roll_log.append(dice_check.as_dict())
    # 空转计数（第三步）：本拍有信息增量就清零，没有就累加。
    # 判定成功但没查到东西也算空转——成功的空转比失败的空转更浪费。
    if str(verdict.get("information_gained") or "").strip() or str(scene or "").strip():
        state.no_progress_streak = 0
    else:
        state.no_progress_streak = int(getattr(state, "no_progress_streak", 0) or 0) + 1
    state.next_role = "investigator"
    if len(state.player_cards) > 1:
        state.advance_player()
    if on_event:
        on_event("coc_turn_done", {
            "role": "keeper", "turn_no": turn.turn_no,
            "chars": len(narrative), "san_loss": san_loss_total,
            "rolls": 1 if dice_check else 0,
            "day": state.current_day,
            "active_player": state.current_player_name(),
            "story_phase": state.last_story_phase or "展开中",
            "beat": beats_done + 1,
            "beat_budget": int(getattr(state, "beat_budget", 0) or 0),
            "parsed": bool(verdict),
            "check": dice_check.one_line() if dice_check else "",
        })
    return turn


def step_investigator(runner, state: CocRunState, *,
                      user_directive: str | None = None,
                      on_event: Callable | None = None,
                      novelized: bool | None = None) -> CocTurn:
    if state.next_role != "investigator":
        raise ValueError(f"当前 next_role={state.next_role}，不能执行 PL 回合")
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    last_keeper = ""
    for t in reversed(state.turns):
        if t.role == "keeper":
            last_keeper = t.text
            break
    if not last_keeper:
        last_keeper = f"（场景刚开场。{state.current_player_name()}环顾四周，还没看清周遭）"
    x = _investigator_x(state, last_keeper, user_directive, novelized=novel)
    if on_event:
        on_event("coc_turn_start", {
            "role": "investigator", "turn_no": len(state.turns) + 1,
            "active_player": state.current_player_name(),
            "active_player_idx": state.active_player_idx,
        })
    text = _run_layer_once(runner, "coc_investigator", x, [], on_event, directive=user_directive)
    # 动作声明解析（第三步）：PL 只输出 JSON 四字段。
    # 解析失败不抛异常——退化成「行动不明」，这一拍不写 storyboard。
    decl = parse_pl_action(text) if novel else {}
    action = str(decl.get("action") or "").strip()
    # PL 回合正文渲染成一行决策记录（不再是心理描写段落）
    pl_text = _render_pl_text(action, decl) if action else (text or "").strip()
    turn = state.add_turn("investigator", pl_text, meta={
        "player_idx": state.active_player_idx,
        "action": action,
        "motivation": str(decl.get("motivation") or ""),
        "hypothesis": str(decl.get("hypothesis") or ""),
        "cost": str(decl.get("cost") or ""),
        "parsed": bool(decl),
    })
    # storyboard：把本拍的 PL 动作挂到最近一拍 KP 裁决上（凑成一行分镜）
    _append_storyboard_row(state, action, decl)
    state.next_role = "keeper"
    if on_event:
        on_event("coc_turn_done", {
            "role": "investigator", "turn_no": turn.turn_no, "chars": len(pl_text),
            "active_player": state.current_player_name(),
            "active_player_idx": state.active_player_idx,
            "action": action,
            "parsed": bool(decl),
        })
    return turn


def _beat_module_core(adapt_parsed: dict | None) -> str:
    """模组核：给 KP 裁决所需的**剧情依据**，不是整个 JSON。

    **教训（第四步返工）**：第一版把整个 adapt_parsed 砍成只剩
    premise+基调，自以为省了 10k 字，结果剧情判断垮了——`cast` 里的
    动机/秘密/当前状态正是 KP 决定「这个动作能查出什么」的依据。
    砍掉它，KP 只能凭空编。

    正确做法：**砍冗余字段，保留剧情字段**。
    - 砍：player_cards（与 PL 人物卡重复）、changed_elements / risk_flags /
      index / one_liner / renamed_cast（对逐拍裁决无直接作用）
    - 留：adapted_premise（梗概）+ cast（人物动机与秘密，紧凑渲染）
    实测：11502 字 → ~2600 字，省 77%，但剧情依据完整。
    """
    ap = adapt_parsed or {}
    parts: list[str] = []
    premise = str(ap.get("adapted_premise") or ap.get("premise") or "").strip()
    if premise:
        parts.append(premise)
    flavor = str(ap.get("flavor_full") or ap.get("flavor") or "").strip()
    if flavor:
        parts.append(f"风格基调：{flavor}")

    # NPC 卡司：紧凑渲染，但**保留动机/秘密/当前状态**——这是剧情判断的依据
    cast = ap.get("cast") or []
    if not cast and isinstance(ap.get("player_cards"), list):
        cast = ap["player_cards"]
    cast_lines: list[str] = []
    for c in cast:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "?").strip()
        if not name:
            continue
        role = str(c.get("role") or c.get("identity") or "").strip()
        occ = str(c.get("occupation") or "").strip()
        mot = str(c.get("motivation") or "").strip()
        sec = str(c.get("secret") or c.get("hidden_secret") or "").strip()
        cur = str(c.get("current_state") or "").strip()
        who = f"{name}（{occ or role}）" if (occ or role) else name
        bits = [b for b in (mot, sec, cur) if b]
        if bits:
            cast_lines.append(f"- {who}：" + "；".join(bits))
        else:
            cast_lines.append(f"- {who}（{role or '身份未记'}）")
    if cast_lines:
        parts.append("## NPC 卡司（姓名/身份必须严格照用）\n" + "\n".join(cast_lines))
    return "\n\n".join(parts) or "（模组核信息缺失）"


def _beat_players_core(state: CocRunState) -> str:
    """调查员简表：只给名字/职业/关键技能，不要整张卡（省 2000+ 字）。"""
    cards = state.player_cards or []
    if not cards:
        return "（无调查员）"
    lines: list[str] = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        skills = c.get("skills") or {}
        sk = ("、".join(f"{k}{v}" for k, v in list(skills.items())[:5])
              if isinstance(skills, dict) else "")
        lines.append(f"- {c.get('name', '?')}（{c.get('occupation', '?')}）技能：{sk or '通用'}")
    return "\n".join(lines)


def step_beat(runner, state: CocRunState, *, adapt_parsed: dict | None,
              user_directive: str | None = None,
              on_event: Callable | None = None,
              novelized: bool | None = None) -> CocTurn:
    """一次调用推进完整一拍（第四步：KP 裁决 + PL 动作合并）。

    合并的理由：旧实现里 PL 拍是在**已知判定结果**的前提下补一句
    「他为什么这么做」——这不是决策，是事后合理化。两次调用之间还会
    自相矛盾（KP 说 A，PL 当作 B 来反应）。合并后因果在同一上下文里想。

    仍保留逐拍而不是一次生成多拍：一次性生成 8-10 拍会让模型跳过中段、
    跳掉伏笔回收。逐拍强制推进才是「每拍都有信息增量」的保证。
    """
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    if not novel:
        # 非小说化模式没有合并拍，退回旧的两次调用
        step_keeper(runner, state, adapt_parsed=adapt_parsed,
                    user_directive=user_directive, on_event=on_event, novelized=novel)
        return step_investigator(runner, state, user_directive=user_directive,
                                 on_event=on_event, novelized=novel)

    beats_done = int(getattr(state, "beats_done", 0) or 0)
    budget = int(getattr(state, "beat_budget", 0) or 0)

    # ---- 组装本拍上下文（砍输入：不含硬卡、不含整份 adapted_module）----
    #
    # **必须直接用 state.beats，不能用 `getattr(...) or []`**：
    # 旧写法在 beats 为空时会拿到一个**临时新列表**，后面 beats.append(...)
    # 全写进这个临时列表，state.beats 永远是空的——于是 KP 下一拍拿不到
    # 「已查清/未查清」，只能重复试探同一批线索（真实事故：跑了 42 拍，
    # 落盘 beats=0，storyboard 里有 23 条却没有一条进入 KP 上下文）。
    beats = state.beats
    if beats is None:
        beats = []
        object.__setattr__(state, "beats", beats)
    known: list[str] = []
    unknown: list[str] = []
    for b in beats:
        known.extend(b.get("known_now") or [])
        for u in (b.get("unknown_now") or []):
            if u not in unknown:
                unknown.append(u)
    known = list(dict.fromkeys(known))[-8:]
    unknown = unknown[-8:]

    who = state.current_player_name()
    card = state.current_player_card() or {}
    ps = state.players_state
    cur = ps[state.active_player_idx] if ps and state.active_player_idx < len(ps) else {}

    # ---- 预算临期：强制进入收束模式 ----
    #
    # 旧实现只按预算硬砍：拍数一到就 break，故事停在半截（三场实测全部
    # hard_cap，42 拍跑完仍写着「展开中」，没有结局）。
    # 这里在预算只剩几拍时**明确告诉 KP 该收尾了**，让它把当前线索收拢、
    # 揭开核心反转、给出结局——这样即使预算用尽，故事也是完整的。
    closing = _closing_phase(beats_done, budget)
    if closing:
        ask_phase = True
        phase_line = (
            '- "story_phase" 本拍**必须**填「已收束」——本线即将到达预算上限，'
            "这一拍是收尾拍，必须给出本线的结局。\n"
            "- 本拍要在完成最后一次行动的同时，**揭开本场核心反转、"
            "回收前文伏笔、给出调查员的最终结局**（谁死/谁疯/谁活、真相是什么）。\n"
        )
    else:
        ask_phase = _should_ask_phase(beats_done)
        phase_line = (
            f'- "story_phase" 本拍必填：{"/".join(STORY_PHASES)}'
            f"（{'核心反转已揭晓、正在收束' if ask_phase else '按实际进度如实填'}）。\n"
            if ask_phase else
            '- "story_phase" 留空按「展开中」处理；判断剧情该收束时请主动如实填写。\n'
        )
    x = (
        f"# 本拍进度\n\n第 {beats_done + 1} 拍"
        + (f" / 预算 {budget} 拍" if budget else "") + "\n\n"
        f"# 模组核\n\n{_beat_module_core(adapt_parsed)}\n\n"
        f"# 调查员\n\n{_beat_players_core(state)}\n"
        f"当前行动：{who}（{card.get('occupation', '?')}）"
        f"SAN{cur.get('san', '?')} HP{cur.get('hp', '?')}\n\n"
        f"# 已查清（不要再重复试探）\n"
        + ("\n".join(f"- {k}" for k in known) if known else "- （暂无）") + "\n\n"
        f"# 还没查清（挑一个去验证）\n"
        + ("\n".join(f"- {u}" for u in unknown) if unknown else "- （暂无）") + "\n\n"
        + (f"# 作者本节指示\n{user_directive}\n\n" if user_directive else "")
        + f"# 人物纪律（最高优先级，违反即输出作废）\n"
        f"- 所有有姓名的出场人物**必须**用上面「NPC 卡司」里的姓名/身份，不得改名换姓、不得杜撰新的命名人物；\n"
        f"- NPC 的动机与秘密必须**符合卡司设定**——判定结果要能从他的动机/秘密里长出来，不能凭空编；\n"
        f"- 调查员的姓名/职业按上面「调查员」表；路人无名无姓、不得透露关键线索；\n"
        f"- 禁止出现唐迟/魂穿/穿越/七天倒计时/T-0。\n\n"
        f"# 输出格式（必须严格遵守）\n"
        f"**只输出一个 ```json 代码块，不要任何正文、解释或前后缀。**\n\n"
        f"```json\n"
        f"{{\n"
        f'  "beat": {beats_done + 1},\n'
        f'  "action": "{who}具体做什么（动宾结构，要能被判定）",\n'
        f'  "motivation": "他为什么这么做（怕什么/想活/想验证）",\n'
        f'  "hypothesis": "他想验证什么假设",\n'
        f'  "cost": "他为此付出/暴露了什么",\n'
        f'  "scene": "场景描写：地点+时间+在场人物+正在发生的具体动作与环境细节，80-200 字，要能独立读懂这一拍发生在什么样的画面里",\n'
        f'  "check": {{\n'
        f'    "skill": "侦查",\n'
        f'    "difficulty": "困难",\n'
        f'    "success_effect": "判定成功时的具体发现（要推进案情，80-200 字，写清看到了什么，以及它如何改变调查员对案情的理解）",\n'
        f'    "failure_effect": "判定失败时的具体代价（要有代价，80-200 字，写清损失了什么、暴露了什么、局面如何变得更糟）"\n'
        f"  }},\n"
        f'  "information_gained": "本拍得到的信息",\n'
        f'  "world_state_change": "剧情变化",\n'
        f'  "known_now": ["本拍后全员已知的事"],\n'
        f'  "unknown_now": ["本拍后仍未查清的事"],\n'
        f'  "next_pressure": "新的压力/悬念",\n'
        f'  "story_phase": "展开中|转折点|已收束"\n'
        f"}}\n```\n\n"
        f"{phase_line}"
        f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )

    if on_event:
        on_event("coc_turn_start", {"role": "beat", "turn_no": len(state.turns) + 1,
                                    "beat": beats_done + 1, "beat_budget": budget,
                                    "session": state.session_index})
    # **透传流式事件**：thinking / delta 原样传给调用方，
    # 作者才能实时看到模型此刻在想什么、正在写什么，而不是只等一个
    # 拍结束才看到结果。这是「不要干等」的技术前提。
    def _beat_on_event(kind: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            if kind in ("thinking", "delta", "notice"):
                out = dict(payload)
                out["session"] = state.session_index
                out["beat"] = beats_done + 1
                on_event(kind, out)
        except Exception:
            pass

    text = _run_layer_once(runner, "coc_beat", x, [], _beat_on_event,
                           directive=user_directive)
    out = parse_kp_verdict(text)
    action = str(out.get("action") or "").strip()
    if not action:
        # 没解析出动作 → 退化：这一拍不掷骰、不记 storyboard，但循环继续
        #
        # **把原始输出留下**：解析失败时没有原文就永远查不出原因。
        # 旧实现只写「第 N 拍解析失败」，作者和开发者都看不到模型到底吐了什么，
        # 于是 42 拍里 19 拍静默作废也无人知晓（真实事故）。
        state.next_role = "keeper"
        if on_event:
            on_event("coc_turn_done", {
                "role": "beat", "turn_no": len(state.turns) + 1,
                "beat": beats_done + 1, "beat_budget": budget,
                "chars": len(text or ""), "parsed": False,
            })
        _emit(on_event, "coc_session_error", {
            "session": state.session_index,
            "error": f"第 {beats_done + 1} 拍解析失败",
            "failed_stage": "beat_parse",
        })
        return state.add_turn("system", f"第 {beats_done + 1} 拍解析失败，跳过本拍。",
                              meta={"failed_stage": "beat_parse",
                                    "raw_head": (text or "")[:600]})

    # ---- 真判定（代码掷骰，KP 不能决定）----
    check_info = out.get("check") or {}
    dice_check = _resolve_check(state, check_info) if check_info else None
    effect = ""
    san_loss = 0
    if dice_check is not None:
        effect = str((check_info.get("success_effect") if dice_check.ok
                      else check_info.get("failure_effect")) or "")
        if not dice_check.ok:
            san_loss = _san_loss_for(dice_check)
            if san_loss:
                _distribute_san_loss(state, san_loss)

    scene = str(out.get("scene") or "").strip()
    narrative = _render_verdict_text(scene, effect, dice_check)
    turn = state.add_turn("keeper", narrative, meta={
        "rolls": [dice_check.as_dict()] if dice_check else [],
        "san_loss": san_loss,
        "story_phase": out.get("story_phase") or "",
        "parsed": True,
    })

    # storyboard：本拍一次性成型（动作 + 判定 + 信息增量）
    sb = getattr(state, "storyboard", None)
    if sb is None:
        sb = []
        object.__setattr__(state, "storyboard", sb)
    sb.append({
        "beat": beats_done + 1,
        "player": who,
        "pl_action": action,
        "pl_motivation": str(out.get("motivation") or ""),
        "pl_hypothesis": str(out.get("hypothesis") or ""),
        "pl_cost": str(out.get("cost") or ""),
        "scene": scene,
        "check": dice_check.one_line() if dice_check else "",
        "check_ok": dice_check.ok if dice_check else None,
        "information_gained": str(out.get("information_gained") or ""),
        "world_state_change": str(out.get("world_state_change") or ""),
        "known_now": list(out.get("known_now") or []),
        "unknown_now": list(out.get("unknown_now") or []),
        "next_pressure": str(out.get("next_pressure") or ""),
        "story_phase": str(out.get("story_phase") or ""),
    })
    if dice_check is not None:
        state.roll_log.append(dice_check.as_dict())
    # beats 同步一份（KP 下一拍要读 known/unknown，通稿层要读全部字段）
    beats.append({
        "beat": beats_done + 1,
        "player": who,
        "pl_action": action,
        "pl_motivation": str(out.get("motivation") or ""),
        "pl_hypothesis": str(out.get("hypothesis") or ""),
        "pl_cost": str(out.get("cost") or ""),
        "scene": scene,
        # **存一行式判定文本，不是 dice_check.as_dict()**：
        # 通稿层要把它塞进给模型的素材里，dict 形态模型读不出「谁做了什么
        # 检定、结果如何」，只能看到一堆键名。
        "check": dice_check.one_line() if dice_check else "",
        "check_ok": dice_check.ok if dice_check else None,
        "effect": effect,
        "information_gained": str(out.get("information_gained") or ""),
        "world_state_change": str(out.get("world_state_change") or ""),
        "known_now": list(out.get("known_now") or []),
        "unknown_now": list(out.get("unknown_now") or []),
        "next_pressure": str(out.get("next_pressure") or ""),
        "story_phase": str(out.get("story_phase") or ""),
        "turn_no": turn.turn_no,
    })

    # story_phase / 空转计数
    phase = str(out.get("story_phase") or "").strip()
    if phase not in STORY_PHASES:
        phase = ""
    if phase:
        state.last_story_phase = phase
    state.consecutive_transition = (int(state.consecutive_transition or 0) + 1
                                    if phase == "转折点" else 0)
    info = str(out.get("information_gained") or "").strip()
    state.no_progress_streak = 0 if (info or scene) else int(
        getattr(state, "no_progress_streak", 0) or 0) + 1

    state.next_role = "keeper"
    if len(state.player_cards) > 1:
        state.advance_player()
    if on_event:
        on_event("coc_turn_done", {
            "role": "beat", "turn_no": turn.turn_no,
            "beat": beats_done + 1, "beat_budget": budget,
            "chars": len(narrative), "action": action,
            "check": dice_check.one_line() if dice_check else "",
            "check_ok": dice_check.ok if dice_check else None,
            "story_phase": state.last_story_phase or "展开中",
            "active_player": state.current_player_name(),
            "parsed": True,
        })
    return turn


def _render_pl_text(action: str, decl: dict) -> str:
    """把 PL 动作声明渲染成一行决策记录。"""
    parts = [f"【{action}】"] if action else []
    mot = str(decl.get("motivation") or "").strip()
    hyp = str(decl.get("hypothesis") or "").strip()
    cost = str(decl.get("cost") or "").strip()
    if mot:
        parts.append(f"（{mot}）")
    if hyp:
        parts.append(f"想验证：{hyp}")
    if cost:
        parts.append(f"代价：{cost}")
    return "\n".join(parts) if parts else action


def _append_storyboard_row(state: CocRunState, action: str, decl: dict) -> None:
    """把 PL 动作挂到最近一拍 KP 裁决上，凑成 storyboard 的一行分镜。

    storyboard 每行 = 一个完整 beat：PL 做了什么 → 判定结果 → 得到什么信息
    → 剧情怎么变 → 留下什么压力。这是这一层真正的产物。
    """
    if not action:
        return
    beats = getattr(state, "beats", None) or []
    if not beats:
        return
    last = beats[-1]
    # 判定结果的可读形式
    chk = last.get("check") or {}
    check_line = ""
    if chk:
        check_line = f"{chk.get('skill','?')} {chk.get('rolled','?')}/{chk.get('target','?')} → {chk.get('label', chk.get('result',''))}"
    last["pl_action"] = action
    last["pl_motivation"] = str(decl.get("motivation") or "")
    last["pl_hypothesis"] = str(decl.get("hypothesis") or "")
    last["pl_cost"] = str(decl.get("cost") or "")
    last["check_line"] = check_line
    # storyboard 是一份独立拷贝：beats 是内部状态，storyboard 是给作者看的产物
    sb = getattr(state, "storyboard", None)
    if sb is None:
        sb = []
        object.__setattr__(state, "storyboard", sb)
    sb.append({
        "beat": last.get("beat"),
        "player": chk.get("player_idx_name", "") if chk else "",
        "pl_action": action,
        "pl_motivation": last["pl_motivation"],
        "pl_hypothesis": last["pl_hypothesis"],
        "pl_cost": last["pl_cost"],
        "scene": last.get("scene", ""),
        "check": check_line,
        "check_ok": bool(chk.get("ok")) if chk else None,
        "information_gained": last.get("information_gained", ""),
        "world_state_change": last.get("world_state_change", ""),
        "next_pressure": last.get("next_pressure", ""),
        "story_phase": last.get("story_phase", ""),
    })


# ---- 主循环 ----

def autorun_until_break(runner, state: CocRunState, *, adapt_parsed, book_store,
                        run_store: CocRunStore, field_no: int,
                        max_steps: int | None = None,
                        user_directive: str | None = None,
                        on_event: Callable | None = None,
                        novelized: bool | None = None) -> dict:
    """自动交替执行 KP↔PL 直到终止条件或达到 beat 预算。
    返回 {"reason": ..., "turns_added": N, "state": state}。
    每回合结束都落盘一次，支持中断恢复。

    **单位（第二步）**：计数单位是 beat（一次 KP+PL 配对 = 一个 beat），
    预算来自 `state.beat_budget`。`max_steps` 作为**上限覆盖**保留——
    显式传了它就以它为准（作者/调用方想少跑几拍时用），没传就用自动估算。

    novelized: 若为 None 则取 state.novelized（默认 True）。
    """
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    state.novelized = novel

    if state.status in ("finalized", "aborted"):
        return {"reason": "already_" + state.status, "turns_added": 0, "state": state}
    state.status = "running"
    run_store.save(state)

    # beat 预算：显式覆盖 > 自动估算。
    #
    # **顺序很重要**：以前显式覆盖排在前面，前端滑块默认 6 会把预算
    # 钉死在 6~8 拍，于是每一场都以 hard_cap 收场——故事被砍断，
    # 作者拿到的是半截推演。自动估算（按 NPC 数 / 调查员数 / 模组体量
    # 推算）本该是主力，现在显式传参反而会压过它。
    #
    # 所以：**只在显式传值大于自动估算时才覆盖**。作者手动调小预算
    # 想省 token 的意图仍然有效，但默认值不再意外截断故事。
    estimated = estimate_beat_budget(adapt_parsed, state.player_cards)
    if max_steps:
        # 显式上限只是「不许超过作者给的数」，不作为「必须跑满这么多」
        requested = max(BEAT_MIN, min(BEAT_MAX, int(max_steps)))
        state.beat_budget = max(estimated, requested)
    else:
        state.beat_budget = max(estimated, int(getattr(state, "beat_budget", 0) or 0))

    # 续跑：beats_done 已经在 state 里（断点恢复），不归零
    steps_done = int(getattr(state, "beats_done", 0) or 0)
    reason = "paused"
    max_turns_cap = state.max_turns
    try:
        while True:
            term = _check_termination(state)
            if term:
                reason = term
                state.status = "awaiting_author"
                break
            # 第四步：小说化模式走合并拍（一次调用 = 一拍）。
            # 非小说化模式仍走旧的 KP→PL 两次调用（step_beat 内部会自动分流）。
            step_beat(runner, state, adapt_parsed=adapt_parsed,
                      user_directive=user_directive, on_event=on_event, novelized=novel)
            run_store.save(state)
            steps_done += 1
            state.beats_done = steps_done
    except Exception as e:
        state.status = "paused"
        state.error = f"{type(e).__name__}: {e}"
        run_store.save(state)
        return {"reason": "error", "turns_added": steps_done * 2, "state": state, "error": str(e)}

    # 终止说明：**必须区分「讲完了」和「被砍了」**（第二步核心）。
    # hard_cap 要明说「被截断、剧情未完成」——不能假装讲完了，
    # 否则作者会拿一个半截故事当创作底本。
    hints = {
        "story_complete": "故事已自然收束。",
        "transition_done": "核心转折已走完。",
        "dead": "调查员全员倒下，跑团以死亡结局告终。",
        "insane": "SAN 归零，调查员陷入临时疯狂。",
        "hard_cap": (f"本线在第 {steps_done} 拍被截断（beat 预算 {state.beat_budget} 已用尽），"
                     "剧情未完成——如需完整结局请加预算重跑。"),
        "max_turns": f"消息条数达到硬上限 {max_turns_cap}，跑团被截断，剧情未完成。",
        "no_progress": (f"连续 {int(getattr(state, 'no_progress_streak', 0) or 0)} 拍没有获得新信息，"
                        "判定为原地打转，跑团自动掐断。"),
    }
    if reason in hints:
        state.add_turn("system", f"自动循环停止：{hints[reason]} 作者可介入或直接 finalize。",
                       meta={"reason": reason, "beats": steps_done,
                             "beat_budget": state.beat_budget,
                             "story_phase": state.last_story_phase})
    run_store.save(state)
    return {"reason": reason, "turns_added": steps_done * 2, "state": state,
            "beats": steps_done, "beat_budget": state.beat_budget,
            "story_phase": state.last_story_phase}


# ---- 作者介入 / 状态修正 ----

def intervene(state: CocRunState, text: str, run_store: CocRunStore, *,
              as_role: str = "author") -> CocTurn:
    """作者强制插入一段内容（可以是改写 PL 行动、追加剧情设定、补扣 SAN 等）。"""
    if as_role == "investigator":
        turn = state.add_turn("investigator", text, meta={"source": "author"})
        state.next_role = "keeper"
    else:
        turn = state.add_turn("author", text, meta={})
    # 作者介入 = 「我知道了，重新来」。failed 必须和 paused 一起被恢复，
    # 否则失败场是死路：error 清了但 status 卡在 failed，作者补完设定
    # 也只能整批重跑，白白丢掉已经跑出来的那些回合。
    state.status = "running" if state.status in ("awaiting_author", "paused", "failed") else state.status
    state.error = None
    run_store.save(state)
    return turn


def apply_state_patch(state: CocRunState, patch: dict, run_store: CocRunStore) -> dict:
    """手动修正 SAN/HP/Day/clues/npcs 等字段，返回 applied 字典。
    多玩家模式下 patch 可传 players:[{idx, san, hp, mp}] 精确修某个玩家。"""
    applied = {}
    if "san" in patch and isinstance(patch["san"], int):
        state.san = max(0, min(99, patch["san"]))
        applied["san"] = state.san
    if "hp" in patch and isinstance(patch["hp"], int):
        state.hp = max(0, min(20, patch["hp"]))
        applied["hp"] = state.hp
    if "mp" in patch and isinstance(patch["mp"], int):
        state.mp = max(0, min(99, patch["mp"]))
        applied["mp"] = state.mp
    if "current_day" in patch and isinstance(patch["current_day"], int):
        state.current_day = max(1, min(7, patch["current_day"]))
        applied["current_day"] = state.current_day
    if "obsession_uses" in patch and isinstance(patch["obsession_uses"], int):
        state.obsession_uses = max(0, patch["obsession_uses"])
        applied["obsession_uses"] = state.obsession_uses
    if "max_turns" in patch and isinstance(patch["max_turns"], int):
        state.max_turns = max(1, patch["max_turns"])
        applied["max_turns"] = state.max_turns
    if "active_player_idx" in patch and isinstance(patch["active_player_idx"], int):
        if state.players_state:
            idx = max(0, min(len(state.players_state) - 1, patch["active_player_idx"]))
            state.active_player_idx = idx
            applied["active_player_idx"] = idx

    if "players" in patch and isinstance(patch["players"], list):
        plist = []
        for p in patch["players"]:
            if not isinstance(p, dict):
                continue
            idx = int(p.get("idx", 0))
            if 0 <= idx < len(state.players_state):
                ps = state.players_state[idx]
                for k in ("san", "hp", "mp"):
                    if k in p and isinstance(p[k], int):
                        ps[k] = max(0, int(p[k]))
                ps["insane"] = ps.get("san", 0) <= 0
                ps["alive"] = ps.get("hp", 0) > 0 and ps.get("san", 0) > 0
                plist.append({"idx": idx, "san": ps["san"], "hp": ps["hp"], "mp": ps["mp"]})
        applied["players"] = plist

    if "npcs" in patch and isinstance(patch["npcs"], dict):
        state.npcs.update(patch["npcs"])
        applied["npcs"] = list(patch["npcs"].keys())
    if "clue" in patch and isinstance(patch["clue"], str):
        clue = {"day": state.current_day, "text": patch["clue"], "turn_no": len(state.turns)}
        state.clues_found.append(clue)
        applied["clue_added"] = clue
    if "cost_note" in patch and isinstance(patch["cost_note"], str):
        state.obsession_cost_notes.append(patch["cost_note"])
        applied["cost_note_added"] = patch["cost_note"]
    state.add_turn("system", f"作者手动修正状态：{json.dumps(applied, ensure_ascii=False)}",
                   meta={"patch": applied})
    run_store.save(state)
    return applied


# ---- day_breakdown 解析 ----
#
# 幕数不设上限：梗概写几幕，这里就解析几幕。
# 旧实现用 [1-7] 正则 + range(1,8) 补位，把结果硬裁成 7 段——
# 梗概写到第 12 幕，第 8 段之后的内容会被直接丢掉，页面上只看得到前 7 幕。
_DAY_HEADER_RE = re.compile(
    r"(?:Day\s*(\d{1,3})|第\s*(\d{1,3}|[一二三四五六七八九十]+)\s*(?:天|日|幕|章))"
    r"[^\n：:]{0,20}[：:\n]"
)
_CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}


def _parse_day_breakdown(summary_raw: str) -> list[dict]:
    """从 finalize/coc_outcome 产物里抽出各幕的分段摘要（幕数不限）。"""
    text = (summary_raw or "").strip()
    if not text:
        return []
    days: list[dict] = []
    matches = list(_DAY_HEADER_RE.finditer(text))
    if matches:
        for i, m in enumerate(matches):
            a = m.group(1) or m.group(2)
            try:
                dnum = int(a)
            except Exception:
                dnum = _CN_NUM.get(a, i + 1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            first_para = re.split(r"\n{2,}", body)[0].strip()
            first_para = first_para.replace("\n", " ")
            if len(first_para) > 120:
                first_para = first_para[:118] + "…"
            if first_para:
                days.append({"day": dnum, "summary": first_para})
    if not days:
        paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        per = max(1, len(paras))
        chunk = max(1, (len(paras) + per - 1) // per)
        for d in range(1, len(paras) + 1):
            seg = paras[(d - 1) * chunk:d * chunk]
            if not seg:
                break
            s = "".join(seg)[:120]
            days.append({"day": d, "summary": s})
    seen = set()
    out = []
    for d in days:
        dn = int(d.get("day", 0))
        if dn >= 1 and dn not in seen:
            seen.add(dn)
            out.append({"day": dn, "summary": d.get("summary", "")})
    out.sort(key=lambda x: x["day"])
    return out


def _collect_survivors(state: CocRunState) -> list[str]:
    """从 players_state 里找出仍存活且未疯狂的玩家名字。"""
    ps = state.players_state
    if not ps:
        return [] if state.hp <= 0 or state.san <= 0 else [state.player_card.get("name", "调查员")]
    return [p.get("name", f"PL{i}") for i, p in enumerate(ps)
            if p.get("alive", True) and not p.get("insane", False) and p.get("hp", 0) > 0]


# ---- finalize：把完整日志喂给 coc_outcome 层压成真相 ----

def finalize(runner, state: CocRunState, run_store: CocRunStore, field_run_store,
             *, field_no: int, adapt_parsed, book_store,
             on_event: Callable | None = None,
             novelized: bool | None = None) -> NodeArtifact:
    """结束跑团，调用 coc_outcome 层产出分段真相摘要（最多 7 段，段号 DayN 只是输出格式的阶段序号，非日历天），存为 NodeArtifact。"""
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    transcript = state.transcript()
    survivors = _collect_survivors(state)
    players_desc = "、".join(
        f"{p.get('name','?')}（SAN{p.get('san','?')}/HP{p.get('hp','?')}）"
        for p in state.players_state
    ) if state.players_state else f"调查员 SAN{state.san}/HP{state.hp}"

    if novel:
        task = SYNOPSIS_TASK_NOVEL
    else:
        task = SYNOPSIS_TASK_PLAIN

    x = (
        f"# 选定模组方案\n\n{json.dumps(adapt_parsed, ensure_ascii=False, indent=2)}\n\n"
        f"# 本场差异化设定\n\n{render_variant_seed(state.variant_seed or {})}\n\n"
        f"# 调查员\n\n{players_desc}\n\n"
        f"# 幸存者：{'、'.join(survivors) if survivors else '无人生还'}\n\n"
        f"# 跑团完整日志（{len(state.turns)} 回合）\n\n{transcript}\n\n"
        f"# 最终状态\n\n"
        f"结局类型：{_check_termination(state) or 'stopped_by_author'}\n"
        f"已发现线索：{len(state.clues_found)} 条\n\n"
        f"{task}"
        f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )
    mats: list[Material] = []
    if book_store is not None:
        mats.append(Material(
            source_id="book:hardcards", family=MaterialFamily.REQUIRED,
            content=book_store.render_for_module_run(),
            title="书级硬卡（原剧本阶段：已剥离唐迟/魂穿/七天倒计时）",
        ))
    if on_event:
        on_event("coc_finalize_start", {"turns": len(state.turns)})
    spec = SPECS["coc_outcome"]
    result = runner.run_layer(spec, [(x, "")], materials=mats, on_event=on_event)
    raw = result.y

    from app.pipeline.field_pipeline import _try_parse_json
    art = field_run_store.get(field_no, "coc_outcome") or NodeArtifact(
        node_id="coc_outcome", layer="coc_outcome")
    art.raw_text = raw
    art.parsed = _try_parse_json(raw)
    art.variants = []
    art.chosen_vid = None
    art.meta = {
        "source": "coc_simulation", "turns": len(state.turns),
        "final_san": state.san, "final_hp": state.hp,
        "ending": _check_termination(state) or "stopped_by_author",
        "survivors": survivors,
        "novelized": novel,
        "players_count": len(state.player_cards),
    }
    field_run_store.save(field_no, art)

    state.final_summary = raw
    state.final_parsed = art.parsed
    state.final_day_breakdown = _parse_day_breakdown(raw)
    state.survivors = survivors
    # 通稿层（第二步）：final_summary 保持原格式不动，通稿落在独立字段。
    # 放在 save 之前，保证 state 落盘时两个字段同时到位。
    build_full_narrative(runner, state, adapt_parsed, book_store, on_event=on_event)
    state.status = "finalized"
    state.add_turn("system", "跑团已 finalize，真相摘要已写入 coc_outcome 节点。",
                   meta={"ending": art.meta["ending"]})
    run_store.save(state)
    if on_event:
        on_event("coc_finalize_done", {
            "chars": len(raw), "ending": art.meta["ending"], "survivors": survivors,
        })
    return art


# ---- 初始化 ----

def init_run(run_store: CocRunStore, field_no: int,
             adapt_parsed: dict,
             player_card_patch: dict | None = None,
             *,
             player_cards: list[dict] | None = None,
             san: int | None = None, hp: int | None = None,
             max_turns: int = 40, variant_seed: dict | None = None,
             session_index: int = 1,
             novelized: bool = True) -> CocRunState:
    """根据选定的 adapt_scenario 创建/重置跑团状态。

    参数：
    - player_card_patch: [向后兼容] 旧单卡补丁；若传则构造单玩家（唐迟）模式。
    - player_cards: 多玩家卡列表（推荐）；若传 player_card_patch 则忽略此字段。
    - novelized: 是否使用中等小说化笔调，默认 True。
    """
    state = CocRunState(field_no=field_no)
    state.session_index = session_index
    state.novelized = novelized
    state.adapt_vid = str(adapt_parsed.get("vid") or "")
    state.setup_meta = {
        "title": adapt_parsed.get("title", ""),
        "world_premise": adapt_parsed.get("world_premise", ""),
        "hook": adapt_parsed.get("hook", ""),
        "obsession_power": adapt_parsed.get("obsession_power", ""),
        "obsession_cost": adapt_parsed.get("obsession_cost", ""),
    }

    if player_card_patch is not None:
        pc = dict(DEFAULT_PLAYER_CARD)
        pc.update(player_card_patch)
        cards = [pc]
    elif player_cards is not None:
        cards = _normalize_player_cards(player_cards)
    else:
        cards = [dict(DEFAULT_PLAYER_CARD)]
    state.player_cards = cards

    seed = variant_seed or generate_variant_seed()
    state.variant_seed = seed
    san_penalty = int(seed.get("san_start_penalty", 0) or 0) if san is None else 0

    san_ov: dict[int, int] = {}
    hp_ov: dict[int, int] = {}
    if san is not None:
        san_ov[0] = int(san)
    if hp is not None:
        hp_ov[0] = int(hp)
    state.players_state = _init_players_state(
        cards,
        san_overrides=san_ov if san_ov else None,
        hp_overrides=hp_ov if hp_ov else None,
        san_start_penalty=san_penalty,
    )
    state.active_player_idx = 0

    state.obsession_uses = int(seed.get("obsession_start_uses", 0) or 0)
    state.max_turns = max_turns
    state.next_role = "keeper"
    state.status = "pending"
    state.turns = []
    # beat 追踪（第二步）。预算在这里就估好，autorun 里只在缺省时补算。
    state.beats = []
    state.beats_done = 0
    state.beat_budget = estimate_beat_budget(adapt_parsed, cards)
    state.last_story_phase = ""
    state.consecutive_transition = 0
    state.clues_found = []
    state.obsession_cost_notes = []
    state.final_summary = ""
    state.final_parsed = None
    state.final_day_breakdown = []
    state.survivors = []
    state.error = None
    state.current_day = 1
    run_store.save(state)
    return state


# ============================================================
# 批量仿真：N 场独立跑团 → 压真相 → 返回 variant 列表（旧版顺序版本，保留）
# ============================================================

def _ending_reason_for_card(state: CocRunState) -> str:
    """卡片用的终止原因：区分「讲完了」与「被截断了」。

    作者选底本时必须知道这一场是自然收束还是撞预算截断——
    hard_cap 的场子里后半段是没写完的，直接当底本会漏情节。
    """
    reason = _check_termination(state, last_phase=getattr(state, "last_story_phase", "") or None)
    if reason:
        return reason
    if int(getattr(state, "beats_done", 0) or 0) <= 0:
        return "no_beat"
    return "stopped_by_author"


def _summary_card_from_state(state: CocRunState, summary_raw: str, turns_used: int) -> dict:
    """把跑完的 session 压成一个可选 variant 的摘要卡。"""
    seed = state.variant_seed or {}
    # one_liner：优先取 final_summary 的第一句（中文。/！/？为句末），
    # 失败则退化成结局标签 + 幸存者数。避免空串或 "..."。
    text = (summary_raw or "").strip()
    one_liner = ""
    if text:
        # 去掉 markdown 标题行 / 前缀注释，取第一段非空内容的第一句
        import re as _re
        cleaned = _re.sub(r"^\s*#.*$", "", text, flags=_re.M).strip()
        m = _re.search(r"[^。！？!?\n]{6,120}[。！？!?]", cleaned)
        one_liner = (m.group(0) if m else cleaned.split("\n", 1)[0])[:120]
    if not one_liner:
        surv_n = len(state.survivors or [])
        one_liner = f"{seed.get('ending_label','未知结局')}；幸存者 {surv_n} 人"
    # full_log：完整 KP/PL 对话日志 + 小说化梗概（压真相结果），供前端直接展示
    transcript_text = state.transcript() or ""
    full_log_parts = []
    if transcript_text:
        full_log_parts.append("===== 跑团完整日志（守密人 / 调查员对话）=====\n")
        full_log_parts.append(transcript_text)
        full_log_parts.append("")
    full_log_parts.append("===== 小说化梗概（原剧本经过）=====\n")
    full_log_parts.append(text or "（本场未能生成梗概）")
    # 通稿层（第二步）：单独一段，前端按 full_narrative 字段取，不与梗概混排
    if state.full_narrative:
        full_log_parts.append("")
        full_log_parts.append("===== 通稿（本场完整故事）=====\n")
        full_log_parts.append(state.full_narrative)
    if state.error:
        full_log_parts.append("")
        full_log_parts.append(f"===== 调试信息 =====\nstate.error: {state.error}")
    full_log = "\n".join(full_log_parts)

    # label/title：用 seed 里的 ending_label，不再由调用方硬覆盖成预设名。
    #
    # **兜底不能用「第 N 场」**：这是给作者看的线名，掉回「第 3 场」等于
    # 三栏在页面上没法区分（真实事故：LLM 种子缺失时第三场就退化成了
    # 毫无辨识度的「第 3 场」）。改成「平行宇宙 #N」——即使没有个性化
    # 线名，三栏至少还能一一对应。
    ending_label = seed.get("ending_label") or f"平行宇宙 #{state.session_index}"
    title = f"候选 #{state.session_index}：{ending_label}"
    # run_status 是**独立字段**，不靠 final_summary 有没有内容来判断成败。
    # 这是本次修复的核心：成败不该是内容的副产品，否则前端只能靠
    # 「正文是不是一段报错」来猜，而那恰恰是旧实现制造的问题。
    failed = bool(state.error) or state.status == "failed"
    # fail_stage：从最后一条 system turn 的 meta 里取（压真相失败会写它）
    fail_stage = ""
    for t in reversed(state.turns or []):
        meta = getattr(t, "meta", None) or {}
        if meta.get("failed_stage"):
            fail_stage = str(meta["failed_stage"])
            break
    return {
        "vid": str(state.session_index),
        "title": title,
        "label": ending_label,
        "run_status": "failed" if failed else "ok",
        "fail_stage": fail_stage,
        "fail_reason": (state.error or "") if failed else "",
        "one_liner": one_liner,
        "full_log": full_log,
        "ending_bias": seed.get("ending_bias", ""),
        "ending_label": ending_label,
        "entrance_hook": seed.get("entrance_hook", ""),
        "tangchi_mood": seed.get("tangchi_mood", ""),
        "extra_npcs": [n.get("name", "") for n in seed.get("extra_npcs", [])],
        "core_twist": seed.get("core_twist", ""),
        "planted_foreshadows": seed.get("planted_foreshadows", []),
        "final_san": state.san,
        "final_hp": state.hp,
        "survivors": state.survivors or _collect_survivors(state),
        "day_breakdown": state.final_day_breakdown or _parse_day_breakdown(summary_raw),
        "players_count": len(state.player_cards),
        "turns": turns_used,
        # ---- beat 引擎（第二步）：作者看到的语义进度，不是「跑了几回合」 ----
        # beats_done / beat_budget 让作者判断「这场的故事讲够长度了吗」；
        # story_phase 让作者知道 KP 觉得故事走到哪一段了。
        # ending_reason 区分「讲完了」和「被截断了」——这两者对底本的价值完全不同。
        "beats_done": int(getattr(state, "beats_done", 0) or 0),
        "beat_budget": int(getattr(state, "beat_budget", 0) or 0),
        "story_phase": getattr(state, "last_story_phase", "") or "展开中",
        "ending_reason": _ending_reason_for_card(state),
        # 决策链分镜表（第四步）：这一层真正的产物。
        # 每拍一行：角色做了什么 → 判定结果 → 得到什么信息 → 剧情怎么变 → 留下什么压力。
        "storyboard": list(getattr(state, "storyboard", None) or []),
        "roll_log": list(getattr(state, "roll_log", None) or []),
        "final_summary": summary_raw,
        "full_narrative": state.full_narrative or "",
        "transcript": transcript_text,
        "state_error": state.error or "",
        # flavor：留空表示全随机；角标由前端按 ending_bias 着色
        "flavor": seed.get("ending_bias", ""),
    }


def run_coc_simulation_batch(
    runner,
    *,
    book_store,
    field_store,
    field_no: int,
    coc_store: CocRunStore,
    field_run_store,
    adapt_parsed: dict,
    n_sessions: int = 5,
    max_pairs_per_session: int = 6,
    max_turns_per_session: int = 24,
    on_event: Callable | None = None,
    user_directive: str | None = None,
    novelized: bool = True,
) -> list[dict]:
    """顺序跑 N 场多轮仿真（保留旧接口，新增 novelized 参数默认 True）。"""
    batch_root = coc_store.root / "_batch"
    batch_root.mkdir(parents=True, exist_ok=True)

    variants: list[dict] = []
    for idx in range(1, n_sessions + 1):
        if on_event:
            on_event("coc_session_start", {
                "session": idx, "total": n_sessions, "total_days": 7,
            })
        rng = random.Random(f"{field_no}-{idx}-{time.time_ns()}")
        seed = generate_variant_seed(rng)
        session_store = CocRunStore(batch_root)
        virtual_field = field_no * 1000 + idx
        if session_store.exists(virtual_field):
            session_store.delete(virtual_field)
        state = init_run(
            session_store, virtual_field, adapt_parsed,
            max_turns=max_turns_per_session,
            variant_seed=seed, session_index=idx,
            novelized=novelized,
        )
        try:
            # thinking / delta **原样透传**，不要改名成 coc_session_turn——
            # 前端靠 kind 区分「进度事件」与「模型实时输出」，改名就收不到了。
            def _sess_on_event(kind: str, payload: dict, i: int = idx) -> None:
                if on_event is None:
                    return
                if kind in ("thinking", "delta"):
                    on_event(kind, {"session": i, **payload})
                else:
                    on_event("coc_session_turn", {"session": i, **payload})

            autorun_until_break(
                runner, state, adapt_parsed=adapt_parsed, book_store=book_store,
                run_store=session_store, field_no=virtual_field,
                max_steps=max_pairs_per_session, user_directive=user_directive,
                novelized=novelized,
                on_event=_sess_on_event if on_event else None,
            )
        except Exception as e:
            state.add_turn("system", f"仿真异常：{type(e).__name__}: {e}",
                           meta={"error": str(e)})
            if on_event:
                on_event("coc_session_error", {"session": idx, "error": str(e)})

        transcript = state.transcript()
        survivors = _collect_survivors(state)
        players_desc = "、".join(p.get("name", "?") for p in state.players_state) if state.players_state else "调查员"
        x = (
            f"# 选定模组方案\n\n{json.dumps(adapt_parsed, ensure_ascii=False, indent=2)}\n\n"
            f"# 本场差异化设定\n\n{render_variant_seed(seed)}\n\n"
            f"# 调查员：{players_desc}\n\n"
            f"# 幸存者：{'、'.join(survivors) if survivors else '无人生还'}\n\n"
            f"# 跑团完整日志（{len(state.turns)} 回合）\n\n{transcript}\n\n"
            f"# 最终状态\n\n"
            f"结局倾向：{seed.get('ending_label','')}\n"
            f"已发现线索：{len(state.clues_found)} 条\n\n"
            f"{SYNOPSIS_TASK_NOVEL}"
            f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
        mats: list[Material] = []
        if book_store is not None:
            mats.append(Material(
                source_id="book:hardcards", family=MaterialFamily.REQUIRED,
                content=book_store.render_for_module_run(),
                title="书级硬卡（原剧本阶段：已剥离唐迟/魂穿/七天倒计时）",
            ))
        try:
            spec = SPECS["coc_outcome"]
            result = runner.run_layer(spec, [(x, "")], materials=mats,
                                      on_event=(lambda kind, payload, i=idx: on_event(
                                          "coc_session_finalize", {"session": i, **payload}
                                      )) if on_event else None)
            raw = result.y
        except Exception as e:
            raw = f"（本场压真相失败：{type(e).__name__}: {e}）"

        state.final_summary = raw
        state.final_day_breakdown = _parse_day_breakdown(raw)
        state.survivors = survivors
        # 通稿层（第二步）：批量候选也产出通稿，让前端能直接展示
        try:
            build_full_narrative(
                runner, state, adapt_parsed, book_store,
                on_event=(lambda kind, payload, i=idx: on_event(
                    "coc_session_narrative", {"session": i, **payload}
                )) if on_event else None,
            )
        except Exception:
            state.full_narrative = _fallback_narrative(state)
        card = _summary_card_from_state(state, raw, len(state.turns))
        variants.append(card)
        if on_event:
            on_event("coc_session_done", {
                "session": idx, "total": n_sessions,
                "vid": card["vid"], "turns": card["turns"],
                "final_san": card["final_san"], "final_hp": card["final_hp"],
                "survivors": survivors,
            })
    return variants


# ============================================================
# 2 同 PL + 1 异 PL 配置构造
# ============================================================

def build_2same_1different_configs(
    player_cards_from_pdf: list[dict],
    tangchi_replace_idx: int | None = None,
) -> tuple[list[list[dict]], list[dict]]:
    """构造"2 同 PL + 1 异 PL"三场并行跑团配置。

    返回 (player_sets, variant_configs)，每个长度均为 3：
      - player_sets[0]: 与 PDF 全集相同（good luck 种子）
      - player_sets[1]: 与 PDF 全集相同（bad luck / brutal 种子）
      - player_sets[2]: 洗牌/换身份的一组（distinct 种子）
        * 在 PDF 卡基础上对 1-2 个关键角色的身份/技能做替换
        * 若 tangchi_replace_idx 指定（0-based），把该位置角色的 name 改成"唐迟"，
          occupation 保留——这样跑出来的日志里唐迟会以该角色身份行动

    variant_configs[i] 为 dict，包含 flavor/label/desc。
    """
    import copy as _copy
    base_cards = _normalize_player_cards(player_cards_from_pdf)
    if not base_cards:
        base_cards = [dict(DEFAULT_PLAYER_CARD)]
    if len(base_cards) < 2:
        extra = make_player_card(f"同行者{len(base_cards)+1}", "外地记者",
                                 san_max=65, skills={"侦查": 55, "图书馆": 60})
        base_cards.append(extra)
    if len(base_cards) > 4:
        base_cards = base_cards[:4]

    def _maybe_tangchi(cards: list[dict]) -> list[dict]:
        out = _copy.deepcopy(cards)
        if tangchi_replace_idx is not None and 0 <= tangchi_replace_idx < len(out):
            out[tangchi_replace_idx]["name"] = "唐迟"
        return out

    set_same_good = _maybe_tangchi(base_cards)
    set_same_bad = _maybe_tangchi(base_cards)

    set_diff = _copy.deepcopy(base_cards)
    n_diff = min(2, len(set_diff))
    swap_candidates = list(range(len(set_diff)))
    swap_candidates.reverse()
    swap_positions = swap_candidates[:n_diff]

    ALTER_OCCUPATIONS = [
        ("走方郎中", {"急救": 60, "医术": 50, "侦查": 35}),
        ("小报记者", {"侦查": 60, "话术": 55, "图书馆": 50}),
        ("退伍兵", {"斗殴": 65, "射击": 55, "侦查": 40}),
        ("古董商", {"鉴定": 60, "神秘学": 50, "图书馆": 55}),
        ("神婆", {"神秘学": 60, "意志": 55, "聆听": 45}),
        ("码头搬运工", {"斗殴": 60, "力量": 65, "聆听": 40}),
    ]
    for pos in swap_positions:
        occ, sk = ALTER_OCCUPATIONS[(pos + len(set_diff)) % len(ALTER_OCCUPATIONS)]
        set_diff[pos]["occupation"] = occ
        merged_sk = dict(set_diff[pos].get("skills") or {})
        merged_sk.update(sk)
        set_diff[pos]["skills"] = merged_sk
        set_diff[pos]["_altered"] = True
    set_diff = _maybe_tangchi(set_diff)

    player_sets = [set_same_good, set_same_bad, set_diff]
    variant_configs = [
        {"flavor": "good",      "label": "好运线",   "desc": "同一组 PL，运气偏向顺利，代价较轻"},
        {"flavor": "bad",       "label": "残酷线",   "desc": "同一组 PL，厄运连连，代价沉重"},
        {"flavor": "distinct",  "label": "身份洗牌线","desc": "1-2 个角色身份/技能被替换，走出不同的破局路径"},
    ]
    return player_sets, variant_configs


# ============================================================
# 并行批量：3 场独立跑团（同/异 PL 配置）
# ============================================================

def _finalize_session_inline(runner, state: CocRunState, adapt_parsed, book_store) -> str:
    """独立会话内存版 finalize：不写正式 field_run_store，只返回 raw summary 文本。
    同时把 day_breakdown / survivors 写回 state。
    """
    transcript = state.transcript()
    survivors = _collect_survivors(state)
    players_desc = "、".join(
        f"{p.get('name','?')}（{state.player_cards[i].get('occupation','')}，"
        f"SAN{p.get('san','?')}/HP{p.get('hp','?')}）"
        for i, p in enumerate(state.players_state)
    ) if state.players_state else "调查员"
    x = (
        f"# 选定模组方案\n\n{json.dumps(adapt_parsed, ensure_ascii=False, indent=2)}\n\n"
        f"# 本场差异化设定\n\n{render_variant_seed(state.variant_seed or {})}\n\n"
        f"# 调查员：{players_desc}\n\n"
        f"# 幸存者：{'、'.join(survivors) if survivors else '无人生还'}\n\n"
        f"# 跑团完整日志（{len(state.turns)} 回合）\n\n{transcript}\n\n"
        f"# 最终状态\n\n"
        f"结局倾向：{(state.variant_seed or {}).get('ending_label','')}\n"
        f"已发现线索：{len(state.clues_found)} 条\n\n"
        f"{SYNOPSIS_TASK_NOVEL}"
        f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
    )
    mats: list[Material] = []
    if book_store is not None:
        mats.append(Material(
            source_id="book:hardcards", family=MaterialFamily.REQUIRED,
            content=book_store.render_for_module_run(),
            title="书级硬卡（原剧本阶段：已剥离唐迟/魂穿/七天倒计时）",
        ))
    spec = SPECS["coc_outcome"]
    result = runner.run_layer(spec, [(x, "")], materials=mats, on_event=None)
    raw = result.y
    # 从梗概里解析【结局标签：XXX】回写到 state.variant_seed，前端卡片会优先展示这个个性化标签
    #
    # **必须用编译好的 Pattern 而不是 re 模块**：`count` 是 Pattern.sub 的
    # 参数，模块级 re.sub 不接受它。写成 _re_label.sub("", raw, count=1)
    # 会被解释成位置参数不足，抛
    # TypeError: sub() missing 1 required positional argument: 'string'
    # ——整个 finalize 阶段炸掉，三场全挂（真实事故）。
    _ENDING_LABEL_RE = re.compile(r"【结局标签[:：]\s*([^】\n]{2,30})】")
    _m = _ENDING_LABEL_RE.search(raw or "")
    if _m:
        new_label = _m.group(1).strip()
        seed = state.variant_seed or {}
        seed["ending_label"] = new_label
        state.variant_seed = seed
        # 顺手把标签行从梗概正文里去掉，保持 final_summary 正文干净
        raw = _ENDING_LABEL_RE.sub("", raw, count=1).strip()
    state.final_summary = raw
    state.final_day_breakdown = _parse_day_breakdown(raw)
    state.survivors = survivors
    # 通稿层（第二步）：独立字段，不动 final_summary
    try:
        build_full_narrative(runner, state, adapt_parsed, book_store)
    except Exception:
        # 双保险：_finalize_session_inline 本身不因增强层失败而炸
        state.full_narrative = _fallback_narrative(state)
    return raw


def _emit(on_event, kind, payload):
    if on_event:
        try:
            on_event(kind, payload)
        except Exception:
            pass


def _run_one_session(
    idx: int,
    total_sessions: int,
    runner_factory,
    *,
    book_store,
    coc_store: CocRunStore,
    field_no: int,
    adapt_parsed: dict,
    player_cards: list[dict],
    player_meta: dict | None = None,
    variant_config: dict,
    max_pairs_per_session: int,
    max_turns_per_session: int,
    on_event: Callable | None,
    user_directive: str | None,
) -> dict:
    """单个 session 的执行体（线程入口）。"""
    session_start = time.time()
    batch_root = coc_store.root / "_batch"
    batch_root.mkdir(parents=True, exist_ok=True)

    _emit(on_event, "coc_session_start", {
        "session": idx, "total": total_sessions,
        "total_days": 7, "day": 1, "turn": 0, "max_turns": max_turns_per_session,
        "percent": 0,
        "message": f"第{idx}场启动：{variant_config.get('label','')}",
    })

    runner = runner_factory()
    session_store = CocRunStore(batch_root)
    virtual_field = field_no * 10000 + (int(time.time_ns() * (idx + 7)) % 1000000) + idx
    if session_store.exists(virtual_field):
        session_store.delete(virtual_field)

    _cfg_seed = variant_config.get("rng_seed")
    rng = random.Random(_cfg_seed if _cfg_seed is not None else f"{field_no}-parallel-{idx}-{time.time_ns()}")
    flavor = variant_config.get("flavor")
    seed = generate_variant_seed(rng, flavor=flavor)
    # 只覆写显式指定的字段（core_twist / ending_label / ending_bias / entrance_hook），
    # flavor=None 时 ending_label 保留 generate_variant_seed 给出的默认值，finalize 阶段 KP 会产出更贴合故事的标签回写。
    for k in ("core_twist", "entrance_hook"):
        if k in variant_config:
            seed[k] = variant_config[k]
    # 如果 variant_config 显式传了 ending_label/ending_bias（例如 mock 或作者指定）才覆盖
    if variant_config.get("ending_label"):
        seed["ending_label"] = variant_config["ending_label"]
    if variant_config.get("ending_bias"):
        seed["ending_bias"] = variant_config["ending_bias"]
    # 方案 B：LLM 生成的差异化设定**整体覆盖**剧情素材字段。
    #
    # 只在 seed 非空（有真实 LLM 产出）时覆盖——兜底 seed 全是空串，
    # 覆盖等于把剧本自己的设定清空。机制参数（san/obession）保留随机值。
    llm_seed = variant_config.get("llm_seed")
    if isinstance(llm_seed, dict) and llm_seed.get("source") == "llm":
        for k in ("core_twist", "entrance_hook", "ending_direction",
                  "ending_label", "tangchi_mood", "planted_foreshadows"):
            v = llm_seed.get(k)
            if v:
                seed[k] = v
        # 结局倾向文本：LLM 给的 direction 优先；label 只作展示用短标签
        if llm_seed.get("ending_direction"):
            seed["ending_direction"] = llm_seed["ending_direction"]
        # LLM 没给 bias 枚举时清掉旧的硬编码 bias，避免前端按旧枚举着色
        seed["ending_bias"] = ""
        seed["source"] = "llm"
    else:
        # 没有 LLM seed（兜底）：清掉硬编码结局标签，绝不把
        #「黑暗结局：巷子里多了一把伞」这种剧本外素材喂给 KP。
        seed["ending_label"] = ""
        seed["ending_direction"] = ""
        seed["ending_bias"] = ""
        seed["source"] = "fallback"
    # 把本场 PL 组合元信息（team_composition_note + 个人卷入理由/秘密）塞到 seed 里，
    # 之后 KP/PL 的 x_block 会读取并使用
    if player_meta:
        seed["player_meta"] = player_meta

    state = init_run(
        session_store, virtual_field, adapt_parsed,
        player_cards=player_cards,
        max_turns=max_turns_per_session,
        variant_seed=seed, session_index=idx,
        novelized=True,
    )

    def turn_cb(kind, payload):
        if not on_event:
            return
        # **流式事件原样透传**（不要改名、不要吞掉）：thinking 是模型此刻的
        # 推理，delta 是正在吐的字。前端靠 kind 区分「进度」与「实时输出」，
        # 这里改了名字上游就再也收不到——作者只能盯着「开场中」干等。
        if kind in ("thinking", "delta", "notice"):
            _emit(on_event, kind, {"session": idx, **payload})
            return
        turn_no = len(state.turns) + (1 if kind == "coc_turn_start" else 0)
        percent = int(min(99, turn_no / max(1, max_turns_per_session) * 100))
        day = state.current_day
        if kind == "coc_turn_done":
            text = ""
            if state.turns:
                last = state.turns[-1]
                text = _build_turn_message(state, last.role, last.turn_no, last.text)
            _emit(on_event, "coc_session_turn", {
                "session": idx, "total": total_sessions,
                "day": day, "total_days": 7,
                "turn": turn_no, "max_turns": max_turns_per_session,
                "percent": percent,
                "message": text or f"第{idx}场·Day{day}·回合{turn_no}",
                "role": payload.get("role", ""),
            })

    autorun_result = None
    autorun_err = None
    try:
        autorun_result = autorun_until_break(
            runner, state, adapt_parsed=adapt_parsed, book_store=book_store,
            run_store=session_store, field_no=virtual_field,
            max_steps=max_pairs_per_session, user_directive=user_directive,
            on_event=turn_cb, novelized=True,
        )
    except Exception as e:
        autorun_err = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        state.add_turn("system", f"仿真异常：{type(e).__name__}: {e}",
                       meta={"error": str(e), "traceback": autorun_err})
        _emit(on_event, "coc_session_error", {
            "session": idx, "total": total_sessions,
            "day": state.current_day, "total_days": 7,
            "turn": len(state.turns), "max_turns": max_turns_per_session,
            "error": str(e),
            "message": f"第{idx}场出错：{type(e).__name__}",
        })

    # autorun_until_break 内部已经捕获异常并返回 reason="error"；它把错误写进 state.error，
    # 但不会 raise，外层 try/except 不会进。所以这里再兜底检查一次。
    if autorun_result is None:
        autorun_result = {"reason": "exception", "turns_added": len(state.turns)}
    if state.error and autorun_result.get("reason") == "error" and len(state.turns) == 0:
        # 一回合都没跑出来：走failed 状态，**不要往 final_summary 写错误文本**。
        # 旧代码写进 final_summary，前端拿它当卡片正文，于是作者看到的是
        # 「一份有内容的梗概」，内容是一句报错——失败被伪装成了成功。
        state.status = "failed"
        state.final_summary = ""
        state.final_day_breakdown = []
        _emit(on_event, "coc_session_error", {
            "session": idx, "total": total_sessions,
            "day": state.current_day, "total_days": 7,
            "turn": 0, "max_turns": max_turns_per_session,
            "error": state.error,
            "failed_stage": "bootstrap",
            "message": f"第{idx}场仿真 0 回合退出：{state.error}",
        })

    _emit(on_event, "coc_session_finalize", {
        "session": idx, "total": total_sessions,
        "day": state.current_day, "turn": len(state.turns),
    })

    try:
        raw = _finalize_session_inline(runner, state, adapt_parsed, book_store)
    except Exception as e:
        # 压真相失败：跑团本身可能有内容，但**梗概拿不到**。
        # 错误文本一律进 state.error / state.status，绝不写进 final_summary——
        # 旧代码把 traceback 写进 final_summary，前端拿它当卡片正文，
        # 于是作者看到「一份有内容的梗概」，实际内容是报错。
        tb = traceback.format_exc()
        raw = ""
        state.status = "failed"
        state.error = f"{type(e).__name__}: {e}"
        state.final_summary = ""
        state.final_day_breakdown = []
        state.survivors = _collect_survivors(state)
        # traceback 留在 turn 日志里（可展开查看），但不进卡片正文
        state.add_turn("system", f"压真相失败：{type(e).__name__}: {e}",
                       meta={"error": str(e), "traceback": tb,
                             "failed_stage": "finalize"})
        _emit(on_event, "coc_session_error", {
            "session": idx, "total": total_sessions,
            "day": state.current_day, "total_days": 7,
            "turn": len(state.turns), "max_turns": max_turns_per_session,
            "error": str(e),
            "failed_stage": "finalize",
            "message": f"第{idx}场压真相失败：{type(e).__name__}",
        })

    session_store.save(state)
    card = _summary_card_from_state(state, raw, len(state.turns))
    # 不再覆盖 card["label"] / card["flavor"]：KP 在 finalize 阶段已经把个性化
    # ending_label 回写到 state.variant_seed；fallback 到 variant_config 的平行宇宙标签，
    # 只有在卡片本身 label 为空（极端情况）时才兜底。
    if not card.get("label"):
        card["label"] = variant_config.get("label", f"第{idx}场")
    if not card.get("flavor"):
        card["flavor"] = variant_config.get("flavor", "") or flavor or ""
    card["desc"] = variant_config.get("desc", "")
    card["elapsed_sec"] = round(time.time() - session_start, 2)
    # 方便调试：把 autorun 的终止原因/回合数/错误写到 card.meta
    card["autorun_reason"] = autorun_result.get("reason") if autorun_result else "unknown"
    card["autorun_turns_added"] = autorun_result.get("turns_added") if autorun_result else 0
    card["autorun_error"] = state.error
    card["state_error"] = state.error

    _emit(on_event, "coc_session_done", {
        "session": idx, "total": total_sessions,
        "day": state.current_day, "total_days": 7,
        "turn": len(state.turns), "max_turns": max_turns_per_session,
        "percent": 100,
        "message": f"第{idx}场完成：{variant_config.get('label','')}，幸存者{len(state.survivors)}人",
        "vid": card["vid"], "turns": card["turns"],
        "final_san": card["final_san"], "final_hp": card["final_hp"],
        "survivors": state.survivors,
        "elapsed_sec": card["elapsed_sec"],
    })
    return card


def run_coc_parallel_batch(
    runner_factory,
    *,
    book_store,
    field_store,
    field_no: int,
    coc_store: CocRunStore,
    field_run_store,
    adapt_parsed: dict,
    player_sets: list[list[dict]],
    player_sets_meta: list[dict] | None = None,
    variant_configs: list[dict],
    max_pairs_per_session: int = 6,
    max_turns_per_session: int = 24,
    on_event: Callable | None = None,
    user_directive: str | None = None,
    max_workers: int = 3,
) -> list[dict]:
    """并行跑 3 场多轮 COC 仿真（默认 2 同 PL + 1 异 PL）。

    - 每场独立 runner（runner_factory() 新建）
    - 每场独立 CocRunStore 子目录（coc_store.root/_batch/），不冲突
    - 每场独立 virtual_field 编号，落盘互不覆盖
    - 内部 autorun_until_break 使用 NOVELIZED 模式
    - 线程安全：on_event 由调用方自行保证线程安全，本函数直接在工作线程里回调
    - 每场结束后调 coc_outcome 压真相，并构造 day_breakdown/survivors/final_summary

    返回 variants 列表（按 player_sets 顺序），每项含：
      vid / title / one_liner / flavor / label / desc /
      final_san / final_hp / survivors / day_breakdown(list[{day,summary}]) /
      turns / final_summary / elapsed_sec

    on_event kind：
      coc_batch_start / coc_session_start / coc_session_turn /
      coc_session_done / coc_batch_done / coc_session_error
    payload 统一含 session(1-based)/total/day/total_days=7/turn/max_turns/percent/message。
    """
    assert len(player_sets) == len(variant_configs), (
        f"player_sets({len(player_sets)}) 与 variant_configs({len(variant_configs)}) 长度必须一致"
    )
    total_sessions = len(player_sets)

    _emit(on_event, "coc_batch_start", {
        "total": total_sessions, "total_days": 7,
        "session": 0, "day": 1, "turn": 0,
        "max_turns": max_turns_per_session, "percent": 0,
        "message": f"并行批量启动，共 {total_sessions} 场",
    })

    results: list[dict | None] = [None] * total_sessions
    max_workers = max(1, min(int(max_workers), total_sessions))

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="coc-batch") as pool:
        future_map = {}
        for i in range(total_sessions):
            fut = pool.submit(
                _run_one_session,
                i + 1, total_sessions,
                runner_factory,
                book_store=book_store,
                coc_store=coc_store,
                field_no=field_no,
                adapt_parsed=adapt_parsed,
                player_cards=player_sets[i],
                player_meta=(player_sets_meta[i] if player_sets_meta and i < len(player_sets_meta) else None),
                variant_config=variant_configs[i],
                max_pairs_per_session=max_pairs_per_session,
                max_turns_per_session=max_turns_per_session,
                on_event=on_event,
                user_directive=user_directive,
            )
            future_map[fut] = i

        for fut in as_completed(future_map):
            idx = future_map[fut]
            try:
                card = fut.result()
                results[idx] = card
            except Exception as e:
                # 线程级异常：整场没跑起来。卡片形状必须与成功卡**结构一致**
                # （前端按同一套字段渲染），但 run_status/fail_reason 明确标出失败，
                # final_summary 保持空串——失败不该伪装成梗概。
                err_card = {
                    "vid": str(idx + 1),
                    "title": f"候选 #{idx+1}：失败",
                    "one_liner": f"本场执行失败：{type(e).__name__}: {e}",
                    "flavor": variant_configs[idx].get("flavor", ""),
                    "label": variant_configs[idx].get("label", ""),
                    "desc": variant_configs[idx].get("desc", ""),
                    "final_san": 0, "final_hp": 0,
                    "survivors": [],
                    "day_breakdown": [{"day": d, "summary": ""} for d in range(1, 8)],
                    "turns": 0,
                    "final_summary": "",
                    "run_status": "failed",
                    "fail_stage": "thread",
                    "fail_reason": f"{type(e).__name__}: {e}",
                    "error": str(e),
                }
                results[idx] = err_card
                _emit(on_event, "coc_session_error", {
                    "session": idx + 1, "total": total_sessions,
                    "day": 1, "total_days": 7,
                    "turn": 0, "max_turns": max_turns_per_session,
                    "error": str(e),
                    "failed_stage": "thread",
                    "message": f"第{idx+1}场线程异常：{type(e).__name__}",
                })

    variants = [r if r is not None else {
        "vid": str(i + 1), "title": f"候选 #{i+1}", "one_liner": "",
        "flavor": variant_configs[i].get("flavor", ""),
        "label": variant_configs[i].get("label", ""),
        "desc": variant_configs[i].get("desc", ""),
        "day_breakdown": [{"day": d, "summary": ""} for d in range(1, 8)],
        "survivors": [], "final_summary": "", "turns": 0,
        "run_status": "failed", "fail_stage": "thread",
        "fail_reason": "该场未返回结果（线程未完成或已被丢弃）",
    } for i, r in enumerate(results)]

    ok_n = sum(1 for v in variants if v.get("run_status") != "failed")
    _emit(on_event, "coc_batch_done", {
        "total": total_sessions, "total_days": 7,
        "session": total_sessions, "day": 7,
        "turn": max_turns_per_session, "max_turns": max_turns_per_session,
        "percent": 100,
        # 成功数用 run_status 判定，不用 `not v.get('error')`——后者漏统计
        # 「压真相失败但卡片没有 error 键」的情况，会把失败算成成功。
        "ok_count": ok_n,
        "failed_count": total_sessions - ok_n,
        "message": (f"并行批量完成，共 {ok_n} 场成功"
                    + ("" if ok_n == total_sessions
                       else f"，{total_sessions - ok_n} 场失败（需重跑）")),
        "vids": [v.get("vid") for v in variants],
    })
    return variants
