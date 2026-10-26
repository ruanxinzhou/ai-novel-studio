"""字数/场数分档表测试：分档是全局唯一真值，门禁与 prompt 都读它。

注意：本文件里 `EXPECTED_BOUNDS` 是**契约断言**，不是第二份真值——
真值仍然只有 `app/knowledge/beat.py` 里的 `BEAT_BOUNDS` 一处。
这里把期望值整体写死，是为了让任何一格数值被改动都立刻红。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import app.knowledge
from app.knowledge.beat import (
    BEAT_BOUNDS,
    beat_bounds,
    chapter_words_target,
)

#: 期望的分档表（契约快照）。30 个数值全部钉死。
EXPECTED_BOUNDS: dict[str, dict] = {
    "hook": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3500,
        "gate_lo": 2400, "gate_hi": 3900,
    },
    "sell": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3500,
        "gate_lo": 2400, "gate_hi": 3900,
    },
    "engine": {
        "scenes_min": 2, "scenes_max": 3,
        "words_min": 3000, "words_max": 3800,
        "gate_lo": 2400, "gate_hi": 4200,
    },
    "climax": {
        "scenes_min": 3, "scenes_max": 4,
        "words_min": 3500, "words_max": 4500,
        "gate_lo": 2800, "gate_hi": 5000,
    },
    "cooldown": {
        "scenes_min": 2, "scenes_max": 2,
        "words_min": 2500, "words_max": 3000,
        "gate_lo": 2000, "gate_hi": 3400,
    },
}


class TestBeatBounds:
    def test_five_beat_types_present(self):
        assert set(BEAT_BOUNDS) == {"hook", "sell", "engine", "climax", "cooldown"}

    def test_bounds_table_matches_expected_contract(self):
        """把 30 个数值一次性钉死。

        这是对**期望表**的断言，不是第二份真值——真值仍在 beat.py。
        改动任何一格数值（包括此前完全没有断言覆盖的 `sell` 整档、
        以及 engine 的 words_max 3800）都会让本测试红。
        """
        assert BEAT_BOUNDS == EXPECTED_BOUNDS

    def test_bounds_table_has_no_extra_keys(self):
        """每档的键必须恰好是 6 个，多一个少一个都不行。"""
        expected_keys = {
            "scenes_min", "scenes_max",
            "words_min", "words_max",
            "gate_lo", "gate_hi",
        }
        for name, band in BEAT_BOUNDS.items():
            assert set(band) == expected_keys, name

    def test_climax_keeps_high_ceiling(self):
        b = beat_bounds("climax")
        assert b["words_max"] == 4500
        assert b["gate_hi"] == 5000
        assert b["scenes_max"] == 4

    def test_hook_band_values(self):
        """hook 档数值（hook 不是最短档——cooldown 的 words_max 更低）。"""
        assert beat_bounds("hook")["words_max"] == 3500
        assert beat_bounds("hook")["scenes_max"] == 3

    def test_cooldown_is_shortest_band(self):
        assert beat_bounds("cooldown")["words_max"] == 3000
        assert beat_bounds("cooldown")["scenes_max"] == 2

    def test_unknown_beat_falls_back_to_engine(self):
        assert beat_bounds("nonsense") == beat_bounds("engine")
        assert beat_bounds("") == beat_bounds("engine")

    def test_beat_type_is_normalized(self):
        """大小写与首尾空白都要归一化。"""
        assert beat_bounds("CLIMAX") == beat_bounds("climax")
        assert beat_bounds("  climax  ") == beat_bounds("climax")
        assert beat_bounds("Climax") == beat_bounds("climax")
        assert beat_bounds(" COOLDOWN ") == beat_bounds("cooldown")

    def test_gate_window_contains_word_band(self):
        """门禁区间必须包住字数区间，否则会出现「目标合法但门禁报错」。"""
        for name, b in BEAT_BOUNDS.items():
            assert b["gate_lo"] <= b["words_min"], name
            assert b["gate_hi"] >= b["words_max"], name

    def test_words_target_inside_band(self):
        for name, b in BEAT_BOUNDS.items():
            t = chapter_words_target(name)
            assert b["words_min"] <= t <= b["words_max"], name

    def test_unknown_beat_target_uses_engine_fallback(self):
        """未知 beat_type 的目标字数走 engine 回退。"""
        assert chapter_words_target("nonsense") == chapter_words_target("engine")
        assert chapter_words_target("") == chapter_words_target("engine")
        assert chapter_words_target("nonsense") == 3400

    def test_beat_bounds_returns_copy_not_shared_table(self):
        """返回的是副本——下游就地改不会污染全局唯一真值。"""
        got = beat_bounds("engine")
        assert got is not BEAT_BOUNDS["engine"]
        got["words_max"] = 99999
        assert BEAT_BOUNDS["engine"]["words_max"] == 3800
        assert beat_bounds("engine")["words_max"] == 3800

    def test_beat_bounds_copy_on_fallback_path_also_isolated(self):
        """回退路径返回的同样是副本。"""
        got = beat_bounds("nonsense")
        got["words_max"] = 1
        assert BEAT_BOUNDS["engine"]["words_max"] == 3800


class TestSceneLimits:
    def test_climax_allows_four_scenes(self):
        """climax 的 4 场合法——旧实现硬卡场数会误杀高潮章。"""
        assert beat_bounds("climax")["scenes_max"] >= 4

    def test_engine_caps_at_three(self):
        assert beat_bounds("engine")["scenes_max"] == 3


class TestPackageExports:
    """包入口的导出契约：store.py 缺席时不能把 None 写进命名空间。"""

    def test_beat_symbols_always_exported(self):
        import app.knowledge as pkg

        assert pkg.BEAT_BOUNDS is BEAT_BOUNDS
        assert pkg.beat_bounds is beat_bounds
        assert pkg.chapter_words_target is chapter_words_target
        for name in ("BEAT_BOUNDS", "beat_bounds", "chapter_words_target"):
            assert name in pkg.__all__, name

    def test_store_symbols_not_exported_when_store_missing(self):
        """store.py 缺席（Task 2 未落地）时，__all__ 不含 store 两个符号。"""
        import app.knowledge as pkg

        if not pkg.STORE_AVAILABLE:
            assert "KnowledgeStore" not in pkg.__all__
            assert "knowledge_for" not in pkg.__all__

    def test_store_symbols_raise_clear_import_error_when_store_missing(self):
        """取用 store 符号必须抛明确的 ImportError，而不是 None 占位。"""
        import app.knowledge as pkg

        if pkg.STORE_AVAILABLE:
            pytest.skip("store.py 已落地，符号可用")
        with pytest.raises(ImportError) as excinfo:
            pkg.KnowledgeStore  # noqa: B018
        assert "store" in str(excinfo.value)
        with pytest.raises(ImportError) as excinfo:
            pkg.knowledge_for  # noqa: B018
        assert "store" in str(excinfo.value)

    def test_unknown_attribute_raises_attribute_error(self):
        import app.knowledge as pkg

        with pytest.raises(AttributeError):
            pkg.definitely_not_a_real_symbol  # noqa: B018


class TestStoreImportGuard:
    """守卫导入的三种场景（用临时目录隔离，不碰真实 app/knowledge）。

    - store.py 缺席      → 降级可用，不导出 store 符号
    - store.py 存在      → 正常导出
    - store.py 内部缺依赖 → 必须照常报错，不能被守卫吞掉
    """

    @staticmethod
    def _fresh_package(tmp_path: Path, store_source: str | None) -> str:
        """在 tmp_path 下造一个 kgprobe 包（内容与真实 app/knowledge 相同）。

        用独立包名而不是 app.knowledge：真实的 app 包此刻已在 sys.modules 里，
        同名导入会复用它、根本到不了 tmp_path。
        """
        pkg_dir = tmp_path / "kgprobe"
        pkg_dir.mkdir(parents=True)
        real_beat = Path(app.knowledge.__file__).with_name("beat.py")
        (pkg_dir / "beat.py").write_text(
            real_beat.read_text(encoding="utf-8"), encoding="utf-8"
        )
        init_src = Path(app.knowledge.__file__).read_text(encoding="utf-8")
        (pkg_dir / "__init__.py").write_text(init_src, encoding="utf-8")
        if store_source is not None:
            (pkg_dir / "store.py").write_text(store_source, encoding="utf-8")
        return str(tmp_path)

    @staticmethod
    def _import_isolated(root: str, modname: str):
        """在隔离的 sys.path 下导入 modname，结束后清理 sys.modules。"""
        saved_path = list(sys.path)
        saved_mods = {k: v for k, v in sys.modules.items() if k.startswith(modname)}
        for k in saved_mods:
            del sys.modules[k]
        sys.path.insert(0, root)
        try:
            return importlib.import_module(modname)
        finally:
            sys.path[:] = saved_path
            for k in [k for k in list(sys.modules) if k.startswith(modname)]:
                del sys.modules[k]
            sys.modules.update(saved_mods)

    def test_store_missing_degrades_without_exporting_store_symbols(self, tmp_path):
        root = self._fresh_package(tmp_path, None)
        mod = self._import_isolated(root, "kgprobe")
        assert mod.STORE_AVAILABLE is False
        assert "KnowledgeStore" not in mod.__all__
        assert "knowledge_for" not in mod.__all__
        assert mod.BEAT_BOUNDS["engine"]["words_max"] == 3800
        with pytest.raises(ImportError):
            mod.KnowledgeStore  # noqa: B018

    def test_store_present_exports_store_symbols(self, tmp_path):
        store_src = (
            "class KnowledgeStore:\n"
            "    pass\n"
            "\n"
            "def knowledge_for(*args, **kwargs):\n"
            "    return None\n"
        )
        root = self._fresh_package(tmp_path, store_src)
        mod = self._import_isolated(root, "kgprobe")
        assert mod.STORE_AVAILABLE is True
        assert "KnowledgeStore" in mod.__all__
        assert "knowledge_for" in mod.__all__
        assert mod.KnowledgeStore is not None
        assert callable(mod.knowledge_for)

    def test_store_broken_dependency_still_raises(self, tmp_path):
        """store.py 自身缺第三方依赖 → 必须照常抛 ModuleNotFoundError。"""
        store_src = "import definitely_not_installed_pkg_xyz\n"
        root = self._fresh_package(tmp_path, store_src)
        with pytest.raises(ModuleNotFoundError) as excinfo:
            self._import_isolated(root, "kgprobe")
        assert "definitely_not_installed_pkg_xyz" in str(excinfo.value)
