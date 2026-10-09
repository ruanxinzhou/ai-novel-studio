import json

import pytest

from app.generation import parse_repaired_json, repair_truncated_json, strip_code_fence


class TestStripCodeFence:
    def test_json_fence_stripped(self):
        raw = '```json\n[{"direction": "理性解谜派"}]\n```'
        assert json.loads(strip_code_fence(raw)) == [{"direction": "理性解谜派"}]

    def test_plain_fence_stripped(self):
        raw = "```\n{\"a\": 1}\n```"
        assert json.loads(strip_code_fence(raw)) == {"a": 1}

    def test_no_fence_unchanged(self):
        raw = '{"a": 1}'
        assert strip_code_fence(raw) == raw

    def test_unterminated_fence_opening_stripped(self):
        # 截断输出只有开头围栏：剥围栏头，闭合交给截断修复
        raw = '```json\n[{"a": 1}'
        assert strip_code_fence(raw) == '[{"a": 1}'
        assert json.loads(repair_truncated_json(strip_code_fence(raw))) == [{"a": 1}]


class TestParseRepairedJsonWithFence:
    def test_fenced_complete_json(self):
        raw = '```json\n[{"direction": "嘴炮社牛流", "hook": "唠晕古神"}]\n```'
        assert parse_repaired_json(raw) == [{"direction": "嘴炮社牛流", "hook": "唠晕古神"}]

    def test_fenced_truncated_json_repaired(self):
        raw = '```json\n[{"direction": "疯狂进化流", "premise": "主角每活过一次恐怖事件'
        data = parse_repaired_json(raw)
        assert data is not None and data[0]["premise"].startswith("主角每活过")

    def test_fenced_object(self):
        raw = '```json\n{"verdict": "pass"}\n```'
        assert parse_repaired_json(raw) == {"verdict": "pass"}


class TestRepairTruncatedJson:
    def test_complete_json_unchanged(self):
        raw = '{"name": "林越", "age": 18}'
        assert repair_truncated_json(raw) == raw

    def test_truncated_inside_string(self):
        raw = '{"name": "林越，代号青锋'
        repaired = repair_truncated_json(raw)
        assert repaired is not None
        assert repaired == raw + '"}'  # 只补闭合符
        assert json.loads(repaired)["name"] == "林越，代号青锋"

    def test_truncated_after_value(self):
        raw = '{"a": 1'
        repaired = repair_truncated_json(raw)
        assert repaired == raw + "}"
        assert json.loads(repaired) == {"a": 1}

    def test_truncated_inside_nested_array(self):
        raw = '{"list": [1, 2'
        repaired = repair_truncated_json(raw)
        assert repaired == raw + "]}"
        assert json.loads(repaired)["list"] == [1, 2]

    def test_truncated_string_inside_array(self):
        raw = '{"roles": ["林越", "厉飞'
        repaired = repair_truncated_json(raw)
        assert json.loads(repaired)["roles"] == ["林越", "厉飞"]

    def test_cjk_content_preserved(self):
        raw = '{"事件": "主角在青云宗外门大比中一鸣惊人，击败了厉飞雨"'
        repaired = repair_truncated_json(raw)
        assert repaired is not None and repaired.startswith(raw)
        assert "青云宗" in json.loads(repaired)["事件"]

    @pytest.mark.parametrize(
        "raw",
        [
            '{"a":',  # 悬空键——补闭合也是坏 JSON，拒绝
            '{"a": 1,}',  # 尾逗号——不是截断形态
            '{"a": ]',  # 括号不匹配
            "[1, 2}",  # 交叉闭合
            '{"a": "x\\',  # 悬空转义符
        ],
    )
    def test_unrepairable_returns_none(self, raw):
        assert repair_truncated_json(raw) is None

    def test_truncated_right_after_comma(self):
        # 常见截断点：恰好断在逗号后——悬空逗号可丢弃，标量不受损
        assert parse_repaired_json('{"v": [1,') == {"v": [1]}
        assert parse_repaired_json('{"a": "甲", "b": "乙') == {"a": "甲", "b": "乙"}

    def test_anti_tamper_suffix_only(self):
        raw = '{"k": [true, false, null'
        repaired = repair_truncated_json(raw)
        assert repaired is not None
        assert repaired[: len(raw)] == raw  # 原有字符一个不动


class TestParseRepairedJson:
    def test_repairable_path(self):
        assert parse_repaired_json('{"v": [1,') == {"v": [1]}

    def test_unrepairable_path(self):
        assert parse_repaired_json('{"a":') is None

    def test_valid_json_direct(self):
        assert parse_repaired_json('{"ok": true}') == {"ok": True}
