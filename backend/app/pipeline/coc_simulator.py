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
    "2. 之后按故事自然推进分成若干关键阶段（**最多 7 幕**）：\n"
    "   - 每幕以「第N幕：」（N=1-7 阿拉伯数字，代表阶段序号，不是日历天）开头；\n"
    "   - 紧接一段 100-200 字的纯叙事段落，具体写出在该阶段发生的事、调查员的行动与选择、NPC 的回应、重要线索/反转/代价；\n"
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
    "- 之后按故事推进分成最多 7 幕，每幕以「第N幕：」开头，紧接 100-200 字叙事段，"
    "写明调查员行动、NPC 动向、重要线索/反转、各调查员的选择与代价；\n"
    "- 末幕写清最终结果（谁死/谁活/真相如何）；\n"
    "- 最后补 100-200 字「结局总评」，首行单独写【结局标签：XXX】。\n"
    "- 文本用模组自身时间表述，不要硬凑 7 天，不要出现骰子/KP/PL 术语，不要写唐迟/魂穿/金手指。"
)

NOVELIZED_PL_PROMPT_SUFFIX = (
    "你扮演当前行动的调查员，用第一人称或近距离第三人称写出他/她此刻的反应、判断、"
    "行动和一两句话对话（如果有同伴在）。不要出现游戏术语。200-400 字。"
)


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


def _check_termination(state: CocRunState) -> str | None:
    """返回终止原因字符串，否则 None。副本按自身时间线推进，不做天数硬上限。"""
    if _all_players_down(state):
        ps = state.players_state
        any_alive = any(p.get("hp", 0) > 0 for p in ps) if ps else state.hp > 0
        if not any_alive:
            return "dead"
        return "insane"
    if len(state.turns) >= state.max_turns:
        return "max_turns"
    return None


# ---- x_block 装配 ----

def _players_status_line(state: CocRunState) -> str:
    """在 x_block 里展示团队状态（小说化模式下仍保留数值用于 KP 参考，但不出现在最终叙事里）。"""
    ps = state.players_state
    if not ps:
        return f"阶段{state.current_day}/7"
    parts = []
    for i, p in enumerate(ps):
        flag = ""
        if p.get("insane"):
            flag = "（疯狂）"
        elif not p.get("alive", True):
            flag = "（死亡）"
        parts.append(f"{p.get('name', f'PL{i+1}')} SAN{p.get('san','?')} HP{p.get('hp','?')}{flag}")
    return f"阶段{state.current_day}/7 | " + "；".join(parts)


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

    transcript = state.transcript(last_n=6) or "（开场，尚无日志）"

    if novelized:
        x = (
            f"# 当前进度\n\n{_players_status_line(state)}\n\n"
            f"# 最近情节\n\n{transcript}\n\n"
            f"# KP 本轮任务\n"
            f"你以第三人称限知视角推进第{state.current_day}阶段的情节：\n"
            f"- 描写环境氛围、NPC 的动作与言语、诡异征兆的自然浮现；\n"
            f"- 让调查员的行动得到回应（成功→推动线索，失败→付出代价/遭遇意外）；\n"
            f"- 给当前轮到行动的调查员以鲜明的、属于他/她性格的反应空间；\n"
            f"- 必须保留克苏鲁恐怖的未知感与无力感，不要一次掀开谜底；\n"
            f"- 注意：副本按自身时间线推进（小时/天/月/年自由跨度，不必凑 7 天），"
            f"文本里用模组自然的时间表述（如「当晚」「第三天凌晨」「十年后」）；"
            f"故事进入中后段（阶段 4-6）时必须推进核心反转，至少回收一个伏笔；\n"
            f"- 当前轮到行动的调查员是：{state.current_player_name()}。\n\n"
            f"{NOVELIZED_KP_PROMPT_SUFFIX}"
            f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    else:
        x = (
            f"# 当前进度\n\n阶段{state.current_day}/7   SAN {state.san}   HP {state.hp}\n\n"
            f"# 最近回合\n\n{transcript}\n\n"
            f"# KP 本轮任务\n"
            f"请扮演守密人，推进第{state.current_day}阶段的情节：\n"
            f"- 用第三人称小说笔调描写环境、NPC 行动、诡异征兆；\n"
            f"- 当需要判定时，在文中明确写出「投掷 XX：rolled/target 成功/失败」；\n"
            f"- SAN 检定失败时写明「失去 N SAN」；\n"
            f"- 必须保留克苏鲁恐怖的未知感与无力感，不要一次掀开谜底；\n"
            f"- 副本按自身时间线推进，不必凑 7 天；中后段要推进核心反转回收伏笔；\n"
            f"- 控制在 800-1200 字。"
            f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    return mats, x


def _investigator_x(state: CocRunState, last_keeper_text: str,
                    user_directive: str | None, *, novelized: bool) -> str:
    """PL 回合的 x_block。"""
    pname = state.current_player_name()
    pcard = state.current_player_card()
    occ = pcard.get("occupation", "")
    skills = pcard.get("skills") or {}
    sk_str = "、".join(f"{k}{v}" for k, v in skills.items())
    mates = [ps.get("name", "") for i, ps in enumerate(state.players_state) if i != state.active_player_idx]
    mates_line = ""
    if mates:
        mates_line = f"同行伙伴：{'、'.join(m for m in mates if m)}。"

    if novelized:
        return (
            f"# 你的人物\n\n"
            f"姓名：{pname}    身份：{occ}\n"
            f"关键技能：{sk_str}\n"
            f"你是一个有自己缺点和算盘的普通人，不是英雄。{mates_line}\n\n"
            f"# 刚才发生的事\n\n{last_keeper_text}\n\n"
            + (f"# 作者本节特别指示\n{user_directive}\n\n" if user_directive else "")
            + f"# 此刻轮到你（{pname}）行动\n"
              f"{NOVELIZED_PL_PROMPT_SUFFIX}"
              f"{MODULE_STAGE_NEGATIVE_CONSTRAINT}"
        )
    return (
        f"# 当前行动调查员人物卡\n\n{state.player_card_render()}\n\n"
        f"# KP 本轮描述\n\n{last_keeper_text}\n\n"
        + (f"# 作者本节特别指示\n{user_directive}\n\n" if user_directive else "")
        + f"# 扮演 {pname}（{occ}）做出反应\n"
          "他/她是有自己缺点和算盘的普通人，不是英雄。输出：先写内心判断（观察到什么/怕什么），"
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

def _distribute_san_loss(state: CocRunState, total_loss: int) -> None:
    """把解析到的 SAN 损失扣给当前行动玩家（主要承受者）。"""
    if total_loss <= 0:
        return
    state.apply_san_loss_to_active(total_loss)


def _build_turn_message(state: CocRunState, role: str, turn_no: int,
                        text: str) -> str:
    """生成 on_event 用的简短中文描述。"""
    head = (text or "").strip().replace("\n", " ")
    cut = re.split(r"[。！？!?]", head, maxsplit=1)[0]
    if len(cut) > 30:
        cut = cut[:30] + "…"
    if role == "keeper":
        return f"第{state.session_index}场·Day{state.current_day}：{cut}"
    else:
        who = state.current_player_name()
        return f"第{state.session_index}场·Day{state.current_day}：{who}——{cut}"


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
    rolls = parse_dice_rolls(text)
    san_loss_total = sum(r.san_loss for r in rolls)
    if san_loss_total:
        _distribute_san_loss(state, san_loss_total)
    new_day = detect_day(text)
    if new_day and new_day >= state.current_day:
        state.current_day = new_day
    turn_meta = {
        "rolls": [r.as_dict() for r in rolls],
        "san_loss": san_loss_total,
        "day": state.current_day,
    }
    turn = state.add_turn("keeper", text, meta=turn_meta)
    for r in rolls:
        r.turn_no = turn.turn_no
        r.player_idx = state.active_player_idx
    state.next_role = "investigator"
    if len(state.player_cards) > 1:
        state.advance_player()
    if on_event:
        on_event("coc_turn_done", {
            "role": "keeper", "turn_no": turn.turn_no,
            "chars": len(text), "san_loss": san_loss_total,
            "rolls": len(rolls),
            "day": state.current_day,
            "active_player": state.current_player_name(),
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
    turn = state.add_turn("investigator", text, meta={"player_idx": state.active_player_idx})
    state.next_role = "keeper"
    if on_event:
        on_event("coc_turn_done", {
            "role": "investigator", "turn_no": turn.turn_no, "chars": len(text),
            "active_player": state.current_player_name(),
            "active_player_idx": state.active_player_idx,
        })
    return turn


# ---- 主循环 ----

def autorun_until_break(runner, state: CocRunState, *, adapt_parsed, book_store,
                        run_store: CocRunStore, field_no: int,
                        max_steps: int | None = None,
                        user_directive: str | None = None,
                        on_event: Callable | None = None,
                        novelized: bool | None = None) -> dict:
    """自动交替执行 KP↔PL 直到终止条件或达到 max_steps 回合对。
    返回 {"reason": ..., "turns_added": N, "state": state}。
    每回合结束都落盘一次，支持中断恢复。

    novelized: 若为 None 则取 state.novelized（默认 True）。
    """
    novel = novelized if novelized is not None else getattr(state, "novelized", True)
    state.novelized = novel

    if state.status in ("finalized", "aborted"):
        return {"reason": "already_" + state.status, "turns_added": 0, "state": state}
    state.status = "running"
    run_store.save(state)

    steps_done = 0
    pair_limit = max_steps or 8
    reason = "paused"
    max_turns_cap = state.max_turns
    try:
        while True:
            term = _check_termination(state)
            if term:
                reason = term
                state.status = "awaiting_author"
                break
            if steps_done >= pair_limit:
                reason = "step_limit"
                state.status = "awaiting_author"
                break
            if len(state.turns) >= max_turns_cap:
                reason = "max_turns"
                state.status = "awaiting_author"
                break
            step_keeper(runner, state, adapt_parsed=adapt_parsed, book_store=book_store,
                        user_directive=user_directive, on_event=on_event, novelized=novel)
            run_store.save(state)
            term = _check_termination(state)
            if term:
                reason = term
                state.status = "awaiting_author"
                break
            step_investigator(runner, state, user_directive=user_directive,
                              on_event=on_event, novelized=novel)
            run_store.save(state)
            steps_done += 1
    except Exception as e:
        state.status = "paused"
        state.error = f"{type(e).__name__}: {e}"
        run_store.save(state)
        return {"reason": "error", "turns_added": steps_done * 2, "state": state, "error": str(e)}

    if reason in ("day7_resolved", "dead", "insane", "max_turns"):
        hints = {
            "day7_resolved": "终局阶段已收束。",
            "dead": "调查员全员倒下，跑团以死亡结局告终。",
            "insane": "SAN 归零，调查员陷入临时疯狂。",
            "max_turns": f"回合数达到硬上限 {state.max_turns}。",
        }
        state.add_turn("system", f"自动循环停止：{hints.get(reason, reason)} 作者可介入或直接 finalize。",
                       meta={"reason": reason})
    run_store.save(state)
    return {"reason": reason, "turns_added": steps_done * 2, "state": state}


# ---- 作者介入 / 状态修正 ----

def intervene(state: CocRunState, text: str, run_store: CocRunStore, *,
              as_role: str = "author") -> CocTurn:
    """作者强制插入一段内容（可以是改写 PL 行动、追加剧情设定、补扣 SAN 等）。"""
    if as_role == "investigator":
        turn = state.add_turn("investigator", text, meta={"source": "author"})
        state.next_role = "keeper"
    else:
        turn = state.add_turn("author", text, meta={})
    state.status = "running" if state.status in ("awaiting_author", "paused") else state.status
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

_DAY_HEADER_RE = re.compile(
    r"(?:Day\s*([1-7])|第\s*([1-7一二三四五六七])\s*(?:天|日|幕|章))[^\n：:]{0,20}[：:\n]"
)
_CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7}


def _parse_day_breakdown(summary_raw: str) -> list[dict]:
    """从 finalize/coc_outcome 产物里抽出 Day1-Day7 的分段摘要。"""
    text = (summary_raw or "").strip()
    if not text:
        return [{"day": d, "summary": ""} for d in range(1, 8)]
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
        per = max(1, min(7, len(paras)))
        chunk = max(1, (len(paras) + per - 1) // per)
        for d in range(1, 8):
            seg = paras[(d - 1) * chunk:d * chunk]
            if not seg:
                break
            s = "".join(seg)[:120]
            days.append({"day": d, "summary": s})
    seen = set()
    out = []
    for d in days:
        dn = int(d.get("day", 0))
        if 1 <= dn <= 7 and dn not in seen:
            seen.add(dn)
            out.append({"day": dn, "summary": d.get("summary", "")})
    existing_days = {d["day"] for d in out}
    for dn in range(1, 8):
        if dn not in existing_days:
            out.append({"day": dn, "summary": ""})
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
    if state.error:
        full_log_parts.append("")
        full_log_parts.append(f"===== 调试信息 =====\nstate.error: {state.error}")
    full_log = "\n".join(full_log_parts)

    # label/title：用 seed 里的 ending_label，不再由调用方硬覆盖成预设名
    ending_label = seed.get("ending_label") or f"第 {state.session_index} 场"
    title = f"候选 #{state.session_index}：{ending_label}"
    return {
        "vid": str(state.session_index),
        "title": title,
        "label": ending_label,
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
        "final_summary": summary_raw,
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
            autorun_until_break(
                runner, state, adapt_parsed=adapt_parsed, book_store=book_store,
                run_store=session_store, field_no=virtual_field,
                max_steps=max_pairs_per_session, user_directive=user_directive,
                novelized=novelized,
                on_event=(lambda kind, payload, i=idx: on_event(
                    "coc_session_turn", {"session": i, **payload}
                )) if on_event else None,
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
    import re as _re_label
    _m = _re_label.search(r"【结局标签[:：]\s*([^】\n]{2,30})】", raw or "")
    if _m:
        new_label = _m.group(1).strip()
        seed = state.variant_seed or {}
        seed["ending_label"] = new_label
        state.variant_seed = seed
        # 顺手把标签行从梗概正文里去掉，保持 final_summary 正文干净
        raw = _re_label.sub("", raw, count=1).strip()
    state.final_summary = raw
    state.final_day_breakdown = _parse_day_breakdown(raw)
    state.survivors = survivors
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
        # 一回合都没跑出来，把错误信息也塞到 final_summary 里，方便前端直接看到
        state.final_summary = f"（本场仿真未能启动：{state.error}）"
        state.final_day_breakdown = _parse_day_breakdown(state.final_summary)
        _emit(on_event, "coc_session_error", {
            "session": idx, "total": total_sessions,
            "day": state.current_day, "total_days": 7,
            "turn": 0, "max_turns": max_turns_per_session,
            "error": state.error,
            "message": f"第{idx}场仿真 0 回合退出：{state.error}",
        })

    _emit(on_event, "coc_session_finalize", {
        "session": idx, "total": total_sessions,
        "day": state.current_day, "turn": len(state.turns),
    })

    try:
        raw = _finalize_session_inline(runner, state, adapt_parsed, book_store)
    except Exception as e:
        tb = traceback.format_exc()
        raw = f"（本场压真相失败：{type(e).__name__}: {e}）\n\n```\n{tb}\n```"
        state.final_summary = raw
        state.final_day_breakdown = _parse_day_breakdown(raw)
        state.survivors = _collect_survivors(state)
        _emit(on_event, "coc_session_error", {
            "session": idx, "total": total_sessions,
            "day": state.current_day, "total_days": 7,
            "turn": len(state.turns), "max_turns": max_turns_per_session,
            "error": str(e),
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
                    "final_summary": f"ERROR: {type(e).__name__}: {e}",
                    "error": str(e),
                }
                results[idx] = err_card
                _emit(on_event, "coc_session_error", {
                    "session": idx + 1, "total": total_sessions,
                    "day": 1, "total_days": 7,
                    "turn": 0, "max_turns": max_turns_per_session,
                    "error": str(e),
                    "message": f"第{idx+1}场线程异常：{type(e).__name__}",
                })

    variants = [r if r is not None else {
        "vid": str(i + 1), "title": f"候选 #{i+1}", "one_liner": "",
        "flavor": variant_configs[i].get("flavor", ""),
        "label": variant_configs[i].get("label", ""),
        "desc": variant_configs[i].get("desc", ""),
        "day_breakdown": [{"day": d, "summary": ""} for d in range(1, 8)],
        "survivors": [], "final_summary": "", "turns": 0,
    } for i, r in enumerate(results)]

    _emit(on_event, "coc_batch_done", {
        "total": total_sessions, "total_days": 7,
        "session": total_sessions, "day": 7,
        "turn": max_turns_per_session, "max_turns": max_turns_per_session,
        "percent": 100,
        "message": f"并行批量完成，共 {sum(1 for v in variants if not v.get('error'))} 场成功",
        "vids": [v.get("vid") for v in variants],
    })
    return variants
