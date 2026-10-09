import pytest

from app.chunking.slicing import (
    detect_max_edit_span,
    split_text_into_chunks,
    split_text_into_paragraphs,
)


class TestSplitTextIntoChunks:
    def test_simple_paragraph_split(self):
        text = "这是第一段。这是第二段。这是第三段。"
        result = split_text_into_chunks(text, max_chunk_size=10, min_chunk_n=3)
        assert len(result) == 3
        assert "".join(result) == text

    def test_long_paragraph_split_respects_max_size(self):
        text = (
            "这是一个很长的段落，包含了很多句子。它应该被分割成多个小块。"
            "这里有一些标点符号，比如句号。还有问号？以及分号；这些都可以用来分割文本。"
        )
        result = split_text_into_chunks(text, max_chunk_size=20, min_chunk_n=4)
        assert len(result) >= 4
        assert all(len(chunk) <= 20 for chunk in result)
        assert "".join(result) == text

    def test_newline_paragraphs_merged_when_too_short(self):
        text = "第一段。\n\n第二段。\n第三段。\n\n第四段很长，需要被分割。这是第四段的继续。"
        result = split_text_into_chunks(text, max_chunk_size=15, min_chunk_n=5)
        assert len(result) >= 5
        assert "".join(result) == text

    def test_merge_under_min_chunk_size(self):
        # min_chunk_size 约束的是全文总量，单段过短时向相邻合并
        text = "短一。\n短二。\n短三。"
        result = split_text_into_chunks(text, max_chunk_size=100, min_chunk_n=1, min_chunk_size=8)
        assert len(result) == 1
        assert "".join(result) == text

    def test_no_split_point_raises(self):
        text = "一二三四五六七八九十百千万亿"
        with pytest.raises(ValueError, match="没有找到分割点"):
            split_text_into_chunks(text, max_chunk_size=5, min_chunk_n=1)

    def test_split_respects_max_chunk_n(self):
        text = "。" * 0 + "甲乙丙丁。甲乙丙丁。甲乙丙丁。甲乙丙丁。甲乙丙丁。甲乙丙丁。"
        result = split_text_into_chunks(text, max_chunk_size=100, min_chunk_n=1, max_chunk_n=2)
        assert len(result) <= 2
        assert "".join(result) == text

    def test_empty_text_raises_on_min_size(self):
        with pytest.raises(ValueError, match="min_chunk_size"):
            split_text_into_chunks("", max_chunk_size=10, min_chunk_n=1, min_chunk_size=5)


class TestSplitParagraphs:
    def test_paragraph_separators_kept(self):
        text = "A段\nB段\n\nC段"
        paras = split_text_into_paragraphs(text)
        assert "".join(paras) == text
        assert len(paras) == 3


class TestDetectMaxEditSpan:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            # 返回值第二个元素为负索引语义，可直接用作切片右端
            ("我吃西红柿", "我不喜欢吃西红柿", (1, -4)),
            ("我吃西红柿", "不喜欢吃西红柿", (0, -4)),
            ("我吃西红柿", "我不喜欢吃", (1, 0)),
            ("我吃西红柿", "你不喜欢吃西瓜", (0, 0)),
            ("同一文本", "同一文本", (4, 0)),
        ],
    )
    def test_cases(self, a, b, expected):
        assert detect_max_edit_span(a, b) == expected
