"""文本切块算法（移植自 Long-Novel-GPT core/writer_utils.py，机制借鉴）。

split_text_into_chunks：在「最少块数/最大块长/最多块数」三重约束下，
先把文本按段落切开，过短则相邻合并，过长则找离中点最近的句子边界劈开。
"""
import re
from difflib import Differ

SENTENCE_SPLIT_PATTERN = r"[。？；]"


def split_text_by_separators(text: str, separators: list[str], keep_separators: bool = True) -> list[str]:
    pattern = f'({"|".join(map(re.escape, separators))}+)'
    chunks = re.split(pattern, text)

    paragraphs: list[str] = []
    current_para: list[str] = []

    for i in range(0, len(chunks), 2):
        content = chunks[i]
        separator = chunks[i + 1] if i + 1 < len(chunks) else ""

        current_para.append(content)
        if keep_separators and separator:
            current_para.append(separator)

        if content.strip():
            paragraphs.append("".join(current_para))
            current_para = []

    return paragraphs


def split_text_into_paragraphs(text: str, keep_separators: bool = True) -> list[str]:
    return split_text_by_separators(text, ["\n"], keep_separators)


def split_text_into_sentences(text: str, keep_separators: bool = True) -> list[str]:
    return split_text_by_separators(text, ["\n", "。", "？", "！", "；"], keep_separators)


def split_text_into_chunks(
    text: str,
    max_chunk_size: int,
    min_chunk_n: int,
    min_chunk_size: int = 1,
    max_chunk_n: int = 1000,
) -> list[str]:
    def split_paragraph(para: str) -> tuple[str, str]:
        mid = len(para) // 2
        split_points = [m.end() for m in re.finditer(SENTENCE_SPLIT_PATTERN, para)]

        if not split_points:
            raise ValueError("没有找到分割点!")

        closest_point = min(split_points, key=lambda x: abs(x - mid))
        if not para[:closest_point].strip() or not para[closest_point:].strip():
            raise ValueError("没有找到分割点!")

        return para[:closest_point], para[closest_point:]

    paragraphs = split_text_into_paragraphs(text)

    if max_chunk_n < 1:
        raise ValueError("max_chunk_n必须大于等于1")
    if sum(len(p) for p in paragraphs) < min_chunk_size:
        raise ValueError(f"分割时，输入的文本长度小于要求的min_chunk_size:{min_chunk_size}")

    count = 0
    while len(paragraphs) > max_chunk_n or min(len(p) for p in paragraphs) < min_chunk_size:
        count += 1
        if count >= 1000:
            raise ValueError("分割进入死循环！")

        # 找出相邻块中长度和最小的两块合并
        min_sum = float("inf")
        min_i = 0
        for i in range(len(paragraphs) - 1):
            curr_sum = len(paragraphs[i]) + len(paragraphs[i + 1])
            if curr_sum < min_sum:
                min_sum = curr_sum
                min_i = i

        paragraphs[min_i : min_i + 2] = ["".join(paragraphs[min_i : min_i + 2])]

    while len(paragraphs) < min_chunk_n or max(len(p) for p in paragraphs) > max_chunk_size:
        count += 1
        if count >= 1000:
            raise ValueError("分割进入死循环！")

        longest_para_i = max(range(len(paragraphs)), key=lambda i: len(paragraphs[i]))
        part1, part2 = split_paragraph(paragraphs[longest_para_i])
        if len(part1) < min_chunk_size or len(part2) < min_chunk_size or len(paragraphs) + 1 > max_chunk_n:
            raise ValueError("没有找到合适的分割点!")
        paragraphs[longest_para_i : longest_para_i + 1] = [part1, part2]

    return paragraphs


def detect_max_edit_span(a: str, b: str) -> tuple[int, int]:
    """求 a→b 的最小编辑区间：返回 (公共前缀长, 负的公共后缀长)。

    用于 UI 应用用户手改时定位实际被改动的窗口，避免全量覆盖。
    """
    diff = Differ().compare(a, b)

    l = 0
    r = 0
    flag_count_l = True

    for tag in diff:
        if tag.startswith(" "):
            if flag_count_l:
                l += 1
            else:
                r += 1
        else:
            flag_count_l = False
            r = 0

    return l, -r
