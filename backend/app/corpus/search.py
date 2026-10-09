"""网文范文语料检索（T10，语料源自 Chinese-WebNovel-Skill analysis/excerpts.csv）。

检索结果作为 REFERENCE/CANDIDATE 材料注入装配器：写作前先匹配 2-4 条同类
范文，先借结构再动笔，替代空泛建议。

字段：excerpt_id / article_id / title / file_path / excerpt_type / tags(|分隔)
     / para_start / para_end / text
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "data" / "excerpts.csv"


@dataclass(frozen=True)
class Excerpt:
    excerpt_id: str
    article_id: str
    title: str
    excerpt_type: str
    tags: tuple[str, ...]
    text: str

    def to_material_dict(self) -> dict:
        """可直接喂给 Material(**...) 的字段。"""
        return {
            "source_id": f"corpus:{self.excerpt_id}",
            "content": self.text,
            "title": f"范文[{self.excerpt_type}]《{self.title}》",
            "keywords": self.tags,
        }

    def as_dict(self) -> dict:
        return {
            "excerpt_id": self.excerpt_id, "article_id": self.article_id,
            "title": self.title, "excerpt_type": self.excerpt_type,
            "tags": list(self.tags), "text": self.text,
        }


@lru_cache(maxsize=1)
def _load_all() -> tuple[Excerpt, ...]:
    if not CORPUS_PATH.exists():
        return ()
    with CORPUS_PATH.open(encoding="utf-8", newline="") as f:
        return tuple(
            Excerpt(
                excerpt_id=row["excerpt_id"], article_id=row["article_id"],
                title=row["title"], excerpt_type=row["excerpt_type"],
                tags=tuple(t for t in row["tags"].split("|") if t),
                text=row["text"],
            )
            for row in csv.DictReader(f)
        )


def list_types() -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for e in _load_all():
        counts[e.excerpt_type] = counts.get(e.excerpt_type, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def list_tags() -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for e in _load_all():
        for t in e.tags:
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _matches(e: Excerpt, excerpt_type: str | None, tag: str | None, keyword: str | None) -> bool:
    if excerpt_type and excerpt_type not in e.excerpt_type:
        return False
    if tag and tag not in e.tags:
        return False
    if keyword and keyword not in e.title and keyword not in e.text:
        return False
    return True


def search(
    excerpt_type: str | None = None,
    tag: str | None = None,
    keyword: str | None = None,
    limit: int = 5,
    offset: int = 0,
) -> list[Excerpt]:
    """组合过滤：类型 + 标签 + 关键词（标题/正文子串），offset 跳过前 N 条后按条数截断。"""
    hits: list[Excerpt] = []
    skipped = 0
    for e in _load_all():
        if not _matches(e, excerpt_type, tag, keyword):
            continue
        if skipped < offset:
            skipped += 1
            continue
        hits.append(e)
        if len(hits) >= limit:
            break
    return hits


def count(
    excerpt_type: str | None = None,
    tag: str | None = None,
    keyword: str | None = None,
) -> int:
    """组合过滤后的总条数（分页 total 用）。"""
    return sum(1 for e in _load_all() if _matches(e, excerpt_type, tag, keyword))
