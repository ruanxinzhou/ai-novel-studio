"""角色声音档案的「抽象化」回归测试。

## 为什么有这一层

声音档案会被**原样注入**写正文的提示词。如果档案里写死了具体动作
（「拇指反复搓食指指腹」）和逐字台词（「我先看下日志」），写手没有别的选择——
它会把那句话原样搬进正文，读者看到的就是「硬要说一下」的机器味。

所以档案必须写**倾向**（方向），让写手按方向自己发明每次不同的具体表现。

三道防线：
1. 渲染层：标签用「倾向/习惯」而不是「动作/口癖」；
2. 注入层：材料头明确说「档案是方向，不是待抄的清单」；
3. 生成层：AI 生成档案时要求写倾向，并检出写得过死的条目提示作者。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.context.ledger import _voiceprint_material  # noqa: E402
from app.entities.store import Voiceprint, _is_over_specified_action  # noqa: E402
from app.entities.voiceprint_gen import _GEN_SYSTEM, normalize_draft  # noqa: E402


# ============================================================
# 一、渲染层：标签语义
# ============================================================

class TestRenderLabels:
    def test_uses_tendency_labels_not_action_labels(self):
        """标签必须读起来像「倾向」，不能像「待抄的动作清单」。"""
        vp = Voiceprint(
            habits=["手部常有下意识的小动作"],
            catchphrases=["爱用一两个字的判断词收尾"],
            dialect=["地球的词偶尔滑出来，顿半拍"],
        )
        line = vp.render("唐迟")
        assert "动作倾向" in line
        assert "口头习惯" in line
        assert "用语习惯" in line
        # 旧标签不能再出现——它们会被模型当成抄写清单
        assert "习惯动作" not in line
        assert "口癖" not in line

    def test_render_keeps_the_actual_tendency_text(self):
        """抽象化的描述本身要完整出现在注入文本里（不是被清洗掉）。"""
        vp = Voiceprint(habits=["手部常有下意识的小动作", "把玩什么东西"])
        line = vp.render("唐迟")
        assert "手部常有下意识的小动作" in line
        assert "把玩什么东西" in line

    def test_empty_voiceprint_renders_empty(self):
        assert Voiceprint().render("唐迟") == ""


# ============================================================
# 二、检出：写得过死的条目
# ============================================================

class TestOverSpecified:
    @pytest.mark.parametrize("text", [
        "拇指反复搓食指指腹，那是长期敲键盘磨出来的",
        "指腹来回搓指缝里的黑痕，搓到指节发红也停不下来",
        "听到解释不了的事，拇指会去摩挲右手虎口的硬茧，等对方说完才收手",
    ])
    def test_flags_concrete_body_actions(self, text):
        assert _is_over_specified_action(text) is True

    @pytest.mark.parametrize("text", [
        "手部常有下意识的小动作",
        "手边总要摆着点什么可把玩的东西",
        "习惯把身边的事按时间或数量记下来",
        "进入新地方先确认出口和退路",
        "总在确认贴身物件是否还在、是否还温热",
    ])
    def test_does_not_flag_abstract_tendencies(self, text):
        """倾向类描述不能被误报——它们正是我们要的写法。"""
        assert _is_over_specified_action(text) is False

    def test_flags_concrete_quoted_tokens(self):
        """带具体台词的条目会被原样搬进正文，也要标出来。"""
        assert _is_over_specified_action(
            "数日子出声——「第X天」「还剩X天」，像给倒计时对表") is True

    def test_over_specified_lists_only_bad_habits(self):
        vp = Voiceprint(habits=[
            "手部常有下意识的小动作",
            "拇指反复搓食指指腹，那是长期敲键盘磨出来的",
        ])
        assert vp.over_specified() == [
            "拇指反复搓食指指腹，那是长期敲键盘磨出来的"]


# ============================================================
# 三、注入层：材料头必须说清「档案是方向」
# ============================================================

class TestInjectionContract:
    def test_material_head_says_archive_is_direction(self, tmp_path):
        """注入材料头必须明确「档案是倾向，不是逐字抄写的清单」。"""
        from app.entities import EntityStore

        path = tmp_path / "entities.json"
        store = EntityStore(str(path))
        store.protagonist.current_body_name = "唐迟"
        store.protagonist.voiceprint = Voiceprint(
            habits=["手部常有下意识的小动作"],
            catchphrases=["爱用一两个字的判断词收尾"],
        )
        mat = _voiceprint_material(store, 1)
        assert mat is not None
        assert "倾向" in mat.content
        assert "自己发明" in mat.content
        # 必须明确禁止把档案字面搬进正文
        assert "绝对禁止" in mat.content

    def test_material_still_marks_missing_characters(self, tmp_path):
        """配齐检查不能因为改措辞而失效。"""
        from app.entities import EntityStore

        path = tmp_path / "entities.json"
        store = EntityStore(str(path))
        store.protagonist.current_body_name = "唐迟"
        store.protagonist.voiceprint = Voiceprint(habits=["只有一条"])
        mat = _voiceprint_material(store, 1)
        assert mat is not None
        assert "尚未配齐" in mat.content


# ============================================================
# 四、生成层：AI 生成时要求写倾向
# ============================================================

class TestGenPrompt:
    def test_prompt_requires_tendency_not_fixed_action(self):
        assert "动作倾向" in _GEN_SYSTEM
        assert "不是「照着演的具体动作」" in _GEN_SYSTEM
        # 旧要求（可执行的具体动作）要彻底换掉
        assert "habits 必须是可执行的物理动作" not in _GEN_SYSTEM

    def test_prompt_gives_five_times_criteria(self):
        """给出可判定的标准：写手能不能为它发明五种不同的具体动作。"""
        assert "五种不同的具体动作" in _GEN_SYSTEM

    def test_prompt_warns_against_literal_copy(self):
        assert "硬要说一下" in _GEN_SYSTEM

    def test_normalize_keeps_abstract_tendency(self):
        """抽象化的倾向不该被清洗规则丢掉。"""
        d = normalize_draft({"habits": [
            "手部常有下意识的小动作",
            "手边总要摆着点什么可把玩的东西",
            "习惯把身边的事按时间或数量记下来",
        ]})
        assert len(d["habits"]) == 3
        assert "手部常有下意识的小动作" in d["habits"]

    def test_normalize_still_drops_abstract_personality(self):
        """抽象人格词仍然要丢——那是另一种病。"""
        d = normalize_draft({"habits": ["性格孤僻", "手部常有下意识的小动作"]})
        assert d["habits"] == ["手部常有下意识的小动作"]
