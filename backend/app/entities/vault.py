"""Obsidian Vault 导出（M3b）：项目 → 一个可直接用 Obsidian 打开的目录。

结构：
    {out}/{书名}/
        {书名}.md          # MOC 首页：层导航 + 章节列表 + 实体索引
        目录/简介.md ...    # 五层当前采纳版本全文
        章节/第001章.md ... # draft 按章节标签切分
        实体/角色/林凡.md   # 每实体一文件，frontmatter + 出现章节互链

互链：章节与层级正文里出现的实体名替换为 [[实体名]]（长名优先，避免
「林凡」截断「林凡儿」）；实体页出现章节链到 [[章节/第NNN章|第N章]]。
Obsidian 按「最短路径」解析链接，子目录不影响 [[名字]] 命中。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.entities.store import TYPE_LABELS, EntityStore
from app.importer.slicing import split_import
from app.projects.store import LAYERS, ProjectStore

LAYER_TITLES = {
    "intro": "简介", "outline": "大纲", "plot": "剧情",
    "draft": "正文", "polish": "润色",
}

_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|]')


def _safe_name(name: str) -> str:
    return _UNSAFE_NAME.sub("_", name.strip())[:60] or "未命名"


def split_chapters(text: str) -> list[tuple[int, str]]:
    """按「第N章」切分（兼容中文数字）；无标签整体一份 [(0, text)]。"""
    return [(c["no"], c["body"]) for c in split_import(text)]


@dataclass
class VaultExport:
    out_dir: str
    files: int = 0
    chapters: int = 0
    entities: int = 0
    layers_exported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def linkify(text: str, names: list[str]) -> str:
    """正文实体名 → [[实体名]]；单趟正则替换（长名优先），不重扫已生成链接。"""
    uniq = sorted({n for n in names if n}, key=len, reverse=True)
    if not uniq:
        return text
    pattern = re.compile("|".join(re.escape(n) for n in uniq))
    return pattern.sub(lambda m: f"[[{m.group(0)}]]", text)


def _frontmatter(pairs: list[tuple[str, object]]) -> str:
    lines = ["---"]
    for k, v in pairs:
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def export_vault(project_store: ProjectStore, entities_store: EntityStore,
                 pid: str, out_root: Path | str) -> VaultExport:
    """导出整个项目；entities_store 必须是该项目的实体库。"""
    meta = project_store.get(pid)
    result = VaultExport(out_dir="")
    book = _safe_name(meta.title)
    out = Path(out_root) / book
    if out.exists():
        result.warnings.append(f"目录已存在，覆盖写入：{out}")
    (out / "目录").mkdir(parents=True, exist_ok=True)
    (out / "章节").mkdir(parents=True, exist_ok=True)

    def write(rel: str, content: str) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        result.files += 1

    entity_names = [e.name for e in entities_store.entities]

    # ---- 五层当前版本 ----
    layer_links: list[str] = []
    for layer in LAYERS:
        v = project_store.current(pid, layer)
        if v is None:
            continue
        fname = f"{LAYER_TITLES[layer]}.md"
        fm = _frontmatter([
            ("layer", layer), ("version", v.seq),
            ("adopted", v.timestamp or "—"), ("note", v.note or ""),
        ])
        body = linkify(v.y, entity_names)
        write(f"目录/{fname}", f"{fm}\n\n# {LAYER_TITLES[layer]}（v{v.seq}）\n\n{body}\n")
        layer_links.append(f"- [[目录/{LAYER_TITLES[layer]}|{LAYER_TITLES[layer]}]]（v{v.seq}）")
        result.layers_exported.append(layer)

    # ---- 章节切分（draft 优先，退回 plot）----
    source_layer = "draft" if "draft" in result.layers_exported else (
        "plot" if "plot" in result.layers_exported else None)
    chapter_entries: list[str] = []
    if source_layer:
        v = project_store.current(pid, source_layer)
        for no, body in split_chapters(v.y):
            if no == 0:
                chapter_entries.append(f"- [[章节/全文|全文]]")
                write("章节/全文.md", linkify(body, entity_names) + "\n")
                continue
            rel = f"章节/第{no:03d}章.md"
            fm = _frontmatter([("chapter", no), ("source", source_layer)])
            write(rel, f"{fm}\n\n{linkify(body, entity_names)}\n")
            chapter_entries.append(f"- [[{rel[:-3]}|第{no}章]]")
            result.chapters += 1
        if source_layer == "plot":
            result.warnings.append("尚无正文层，章节页按剧情层切分")

    # ---- 实体页 ----
    by_type: dict[str, list] = {}
    for e in entities_store.entities:
        by_type.setdefault(e.type, []).append(e)
        fm = _frontmatter([
            ("type", e.type), ("status", e.status), ("source", e.source),
            ("appearances", e.appearances), ("updated", e.updated_at or "—"),
        ])
        parts = [fm, f"\n# {e.name}\n"]
        if e.summary:
            parts.append(f"\n{e.summary}\n")
        if e.details:
            parts.append(f"\n## 设定\n\n{e.details}\n")
        if e.appearances:
            links = "\n".join(
                f"- [[章节/第{c:03d}章|第{c}章]]" for c in e.appearances)
            parts.append(f"\n## 出现章节\n\n{links}\n")
        write(f"实体/{TYPE_LABELS[e.type]}/{_safe_name(e.name)}.md", "\n".join(parts))
        result.entities += 1

    # ---- MOC 首页 ----
    moc: list[str] = [f"# {meta.title}", ""]
    if meta.intro:
        moc += [f"> {meta.intro}", ""]
    moc += ["## 创作层级", *(layer_links or ["- （尚未采纳任何层）"]), ""]
    moc += ["## 章节", *(chapter_entries or ["- （尚未切分出章节）"]), ""]
    moc.append("## 实体")
    if not by_type:
        moc.append("- （实体库为空，可从正文抽取或手动补录）")
    for etype, label in TYPE_LABELS.items():
        ents = by_type.get(etype)
        if not ents:
            continue
        moc.append(f"\n### {label}")
        for e in sorted(ents, key=lambda x: x.name):
            moc.append(f"- [[{e.name}]] — {e.summary or '（无描述）'}")
    write(f"{book}.md", "\n".join(moc) + "\n")

    result.out_dir = str(out)
    return result
