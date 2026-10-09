import pytest

from app.chunking import Chunk, XYWriter, detect_max_edit_span


def make_body(seed: str, sentences: int = 12) -> str:
    return "".join(f"{seed}第{i}句，情节推进到这里。主角的处境发生变化！" for i in range(sentences))


def make_pairs(n_pairs: int = 12, y_sentences: int = 12) -> list[tuple[str, str]]:
    pairs = []
    for i in range(n_pairs):
        x = f"第{i}段纲要：主角进入场景{i}，遭遇冲突，事件升级，出现转折。"
        y = make_body(f"章节{i}", y_sentences)
        pairs.append((x, y))
    return pairs


class TestBasicProperties:
    def test_x_y_views(self):
        pairs = [("纲一。", "文一。"), ("纲二。", "文二。")]
        w = XYWriter(pairs)
        assert w.x == "纲一。纲二。"
        assert w.y == "文一。文二。"
        assert w.x_len == 6
        assert w.y_len == 6
        assert w.pair_count == 2


class TestAlignSpan:
    def setup_method(self):
        self.w = XYWriter(make_pairs(3))

    def test_x_span_aligns_outward_to_pair_boundary(self):
        x_len0 = len(self.w.xy_pairs[0][0])
        aligned, pair_span = self.w.align_span(x_span=(10, x_len0 - 5))
        assert pair_span == (0, 1)
        assert aligned == (0, x_len0)

    def test_y_span_straddling_boundary(self):
        y0 = len(self.w.xy_pairs[0][1])
        aligned, pair_span = self.w.align_span(y_span=(y0 - 5, y0 + 5))
        assert pair_span == (0, 2)

    def test_errors(self):
        with pytest.raises(ValueError, match="至少提供一个"):
            self.w.align_span()
        with pytest.raises(ValueError, match="只能提供一个"):
            self.w.align_span(x_span=(0, 1), y_span=(0, 1))


class TestGetChunk:
    def setup_method(self):
        self.w = XYWriter(make_pairs(6))

    def test_pair_span_with_context(self):
        chunk = self.w.get_chunk(pair_span=(2, 3), context_length=1)
        # 文本区恰为 pair 2；上下文区为 pair 1..3
        assert chunk.text_pairs == (self.w.xy_pairs[2],)
        assert chunk.chunk_pairs == tuple(self.w.xy_pairs[1:4])
        assert chunk.text_source_slice == slice(2, 3)
        assert chunk.x_chunk == self.w.xy_pairs[2][0]
        assert chunk.y_chunk == self.w.xy_pairs[2][1]
        assert chunk.x_chunk_context == "".join(p[0] for p in self.w.xy_pairs[1:4])

    def test_pair_span_at_start_no_left_context(self):
        chunk = self.w.get_chunk(pair_span=(0, 1), context_length=2)
        assert chunk.source_slice == (0, 3)
        assert chunk.text_slice == (0, -2)

    def test_xy_span_smooth_alignment(self):
        x_total = self.w.x_len
        chunk = self.w.get_chunk(x_span=(100, 100 + 50), context_length=100)
        # 上下文窗口也按 pair 边界外扩
        start, stop = chunk.source_slice
        assert 0 <= start < stop <= self.w.pair_count
        assert chunk.text_source_slice.stop <= self.w.pair_count
        assert x_total == self.w.x_len  # writer 未被修改

    def test_edit_preserves_window(self):
        chunk = self.w.get_chunk(pair_span=(2, 3), context_length=1)
        new_chunk = chunk.edit(y_chunk="全新正文。")
        assert new_chunk.text_pairs[0][1] == "全新正文。"
        assert new_chunk.text_pairs[0][0] == self.w.xy_pairs[2][0]
        assert new_chunk.chunk_pairs[0] == self.w.xy_pairs[1]

    def test_to_dict_roundtrip(self):
        chunk = self.w.get_chunk(pair_span=(1, 2), context_length=1)
        assert Chunk.from_dict(chunk.to_dict()) == chunk


class TestGetChunksWindowSlicing:
    def test_full_coverage_and_contiguity(self):
        w = XYWriter(make_pairs(12))
        chunks = w.get_chunks()
        assert len(chunks) > 1
        spans = [c.text_source_slice for c in chunks]
        assert spans[0].start == 0
        assert spans[-1].stop == w.pair_count
        for prev, nxt in zip(spans, spans[1:]):
            assert nxt.start == prev.stop

    def test_context_bounded(self):
        w = XYWriter(make_pairs(12, y_sentences=30))
        chunks = w.get_chunks()
        max_x_pair = max(len(p[0]) for p in w.xy_pairs)
        max_y_pair = max(len(p[1]) for p in w.xy_pairs)
        # 真实契约：目标区(≤0.8×块长) + 两侧上下文(≤0.5×块长×2) + pair 对齐外扩(≤2×最大pair)
        bound_x = int(w.x_chunk_length * 0.8) + 2 * (w.x_chunk_length // 2) + 2 * max_x_pair + 10
        bound_y = int(w.y_chunk_length * 0.8) + 2 * (w.y_chunk_length // 2) + 2 * max_y_pair + 10
        for c in chunks:
            assert len(c.x_chunk_context) <= bound_x
            assert len(c.y_chunk_context) <= bound_y

    def test_x_only_mode_outline_generation(self):
        pairs = [(f"第{i}段纲要，事件推进。", "") for i in range(10)]
        w = XYWriter(pairs, x_chunk_length=40, y_chunk_length=40)
        chunks = w.get_chunks()
        assert len(chunks) >= 2
        spans = [c.text_source_slice for c in chunks]
        for prev, nxt in zip(spans, spans[1:]):
            assert nxt.start == prev.stop
        assert all(c.y_chunk == "" for c in chunks)

    def test_y_only_mode_chapter_generation(self):
        pairs = [("", make_body(f"段{i}", 8)) for i in range(10)]
        w = XYWriter(pairs, x_chunk_length=50, y_chunk_length=60)
        chunks = w.get_chunks()
        assert len(chunks) >= 2
        assert all(c.x_chunk == "" for c in chunks)
        spans = [c.text_source_slice for c in chunks]
        assert spans[-1].stop == w.pair_count

    def test_pair_span_subset(self):
        w = XYWriter(make_pairs(12))
        chunks = w.get_chunks(pair_span=(3, 7))
        assert chunks[0].text_source_slice.start >= 3
        assert chunks[-1].text_source_slice.stop <= 7

    def test_offset_ratio_overrides_first_window(self):
        # offset_ratio 的语义是“首窗尺寸覆盖”（如断点续跑跳过已生成前缀），不是放大
        w = XYWriter(make_pairs(12, y_sentences=4))
        plain = w.get_chunks()
        offset = w.get_chunks(offset_ratio=0.5)
        # pair 恰好 100 字 ⇒ 首窗 500 字对齐到 5 个 pair
        assert len(offset[0].y_chunk) == 500
        assert len(plain[0].y_chunk) == 800
        assert offset[1].text_source_slice.start == 5
        assert plain[1].text_source_slice.start == 8


class TestApplyChunks:
    def setup_method(self):
        self.w = XYWriter(make_pairs(12))

    def test_roundtrip_write_back(self):
        chunks = self.w.get_chunks()
        new_chunks = [c.edit(y_chunk=c.y_chunk.replace("。", "！")) for c in chunks]
        self.w.apply_chunks(chunks, new_chunks)

        # 全覆盖切片 ⇒ y 恰为新内容拼接，x 不动
        assert self.w.y == "".join(c.y_chunk for c in new_chunks)
        assert self.w.x == "".join(c.x_chunk for c in new_chunks)

    def test_roundtrip_keeps_pair_alignment(self):
        chunks = self.w.get_chunks()
        new_chunks = [c.edit(y_chunk="重写后的正文，情节不变。") for c in chunks]
        self.w.apply_chunks(chunks, new_chunks)
        assert all(y == "重写后的正文，情节不变。" for _, y in self.w.xy_pairs)
        # 重新切片仍完整覆盖
        spans = [c.text_source_slice for c in self.w.get_chunks()]
        assert spans[-1].stop == self.w.pair_count

    def test_partial_span_write_back(self):
        chunks = self.w.get_chunks(pair_span=(2, 5))
        new_chunks = [c.edit(y_chunk="中间替换。") for c in chunks]
        self.w.apply_chunks(chunks, new_chunks)
        assert self.w.y.startswith(make_body("章节0") + make_body("章节1"))
        assert "中间替换。" in self.w.y
        assert self.w.y.endswith(make_body("章节11"))

    def test_overlap_raises(self):
        a = self.w.get_chunk(pair_span=(0, 2))
        b = self.w.get_chunk(pair_span=(1, 3))
        with pytest.raises(ValueError, match="重叠"):
            self.w.apply_chunks([a, b], [a.edit(y_chunk="X"), b.edit(y_chunk="Y")])

    def test_fuzzy_relocate_after_insertion(self):
        chunks = self.w.get_chunks(pair_span=(2, 4))
        chunk = chunks[0]
        # 在头部插入一个 pair，使 text_source_slice 直接定位失效
        self.w.xy_pairs.insert(0, ("插入的纲要。", "插入的正文。"))
        pair_start, pair_end = self.w.get_chunk_pair_span(chunk)
        assert (pair_start, pair_end) == (chunk.text_source_slice.start + 1, chunk.text_source_slice.stop + 1)

    def test_apply_chunk_edit_convenience(self):
        chunk = self.w.get_chunk(pair_span=(0, 1))
        self.w.apply_chunk_edit(chunk, "y_chunk", "首段重写。")
        assert self.w.xy_pairs[0][1] == "首段重写。"
        with pytest.raises(ValueError, match="x_chunk / y_chunk"):
            self.w.apply_chunk_edit(chunk, "z_chunk", "bad")


class TestMapTextWoLLM:
    def test_oversized_x_split(self):
        long_x = make_body("纲", 20)
        w = XYWriter([(long_x, "")], x_chunk_length=50, y_chunk_length=1000)
        chunk = w.get_chunk(pair_span=(0, 1))
        new_chunk = w.map_text_wo_llm(chunk)
        assert len(new_chunk.text_pairs) > 1
        assert all(len(x) <= 50 for x, _ in new_chunk.text_pairs)
        assert "".join(x for x, _ in new_chunk.text_pairs) == long_x

    def test_oversized_y_split(self):
        long_y = make_body("文", 20)
        w = XYWriter([("", long_y)], x_chunk_length=1000, y_chunk_length=50)
        chunk = w.get_chunk(pair_span=(0, 1))
        new_chunk = w.map_text_wo_llm(chunk)
        assert len(new_chunk.text_pairs) > 1
        assert all(len(y) <= 50 for _, y in new_chunk.text_pairs)

    def test_both_oversized_raises(self):
        w = XYWriter([(make_body("纲", 20), make_body("文", 20))], x_chunk_length=50, y_chunk_length=50)
        chunk = w.get_chunk(pair_span=(0, 1))
        with pytest.raises(ValueError, match="窗口太小"):
            w.map_text_wo_llm(chunk)

    def test_normal_pairs_untouched(self):
        w = XYWriter(make_pairs(3), x_chunk_length=1000, y_chunk_length=1000)
        chunk = w.get_chunk(pair_span=(0, 2))
        new_chunk = w.map_text_wo_llm(chunk)
        assert new_chunk.text_pairs == chunk.text_pairs


class TestDetectMaxEditSpanIntegration:
    def test_locate_user_edit(self):
        original = make_body("章节0", 5)
        edited = original.replace("第3句", "第三句")
        prefix, neg_suffix = detect_max_edit_span(original, edited)
        assert original[:prefix] == edited[:prefix]
        assert original[neg_suffix:] == edited[neg_suffix:]
        # 改动被夹在 [prefix, neg_suffix) 内，区间小于全文
        assert prefix - neg_suffix < len(original)
