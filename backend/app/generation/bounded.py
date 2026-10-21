"""有界完成器（M1-T8，机制源自 Vela bounded-completion）。

- complete_prose：finish_reason=length 触发有界续写，拼接去重 ≤max_continuations
  次；单次续写去重后新增不足 min_progress_chars 视为无进展即停。
  无论哪种终态都返回已积累文本（部分成果不丢弃）。
- complete_structured：坏 JSON → 整体重生成 ≤max_regenerations 次；
  相同坏产出重复出现视为无进展即停。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.generation.json_repair import parse_repaired_json

COMPLETE = "complete"
BOUNDED = "bounded"
NO_PROGRESS = "no_progress"
EXHAUSTED = "exhausted"


def strip_overlap(prev_tail: str, new_head: str, max_overlap_chars: int = 200) -> str:
    """去拼接重复：new_head 开头若与 prev_tail 结尾存在重叠，削掉最长重叠。"""
    k_max = min(max_overlap_chars, len(prev_tail), len(new_head))
    for k in range(k_max, 0, -1):
        if prev_tail[-k:] == new_head[:k]:
            return new_head[k:]
    return new_head


@dataclass(frozen=True)
class ProseCompletion:
    text: str
    status: str  # complete | bounded | no_progress
    attempts: int
    dedup_overlaps: int


def complete_prose(
    generate: Callable[[str | None], tuple[str, str]],
    max_continuations: int = 3,
    min_progress_chars: int = 10,
    max_overlap_chars: int = 200,
) -> ProseCompletion:
    """generate(accumulated_text | None) -> (text, finish_reason)。

    首次传入 None；续写时传入已积累文本，由调用方闭包拼续写指令。
    """
    accumulated = ""
    attempts = 0
    overlaps = 0

    while True:
        text, finish_reason = generate(accumulated if attempts else None)
        attempts += 1

        stripped = text.strip()

        if attempts > 1:
            text = strip_overlap(accumulated, text, max_overlap_chars)
            stripped = text.strip()
            if text:
                overlaps += 1

        accumulated += text

        # 空正文：M3.1-Flash 等模型偶发"只输出 reasoning 就 stop"，把它当作被截断，
        # 触发续写（调用方会发"从断点处续写"指令）；续写后仍无正文则返回 no_progress。
        if not stripped:
            if attempts > max_continuations:
                return ProseCompletion(accumulated, NO_PROGRESS, attempts, overlaps)
            # 把 finish_reason 伪装成 length，让循环继续走续写分支
            finish_reason = "length"
        elif finish_reason == "length" and len(stripped) < min_progress_chars:
            return ProseCompletion(accumulated, NO_PROGRESS, attempts, overlaps)

        if finish_reason != "length":
            return ProseCompletion(accumulated, COMPLETE, attempts, overlaps)
        if attempts > max_continuations:
            return ProseCompletion(accumulated, BOUNDED, attempts, overlaps)


@dataclass(frozen=True)
class StructuredCompletion:
    data: object | None
    status: str  # complete | exhausted | no_progress
    attempts: int


def complete_structured(
    generate: Callable[[], str],
    max_regenerations: int = 2,
) -> StructuredCompletion:
    """generate() 每次整体重产出一个 JSON 字符串；词法可修复的截断也算成功。"""
    seen_bad: set[str] = set()
    max_attempts = 1 + max_regenerations

    for attempt in range(1, max_attempts + 1):
        raw = generate()
        data = parse_repaired_json(raw)
        if data is not None:
            return StructuredCompletion(data, COMPLETE, attempt)
        if raw in seen_bad:
            return StructuredCompletion(None, NO_PROGRESS, attempt)
        seen_bad.add(raw)

    return StructuredCompletion(None, EXHAUSTED, max_attempts)
