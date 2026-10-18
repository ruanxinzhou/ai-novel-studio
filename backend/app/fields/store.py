"""场（Field）存储：卷→场→日→章 四层结构的"场"层。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

FIELD_STATUSES = (
    "planned",        # 已建讣告卡，未开写
    "adapting",       # ①剧本改编中
    "simming",        # ②COC跑团推演中
    "outing",         # ③唐迟大纲替换中
    "splitting",      # ④分章命名
    "writing",        # ⑤⑥章细纲+正文创作中
    "epilogue",       # ⑦散场（残页/杀青）
    "interlude",      # ⑧现实间章创作中
    "finalized",      # 戏里+间章全部定稿
)

DRAMA_STATUSES = ("planned", "adapting", "simming", "outing", "splitting", "writing", "finalized")
INTERLUDE_STATUSES = ("planned", "outlining", "writing", "finalized")

VALID_REPLACED_ROLES = ("PL", "NPC", "BOSS", "幕后黑手")

# 回转寿司各阶段卡片 key
SCAFFOLD_CARD_KEYS = (
    "obituary_card",
    "entity_card",
    "truth_outline",
    "tangchi_start",
    "core_twist",
    "foreshadows",
    "cost_cap",
)
SETTLE_CARD_KEYS = (
    "wrapup",
)
WALKTHROUGH_CARD_KEY = "walkthrough"
SCHEDULE_CARD_KEY = "chapter_plan"
ALL_CARD_KEYS = SCAFFOLD_CARD_KEYS + SETTLE_CARD_KEYS + (WALKTHROUGH_CARD_KEY, SCHEDULE_CARD_KEY, "core")

VALID_WORLD_TYPES = (
    "克苏鲁小镇", "科幻空间站", "古风玄幻", "民国怪谈",
    "现代都市诡异", "西方奇幻", "日式怪谈", "文学同人", "影视同人",
    "其他",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Obituary:
    """讣告卡——每场的叙事路由器。"""
    deceased_name: str = ""              # 死者姓名（附身对象）
    deceased_identity: str = ""          # 死者身份
    cause_of_death: str = ""             # 死因（文字描述；空=尚未知）
    cause_blacked: bool = False          # 死因是否涂黑（读者/主角都不知道）
    seventh_day_death: str = ""          # T-0（入戏第七日）死法/死劫兑现方式
    obsession: str = ""                  # 执念
    obsession_power: str = ""            # 执念金手指（能力描述）
    obsession_cost: str = ""             # 人间副作用
    day_beats_override: dict | None = None  # 本场特化唐迟入戏日节点（None=用全局 T-6→T-0 基线）
    remaining_page_ash: str = ""         # 残页灰文（散场时填）
    cast_replaced_role: str = "PL"       # 唐迟替换角色：PL/NPC/BOSS/幕后黑手
    finale_record_template: str = ""     # 杀青一行模板

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Obituary":
        if not d:
            return cls()
        role = str(d.get("cast_replaced_role", "PL") or "PL")
        if role not in VALID_REPLACED_ROLES:
            role = "PL"
        return cls(
            deceased_name=str(d.get("deceased_name", "") or ""),
            deceased_identity=str(d.get("deceased_identity", "") or ""),
            cause_of_death=str(d.get("cause_of_death", "") or ""),
            cause_blacked=bool(d.get("cause_blacked", False)),
            seventh_day_death=str(d.get("seventh_day_death", "") or ""),
            obsession=str(d.get("obsession", "") or ""),
            obsession_power=str(d.get("obsession_power", "") or ""),
            obsession_cost=str(d.get("obsession_cost", "") or ""),
            day_beats_override=d.get("day_beats_override"),
            remaining_page_ash=str(d.get("remaining_page_ash", "") or ""),
            cast_replaced_role=role,
            finale_record_template=str(d.get("finale_record_template", "") or ""),
        )

    def render(self, field_no: int) -> str:
        """本场讣告渲染，作为任务书注入。"""
        lines = [f"## 第{field_no}场·讣告卡\n"]
        if self.deceased_name:
            lines.append(f"**死者**：{self.deceased_name}（{self.deceased_identity or '身份待补'}）")
        if self.cause_blacked:
            lines.append(f"**死因**：■■■■（涂黑）")
        elif self.cause_of_death:
            lines.append(f"**死因**：{self.cause_of_death}")
        if self.seventh_day_death:
            lines.append(f"**第七日死法**：{self.seventh_day_death}")
        lines.append(f"**唐迟替换角色**：{self.cast_replaced_role}")
        if self.obsession:
            lines.append(f"**执念**：{self.obsession}")
        if self.obsession_power:
            lines.append(f"**执念金手指**：{self.obsession_power}")
        if self.obsession_cost:
            lines.append(f"**执念代价**：{self.obsession_cost}（写作时必须安排副作用兑现）")
        return "\n".join(lines) + "\n"


@dataclass
class DayWork:
    day: int
    summary: str = ""
    required_hook: str = ""
    chapters: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DayWork":
        return cls(
            day=int(d.get("day", 1)),
            summary=str(d.get("summary", "") or ""),
            required_hook=str(d.get("required_hook", "") or ""),
            chapters=[int(c) for c in (d.get("chapters") or [])],
        )


@dataclass
class CardVersion:
    """卡片的一个锁定版本。"""
    text: str
    at: str
    source_msg_id: str | None = None
    note: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "CardVersion":
        return cls(
            text=str(d.get("text", "") or ""),
            at=str(d.get("at", "") or ""),
            source_msg_id=d.get("source_msg_id"),
            note=str(d.get("note", "") or ""),
        )


@dataclass
class CardLock:
    """单张卡片的锁定状态+版本历史。"""
    key: str
    text: str = ""
    locked: bool = False
    locked_at: str = ""
    locked_by_msg_id: str | None = None
    versions: list[CardVersion] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "text": self.text,
            "locked": self.locked,
            "locked_at": self.locked_at,
            "locked_by_msg_id": self.locked_by_msg_id,
            "versions": [v.as_dict() for v in self.versions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CardLock":
        return cls(
            key=str(d.get("key", "") or ""),
            text=str(d.get("text", "") or ""),
            locked=bool(d.get("locked", False)),
            locked_at=str(d.get("locked_at", "") or ""),
            locked_by_msg_id=d.get("locked_by_msg_id"),
            versions=[CardVersion.from_dict(v) for v in (d.get("versions") or [])],
        )


@dataclass
class FieldWork:
    no: int
    title: str = ""
    volume_no: int = 1
    world_type: str = "克苏鲁小镇"
    world_premise: str = ""
    obituary: Obituary = field(default_factory=Obituary)
    drama_status: str = "planned"
    interlude_status: str = "planned"
    days: dict[int, DayWork] = field(default_factory=dict)
    interlude_chapters: list[int] = field(default_factory=list)
    expected_load: dict = field(default_factory=dict)
    actual_load: dict = field(default_factory=dict)
    source_materials: dict = field(default_factory=dict)
    # 候选池存档（①adapt/②sim/③outline/⑧interlude_outline）
    variants: dict[str, list[dict]] = field(default_factory=dict)
    chosen_variant: dict[str, str] = field(default_factory=dict)  # node_type -> vid
    created_at: str = ""
    finalized_at: str = ""
    interlude_finalized_at: str = ""
    # M1 新增：回转寿司产物（纯文本锁版本）
    core: str = ""
    cards: dict[str, CardLock] = field(default_factory=dict)
    walkthrough_log: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["obituary"] = self.obituary.as_dict()
        d["days"] = {str(k): v.as_dict() if hasattr(v, "as_dict") else v for k, v in self.days.items()}
        d["cards"] = {k: v.as_dict() for k, v in self.cards.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FieldWork":
        days_raw = d.get("days") or {}
        days = {}
        for k, v in days_raw.items():
            if isinstance(v, dict):
                days[int(k)] = DayWork.from_dict(v)
        wt = str(d.get("world_type", "克苏鲁小镇") or "克苏鲁小镇")
        if wt not in VALID_WORLD_TYPES:
            wt = "其他"
        ds = str(d.get("drama_status", "planned") or "planned")
        if ds not in DRAMA_STATUSES:
            ds = "planned"
        its = str(d.get("interlude_status", "planned") or "planned")
        if its not in INTERLUDE_STATUSES:
            its = "planned"
        return cls(
            no=int(d["no"]),
            title=str(d.get("title", "") or ""),
            volume_no=int(d.get("volume_no", 1) or 1),
            world_type=wt,
            world_premise=str(d.get("world_premise", "") or ""),
            obituary=Obituary.from_dict(d.get("obituary")),
            drama_status=ds,
            interlude_status=its,
            days=days,
            interlude_chapters=[int(c) for c in (d.get("interlude_chapters") or [])],
            expected_load=dict(d.get("expected_load") or {}),
            actual_load=dict(d.get("actual_load") or {}),
            source_materials=dict(d.get("source_materials") or {}),
            variants={k: list(v) for k, v in (d.get("variants") or {}).items()},
            chosen_variant=dict(d.get("chosen_variant") or {}),
            created_at=str(d.get("created_at", "") or ""),
            finalized_at=str(d.get("finalized_at", "") or ""),
            interlude_finalized_at=str(d.get("interlude_finalized_at", "") or ""),
            core=str(d.get("core", "") or ""),
            cards={k: CardLock.from_dict({**v, "key": k}) for k, v in (d.get("cards") or {}).items()},
            walkthrough_log=list(d.get("walkthrough_log") or []),
        )

    def ensure_days(self) -> None:
        """补齐唐迟入戏第1-7天（T-6→T-0）空 DayWork（如果不存在）。"""
        for d in range(1, 8):
            if d not in self.days:
                self.days[d] = DayWork(day=d)

    def chapter_day(self, chapter_no: int) -> int | None:
        """返回章号所属 day（None=间章或未分配）。"""
        if chapter_no in self.interlude_chapters:
            return None
        for day, dw in self.days.items():
            if chapter_no in dw.chapters:
                return day
        return None


class FieldStore:
    """场存储：负责 fields/index.json 和 fields/{no}.json 原子读写。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._cache: dict[int, FieldWork] = {}

    # ---- 索引 ----

    def _load_index(self) -> dict:
        if not self._index_path.exists():
            return {"fields": []}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _save_index(self, idx: dict) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    # ---- 单场文件 ----

    def _path(self, no: int) -> Path:
        return self.root / f"{no}.json"

    def _load(self, no: int) -> FieldWork | None:
        if no in self._cache:
            return self._cache[no]
        p = self._path(no)
        if not p.exists():
            return None
        fw = FieldWork.from_dict(json.loads(p.read_text(encoding="utf-8")))
        self._cache[no] = fw
        return fw

    def _flush(self, fw: FieldWork) -> None:
        p = self._path(fw.no)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(fw.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        self._cache[fw.no] = fw
        # 更新索引
        idx = self._load_index()
        entries = [e for e in idx["fields"] if e["no"] != fw.no]
        entries.append({
            "no": fw.no, "title": fw.title, "volume_no": fw.volume_no,
            "world_type": fw.world_type,
            "drama_status": fw.drama_status, "interlude_status": fw.interlude_status,
        })
        entries.sort(key=lambda e: e["no"])
        idx["fields"] = entries
        self._save_index(idx)

    # ---- CRUD ----

    def list_all(self) -> list[dict]:
        return list(self._load_index()["fields"])

    def all_nos(self) -> list[int]:
        return [e["no"] for e in self.list_all()]

    def get(self, no: int) -> FieldWork | None:
        return self._load(no)

    def create(self, title: str = "", volume_no: int = 1, world_type: str = "克苏鲁小镇",
               no: int | None = None) -> FieldWork:
        """新建一场。no 不传则自动取下一号；传了则用指定编号（若已存在会抛错）。"""
        existing = self.all_nos()
        if no is None:
            no = (max(existing) + 1) if existing else 1
        elif no in existing:
            raise ValueError(f"场 {no} 已存在")
        fw = FieldWork(
            no=no, title=title or f"第{no}场", volume_no=volume_no,
            world_type=world_type, created_at=_now(),
        )
        fw.ensure_days()
        self._flush(fw)
        return fw

    def update(self, no: int, **kwargs) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        for k, v in kwargs.items():
            if k == "obituary" and isinstance(v, dict):
                fw.obituary = Obituary.from_dict(v)
            elif k == "drama_status" and v in DRAMA_STATUSES:
                fw.drama_status = v
            elif k == "interlude_status" and v in INTERLUDE_STATUSES:
                fw.interlude_status = v
            elif k == "world_type" and v in VALID_WORLD_TYPES:
                fw.world_type = v
            elif hasattr(fw, k):
                setattr(fw, k, v)
        self._flush(fw)
        return fw

    def ensure_day(self, no: int, day: int, summary: str = "", required_hook: str = "") -> DayWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.ensure_days()
        dw = fw.days[day]
        if summary:
            dw.summary = summary
        if required_hook:
            dw.required_hook = required_hook
        self._flush(fw)
        return dw

    def assign_chapters_to_day(self, no: int, day: int, chapters: list[int]) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.ensure_days()
        # 先从其他 Day 里移除这些章
        for d, dw in fw.days.items():
            dw.chapters = [c for c in dw.chapters if c not in chapters]
        fw.interlude_chapters = [c for c in fw.interlude_chapters if c not in chapters]
        fw.days[day].chapters = chapters
        self._flush(fw)
        return fw

    def assign_interlude_chapters(self, no: int, chapters: list[int]) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        # 从各 Day 里移除
        for dw in fw.days.values():
            dw.chapters = [c for c in dw.chapters if c not in chapters]
        fw.interlude_chapters = chapters
        self._flush(fw)
        return fw

    def mark_finalized(self, no: int) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.finalized_at = _now()
        fw.drama_status = "finalized"
        self._flush(fw)
        return fw

    def mark_interlude_finalized(self, no: int) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.interlude_finalized_at = _now()
        fw.interlude_status = "finalized"
        # 如果戏里也定稿了，整个场标记为 finalized
        if fw.drama_status == "finalized":
            fw.finalized_at = fw.finalized_at or _now()
        self._flush(fw)
        return fw

    # ---- 候选池 ----

    def add_variants(self, no: int, node_type: str, variants: list[dict]) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.variants.setdefault(node_type, []).extend(variants)
        self._flush(fw)
        return fw

    def choose_variant(self, no: int, node_type: str, vid: str) -> FieldWork:
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.chosen_variant[node_type] = vid
        # 把其他候选 chosen=False，选中那个 True
        for v in fw.variants.get(node_type, []):
            v["chosen"] = (v.get("vid") == vid)
        self._flush(fw)
        return fw

    def chosen_variant_text(self, no: int, node_type: str) -> str | None:
        fw = self._load(no)
        if fw is None:
            return None
        vid = fw.chosen_variant.get(node_type)
        if not vid:
            return None
        for v in fw.variants.get(node_type, []):
            if v.get("vid") == vid:
                return v.get("text", "")
        return None

    # ---- 回转寿司卡锁 ----

    def lock_card(self, no: int, key: str, text: str,
                  source_msg_id: str | None = None, note: str = "") -> CardLock:
        """锁一张卡（新建或追加版本）。最多保留最近 5 版。"""
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        now = _now()
        card = fw.cards.get(key)
        if card is None:
            card = CardLock(key=key)
            fw.cards[key] = card
        # 当前文本推入版本历史（若与新文本不同）
        if card.text and card.text != text:
            card.versions.append(CardVersion(
                text=card.text, at=card.locked_at or now,
                source_msg_id=card.locked_by_msg_id, note="prev",
            ))
        card.text = text
        card.locked = True
        card.locked_at = now
        card.locked_by_msg_id = source_msg_id
        # 截断版本历史
        if len(card.versions) > 5:
            card.versions = card.versions[-5:]
        # 备注
        if note and card.versions:
            card.versions[-1].note = note
        self._flush(fw)
        return card

    def set_core(self, no: int, text: str, source_msg_id: str | None = None) -> FieldWork:
        """锁副本核（kindle 阶段产物，纯文本）。"""
        fw = self._load(no)
        if fw is None:
            raise ValueError(f"场不存在：{no}")
        fw.core = text
        self._flush(fw)
        return fw

    def get_card_text(self, no: int, key: str) -> str:
        """读一张卡当前锁文本（core 是特殊 key）。不存在返回 ""。"""
        fw = self._load(no)
        if fw is None:
            return ""
        if key == "core":
            return fw.core or ""
        card = fw.cards.get(key)
        return card.text if (card and card.locked) else ""

    def unlock_cards(self, no: int, keys: list[str]) -> None:
        """解锁一批卡（回退时用），清文本与 locked 标记。core 传 "core" 清空 core 文本。"""
        fw = self._load(no)
        if fw is None:
            return
        changed = False
        for k in keys:
            if k == "core":
                if fw.core:
                    fw.core = ""
                    changed = True
                continue
            card = fw.cards.get(k)
            if card is None:
                continue
            card.locked = False
            card.text = ""
            card.locked_at = None
            card.locked_by_msg_id = None
            changed = True
        if changed:
            self._flush(fw)

    def locked_cards(self, no: int) -> dict[str, CardLock]:
        fw = self._load(no)
        if fw is None:
            return {}
        return dict(fw.cards)
