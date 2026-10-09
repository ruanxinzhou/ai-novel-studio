"""Chunk：xy_pairs 上的滑动窗口对象（移植自 Long-Novel-GPT core/writer.py）。

一个 Chunk 携带：
- chunk_pairs: 上下文窗口内的全部 (x, y) 对
- source_slice: 窗口在原 xy_pairs 中的 pair 下标区间 [start, stop)
- text_slice: 目标编辑区在窗口内的区间，start 从左数、stop 为负从右数（或 None 表示到末尾）

因此 text 区恒贴着窗口右缘，context 区是它左边的扩展——这保证了
text_source_slice = source_slice 平移后的确定性回填定位。
"""
from __future__ import annotations

from dataclasses import dataclass, field

Pair = tuple[str, str]


@dataclass(frozen=True)
class Chunk:
    chunk_pairs: tuple[Pair, ...]
    source_slice: tuple[int, int]
    text_slice: tuple[int, int | None] = field(default=(0, None))

    def __post_init__(self) -> None:
        start, stop = self.text_slice
        if stop is not None and stop >= 0:
            raise ValueError("text_slice end 必须为 None 或负数（目标区贴右缘）")

    # ---- 视图 ----
    @property
    def source_span(self) -> slice:
        return slice(*self.source_slice)

    @property
    def text_pairs(self) -> tuple[Pair, ...]:
        start, stop = self.text_slice
        return self.chunk_pairs[start:stop]

    @property
    def x_chunk(self) -> str:
        return "".join(pair[0] for pair in self.text_pairs)

    @property
    def y_chunk(self) -> str:
        return "".join(pair[1] for pair in self.text_pairs)

    @property
    def x_chunk_context(self) -> str:
        return "".join(pair[0] for pair in self.chunk_pairs)

    @property
    def y_chunk_context(self) -> str:
        return "".join(pair[1] for pair in self.chunk_pairs)

    # ---- 回填定位 ----
    @property
    def text_source_slice(self) -> slice:
        """目标编辑区映射回原 xy_pairs 的 pair 下标区间。"""
        start, stop = self.text_slice
        source_stop = self.source_slice[1] + (stop if stop is not None else 0)
        return slice(self.source_slice[0] + start, source_stop)

    # ---- 编辑 ----
    def edit(self, x_chunk: str | None = None, y_chunk: str | None = None, text_pairs: list[Pair] | tuple[Pair, ...] | None = None) -> "Chunk":
        if x_chunk is not None:
            text_pairs = [(x_chunk, self.y_chunk)]
        elif y_chunk is not None:
            text_pairs = [(self.x_chunk, y_chunk)]
        elif text_pairs is None:
            raise ValueError("edit 需要 x_chunk / y_chunk / text_pairs 之一")

        chunk_pairs = list(self.chunk_pairs)
        start, stop = self.text_slice
        chunk_pairs[start:stop] = list(text_pairs)

        return Chunk(chunk_pairs=tuple(chunk_pairs), source_slice=self.source_slice, text_slice=self.text_slice)

    # ---- 传输 ----
    def to_dict(self) -> dict:
        return {
            "chunk_pairs": [list(p) for p in self.chunk_pairs],
            "source_slice": list(self.source_slice),
            "text_slice": [self.text_slice[0], self.text_slice[1]],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        return cls(
            chunk_pairs=tuple((p[0], p[1]) for p in data["chunk_pairs"]),
            source_slice=tuple(data["source_slice"]),
            text_slice=(data["text_slice"][0], data["text_slice"][1]),
        )
