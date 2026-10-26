"""知情表（按章）+ 字数场数分档。

模块职责边界：
- beat.py    字数/场数分档表（全局唯一真值，门禁与 prompt 都读它）
- store.py   按章知情表的读写
- gen.py     知情表自动推导
- replan.py  骨架重排
"""
from __future__ import annotations

from .beat import BEAT_BOUNDS, beat_bounds, chapter_words_target

try:  # store.py 在 Task 2 落地；它缺席时不能拖垮 beat 这条唯一真值
    from .store import KnowledgeStore, knowledge_for
except ModuleNotFoundError as _exc:  # pragma: no cover - 取决于 store.py 是否已落地
    # 只吞「store 模块本身不存在」；store.py 内部缺第三方依赖必须照常报错。
    if (_exc.name or "").rsplit(".", 1)[-1] != "store":
        raise
    KnowledgeStore = None  # type: ignore[assignment,misc]
    knowledge_for = None  # type: ignore[assignment,misc]

__all__ = [
    "BEAT_BOUNDS",
    "beat_bounds",
    "chapter_words_target",
    "KnowledgeStore",
    "knowledge_for",
]
