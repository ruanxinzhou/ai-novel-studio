"""实体库（M3b）：角色/地点/道具/情节线/伏笔，项目维度持久化。

布局：backend/data/projects/{pid}/entities.json（与标记同模式，原子写入）。

合并策略（LLM 抽取回灌）：同类型同名视为同一实体——已存在则只补空字段与
出现章节（去重追加），不覆盖人工已写内容；不存在则新建 source=extracted。
伏笔有独立生命周期：planted（埋下）→ paid（回收）/ dropped（弃用）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

ENTITY_TYPES = ("character", "location", "item", "plotline", "foreshadow")

TYPE_LABELS = {
    "character": "角色",
    "location": "地点",
    "item": "道具",
    "plotline": "情节线",
    "foreshadow": "伏笔",
}

ENTITY_STATUSES = ("active", "retired", "planted", "paid", "dropped")

STATUS_LABELS = {
    "active": "活跃",
    "retired": "退场",
    "planted": "已埋",
    "paid": "已回收",
    "dropped": "已弃",
}

TYPE_STATUS_DEFAULT = {"foreshadow": "planted"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Entity:
    id: str
    type: str
    name: str
    summary: str = ""
    details: str = ""
    status: str = "active"
    source: str = "manual"            # manual | extracted
    appearances: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def as_summary(self) -> dict:
        return {
            "id": self.id, "type": self.type, "name": self.name,
            "summary": self.summary, "status": self.status,
            "appearances": self.appearances,
        }


class EntityStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entities: list[Entity] = []
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.entities = [Entity(**d) for d in data.get("entities", [])]
            except (json.JSONDecodeError, TypeError, OSError):
                self.entities = []

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"entities": [e.as_dict() for e in self.entities]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ---- 查询 ----

    def list(self, type: str | None = None, status: str | None = None,
             keyword: str | None = None) -> list[Entity]:
        out = self.entities
        if type:
            out = [e for e in out if e.type == type]
        if status:
            out = [e for e in out if e.status == status]
        if keyword:
            k = keyword.strip()
            out = [e for e in out if k in e.name or k in e.summary or k in e.details]
        return out

    def get(self, eid: str) -> Entity:
        for e in self.entities:
            if e.id == eid:
                return e
        raise ValueError(f"实体不存在：{eid}")

    def find(self, type: str, name: str) -> Entity | None:
        name = name.strip()
        for e in self.entities:
            if e.type == type and e.name == name:
                return e
        return None

    # ---- 写入 ----

    def add(self, type: str, name: str, summary: str = "", details: str = "",
            status: str | None = None, source: str = "manual",
            chapter: int | None = None) -> Entity:
        if type not in ENTITY_TYPES:
            raise ValueError(f"未知实体类型：{type}")
        name = name.strip()
        if not name:
            raise ValueError("实体名不能为空")
        if self.find(type, name) is not None:
            raise ValueError(f"实体已存在：{TYPE_LABELS[type]}「{name}」")
        appearances = [chapter] if chapter is not None else []
        entity = Entity(
            id=f"{type}-{len(self.entities)}-{name[:16]}",
            type=type, name=name,
            summary=summary.strip(), details=details.strip(),
            status=status or TYPE_STATUS_DEFAULT.get(type, "active"),
            source=source,
            appearances=appearances,
            created_at=_now(), updated_at=_now(),
        )
        self.entities.append(entity)
        self._flush()
        return entity

    def update(self, eid: str, name: str | None = None, summary: str | None = None,
               details: str | None = None, status: str | None = None) -> Entity:
        e = self.get(eid)
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("实体名不能为空")
            dup = self.find(e.type, name)
            if dup is not None and dup.id != eid:
                raise ValueError(f"同名实体已存在：{name}")
            e.name = name
        if summary is not None:
            e.summary = summary.strip()
        if details is not None:
            e.details = details.strip()
        if status is not None:
            if status not in ENTITY_STATUSES:
                raise ValueError(f"非法状态：{status}")
            e.status = status
        e.updated_at = _now()
        self._flush()
        return e

    def delete(self, eid: str) -> None:
        e = self.get(eid)
        self.entities.remove(e)
        self._flush()

    def record_appearance(self, eid: str, chapter: int) -> Entity:
        e = self.get(eid)
        if chapter not in e.appearances:
            e.appearances.append(chapter)
            e.appearances.sort()
            e.updated_at = _now()
            self._flush()
        return e

    # ---- LLM 抽取回灌 ----

    def merge_extracted(self, found: list[dict], chapter: int | None = None) -> dict:
        """抽取结果合并：同类型同名只补空与章节；返回 created/updated/dropped。"""
        created: list[Entity] = []
        updated: list[Entity] = []
        dropped: list[dict] = []
        for item in found:
            if not isinstance(item, dict):
                dropped.append({"raw": item, "reason": "非对象"})
                continue
            etype = str(item.get("type", "")).strip()
            name = str(item.get("name", "")).strip()
            if etype not in ENTITY_TYPES:
                dropped.append({"raw": item, "reason": f"未知类型：{etype}"})
                continue
            if not name:
                dropped.append({"raw": item, "reason": "名为空"})
                continue
            summary = str(item.get("summary", "")).strip()
            details = str(item.get("details", "")).strip()
            existing = self.find(etype, name)
            if existing is None:
                created.append(self.add(etype, name, summary, details,
                                        source="extracted", chapter=chapter))
                continue
            changed = False
            if not existing.summary and summary:
                existing.summary = summary
                changed = True
            if not existing.details and details:
                existing.details = details
                changed = True
            if chapter is not None and chapter not in existing.appearances:
                existing.appearances.append(chapter)
                existing.appearances.sort()
                changed = True
            if changed:
                existing.updated_at = _now()
                updated.append(existing)
        if created or updated:
            self._flush()
        return {
            "created": [e.as_dict() for e in created],
            "updated": [e.as_dict() for e in updated],
            "dropped": dropped,
        }
