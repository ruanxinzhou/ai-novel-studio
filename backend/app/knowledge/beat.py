"""字数/场数分档表——全局唯一真值。

**为什么分档而不是一刀 3000 字**：实测定稿三章 6766/4994/6227 字，
一刀 3000 会让 climax 章（陆骁收网、红线结算）的高潮被削掉，
而且 81000 字的卷要拉成 27 章，七日倒计时的节奏全碎。

**为什么场数跟着 beat_type 走**：起承转合（qi/cheng/zhuan/he）是
**一个场景的内部结构**，不是一章的四个场景。hook 章 2 场就够，
climax 章 3-4 场才铺得开硬卡场数。

门禁区间（gate_lo/gate_hi）必须包住字数区间（words_min/words_max），
否则会出现「目标字数合法但门禁报错」的自相矛盾。
"""
from __future__ import annotations

#: beat_type → 场数区间 / 字数区间 / 门禁上下限
#: 字数区间是作者的目标；门禁区间是机器判定，留出容差。
BEAT_BOUNDS: dict[str, dict] = {
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

#: 未知 beat_type 的回退档
_FALLBACK = "engine"


def beat_bounds(beat_type: str) -> dict:
    """取某 beat_type 的分档；未知/空值回退到 engine 档。"""
    key = str(beat_type or "").strip().lower()
    return BEAT_BOUNDS.get(key, BEAT_BOUNDS[_FALLBACK])


def chapter_words_target(beat_type: str) -> int:
    """该档的目标字数（区间中值，四舍五入到百位）。"""
    b = beat_bounds(beat_type)
    mid = (b["words_min"] + b["words_max"]) / 2
    return int(round(mid / 100.0) * 100)
