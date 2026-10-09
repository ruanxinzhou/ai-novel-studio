import pytest

from app.context import (
    Material,
    MaterialAssembler,
    MaterialCapacityError,
    MaterialFamily,
    truncate_tail_at_sentence,
    utf8_size,
)


def mat(sid: str, family: MaterialFamily, content: str, keywords: tuple[str, ...] = (), title: str = "") -> Material:
    return Material(source_id=sid, family=family, content=content, keywords=keywords, title=title)


class TestTruncateTailAtSentence:
    def test_short_text_unchanged(self):
        assert truncate_tail_at_sentence("短文。", 100) == "短文。"

    def test_tail_starts_after_sentence_boundary(self):
        text = "第一句。" * 50  # 每句 4 字，共 200 字
        result = truncate_tail_at_sentence(text, 100)
        assert len(result) <= 100
        assert result.startswith("第")  # 从句边界后起头，不缺句首
        assert text.endswith(result)

    def test_no_boundary_falls_back_to_raw_slice(self):
        text = "无标点" * 100
        assert truncate_tail_at_sentence(text, 50) == text[-50:]

    def test_empty_text(self):
        assert truncate_tail_at_sentence("", 100) == ""


class TestRequiredAlwaysIncluded:
    def test_required_and_anchor_fit(self):
        assembler = MaterialAssembler(max_bytes=10_000)
        snapshot = assembler.assemble(
            [mat("r1", MaterialFamily.REQUIRED, "主角设定：林越，十八岁。")],
            anchor_text="上一章结尾，他握紧了剑。",
        )
        assert snapshot.anchor == "上一章结尾，他握紧了剑。"
        assert [m.source_id for m in snapshot.included] == ["r1"]
        assert snapshot.omissions == ()
        assert snapshot.used_bytes == utf8_size("上一章结尾，他握紧了剑。主角设定：林越，十八岁。")

    def test_required_overflow_raises_loudly(self):
        assembler = MaterialAssembler(max_bytes=50)
        with pytest.raises(MaterialCapacityError) as exc_info:
            assembler.assemble(
                [mat("r1", MaterialFamily.REQUIRED, "设定" * 20), mat("r2", MaterialFamily.REQUIRED, "设定" * 20)]
            )
        assert set(exc_info.value.source_ids) == {"r1", "r2"}
        assert exc_info.value.overflow_bytes > 0

    def test_anchor_alone_overflow_raises(self):
        assembler = MaterialAssembler(max_bytes=30, anchor_max_chars=1000)
        with pytest.raises(MaterialCapacityError):
            assembler.assemble([], anchor_text="这是一个远超三十字节预算的锚点文本，必须失败而不是被截断。")


class TestOptionalSelection:
    def test_family_priority_finalized_before_candidate_before_reference(self):
        # 预算只够装一条（每条约 30 字节）：三条同等相关度，应装入定稿
        assembler = MaterialAssembler(max_bytes=50)
        snapshot = assembler.assemble(
            [
                mat("ref1", MaterialFamily.REFERENCE, "参考资料内容，提到主角。"),
                mat("cand1", MaterialFamily.CANDIDATE, "候选稿内容，提到主角。"),
                mat("fin1", MaterialFamily.FINALIZED, "定稿内容，提到主角。"),
            ]
        )
        assert [m.source_id for m in snapshot.included] == ["fin1"]
        omitted_ids = {o.source_id for o in snapshot.omissions}
        assert omitted_ids == {"ref1", "cand1"}

    def test_relevance_orders_within_family(self):
        assembler = MaterialAssembler(max_bytes=300)
        snapshot = assembler.assemble(
            [
                mat("low", MaterialFamily.FINALIZED, "普通内容。"),
                mat("high", MaterialFamily.FINALIZED, "林越与厉飞雨在青云宗比试剑法。", keywords=("厉飞雨", "剑法")),
            ]
        )
        assert [m.source_id for m in snapshot.included][0] == "high"

    def test_capacity_skip_continues_to_smaller_material(self):
        # 大材料装不下被记收据，但不阻断后续小材料装入（first-fit 继续）
        assembler = MaterialAssembler(max_bytes=200)
        snapshot = assembler.assemble(
            [
                mat("big", MaterialFamily.FINALIZED, "大材料" * 30),
                mat("small", MaterialFamily.FINALIZED, "小材料"),
            ]
        )
        included_ids = [m.source_id for m in snapshot.included]
        assert included_ids == ["small"]
        assert len(snapshot.omissions) == 1
        assert snapshot.omissions[0].source_id == "big"
        assert snapshot.omissions[0].reason == "capacity"

    def test_optional_never_truncated_partially(self):
        # 可选材料要么整体进入，要么整体省略——不出现半截内容
        assembler = MaterialAssembler(max_bytes=60)
        snapshot = assembler.assemble([mat("opt", MaterialFamily.REFERENCE, "完全装不下的长材料" * 10)])
        assert snapshot.included == ()
        assert snapshot.omissions[0].byte_size == utf8_size("完全装不下的长材料" * 10)


class TestValidation:
    def test_duplicate_source_id_rejected(self):
        assembler = MaterialAssembler(max_bytes=10_000)
        with pytest.raises(ValueError, match="source_id 重复"):
            assembler.assemble(
                [mat("a", MaterialFamily.REQUIRED, "内容一。"), mat("a", MaterialFamily.FINALIZED, "内容二。")]
            )

    def test_empty_content_rejected(self):
        assembler = MaterialAssembler(max_bytes=10_000)
        with pytest.raises(ValueError, match="内容为空"):
            assembler.assemble([mat("empty", MaterialFamily.FINALIZED, "")])

    def test_non_positive_budget_rejected(self):
        with pytest.raises(ValueError, match="max_bytes"):
            MaterialAssembler(max_bytes=0)


class TestSnapshotDeterminism:
    def test_same_input_same_digest(self):
        materials = [
            mat("r1", MaterialFamily.REQUIRED, "必需设定。"),
            mat("f1", MaterialFamily.FINALIZED, "定稿一段。", keywords=("林越",)),
            mat("c1", MaterialFamily.CANDIDATE, "候选一段。"),
        ]
        s1 = MaterialAssembler(max_bytes=10_000).assemble(materials, anchor_text="锚点。")
        s2 = MaterialAssembler(max_bytes=10_000).assemble(list(materials), anchor_text="锚点。")
        assert s1.digest() == s2.digest()

    def test_different_omission_changes_digest(self):
        base = [mat("f1", MaterialFamily.FINALIZED, "定稿。"), mat("f2", MaterialFamily.FINALIZED, "第二条定稿。")]
        tight = MaterialAssembler(max_bytes=30).assemble(base)
        loose = MaterialAssembler(max_bytes=10_000).assemble(base)
        assert tight.digest() != loose.digest()

    def test_render_sections_covers_all_families(self):
        snapshot = MaterialAssembler(max_bytes=10_000).assemble(
            [
                mat("r1", MaterialFamily.REQUIRED, "设定。", title="角色卡"),
                mat("f1", MaterialFamily.FINALIZED, "定稿。"),
                mat("c1", MaterialFamily.CANDIDATE, "候选。"),
                mat("ref1", MaterialFamily.REFERENCE, "参考。"),
            ],
            anchor_text="锚点。",
        )
        sections = snapshot.render_sections()
        assert "锚点·上一章结尾" in sections
        assert "必需材料" in sections and "【角色卡】" in sections["必需材料"]
        assert "定稿摘录" in sections
        assert "候选稿摘录" in sections
        assert "参考资料" in sections

    def test_used_bytes_matches_content(self):
        snapshot = MaterialAssembler(max_bytes=10_000).assemble(
            [mat("r1", MaterialFamily.REQUIRED, "设定内容。"), mat("f1", MaterialFamily.FINALIZED, "定稿。")],
            anchor_text="锚点文本。",
        )
        expected = utf8_size("锚点文本。设定内容。定稿。")
        assert snapshot.used_bytes == expected
        assert snapshot.used_bytes <= snapshot.budget_bytes
