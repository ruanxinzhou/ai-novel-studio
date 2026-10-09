"""XYWriter：xy_pairs 创作流的核心坐标运算（移植自 Long-Novel-GPT core/writer.py 非LLM部分）。

x = 纲要（大纲/剧情/写作指令），y = 文本（正文/扩写结果）。所有操作只做
字符区间 ↔ pair 区间的对齐与窗口划分，不感知任何 LLM——生成由流水线层
把每个 Chunk 交给 Provider 完成，结果经 apply_chunks 回填。

批量切片策略（get_chunks）：目标区按 0.8×块长切成窗口，每窗向外扩
0.5×块长的上下文（x/y 各自独立计量）；纯 x 区选 x 窗，纯 y 区选 y 窗，
交界处选 source 窗口更小的一侧。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from itertools import accumulate

from app.chunking.chunk import Chunk, Pair
from app.chunking.slicing import detect_max_edit_span, split_text_into_chunks

__all__ = ["XYWriter", "detect_max_edit_span"]

MAX_SLICE_ITERATIONS = 10000


class XYWriter:
    def __init__(
        self,
        xy_pairs: list[Pair],
        x_chunk_length: int = 1000,
        y_chunk_length: int = 1000,
    ):
        self.xy_pairs: list[Pair] = [tuple(p) for p in xy_pairs]
        self.x_chunk_length = x_chunk_length
        self.y_chunk_length = y_chunk_length

    # ---- 全量视图 ----
    @property
    def x(self) -> str:
        return "".join(pair[0] for pair in self.xy_pairs)

    @property
    def y(self) -> str:
        return "".join(pair[1] for pair in self.xy_pairs)

    @property
    def x_len(self) -> int:
        return sum(len(pair[0]) for pair in self.xy_pairs)

    @property
    def y_len(self) -> int:
        return sum(len(pair[1]) for pair in self.xy_pairs)

    @property
    def pair_count(self) -> int:
        return len(self.xy_pairs)

    def count_span_length(self, pair_span: tuple[int, int]) -> tuple[int, int]:
        pairs = self.xy_pairs[pair_span[0] : pair_span[1]]
        return sum(len(p[0]) for p in pairs), sum(len(p[1]) for p in pairs)

    # ---- 区间对齐 ----
    def align_span(self, x_span: tuple[int, int] | None = None, y_span: tuple[int, int] | None = None) -> tuple[tuple[int, int], tuple[int, int]]:
        """把字符区间向外扩张到整 pair 边界，返回 (对齐字符区间, pair区间)。"""
        if x_span is None and y_span is None:
            raise ValueError("x_span 与 y_span 至少提供一个")
        if x_span is not None and y_span is not None:
            raise ValueError("x_span 与 y_span 只能提供一个")

        is_x = x_span is not None
        span = x_span if is_x else y_span
        cumsum = list(accumulate([0] + [len(pair[0 if is_x else 1]) for pair in self.xy_pairs]))

        l, r = span
        start_chunk = bisect_right(cumsum, l) - 1
        end_chunk = bisect_left(cumsum, r)

        aligned_l, aligned_r = cumsum[start_chunk], cumsum[end_chunk]

        if not (aligned_l <= l < aligned_r):
            raise ValueError("对齐区间未覆盖输入区间起点")
        if not (aligned_l < r <= aligned_r):
            raise ValueError("对齐区间未覆盖输入区间终点")
        if not (0 <= start_chunk < end_chunk <= len(self.xy_pairs)):
            raise ValueError("pair 区间越界")

        return (aligned_l, aligned_r), (start_chunk, end_chunk)

    # ---- 取窗 ----
    def get_chunk(
        self,
        pair_span: tuple[int, int] | None = None,
        x_span: tuple[int, int] | None = None,
        y_span: tuple[int, int] | None = None,
        context_length: int = 0,
        smooth: bool = True,
    ) -> Chunk:
        if sum(x is not None for x in (pair_span, x_span, y_span)) != 1:
            raise ValueError("pair_span / x_span / y_span 三选一")

        if pair_span is not None:
            if not (pair_span[0] >= 0 and pair_span[1] <= len(self.xy_pairs)):
                raise ValueError("pair_span 越界")
            context_pair_span = (
                max(0, pair_span[0] - context_length),
                min(len(self.xy_pairs), pair_span[1] + context_length),
            )
        else:
            if not smooth:
                raise ValueError("x/y 字符区间必须 smooth 对齐（smooth=True）")

            is_x = x_span is not None
            span = x_span if is_x else y_span
            span, pair_span = self.align_span(x_span=span if is_x else None, y_span=span if not is_x else None)

            total = self.x_len if is_x else self.y_len
            context_span = (max(0, span[0] - context_length), min(total, span[1] + context_length))
            context_span, context_pair_span = self.align_span(
                x_span=context_span if is_x else None, y_span=context_span if not is_x else None
            )

        chunk_pairs = tuple(self.xy_pairs[context_pair_span[0] : context_pair_span[1]])
        start = pair_span[0] - context_pair_span[0]
        stop = pair_span[1] - context_pair_span[1]
        if stop > 0:
            raise ValueError("目标区未贴窗口右缘（内部不变量被破坏）")
        text_slice = (start, None if stop == 0 else stop)

        return Chunk(chunk_pairs=chunk_pairs, source_slice=context_pair_span, text_slice=text_slice)

    def get_chunks(
        self,
        pair_span: tuple[int, int] | None = None,
        chunk_length_ratio: float = 1,
        context_length_ratio: float = 1,
        offset_ratio: float | tuple[int, int] = 0,
    ) -> list[Chunk]:
        """把 pair_span 区间切成批量生成窗口。offset_ratio 仅首窗生效（用于跳过已生成前缀）。"""
        pair_span = pair_span or (0, len(self.xy_pairs))
        chunk_length = (
            self.x_chunk_length * chunk_length_ratio,
            self.y_chunk_length * chunk_length_ratio,
        )
        context_length = (
            self.x_chunk_length // 2 * context_length_ratio,
            self.y_chunk_length // 2 * context_length_ratio,
        )

        offset: tuple[int, int] | int = 0
        if isinstance(offset_ratio, tuple):
            offset = offset_ratio
        elif 0 < offset_ratio < 1:
            offset = (int(chunk_length[0] * offset_ratio), int(chunk_length[1] * offset_ratio))

        chunks: list[Chunk] = []
        start = pair_span[0]
        cstart = self.count_span_length((0, start))
        max_cend = self.count_span_length((0, pair_span[1]))

        iterations = 0
        while start < pair_span[1]:
            iterations += 1
            if iterations > MAX_SLICE_ITERATIONS:
                raise ValueError("窗口切片未收敛，请检查 xy_pairs 是否存在异常超长 pair")

            if offset:
                cend = cstart[0] + offset[0], cstart[1] + offset[1]
                offset = 0
            else:
                # 0.8 系数：不求最优划分，给批量重生成留出可控粒度
                cend = cstart[0] + int(chunk_length[0] * 0.8), cstart[1] + int(chunk_length[1] * 0.8)
            cend = min(cend[0], max_cend[0]), min(cend[1], max_cend[1])

            x_len, y_len = cend[0] - cstart[0], cend[1] - cstart[1]
            chunk1 = self.get_chunk(x_span=(cstart[0], cend[0]), context_length=context_length[0]) if x_len > 0 else None
            chunk2 = self.get_chunk(y_span=(cstart[1], cend[1]), context_length=context_length[1]) if y_len > 0 else None

            if chunk1 is not None and chunk2 is None:
                chunk = chunk1
            elif chunk1 is None and chunk2 is not None:
                chunk = chunk2
            elif chunk1 is not None and chunk2 is not None:
                # 交界处：取上下文窗口更小的一侧，降低 prompt 冗余
                chunk = (
                    chunk1
                    if chunk1.source_slice[1] - chunk1.source_slice[0] < chunk2.source_slice[1] - chunk2.source_slice[0]
                    else chunk2
                )
            else:
                raise ValueError("x_span 与 y_span 均为零长度")

            chunks.append(chunk)
            start = chunk.text_source_slice.stop
            cstart = self.count_span_length((0, start))

        return chunks

    # ---- 回填 ----
    def get_chunk_pair_span(self, chunk: Chunk) -> tuple[int, int]:
        """把 Chunk（可能来自别的 Writer 实例）定位回本 writer 的 pair 区间。"""
        pair_start, pair_end = chunk.text_source_slice.start, chunk.text_source_slice.stop
        if pair_end <= len(self.xy_pairs):
            merged_x = "".join(p[0] for p in self.xy_pairs[pair_start:pair_end])
            merged_y = "".join(p[1] for p in self.xy_pairs[pair_start:pair_end])
            if merged_x == chunk.x_chunk and merged_y == chunk.y_chunk:
                return pair_start, pair_end

        pair_start, pair_end = 0, len(self.xy_pairs)
        x_chunk, y_chunk = chunk.x_chunk, chunk.y_chunk
        for i, (x, y) in enumerate(self.xy_pairs):
            if x_chunk[:50].startswith(x[:50]) and y_chunk[:50].startswith(y[:50]):
                pair_start = i
                break

        for i in range(pair_start, len(self.xy_pairs)):
            x, y = self.xy_pairs[i]
            if x_chunk[-50:].endswith(x[-50:]) and y_chunk[-50:].endswith(y[-50:]):
                pair_end = i + 1
                break

        merged_x = "".join(p[0] for p in self.xy_pairs[pair_start:pair_end])
        merged_y = "".join(p[1] for p in self.xy_pairs[pair_start:pair_end])
        if x_chunk != merged_x or y_chunk != merged_y:
            raise ValueError("Chunk 无法回填定位：内容与 xy_pairs 不匹配")

        return pair_start, pair_end

    def apply_chunks(self, chunks: list[Chunk], new_chunks: list[Chunk]) -> None:
        """把一批编辑后的 Chunk 回填进 xy_pairs（从右往左替换，天然处理块长变化）。"""
        if len(chunks) != len(new_chunks):
            raise ValueError("chunks 与 new_chunks 数量不一致")

        occupied = [False] * len(self.xy_pairs)
        pair_span_list = [self.get_chunk_pair_span(e) for e in chunks]
        for pair_span in pair_span_list:
            if any(occupied[pair_span[0] : pair_span[1]]):
                raise ValueError("回填区块存在重叠")
            occupied[pair_span[0] : pair_span[1]] = [True] * (pair_span[1] - pair_span[0])

        replacements = sorted(
            zip(pair_span_list, [e.text_pairs for e in new_chunks]),
            key=lambda item: item[0][0],
            reverse=True,
        )
        for (start, end), new_pairs in replacements:
            self.xy_pairs[start:end] = [tuple(p) for p in new_pairs]

    def apply_chunk_edit(self, chunk: Chunk, key: str, value: str) -> None:
        if key not in ("x_chunk", "y_chunk"):
            raise ValueError("key 仅支持 x_chunk / y_chunk")
        new_chunk = chunk.edit(**{key: value})
        self.apply_chunks([chunk], [new_chunk])

    # ---- 无 LLM 重映射 ----
    def map_text_wo_llm(self, chunk: Chunk) -> Chunk:
        """对超长 pair 做纯算法重切（不调 LLM）：单侧超长按侧切分，双侧超长报错。"""
        new_xy_pairs: list[Pair] = []
        for x, y in chunk.text_pairs:
            if x.strip() and not y.strip():
                x_pairs = split_text_into_chunks(x, self.x_chunk_length, min_chunk_n=1, min_chunk_size=5)
                new_xy_pairs.extend([(x_pair, y) for x_pair in x_pairs])
            elif not x.strip() and y.strip():
                y_pairs = split_text_into_chunks(y, self.y_chunk_length, min_chunk_n=1, min_chunk_size=5)
                new_xy_pairs.extend([(x, y_pair) for y_pair in y_pairs])
            else:
                if len(x) > self.x_chunk_length or len(y) > self.y_chunk_length:
                    raise ValueError("窗口太小或段落太长! 考虑选择更大的窗口长度或手动分段。")
                new_xy_pairs.append((x, y))

        return chunk.edit(text_pairs=new_xy_pairs)
