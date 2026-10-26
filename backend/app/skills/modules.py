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
        "title": "分卷与章纲：黄金三章/卷目标兑现日程/前10-20章章纲",
        "stages": ["intro", "outline", "plot"],
        "default_for": ["outline"],
    },
    "plot_logic": {
        "title": "欲望接力：开篇接棒/欲望升级/章末交棒 + 章内因果五层",
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
        "title": "完稿审查：七项一致性逐项稳/不稳（含跨章联结），两项不稳不交稿",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    # 书专属模块（《第七日的亡者》）
    "coc_horror": {
        "title": "克苏鲁恐怖：五层叠加/神不出场/侵蚀度显影/无空泛形容词",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    "infinite_loop_unit": {
        "title": "七日单元剧：一场=一讣告=七日+幕间/执念机制代价三支柱/D4反转D7终局",
        "stages": ["outline", "plot"],
        "default_for": [],
    },
    "interlude_relief": {
        "title": "幕间调剂：8:2 日常+暗刺/代价落地/活人关系/软钩收尾",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    "obsession_cost": {
        "title": "执念代价：五维度代价轮换/对等分档/五窗口持续展示/记账法升级",
        "stages": ["outline", "plot", "draft", "polish"],
        "default_for": [],
    },
    # ---- 分场景写法（按本章场景自动注入，见 scene_detect.py）----
    #
    # 这些**不进 default_for**：默认集是全书通用的硬规则，分场景模块要靠
    # scene_detect.detect_scenes() 判断本章属于哪几类后按需注入——
    # 全量注入会把无关技法塞进上下文挤爆正文预算，
    # 且「这章没有恋爱」却注入恋爱模块，模型会开始往正文里硬塞情话。
    "scene_livedetail": {
        "title": "生活质感：三色描写分级/镜头调度/五感轮换/细节立人",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    "scene_combat": {
        "title": "战斗写法：四拍动作/三拍节奏/禁止报招式名/胜必留代价",
        "stages": ["plot", "draft"],
        "default_for": [],
    },
    "psychology_inner": {
        "title": "心理内心戏：情绪→身体→动作/禁叙述者下结论/情绪滞后性",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
    "scene_romance": {
        "title": "情感恋爱戏：情绪禁直说/潜台词/生活细节承载/冰山三式",
        "stages": ["plot", "draft", "polish"],
        "default_for": [],
    },
}


def available_modules() -> list[dict]:
    return [
        {"name": name, "title": meta["title"], "stages": meta["stages"],
         "default_for": meta["default_for"],
         "rules_bytes": len(module_rules(name).encode("utf-8")),
         "digest_bytes": len(module_digest(name).encode("utf-8"))}
        for name, meta in MODULES.items()
    ]


def module_rules(name: str) -> str:
    if name not in MODULES:
        raise ValueError(f"未知写作模块：{name}")
    path = MODULE_DIR / name / "runtime.md"
    if not path.exists():
        raise ValueError(f"模块规则文件缺失：{name}")
    return path.read_text(encoding="utf-8")


def module_digest(name: str) -> str:
    """模块摘要版规则（digest.md）：紧凑指令化要点，上下文紧张时代替全文注入。
    仍是整体注入（只是更短的整体），不违背「绝不静默截断」的原则。"""
    if name not in MODULES:
        raise ValueError(f"未知写作模块：{name}")
    path = MODULE_DIR / name / "digest.md"
    if not path.exists():
        raise ValueError(f"模块摘要文件缺失：{name}")
    return path.read_text(encoding="utf-8")


def resolve_for_layer(layer: str, requested: list[str] | None = None) -> list[str]:
    """显式请求优先；未指定时用该层默认集。未知模块一律报错。"""
    if requested:
        for name in requested:
            if name not in MODULES:
                raise ValueError(f"未知写作模块：{name}")
        return list(requested)
    return [name for name, meta in MODULES.items() if layer in meta["default_for"]]


def skill_materials(layer: str, requested: list[str] | None = None,
                    digest: bool = False) -> list[Material]:
    """启用模块 → REQUIRED 材料（source_id 前缀 skill:）。
    digest=True 注入摘要版（紧凑指令），False 注入完整规则。"""
    return [
        Material(
            source_id=f"skill:{name}",
            family=MaterialFamily.REQUIRED,
            content=module_digest(name) if digest else module_rules(name),
            title=f"写作模块[{name}]" + ("（摘要）" if digest else ""),
        )
        for name in resolve_for_layer(layer, requested)
    ]
