"""定稿反推的落地项：气息长度 / 档案密度 / 推进位驻留位。

这些指标全部来自 2026-10-26 的实测对照：
人工定稿 vs 机器产出（novel-studio 项目 20261014-c6c432 前三章）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.archive import archive_density, archive_hint  # noqa: E402
from app.generation.humanize import _rhythm_metrics  # noqa: E402
from app.generation.lexicon import classify_hits  # noqa: E402


# ============================================================
# 一、气息长度（段内逗号数）
# ============================================================

class TestBreathLength:
    """人工定稿的段是「一口气」——段内逗号 1.5-2.1 个/段，机器稿 1.08。"""

    def test_breath_metric_computed(self):
        m = _rhythm_metrics("他低头瞄了眼手机，凌晨零点四十七，再熬十三分钟就得关机。\n\n屋里没人。")
        assert m["段内逗号均数"] > 0
        assert "多逗号段占比" in m

    def test_machine_style_has_low_breath(self):
        """摄影机切法：一句一段 → 段内逗号接近 0。"""
        text = "\n\n".join([
            "凌晨零点四十七分。",
            "手机屏幕在掌心亮着。",
            "电量还剩百分之九。",
            "再过十三分钟，它就会关机。",
            "楼门老旧。",
            "黄铜锁芯被潮气浸得发涩。",
        ])
        m = _rhythm_metrics(text)
        assert m["段内逗号均数"] < 1.0
        assert m["多逗号段占比"] == 0.0

    def test_human_style_has_high_breath(self):
        """人在说话：段内连着走几个动作，用逗号接住。"""
        text = "\n\n".join([
            "他低头瞄了眼手心里亮着的手机屏幕，凌晨零点四十七，再熬十三分钟这破机器就得自动关机。",
            "单元门那把老掉牙的黄铜锁也不知道撑了多少年，一到后半夜受潮就发涩，钥匙插进去拧到一半死活转不动了。",
        ])
        m = _rhythm_metrics(text)
        assert m["段内逗号均数"] >= 2.0
        assert m["多逗号段占比"] == 100.0

    def test_empty_text_safe(self):
        m = _rhythm_metrics("")
        assert m["段内逗号均数"] == 0.0
        assert m["段落数"] == 0


# ============================================================
# 二、档案密度
# ============================================================

class TestArchiveDensity:
    """数字要挂在人/时/物上——定稿给现场，机器稿给摘要。"""

    def test_age_anchor_counted(self):
        d = archive_density("邵广勤是闸房维修工，四十七岁，住在二楼最里面那间。")
        assert d["by_kind"]["年龄"] >= 1
        assert d["by_kind"]["位置"] >= 1

    def test_time_anchor_counted(self):
        d = archive_density("今早七点十分，他倒在上游闸房外。")
        assert d["by_kind"]["时刻"] >= 1

    def test_count_noun_anchor_counted(self):
        d = archive_density("旧客户名单分成三页，第一页有46个账号。")
        assert d["by_kind"]["计数"] >= 1

    def test_summary_has_fewer_archives_than_scene(self):
        """摘要 vs 现场——这是定稿和机器稿的核心差别之一。"""
        summary = "邵广勤是闸房维修工，今早猝死，死因还没查清。"
        scene = ("邵广勤是闸房维修工，四十七岁，住在二楼最里面那间。"
                 "听镇上的人说，今早七点十分，他倒在上游闸房外，"
                 "胸口发闷，脸朝下扣在积水里。")
        assert archive_density(scene)["total"] > archive_density(summary)["total"]

    def test_empty_safe(self):
        d = archive_density("")
        assert d["total"] == 0
        assert d["per_k"] == 0.0

    def test_hint_generated_when_low(self):
        h = archive_hint("他走了。", min_per_k=2.0)
        assert h
        assert "档案密度" in h

    def test_no_hint_when_high(self):
        text = "四十七岁，住在二楼最里面那间。今早七点十分。46个账号。12页。"
        assert archive_hint(text, min_per_k=2.0) == ""


# ============================================================
# 三、推进位 / 驻留位
# ============================================================

class TestAdvanceDwell:
    """把「重复」和「复读」分开：推进位不计数，驻留位才计数。"""

    def test_advance_verb_is_advance(self):
        assert classify_hits("他手指敲了下桌面。") == "advance"
        assert classify_hits("他拿起包，推开了门。") == "advance"
        assert classify_hits("他看清了那张照片。") == "advance"

    def test_dwell_marker_is_dwell(self):
        assert classify_hits("他手指又敲了下桌面。") == "dwell"
        assert classify_hits("喉咙依旧发紧。") == "dwell"

    def test_dwell_wins_over_advance(self):
        """驻留信号优先——「他又按了按门把手」写的是同一件事。"""
        assert classify_hits("他又按了按门把手。") == "dwell"

    def test_unknown_defaults_to_advance(self):
        """判不准要往推进那边靠——宁可漏报，不要误伤定稿。"""
        assert classify_hits("屋里有股潮湿味。") == "advance"


# ============================================================
# 四、prompt 契约
# ============================================================

class TestPromptContracts:
    def test_draft_prompt_has_breath_rule(self):
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS
        p = DEFAULT_INSTRUCTIONS["draft"]
        assert "一段 = 一口气" in p
        assert "呼吸单位" in p
        assert "摄影机" in p

    def test_draft_prompt_has_archive_rule(self):
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS
        p = DEFAULT_INSTRUCTIONS["draft"]
        assert "给现场不给摘要" in p
        assert "物件可以有脾气" in p

    def test_polish_prompt_has_breath_rule(self):
        from app.pipeline.prompts import DEFAULT_INSTRUCTIONS
        p = DEFAULT_INSTRUCTIONS["polish"]
        assert "一段 = 一口气" in p
        assert "禁止把原文的一句话拆成独立成段" in p

    def test_humanize_rule_allows_same_channel_when_advancing(self):
        from app.generation.humanize import system_prompt_addendum
        p = system_prompt_addendum("draft")
        assert "推进位" in p
        assert "驻留位" in p

    def test_humanize_rule_demotes_absolute_channel_limit(self):
        from app.generation.humanize import system_prompt_addendum
        p = system_prompt_addendum("draft")
        assert "同一通道允许连续出现" in p

    def test_style_card_calibrated(self):
        """文风卡要写进定稿实测值。"""
        from app import config as _config
        p = _config.REPO / "backend" / "data" / "projects" / "20261014-c6c432" / "style_dna.md"
        if not p.exists():
            pytest.skip("本书文风卡不在本机")
        t = p.read_text(encoding="utf-8")
        assert "段内逗号" in t
        assert "档案密度" in t
        assert "推进位" in t
