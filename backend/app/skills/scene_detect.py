"""本章场景识别：判断这一章需要哪几个分场景写法模块。

## 为什么需要这一层

分场景写法（战斗/心理/情感/生活质感）做成模块后，注入方式有两种：
- **全量注入**：每章都把所有场景模块塞进上下文。问题有两个——
  1. 上下文预算被挤占（每个模块摘要 600-1200 字，全量就是 4-5K 字）；
  2. **更糟的是模型会照着无关技法硬塞内容**——这章没有恋爱，
     却注入恋爱模块，模型就会开始往正文里编情话和心理描写。

- **按需注入**：先判断本章属于哪几类，只注入命中的。

所以这里要解决的就是：**怎么用最便宜的方式判断本章有哪些场景。**

## 判断依据的优先级

从高到低（越靠前信号越硬）：

| 优先级 | 来源 | 为什么可信 |
|:---:|:---|:---|
| 1 | 章细纲的「场景」字段 | 作者/LLM 已经写明这是什么场景，最硬的信号 |
| 2 | 节奏骨架 plot_beats 四段 | 起承转合里写了动作/冲突 → 有战斗；写了认知反差 → 有心理 |
| 3 | 方向卡 beat_type | climax 大概率有战斗或恐怖爆发 |
| 4 | 关键词计数 | 兜底。词频统计容易误判，所以只当补充 |

**关键设计**：关键词命中数必须达到阈值才算命中，且生活质感模块
（scene_livedetail）几乎总是注入——它是所有场景的底子，不是"某一种场景"。

## 为什么不调 LLM

每章多一次 LLM 调用 = 多几十秒延迟 + 一份 token 成本，
而这里的判断本质上是个关键词分类任务，规则能覆盖 80% 的情况。
剩下的漏判，代价是「这章少了个针对性技法提示」，
比误判（塞进无关技法导致模型乱写）小得多。

所以这里的取舍是**宁可漏判，不可误判**。
"""
from __future__ import annotations

import re
from typing import Iterable

# ============================================================
# 场景 → 模块映射
# ============================================================

#: 识别出的场景标签 → 写作模块名
SCENE_TO_MODULES: dict[str, tuple[str, ...]] = {
    "combat": ("scene_combat",),
    "psychology": ("psychology_inner",),
    "romance": ("scene_romance",),
    "horror": ("coc_horror",),
}

#: 底子模块：任何一章都要，不靠识别
BASE_MODULES: tuple[str, ...] = ("scene_livedetail",)

# ============================================================
# 关键词表
# ============================================================
# 每个场景一个 (强信号, 弱信号) 二元组。
# 强信号命中 1 个即可判定；弱信号需要累计到阈值。
# 分强弱的理由：「杀」这种字在「杀人」和「凶手」里都出现，
# 但「搏」「围攻」「制服」几乎只在战斗语境出现。

_KEYWORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], int]] = {
    "combat": (
        # 强信号：几乎只在战斗语境
        ("搏", "围攻", "制服", "缠斗", "交手", "过招", "还手", "反击",
         "格挡", "闪避", "刺穿", "劈砍", "掐住", "按住", "掀翻", "摔在",
         "拳", "刀锋", "刃口", "伤口", "血涌", "重伤", "制服不了"),
        # 弱信号：需要累计
        ("打", "冲", "撞", "扑", "踢", "抓", "扭", "挣", "退", "挡", "躲",
         "扑上去", "动手", "拼命", "搏斗", "厮杀", "缠"),
        2,
    ),
    "psychology": (
        # 强信号：认知/心理活动的专有表述
        # （「犹豫/迟疑/挣扎/矛盾」写成双字词更准——单字「决」会误命中「决定」类中性句）
        ("心里清楚", "心里明白", "意识到", "回想", "记起", "想起",
         "念头", "心里一动", "犹豫了", "迟疑", "挣扎", "矛盾",
         "自我怀疑", "说服自己", "下了决心", "在心里", "自言自语",
         "翻了翻", "算了算", "推演", "复盘"),
        # 弱信号
        ("想", "觉得", "以为", "怀疑", "担心", "记得", "希望", "怕",
         "心里", "脑中", "暗自", "不由得"),
        3,
    ),
    "romance": (
        # 强信号：关系进展 + 情绪外显的身体反应
        # 分两类收词：
        #  · 关系类（心动/牵手/拥抱/吻）——推进关系的物理动作
        #  · 外显类（脸红/耳根/通红/忍不住看）——情绪压不住的生理泄露，
        #    对「心里想但嘴上不说」的角色无效，对「藏不住」的角色有效
        ("心动", "牵手", "拥抱", "拥抱住", "抱住", "吻", "亲吻", "脸颊",
         "通红", "脸红", "耳根", "怦然", "眷恋", "爱意", "动情",
         "情愫", "暧昧", "试探对方", "忍不住看", "说不出话",
         "不敢看", "凑近", "贴近", "靠过去", "拉住", "拽住"),
        # 弱信号：需要密度累计，避免"喜欢/在意"单个词就误判
        ("喜欢", "在意", "心疼", "照顾", "陪", "等", "笑", "温柔",
         "靠近", "手指", "目光", "心软", "心虚", "忍不住", "脱口"),
        4,
    ),
    "horror": (
        # 强信号
        ("诡异", "诡异", "不寒而栗", "毛骨悚然", "发霉", "腐朽",
         "渗出", "蠕动", "扭曲", "无面", "看不清脸", "咯吱",
         "亡者", "死者", "尸体", "残页", "禁忌", "不可名状"),
        # 弱信号
        ("黑", "暗", "冷", "湿", "响", "影", "腐", "血", "旧", "低语"),
        3,
    ),
}

#: 场景关键词出现在「章细纲场景名」里时的加权（场景名比正文可靠得多）
_SCENE_NAME_HINT = re.compile(
    r"(战斗|交手|搏|厮杀|追逐|逃亡|围攻|反击|刺杀|"
    r"告白|摊牌|诀别|重逢|拥抱|亲密|暧昧|告白|"
    r"独处|回忆|内心|独白|抉择|动摇|挣扎|"
    r"遭遇|异变|侵蚀|污染|仪式|祭|诡|恐怖|惊悚|发现|真相)",
)

#: 场景块名（「第1场：外桥追逐」）里的意图词——**独立于正文强信号表**。
#:
#: 为什么分开：作者给场景起名时用的是「追逐」「告白」「独处」「遭遇」这类
#: 概括词，它们几乎不出现在正文里。早期版本让场景名去匹配正文的强信号词表，
#: 结果「外桥追逐」匹配不上任何词——名字里明明写着追逐，正文也写了围攻制服，
#: 却因为名字没命中而只拿 2 分，战斗模块不注入。这两个词表必须各自维护。
_SCENE_NAME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "combat": ("战斗", "交手", "搏", "厮杀", "追逐", "逃亡", "围攻",
               "反击", "刺杀", "缠斗", "制服", "对峙", "突围", "追杀", "搏斗"),
    "psychology": ("独处", "回忆", "内心", "独白", "抉择", "动摇", "挣扎",
                   "沉默", "思考", "犹豫", "回忆杀", "夜谈", "自省"),
    "romance": ("告白", "摊牌", "诀别", "重逢", "拥抱", "亲密", "暧昧",
                "心动", "表白", "约会", "吻", "相拥", "耳语", "许诺"),
    "horror": ("遭遇", "异变", "侵蚀", "污染", "仪式", "祭", "诡", "恐怖",
               "惊悚", "发现", "真相", "崩塌", "献祭", "禁地", "遗迹", "异响"),
}

#: 明确不该触发战斗的场景（避免"打"字误判）
_COMBAT_NEGATIVE = re.compile(
    r"(打电话|打开|打开手机|打开灯|打开门|打开电脑|打开文件|"
    r"打开书|打算|打扫|打量|打算做|打伞|打嗝|打盹|打嗝|打印|"
    r"打字|打算说|打得|打得过|打不开|打雷)",
)

#: 亲属称谓出现在**叙述里**时压制恋爱判定——「哥哥」「妹妹」是关系，不是恋爱。
#:
#: 但只在叙述文本上生效，不看实体名：`entities` 会被喂进 text，
#: 而「江喻的姐姐」这种**角色名本身就带亲属字**很常见
#: （如「闸房维修工邵广勤」无关，但「陶露」这类无关）——
#: 一旦角色名里带「妹」「姐」，整章恋爱信号会被误压成 0。
#: 所以这个负向只在「叙述正文」子集上跑，不扫实体名。
_ROMANCE_NEGATIVE = re.compile(r"(哥哥|姐姐|妹妹|弟弟|父母|父亲|母亲|兄妹|姐弟|爷奶)")


# ============================================================
# 文本抽取
# ============================================================

def _flatten(*sources: object) -> str:
    """把各种形态的来源拍平成一段可搜索的文本。

    刻意不深挖结构：判断只需要字面信号，深挖反而会漏
    （比如 entities_on_stage 里的角色名很少带场景词）。
    """
    out: list[str] = []

    def walk(node: object, depth: int = 0) -> None:
        if depth > 4:
            return
        if node is None:
            return
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                # 字段名本身也可能有信号（"scene"/"emotion"/"conflict"）
                if isinstance(k, str):
                    out.append(k)
                walk(v, depth + 1)
        elif isinstance(node, (list, tuple, set)):
            for v in node:
                walk(v, depth + 1)

    for s in sources:
        walk(s)
    return "\n".join(out)


def _scene_names_from_plot(plot_text: str) -> list[str]:
    """从章细纲里抽出场景块名（「第1场：<场名>」）。"""
    names: list[str] = []
    for m in re.finditer(r"第\s*\d+\s*场\s*[：:]\s*([^\n]+)", plot_text or ""):
        name = m.group(1).strip()
        if name:
            names.append(name)
    return names


# ============================================================
# 核心
# ============================================================

def detect_scenes(
    *,
    plot_text: str = "",
    brief: dict | None = None,
    focus: str = "",
    entities: Iterable[str] | None = None,
    ch_kind: str | None = None,
) -> dict:
    """判断本章有哪些场景类型。

    返回 {"scenes": [...], "scores": {...}, "modules": [...], "reasons": {...}}
    scenes  命中的场景标签（无序，建议按 SCENE_TO_MODULES 的声明顺序）
    modules 应当注入的模块名（已去重，含底子模块）
    reasons 每个场景的判定依据（调试与作者追问时用）
    """
    brief = brief if isinstance(brief, dict) else {}
    selected = brief.get("selected") if isinstance(brief.get("selected"), dict) else {}

    scene_names = _scene_names_from_plot(plot_text)

    # 信号源分两组，因为**否定语境只能扫叙述，不能扫实体名**：
    #   narr_text 叙述侧（细纲正文 + 方向卡）——参与否定判定
    #   text      全量（叙述 + 实体名）——参与正向命中
    # 角色名里带「妹妹」「姐姐」很常见，混进否定判定会误杀整章恋爱信号。
    narr_text = _flatten(
        plot_text,
        selected.get("plot_beats"),
        selected.get("summary"),
        selected.get("hooks"),
        selected.get("focus"),
        focus,
    )
    text = narr_text + "\n" + "\n".join(str(x) for x in (entities or []))

    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}

    for scene, (strong, weak, weak_need) in _KEYWORDS.items():
        # 命中种类数，不是出现次数——同一个词出现 20 次也只算 1，
        # 否则长章节会被单个词刷爆成假阳。
        hits_strong = [kw for kw in strong if kw in text]
        hits_weak = [kw for kw in weak if kw in text]
        # 场景名单独判定：名字里出现「追逐/告白/独处」这类词是作者
        # 直接标注的意图，比正文里偶然出现一个「打」可靠得多。
        name_hit = any(
            kw in nm for nm in scene_names
            for kw in _SCENE_NAME_KEYWORDS.get(scene, ())
        )

        neg_hit = (
            _COMBAT_NEGATIVE.search(narr_text) is not None if scene == "combat"
            else _ROMANCE_NEGATIVE.search(narr_text) is not None if scene == "romance"
            else False
        )

        score = 0
        why: list[str] = []
        # ---- 阈值 2，满分 5 ----
        # 阈值定 2 的理由：「脸红 + 拉住 + 凑近」三个强信号同时出现，
        # 在纯日常章里几乎不可能巧合——**信号密度本身就是证据**，
        # 不需要再靠场景名或弱信号来凑分。
        # （早期把阈值定 3，导致「脸红+拉住+凑近」只得 2 分被判非恋爱，
        #   而这种章明明就是恋爱戏——阈值必须让密度说话。）
        #
        # 分档：
        #   场景名 2 + 强信号密集(≥4) 3 + 强信号(2~3) 2 + 强信号(1) 1 + 弱信号 1
        if name_hit:
            score += 2
            why.append("场景名")
        if len(hits_strong) >= 4:
            score += 3
            why.append(f"强信号密集×{len(hits_strong)}")
        elif len(hits_strong) >= 2:
            score += 2
            why.append(f"强信号×{len(hits_strong)}")
        elif hits_strong:
            score += 1
            why.append("强信号×1")
        if len(hits_weak) >= weak_need:
            score += 1
            why.append(f"弱信号×{len(hits_weak)}")

        # 否定语境只压制不清零——否则「打算杀他」会把真战斗压掉
        if neg_hit and scene in ("combat", "romance"):
            score -= 1
            why.append("否定语境")

        scores[scene] = score
        if score >= 2 and why:
            reasons[scene] = "、".join(why)

    # beat_type 硬信号：高潮章大概率有爆发场景。
    # 给 3 分（不是阈值 2）——这是**弱兜底**，只该在真的写到 2 分时补一把，
    # 不该自己就把判定推过阈：beat_type=climax 的章未必是战斗章。
    beat = (selected.get("beat_type") or "").lower()
    if beat == "climax":
        if scores.get("combat", 0) < 3:
            scores["combat"] = max(scores["combat"], 3)
        if scores.get("horror", 0) < 3:
            scores["horror"] = max(scores["horror"], 3)

    scenes = [s for s in SCENE_TO_MODULES if scores.get(s, 0) >= 2]

    # 副本章默认带恐怖底子（本书是克苏鲁无限流）
    if ch_kind == "dungeon" and "horror" not in scenes:
        scenes.append("horror")

    modules: list[str] = list(BASE_MODULES)
    for s in scenes:
        for m in SCENE_TO_MODULES.get(s, ()):
            if m not in modules:
                modules.append(m)

    return {
        "scenes": scenes,
        "scores": scores,
        "modules": modules,
        "reasons": reasons,
    }


def auto_modules_for(
    *,
    plot_text: str = "",
    brief: dict | None = None,
    focus: str = "",
    entities: Iterable[str] | None = None,
    ch_kind: str | None = None,
    requested: list[str] | None = None,
) -> list[str]:
    """给注入点用的便捷封装：显式点名优先，否则按场景识别。

    显式 requested 时**不做场景识别**——作者手动勾了就是他的判断，
    规则再聪明也不该覆盖。"""
    if requested:
        return list(dict.fromkeys(requested))
    return detect_scenes(
        plot_text=plot_text, brief=brief, focus=focus,
        entities=entities, ch_kind=ch_kind,
    )["modules"]