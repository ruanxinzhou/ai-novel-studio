"""【第N章必现】标记（M2）：从大纲/剧情提取 → 落位到章生成材料 → 校验兑现。

- 提取：正则匹配「【第N章必现】内容」，一行一条。
- 落位：运行第 N 章时，pending 标记作为 REQUIRED 材料（分区「必现标记」）
  进入装配，装不下显式失败——必现要求不允许被静默丢掉。
- 校验：对成稿做子串命中检测；命中与否作者可手动改状态，防止误判。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

MARKER_PATTERN = re.compile(r"【第(\d+)章必现】([^\n【。！？；]+)")

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_DROPPED = "dropped"


@dataclass
class Marker:
    id: str
    target_chapter: int
    content: str
    status: str = STATUS_PENDING      # pending | done | dropped
    source: str = "manual"            # manual | extracted | review
    created_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def extract_markers(text: str) -> list[Marker]:
    marks: list[Marker] = []
    for i, m in enumerate(MARKER_PATTERN.finditer(text)):
        marks.append(Marker(
            id=f"ch{m.group(1)}-{i}-{m.group(2).strip()[:12]}",
            target_chapter=int(m.group(1)),
            content=m.group(2).strip(),
            source="extracted",
            created_at=_now(),
        ))
    return marks


class MarkerStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.markers: list[Marker] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.markers = [Marker(**d) for d in data.get("markers", [])]
            except (json.JSONDecodeError, TypeError, OSError):
                self.markers = []

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"markers": [m.as_dict() for m in self.markers]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def add(self, target_chapter: int, content: str, source: str = "manual") -> Marker:
        content = content.strip()
        for m in self.markers:
            if m.target_chapter == target_chapter and m.content == content:
                return m  # 幂等：同章同内容不重复
        marker = Marker(
            id=f"ch{target_chapter}-{len(self.markers)}-{content[:12]}",
            target_chapter=target_chapter, content=content,
            source=source, created_at=_now(),
        )
        self.markers.append(marker)
        self._flush()
        return marker

    def extract_and_add(self, text: str) -> list[Marker]:
        found = extract_markers(text)
        added = [self.add(m.target_chapter, m.content, source="extracted") for m in found]
        return added

    def set_status(self, marker_id: str, status: str) -> Marker:
        if status not in (STATUS_PENDING, STATUS_DONE, STATUS_DROPPED):
            raise ValueError(f"非法状态：{status}")
        for m in self.markers:
            if m.id == marker_id:
                m.status = status
                self._flush()
                return m
        raise ValueError(f"标记不存在：{marker_id}")

    def pending_for(self, chapter_no: int) -> list[Marker]:
        return [m for m in self.markers
                if m.target_chapter == chapter_no and m.status == STATUS_PENDING]

    def verify(self, prose: str, chapter_no: int | None = None) -> list[dict]:
        """子串命中检测：pending 标记逐条看正文是否含标记内容。"""
        out = []
        for m in self.markers:
            if chapter_no is not None and m.target_chapter != chapter_no:
                continue
            if m.status != STATUS_PENDING:
                continue
            out.append({
                "marker": m.as_dict(),
                "hit": m.content in prose,
            })
        return out


def markers_material(markers: list[Marker], chapter_no: int):
    """pending 标记 → REQUIRED 材料（分区「必现标记」）。无标记返回 None。"""
    if not markers:
        return None
    from app.context import Material, MaterialFamily
    lines = "\n".join(f"- {m.content}" for m in markers)
    return Material(
        source_id=f"markers:ch{chapter_no}",
        family=MaterialFamily.REQUIRED,
        content=f"以下内容必须在本章正文中出现或明确兑现，不得遗漏：\n{lines}",
        title=f"必现标记（第{chapter_no}章）",
    )
