"""场流水线节点产物存储：nodes/{field_no}/{node_id}.json 原子落盘。

每个节点运行后把原始 LLM 输出、解析后的结构化数据、时间戳、模型调用次数写到这里；
下游节点按 node_id 拿上游产物当输入。和 FieldStore 分开——FieldStore 存作者确认后的
"定稿级"数据（已选的 variant、分章结果、讣告等），RunStore 存"流水线上"的中间产物，
允许重跑覆盖。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class NodeArtifact:
    """单节点产物。"""
    node_id: str
    layer: str                 # pipeline 层 key
    raw_text: str = ""         # LLM 原始输出
    parsed: Any = None         # 解析后的 JSON 对象（若有）
    variants: list[dict] = field(default_factory=list)  # variant-pool 节点的候选
    chosen_vid: str | None = None
    meta: dict = field(default_factory=dict)  # 调用次数/attempts/状态
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NodeArtifact":
        return cls(
            node_id=str(d.get("node_id", "")),
            layer=str(d.get("layer", "")),
            raw_text=str(d.get("raw_text", "") or ""),
            parsed=d.get("parsed"),
            variants=[dict(v) for v in (d.get("variants") or [])],
            chosen_vid=d.get("chosen_vid"),
            meta=dict(d.get("meta") or {}),
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
        )


# 节点执行顺序定义：(node_id, layer_key, is_variant_pool, x_source)
# x_source 说明此节点的 x_block 从哪里来：
#   - "obituary"       → 本场讣告卡渲染文本
#   - "prev"           → 上一节点 chosen_text/raw_text
#   - "prev:parsed"    → 上一节点 parsed JSON 渲染
#   - "field_meta"     → 场元信息（title/world_premise/...）
FIELD_PIPELINE_NODES: list[tuple[str, str, bool, str]] = [
    # ① 模组改编候选（5 选 1）
    ("adapt_scenario",     "adapt_scenario",     True,  "obituary"),
    # ② COC 多场跑团仿真 → 5 个真相候选（5 选 1）
    ("coc_outcome",        "coc_outcome",        True,  "chosen:adapt_scenario"),
    # ③ 唐迟大纲候选（5 选 1）
    ("tang_outline",       "tang_outline",       True,  "chosen:adapt_scenario+coc_outcome"),
    # ④ 自动分章（单文本 JSON 输出；可人工编辑）
    ("split_chapters",     "split_chapters",     False, "chosen:tang_outline"),
    # ============ 以下节点在作者写完所有正文并定稿戏里之后再执行 ============
    # ⑤ 世界规则提炼
    ("distill_world",      "distill_world",      False, "adapt+outcome+tang_drafts"),
    # ⑥ 执念使用档案
    ("distill_obsession",  "distill_obsession",  False, "full_field_drafts"),
    # ⑦ 实体抽取
    ("distill_entity",     "distill_entity",     False, "full_field_drafts"),
    # ⑧ 讣告残页 + 杀青一句话
    ("epilogue_ash",       "epilogue_ash",       False, "full_field_drafts"),
    # ⑨ 间章必载清单
    ("interlude_expected", "interlude_expected", False, "epilogue_ash"),
    # ⑩ 间章大纲候选（3 选 1）
    ("interlude_outline",  "interlude_outline",  True,  "interlude_expected"),
]

# 需要作者介入/写入正文后才能继续的"分界点"
STOPPING_POINTS = (
    "split_chapters",     # 分章完→作者去写各章 plot/draft/polish
    "epilogue_ash",       # 残页+杀青后→作者去写幕间正文（其实幕间正文在 interlude_outline 后）
)

# variant-pool 节点的默认候选数量
DEFAULT_VARIANT_COUNTS: dict[str, int] = {
    "adapt_scenario": 5,
    "coc_outcome": 5,
    "tang_outline": 5,
    "interlude_outline": 3,
}


class FieldRunStore:
    """场流水线中间产物存取。"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, field_no: int) -> Path:
        d = self.root / str(field_no)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, field_no: int, node_id: str) -> Path:
        return self._dir(field_no) / f"{node_id}.json"

    def get(self, field_no: int, node_id: str) -> NodeArtifact | None:
        p = self._path(field_no, node_id)
        if not p.exists():
            return None
        return NodeArtifact.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, field_no: int, art: NodeArtifact) -> None:
        art.updated_at = _now()
        if not art.created_at:
            art.created_at = art.updated_at
        p = self._path(field_no, art.node_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(art.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def list_artifacts(self, field_no: int) -> list[dict]:
        """列出本场所有节点产物的概要（按 FIELD_PIPELINE_NODES 顺序）。"""
        out: list[dict] = []
        for node_id, layer, is_variant, _ in FIELD_PIPELINE_NODES:
            art = self.get(field_no, node_id)
            out.append({
                "node_id": node_id,
                "layer": layer,
                "is_variant_pool": is_variant,
                "status": "done" if art else "pending",
                "chosen_vid": art.chosen_vid if art else None,
                "variants_count": len(art.variants) if art else 0,
                "updated_at": art.updated_at if art else None,
            })
        return out

    def chosen_text(self, field_no: int, node_id: str) -> str | None:
        """取某 variant 节点已选中文本；非 variant 节点取 raw_text。"""
        art = self.get(field_no, node_id)
        if art is None:
            return None
        if art.variants:
            vid = art.chosen_vid
            for v in art.variants:
                if v.get("vid") == vid:
                    return v.get("text", "")
            return None
        return art.raw_text

    def chosen_parsed(self, field_no: int, node_id: str) -> Any:
        """取已选中 variant 的 parsed；非 variant 节点直接取 parsed。"""
        art = self.get(field_no, node_id)
        if art is None:
            return None
        if art.variants:
            vid = art.chosen_vid
            for v in art.variants:
                if v.get("vid") == vid:
                    return v.get("parsed")
            return None
        return art.parsed

    def choose_variant(self, field_no: int, node_id: str, vid: str) -> NodeArtifact:
        art = self.get(field_no, node_id)
        if art is None:
            raise ValueError(f"节点产物不存在：{node_id}")
        if not any(v.get("vid") == vid for v in art.variants):
            raise ValueError(f"候选 {vid} 不存在于节点 {node_id}")
        art.chosen_vid = vid
        self.save(field_no, art)
        return art
