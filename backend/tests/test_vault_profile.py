"""实体档案结构化填充 + 方向卡起承转合 的回归测试。

背景：
- 卷级落库曾只写「名字 + 一句括注」，107 个 volume_plan 角色全是空档案
  （简介写的是「周朔（电话）」里的括注「电话」）。
- 方向卡的 plot_beats 没进 prompt schema，作者一重抽就把节奏骨架的起承转合冲掉。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.brief_routes import (
    BEAT_KEYS,
    _clean_beat_text,
    normalize_plot_beats,
)
from app.entities.store import EntityStore
from app.entities.vault_plan import (
    _build_profile,
    _is_placeholder_summary,
    seed_from_volume_plan,
)


# ============================================================
# 一、_build_profile：从结构化数据拼档案
# ============================================================

CAST_ZHOU = {
    "name": "周朔",
    "role": "刑侦中队长",
    "occupation": "雾汀县公安局刑侦中队长",
    "age": "38",
    "gender": "男",
    "appearance": "常年熬夜，眼下青黑，右手虎口有旧伤",
    "personality": "话少，认死理，对下属护短",
    "motivation": "三年前那起旧案的真相",
    "secret": "他就是当年办错案子的人",
    "key_skills": {"侦查": 74, "意志": 57, "口才": 41},
}


def test_build_profile_from_cast_full():
    summary, details = _build_profile("周朔", cast_card=CAST_ZHOU)
    assert summary == "周朔，雾汀县公安局刑侦中队长。"
    # 每个字段一行，源数据有的都在
    assert "身份：雾汀县公安局刑侦中队长" in details
    assert "年龄：38" in details
    assert "外貌：常年熬夜，眼下青黑，右手虎口有旧伤" in details
    assert "动机：三年前那起旧案的真相" in details
    assert "秘密：他就是当年办错案子的人" in details
    # 技能 dict 压成一行
    assert "关键技能：侦查74/意志57/口才41" in details


def test_build_profile_skips_missing_fields():
    """源数据没有的字段不出现，绝不占位凑格式。"""
    _summary, details = _build_profile("路人", cast_card={"name": "路人", "role": "保安"})
    lines = [ln for ln in details.splitlines() if ln.strip()]
    assert lines == ["身份：保安"]


def test_build_profile_renamed_cast_uses_correct_schema():
    """sushi_generator 的 schema 是 original/new_name/role，不是 from/to。"""
    renamed = {"original": "陈建国", "new_name": "周朔", "role": "刑侦中队长"}
    summary, details = _build_profile("周朔", renamed=renamed)
    assert "身份：刑侦中队长" in details
    assert "原名：陈建国" in details
    assert "陈建国" not in summary  # 原名只进 details，不污染简介


def test_build_profile_renamed_legacy_from_to():
    """旧数据用 from/to 也要认。"""
    renamed = {"from": "陈建国", "to": "周朔", "role": "队长"}
    _summary, details = _build_profile("周朔", renamed=renamed)
    assert "原名：陈建国" in details
    assert "身份：队长" in details


def test_build_profile_host_extra_fields():
    host = {
        "name": "林薇",
        "identity": "殡仪馆夜班守灵人",
        "body_state": "左肋有缝合伤口，体温偏低",
        "original_story_position": "原剧本第3天死者",
        "seventh_day_death": "第七日夜里被自己缝好的尸体勒死",
        "hidden_death_cause": "尸体在他怀里复生",
        "struggle_hook": "每次照镜子，镜中人比他慢半拍",
        "info_gap_hint": "他不知道自己是第七日的死者",
    }
    summary, details = _build_profile("林薇", host=host)
    assert summary == "林薇，殡仪馆夜班守灵人。"
    assert "身体状态：左肋有缝合伤口，体温偏低" in details
    assert "第七日死状：第七日夜里被自己缝好的尸体勒死" in details
    assert "隐藏死因：尸体在他怀里复生" in details
    assert "挣扎钩子：每次照镜子，镜中人比他慢半拍" in details
    assert "信息差：他不知道自己是第七日的死者" in details


def test_build_profile_falls_back_to_note():
    """什么都没给时，退回 chapter_plan 的括注，不留空。"""
    summary, details = _build_profile("宿管王姨", note="半透明")
    assert summary == "半透明"
    assert "备注：半透明" in details


def test_is_placeholder_summary():
    assert _is_placeholder_summary("")
    assert _is_placeholder_summary("电话")      # 括注碎片
    assert _is_placeholder_summary("宿管")      # 通名碎片
    assert not _is_placeholder_summary("雾汀县公安局刑侦中队长，三年前旧案的经办人")
    assert not _is_placeholder_summary("周朔，刑侦队长。")


# ============================================================
# 二、落库：顺序 + 幂等 + 不覆盖作者
# ============================================================

def _vol():
    return SimpleNamespace(
        rhythm_blueprint={
            "chapter_plan": [
                {
                    "chapter": 1,
                    "day": 1,
                    "characters_on_stage": ["周朔（电话）", "林薇"],
                    "location": "县局审讯室",
                    "foreshadow_seeds": ["伏笔点1：出现一枚刻着学号的铜扣，暂不解释"],
                    "foreshadow_payback": [],
                },
                {
                    "chapter": 2,
                    "day": 2,
                    "characters_on_stage": ["周朔", "林薇"],
                    "location": "殡仪馆",
                    "foreshadow_seeds": [],
                    "foreshadow_payback": [],
                },
            ],
        },
        start_chapter_no=1,
        adapted_module={
            "cast": [CAST_ZHOU],
            "player_cards": [{"name": "苏雅", "desc": "法医。话极少，手比嘴诚实。"}],
            "renamed_cast": [{"original": "陈建国", "new_name": "周朔", "role": "刑侦中队长"}],
        },
        host_character={
            "name": "林薇",
            "identity": "殡仪馆夜班守灵人",
            "seventh_day_death": "第七日夜里被自己缝好的尸体勒死",
        },
        death_nodes=[],
        climax_nodes=[],
        clue_nodes=[],
    )


@pytest.fixture
def store(tmp_path):
    return EntityStore(tmp_path / "entities.json")


def test_seed_fills_profile_before_chapter_scan(store):
    """关键回归：chapter_plan 的「周朔（电话）」不能把简介写成「电话」。"""
    seed_from_volume_plan(store, _vol())
    e = store.find("character", "周朔")
    assert e is not None
    # 完整档案来自 cast，不是括注
    assert e.summary == "周朔，雾汀县公安局刑侦中队长。"
    assert "年龄：38" in e.details
    assert "关键技能：侦查74/意志57/口才41" in e.details
    assert "电话" not in e.summary
    # 章号由 chapter_plan 补齐（两章都出场）
    assert e.appearances == [1, 2]


def test_seed_host_and_player_cards(store):
    seed_from_volume_plan(store, _vol())
    host = store.find("character", "林薇")
    assert host.summary == "林薇，殡仪馆夜班守灵人。"
    assert "第七日死状" in host.details
    assert host.appearances == [1, 2]

    player = store.find("character", "苏雅")
    assert player is not None
    assert "法医" in player.summary


def test_seed_replaces_placeholder_summary(store):
    """库里已有碎片简介时，本轮补成完整档案。"""
    store.add("character", "周朔", summary="电话", chapter=1)
    seed_from_volume_plan(store, _vol())
    e = store.find("character", "周朔")
    assert e.summary == "周朔，雾汀县公安局刑侦中队长。"


def test_seed_never_overwrites_author_content(store):
    """作者写过的简介与档案一律不动，只补章号。"""
    store.add("character", "周朔", summary="我自己写的简介，很长很长", details="我自己写的档案")
    seed_from_volume_plan(store, _vol())
    e = store.find("character", "周朔")
    assert e.summary == "我自己写的简介，很长很长"
    assert e.details == "我自己写的档案"
    assert e.appearances == [1, 2]


def test_seed_is_idempotent(store):
    """重复落库不产生重复实体。"""
    seed_from_volume_plan(store, _vol())
    before = len(store.list(type="character"))
    res = seed_from_volume_plan(store, _vol())
    after = len(store.list(type="character"))
    assert before == after
    assert res.created_count == 0
    # 章号不重复累加
    assert store.find("character", "周朔").appearances == [1, 2]


def test_seed_plants_foreshadow(store):
    """伏笔种子是整句，清洗后取首分句（去掉「暂不解释」这类操作备注）。"""
    seed_from_volume_plan(store, _vol())
    e = store.find("foreshadow", "出现一枚刻着学号的铜扣")
    assert e is not None
    assert e.state_attrs.get("plant_chapter") == "1"


# ============================================================
# 三、起承转合：清洗与兜底
# ============================================================

def test_clean_beat_text_strips_prefix():
    assert _clean_beat_text("起：唐迟在审讯室醒来") == "唐迟在审讯室醒来"
    assert _clean_beat_text("**转** 规则显形") == "规则显形"
    assert _clean_beat_text("（合）倒计时归零") == "倒计时归零"
    assert _clean_beat_text("正常内容") == "正常内容"
    assert _clean_beat_text(None) == ""


def test_normalize_plot_beats_partial_fill():
    """只填了两段 → 缺的用 fallback 补齐。"""
    got = normalize_plot_beats(
        {"qi": "开场", "he": "收尾"},
        {"qi": "骨架起", "cheng": "骨架承", "zhuan": "骨架转", "he": "骨架合"},
    )
    assert got == {"qi": "开场", "cheng": "骨架承", "zhuan": "骨架转", "he": "收尾"}


def test_normalize_plot_beats_drops_empty():
    assert normalize_plot_beats({"qi": "  ", "zhuan": "反转"}) == {"zhuan": "反转"}
    assert normalize_plot_beats(None) == {}
    assert normalize_plot_beats("不是字典") == {}


def test_normalize_plot_beats_keys_complete():
    """四段齐全时键集合固定，前端按 BEAT_KEYS 渲染不会漏。"""
    got = normalize_plot_beats(
        {"qi": "a", "cheng": "b", "zhuan": "c", "he": "d"},
    )
    assert set(got) == {k for k, _ in BEAT_KEYS}