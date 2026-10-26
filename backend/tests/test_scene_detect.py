"""分场景技法模块 + 声音档案生成的回归测试。

覆盖三件事：
1. 四个新模块文件齐备且非空（模块缺失时 skill_materials 会抛错并阻断正文生成）
2. 场景识别：战斗/心理/情感能认出来，纯日常不误判
3. 声音档案草稿：清洗规则（剔除抽象人格词）与「不覆盖作者」
"""
from __future__ import annotations

import pytest

from app.skills import MODULES, available_modules, module_digest, module_rules
from app.skills.scene_detect import (
    BASE_MODULES,
    auto_modules_for,
    detect_scenes,
)
from app.entities.voiceprint_gen import (
    FIELD_SHAPE,
    diff_against_current,
    normalize_draft,
)


# ============================================================
# 一、模块齐备性
# ============================================================

NEW_MODULES = ("scene_livedetail", "scene_combat", "psychology_inner", "scene_romance")


@pytest.mark.parametrize("name", NEW_MODULES)
def test_new_modules_registered(name):
    assert name in MODULES


@pytest.mark.parametrize("name", NEW_MODULES)
def test_new_modules_have_files(name):
    """文件缺失时 module_rules 直接抛错，会阻断正文生成——必须存在。"""
    assert len(module_rules(name).strip()) > 500
    assert len(module_digest(name).strip()) > 200


@pytest.mark.parametrize("name", NEW_MODULES)
def test_new_modules_not_in_default(name):
    """分场景模块不能进 default_for——那会导致每章全量注入。"""
    assert name not in MODULES[name]["default_for"]


def test_available_modules_includes_new():
    names = {m["name"] for m in available_modules()}
    assert set(NEW_MODULES).issubset(names)


# ============================================================
# 二、场景识别
# ============================================================

def test_detect_combat_chapter():
    r = detect_scenes(
        plot_text=(
            "第1场：外桥追逐\n"
            "- 场景：唐迟被三名感染者围攻，他反手制服其中一个\n"
            "- 关键动作：闪避→撞开→刀锋划过他的小臂\n"
        ),
        ch_kind="dungeon",
    )
    assert "combat" in r["scenes"]
    assert "scene_combat" in r["modules"]


def test_detect_psychology_chapter():
    r = detect_scenes(
        brief={"selected": {
            "plot_beats": {
                "qi": "他醒来",
                "cheng": "他意识到自己还在这里",
                "zhuan": "他记起三年前那件事，其实他一直知道",
                "he": "他犹豫了很久，下了决心",
            },
            "summary": "唐迟在旅社反复回想，怀疑自己是不是记错了",
        }},
        ch_kind="dungeon",
    )
    assert "psychology" in r["scenes"]
    assert "psychology_inner" in r["modules"]


def test_detect_romance_chapter():
    r = detect_scenes(
        plot_text=(
            "第1场：天台\n"
            "- 场景：江喻忽然回头，脸红着说喜欢他\n"
            "- 关键动作：拉住他的袖子，凑近\n"
        ),
        ch_kind="dungeon",
    )
    assert "romance" in r["scenes"]
    assert "scene_romance" in r["modules"]


def test_plain_chapter_not_overtriggered():
    """纯日常章不该被塞进战斗/恋爱模块——误判会让模型硬塞内容。"""
    r = detect_scenes(
        plot_text=(
            "第1场：早餐摊\n"
            "- 场景：秦九畴在路边摊吃面，和摊主闲聊天气\n"
            "- 关键动作：放下筷子，掏出钱\n"
        ),
        ch_kind="interlude",
    )
    assert r["scenes"] == []
    assert r["modules"] == list(BASE_MODULES)


def test_base_module_always_present():
    """生活质感是底子，任何一章都要。"""
    r = detect_scenes(plot_text="", ch_kind="dungeon")
    assert "scene_livedetail" in r["modules"]


def test_dungeon_always_gets_horror():
    """副本章是克苏鲁无限流，恐怖底子必带。"""
    r = detect_scenes(plot_text="第1场：旅社\n- 场景：他睡着了", ch_kind="dungeon")
    assert "horror" in r["scenes"]
    assert "coc_horror" in r["modules"]


def test_mainline_not_forced_horror():
    r = detect_scenes(plot_text="第1场：出租屋\n- 场景：他加班到深夜", ch_kind="mainline")
    assert "horror" not in r["scenes"]


def test_repeat_word_does_not_inflate():
    """同一个词出现 20 次也只算 1 命中，否则长章节会被单词刷爆。"""
    brief = {"selected": {"summary": "他打了他打了他打了他打了他打了他打了他打了"}}
    once = detect_scenes(brief=brief, ch_kind="mainline")["scores"]["combat"]
    brief2 = {"selected": {"summary": "他打了一下"}}
    single = detect_scenes(brief=brief2, ch_kind="mainline")["scores"]["combat"]
    assert once == single


def test_combat_negative_context():
    """「打算」「打开」不该被当成战斗。"""
    r = detect_scenes(
        plot_text="第1场：出租屋\n- 场景：他打算打开电脑，查一份资料",
        ch_kind="mainline",
    )
    assert "combat" not in r["scenes"]


def test_auto_modules_respects_explicit_request():
    """作者手动点名时不跑识别——他的判断优先。"""
    got = auto_modules_for(plot_text="第1场：战斗\n- 场景：厮杀", requested=["anti_ai_voice"])
    assert got == ["anti_ai_voice"]


def test_auto_modules_dedup():
    got = auto_modules_for(
        plot_text="第1场：告白\n- 场景：脸红着拉住他",
        ch_kind="dungeon",
    )
    assert len(got) == len(set(got))


# ============================================================
# 三、声音档案草稿
# ============================================================

def test_normalize_drops_abstract_personality():
    """抽象人格词不是可执行动作，留在档案里等于把 AI 味写进去。"""
    d = normalize_draft({"habits": ["性格孤僻", "沉默寡言", "摸口袋找烟"]})
    assert d["habits"] == ["摸口袋找烟"]


def test_normalize_splits_multiline_string():
    """catchphrases 上限 2（口癖贵不在多），多出来的被裁掉。"""
    d = normalize_draft({"catchphrases": "热的。\n不烫。\n行。"})
    assert d["catchphrases"] == ["热的", "不烫"]


def test_normalize_keeps_all_dialect():
    d = normalize_draft({"dialect": "把「知道」说成「晓得的」\n句尾常带「嘞」"})
    assert len(d["dialect"]) == 2


def test_normalize_strips_field_prefix():
    d = normalize_draft({"register": "语气基调：短句多，懒得解释"})
    assert d["register"] == "短句多，懒得解释"


def test_normalize_caps_list_length():
    d = normalize_draft({"habits": [f"动作{i}" for i in range(10)]})
    assert len(d["habits"]) <= 5


def test_normalize_accepts_dict_items():
    d = normalize_draft({"habits": [{"habit": "摸口袋找烟"}, {"text": "报时间"}]})
    assert d["habits"] == ["摸口袋找烟", "报时间"]


def test_normalize_handles_garbage():
    assert normalize_draft(None)["habits"] == []
    assert normalize_draft("不是字典")["register"] == ""
    assert set(normalize_draft({})) == set(FIELD_SHAPE)


def test_normalize_strips_item_numbering():
    """模型爱在列表项前加「1.」，编辑器里每行已分开，编号是冗余噪声。"""
    d = normalize_draft({"habits": ["1. 摸口袋找烟", "2、报时间", "- 弹裤缝"]})
    assert d["habits"] == ["摸口袋找烟", "报时间", "弹裤缝"]


def test_normalize_strips_tail_note():
    """尾部注释是给作者看的说明，注入时只会让模型模仿括号。"""
    d = normalize_draft({"catchphrases": ["哈——（拉长音，对着空处，可以重复一夜）"]})
    assert d["catchphrases"] == ["哈——"]


def test_normalize_keeps_short_paren():
    """短括注是动作本身的补充，不能剥（与 store.render 的规则一致）。"""
    d = normalize_draft({"habits": ["短促地应了一声（催促）"]})
    assert d["habits"] == ["短促地应了一声（催促）"]


def test_diff_marks_changed_fields():
    draft = {"register": "短句多", "psychology": "把感受换算成数字"}
    current = {"register": "长句", "psychology": "把感受换算成数字"}
    d = diff_against_current(draft, current)
    assert "register" in d["changed"]
    assert "psychology" in d["unchanged"]


def test_diff_with_empty_current():
    """空档案时所有有内容的字段都算 changed（前端据此默认勾选）。"""
    draft = {"register": "短句多"}
    d = diff_against_current(draft, {})
    assert "register" in d["changed"]