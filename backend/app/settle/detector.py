"""杀青（settle）伏笔检测器：本卷 planted 但未回收的伏笔自动识别。

扫描已绑定到 field_no 的 finalized 章正文，按 name/summary 关键词做简单子串匹配，
统计 mentions；判定 planted（未提及）/mentioned（提及未回收）/overdue（到期未回收）。
"""
from __future__ import annotations

from app.entities.store import EntityStore
from app.wizard.store import DeskStore


def _chapter_text(ch) -> str:
    """取章正文：polish 优先，draft 兜底；两者都有取 polish。"""
    polish = ch.polish or {}
    if isinstance(polish, dict) and polish.get("text"):
        return str(polish.get("text", ""))
    draft = ch.draft or {}
    if isinstance(draft, dict) and draft.get("text"):
        return str(draft.get("text", ""))
    return ""


def _count_mentions(text_lower: str, name: str, summary: str) -> int:
    """简单子串计数：同时用 name 与 summary 前 4 字作为关键词，大小写不敏感。"""
    count = 0
    name_kw = name.strip()
    if name_kw:
        count += text_lower.count(name_kw.lower())
    sum_kw = summary.strip()[:4]
    if sum_kw and sum_kw != name_kw:
        count += text_lower.count(sum_kw.lower())
    return count


def detect_foreshadows(es: EntityStore, desk: DeskStore, field_no: int | None = None) -> dict:
    """检测已 planted 但本卷正文未提及/未回收的伏笔。

    Args:
        es: 实体库（需 es.list(type='foreshadow'), es.get(eid), ...）
        desk: 章台
        field_no: 限定检测绑定到本场（卷）的章；None 则扫所有 finalized 章。

    Returns:
        {
          "unresolved": [{"id","name","summary","plant_chapter","due_chapter","intensity",
                          "mentions_in_volume","status"}],
          "paid_in_volume": [{"id","name","payoff_chapter"}],
          "newly_planted_hints": []   # 预留
        }
    """
    # 收集本卷 finalized 章的章号与正文
    chapter_texts: dict[int, str] = {}
    finalized_nos: list[int] = []
    for no in desk.all_nos():
        ch = desk.get(no)
        if not ch:
            continue
        if not bool(ch.finalized_at):
            continue
        if field_no is not None and ch.field_no != field_no:
            continue
        finalized_nos.append(no)
        chapter_texts[no] = _chapter_text(ch)

    max_finalized_no = max(finalized_nos) if finalized_nos else 0
    all_text_lower = "\n".join(chapter_texts.values()).lower()

    unresolved: list[dict] = []
    paid_in_volume: list[dict] = []

    foreshadows = es.list(type="foreshadow")
    for e in foreshadows:
        sa = e.state_attrs or {}
        plant_chapter = sa.get("plant_chapter", "")
        due_chapter = sa.get("due_chapter", "")
        payoff_chapter = sa.get("payoff_chapter", "")
        intensity = sa.get("intensity", "")

        # paid_in_volume：本卷变成 paid 的伏笔，且 payoff_chapter 在本卷章号范围内
        if e.status == "paid":
            try:
                payoff_no = int(payoff_chapter)
            except (TypeError, ValueError):
                payoff_no = None
            if payoff_no is not None and payoff_no in finalized_nos:
                paid_in_volume.append({
                    "id": e.id,
                    "name": e.name,
                    "payoff_chapter": payoff_chapter,
                })
            continue

        # dropped 状态的伏笔不再算作 unresolved
        if e.status == "dropped":
            continue

        # 仅保留 planted 状态的伏笔进入 unresolved
        if e.status != "planted":
            continue

        mentions = _count_mentions(all_text_lower, e.name, e.summary)

        # 判定 overdue：due_chapter 可解析为 int 且 <= max_finalized_no
        is_overdue = False
        try:
            due_no = int(due_chapter) if due_chapter else None
        except (TypeError, ValueError):
            due_no = None
        if due_no is not None and max_finalized_no > 0 and due_no <= max_finalized_no:
            is_overdue = True

        if is_overdue:
            status = "overdue"
        elif mentions > 0:
            status = "mentioned"
        else:
            status = "planted"

        unresolved.append({
            "id": e.id,
            "name": e.name,
            "summary": e.summary,
            "plant_chapter": plant_chapter,
            "due_chapter": due_chapter,
            "intensity": intensity,
            "mentions_in_volume": mentions,
            "status": status,
        })

    return {
        "unresolved": unresolved,
        "paid_in_volume": paid_in_volume,
        "newly_planted_hints": [],
    }
