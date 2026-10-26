"""知情表（按章）+ 字数场数分档。

模块职责边界：
- beat.py    字数/场数分档表（全局唯一真值，门禁与 prompt 都读它）
- store.py   按章知情表的读写
- gen.py     知情表自动推导
- replan.py  骨架重排
"""
from __future__ import annotations

from .beat import BEAT_BOUNDS, beat_bounds, chapter_words_target

#: store.py 是否已就位。store.py 由 Task 2 交付，缺席期间本包仍然可用，
#: 只是不提供 KnowledgeStore / knowledge_for。
STORE_AVAILABLE = False

__all__ = [
    "BEAT_BOUNDS",
    "STORE_AVAILABLE",
    "beat_bounds",
    "chapter_words_target",
]

try:  # store.py 在 Task 2 落地；它缺席时不能拖垮 beat 这条唯一真值
    from .store import KnowledgeStore, knowledge_for
except ModuleNotFoundError as _exc:  # pragma: no cover - 取决于 store.py 是否已落地
    # 只吞「本包的 store 子模块本身不存在」（相对导入缺失时 name == "app.knowledge.store"）。
    # store.py 内部缺第三方依赖时 name 是那个第三方包名，必须照常报错，不能伪装成「还没写」。
    if _exc.name != f"{__name__}.store":
        raise
    STORE_AVAILABLE = False
else:
    STORE_AVAILABLE = True
    __all__ += ["KnowledgeStore", "knowledge_for"]


def __getattr__(name: str):
    """store.py 缺席时给出明确 ImportError。

    不用 None 占位的原因：下游写 `KnowledgeStore(...)` 会拿到
    `TypeError: 'NoneType' object is not callable`，那是「类型错了」的报错，
    把真正原因（store.py 还没写）藏了起来。这里的 ImportError 直接说明缺哪个模块。

    注意：模块级 `__getattr__` 只在常规查找失败时才会被调用——
    store.py 就位时这两个名字是真实属性，根本走不到这里。
    """
    if name in ("KnowledgeStore", "knowledge_for"):
        if STORE_AVAILABLE:  # pragma: no cover - 正常路径不会进到这里
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        raise ImportError(
            f"{__name__}.{name} 依赖 {__name__}.store，但该模块尚不存在"
            "（store.py 由 Task 2 交付）。当前可用的是 app.knowledge.beat 的"
            "分档接口（BEAT_BOUNDS / beat_bounds / chapter_words_target）。"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
