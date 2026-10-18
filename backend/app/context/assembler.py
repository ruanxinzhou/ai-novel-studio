"""材料分层装配器（M1-T7 骨架，机制源自 Vela chapter-materials）。

裁决顺序：
1. 校验（空内容/重复 source_id 直接拒绝）
2. 锚点（上一章结尾句边界截断）先占预算，与 REQUIRED 同等地位——必进
3. REQUIRED 求和，装不下 → MaterialCapacityError（列出超出来源）
4. 可选族按 族序(定稿>候选>参考) → 相关词命中数 → source_id 排序，逐个
   first-fit 装入；放不下的记省略收据（reason=capacity）后继续尝试后续
5. 全量快照（含省略）sha256 哈希——同输入可跨会话重放
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.context.material import (
    MANDATORY_FAMILIES,
    OPTIONAL_FAMILY_ORDER,
    Material,
    MaterialCapacityError,
    MaterialFamily,
    Omission,
    utf8_size,
)

ANCHOR_MAX_CHARS = 1000

_SENTENCE_BOUNDARIES = ("。", "！", "？", "…", "\n")


def truncate_tail_at_sentence(text: str, max_chars: int = ANCHOR_MAX_CHARS) -> str:
    """取 text 结尾 ≤max_chars 字，且从最近的句子边界之后开始（避免从半句起头）。"""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    for i, ch in enumerate(tail):
        if ch in _SENTENCE_BOUNDARIES:
            return tail[i + 1 :]
    return tail


@dataclass(frozen=True)
class ContextSnapshot:
    anchor: str
    included: tuple[Material, ...]
    omissions: tuple[Omission, ...]
    budget_bytes: int
    used_bytes: int

    def digest(self) -> str:
        """included + omissions 全量哈希；输入不变则哈希不变，同输入可重放。"""
        payload = {
            "anchor": self.anchor,
            "budget_bytes": self.budget_bytes,
            "included": [
                {
                    "source_id": m.source_id,
                    "family": m.family.value,
                    "title": m.title,
                    "content": m.content,
                }
                for m in self.included
            ],
            "omissions": [o.to_dict() for o in self.omissions],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def render_sections(self) -> dict[str, str]:
        """按族渲染成 prompt 分区（系统合同在 T5 由 PromptRegistry 拼接）。

        REQUIRED 中 source_id 以 skill: 开头的是写作模块规则，单独成
        「写作模块」分区，与层指令里的引用对齐——规则与素材不混排。
        """
        sections: dict[str, str] = {}
        if self.anchor:
            sections["锚点·上一章结尾"] = self.anchor
        for family in MaterialFamily:
            items = [m for m in self.included if m.family == family]
            if not items:
                continue
            if family is MaterialFamily.CONTEXT:
                # 运行时实况/本场世界基础——独立分区，不混入 REQUIRED 的 skill/markers 拆分
                sections["本场实况·世界基础"] = "\n\n".join(
                    f"【{m.title or m.source_id}】\n{m.content}" for m in items
                )
                continue
            if family is MaterialFamily.REQUIRED:
                rules = [m for m in items if m.source_id.startswith("skill:")]
                marks = [m for m in items if m.source_id.startswith("markers:")]
                plain = [m for m in items
                         if not m.source_id.startswith("skill:")
                         and not m.source_id.startswith("markers:")]
                if rules:
                    sections["写作模块"] = "\n\n".join(
                        f"【{m.title or m.source_id}】\n{m.content}" for m in rules
                    )
                if marks:
                    sections["必现标记"] = "\n\n".join(m.content for m in marks)
                items = plain
                if not items:
                    continue
            header = {
                MaterialFamily.REQUIRED: "必需材料",
                MaterialFamily.FINALIZED: "定稿摘录",
                MaterialFamily.CANDIDATE: "候选稿摘录",
                MaterialFamily.REFERENCE: "参考资料",
            }[family]
            sections[header] = "\n\n".join(
                f"【{m.title or m.source_id}】\n{m.content}" for m in items
            )
        return sections


class MaterialAssembler:
    def __init__(self, max_bytes: int, anchor_max_chars: int = ANCHOR_MAX_CHARS):
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        self.max_bytes = max_bytes
        self.anchor_max_chars = anchor_max_chars

    def assemble(self, materials: list[Material], anchor_text: str = "") -> ContextSnapshot:
        self._validate(materials)

        anchor = truncate_tail_at_sentence(anchor_text, self.anchor_max_chars)
        used = utf8_size(anchor)

        mandatory = [m for m in materials if m.family in MANDATORY_FAMILIES]
        mandatory_bytes = sum(m.byte_size for m in mandatory)
        if used + mandatory_bytes > self.max_bytes:
            overflow = used + mandatory_bytes - self.max_bytes
            ids = [m.source_id for m in mandatory]
            raise MaterialCapacityError(
                f"必进材料（CONTEXT+REQUIRED）超出预算 {overflow} 字节（锚点 {used} + 必进 {mandatory_bytes} > 预算 {self.max_bytes}）。"
                "请扩充预算或精简必进材料——禁止静默截断。",
                overflow_bytes=overflow,
                source_ids=ids,
            )
        included = list(mandatory)
        used += mandatory_bytes

        optional = [m for m in materials if m.family not in MANDATORY_FAMILIES]
        optional.sort(key=lambda m: (OPTIONAL_FAMILY_ORDER.index(m.family), -m.relevance, m.source_id))

        omissions: list[Omission] = []
        for m in optional:
            if used + m.byte_size <= self.max_bytes:
                included.append(m)
                used += m.byte_size
            else:
                omissions.append(
                    Omission(source_id=m.source_id, family=m.family, reason="capacity", byte_size=m.byte_size)
                )

        return ContextSnapshot(
            anchor=anchor,
            included=tuple(included),
            omissions=tuple(omissions),
            budget_bytes=self.max_bytes,
            used_bytes=used,
        )

    @staticmethod
    def _validate(materials: list[Material]) -> None:
        seen: set[str] = set()
        for m in materials:
            if not m.source_id:
                raise ValueError("材料缺少 source_id")
            if m.source_id in seen:
                raise ValueError(f"source_id 重复: {m.source_id}")
            seen.add(m.source_id)
            if m.family in MANDATORY_FAMILIES and not m.content.strip():
                raise ValueError(f"必进材料（{m.family.value}）内容为空: {m.source_id}")
            if m.byte_size == 0:
                raise ValueError(f"材料内容为空: {m.source_id}")
