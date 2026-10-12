"""项目状态账本：把「已写状态」实时推导成可注入材料。

续写大纲时作者只给新方向，机器必须先知道前面实际写成了什么样：
- 已写剧情（FINALIZED）：章台逐章 plot 梗概——实际发生了什么
- 正文近况（FINALIZED）：最近已写正文的结尾——文风与进度衔接
- 实体账本（REQUIRED）：角色/地点/道具/情节线清单 + 未回收伏笔债务

全部从项目数据实时推导、不落快照——账实永不分离。
"""
from __future__ import annotations

import re

from app.context.assembler import truncate_tail_at_sentence
from app.context.material import Material, MaterialFamily
from app.entities.store import TYPE_LABELS, EntityStore
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
