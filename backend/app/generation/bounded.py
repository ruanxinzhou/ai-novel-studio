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

# 回退检测：命中段之间最多允许跳过几段不命中（模型重写常把一段拆成两段改几个字）
_ROLLBACK_BRIDGE_GAP = 4


def strip_overlap(prev_tail: str, new_head: str, max_overlap_chars: int = 200) -> str:
    """去拼接重复：new_head 开头若与 prev_tail 结尾存在重叠，削掉最长重叠。

    这里只处理**尾部对齐**的重叠（续写从断点接上时的正常情形）。
    「模型整体回退重写」——新内容里某一大段与已写内容的中段完全相同——
    不在这里处理，见 complete_prose 里的回退检测：那种情况要整体截断，
    削个头是不够的。
    """
    k_max = min(max_overlap_chars, len(prev_tail), len(new_head))
    for k in range(k_max, 0, -1):
        if prev_tail[-k:] == new_head[:k]:
            return new_head[k:]
    return new_head


def find_rollback_span(accumulated: str, new_text: str, min_span: int = 200) -> tuple[int, int]:
    """抓「模型整体回退重写」：它会把已写过的段落原样再来一遍。

    返回 (在 new_text 中应截断的位置, 被判定为回退的字符数)；
    没找到够长的回退时返回 (0, 0)——即不截断。

    截断点是**回退段的起点**，不是第一个完全相同段的位置。整体回退时
    开头往往只被改动一两个字（「楼道灯亮了」→「楼道灯立刻亮了」），
    从第一个完全相同的段开始截会留下一截复读尾巴，必须回到连续命中的最早段。
    """
    if not accumulated or not new_text or len(new_text) < min_span:
        return 0, 0
    acc_paras = accumulated.split("\n\n")
    new_paras = new_text.split("\n\n")

    # 判定「实质相同」：完全相同，或剥掉空白后相同。前缀近似单列在 _hit 里。
    acc_index: set[str] = set()
    for p in acc_paras:
        n = "".join(p.split())
        if n:
            acc_index.add(n)

    def _hit(p: str) -> bool:
        n = "".join(p.split())
        if not n:
            return False
        if n in acc_index:
            return True
        # 前缀近似：任一已写段落以本段前 85% 开头
        head = n[: max(20, int(len(n) * 0.85))]
        return any(len(a) >= len(head) and a.startswith(head) for a in acc_index)

    best_pos, best_len = 0, 0
    run_start = -1
    for i, p in enumerate(new_paras):
        if _hit(p):
            if run_start < 0:
                run_start = i
        else:
            if run_start >= 0:
                run_start = _close_run(run_start, i, new_paras, _hit)
                cut = len("\n\n".join(new_paras[:run_start]))
                length = len("\n\n".join(new_paras[run_start:i]))
                if length > best_len:
                    best_pos, best_len = cut, length
                run_start = -1
    if run_start >= 0:
        run_start = _close_run(run_start, len(new_paras), new_paras, _hit)
        cut = len("\n\n".join(new_paras[:run_start]))
        length = len("\n\n".join(new_paras[run_start:]))
        if length > best_len:
            best_pos, best_len = cut, length
    return (best_pos, best_len) if best_len >= min_span else (0, 0)


def _close_run(run_start: int, run_end: int, new_paras: list[str], hit) -> int:
    """把回退段起点往前挪过头：夹着若干段微改动的复读仍算同一轮回退。

    模型重写时经常把一段拆成两段、各改几个字（「他站在门口等了两秒，又跺了一脚。
    灯还是立刻亮。」→「他等了两秒，又跺一脚。」「灯还是立刻亮。」），
    所以不能只看连续命中，要允许跳过中间的不命中段再往前找。
    """
    i = run_start
    j = run_start - 1
    skipped = 0
    while j >= 0 and skipped <= _ROLLBACK_BRIDGE_GAP:
        if hit(new_paras[j]):
            i = j
            skipped = 0
        else:
            skipped += 1
        j -= 1
    return i


@dataclass(frozen=True)
class ProseCompletion:
    text: str
    status: str  # complete | bounded | no_progress
    attempts: int
    dedup_overlaps: int
    rewound_chars: int = 0  # 因检测到整体回退重写而被丢弃的字符数


def complete_prose(
    generate: Callable[[str | None], tuple[str, str]],
    max_continuations: int = 3,
    min_progress_chars: int = 10,
    max_overlap_chars: int = 200,
    rollback_span: int = 200,
) -> ProseCompletion:
    """generate(accumulated_text | None) -> (text, finish_reason)。

    首次传入 None；续写时传入已积累文本，由调用方闭包拼续写指令。

    rollback_span：续写内容里出现这么长的一段与已写内容完全相同时，
    判定为「模型整体回退重写」，从那段起截断，只保留新增的尾部。

    空产出后 accumulated 保持空串，下一轮 generate 收到的是「空正文」信号，
    调用方据此改发「上一轮全程在思考没落笔、现在直接落笔」，
    而不是「从断点续写」——否则推理模型会从头再想一遍。
    """
    accumulated = ""
    attempts = 0
    overlaps = 0
    rewound = 0
    empty_streak = 0

    while True:
        text, finish_reason = generate(accumulated if attempts else None)
        attempts += 1

        stripped = text.strip()

        if attempts > 1:
            # 先抓整体回退重写：新内容里有一大段是已写过的原文。
            # 这必须在 strip_overlap 之前做——回退时新内容开头往往
            # 接在已写内容末尾之后，尾部对齐根本匹配不上。
            if rollback_span > 0 and accumulated:
                pos, span = find_rollback_span(accumulated, text, rollback_span)
                if span > 0:
                    rewound += span
                    text = text[:pos]
                    overlaps += 1
            text = strip_overlap(accumulated, text, max_overlap_chars)
            stripped = text.strip()
            if text:
                overlaps += 1

        accumulated += text

        # 回退截断后可能把这一轮清空了：那说明模型这轮完全在复读，
        # 交给下一轮重试（attempts 还没用完）或按无进展收尾。
        # 空正文：M3.1-Flash 等模型偶发"只输出 reasoning 就 stop"，把它当作被截断，
        # 触发续写（调用方会发"从断点处续写"指令）；续写后仍无正文则返回 no_progress。
        if not stripped:
            empty_streak += 1
            if attempts > max_continuations:
                return ProseCompletion(accumulated, NO_PROGRESS, attempts, overlaps, rewound)
            # 把 finish_reason 伪装成 length，让循环继续走续写分支。
            # 注意 accumulated 仍是空串，所以下一轮 generate 收到的是"空正文"信号，
            # 调用方据此改发「上一轮全程在思考、现在直接落笔」而不是「从断点续写」。
            finish_reason = "length"
        elif finish_reason == "length" and len(stripped) < min_progress_chars:
            return ProseCompletion(accumulated, NO_PROGRESS, attempts, overlaps, rewound)
        else:
            empty_streak = 0

        if finish_reason != "length":
            return ProseCompletion(accumulated, COMPLETE, attempts, overlaps, rewound)
        if attempts > max_continuations:
            return ProseCompletion(accumulated, BOUNDED, attempts, overlaps, rewound)


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
