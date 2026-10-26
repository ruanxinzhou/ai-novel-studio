"""字数/场数分档表测试：分档是全局唯一真值，门禁与 prompt 都读它。"""
from __future__ import annotations

from app.knowledge.beat import (
    BEAT_BOUNDS,
    beat_bounds,
    chapter_words_target,
)


class TestBeatBounds:
    def test_four_beat_types_present(self):
        assert set(BEAT_BOUNDS) == {"hook", "sell", "engine", "climax", "cooldown"}

    def test_climax_keeps_high_ceiling(self):
        b = beat_bounds("climax")
        assert b["words_max"] == 4500
        assert b["gate_hi"] == 5000
        assert b["scenes_max"] == 4

    def test_hook_is_shortest(self):
        assert beat_bounds("hook")["words_max"] == 3500
        assert beat_bounds("hook")["scenes_max"] == 3

    def test_cooldown_is_shortest_band(self):
        assert beat_bounds("cooldown")["words_max"] == 3000
        assert beat_bounds("cooldown")["scenes_max"] == 2

    def test_unknown_beat_falls_back_to_engine(self):
        assert beat_bounds("nonsense") == beat_bounds("engine")
        assert beat_bounds("") == beat_bounds("engine")

    def test_gate_window_contains_word_band(self):
        """门禁区间必须包住字数区间，否则会出现「目标合法但门禁报错」。"""
        for name, b in BEAT_BOUNDS.items():
            assert b["gate_lo"] <= b["words_min"], name
            assert b["gate_hi"] >= b["words_max"], name

    def test_words_target_inside_band(self):
        for name, b in BEAT_BOUNDS.items():
            t = chapter_words_target(name)
            assert b["words_min"] <= t <= b["words_max"], name


class TestSceneLimits:
    def test_climax_allows_four_scenes(self):
        """climax 的 4 场合法——旧实现硬卡场数会误杀高潮章。"""
        assert beat_bounds("climax")["scenes_max"] >= 4

    def test_engine_caps_at_three(self):
        assert beat_bounds("engine")["scenes_max"] == 3
