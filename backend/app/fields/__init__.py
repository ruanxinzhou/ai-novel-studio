"""场（Field）数据模型：每场=一个讣告=一个副本+一个间章对。

数据持久化位置：
    backend/data/projects/{pid}/fields/index.json        # 场目录（元信息列表）
    backend/data/projects/{pid}/fields/{field_no}.json  # 单场完整数据（含讣告/Day/source_materials）
    backend/data/projects/{pid}/fields/nodes/{no}/...   # 场流水线节点中间产物（RunStore）
    backend/data/projects/{pid}/fields/coc/{no}.json    # COC 多轮跑团会话状态
"""
from app.fields.store import (
    ALL_CARD_KEYS,
    CardLock,
    CardVersion,
    DayWork,
    FieldStore,
    FieldWork,
    Obituary,
    SCAFFOLD_CARD_KEYS,
    SCHEDULE_CARD_KEY,
    WALKTHROUGH_CARD_KEY,
)
from app.fields.run_store import (
    FieldRunStore, NodeArtifact,
    FIELD_PIPELINE_NODES, STOPPING_POINTS, DEFAULT_VARIANT_COUNTS,
)
from app.fields.coc_run import (
    CocRunState, CocRunStore, CocTurn, CocRoll,
    DEFAULT_PLAYER_CARD,
    parse_dice_rolls, detect_day,
)

__all__ = [
    "ALL_CARD_KEYS",
    "CardLock",
    "CardVersion",
    "DayWork",
    "FieldStore",
    "FieldWork",
    "Obituary",
    "SCAFFOLD_CARD_KEYS",
    "SCHEDULE_CARD_KEY",
    "WALKTHROUGH_CARD_KEY",
    "FieldRunStore", "NodeArtifact",
    "FIELD_PIPELINE_NODES", "STOPPING_POINTS", "DEFAULT_VARIANT_COUNTS",
    "CocRunState", "CocRunStore", "CocTurn", "CocRoll", "DEFAULT_PLAYER_CARD",
    "parse_dice_rolls", "detect_day",
]
