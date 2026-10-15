"""实体库（世界状态热板）：角色/地点/道具/情节线/伏笔 + 状态变动链 + 关系图 + 待确认变化。

布局：backend/data/projects/{pid}/entities.json（原子写入）。

M1 新增：
- Entity.state_attrs / state_log：当前状态快照 + 变动历史 id 引用
- StateChange：单次状态变动（id/entity/attr/old/new/cause/chapter/source/confirmed）
- Relation：实体间有向关联边（holds/located_at/knows_about/...）
- ProtagonistState：唐迟热板展示状态（SAN/位置/持有物/已知真相...）
- StateDiff 及子 Suggestion：一次锁文本后 AI 析出的待确认变化清单
- EntityStore 顶层新增 changes/relations/pending_diffs/applied_diffs/protagonist
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

PROTAGONIST_ID = "character-protagonist-tangchi"

# 实体可追踪属性白名单（非强制，允许自定义 attr；用于 UI 聚组 + LLM prompt 稳定）
ENTITY_ATTRS = {
    "character": {
        "location": "当前位置",
        "alive": "生死状态",
        "attitude_to_tangchi": "对唐迟态度",
        "suspicion_level": "对唐迟怀疑度",
        "knowledge_of_supernatural": "对超自然的知情度",
        "sanity": "理智值",
        "physical_state": "身体状态",
        "secret_known": "掌握的秘密",
    },
    "item": {
        "location": "当前位置",
        "holder": "持有者",
        "temperature": "温度/发热程度",
        "activation_state": "激活/触发状态",
        "visibility": "可见性（谁能看到）",
        "condition": "完好度",
    },
    "location": {
        "accessibility": "可进入性",
        "contamination": "污染程度",
        "active_anomaly": "当前活跃异常",
    },
    "foreshadow": {
        "plant_chapter": "埋点章",
        "due_chapter": "预计回收章",
        "payoff_chapter": "实际回收章",
        "intensity": "张力",
    },
    "plotline": {
        "status": "推进状态",
        "pressure": "当前压力值",
    },
}

VALID_REL_TYPES = (
    "holds",
    "located_at",
    "knows_about",
    "suspicious_of",
    "family",
    "acquaintance",
    "friend",
    "hostile",
    "obsessed_by",
    "buried_in",
    "points_to",
    "appears_with",
)

REL_TYPE_LABELS = {
    "holds": "持有",
    "located_at": "位于",
    "knows_about": "知情于",
    "suspicious_of": "怀疑",
    "family": "亲属",
    "acquaintance": "认识",
    "friend": "朋友",
    "hostile": "敌对",
    "obsessed_by": "执念纠缠",
    "buried_in": "埋于",
    "points_to": "指向",
    "appears_with": "伴随出现",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    import secrets
    return secrets.token_hex(6)


# ============================================================
# Dataclasses
# ============================================================

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
    # M1 新增
    state_attrs: dict[str, str] = field(default_factory=dict)
    state_log: list[str] = field(default_factory=list)  # change ids in time order

    def as_dict(self) -> dict:
        return asdict(self)

    def as_summary(self) -> dict:
        return {
            "id": self.id, "type": self.type, "name": self.name,
            "summary": self.summary, "status": self.status,
            "appearances": self.appearances,
            "state_attrs": dict(self.state_attrs),
        }


@dataclass
class StateChange:
    id: str
    entity_id: str
    attr: str
    label: str = ""
    old_value: str = ""
    new_value: str = ""
    cause: str = ""
    field_no: int | None = None
    chapter: int | None = None
    source_msg_id: str | None = None
    source_kind: str = "manual"   # manual | extracted | ai_suggested
    confirmed: bool = True
    created_at: str = ""
    applied_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StateChange":
        return cls(
            id=d.get("id") or _new_id(),
            entity_id=d["entity_id"],
            attr=d.get("attr", ""),
            label=d.get("label", ""),
            old_value=d.get("old_value", "") or "",
            new_value=d.get("new_value", "") or "",
            cause=d.get("cause", "") or "",
            field_no=d.get("field_no"),
            chapter=d.get("chapter"),
            source_msg_id=d.get("source_msg_id"),
            source_kind=d.get("source_kind", "manual"),
            confirmed=bool(d.get("confirmed", True)),
            created_at=d.get("created_at", ""),
            applied_at=d.get("applied_at", ""),
        )


@dataclass
class Relation:
    id: str
    from_id: str
    to_id: str
    rel_type: str
    label: str = ""
    since_field: int | None = None
    since_chapter: int | None = None
    active: bool = True
    end_reason: str = ""
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Relation":
        return cls(
            id=d.get("id") or _new_id(),
            from_id=d["from_id"],
            to_id=d["to_id"],
            rel_type=d.get("rel_type", ""),
            label=d.get("label", "") or "",
            since_field=d.get("since_field"),
            since_chapter=d.get("since_chapter"),
            active=bool(d.get("active", True)),
            end_reason=str(d.get("end_reason", "") or ""),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class ProtagonistState:
    # ---- 当前副本内 ----
    current_body_name: str = "唐迟"
    current_body_identity: str = ""
    current_field_no: int | None = None
    current_day: int | None = None
    current_location: str = ""
    erosion_level: int = 0                 # 本场侵蚀度（临时，杀青结算）
    sanity_hint: str = ""
    held_items: list[str] = field(default_factory=list)           # 本场持有物
    # ---- 永久累积（跨卷保留） ----
    active_crafts: list[dict] = field(default_factory=list)       # 学到的手艺 [{name, from_field, proficiency, note}]
    known_truths: list[str] = field(default_factory=list)         # 关于世界/诸神/自身的认知碎片
    key_relations: list[str] = field(default_factory=list)        # 关系（历史，简短标签）
    dead_echoes: list[dict] = field(default_factory=list)         # 死者残影 [{name, craft_hint, obsession, fade_stage, from_field}]
    god_marks: list[dict] = field(default_factory=list)           # 邪神标记 [{god, seal_form, risk, from_field}]
    brought_items: list[dict] = field(default_factory=list)       # 带回复本的物品 [{name, contam, effect, from_field}]
    blue_star_bonds: list[dict] = field(default_factory=list)     # 蓝星羁绊人物/猫 [{name, relation, depth, note}]
    kill_record: list[str] = field(default_factory=list)          # 杀青账（每场一行，简短）
    contamination_level: int = 0                                  # 累积污染度（0-100，跨卷）
    field_count: int = 0                                          # 已完成副本数
    updated_at: str = ""
    updated_from_chapter: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ProtagonistState":
        if not d:
            return cls()
        return cls(
            current_body_name=d.get("current_body_name", "唐迟"),
            current_body_identity=d.get("current_body_identity", ""),
            current_field_no=d.get("current_field_no"),
            current_day=d.get("current_day"),
            current_location=d.get("current_location", ""),
            erosion_level=int(d.get("erosion_level", 0) or 0),
            sanity_hint=d.get("sanity_hint", ""),
            held_items=list(d.get("held_items", []) or []),
            active_crafts=list(d.get("active_crafts", []) or []),
            known_truths=list(d.get("known_truths", []) or []),
            key_relations=list(d.get("key_relations", []) or []),
            dead_echoes=list(d.get("dead_echoes", []) or []),
            god_marks=list(d.get("god_marks", []) or []),
            brought_items=list(d.get("brought_items", []) or []),
            blue_star_bonds=list(d.get("blue_star_bonds", []) or []),
            kill_record=list(d.get("kill_record", []) or []),
            contamination_level=int(d.get("contamination_level", 0) or 0),
            field_count=int(d.get("field_count", 0) or 0),
            updated_at=d.get("updated_at", ""),
            updated_from_chapter=d.get("updated_from_chapter"),
        )


# ---- StateDiff suggestions ----

@dataclass
class NewEntitySuggestion:
    type: str
    name: str
    summary: str = ""
    details: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "NewEntitySuggestion":
        return cls(type=d.get("type", ""), name=d.get("name", ""),
                   summary=d.get("summary", ""), details=d.get("details", ""))


@dataclass
class StateChangeSuggestion:
    entity_ref: str
    attr: str
    label: str = ""
    old_value: str = ""
    new_value: str = ""
    cause: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "StateChangeSuggestion":
        return cls(
            entity_ref=d.get("entity_ref", ""),
            attr=d.get("attr", ""),
            label=d.get("label", ""),
            old_value=d.get("old_value", "") or "",
            new_value=d.get("new_value", "") or "",
            cause=d.get("cause", "") or "",
        )


@dataclass
class ForeshadowSuggestion:
    name: str
    summary: str = ""
    kind: str = "new"  # new | paid | advanced
    payoff_how: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "ForeshadowSuggestion":
        return cls(name=d.get("name", ""), summary=d.get("summary", ""),
                   kind=d.get("kind", "new"), payoff_how=d.get("payoff_how", ""))


@dataclass
class RippleHint:
    entity_ref: str
    attr: str
    predicted_new: str = ""
    reason: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "RippleHint":
        return cls(entity_ref=d.get("entity_ref", ""), attr=d.get("attr", ""),
                   predicted_new=d.get("predicted_new", ""), reason=d.get("reason", ""))


@dataclass
class StateDiff:
    id: str
    source: str
    source_msg_id: str | None = None
    field_no: int | None = None
    chapter: int | None = None
    created_at: str = ""
    status: str = "pending"  # pending | applied | dismissed
    new_entities: list[NewEntitySuggestion] = field(default_factory=list)
    state_changes: list[StateChangeSuggestion] = field(default_factory=list)
    new_foreshadows: list[ForeshadowSuggestion] = field(default_factory=list)
    foreshadow_payoffs: list[ForeshadowSuggestion] = field(default_factory=list)
    ripple_hints: list[RippleHint] = field(default_factory=list)
    confirmed_selections: dict[str, dict[int, bool]] = field(default_factory=dict)
    note: str = ""
    applied_at: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        # list of dataclasses already serialized by asdict, but asdict recurses; ok
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StateDiff":
        def _lst(cls_, key):
            return [cls_.from_dict(x) for x in (d.get(key) or [])]
        return cls(
            id=d.get("id") or _new_id(),
            source=d.get("source", ""),
            source_msg_id=d.get("source_msg_id"),
            field_no=d.get("field_no"),
            chapter=d.get("chapter"),
            created_at=d.get("created_at", ""),
            status=d.get("status", "pending"),
            new_entities=_lst(NewEntitySuggestion, "new_entities"),
            state_changes=_lst(StateChangeSuggestion, "state_changes"),
            new_foreshadows=_lst(ForeshadowSuggestion, "new_foreshadows"),
            foreshadow_payoffs=_lst(ForeshadowSuggestion, "foreshadow_payoffs"),
            ripple_hints=_lst(RippleHint, "ripple_hints"),
            confirmed_selections=dict(d.get("confirmed_selections", {}) or {}),
            note=d.get("note", "") or "",
            applied_at=d.get("applied_at", ""),
        )


# ============================================================
# Store
# ============================================================

class EntityStore:
    MAX_APPLIED_DIFFS = 20

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entities: list[Entity] = []
        self.changes: dict[str, StateChange] = {}
        self.relations: list[Relation] = []
        self.protagonist: ProtagonistState = ProtagonistState()
        self.pending_diffs: list[StateDiff] = []
        self.applied_diffs: list[StateDiff] = []
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8-sig")  # 自动吃掉 BOM
                data = json.loads(raw)
                self.entities = [Entity(**_entity_from_legacy(d)) for d in data.get("entities", [])]
                for c in data.get("changes", []):
                    try:
                        sc = StateChange.from_dict(c)
                        self.changes[sc.id] = sc
                    except Exception:
                        pass
                for r in data.get("relations", []):
                    try:
                        self.relations.append(Relation.from_dict(r))
                    except Exception:
                        pass
                self.protagonist = ProtagonistState.from_dict(data.get("protagonist"))
                for d in data.get("pending_diffs", []):
                    try:
                        self.pending_diffs.append(StateDiff.from_dict(d))
                    except Exception:
                        pass
                for d in data.get("applied_diffs", []):
                    try:
                        self.applied_diffs.append(StateDiff.from_dict(d))
                    except Exception:
                        pass
            except (json.JSONDecodeError, TypeError, OSError):
                pass

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entities": [e.as_dict() for e in self.entities],
            "changes": [c.as_dict() for c in self.changes.values()],
            "relations": [r.as_dict() for r in self.relations],
            "protagonist": self.protagonist.as_dict(),
            "pending_diffs": [d.as_dict() for d in self.pending_diffs],
            "applied_diffs": [d.as_dict() for d in self.applied_diffs[-self.MAX_APPLIED_DIFFS:]],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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
        if eid == PROTAGONIST_ID:
            raise ValueError("主角状态请通过 protagonist 字段访问，不是普通 Entity")
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

    def neighbors(self, eid: str) -> list[tuple[Relation, Entity]]:
        """返回一个实体的所有活跃关联+对方实体（双向）。主角 eid 也支持。"""
        out: list[tuple[Relation, Entity]] = []
        for r in self.relations:
            if not r.active:
                continue
            other_id: str | None = None
            if r.from_id == eid:
                other_id = r.to_id
            elif r.to_id == eid:
                other_id = r.from_id
            if other_id is None:
                continue
            if other_id == PROTAGONIST_ID:
                # 主角无 Entity 对象，跳过；调用方自行处理主角情况
                continue
            try:
                other = self.get(other_id)
                out.append((r, other))
            except ValueError:
                continue
        return out

    def changes_for(self, eid: str, *, confirmed_only: bool = True) -> list[StateChange]:
        out = [self.changes[cid] for cid in
               (self._eid_log(eid)) if cid in self.changes]
        if confirmed_only:
            out = [c for c in out if c.confirmed]
        out.sort(key=lambda c: c.created_at)
        return out

    def _eid_log(self, eid: str) -> list[str]:
        if eid == PROTAGONIST_ID:
            # 主角单独收集：找 changes 里 entity_id == PROTAGONIST_ID 的全部 id
            return [c.id for c in self.changes.values() if c.entity_id == PROTAGONIST_ID]
        e = self.get(eid)
        return list(e.state_log)

    def current_attr(self, eid: str, attr: str) -> str | None:
        if eid == PROTAGONIST_ID:
            # 主角 state_attrs 等价于 protagonist 字段的扁平展示
            mapping = {
                "location": self.protagonist.current_location,
                "body": self.protagonist.current_body_name,
                "identity": self.protagonist.current_body_identity,
                "erosion": str(self.protagonist.erosion_level),
                "sanity": self.protagonist.sanity_hint,
                "contamination": str(self.protagonist.contamination_level),
                "field_count": str(self.protagonist.field_count),
            }
            return mapping.get(attr)
        e = self.get(eid)
        return e.state_attrs.get(attr)

    def chapter_snapshot(self, chapter: int) -> dict:
        """返回截至某章开始时的实体快照（用于 prompt 注入 / 开章任务书 / 侧边面板）。"""
        entities_snap = []
        for e in self.entities:
            # 仅出现在 chapter 之前（含本章已出现）的实体
            relevant_appearances = [c for c in e.appearances if c <= chapter]
            if not relevant_appearances and chapter > 0:
                # 仍纳入 active 实体（防止新建实体未标 appearances）
                if e.status not in ("active", "planted"):
                    continue
            attrs_until = {}
            for cid in e.state_log:
                c = self.changes.get(cid)
                if not c or not c.confirmed:
                    continue
                if c.chapter is not None and c.chapter > chapter:
                    continue
                attrs_until[c.attr] = c.new_value
            entities_snap.append({
                "id": e.id, "type": e.type, "name": e.name,
                "summary": e.summary, "status": e.status,
                "appearances": relevant_appearances,
                "state_attrs": attrs_until,
            })
        foreshadows = [e for e in entities_snap if e["type"] == "foreshadow"
                       and e["status"] == "planted"]
        recent_changes = []
        for c in sorted(self.changes.values(), key=lambda x: x.created_at):
            if not c.confirmed:
                continue
            if c.chapter is not None and c.chapter >= chapter - 1:
                recent_changes.append({
                    "entity_id": c.entity_id, "attr": c.attr,
                    "old": c.old_value, "new": c.new_value,
                    "cause": c.cause, "chapter": c.chapter,
                })
        return {
            "chapter": chapter,
            "protagonist": self.protagonist.as_dict(),
            "entities": entities_snap,
            "active_foreshadows": foreshadows,
            "recent_changes": recent_changes[-15:],
            "relations": [r.as_dict() for r in self.relations if r.active],
        }

    # ---- 写入 ----

    def add(self, type: str, name: str, summary: str = "", details: str = "",
            status: str | None = None, source: str = "manual",
            chapter: int | None = None,
            state_attrs: dict[str, str] | None = None) -> Entity:
        if type not in ENTITY_TYPES:
            raise ValueError(f"未知实体类型：{type}")
        name = name.strip()
        if not name:
            raise ValueError("实体名不能为空")
        if self.find(type, name) is not None:
            raise ValueError(f"实体已存在：{TYPE_LABELS[type]}「{name}」")
        appearances = [chapter] if chapter is not None else []
        now = _now()
        entity = Entity(
            id=f"{type}-{len(self.entities)}-{name[:16]}",
            type=type, name=name,
            summary=summary.strip(), details=details.strip(),
            status=status or TYPE_STATUS_DEFAULT.get(type, "active"),
            source=source,
            appearances=appearances,
            state_attrs=dict(state_attrs or {}),
            created_at=now, updated_at=now,
        )
        self.entities.append(entity)
        self._flush()
        return entity

    def update(self, eid: str, name: str | None = None, summary: str | None = None,
               details: str | None = None, status: str | None = None,
               state_attrs: dict[str, str] | None = None) -> Entity:
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
        if state_attrs:
            for k, v in state_attrs.items():
                if v is None or v == "":
                    e.state_attrs.pop(k, None)
                else:
                    e.state_attrs[k] = v
        e.updated_at = _now()
        self._flush()
        return e

    def delete(self, eid: str) -> None:
        e = self.get(eid)
        self.entities.remove(e)
        # 删除相关 changes 和 relations
        self.changes = {cid: c for cid, c in self.changes.items() if c.entity_id != eid}
        self.relations = [r for r in self.relations if r.from_id != eid and r.to_id != eid]
        self._flush()

    def record_appearance(self, eid: str, chapter: int) -> Entity:
        e = self.get(eid)
        if chapter not in e.appearances:
            e.appearances.append(chapter)
            e.appearances.sort()
            e.updated_at = _now()
            self._flush()
        return e

    def add_change(self, change: StateChange) -> StateChange:
        """新增状态变动；confirmed=True 时自动更新 state_attrs 和 appearances。"""
        if not change.id:
            change.id = _new_id()
        if not change.created_at:
            change.created_at = _now()
        if change.confirmed and not change.applied_at:
            change.applied_at = _now()
        self.changes[change.id] = change
        if change.confirmed:
            self._apply_change_to_entity(change)
        self._flush()
        return change

    def confirm_change(self, change_id: str) -> StateChange:
        c = self.changes.get(change_id)
        if c is None:
            raise ValueError(f"变动不存在：{change_id}")
        if not c.confirmed:
            c.confirmed = True
            c.applied_at = _now()
            self._apply_change_to_entity(c)
            self._flush()
        return c

    def _apply_change_to_entity(self, c: StateChange) -> None:
        if c.entity_id == PROTAGONIST_ID:
            # 主角状态映射：常见 attr 直接改 ProtagonistState，其余走 update_protagonist
            if c.attr == "location":
                self.protagonist.current_location = c.new_value
            elif c.attr == "body":
                self.protagonist.current_body_name = c.new_value
            elif c.attr == "identity":
                self.protagonist.current_body_identity = c.new_value
            elif c.attr == "erosion":
                try:
                    self.protagonist.erosion_level = int(c.new_value)
                except (TypeError, ValueError):
                    pass
            elif c.attr == "sanity":
                self.protagonist.sanity_hint = c.new_value
            elif c.attr == "contamination":
                try:
                    self.protagonist.contamination_level = int(c.new_value)
                except (TypeError, ValueError):
                    pass
            elif c.attr == "field_count":
                try:
                    self.protagonist.field_count = int(c.new_value)
                except (TypeError, ValueError):
                    pass
            if c.chapter is not None:
                self.protagonist.updated_from_chapter = c.chapter
            self.protagonist.updated_at = c.applied_at or _now()
            return
        try:
            e = self.get(c.entity_id)
        except ValueError:
            return
        if c.attr:
            if c.new_value == "":
                e.state_attrs.pop(c.attr, None)
            else:
                e.state_attrs[c.attr] = c.new_value
        if c.id not in e.state_log:
            e.state_log.append(c.id)
        if c.chapter is not None and c.chapter not in e.appearances:
            e.appearances.append(c.chapter)
            e.appearances.sort()
        e.updated_at = c.applied_at or _now()

    def add_relation(self, relation: Relation) -> Relation:
        if not relation.id:
            relation.id = _new_id()
        if not relation.created_at:
            relation.created_at = _now()
        if not relation.updated_at:
            relation.updated_at = relation.created_at
        # 检查重复
        for r in self.relations:
            if (r.from_id == relation.from_id and r.to_id == relation.to_id
                    and r.rel_type == relation.rel_type and r.active == relation.active):
                return r
        self.relations.append(relation)
        self._flush()
        return relation

    def deactivate_relation(self, rel_id: str, end_reason: str = "") -> Relation:
        for r in self.relations:
            if r.id == rel_id:
                r.active = False
                r.end_reason = end_reason
                r.updated_at = _now()
                self._flush()
                return r
        raise ValueError(f"关系不存在：{rel_id}")

    def update_protagonist(self, **kwargs) -> ProtagonistState:
        for k, v in kwargs.items():
            if hasattr(self.protagonist, k) and v is not None:
                setattr(self.protagonist, k, v)
        self.protagonist.updated_at = _now()
        self._flush()
        return self.protagonist

    # ---- 杀青自动归账（M3 第三步） ----

    @staticmethod
    def _fuzzy_match_name(target: str, items: list[dict]) -> int | None:
        """按 name 子串模糊匹配，返回下标；找不到返回 None。"""
        if not target:
            return None
        t = target.strip()
        # 先精确
        for i, it in enumerate(items):
            if str(it.get("name", "")).strip() == t:
                return i
        # 再子串
        for i, it in enumerate(items):
            n = str(it.get("name", "")).strip()
            if n and (n in t or t in n):
                return i
        return None

    _PROFICIENCY_RANK = {"初窥": 1, "小成": 2, "精通": 3, "大成": 4}
    _FADE_RANK = {"新鲜": 1, "淡去": 2, "快散": 3}

    @classmethod
    def _bump_proficiency(cls, cur: str, incoming: str) -> str:
        """手艺熟练度取更高者。"""
        a = cls._PROFICIENCY_RANK.get(cur, 1)
        b = cls._PROFICIENCY_RANK.get(incoming, 1)
        return incoming if b >= a else cur

    @classmethod
    def _bump_fade(cls, cur: str, incoming: str) -> str:
        """残影消退度：新残影覆盖旧消退（重新变"新鲜"）。"""
        # 新来了同名残影 → 执念重新变新鲜
        return incoming or cur or "新鲜"

    def apply_settle_diff(self, diff: dict, field_no: int) -> dict:
        """把杀青归账 diff 应用到 protagonist 状态；返回实际写入的摘要。
        已存在的 craft/echo 按 name 模糊匹配，加深而不是重复追加。
        """
        if not isinstance(diff, dict):
            raise ValueError("diff 必须是 dict")
        p = self.protagonist
        fn = diff.get("from_field") or field_no
        fn_str = str(fn) if fn is not None else ""
        summary = {
            "crafts_added": [], "crafts_deepened": [],
            "echoes_added": [], "echoes_refreshed": [],
            "god_marks_added": [],
            "brought_items_added": [],
            "bonds_added": [], "bonds_deepened": [],
            "contamination_before": p.contamination_level,
            "contamination_after": p.contamination_level,
            "field_count_before": p.field_count,
            "field_count_after": p.field_count,
            "kill_record_appended": None,
        }

        # crafts
        for c in diff.get("crafts", []) or []:
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            prof = str(c.get("proficiency", "初窥") or "初窥").strip()
            idx = self._fuzzy_match_name(name, p.active_crafts)
            if idx is None:
                p.active_crafts.append({
                    "name": name, "proficiency": prof or "初窥",
                    "from_field": str(c.get("from_field") or fn_str),
                    "note": str(c.get("note", "") or ""),
                })
                summary["crafts_added"].append(name)
            else:
                existing = p.active_crafts[idx]
                new_prof = self._bump_proficiency(
                    str(existing.get("proficiency", "初窥")), prof)
                existing["proficiency"] = new_prof
                summary["crafts_deepened"].append(name)

        # echoes（残影）
        for e in diff.get("echoes", []) or []:
            name = str(e.get("name", "")).strip()
            if not name:
                continue
            idx = self._fuzzy_match_name(name, p.dead_echoes)
            if idx is None:
                p.dead_echoes.append({
                    "name": name,
                    "craft_hint": str(e.get("craft_hint", "") or ""),
                    "obsession": str(e.get("obsession", "") or ""),
                    "fade_stage": str(e.get("fade_stage", "新鲜") or "新鲜"),
                    "from_field": str(e.get("from_field") or fn_str),
                })
                summary["echoes_added"].append(name)
            else:
                existing = p.dead_echoes[idx]
                existing["fade_stage"] = self._bump_fade(
                    str(existing.get("fade_stage", "淡去")),
                    str(e.get("fade_stage", "新鲜") or "新鲜"))
                # 补强 craft_hint/obsession（有新文本就覆盖）
                if e.get("craft_hint"):
                    existing["craft_hint"] = str(e["craft_hint"])
                if e.get("obsession"):
                    existing["obsession"] = str(e["obsession"])
                summary["echoes_refreshed"].append(name)

        # god_marks（邪神标记）：同名同 god 则更新 risk，否则追加
        for m in diff.get("god_marks", []) or []:
            god = str(m.get("god", "")).strip()
            seal = str(m.get("seal_form", "")).strip()
            if not god and not seal:
                continue
            existing_idx = None
            for i, gm in enumerate(p.god_marks):
                if god and str(gm.get("god", "")).strip() == god:
                    existing_idx = i
                    break
                if seal and str(gm.get("seal_form", "")).strip() == seal:
                    existing_idx = i
                    break
            if existing_idx is None:
                p.god_marks.append({
                    "god": god,
                    "seal_form": seal,
                    "risk": str(m.get("risk", "") or ""),
                    "from_field": str(m.get("from_field") or fn_str),
                })
                summary["god_marks_added"].append(god or seal)
            else:
                if m.get("risk"):
                    p.god_marks[existing_idx]["risk"] = str(m["risk"])

        # brought_items
        for b in diff.get("brought_items", []) or []:
            name = str(b.get("name", "")).strip()
            if not name:
                continue
            try:
                contam = int(b.get("contam", 0) or 0)
            except (TypeError, ValueError):
                contam = 0
            p.brought_items.append({
                "name": name,
                "contam": max(0, min(10, contam)),
                "effect": str(b.get("effect", "") or ""),
                "from_field": str(b.get("from_field") or fn_str),
            })
            summary["brought_items_added"].append(name)

        # bonds_delta
        for bd in diff.get("bonds_delta", []) or []:
            name = str(bd.get("name", "")).strip()
            if not name:
                continue
            relation = str(bd.get("relation", "") or "")
            depth = str(bd.get("depth", "浅") or "浅")
            note = str(bd.get("note", "") or "")
            idx = self._fuzzy_match_name(name, p.blue_star_bonds)
            if idx is None:
                p.blue_star_bonds.append({
                    "name": name, "relation": relation, "depth": depth, "note": note,
                })
                summary["bonds_added"].append(name)
            else:
                existing = p.blue_star_bonds[idx]
                if relation:
                    existing["relation"] = relation
                # depth 若更深则更新：浅<中<深
                depth_rank = {"浅": 1, "中": 2, "深": 3}
                if depth_rank.get(depth, 1) > depth_rank.get(existing.get("depth", "浅"), 1):
                    existing["depth"] = depth
                if note:
                    existing["note"] = (existing.get("note", "") + " / " + note).strip(" /")
                summary["bonds_deepened"].append(name)

        # contamination_delta
        try:
            cd = int(diff.get("contamination_delta", 0) or 0)
        except (TypeError, ValueError):
            cd = 0
        p.contamination_level = max(0, min(100, p.contamination_level + max(0, cd)))
        summary["contamination_after"] = p.contamination_level

        # field_count_inc
        try:
            fci = int(diff.get("field_count_inc", 1) or 1)
        except (TypeError, ValueError):
            fci = 1
        p.field_count += max(0, fci)
        summary["field_count_after"] = p.field_count

        # kill_record append
        line = str(diff.get("kill_record_line", "") or "").strip()
        if line:
            p.kill_record.append(line)
            summary["kill_record_appended"] = line

        p.updated_at = _now()
        self._flush()
        return summary

    def apply_diff(self, diff_id: str,
                   selections: dict[str, dict[int, bool]]) -> dict:
        diff = next((d for d in self.pending_diffs if d.id == diff_id), None)
        if diff is None:
            raise ValueError(f"待确认变更不存在：{diff_id}")
        diff.confirmed_selections = selections or {}
        created: list[dict] = []
        changed: list[dict] = []
        paid: list[dict] = []

        def _selected(bucket: str, idx: int) -> bool:
            bucket_map = diff.confirmed_selections.get(bucket, {}) or {}
            return bool(bucket_map.get(idx, bucket_map.get(str(idx), False)))

        # 新实体
        for i, ne in enumerate(diff.new_entities):
            if not _selected("new_entities", i):
                continue
            if ne.type not in ENTITY_TYPES or not ne.name:
                continue
            if self.find(ne.type, ne.name) is not None:
                continue
            e = self.add(ne.type, ne.name, ne.summary, ne.details,
                         source="extracted", chapter=diff.chapter)
            created.append(e.as_summary())

        # 状态变化
        for i, sc in enumerate(diff.state_changes):
            if not _selected("state_changes", i):
                continue
            eid = sc.entity_ref
            if eid.startswith("new:"):
                # 引用同 diff 里的新实体，按 name 匹配（简化处理）
                name = eid.split("new:", 1)[1].strip()
                found = self.find("character", name) or self.find("item", name) \
                    or self.find("location", name) or self.find("plotline", name) \
                    or self.find("foreshadow", name)
                if found is None:
                    continue
                eid = found.id
            elif eid != PROTAGONIST_ID:
                # 非主角：必须存在，否则 skip（防止 AI 幻觉把变更写到幽灵实体）
                try:
                    self.get(eid)
                except ValueError:
                    continue
            ch = StateChange(
                id=_new_id(), entity_id=eid, attr=sc.attr, label=sc.label,
                old_value=sc.old_value, new_value=sc.new_value, cause=sc.cause,
                field_no=diff.field_no, chapter=diff.chapter,
                source_msg_id=diff.source_msg_id, source_kind="extracted",
                confirmed=True, created_at=_now(), applied_at=_now(),
            )
            self.changes[ch.id] = ch
            self._apply_change_to_entity(ch)
            changed.append(ch.as_dict())

        # 新伏笔
        for i, nf in enumerate(diff.new_foreshadows):
            if not _selected("new_foreshadows", i):
                continue
            if not nf.name:
                continue
            if self.find("foreshadow", nf.name) is None:
                e = self.add("foreshadow", nf.name, summary=nf.summary,
                             source="extracted", chapter=diff.chapter)
                created.append(e.as_summary())

        # 伏笔回收
        for i, pf in enumerate(diff.foreshadow_payoffs):
            if not _selected("foreshadow_payoffs", i):
                continue
            e = self.find("foreshadow", pf.name)
            if e is not None:
                e.status = "paid"
                e.updated_at = _now()
                if diff.chapter is not None and diff.chapter not in e.appearances:
                    e.appearances.append(diff.chapter)
                    e.appearances.sort()
                paid.append(e.as_summary())

        # 涟漪（采纳后变成 ai_suggested 的状态变化）
        for i, rh in enumerate(diff.ripple_hints):
            if not _selected("ripple_hints", i):
                continue
            eid = rh.entity_ref
            if eid.startswith("new:"):
                continue
            ch = StateChange(
                id=_new_id(), entity_id=eid, attr=rh.attr,
                new_value=rh.predicted_new, cause=rh.reason,
                field_no=diff.field_no, chapter=diff.chapter,
                source_msg_id=diff.source_msg_id, source_kind="ai_suggested",
                confirmed=True, created_at=_now(), applied_at=_now(),
            )
            self.changes[ch.id] = ch
            self._apply_change_to_entity(ch)
            changed.append(ch.as_dict())

        diff.status = "applied"
        diff.applied_at = _now()
        self.pending_diffs = [d for d in self.pending_diffs if d.id != diff_id]
        self.applied_diffs.append(diff)
        if len(self.applied_diffs) > self.MAX_APPLIED_DIFFS:
            self.applied_diffs = self.applied_diffs[-self.MAX_APPLIED_DIFFS:]
        self._flush()
        return {"created": created, "changed": changed, "paid": paid,
                "remaining_pending": len(self.pending_diffs)}

    def dismiss_diff(self, diff_id: str) -> None:
        diff = next((d for d in self.pending_diffs if d.id == diff_id), None)
        if diff is None:
            return
        diff.status = "dismissed"
        self.pending_diffs = [d for d in self.pending_diffs if d.id != diff_id]
        self.applied_diffs.append(diff)
        if len(self.applied_diffs) > self.MAX_APPLIED_DIFFS:
            self.applied_diffs = self.applied_diffs[-self.MAX_APPLIED_DIFFS:]
        self._flush()

    def add_pending_diff(self, diff: StateDiff) -> StateDiff:
        if not diff.id:
            diff.id = _new_id()
        if not diff.created_at:
            diff.created_at = _now()
        self.pending_diffs.append(diff)
        self._flush()
        return diff

    # ---- LLM 抽取回灌（保留旧接口，兼容现有调用） ----

    def merge_extracted(self, found: list[dict], chapter: int | None = None) -> dict:
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


# ---- 兼容：旧 entities.json 里 entity 没有 state_attrs/state_log 字段，
# dataclass 默认会报错（因为我们加了非 default 字段在 default 字段之后其实没问题，
# 但这里写个 helper 容错） ----

def _entity_from_legacy(d: dict) -> dict:
    """确保旧 entity dict 补齐 M1 新字段，喂给 Entity(**d) 不报错。"""
    d = dict(d)
    d.setdefault("state_attrs", {})
    d.setdefault("state_log", [])
    return d
