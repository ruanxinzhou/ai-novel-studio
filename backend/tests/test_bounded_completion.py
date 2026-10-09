from app.generation import (
    BOUNDED,
    COMPLETE,
    EXHAUSTED,
    NO_PROGRESS,
    complete_prose,
    complete_structured,
    strip_overlap,
)


def scripted_prose(steps: list[tuple[str, str]]):
    """按调用次序回放 (text, finish_reason)；记录每次收到的 accumulated。"""
    calls: list[str | None] = []
    iterator = iter(steps)

    def generate(accumulated: str | None) -> tuple[str, str]:
        calls.append(accumulated)
        return next(iterator)

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


class TestStripOverlap:
    def test_removes_longest_overlap(self):
        assert strip_overlap("前文。结尾句。", "结尾句。新内容。") == "新内容。"

    def test_no_overlap_unchanged(self):
        assert strip_overlap("前文。", "全新内容。") == "全新内容。"

    def test_overlap_capped(self):
        big = "重" * 300
        # 搜索上限 200：只削掉 200 字重叠，保留更早的部分
        assert strip_overlap(big, big + "后续") == "重" * 100 + "后续"


class TestCompleteProse:
    def test_stop_first_try(self):
        gen = scripted_prose([("完整正文。", "stop")])
        result = complete_prose(gen)
        assert result.status == COMPLETE
        assert result.attempts == 1
        assert result.text == "完整正文。"
        assert gen.calls[0] is None  # 首次不带积累文本

    def test_length_then_stop(self):
        gen = scripted_prose([("上半段，情节继续展开不收束。", "length"), ("下半段终于收尾。", "stop")])
        result = complete_prose(gen)
        assert result.status == COMPLETE
        assert result.attempts == 2
        assert result.text == "上半段，情节继续展开不收束。下半段终于收尾。"
        assert gen.calls[1] == "上半段，情节继续展开不收束。"  # 续写时传入积累文本

    def test_overlap_dedup_joins(self):
        gen = scripted_prose(
            [
                ("正文 A，最后一句是收束句。", "length"),
                ("收束句。接着写全新的情节内容继续推进。", "stop"),
            ]
        )
        result = complete_prose(gen)
        assert result.text == "正文 A，最后一句是收束句。接着写全新的情节内容继续推进。"
        assert result.dedup_overlaps == 1

    def test_bounded_after_max_continuations(self):
        # 每次续写都有 ≥10 字新增但永远截断 → 打满 1+3 次后有界停
        parts = ["甲" * 12, "乙" * 12, "丙" * 12, "丁" * 12]
        steps = []
        acc = ""
        for i, part in enumerate(parts):
            acc += part
            steps.append((acc, "length"))
        gen = scripted_prose(steps)
        result = complete_prose(gen, max_continuations=3)
        assert result.status == BOUNDED
        assert result.attempts == 4
        assert result.text == "".join(parts)  # 部分成果保留

    def test_no_progress_stops_early(self):
        # 续写只重复了重叠部分（新增 < 10 字）且仍截断 → 无进展即停
        gen = scripted_prose(
            [
                ("正文开头，情节向前推进了一大段。", "length"),
                ("正文开头，情节向前推进了一大段。", "length"),
            ]
        )
        result = complete_prose(gen, min_progress_chars=10)
        assert result.status == NO_PROGRESS
        assert result.attempts == 2


class TestCompleteStructured:
    def test_valid_first_try(self):
        result = complete_structured(lambda: '{"ok": true}')
        assert result.status == COMPLETE
        assert result.data == {"ok": True}
        assert result.attempts == 1

    def test_repairable_truncation_counts_as_success(self):
        result = complete_structured(lambda: '{"items": ["甲", "乙"')
        assert result.status == COMPLETE
        assert result.data == {"items": ["甲", "乙"]}

    def test_bad_then_good(self):
        outputs = iter(['{"bad":', '{"good": 1}'])
        result = complete_structured(lambda: next(outputs))
        assert result.status == COMPLETE
        assert result.attempts == 2
        assert result.data == {"good": 1}

    def test_exhausted_after_max_regenerations(self):
        # 每次坏得不一样 → 打满 1+2 次重生成后穷尽
        outputs = iter(['{"bad一":', '{"bad二":', '{"bad三":'])
        result = complete_structured(lambda: next(outputs), max_regenerations=2)
        assert result.status == EXHAUSTED
        assert result.attempts == 3

    def test_identical_bad_output_stops_early(self):
        counter = {"n": 0}

        def generate() -> str:
            counter["n"] += 1
            return '{"same":'

        result = complete_structured(generate, max_regenerations=5)
        assert result.status == NO_PROGRESS
        assert result.attempts == 2
        assert counter["n"] == 2
