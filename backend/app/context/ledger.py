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


# ---- 开章核对单（章台写第 N 章前的机器自检） ----

CHECKLIST_SECTION_LIMITS = {"character": 20, "location": 10, "item": 12, "plotline": 8}
CHECKLIST_FORESHADOW_LISTED = 15     # 到期伏笔最多列多少条
FORESHADOW_DUE_AFTER = 8             # 账龄超过多少章标「重点兑现」

# 兑现节奏（P2③）：空账期用拆书经验目标线垫底，攒了自家数据用自家的
PAYOFF_TARGET_SMALL = 3              # 小爽点目标章距（天蚕土豆：2-3 章一次）
PAYOFF_TARGET_MEDIUM = 12            # 中兑现目标章距（10-15 章一次取中值）
PAYOFF_HUNGER_AFTER = 4              # 连续多少章无兑现报「节奏饥饿」
_PAID_SCALES = ("small", "medium", "climax")


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
    """开章核对单：写第 N 章前的 REQUIRED 自检材料。

    四块——本章位置（进度与跳章提醒）、节奏位置（距上次兑现几章，
    无自家记录退化为目标线）、实体快照（截至上一章的既有状态，
    含已退场/未出场标注）、到期伏笔（按账龄排序，越老越该兑现）。
    四块全空（开书第一章且无设定）返回 None，不注入空核对单。
    """
    sections: list[str] = []

    # 排除本章：写完本章剧情后重跑同阶段，核对单不变才能命中幂等收据
    written = [no for no in _written_nos(desk) if no != chapter]
    prior_written = [no for no in written if no < chapter]
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
        sections.append("【本章位置】\n" + "；".join(pos))

    rhythm = _rhythm_section(desk, chapter, bool(written))
    if rhythm is not None:
        sections.append(rhythm)

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

    planted = [e for e in entity_store.list(type="foreshadow", status="planted")
               if e.appearances and min(e.appearances) < chapter]
    if planted:
        planted.sort(key=lambda e: (min(e.appearances), e.name))
        lines = []
        for e in planted[:CHECKLIST_FORESHADOW_LISTED]:
            age = chapter - min(e.appearances)
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            seg += f"（第{min(e.appearances)}章埋，已欠 {age} 章"
            if age >= FORESHADOW_DUE_AFTER:
                seg += "，重点兑现"
            seg += "）"
            lines.append(f"- {seg}")
        if len(planted) > CHECKLIST_FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - CHECKLIST_FORESHADOW_LISTED} 条略）")
        sections.append("【到期伏笔】（按账龄排序，本章应安排推进或兑现其一）\n" + "\n".join(lines))

    if not sections:
        return None
    head = (f"【开章核对单·第{chapter}章】写本章前机器自检。新章必须与下列既有状态一致："
            "已出场角色/地点/道具/情节线沿用既有设定，不得重设或自相矛盾；"
            "到期伏笔是本章的待办债务，应在剧情中安排推进或兑现。")
    return Material(source_id=f"checklist:ch{chapter}", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n\n".join(sections),
                    title=f"开章核对单（第{chapter}章）")
