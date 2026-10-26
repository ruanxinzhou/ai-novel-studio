"""副本跑团 · beat 引擎与通稿层测试（第二步）。

对应设计文档：docs/superpowers/specs/2026-10-22-coc-beat-engine-design.md

三条核心断言：
1. **beat 是计数单位**，不再拿 pair_limit 当语义进度；
2. **解析失败不炸**：KP 不给合法 JSON 时降级叙事，story_phase 不猜；
3. **通稿不能伪装成功**：生成失败要如实标注降级，不能拿半截稿充数。
"""
from __future__ import annotations

import pytest

from app.fields.coc_run import CocRunState
from app.pipeline.coc_simulator import (
    BEAT_MAX,
    BEAT_MIN,
    _check_termination,
    _should_ask_phase,
    estimate_beat_budget,
    narrative_violations,
    parse_kp_dual_output,
    sanitize_narrative,
)


# ============================================================
# 一、beat 预算估算（不依赖不存在的字段）
# ============================================================

def test_beat_budget_uses_real_signals():
    """上游 acts×4+clues×2+8 依赖不存在的键，这里用 NPC/调查员/文本长度。"""
    small = estimate_beat_budget({"cast": []}, [{"name": "A"}])
    big = estimate_beat_budget(
        {"cast": [{"name": f"N{i}"} for i in range(6)],
         "premise": "长" * 3000},
        [{"name": "A"}, {"name": "B"}],
    )
    assert BEAT_MIN <= small <= BEAT_MAX
    assert big > small, "模组越大预算必须越高"


def test_beat_budget_clamps_to_range():
    """空输入也不能给 0 或负数——0 预算会让跑团一拍都不跑。"""
    assert estimate_beat_budget(None, None) >= BEAT_MIN
    huge = estimate_beat_budget(
        {"cast": [{"name": f"N{i}"} for i in range(500)], "premise": "长" * 99999},
        [{"name": "P"}] * 40,
    )
    assert huge <= BEAT_MAX


def test_beat_budget_tolerates_malformed_cast():
    """cast 不是 list 时不能崩——它直接来自 LLM 输出。"""
    assert estimate_beat_budget({"cast": "不是列表"}, None) >= BEAT_MIN
    assert estimate_beat_budget({"cast": [None, 3, "x"]}, None) >= BEAT_MIN


# ============================================================
# 二、KP 双输出解析与容错
# ============================================================

def test_dual_output_parses_json_and_narrative():
    text = (
        "先说结论。\n\n"
        "```json\n"
        '{"beat": 3, "information_gained": "确认井里有声音",'
        ' "known_now": ["井里有声音"], "unknown_now": ["声音来源"],'
        ' "story_phase": "转折点"}\n'
        "```\n\n"
        "雨停了。老周把灯举高，光落在井口的青苔上。"
    )
    verdict, narrative = parse_kp_dual_output(text)
    assert verdict.get("information_gained") == "确认井里有声音"
    assert verdict.get("story_phase") == "转折点"
    assert "老周把灯举高" in narrative
    assert "```json" not in narrative, "JSON 块不能漏进叙事"


def test_dual_output_degrades_without_json():
    """模型只给了散文时，不能抛异常——退化成「全当叙事」。"""
    verdict, narrative = parse_kp_dual_output("就是一段普通叙事，没有任何结构。")
    assert verdict == {}
    assert "普通叙事" in narrative


def test_dual_output_tolerates_broken_json():
    """JSON 语法错误是常态，不是异常路径。"""
    verdict, narrative = parse_kp_dual_output(
        '```json\n{"beat": 2, "information_gained":\n```\n\n后面的叙事。'
    )
    assert verdict == {}
    assert narrative


def test_dual_output_never_returns_none():
    """契约：永远返回 (dict, str)，不允许 None——调用方不做 None 检查。"""
    for text in ("", "   ", None, 123):
        verdict, narrative = parse_kp_dual_output(text)
        assert isinstance(verdict, dict)
        assert isinstance(narrative, str)


# ============================================================
# 三、story_phase 双轨制
# ============================================================

def test_ask_phase_every_five_beats():
    assert _should_ask_phase(0) is True
    assert _should_ask_phase(4) is False
    assert _should_ask_phase(5) is True


def test_story_phase_completes_run():
    """story_phase=已收束 → story_complete，不是硬截断。"""
    s = CocRunState(field_no=1)
    s.beats_done = 6
    s.beat_budget = 20
    s.last_story_phase = "已收束"
    s.players_state = [{"name": "A", "san": 50, "hp": 8, "alive": True}]
    reason = _check_termination(s, last_phase="已收束")
    assert reason == "story_complete"


def test_hard_cap_is_not_story_complete():
    """撞预算必须说 hard_cap，不能假装故事讲完了。"""
    s = CocRunState(field_no=1)
    s.beats_done = 20
    s.beat_budget = 20
    s.last_story_phase = "展开中"
    s.players_state = [{"name": "A", "san": 50, "hp": 8, "alive": True}]
    assert _check_termination(s, last_phase="展开中") == "hard_cap"


def test_story_phase_wins_over_hard_cap():
    """最后一拍同时收束+撞预算：语义完成优先，但仍是完成不是截断。"""
    s = CocRunState(field_no=1)
    s.beats_done = 20
    s.beat_budget = 20
    s.last_story_phase = "已收束"
    s.players_state = [{"name": "A", "san": 50, "hp": 8, "alive": True}]
    assert _check_termination(s, last_phase="已收束") == "story_complete"


def test_blank_phase_inherits_previous():
    """留空 story_phase 按展开中处理（双轨制：不猜）。"""
    s = CocRunState(field_no=1)
    s.beats_done = 3
    s.beat_budget = 20
    s.last_story_phase = ""
    s.players_state = [{"name": "A", "san": 50, "hp": 8, "alive": True}]
    assert _check_termination(s, last_phase="") is None


def test_all_players_down_still_terminates():
    """全灭优先于一切其他判定。"""
    s = CocRunState(field_no=1)
    s.beats_done = 2
    s.beat_budget = 20
    s.players_state = [{"name": "A", "alive": False, "san": 0, "hp": 0}]
    assert _check_termination(s, last_phase="展开中") == "dead"


# ============================================================
# 四、通稿清洗（人名规则 + 禁止项）
# ============================================================

def test_sanitize_removes_day_markers():
    out = sanitize_narrative("Day3 的夜里，Day7 他终于明白。")
    assert "Day" not in out
    assert "第三天" in out


def test_sanitize_removes_dice_and_stats():
    out = sanitize_narrative("他投掷 1D100，投掷 大成功，SAN 掉到 30，HP 剩下 2。")
    assert "1D100" not in out
    assert "大成功" not in out
    assert "SAN" not in out
    assert "HP" not in out


def test_sanitize_removes_table_terminology():
    out = sanitize_narrative("KP 让他掷骰，PL 选择了回避，守密人宣布失败。")
    assert "KP" not in out and "PL" not in out and "守密人" not in out


def test_sanitize_keeps_story_content():
    """清洗只动桌游残留，绝不删叙事本身。"""
    text = "老周把灯举高，光落在井口的青苔上，井底传来一声闷响。"
    assert sanitize_narrative(text) == text


def test_sanitize_handles_empty():
    assert sanitize_narrative("") == ""
    assert sanitize_narrative(None) == ""


# ============================================================
# 五、人名规则检测（只检测，不改写）
# ============================================================

def test_detects_surname_substitution():
    """全名没出现但姓氏出现 → 疑似被写成「老周」。"""
    text = "老周把灯举高，光落在井口的青苔上。他身后站着另一个人。"
    issues = narrative_violations(text, ["周成德"])
    assert any("周成德" in i for i in issues)


def test_passes_when_full_name_present():
    text = "周成德把灯举高。井口的青苔上落着水。"
    assert narrative_violations(text, ["周成德"]) == []


def test_ignores_absent_characters():
    """没出场的人不该被判违规——那会让噪声淹没真问题。"""
    text = "老陈站在门口。雨下了一整夜。"
    assert narrative_violations(text, ["周成德"]) == []


def test_detects_residual_day_and_stats():
    text = "Day2 清晨，他的 SAN 已经掉到 12。"
    issues = narrative_violations(text, [])
    assert any("Day" in i or "SAN" in i for i in issues)


def test_violations_on_empty_is_error():
    assert narrative_violations("", []) == ["通稿为空"]


# ============================================================
# 六、通稿降级不伪装成功
# ============================================================

def test_fallback_marks_degradation():
    """降级稿必须自带「这不是通稿」的声明，不能冒充完整产物。"""
    from app.pipeline.coc_simulator import _fallback_narrative

    s = CocRunState(field_no=1)
    s.add_turn("keeper", "雨停了。", meta={})
    out = _fallback_narrative(s)
    assert "不构成完整通稿" in out
    assert "雨停了" in out


def test_fallback_with_no_keeper_turns():
    from app.pipeline.coc_simulator import _fallback_narrative

    s = CocRunState(field_no=1)
    out = _fallback_narrative(s)
    assert out and "无法生成通稿" in out


# ============================================================
# 七、卡片暴露 beat 进度（作者要能判断「讲够了没」）
# ============================================================

def _card(**kw):
    from app.pipeline.coc_simulator import _summary_card_from_state

    s = CocRunState(field_no=1)
    s.players_state = [{"name": "A", "san": 50, "hp": 8, "alive": True}]
    s.player_cards = [{"name": "A", "occupation": "记者"}]
    for k, v in kw.items():
        setattr(s, k, v)
    return _summary_card_from_state(s, "梗概正文。", s.beats_done * 2)


def test_card_exposes_beat_progress():
    card = _card(beats_done=7, beat_budget=20, last_story_phase="转折点")
    assert card["beats_done"] == 7
    assert card["beat_budget"] == 20
    assert card["story_phase"] == "转折点"


def test_card_distinguishes_complete_from_truncated():
    """story_complete 与 hard_cap 对底本的价值完全不同，必须分开报。"""
    done = _card(beats_done=20, beat_budget=20, last_story_phase="已收束")
    cut = _card(beats_done=20, beat_budget=20, last_story_phase="展开中")
    assert done["ending_reason"] == "story_complete"
    assert cut["ending_reason"] == "hard_cap"


def test_card_reports_no_beat_run():
    """0 拍=线程没跑起来，不能报成「作者主动停止」。"""
    card = _card(beats_done=0, beat_budget=20, last_story_phase="")
    assert card["ending_reason"] == "no_beat"


def test_card_carries_full_narrative():
    card = _card(full_narrative="通稿正文。")
    assert card["full_narrative"] == "通稿正文。"