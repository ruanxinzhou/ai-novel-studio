"""写作模块库（T10，规则文本源自 Chinese-WebNovel-Skill 专项模块 runtime.md）。

模块 = 可注入的写作规则（人物一致性/章末拉力/去 AI 味…），运行时按层默认集
或用户显式勾选，作为 REQUIRED 材料进入装配——规则必须整体进上下文，装不下
显式失败，绝不静默截断。

三段链路（上游→下游）：前置规划（concept_planning→opening/volume_outline）
→ 正文执行（plot_logic→character_consistency→transition→dialogue
→chapter_ending→anti_ai_voice）→ 完稿收口（consistency_review）。
"""
from __future__ import annotations

from pathlib import Path

from app.context import Material, MaterialFamily

MODULE_DIR = Path(__file__).resolve().parent / "modules"

MODULES: dict[str, dict] = {
    "concept_planning": {
        "title": "选材构思：简介压成题材/消费点/hook/premise/故事引擎",
        "stages": ["intro", "outline"],
        "default_for": [],
    },
    "opening": {
        "title": "开头成交：前300字抓手/1500字卖点/主角亮相",
        "stages": ["outline", "plot", "draft"],
        "default_for": [],
    },
    "volume_outline": {
        "title": "分卷与章纲：黄金三章/分卷设计/前10-20章章纲/卷末兑现",
        "stages": ["intro", "outline", "plot"],
        "default_for": ["outline"],
    },
    "plot_logic": {
        "title": "因果链：动机→触发→决策→后果→兑现",
        "stages": ["outline", "plot", "draft", "polish"],
        "default_for": ["plot"],
    },
    "character_consistency": {
        "title": "人物连续性：目标/情绪/关系/身体/声音五类状态不崩",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    "transition": {
        "title": "场景桥梁：时间跳切/空间切换/情绪承接/视角切换",
        "stages": ["draft", "polish"],
        "default_for": [],
    },
    "dialogue": {
        "title": "对白结构：关系压力/人物声音/信息嵌入/对白刀口",
        "stages": ["draft", "polish"],
        "default_for": [],
    },
    "chapter_ending": {
        "title": "章末收束：拉力/余韵/回钩/下章承接（追更感）",
        "stages": ["plot", "draft"],
        "default_for": [],
    },
    "anti_ai_voice": {
        "title": "去 AI 味：清空泛总结/套话氛围/说明书对白/统一腔调",
        "stages": ["plot", "draft", "polish"],
        "default_for": ["draft", "polish"],
    },
    "consistency_review": {
        "title": "完稿审查：六项一致性逐项稳/不稳，两项不稳不交稿",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
}


def available_modules() -> list[dict]:
    return [
        {"name": name, "title": meta["title"], "stages": meta["stages"],
         "default_for": meta["default_for"],
         "rules_bytes": len(module_rules(name).encode("utf-8"))}
        for name, meta in MODULES.items()
    ]


def module_rules(name: str) -> str:
    if name not in MODULES:
        raise ValueError(f"未知写作模块：{name}")
    path = MODULE_DIR / name / "runtime.md"
    if not path.exists():
        raise ValueError(f"模块规则文件缺失：{name}")
    return path.read_text(encoding="utf-8")


def resolve_for_layer(layer: str, requested: list[str] | None = None) -> list[str]:
    """显式请求优先；未指定时用该层默认集。未知模块一律报错。"""
    if requested:
        for name in requested:
            if name not in MODULES:
                raise ValueError(f"未知写作模块：{name}")
        return list(requested)
    return [name for name, meta in MODULES.items() if layer in meta["default_for"]]


def skill_materials(layer: str, requested: list[str] | None = None) -> list[Material]:
    """启用模块 → REQUIRED 材料（source_id 前缀 skill:）。"""
    return [
        Material(
            source_id=f"skill:{name}",
            family=MaterialFamily.REQUIRED,
            content=module_rules(name),
            title=f"写作模块[{name}]",
        )
        for name in resolve_for_layer(layer, requested)
    ]
