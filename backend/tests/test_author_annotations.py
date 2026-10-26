"""作者批注（【】标注）的作用域标定与注入回归测试。

## 核心规则

「新增」和「修改」指向的东西不同：

- **修改类**（改成/这一段有问题/删掉）→ 指向**某一段**（对象）
- **新增类**（这里要增加/补一段）→ 指向**某个位置**（插入点）

所以：
- 行内批注 → 改本段
- 独占行·修改 → 作用于**上一段**（作者先看到内容再写意见）
- 独占行·新增 → 作用在**该位置**（插入点）
- 显式【第N段】→ 第 N 段
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.annotations import (  # noqa: E402
    Annotation,
    render_annotation_block,
    scan_annotations,
    strip_leftover_markers,
    unmet_annotations,
)


# ============================================================
# 五、润色层集成（走真实 run_chapter_stage）
# ============================================================

class TestPolishIntegration:
    """批注必须在润色这一步被识别、注入、并参与后置校验。"""

    def _env(self, tmp_path):
        from types import SimpleNamespace
        from app.entities import EntityStore
        from app.pipeline.prompts import PromptStore
        from app.projects import ProjectStore
        from app.review import MarkerStore
        from app.wizard import DeskStore, WizardStore

        project = ProjectStore(tmp_path / "projects")
        pid = project.create("批注书", "").id
        base = tmp_path / pid
        return SimpleNamespace(
            tmp=tmp_path, pid=pid, project=project,
            wizard=WizardStore(base / "wizard.json"),
            desk=DeskStore(base / "desk.json"),
            markers=MarkerStore(base / "markers.json"),
            entities=EntityStore(base / "entities.json"),
            prompts=PromptStore(),
        )

    def test_polish_injects_annotation_block_and_strips_markers(self, tmp_path):
        """正文里的【】批注：进模型前剥离成锚点，注入批注清单，成稿里锚点被清掉。"""
        from app.providers.base import LLMResponse, Usage
        from app.wizard import run_chapter_stage

        env = self._env(tmp_path)
        env.project.adopt(env.pid, "outline", [], "第1章\n开篇。")
        env.desk.record(1, "plot", "剧情。")
        env.desk.record(1, "draft", "他推开门。\n\n【改成他踹开门】\n\n屋里没人。")

        class CapProvider:
            default_model = None

            def __init__(self):
                self.messages: list = []

            def chat(self, messages, model=None, max_tokens=None, extra=None):
                self.messages.extend(messages)
                # 模型忘了删锚点（后处理要负责清）
                return LLMResponse(
                    text="他一脚踹开门。⟦A1⟧\n\n屋里没人。",
                    usage=Usage(input_tokens=1, output_tokens=1))

        provider = CapProvider()
        r = run_chapter_stage(provider, env.prompts, env.project, env.pid, env.markers,
                              env.wizard, env.desk, 1, "polish", journal_dir=None)

        prompt = "\n".join(m["content"] for m in provider.messages)
        # 批注清单进了 prompt
        assert "作者批注" in prompt
        assert "A1" in prompt
        # 正文里【】被剥离成锚点（【】不能原样进模型）
        assert "【改成他踹开门】" not in prompt
        assert "⟦A1⟧" in prompt

        # 报告：识别到 1 条、锚点残留被清理
        rep = r["annotations"]
        assert rep["count"] == 1
        assert rep["leftover_markers"] == 1
        # 成稿里没有残留
        assert "⟦" not in r["y"]
        assert "【" not in r["y"]

    def test_unmet_annotation_reported(self, tmp_path):
        """模型没落实批注 → 报告里要能看出来。"""
        from app.providers.base import LLMResponse, Usage
        from app.wizard import run_chapter_stage

        env = self._env(tmp_path)
        env.project.adopt(env.pid, "outline", [], "第1章\n开篇。")
        env.desk.record(1, "plot", "剧情。")
        env.desk.record(1, "draft", "他推开门。\n\n【改成他踹开门】")

        class CapProvider:
            default_model = None

            def __init__(self):
                self.messages: list = []

            def chat(self, messages, model=None, max_tokens=None, extra=None):
                self.messages.extend(messages)
                return LLMResponse(
                    text="他推开门。屋里没人。",
                    usage=Usage(input_tokens=1, output_tokens=1))

        r = run_chapter_stage(CapProvider(), env.prompts, env.project, env.pid, env.markers,
                              env.wizard, env.desk, 1, "polish", journal_dir=None)
        rep = r["annotations"]
        assert rep["count"] == 1
        assert len(rep["unmet"]) == 1

    def test_no_annotations_leaves_text_untouched(self, tmp_path):
        """没有批注时不注入批注块，正文原样进模型。"""
        from app.providers.base import LLMResponse, Usage
        from app.wizard import run_chapter_stage

        env = self._env(tmp_path)
        env.project.adopt(env.pid, "outline", [], "第1章\n开篇。")
        env.desk.record(1, "plot", "剧情。")
        env.desk.record(1, "draft", "他推开门。屋里没人。")

        class CapProvider:
            default_model = None

            def __init__(self):
                self.messages: list = []

            def chat(self, messages, model=None, max_tokens=None, extra=None):
                self.messages.extend(messages)
                return LLMResponse(text="他推开门。屋里没人。",
                                  usage=Usage(input_tokens=1, output_tokens=1))

        provider = CapProvider()
        r = run_chapter_stage(provider, env.prompts, env.project, env.pid, env.markers,
                              env.wizard, env.desk, 1, "polish", journal_dir=None)
        assert r["annotations"]["count"] == 0
        prompt = "\n".join(m["content"] for m in provider.messages)
        # 没有批注 → 不注入批注清单材料（提示语本身在 polish 契约里，不算）
        assert "【作者批注·最高优先级·必须逐条落实】" not in prompt
        # 正文原样进模型
        assert "他推开门。屋里没人。" in prompt


# ============================================================
# 一、作用域标定
# ============================================================

class TestScopeDetection:
    def test_inline_annotation_targets_same_paragraph(self):
        """行内批注：作用于本段。"""
        scan = scan_annotations("他推开门。【改成踹开门】\n\n屋里没人。")
        assert scan.count == 1
        a = scan.annotations[0]
        assert a.intent == "replace"
        assert a.target_para == 0
        assert a.inline is True

    def test_standalone_replace_targets_previous_paragraph(self):
        """独占行·修改：作用于上一段。"""
        scan = scan_annotations("他推开门。\n\n【这一段有问题，改成他踹开门】\n\n屋里没人。")
        assert scan.count == 1
        a = scan.annotations[0]
        assert a.intent == "replace"
        assert a.target_para == 0     # 第 1 段（0-based）
        assert a.inline is False

    def test_standalone_insert_is_positional(self):
        """独占行·新增：是插入点，没有目标段。"""
        scan = scan_annotations("他推开门。\n\n【这里要增加他检查弹匣的动作】\n\n屋里没人。")
        assert scan.count == 1
        a = scan.annotations[0]
        assert a.intent == "insert"
        assert a.target_para == -1    # 位置型

    def test_standalone_delete_targets_previous_paragraph(self):
        scan = scan_annotations("他推开门。\n\n【删掉这一段】\n\n屋里没人。")
        a = scan.annotations[0]
        assert a.intent == "delete"
        assert a.target_para == 0

    def test_explicit_paragraph_anchor(self):
        """显式【第N段】→ 精确指向那一段。"""
        scan = scan_annotations(
            "第一段。\n\n第二段。\n\n第三段。\n\n【第2段：改成这里要有一场雨】\n\n第四段。")
        a = scan.annotations[0]
        assert a.target_para == 1     # 第 2 段
        # 锚定前缀应被剥掉，不留在批注正文里
        assert "第2段" not in a.text
        assert "改成" in a.text

    def test_explicit_prefix_overrides_keywords(self):
        """显式前缀优先于关键词推断。"""
        scan = scan_annotations("他推开门。\n\n【新增：这里要有一场雨】\n\n屋里没人。")
        a = scan.annotations[0]
        assert a.intent == "insert"
        assert "一场雨" in a.text

    def test_multiple_annotations_get_distinct_markers(self):
        scan = scan_annotations(
            "甲。\n\n【这里要增加一段】\n\n乙。\n\n【这段改成别的】\n\n丙。")
        assert scan.count == 2
        markers = {a.marker for a in scan.annotations}
        assert len(markers) == 2
        assert scan.annotations[0].intent == "insert"
        assert scan.annotations[1].intent == "replace"
        assert scan.annotations[1].target_para == 1   # 「乙」那段

    def test_weak_position_word_does_not_beat_insert(self):
        """「这里要有一场雨」含弱词「这里」，但语义是新增——不能判成修改。"""
        scan = scan_annotations("甲。\n\n【这里要有一场雨】")
        assert scan.annotations[0].intent == "insert"

    def test_explicit_strong_replace_wins_over_insert_word(self):
        """「改成…」是强修改意图，不能被「加」抢走。"""
        scan = scan_annotations("甲。\n\n【改成这里要加一场雨】")
        assert scan.annotations[0].intent == "replace"

    def test_out_of_range_paragraph_anchor_still_lands(self):
        """段号超出范围时锚点必须落地——丢了作者会以为「我明明写了」。"""
        scan = scan_annotations("第一段。\n\n第二段。\n\n【第9段：改成这里要有一场雨】")
        a = scan.annotations[0]
        assert a.marker in scan.clean_text
        assert "⟦" in scan.clean_text
        assert a.anchor_note

    def test_out_of_range_insert_lands_at_end(self):
        scan = scan_annotations("第一段。\n\n第二段。\n\n【第9段：加上雨声】")
        assert scan.count == 1
        assert "⟦A1⟧" in scan.clean_text


# ============================================================
# 二、正文剥离与锚点
# ============================================================

class TestStripAndMarkers:
    def test_no_bracket_left_in_clean_text(self):
        """剥离后正文里不能还有【】——那是会被当成叙述内容的。"""
        scan = scan_annotations(
            "他推开门。【改成踹开门】\n\n【这里要增加X】\n\n屋里没人。")
        assert "【" not in scan.clean_text
        assert "】" not in scan.clean_text

    def test_markers_present_for_each_annotation(self):
        scan = scan_annotations("甲。\n\n【这里要增加一段】\n\n乙。")
        assert "⟦A1⟧" in scan.clean_text
        assert scan.clean_text.count("⟦") == scan.count

    def test_insert_marker_on_its_own_line(self):
        """新增类的锚点单独成行——它标的是「插入点」。"""
        scan = scan_annotations("甲。\n\n【这里要增加一段】\n\n乙。")
        lines = [ln.strip() for ln in scan.clean_text.split("\n")]
        assert "⟦A1⟧" in lines

    def test_replace_marker_at_paragraph_end(self):
        """修改类的锚点放在目标段末尾——模型一眼看到要改哪段。"""
        scan = scan_annotations("他推开门。\n\n【改成踹开门】\n\n屋里没人。")
        first = scan.clean_text.split("\n")[0]
        assert first.endswith("⟦A1⟧")

    def test_delete_marker_at_paragraph_head(self):
        """删除类的锚点放在段首——模型知道这一整段要拿掉。"""
        scan = scan_annotations("他推开门。\n\n【删掉这一段】\n\n屋里没人。")
        first = scan.clean_text.split("\n")[0]
        assert first.startswith("⟦A1⟧")

    def test_plain_text_unchanged(self):
        """没有批注时正文原样返回。"""
        text = "他推开门。\n\n屋里没人。"
        scan = scan_annotations(text)
        assert scan.count == 0
        assert scan.clean_text == text

    def test_non_annotation_brackets_kept(self):
        """正文里本来就有的方括号不能被当批注吃掉。"""
        text = "参考[1]的记录。"
        scan = scan_annotations(text)
        assert scan.count == 0
        assert "[1]" in scan.clean_text

    def test_bracket_reference_with_hint_kept(self):
        """半角方括号 + 指令词 → 认作批注。"""
        scan = scan_annotations("他推开门。[改成踹开门]")
        assert scan.count == 1


# ============================================================
# 三、注入块
# ============================================================

class TestInjectionBlock:
    def test_block_lists_markers_and_scopes(self):
        scan = scan_annotations(
            "甲。\n\n【这里要增加一段】\n\n乙。\n\n【这段改成别的】")
        block = render_annotation_block(scan.annotations)
        assert "A1" in block
        assert "A2" in block
        assert "新增" in block
        assert "修改" in block
        # 必须说清「锚点要删掉」
        assert "删掉" in block

    def test_empty_annotations_gives_empty_block(self):
        assert render_annotation_block([]) == ""

    def test_polish_prompt_mentions_annotations(self):
        """润色层的 prompt 必须知道批注的存在和优先级。"""
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS
        p = DEFAULT_INSTRUCTIONS["polish"]
        assert "作者批注" in p
        assert "最高优先级" in p
        assert "⟦" in p or "锚点" in p


# ============================================================
# 四、后置校验
# ============================================================

class TestPostCheck:
    def test_leftover_markers_stripped(self):
        cleaned, n = strip_leftover_markers("他推开门。⟦A1⟧\n屋里没人。")
        assert "⟦" not in cleaned
        assert n == 1

    def test_leftover_brackets_stripped(self):
        """模型把批注原样抄回来时也要清掉。"""
        cleaned, _n = strip_leftover_markers("他推开门。【改成踹开门】")
        assert "【" not in cleaned

    def test_unmet_detected_by_keyword(self):
        anns = scan_annotations("他推开门。\n\n【改成踹开门】").annotations
        unmet = unmet_annotations(anns, "他推开门。屋里没人。")
        assert len(unmet) == 1

    def test_applied_change_not_flagged(self):
        """模型用自己的话改写了（踹→踢），关键词命中就不该报。"""
        anns = scan_annotations("他推开门。\n\n【改成踹开门】").annotations
        unmet = unmet_annotations(anns, "他一脚踹开门。屋里没人。")
        assert unmet == []

    def test_no_annotations_no_unmet(self):
        assert unmet_annotations([], "任意文本") == []

    def test_source_text_captured(self):
        """每条批注要记住作用域对应的原文。"""
        scan = scan_annotations("他推开门。\n\n【改成踹开门】")
        a = scan.annotations[0]
        assert a.source_text == "他推开门。"

    def test_word_already_in_source_does_not_count_as_applied(self):
        """原文里本来就有的词不能当成「已落实」的证据。

        批注「改成踹开门」，原文「他推开门」——
        成稿还是「他推开门」，只有「开门」重合，
        「踹」是原文没有的且没出现 → 必须判未落实。
        """
        anns = scan_annotations("他推开门。\n\n【改成踹开门】").annotations
        unmet = unmet_annotations(anns, "他推开门。屋里没人。")
        assert len(unmet) == 1

    def test_delete_flagged_when_target_still_present(self):
        """删除类：目标段还在 → 未落实。"""
        anns = scan_annotations("他推开门。\n\n【删掉这一段】").annotations
        unmet = unmet_annotations(anns, "他推开门。屋里没人。")
        assert len(unmet) == 1

    def test_delete_ok_when_target_removed(self):
        """删除类：目标段真没了 → 已落实。"""
        anns = scan_annotations("他推开门。\n\n【删掉这一段】").annotations
        unmet = unmet_annotations(anns, "屋里没人。")
        assert unmet == []

    def test_insert_checked_against_full_text(self):
        """新增类：内容没写进去 → 未落实。"""
        anns = scan_annotations("甲。\n\n【这里要增加他检查弹匣】").annotations
        unmet = unmet_annotations(anns, "甲。屋里没人。")
        assert len(unmet) == 1

    def test_insert_applied_detected(self):
        anns = scan_annotations("甲。\n\n【这里要增加他检查弹匣】").annotations
        unmet = unmet_annotations(anns, "甲。他蹲下检查弹匣。屋里没人。")
        assert unmet == []
