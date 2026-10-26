"""回归测试：JSON 提取器 + beats 累积 + 幕数不设上限。

覆盖三处真实缺陷：
1. `parse_kp_verdict` 用非贪婪正则抠 JSON，遇到嵌套对象/字符串里带花括号/
   输出前后带解释文字/围栏未闭合时全部匹配失败——实测 42 拍里 19 拍静默作废。
2. `step_beat` 用 `getattr(state, "beats", None) or []` 拿到临时列表，
   `beats.append(...)` 写进临时对象，state.beats 永远为空——KP 拿不到
   「已查清/未查清」，只能重复试探。
3. 梗概「最多 7 幕」写死 + `day_breakdown` 硬裁 7 段，第 8 幕之后内容丢失。
"""
from __future__ import annotations

import pytest

from app.pipeline.coc_simulator import (
    _extract_json_obj,
    _parse_day_breakdown,
    parse_kp_verdict,
    parse_pl_action,
)


FENCE = "```json\n"
FENCE_END = "\n```"


class TestExtractJsonObj:
    def test_plain_fenced(self):
        raw = FENCE + '{"beat": 1, "action": "推门"}' + FENCE_END
        assert _extract_json_obj(raw)["action"] == "推门"

    def test_nested_object(self):
        """嵌套对象：非贪婪正则会在第一个 } 就截断，抠出非法 JSON。"""
        raw = (FENCE + '{"beat": 1, "check": {"skill": "侦查", "difficulty": "困难", '
               '"success_effect": "a", "failure_effect": "b"}, "action": "查门框"}'
               + FENCE_END)
        obj = _extract_json_obj(raw)
        assert obj["action"] == "查门框"
        assert obj["check"]["skill"] == "侦查"
        assert obj["check"]["failure_effect"] == "b"

    def test_brace_inside_string_value(self):
        """字符串值里带花括号：正则会把中间的 } 当成对象结束。"""
        raw = FENCE + '{"beat": 1, "effect": "他在门框上刻下 {标记}", "action": "刻"}' + FENCE_END
        obj = _extract_json_obj(raw)
        assert obj["action"] == "刻"
        assert "标记" in obj["effect"]

    def test_leading_prose(self):
        """模型在 JSON 前面加了自己的说明文字。"""
        raw = "以下是本拍的裁决：\n\n" + FENCE + '{"beat": 1, "action": "撬锁"}' + FENCE_END
        assert _extract_json_obj(raw)["action"] == "撬锁"

    def test_trailing_prose(self):
        raw = FENCE + '{"beat": 1, "action": "撬锁"}' + FENCE_END + "\n\n本拍结束。"
        assert _extract_json_obj(raw)["action"] == "撬锁"

    def test_unclosed_fence(self):
        """输出被截断，围栏没闭合——正则要求 ``` 收尾，整段匹配不上。"""
        raw = FENCE + '{"beat": 1, "action": "撬锁"}'
        assert _extract_json_obj(raw)["action"] == "撬锁"

    def test_bare_json_no_fence(self):
        raw = '{"beat": 1, "action": "撬锁"}'
        assert _extract_json_obj(raw)["action"] == "撬锁"

    def test_raw_control_char_in_value(self):
        """字符串值里有未转义换行：标准 JSON 非法，但语义可救——
        清洗后应当被解析成真正的换行，而不是整拍作废。"""
        raw = FENCE + '{"beat": 1, "action": "第一行\n第二行"}' + FENCE_END
        obj = _extract_json_obj(raw)
        assert obj["action"] == "第一行\n第二行"

    def test_empty_returns_empty_dict(self):
        assert _extract_json_obj("") == {}
        assert _extract_json_obj("   ") == {}
        assert _extract_json_obj("没有任何 JSON 的一段话") == {}

    def test_not_a_dict_returns_empty(self):
        assert _extract_json_obj(FENCE + '[1, 2, 3]' + FENCE_END) == {}

    def test_first_object_wins(self):
        raw = (FENCE + '{"beat": 1, "action": "第一个"}' + FENCE_END + "\n"
               + FENCE + '{"beat": 2, "action": "第二个"}' + FENCE_END)
        assert _extract_json_obj(raw)["action"] == "第一个"


class TestParsersUseExtractor:
    def test_parse_kp_verdict_nested(self):
        raw = (FENCE + '{"beat": 1, "check": {"skill": "侦查"}, "action": "x"}'
               + FENCE_END)
        assert parse_kp_verdict(raw)["check"]["skill"] == "侦查"

    def test_parse_pl_action_leading_prose(self):
        raw = "好，我选择：\n" + FENCE + '{"action": "追问", "cost": "暴露"}' + FENCE_END
        obj = parse_pl_action(raw)
        assert obj["action"] == "追问"
        assert obj["cost"] == "暴露"

    def test_parsers_never_raise(self):
        for bad in ("", "```json\n{坏掉的\n```", "null", "[]"):
            assert isinstance(parse_kp_verdict(bad), dict)
            assert isinstance(parse_pl_action(bad), dict)


class TestDayBreakdownNoCap:
    def test_more_than_seven_acts_all_kept(self):
        """12 幕必须全部保留——旧实现用 [1-7] 正则 + range(1,8) 补位，
        第 8 幕之后的内容会被直接丢掉。"""
        text = "背景段。\n\n" + "\n\n".join(
            f"第{i}幕：这是第 {i} 幕的叙事段落，讲述调查员的行动与代价。" for i in range(1, 13)
        )
        out = _parse_day_breakdown(text)
        assert len(out) == 12
        assert [d["day"] for d in out] == list(range(1, 13))
        assert "第 12 幕" in out[-1]["summary"]

    def test_fewer_than_seven_acts_not_padded(self):
        """只有 3 幕时不该补出 4-7 的空段——旧实现会补 4 条空 summary。"""
        text = "背景段。\n\n" + "\n\n".join(
            f"第{i}幕：内容 {i}" for i in range(1, 4)
        )
        out = _parse_day_breakdown(text)
        assert len(out) == 3
        assert all(d["summary"] for d in out)

    def test_empty_input_returns_empty_list(self):
        assert _parse_day_breakdown("") == []

    def test_cn_numeral_acts(self):
        text = "第1幕：一\n\n第2幕：二\n\n第10幕：十"
        out = _parse_day_breakdown(text)
        assert [d["day"] for d in out] == [1, 2, 10]
