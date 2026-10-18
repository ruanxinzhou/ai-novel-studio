"""材料数据模型（M1-T7 骨架；provenance 来源分类在 M3 引入）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


class MaterialFamily(str, Enum):
    """材料五族（Vela 机制）：

    - CONTEXT：运行时实况/世界基础（玩家状态、本场世界设定等）——必须整体进入，
      是 AI 保持叙事一致性的地面真值，装不下就显式失败；渲染为独立分区
    - REQUIRED：作者资料/角色档案/后续计划——必须整体进入，装不下就显式失败
    - FINALIZED：已定稿章节片段——可选，优先级最高
    - CANDIDATE：未定稿候选稿——可选
    - REFERENCE：参考资料——可选，优先级最低
    """

    CONTEXT = "context"
    REQUIRED = "required"
    FINALIZED = "finalized"
    CANDIDATE = "candidate"
    REFERENCE = "reference"


# 必进族（锚点之外）——装不下就抛 MaterialCapacityError，绝不静默丢弃
MANDATORY_FAMILIES = (MaterialFamily.CONTEXT, MaterialFamily.REQUIRED)


# 可选族的装入优先序：定稿 > 候选 > 参考（写作优先采信有来源的定稿原文）
OPTIONAL_FAMILY_ORDER = (
    MaterialFamily.FINALIZED,
    MaterialFamily.CANDIDATE,
    MaterialFamily.REFERENCE,
)


@dataclass(frozen=True)
class Material:
    source_id: str
    family: MaterialFamily
    content: str
    title: str = ""
    keywords: tuple[str, ...] = field(default=())

    @property
    def byte_size(self) -> int:
        return utf8_size(self.content)

    @property
    def relevance(self) -> int:
        """相关词命中数（去重计），用于可选族排序。"""
        return sum(1 for kw in set(self.keywords) if kw and kw in self.content)


@dataclass(frozen=True)
class Omission:
    """省略收据：一条材料为何没进上下文，可审计。"""

    source_id: str
    family: MaterialFamily
    reason: str
    byte_size: int

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "family": self.family.value,
            "reason": self.reason,
            "byte_size": self.byte_size,
        }


class MaterialCapacityError(Exception):
    """必需材料装不下预算——显式失败，绝不静默截断。"""

    def __init__(self, message: str, overflow_bytes: int, source_ids: list[str]):
        super().__init__(message)
        self.overflow_bytes = overflow_bytes
        self.source_ids = source_ids
