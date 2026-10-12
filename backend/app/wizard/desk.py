"""章台单章微循环（M5 圆桌结论：连载期日更循环）。

plot → draft → polish 全部复用 PipelineRunner（幂等收据 + 有界续写）：
- draft 注入本章 pending 必现标记与上一章结尾滚动锚点；
- 方向卡作为 REFERENCE 材料随行（作者签收过的方向才注入）；
- 审稿复用七项一致性清单（第七项跨章联结对照上一章结尾）；
- finalize 转保鲜飞轮：实体抽取 + 伏笔兑现问询 + 标记核验 + 整本合成采纳 draft 版本
  （保持 Vault 导出兼容），飞轮失败不阻断定稿，错误进校正清单。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.context import Material, MaterialFamily
from app.context.ledger import build_chapter_checklist
from app.entities import extract_entities, reconcile_foreshadows
from app.pipeline import SPECS, PipelineRunner
from app.pipeline.engine import CONTINUATION_TAIL_CHARS
from app.pipeline.journal import RunJournal, compute_run_key
from app.pipeline.prompts import PromptStore
from app.review.markers import MarkerStore, markers_material
from app.providers.base import Provider
from app.wizard.store import STAGES, DeskStore, WizardStore

_CHAPTER_HEAD = re.compile(r"^#{0,4}\s*第\s*(\d+)\s*章", re.MULTILINE)
_VOLUME_HEAD = re.compile(r"^#{0,4}\s*第\s*[0-9一二三四五六七八九十百千]+\s*卷[^\n]*$", re.MULTILINE)
_SEPARATOR_LINE = re.compile(r"^[-=]{3,}\s*$")


def _volume_title_of(line: str) -> str:
    return line.lstrip("#").strip()


def _strip_separators(chunk: str) -> str:
    """去掉 Markdown 分隔线（------ / ======），它们只是装饰不进章大纲。"""
    return "\n".join(
        ln for ln in chunk.splitlines() if not _SEPARATOR_LINE.match(ln)
    ).strip()


def parse_outline(outline_text: str) -> tuple[dict[int, str], list[dict]]:
    """大纲拆章 + 卷分组（兼容裸格式「第1章」与 Markdown 标题「## 第1章」）。

    返回 (chapters, volumes)：
    - chapters：{章号: 章大纲正文}，不含章号头与卷头（卷头是结构不是正文）；
    - volumes：[{title, note, chapters:[章号]}] 按出现顺序；首个卷头之前已有的章
      归入无名首组（前端对无名组不渲染卷头）。卷头到下一章之间的零散文字记为
      该卷 note，不丢也不污染章大纲。
    """
    text = outline_text or ""
    matches = list(_CHAPTER_HEAD.finditer(text))
    chapters: dict[int, str] = {}
    volumes: list[dict] = []
    current: dict | None = None

    def open_volume(title: str) -> dict:
        v = {"title": title, "note": "", "chapters": []}
        volumes.append(v)
        return v

    pre = text[: matches[0].start()] if matches else text
    m = _VOLUME_HEAD.search(pre)
    if m:
        current = open_volume(_volume_title_of(m.group(0)))
        current["note"] = _strip_separators(pre[m.end():])

    for i, cm in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[cm.end():end]
        vol = _VOLUME_HEAD.search(chunk)
        next_title, next_note = "", ""
        if vol:
            next_title = _volume_title_of(vol.group(0))
            next_note = _strip_separators(chunk[vol.end():])
            chunk = chunk[: vol.start()]
        no = int(cm.group(1))
        if current is None:
            current = open_volume("")            # 卷头出现在更后面：先开无名首组
        current["chapters"].append(no)
        chapters[no] = _strip_separators(chunk)
        if vol:
            current = open_volume(next_title)    # 章归组后再开新卷，保证卷序正确
            current["note"] = next_note
    return chapters, volumes


def chapter_outlines(outline_text: str) -> dict[int, str]:
    """outline 文本按「第N章」段头拆章：{no: 该章大纲正文（不含章号头）}。"""
    return parse_outline(outline_text)[0]


def outline_map(project_store, pid: str) -> dict[int, str]:
    v = project_store.current(pid, "outline")
    return chapter_outlines(v.y) if v else {}


def chapter_title_of(body: str) -> str:
    lines = body.strip().splitlines()
    return lines[0].strip()[:20] if lines and lines[0].strip() else ""


def work_text(work, stage: str) -> str:
    d = getattr(work, stage, None) if work else None
    return (d.get("text", "") if d else "").strip()


def previous_tail(desk: DeskStore, chapter: int) -> str:
    """上一章结尾 500 字作为滚动锚点（润色 > 正文）；审稿第七项「跨章联结」也用它对照。"""
    for no in sorted((n for n in desk.all_nos() if n < chapter), reverse=True):
        for stage in ("polish", "draft"):
            t = work_text(desk.get(no), stage)
            if t:
                return t[-CONTINUATION_TAIL_CHARS:]
    return ""


def _direction_material(wizard: WizardStore) -> Material | None:
    d = wizard.get()
    if not d.direction.strip():
        return None
    source = {"picked": "方向卡（作者选用）", "edited": "方向卡（作者改写）",
              "manual": "方向（作者自写）"}.get(d.direction_source, "方向卡")
    return Material(source_id="wizard:direction", family=MaterialFamily.REFERENCE,
                    content=d.direction, title=f"故事方向·{source}")


def run_chapter_stage(
    provider: Provider,
    prompt_store: PromptStore,
    project_store,
    pid: str,
    marker_store: MarkerStore | None,
    wizard: WizardStore,
    desk: DeskStore,
    chapter: int,
    stage: str,
    directive: str | None = None,
    force: bool = False,
    journal_dir: Path | str | None = None,
    extra_materials: list[Material] | None = None,
    entity_store=None,
    on_event=None,
) -> dict:
    """单章单层生成：plot/draft/polish；同输入幂等回收据，force 绕过。
    extra_materials：作者勾选的跨项目参考层（REFERENCE 族，互为范文）。
    entity_store：给定时为 plot/draft 注入开章核对单（实体快照/到期伏笔/节奏位置）。"""
    if stage not in STAGES:
        raise ValueError(f"未知阶段：{stage}")
    spec = SPECS[stage]
    work = desk.get(chapter)
    o_map = outline_map(project_store, pid)

    materials: list[Material] = []
    if stage == "plot":
        body = o_map.get(chapter, "")
        if not body:
            raise ValueError(f"大纲中未找到第 {chapter} 章，请先完成故事架构站")
        x = body
        materials.append(Material(source_id=f"outline:ch{chapter}", family=MaterialFamily.FINALIZED,
                                  content=body, title=f"第{chapter}章大纲"))
        intro = project_store.current(pid, "intro")
        if intro is not None:
            materials.append(Material(source_id="intro", family=MaterialFamily.FINALIZED,
                                      content=intro.y, title="故事简介"))
    elif stage == "draft":
        x = work_text(work, "plot")
        if not x:
            raise ValueError("本章还没有剧情，请先生成剧情")
        body = o_map.get(chapter, "")
        if body:
            materials.append(Material(source_id=f"outline:ch{chapter}", family=MaterialFamily.FINALIZED,
                                      content=body, title=f"第{chapter}章大纲"))
    else:  # polish
        x = work_text(work, "draft")
        if not x:
            raise ValueError("本章还没有正文，请先生成正文")

    direction_mat = _direction_material(wizard)
    if direction_mat is not None:
        materials.append(direction_mat)
    if extra_materials:
        materials.extend(extra_materials)
    checklist_injected = False
    if entity_store is not None and stage in ("plot", "draft"):
        checklist = build_chapter_checklist(
            entity_store, desk, chapter, max(o_map) if o_map else 0)
        if checklist is not None:
            materials.append(checklist)
            checklist_injected = True
    anchor = ""
    if stage == "draft":
        if marker_store is not None:
            pending = marker_store.pending_for(chapter)
            if pending:
                materials.insert(0, markers_material(pending, chapter))
        anchor = previous_tail(desk, chapter)

    run_key = compute_run_key(stage, [x],
                              [(m.source_id, m.family.value, m.content) for m in materials],
                              directive)
    journal = None
    if journal_dir is not None and not force:
        journal = RunJournal(Path(journal_dir) / f"{run_key}.json", stage, run_key)
    runner = PipelineRunner(provider, prompt_store=prompt_store)
    result = runner.run_layer(spec, [(x, "")], materials=materials,
                              initial_anchor=anchor, user_directive=directive,
                              journal=journal, on_event=on_event)
    desk.record(chapter, stage, result.y, run_key, title=chapter_title_of(o_map.get(chapter, "")))
    return {
        "chapter": chapter, "stage": stage, "y": result.y, "run_id": run_key,
        "resumed_indices": result.resumed_indices,
        "checklist_injected": checklist_injected,
        "calls": [{"completion_status": c.completion_status, "attempts": c.attempts}
                  for c in result.calls],
    }


def finalize_chapter(
    provider: Provider,
    prompt_store: PromptStore,
    project_store,
    pid: str,
    entity_store,
    marker_store: MarkerStore | None,
    desk: DeskStore,
    chapter: int,
    auto_entities: bool = True,
    payoff: str | None = None,
) -> dict:
    """定稿 + 保鲜飞轮：实体抽取、伏笔兑现问询、标记核验、整本合成采纳。

    payoff：定稿时顺手点选的兑现规模四选一（none/small/medium/climax），
    供开章核对单的节奏位置与审稿的节奏饥饿检查消费。"""
    work = desk.get(chapter)
    text = work_text(work, "polish") or work_text(work, "draft")
    if not text:
        raise ValueError("本章还没有可定稿的正文，请先生成正文")
    if payoff is not None:
        desk.record_payoff(chapter, payoff)

    entities_report: dict | None = None
    foreshadow_report: dict | None = None
    if auto_entities and entity_store is not None:
        try:
            r = extract_entities(provider, entity_store, prompt_store, text, chapter=chapter)
            entities_report = {"created": r.created, "updated": r.updated,
                               "dropped": len(r.dropped), "attempts": r.attempts}
        except Exception as e:   # 飞轮失败不阻断定稿
            entities_report = {"error": str(e)[:300]}
        try:
            fr = reconcile_foreshadows(provider, entity_store, prompt_store, text, chapter=chapter)
            foreshadow_report = fr.as_dict() if fr is not None else None
        except Exception as e:   # 问询失败同样不阻断
            foreshadow_report = {"error": str(e)[:300]}
    marker_hits = marker_store.verify(text, chapter) if marker_store is not None else []

    parts: list[str] = []
    for no in desk.all_nos():
        w = desk.get(no)
        body = work_text(w, "polish") or work_text(w, "draft")
        if not body:
            continue
        title = f" {w.title}" if w and w.title else ""
        parts.append(f"第{no}章{title}\n{body}")
    v = project_store.adopt(pid, "draft", x_pairs=[], y="\n\n".join(parts),
                            note=f"第{chapter}章定稿（保鲜飞轮已转）")
    desk.mark_finalized(chapter)
    return {
        "chapter": chapter, "chars": len(text),
        "entities": entities_report,
        "foreshadow": foreshadow_report,
        "marker_hits": marker_hits,
        "draft_version": v.seq,
        "total_chapters": len(parts),
    }
