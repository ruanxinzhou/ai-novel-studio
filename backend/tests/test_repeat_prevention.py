"""重复描写的**源头预防**回归测试。

## 问题背景

第 3 章生成后账本报：
- 「手部小动作 已用 17 次」
- 「沉重 已用 12 次」

作者看到的是「重复描写」，但根因不在检测，在于**规则自相矛盾**：

1. 正文铁律强制「两句对白之间插入 ≤7 字身体小动作」
2. 角色档案只给了「手部有小动作」**一个方向**
3. 写手所有小动作只能往手部挤 → 一章全是「他手指…他拇指…他掌心…」

所以修复分两层：
- **预防层**：档案必须覆盖多个身体通道；铁律从「强制插入」改为「节奏调剂 + 通道轮换」
- **检测层**：语义场成员词去歧义、同段命中不算复读、只报真复读
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.entities.store import Voiceprint  # noqa: E402
from app.entities.voiceprint_gen import _GEN_SYSTEM  # noqa: E402
from app.generation.guard import _find_fragments  # noqa: E402
from app.generation.lexicon import (  # noqa: E402
    SEMANTIC_FIELDS,
    LexiconStore,
)
from app.generation.quality import (  # noqa: E402
    SoftReviewResult,
    parse_soft_review,
    rewrite_prompt_with_hints,
)


# ============================================================
# 一、预防层：动作倾向必须覆盖多个通道
# ============================================================

class TestChannelBalance:
    def test_all_hand_habits_is_narrow(self):
        """全挤在手上 = 复读配方，必须被检出。"""
        vp = Voiceprint(habits=[
            "手部常有下意识的小动作",
            "手边总要摆着点什么可把玩的东西",
        ])
        bal = vp.channel_balance()
        assert bal["narrow"] is True
        assert bal["dominant"] == "手部"
        assert bal["n_channels"] == 1

    def test_multi_channel_habits_is_not_narrow(self):
        """分布在多个通道 = 写手有得选，不会复读。"""
        vp = Voiceprint(habits=[
            "手部常有下意识的小动作",
            "视线会先扫一遍环境：出口、人数、能退到哪",
            "呼吸节奏会变：紧张时压着气说话",
            "姿势总带一点随时能动的紧绷：重心偏在脚后跟",
            "说话时声线压得比平时低半度",
            "走动时脚步比看上去轻",
        ])
        bal = vp.channel_balance()
        assert bal["narrow"] is False
        assert bal["n_channels"] >= 4

    def test_detects_each_channel(self):
        vp = Voiceprint(habits=[
            "手部常有下意识的小动作",
            "视线会先扫一遍环境",
            "呼吸节奏会变",
            "姿势带一点紧绷",
            "说话时声线压低半度",
            "走动时脚步很轻",
            "跟人说话总隔着半步",
        ])
        chans = vp.channel_balance()["channels"]
        for expected in ("手部", "视线", "呼吸", "姿势", "声线", "脚步", "距离感"):
            assert expected in chans, f"{expected} 通道没被识别"

    def test_single_habit_is_not_narrow(self):
        """只有一条时不构成「窄通道」——那是档案没配齐，不是通道问题。"""
        vp = Voiceprint(habits=["手部常有下意识的小动作"])
        assert vp.channel_balance()["narrow"] is False


class TestChannelRulesInPrompts:
    def test_gen_prompt_requires_multiple_channels(self):
        """生成端必须要求多通道，并明确「全在手上 = 复读配方」。"""
        assert "多个身体通道" in _GEN_SYSTEM
        assert "复读配方" in _GEN_SYSTEM
        assert "分布在不同通道" in _GEN_SYSTEM

    def test_gen_prompt_lists_channel_examples(self):
        for ch in ("手部", "视线", "呼吸", "姿势", "声线", "脚步"):
            assert ch in _GEN_SYSTEM


# ============================================================
# 二、预防层：注入材料要求通道轮换
# ============================================================

class TestInjectionChannelRules:
    def test_material_head_requires_channel_rotation(self, tmp_path):
        from app.entities import EntityStore
        from app.context.ledger import _voiceprint_material

        path = tmp_path / "entities.json"
        store = EntityStore(str(path))
        store.protagonist.current_body_name = "唐迟"
        store.protagonist.voiceprint = Voiceprint(
            habits=["手部常有下意识的小动作", "视线会先扫一遍环境"],
            catchphrases=["爱用一两个字的判断词收尾"],
        )
        mat = _voiceprint_material(store, 1)
        assert mat is not None
        assert "通道分散" in mat.content
        assert "轮换使用不同通道" in mat.content
        # 必须给出明确的次数上限
        assert "2-3 次" in mat.content


class TestActionAnchorIsNotMandatory:
    def test_humanize_demotes_mandatory_action_insertion(self):
        """铁律 6 必须从「强制插入」改成「节奏调剂」。"""
        from app.generation.humanize import system_prompt_addendum

        text = system_prompt_addendum("draft")
        assert "节奏调剂" in text
        assert "不是「每两句必须有一个」" in text
        assert "连续两次不要用同一通道" in text or "连续两次不重复" in text

    def test_draft_prompt_has_channel_rotation(self):
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS

        d = DEFAULT_INSTRUCTIONS["draft"]
        assert "同一通道" in d
        assert "同一个道具动作最多 1-2 次" in d

    def test_polish_prompt_requires_switching_channel(self):
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS

        assert "补的时候要换通道" in DEFAULT_INSTRUCTIONS["polish"]


# ============================================================
# 三、检测层：语义场成员词去歧义
# ============================================================

class TestSemanticFieldDisambiguation:
    @pytest.mark.parametrize("word", ["压", "热", "温", "湿", "潮", "灯", "冷", "凉"])
    def test_high_ambiguity_single_chars_removed(self, word):
        """单字高歧义词必须移出成员表。

        「压低声音」「血压高」不是沉重感；「温度」「温和」不是温热感；
        「灯」是普通名词。这些混进去会把正常中文全记成"感受复读"。
        """
        all_members = [m for members in SEMANTIC_FIELDS.values() for m in members]
        assert word not in all_members, f"「{word}」仍在语义场成员表里"

    def test_heavy_field_has_no_press(self):
        """「沉重」里不能有「压」——它是普通动词。"""
        assert "压" not in SEMANTIC_FIELDS["沉重"]

    def test_concrete_two_char_words_kept(self):
        """成词的写法要留下——它们才是真正的感受描写。"""
        for w in ("沉甸甸", "滚烫", "湿漉漉", "潮湿"):
            all_members = [m for members in SEMANTIC_FIELDS.values() for m in members]
            assert w in all_members


# ============================================================
# 四、检测层：同段命中不算复读
# ============================================================

class TestSameParagraphNotRepeat:
    def test_same_paragraph_multiple_hits_count_once(self, tmp_path):
        """一句描写里多个手部词 = 一次描写，不是多次复读。"""
        store = LexiconStore(tmp_path / "lex.json")
        text = "他低头看自己的手背。\n手指蜷起来，指缝里全是黑泥，掌心也是。"
        store.record_chapter(text, 1)
        hits = store.data.get("手部小动作", {}).get(1)
        assert hits is not None
        assert len(hits.hits) >= 4          # 命中次数是 4
        # 但按段落归组后只有 2 段 → 最多 1 次窗内复读
        assert len(store.window_violations(1)) <= 1

    def test_different_paragraphs_still_detected(self, tmp_path):
        """跨段反复**驻留**仍要检出（2026-10-26：只有驻留位算复读）。"""
        store = LexiconStore(tmp_path / "lex.json")
        text = ("他手指敲了下桌面。\n"
                "过了很久，他又看了一眼窗外。\n"
                "他手指又搭上了门把手。\n"
                "雨还在下，他的指节又敲了敲铁门。")
        store.record_chapter(text, 1)
        viol = store.window_violations(1)
        assert len(viol) >= 1

    def test_advance_slot_not_counted_as_repeat(self, tmp_path):
        """推进位不计入复读。

        人工定稿里手部动作可以连做八次（掏钥匙→接糖→抠硬币），
        每次都让状态往前走了一步——那是推进，不是复读。
        """
        store = LexiconStore(tmp_path / "lex.json")
        text = ("他手指敲了下桌面。\n"
                "他的指腹按在门把手上。\n"
                "他的手掌贴上了冰冷的铁。\n"
                "他的指尖在裤缝上蹭了蹭。")
        store.record_chapter(text, 1)
        assert store.window_violations(1) == []

    def test_top_n_filters_noise(self, tmp_path):
        """只报真复读：孤立的 1 次不算。"""
        store = LexiconStore(tmp_path / "lex.json")
        text = ("他手指敲了下桌面。\n"
                "过了很久，他又看了一眼窗外。\n"
                "他的指腹按在门把手上。\n"
                "雨还在下。\n"
                "他掌心贴上了冰冷的铁。\n"
                "远处传来一声闷响。\n"
                "他的指节在裤缝上蹭了蹭。\n"
                "没有人说话。")
        store.record_chapter(text, 1)
        tops = store.top_n_for_chapter(1)
        # 单次调用不该进 top（要看 MIN_REPEAT_CALLS）
        for t in tops:
            assert t["violations"] >= store.MIN_REPEAT_CALLS or (
                t["violations"] >= 2 and t["total_across_book"] >= store.MIN_REPEAT_TOTAL)


# ============================================================
# 五、P1 参与重写
# ============================================================

class TestP1TriggersRewrite:
    def test_soft_review_p1_included_in_rewrite_prompt(self):
        """P1 必须传给重写模型——旧行为丢掉了，导致「报了 8 条却一条没改」。"""
        review = SoftReviewResult(total=70, p0=[], p1=["句长偏匀", "对白标签单调"])
        out = rewrite_prompt_with_hints("原文指令", review)
        assert "句长偏匀" in out
        assert "对白标签单调" in out

    def test_rewrite_prompt_without_any_issue_is_unchanged(self):
        review = SoftReviewResult(total=90)
        assert rewrite_prompt_with_hints("原文指令", review) == "原文指令"


class TestSoftReviewScoreParsing:
    def test_chinese_score_keys_are_accepted(self):
        """模型用中文键名返回时，不能全记 0 分。"""
        raw = ('{"scores": {"伏笔": 7, "节奏": 6, "对话": 8}, "total": 70, '
               '"p0_fatal": [], "p1_warnings": []}')
        res = parse_soft_review(raw)
        assert res.scores["伏笔"] == 7
        assert res.scores["节奏"] == 6
        assert res.total == 70

    def test_missing_total_is_recomputed(self):
        """没给 total 但有分数时，不该显示 0 分。"""
        raw = ('{"scores": {"foreshadowing": 8, "pacing": 7, "dialogue": 8}, '
               '"p0_fatal": [], "p1_warnings": []}')
        res = parse_soft_review(raw)
        assert res.total > 0


# ============================================================
# 六、去 AI 味护栏：单字成句误判
# ============================================================

class TestGuardShortSentence:
    @pytest.mark.parametrize("line", ["谁？", "嗯。", "不。", "对。", "走。", "钱。"])
    def test_legal_short_sentences_not_flagged(self, line):
        """合法的单字成段是活人痕迹，不能被当成崩坏回滚。"""
        assert _find_fragments(line) == []

    @pytest.mark.parametrize("line", ["他。", "了。", "他他他。"])
    def test_broken_shells_still_flagged(self, line):
        """真正崩坏的单字壳仍要抓。"""
        assert _find_fragments(line) != []

    def test_dangling_verb_with_period_flagged(self):
        """「他感到。」——动词被删光后剩下的壳。"""
        assert _find_fragments("他感到。") != []

    def test_normal_psych_verb_sentence_not_flagged(self):
        """「他感到恐惧」是正常句，不能误伤。"""
        assert _find_fragments("他感到恐惧涌上心头。") == []
