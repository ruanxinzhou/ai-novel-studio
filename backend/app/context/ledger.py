"""项目状态账本：把「已写状态」实时推导成可注入材料。

续写大纲时作者只给新方向，机器必须先知道前面实际写成了什么样：
- 已写剧情（FINALIZED）：章台逐章 plot 梗概——实际发生了什么
- 正文近况（FINALIZED）：最近已写正文的结尾——文风与进度衔接
- 实体账本（REQUIRED）：角色/地点/道具/情节线清单 + 未回收伏笔债务

全部从项目数据实时推导、不落快照——账实永不分离。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.context.assembler import truncate_tail_at_sentence
from app.context.material import Material, MaterialFamily
from app.entities.store import TYPE_LABELS, EntityStore

if TYPE_CHECKING:      # 仅类型标注；运行时由调用方传入，避免 wizard→context→wizard 环
    from app.wizard.store import DeskStore

PLOT_DIGEST_MAX_CHAPTERS = 40      # 剧情梗概最多带多少章（超出留最近段）
PLOT_PER_CHAPTER_CHARS = 160       # 每章梗概截断
DRAFT_RECENT_CHARS = 6_000         # 正文近况字符上限
ENTITY_SECTION_LIMITS = {"character": 40, "location": 25, "item": 25, "plotline": 15}
FORESHADOW_LISTED = 40             # 未回收伏笔最多列多少条
_SUMMARY_CHARS = 40                # 实体简介截断


def _stage_text(work, stage: str) -> str:
    d = getattr(work, stage, None) if work else None
    return (d.get("text", "") if d else "").strip()


def _ch_span(chapters: list[int]) -> str:
    if not chapters:
        return ""
    if len(chapters) == 1:
        return f"第{chapters[0]}章"
    return f"第{min(chapters)}-{max(chapters)}章"


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _plot_digest(desk: DeskStore) -> Material | None:
    lines: list[str] = []
    for no in desk.all_nos():
        work = desk.get(no)
        plot = _stage_text(work, "plot")
        if not plot:
            continue
        title = f" {work.title}" if work and work.title else ""
        lines.append(f"第{no}章{title}：{_clean(plot, PLOT_PER_CHAPTER_CHARS)}")
    if not lines:
        return None
    total = len(lines)
    if total > PLOT_DIGEST_MAX_CHAPTERS:
        lines = lines[-PLOT_DIGEST_MAX_CHAPTERS:]
    head = f"已写章节的实际剧情（章台逐章记录，共 {total} 章"
    if total > PLOT_DIGEST_MAX_CHAPTERS:
        head += f"，此处列最近 {len(lines)} 章，更早章节见已有大纲"
    content = head + "）：\n" + "\n".join(lines)
    return Material(source_id="state:plot-digest", family=MaterialFamily.FINALIZED,
                    content=content, title="已写剧情梗概")


def _draft_recent(project_store, desk: DeskStore, pid: str) -> Material | None:
    for no in sorted(desk.all_nos(), reverse=True):
        work = desk.get(no)
        text = _stage_text(work, "polish") or _stage_text(work, "draft")
        if not text:
            continue
        title = f" {work.title}" if work and work.title else ""
        body = truncate_tail_at_sentence(text, DRAFT_RECENT_CHARS)
        return Material(source_id="state:draft-tail", family=MaterialFamily.FINALIZED,
                        content=f"第{no}章{title}\n{body}",
                        title=f"正文近况（第{no}章，实际写到这）")
    v = project_store.current(pid, "draft")
    if v is not None and v.y.strip():
        return Material(source_id="state:draft-tail", family=MaterialFamily.FINALIZED,
                        content=truncate_tail_at_sentence(v.y, DRAFT_RECENT_CHARS),
                        title="正文近况（正文层结尾）")
    return None


def _entity_lines(entities: list, limit: int) -> list[str]:
    lines: list[str] = []
    for e in entities[:limit]:
        seg = e.name
        if e.summary:
            seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
        if e.appearances:
            seg += f"（{_ch_span(e.appearances)}）"
        if e.status == "retired":
            seg += "〔已退场〕"
        lines.append(f"- {seg}")
    if len(entities) > limit:
        lines.append(f"（其余 {len(entities) - limit} 个略）")
    return lines


def _entity_ledger(store: EntityStore) -> Material | None:
    sections: list[str] = []
    for etype in ("character", "location", "item", "plotline"):
        ents = store.list(type=etype)
        if not ents:
            continue
        ents.sort(key=lambda e: (-len(e.appearances), e.name))
        sections.append(f"【{TYPE_LABELS[etype]}】\n"
                        + "\n".join(_entity_lines(ents, ENTITY_SECTION_LIMITS[etype])))

    planted = store.list(type="foreshadow", status="planted")
    paid = len(store.list(type="foreshadow", status="paid"))
    dropped = len(store.list(type="foreshadow", status="dropped"))
    if planted:
        planted.sort(key=lambda e: (min(e.appearances) if e.appearances else 10**9, e.name))
        lines = []
        for e in planted[:FORESHADOW_LISTED]:
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            if e.appearances:
                seg += f"（第{min(e.appearances)}章埋）"
            lines.append(f"- {seg}")
        if len(planted) > FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - FORESHADOW_LISTED} 条略）")
        if paid or dropped:
            lines.append(f"（另有已回收 {paid} 条、已弃 {dropped} 条，不再列出）")
        sections.append("【未回收伏笔】（已埋未收——续写时优先安排推进或兑现）\n" + "\n".join(lines))

    if not sections:
        return None
    head = ("本书当前实体与伏笔账本（实时状态）。续写新章必须延续：已出场角色、地点、道具与设定"
            "不得重设或自相矛盾；未回收伏笔是新章的待办债务，应在新段中安排推进或兑现。")
    return Material(source_id="state:entities", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n\n".join(sections),
                    title="实体账本（角色/地点/道具/情节线/未回收伏笔）")


def build_state_materials(project_store, entity_store: EntityStore,
                          desk: DeskStore, pid: str) -> list[Material]:
    """项目已写状态 → 三类材料（有什么带什么，没有的不硬造）。"""
    mats: list[Material] = []
    for m in (_plot_digest(desk), _draft_recent(project_store, desk, pid),
              _entity_ledger(entity_store)):
        if m is not None:
            mats.append(m)
    return mats


# ---- 开章任务书（章台写第 N 章前的机器自检，五段结构） ----

CHECKLIST_SECTION_LIMITS = {"character": 20, "location": 10, "item": 12, "plotline": 8}
CHECKLIST_FORESHADOW_LISTED = 15     # 到期伏笔最多列多少条
FORESHADOW_DUE_AFTER = 8             # 账龄超过多少章 → 本章必须处理
FORESHADOW_HARD_AFTER = 12           # 账龄超过多少章 → 硬债务（最老一批尽快处理）
OPENING_TAIL_CHARS = 400             # 【开篇委托】带的上一章结尾长度

# 兑现节奏（P2③）：空账期用拆书经验目标线垫底，攒了自家数据用自家的
PAYOFF_TARGET_SMALL = 3              # 小爽点目标章距（天蚕土豆：2-3 章一次）
PAYOFF_TARGET_MEDIUM = 12            # 中兑现目标章距（10-15 章一次取中值）
PAYOFF_HUNGER_AFTER = 4              # 连续多少章无兑现报「节奏饥饿」
_PAID_SCALES = ("small", "medium", "climax")

# 三线节奏（Strand）：quest 主线 / fire 冲突 / bond 羁绊——常量归本模块，
# wizard.store 仅引用做入参校验（运行时反向 import 会成环）
STRANDS = ("quest", "fire", "bond")
STRAND_LABELS = {"quest": "主线", "fire": "冲突", "bond": "羁绊"}
STRAND_WINDOW = 20                   # 占比统计窗口：最近多少个已写章
STRAND_TARGET_SHARES = {"quest": 0.6, "fire": 0.2, "bond": 0.2}
STRAND_GAP_WARNS = {"quest": 5, "fire": 10, "bond": 15}   # 断档红线：连续 N 个已写章未推进


def _payoff_records(desk: DeskStore, chapter: int) -> list[tuple[int, str]]:
    """截至上一章的兑现记录 [(章号, 规模)]，按章号升序；排除本章保证幂等。"""
    out = []
    for no in desk.all_nos():
        if no >= chapter:
            continue
        w = desk.get(no)
        if w is not None and w.payoff:
            out.append((no, w.payoff))
    return out


def _prior_written(desk: DeskStore, chapter: int) -> list[int]:
    """截至上一章、已有剧情或正文的章号升序列表（plot 算开工，draft 算成文）。"""
    out = []
    for no in desk.all_nos():
        if no >= chapter:
            continue
        work = desk.get(no)
        if work is not None and (_stage_text(work, "plot") or _stage_text(work, "draft")):
            out.append(no)
    return out


def _strand_tag_map(desk: DeskStore, nos: list[int]) -> dict[int, list[str]]:
    return {no: list(getattr(desk.get(no), "strands", []) or []) for no in nos}


def _rhythm_section(desk: DeskStore, chapter: int, written: bool) -> str | None:
    """节奏位置块：距上次兑现几章、该蓄还是该爆；无自家数据退化为目标线。"""
    records = _payoff_records(desk, chapter)
    if records:
        last_no, last_scale = max(records)
        gap = chapter - last_no
        seg = f"距上次兑现已 {gap} 章（第 {last_no} 章"
        if last_scale in ("medium", "climax"):
            seg += "，中兑现以上"
        seg += "）"
        if gap >= PAYOFF_HUNGER_AFTER:
            seg += f"——超过 {PAYOFF_HUNGER_AFTER} 章未喂，本章宜安排小爽点及以上兑现"
        medium_last = max((no for no, s in records if s in ("medium", "climax")), default=0)
        if medium_last:
            seg += f"；距上次中兑现 {chapter - medium_last} 章"
        return "【节奏位置】\n" + seg
    if not written:
        return None
    return ("【节奏位置】（本书尚无兑现记录，先用目标线垫底：小爽点约 "
            f"{PAYOFF_TARGET_SMALL} 章一次、中兑现约 {PAYOFF_TARGET_MEDIUM} 章一次；"
            "定稿时点选「本章兑现」四选一，攒 20 章即有自家量纲）")


def rhythm_hunger(desk: DeskStore, chapter: int) -> str | None:
    """节奏饥饿（审稿附加项，纯数据推导不走 LLM）：从本章往回数连续无兑现的
    已写章数（none 或未记录都算无兑现），≥ PAYOFF_HUNGER_AFTER 报警。"""
    streak = 0
    for no in range(chapter, 0, -1):
        w = desk.get(no)
        if w is None or not (_stage_text(w, "plot") or _stage_text(w, "draft")):
            break                       # 未写的章不算
        if w.payoff in _PAID_SCALES:
            break
        streak += 1
    if streak >= PAYOFF_HUNGER_AFTER:
        return (f"节奏饥饿：已连续 {streak} 章无兑现"
                f"（目标线：小爽点约 {PAYOFF_TARGET_SMALL} 章一次），本章宜安排兑现")
    return None


# ---- 三线节奏（纯数据推导） ----

def strand_warnings(desk: DeskStore, chapter: int) -> list[str]:
    """三线断档/占比报警（审稿附加项，不走 LLM）。

    断档：某线连续 STRAND_GAP_WARNS 个已写章未推进 → 报警。
    占比：近 STRAND_WINDOW 个已写章中某线覆盖率不足目标一半（≥10 章样本）→ 报警。
    打卡率低（近 20 章超三成没点三线标签）→ 提示补打卡。
    """
    prior = _prior_written(desk, chapter)
    if not prior:
        return []
    tag_map = _strand_tag_map(desk, prior)
    warns: list[str] = []
    for strand in STRANDS:
        streak = 0
        for no in reversed(prior):
            if strand in tag_map[no]:
                break
            streak += 1
        if streak >= STRAND_GAP_WARNS[strand]:
            warns.append(f"{STRAND_LABELS[strand]}线断档：已连续 {streak} 章未推进"
                         f"（红线 {STRAND_GAP_WARNS[strand]} 章），本章宜安排")
    window = prior[-STRAND_WINDOW:]
    if len(window) >= 10:
        untagged = sum(1 for no in window if not tag_map[no])
        if untagged * 3 > len(window):
            warns.append(f"近 {len(window)} 章有 {untagged} 章未点三线标签——"
                         "定稿时顺手点选本章推进了 主线/冲突/羁绊 哪些线，占比账才准")
        for strand in STRANDS:
            share = sum(1 for no in window if strand in tag_map[no]) / len(window)
            target = STRAND_TARGET_SHARES[strand]
            if share < target / 2:
                warns.append(f"{STRAND_LABELS[strand]}线近 {len(window)} 章覆盖率 {share:.0%}"
                             f"（目标约 {target:.0%}）——偶尔回头推一推，别一直冷着")
    return warns


def _strand_section(desk: DeskStore, chapter: int, written_nos: list[int]) -> str | None:
    """【怎么写更顺】里的三线近况块：每条线距上次推进几章 + 本章宜覆盖提示。"""
    prior = [no for no in written_nos if no < chapter]
    if not prior:
        return None
    tag_map = _strand_tag_map(desk, prior)
    if not any(tag_map.values()):
        return ("三线近况：本书尚未记录三线标签（定稿时点选本章推进了 主线/冲突/羁绊 哪些线）。"
                "参考配比：主线约六成章节要推，冲突与羁绊各约两成——主线断 5 章、冲突断 10 章、"
                "羁绊断 15 章就算断档。")
    lines = []
    for strand in STRANDS:
        label = STRAND_LABELS[strand]
        lasts = [no for no in prior if strand in tag_map[no]]
        if lasts:
            last = max(lasts)
            gap = chapter - last
            seg = f"- {label}线：距上次推进 {gap} 章（第 {last} 章）"
            if gap > STRAND_GAP_WARNS[strand]:
                seg += "——已超断档红线，本章宜安排推进"
        else:
            seg = f"- {label}线：本书尚未推进过"
        lines.append(seg)
    return "三线近况（参考配比：主线约六成、冲突与羁绊各约两成）：\n" + "\n".join(lines)


def _opening_commission(desk: DeskStore, chapter: int) -> str | None:
    """【开篇委托】：上一章实际结尾 + 接棒要求。"""
    for no in sorted((n for n in desk.all_nos() if n < chapter), reverse=True):
        work = desk.get(no)
        tail = _stage_text(work, "polish") or _stage_text(work, "draft")
        if not tail:
            continue
        body = truncate_tail_at_sentence(tail, OPENING_TAIL_CHARS)
        return ("【开篇委托】（上一章实际写到这——本章开篇必须接住它的变化，不另起炉灶）\n"
                f"第{no}章结尾：{body}")
    return None


def _written_nos(desk: DeskStore) -> list[int]:
    """已有剧情或正文的章（plot 阶段算开工，draft 算成文）。"""
    out = []
    for no in desk.all_nos():
        work = desk.get(no)
        if _stage_text(work, "plot") or _stage_text(work, "draft"):
            out.append(no)
    return out


def build_chapter_checklist(entity_store: EntityStore, desk: DeskStore,
                            chapter: int, outline_total: int) -> Material | None:
    """开章任务书：写第 N 章前的 REQUIRED 自检材料，五段结构。

    ①开篇委托（上一章结尾，开篇接住变化）②本章故事（进度与跳章提醒）
    ③本章人物与设定（截至上一章的实体快照，含已退场/未出场标注）
    ④怎么写更顺（节奏位置 + 三线近况 + 到期伏笔按账龄排急缓）
    ⑤收在哪里（章末收束要求）。五段全空（开书第一章且无设定）返回 None。
    """
    sections: list[str] = []

    # 排除本章：写完本章剧情后重跑同阶段，任务书不变才能命中幂等收据
    written = [no for no in _written_nos(desk) if no != chapter]
    prior_written = [no for no in written if no < chapter]

    # ① 开篇委托
    opening = _opening_commission(desk, chapter)
    if opening is not None:
        sections.append(opening)

    # ② 本章故事（位置）
    pos = [f"本章为第 {chapter} 章"]
    if outline_total > 0:
        pos.append(f"大纲已排至第 {outline_total} 章")
        if outline_total >= chapter and chapter > 0:
            pos.append(f"进度约 {chapter * 100 // outline_total}%")
    if written:
        pos.append(f"已写 {len(written)} 章（最近写到第 {max(written)} 章）")
        if prior_written and max(prior_written) < chapter - 1:
            pos.append(f"注意：第 {max(prior_written) + 1}-{chapter - 1} 章尚未写，本章是跳章续写")
    if len(pos) > 1:  # 只有「本章为第 N 章」一行时不单独成节
        sections.append("【本章故事】（本章位置）\n" + "；".join(pos))

    # ③ 本章人物与设定（实体快照）
    for etype in ("character", "location", "item", "plotline"):
        ents = []
        for e in entity_store.list(type=etype):
            prior = [a for a in e.appearances if a < chapter]
            if not e.appearances or prior:
                ents.append((e, prior))
        if not ents:
            continue
        ents.sort(key=lambda t: (-(max(t[1]) if t[1] else 0), t[0].name))
        lines = []
        shown = ents[:CHECKLIST_SECTION_LIMITS[etype]]
        for e, prior in shown:
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            if e.status == "retired":
                seg += f"〔已退场〕（{_ch_span(prior)}）" if prior else "〔已退场，尚未出场〕"
            elif not prior:
                seg += "〔设定中，尚未出场〕"
            else:
                seg += f"（{_ch_span(prior)}）"
            lines.append(f"- {seg}")
        if len(ents) > CHECKLIST_SECTION_LIMITS[etype]:
            lines.append(f"（其余 {len(ents) - CHECKLIST_SECTION_LIMITS[etype]} 个略）")
        sections.append(f"【{TYPE_LABELS[etype]}快照】（截至上一章的既有设定，不得重设或矛盾）\n"
                        + "\n".join(lines))

    # ④ 怎么写更顺：节奏位置 + 三线近况 + 到期伏笔
    smooth_parts: list[str] = []
    rhythm = _rhythm_section(desk, chapter, bool(written))
    if rhythm is not None:
        smooth_parts.append(rhythm)
    strand = _strand_section(desk, chapter, written)
    if strand is not None:
        smooth_parts.append(strand)

    planted = [e for e in entity_store.list(type="foreshadow", status="planted")
               if e.appearances and min(e.appearances) < chapter]
    if planted:
        planted.sort(key=lambda e: (min(e.appearances), e.name))
        due_n = sum(1 for e in planted
                    if chapter - min(e.appearances) >= FORESHADOW_DUE_AFTER)
        hard_n = sum(1 for e in planted
                     if chapter - min(e.appearances) >= FORESHADOW_HARD_AFTER)
        head = (f"【到期伏笔】（未回收共 {len(planted)} 条，按账龄排序——账龄 ≥{FORESHADOW_DUE_AFTER} 章"
                f"的 {due_n} 条是本章硬债务，必须安排推进或兑现其一")
        if hard_n:
            head += f"；其中 ≥{FORESHADOW_HARD_AFTER} 章的 {hard_n} 条已拖到极限，最老的优先处理"
        head += "）"
        lines = []
        for e in planted[:CHECKLIST_FORESHADOW_LISTED]:
            age = chapter - min(e.appearances)
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            seg += f"（第{min(e.appearances)}章埋，已欠 {age} 章"
            if age >= FORESHADOW_DUE_AFTER:
                seg += "，本章必须处理"
            seg += "）"
            lines.append(f"- {seg}")
        if len(planted) > CHECKLIST_FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - CHECKLIST_FORESHADOW_LISTED} 条略）")
        smooth_parts.append(head + "\n" + "\n".join(lines))
    if smooth_parts:
        sections.append("【怎么写更顺】\n\n" + "\n\n".join(smooth_parts))

    if not sections:
        return None

    # ⑤ 收在哪里（章末收束要求）
    sections.append(
        "【收在哪里】本章收尾要求：章末必须落在「变化」上——局势、关系或认知至少一处"
        "不可逆转；结尾留下一章去向（钩子/悬念/未决动作），不要收成总结陈述，"
        "也不要在本章内把下一章的爆点提前泄掉。")

    head = (f"【开章任务书·第{chapter}章】写本章前的机器自检与委托。新章必须与下列既有状态一致："
            "已出场角色/地点/道具/情节线沿用既有设定，不得重设或自相矛盾；到期伏笔是本章的待办债务，"
            "应在剧情中安排推进或兑现。材料冲突时按此优先级取舍：作者指令 > 本章大纲 > "
            "本任务书既有状态 > 其他参考材料。")
    return Material(source_id=f"checklist:ch{chapter}", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n\n".join(sections),
                    title=f"开章任务书（第{chapter}章）")
