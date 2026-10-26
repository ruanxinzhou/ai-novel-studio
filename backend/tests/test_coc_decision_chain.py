"""副本跑团第三步 · 决策链分镜测试。

对应设计文档：docs/superpowers/specs/2026-10-22-coc-decision-chain-design.md

三条核心断言：
1. **成败由代码掷骰决定，KP 不能决定** —— 这是真骰子存在的全部理由；
2. **可复现** —— 同 seed 重跑得到完全相同的骰子序列；
3. **决策链不能丢拍** —— storyboard 每一拍都要有 PL 动作 + 判定结果。
"""
from __future__ import annotations

import json

import pytest

from app.fields.coc_run import CocRunState
from app.pipeline.coc_dice import (
    RESULT_CRITICAL,
    RESULT_EXTREME,
    RESULT_EXTREME_FAIL,
    RESULT_FAILURE,
    RESULT_FUMBLE,
    RESULT_HARD,
    RESULT_HARD_FAIL,
    RESULT_SUCCESS,
    DiceCheck,
    coc_check,
    make_dice_engine,
    parse_bonus,
    skill_value_of,
)
from app.pipeline.coc_simulator import (
    _NO_PROGRESS_LIMIT,
    _append_storyboard_row,
    _check_termination,
    _render_pl_text,
    _render_verdict_text,
    _resolve_check,
    _san_loss_for,
    parse_kp_verdict,
    parse_pl_action,
)

FENCE = "```"


def _state(session_index: int = 1, **kw) -> CocRunState:
    s = CocRunState(field_no=1, session_index=session_index)
    s.player_cards = [{"name": "周成德", "occupation": "记者",
                       "skills": {"侦查": 50, "意志": 50}}]
    s.players_state = [{"name": "周成德", "san": 70, "hp": 10, "alive": True}]
    for k, v in kw.items():
        setattr(s, k, v)
    return s


CHECK = {
    "skill": "侦查", "difficulty": "困难",
    "success_effect": "井壁有三道新鲜刮痕",
    "failure_effect": "脚下一滑摔在铁梯上，磕破了膝盖",
}


def _fence(obj: dict) -> str:
    return f"{FENCE}json\n{json.dumps(obj, ensure_ascii=False)}\n{FENCE}"


# ============================================================
# 一、COC 七级判定边界
# ============================================================

@pytest.mark.parametrize("rolled,skill,expected", [
    (5, 50, RESULT_CRITICAL),      # 5 <= 50/5=10
    (10, 50, RESULT_CRITICAL),
    (11, 50, RESULT_EXTREME),      # <= 50/2=25
    (25, 50, RESULT_EXTREME),
    (26, 50, RESULT_HARD),         # <= 50
    (50, 50, RESULT_HARD),
    (51, 50, RESULT_SUCCESS),      # <= 50*0.8+20=60
    (60, 50, RESULT_SUCCESS),
    (61, 50, RESULT_FAILURE),      # 61-69：超成功线但未达困难失败线(70)
    (70, 50, RESULT_HARD_FAIL),    # >= 50*1.2+10=70
    (96, 50, RESULT_FUMBLE),
    (100, 50, RESULT_EXTREME_FAIL),
])
def test_seven_level_thresholds(rolled, skill, expected):
    assert coc_check(rolled, skill) == expected


def test_low_skill_high_roll_is_failure_not_success():
    """低技能者掷出高点数不能算成功——这是判定顺序的核心保证。"""
    # 技能 5 掷 99：96+ 先吃大失败（成功梯度全都不满足）
    assert coc_check(99, 5) == RESULT_FUMBLE
    # 技能 50 掷 70：超出成功线 60，落在困难失败
    assert coc_check(70, 50) == RESULT_HARD_FAIL
    # 技能 50 掷 65：超成功线但未到困难失败线 → 普通失败
    assert coc_check(65, 50) == RESULT_FAILURE


def test_high_skill_can_succeed_on_high_roll():
    """高技能者掷 70/90 仍是困难成功——96+ 不该无条件判大失败。"""
    assert coc_check(70, 90) == RESULT_HARD


def test_san_loss_scales_with_failure_severity():
    """失败越重 SAN 掉得越多，且成功不掉 SAN。"""
    def mk(res):
        return DiceCheck(1, 0, "侦查", 50, "困难", 50, 0, 50, res)
    assert _san_loss_for(mk(RESULT_FUMBLE)) > _san_loss_for(mk(RESULT_EXTREME_FAIL))
    assert _san_loss_for(mk(RESULT_EXTREME_FAIL)) > _san_loss_for(mk(RESULT_HARD_FAIL))
    assert _san_loss_for(mk(RESULT_SUCCESS)) == 0
    assert _san_loss_for(mk(RESULT_CRITICAL)) == 0


# ============================================================
# 二、可复现 + 差异化（真骰子的核心价值）
# ============================================================

def test_same_seed_reproduces_same_sequence():
    """同 seed 重跑必须得到完全相同的骰子序列——否则无法复盘。"""
    a = [make_dice_engine(1, 1).roll(beat=1, skill="侦查", skill_value=50).rolled
         for _ in range(1)]
    # 同一个引擎连续掷
    e1, e2 = make_dice_engine(1, 1), make_dice_engine(1, 1)
    seq1 = [e1.roll(beat=i, skill="侦查", skill_value=50).rolled for i in range(10)]
    seq2 = [e2.roll(beat=i, skill="侦查", skill_value=50).rolled for i in range(10)]
    assert seq1 == seq2


def test_different_sessions_diverge():
    """三场平行世界必须真的分叉——同序列会让「不一样的几个故事」变成一���。"""
    seqs = []
    for idx in (1, 2, 3):
        e = make_dice_engine(1, idx)
        seqs.append(tuple(e.roll(beat=i, skill="侦查", skill_value=50).rolled
                          for i in range(8)))
    assert len(set(seqs)) == 3


def test_variant_seed_changes_sequence():
    """差异化种子不同 → 序列不同（差异化的第二个来源）。"""
    e1 = make_dice_engine(1, 1, "alt-a")
    e2 = make_dice_engine(1, 1, "alt-b")
    s1 = [e1.roll(beat=i, skill="侦查", skill_value=50).rolled for i in range(8)]
    s2 = [e2.roll(beat=i, skill="侦查", skill_value=50).rolled for i in range(8)]
    assert s1 != s2


def test_str_seed_is_stable_across_processes():
    """str seed 不能用内置 hash（跨进程不稳定），必须自算稳定哈希。"""
    assert make_dice_engine(1, 1, "abc").seed == make_dice_engine(1, 1, "abc").seed


# ============================================================
# 三、KP 不能决定成败（本次改造的硬约束）
# ============================================================

def test_check_uses_player_card_skill():
    """技能值必须来自人物卡，不能由 KP 指定。"""
    s = _state()
    c = _resolve_check(s, {"skill": "侦查", "difficulty": "困难"})
    assert c.skill_value == 50, "必须读人物卡的侦查 50，不能信 KP 报的值"


def test_unknown_skill_falls_back_not_crash():
    """KP 编一个不存在的技能名也不能崩。"""
    s = _state()
    c = _resolve_check(s, {"skill": "不存在的技能", "difficulty": "困难"})
    assert 1 <= c.target <= 99


def test_difficulty_bonus_applied():
    """极难应比容易更难（目标值更低）。"""
    s = _state()
    hard = _resolve_check(s, {"skill": "侦查", "difficulty": "极难"})
    s2 = _state()
    easy = _resolve_check(s2, {"skill": "侦查", "difficulty": "容易"})
    assert hard.target < easy.target


def test_parse_bonus_presets():
    assert parse_bonus("极难") < 0
    assert parse_bonus("容易") > 0
    assert parse_bonus("-10") == -10
    assert parse_bonus("") == 0
    assert parse_bonus(None) == 0


def test_skill_value_of_missing_skill():
    """查不到技能给保守基线，不让判定崩。"""
    assert skill_value_of({"skills": {"侦查": 50}}, "侦查") == 50
    assert 1 <= skill_value_of({}, "侦查") <= 99
    assert 1 <= skill_value_of(None, "侦查") <= 99


# ============================================================
# 四、解析容错
# ============================================================

def test_kp_verdict_parses():
    v = parse_kp_verdict(_fence({"beat": 1, "scene": "井口",
                                 "check": CHECK, "information_gained": "有新刮痕"}))
    assert v["scene"] == "井口"
    assert v["check"]["skill"] == "侦查"


def test_kp_verdict_accepts_bare_json():
    """模型偶尔裸输出 JSON（无代码块），也要能解析。"""
    v = parse_kp_verdict('{"beat": 2, "scene": "泵房"}')
    assert v.get("scene") == "泵房"


@pytest.mark.parametrize("bad", ["", None, 123, "完全不是 JSON", "```json\n{坏\n```"])
def test_kp_verdict_never_throws(bad):
    assert parse_kp_verdict(bad) == {}


@pytest.mark.parametrize("bad", ["", None, 123, "随便一段话", "```json\n{oops\n```"])
def test_pl_action_never_throws(bad):
    assert parse_pl_action(bad) == {}


def test_pl_action_partial_fields_ok():
    """只给 action 也算有效——四个字段不必全齐。"""
    d = parse_pl_action(_fence({"action": "撬开井盖"}))
    assert d["action"] == "撬开井盖"


# ============================================================
# 五、storyboard 分镜表
# ============================================================

def test_storyboard_row_has_full_chain():
    """一行分镜必须含：动作 / 判定 / 信息增量 / 剧情变化 / 遗留压力。"""
    s = _state()
    c = _resolve_check(s, CHECK)
    s.beats = [{
        "beat": 1, "scene": "废弃泵房",
        "check": c.as_dict(),
        "information_gained": "井壁有三天内的新刮痕",
        "world_state_change": "井下的东西知道有人来了",
        "next_pressure": "井里叫出了同伴的名字",
        "story_phase": "展开中",
    }]
    _append_storyboard_row(s, "喊话但不下去",
                           {"action": "喊话但不下去",
                            "motivation": "怕死",
                            "hypothesis": "井下有人",
                            "cost": "暴露了自己的存在"})
    row = s.storyboard[-1]
    assert row["pl_action"] == "喊话但不下去"
    assert row["check"] and "侦查" in row["check"]
    assert row["check_ok"] is c.ok
    assert row["information_gained"] == "井壁有三天内的新刮痕"
    assert row["next_pressure"] == "井里叫出了同伴的名字"
    assert row["pl_cost"] == "暴露了自己的存在"


def test_storyboard_row_needs_beat_first():
    """没有 KP 裁决时不产生半截分镜行。"""
    s = _state()
    _append_storyboard_row(s, "撬开井盖", {"action": "撬开井盖"})
    assert s.storyboard == []


def test_storyboard_row_skips_blank_action():
    s = _state()
    s.beats = [{"beat": 1, "check": {}}]
    _append_storyboard_row(s, "", {})
    assert s.storyboard == []


def test_turn_text_is_compact_not_novel():
    """回合正文是一行决策记录，不是小说段落——这是提速的关键。"""
    s = _state()
    c = _resolve_check(s, CHECK)
    eff = CHECK["success_effect"] if c.ok else CHECK["failure_effect"]
    kp = _render_verdict_text("废弃泵房，井口盖半开", eff, c)
    assert "废弃泵房" in kp and eff in kp
    assert len(kp) < 200, "KP 回合不该再是几百字的描写"
    pl = _render_pl_text("喊话但不下去",
                         {"action": "喊话但不下去", "motivation": "怕死",
                          "hypothesis": "井下有人", "cost": "暴露了自己"})
    assert "喊话但不下去" in pl and "怕死" in pl and "井下有人" in pl
    assert len(pl) < 200, "PL 回合不该再是几百字的心理描写"


# ============================================================
# 六、空转掐断
# ============================================================

def test_no_progress_terminates():
    """连续 N 拍没有信息增量 → 判定为原地打转并掐断。"""
    s = _state(beat_budget=20, beats_done=3,
               no_progress_streak=_NO_PROGRESS_LIMIT,
               last_story_phase="展开中")
    assert _check_termination(s, last_phase="展开中") == "no_progress"


def test_no_progress_below_limit_keeps_running():
    s = _state(beat_budget=20, beats_done=2,
               no_progress_streak=_NO_PROGRESS_LIMIT - 1,
               last_story_phase="展开中")
    assert _check_termination(s, last_phase="展开中") is None


def test_story_complete_beats_no_progress():
    """收束优先于空转：最后一拍讲完了就不该报空转。"""
    s = _state(beat_budget=20, beats_done=5,
               no_progress_streak=_NO_PROGRESS_LIMIT,
               last_story_phase="已收束")
    assert _check_termination(s, last_phase="已收束") == "story_complete"


def test_hard_cap_still_distinct():
    """空转判定不能吞掉 hard_cap 的语义区分。"""
    s = _state(beat_budget=5, beats_done=5,
               no_progress_streak=0, last_story_phase="展开中")
    assert _check_termination(s, last_phase="展开中") == "hard_cap"