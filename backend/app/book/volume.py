"""卷（Volume）存储：卷级规划板。

布局：backend/data/projects/{pid}/volumes/
  - index.json      卷索引
  - {no}.json       单卷数据

Volume 对应一场副本（field_no），统筹整卷的基调弧线、长线伏笔、蓝星羁绊推进、神明隐晦出场、污染度目标、爽点计划。
每场杀青后通过 post_field_settle 记录本场实际归账结果。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 七层反转进度取值
LAYER_STATUSES = ("未开始", "已铺垫", "已到位")


@dataclass
class Volume:
    """单卷规划板 + 本场杀青归账。"""
    volume_no: int
    field_no: int
    title: str = ""
    tone_arc: str = ""
    planned_interludes: int = 3
    foreshadows_to_plant: list[str] = field(default_factory=list)
    bonds_to_deepen: list[str] = field(default_factory=list)
    gods_to_hint: list[str] = field(default_factory=list)
    contamination_goal: int = 0
    endgame_layer_hints: dict = field(default_factory=dict)  # { "1": "未开始", ..., "7": "未开始" }
    satisfaction_beats: list[str] = field(default_factory=list)
    post_field_settle: dict | None = None  # 杀青后填入

    # 卷级写作工艺 checkpoints（每卷独立一套黄金三章+兑现日程+审稿清单）
    craft_checkpoints: dict = field(default_factory=lambda: {
        "golden3": {
            "day1": {
                "hook_300": "",
                "sell_1500": "",
                "mc_appear": "",
                "chapter_end_nail": "",
            },
            "day2": {
                "rule_reveal": [],
                "mc_first_active_choice": "",
                "chapter_end_nail": "",
            },
            "day3": {
                "first_win": "",
                "engine_fire": "",
                "longline_planted": [],
                "chapter_end_nail": "",
            },
        },
        "volume_goal_checklist": [],
        "chapter_outline": [],
        "review_flags": {
            "timeline_ok": False,
            "character_ok": False,
            "rules_ok": False,
            "foreshadow_ok": False,
            "craft_paid": False,
            "contamination_accounted": False,
            "ending_nail_ok": False,
        },
    })

    created_at: str = ""
    updated_at: str = ""

    # ---- 工作台重构新增 ----
    volume_kind: str = "dungeon"  # 'dungeon' | 'interlude'
    adapted_module: str | None = None
    simulated_run: dict | None = None
    death_nodes: list = field(default_factory=list)
    climax_nodes: list = field(default_factory=list)
    clue_nodes: list = field(default_factory=list)
    pasted_nodes: list = field(default_factory=list)
    host_character: dict | None = None
    entry_point: dict | None = None
    info_gap: str | None = None
    stance: dict | None = None
    tangchi_story_outline: dict | None = None
    deviation_points: list = field(default_factory=list)
    rhythm_blueprint: dict | None = None
    scaffold_cards: dict = field(default_factory=dict)
    settle: dict | None = None
    interlude_plan: dict | None = None
    stage_completed: dict = field(default_factory=lambda: {
        "setup": False, "adapt": False, "simulate": False,
        "embody": False, "scaffold": False, "rhythm": False,
    })

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Volume":
        layers = d.get("endgame_layer_hints") or {}
        # 补齐七层默认
        norm_layers = {}
        for i in range(1, 8):
            v = layers.get(str(i)) or layers.get(i) or "未开始"
            if v not in LAYER_STATUSES:
                v = "未开始"
            norm_layers[str(i)] = v
        # craft_checkpoints 默认模板（旧数据缺字段时补齐）
        import copy as _copy
        _cp_default: dict = {
            "golden3": {
                "day1": {"hook_300": "", "sell_1500": "", "mc_appear": "", "chapter_end_nail": ""},
                "day2": {"rule_reveal": [], "mc_first_active_choice": "", "chapter_end_nail": ""},
                "day3": {"first_win": "", "engine_fire": "", "longline_planted": [], "chapter_end_nail": ""},
            },
            "volume_goal_checklist": [],
            "chapter_outline": [],
            "review_flags": {
                "timeline_ok": False, "character_ok": False, "rules_ok": False,
                "foreshadow_ok": False, "craft_paid": False,
                "contamination_accounted": False, "ending_nail_ok": False,
            },
        }
        cp = d.get("craft_checkpoints")
        if isinstance(cp, dict):
            merged = _copy.deepcopy(_cp_default)
            # 浅层合并
            for k, v in cp.items():
                if k in merged:
                    if isinstance(v, dict) and isinstance(merged[k], dict):
                        merged[k].update(v)
                    else:
                        merged[k] = v
            cp = merged
        else:
            cp = _cp_default
        return cls(
            volume_no=int(d.get("volume_no", 1) or 1),
            field_no=int(d.get("field_no", 0) or 0),
            title=str(d.get("title", "") or ""),
            tone_arc=str(d.get("tone_arc", "") or ""),
            planned_interludes=int(d.get("planned_interludes", 3) or 3),
            foreshadows_to_plant=list(d.get("foreshadows_to_plant", []) or []),
            bonds_to_deepen=list(d.get("bonds_to_deepen", []) or []),
            gods_to_hint=list(d.get("gods_to_hint", []) or []),
            contamination_goal=int(d.get("contamination_goal", 0) or 0),
            endgame_layer_hints=norm_layers,
            satisfaction_beats=list(d.get("satisfaction_beats", []) or []),
            post_field_settle=d.get("post_field_settle"),
            craft_checkpoints=cp,
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
            # ---- 工作台重构新增 ----
            volume_kind=str(d.get("volume_kind", "dungeon") or "dungeon"),
            adapted_module=d.get("adapted_module"),
            simulated_run=d.get("simulated_run"),
            death_nodes=list(d.get("death_nodes", []) or []),
            climax_nodes=list(d.get("climax_nodes", []) or []),
            clue_nodes=list(d.get("clue_nodes", []) or []),
            pasted_nodes=list(d.get("pasted_nodes", []) or []),
            host_character=d.get("host_character"),
            entry_point=d.get("entry_point"),
            info_gap=d.get("info_gap"),
            stance=d.get("stance"),
            tangchi_story_outline=d.get("tangchi_story_outline"),
            deviation_points=list(d.get("deviation_points", []) or []),
            rhythm_blueprint=d.get("rhythm_blueprint"),
            scaffold_cards=dict(d.get("scaffold_cards", {}) or {}),
            settle=d.get("settle"),
            interlude_plan=d.get("interlude_plan"),
            stage_completed=dict(d.get("stage_completed") or {
                "setup": False, "adapt": False, "simulate": False,
                "embody": False, "scaffold": False, "rhythm": False,
            }),
        )


class VolumeStore:
    """卷存储：负责 volumes/index.json 和 volumes/{no}.json 原子读写。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._cache: dict[int, Volume] = {}

    # ---- 索引 ----
    def _load_index(self) -> dict:
        if not self._index_path.exists():
            return {"volumes": []}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"volumes": []}

    def _save_index(self, idx: dict) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    # ---- 单卷文件 ----
    def _path(self, no: int) -> Path:
        return self.root / f"{no}.json"

    def _load(self, no: int) -> Volume | None:
        if no in self._cache:
            return self._cache[no]
        p = self._path(no)
        if not p.exists():
            return None
        try:
            v = Volume.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None
        self._cache[no] = v
        return v

    def _flush(self, v: Volume) -> None:
        p = self._path(v.volume_no)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(v.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        self._cache[v.volume_no] = v
        # 更新索引
        idx = self._load_index()
        entries = [e for e in idx.get("volumes", []) if e.get("volume_no") != v.volume_no]
        entries.append({
            "volume_no": v.volume_no,
            "field_no": v.field_no,
            "title": v.title,
            "tone_arc": v.tone_arc,
            "contamination_goal": v.contamination_goal,
            "planned_interludes": v.planned_interludes,
            "volume_kind": v.volume_kind,
        })
        entries.sort(key=lambda e: e["volume_no"])
        idx["volumes"] = entries
        self._save_index(idx)

    # ---- CRUD ----
    def all_list(self) -> list[Volume]:
        idx = self._load_index()
        out: list[Volume] = []
        for e in idx.get("volumes", []):
            no = e.get("volume_no")
            if no is None:
                continue
            v = self._load(int(no))
            if v is not None:
                out.append(v)
        return out

    def get(self, vol_no: int) -> Volume | None:
        return self._load(vol_no)

    def get_by_field(self, field_no: int) -> Volume | None:
        """按 field_no 查找对应卷（一场副本对应一卷）。"""
        for v in self.all_list():
            if v.field_no == field_no:
                return v
        return None

    def latest(self) -> Volume | None:
        vols = self.all_list()
        if not vols:
            return None
        return max(vols, key=lambda v: v.volume_no)

    def save(self, v: Volume) -> Volume:
        if not v.created_at:
            v.created_at = _now()
        v.updated_at = _now()
        # 确保七层反转默认值
        for i in range(1, 8):
            if str(i) not in v.endgame_layer_hints:
                v.endgame_layer_hints[str(i)] = "未开始"
        self._flush(v)
        return v

    def create_next(self, book_store, field_no: int) -> Volume:
        """创建下一卷（volume_no = 现有 max + 1）。
        book_store 参数保留签名一致性（类似 FieldStore.create 与 BookStore 的关系），
        这里不强制依赖，直接基于现有索引取下一号。
        """
        existing = [v.volume_no for v in self.all_list()]
        next_no = (max(existing) + 1) if existing else 1
        # 检查是否已存在同 field_no 的卷
        existing_for_field = self.get_by_field(field_no)
        if existing_for_field is not None:
            return existing_for_field
        v = Volume(
            volume_no=next_no,
            field_no=field_no,
            title=f"卷{next_no}",
            created_at=_now(),
            updated_at=_now(),
        )
        self._flush(v)
        return v

    def create_new_volume(self, *, title: str = "", volume_kind: str = "dungeon") -> Volume:
        """工作台用：取 max volume_no +1 创建新卷，field_no 同 volume_no。
        不做幂等检查（允许连续创建多个卷）。
        """
        existing = [v.volume_no for v in self.all_list()]
        next_no = (max(existing) + 1) if existing else 1
        if not title:
            if volume_kind == "interlude":
                title = "蓝星间章"
            else:
                title = f"卷{next_no}"
        v = Volume(
            volume_no=next_no,
            field_no=next_no,
            title=title,
            volume_kind=volume_kind,
            created_at=_now(),
            updated_at=_now(),
        )
        self._flush(v)
        return v

    def update_planning(self, vol_no: int, **kwargs) -> Volume:
        """更新规划板字段（title/tone_arc/planned_interludes/foreshadows_to_plant/
        bonds_to_deepen/gods_to_hint/contamination_goal/satisfaction_beats/endgame_layer_hints）。"""
        v = self._load(vol_no)
        if v is None:
            raise ValueError(f"卷不存在：{vol_no}")
        planning_fields = {
            "title", "tone_arc", "planned_interludes", "foreshadows_to_plant",
            "bonds_to_deepen", "gods_to_hint", "contamination_goal",
            "satisfaction_beats", "endgame_layer_hints",
            "adapted_module", "simulated_run",
            "host_character", "entry_point", "info_gap",
            "stance", "tangchi_story_outline",
            # Task 8：副本详设 6 卡
            "death_nodes", "climax_nodes", "clue_nodes",
            "pasted_nodes", "scaffold_cards",
            # Task 9：节奏骨架
            "rhythm_blueprint",
            "stage_completed",
        }
        # 这些字段允许显式传 None 来清空重跑
        _allow_none = {"simulated_run"}
        # 这些字段的 falsey 值（[]/{}/False/"") 是有效值，必须落盘
        _allow_falsey = {"death_nodes", "climax_nodes", "clue_nodes",
                         "pasted_nodes", "scaffold_cards", "stage_completed",
                         "satisfaction_beats", "foreshadows_to_plant",
                         "bonds_to_deepen", "gods_to_hint",
                         "rhythm_blueprint"}
        for k, val in kwargs.items():
            if k not in planning_fields:
                continue
            if val is None:
                if k in _allow_none:
                    setattr(v, k, None)
                continue
            # falsey 但属于 _allow_falsey（如 []/{}）照常写入
            if not val and k not in _allow_falsey:
                # 空字符串/0 对于普通字段跳过（避免覆盖默认）
                continue
            setattr(v, k, val)
        self.save(v)
        return v

    def record_settle(self, vol_no: int, settle: dict) -> Volume:
        """杀青后写入 post_field_settle。"""
        v = self._load(vol_no)
        if v is None:
            raise ValueError(f"卷不存在：{vol_no}")
        v.post_field_settle = settle
        self.save(v)
        return v
