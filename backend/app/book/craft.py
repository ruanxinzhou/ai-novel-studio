"""网文写作工艺手册（双层应用）。

每一项 craft 是一条「写作时必须执行的工艺动作」，渲染后注入所有 LLM 提示词作为
REQUIRED 材料。所有阶段的 AI（回转寿司写卡 / 大纲 / 正文 draft / 润色 / 审稿）都会
自动看到并遵守。

双层应用：
  - 书级 craft：整本书层面应用。黄金三章 = 书的第 1-3 章，抓读者、立主角、抛钩子。
  - 卷级 craft：每卷（每场副本）独立应用一次完整技能链。该卷的 Day1-Day3 = 该卷的「黄金三章」，
    负责把读者「重新拽进场」（读者可能隔了几天才看下一卷），相当于每一卷都有自己的「小开头」。
"""
from __future__ import annotations

from typing import Any


# ---- 10 项通用工艺（用户给定，固化为默认规则）----

CRAFT_SKILLS: list[dict[str, Any]] = [
    {
        "id": "concept_planning",
        "name": "选材构思",
        "what_it_does": "简介压成题材 / 消费点 / hook / premise / 故事引擎",
        "applies_to": ["简介", "大纲"],
        "budget_kb": 3,
        "rule": (
            "动笔前必须先回答 5 个问题：(1) 题材标签是什么（让读者一秒知道要不要点开）；"
            "(2) 消费点/爽点是什么（读者冲着什么来的，必须写在简介里）；"
            "(3) hook 是什么（一句话悬念，让读者想点进去）；"
            "(4) premise（核心设定 + 铁律，全书不打破）；"
            "(5) 故事引擎是什么（主角要什么、阻碍是什么、为什么会一直推进）。"
            "五个问题答不清不要开写。"
        ),
    },
    {
        "id": "opening",
        "name": "开头成交",
        "what_it_does": "前 300 字抓手 / 1500 字卖点 / 主角亮相",
        "applies_to": ["大纲", "剧情", "正文"],
        "budget_kb": 3,
        "rule": (
            "黄金三章的核心技能，双层应用：\n"
            "（书级）第 1 章前 300 字必须给出抓手（一个具体的、感官化的、"
            "让读者心里咯噔一下的细节/动作/一句话）；前 1500 字内必须亮出本书的核心卖点"
            "（主角是谁+他的日常+一点不对劲，告诉读者这是什么类型、后面会发生什么）；"
            "主角第一次出场必须带有一个清晰的「欲望/困境/软肋」标签，读者 3 秒内能共情。\n"
            "（卷级）每场副本的 Day1 章节同样适用：副本开场前 300 字必须给"
            "陌生躯壳里醒来的违和感+一个具体的不对劲细节；1500 字内让读者知道"
            "这一场是什么地方、死者是谁、倒计时已经开始。\n"
            "铁律：前 300 字不许写景、不许写天气、不许世界观背景大段介绍，"
            "必须从主角的一个正在进行的动作/感官切入。"
        ),
    },
    {
        "id": "volume_outline",
        "name": "分卷与章纲",
        "what_it_does": "黄金三章 / 卷目标兑现日程 / 前 10-20 章章纲",
        "applies_to": ["简介", "大纲", "剧情"],
        "budget_kb": 3,
        "rule": (
            "开写每一卷之前，必须先出：(1) 本卷黄金三章（Day1-Day3 各章的抓手、卖点、章末钩子）；"
            "(2) 本卷目标兑现日程——本卷要兑现什么爽点/什么真相/什么羁绊加深/什么手艺入手/污染加多少，"
            "列成 checklist，写完一卷全部打勾才算「卷关」；(3) 本卷前 10-20 章章纲（至少每章一句话 + 情绪档位 + 章末钩子）。\n"
            "书级黄金三章（第 1-3 章）是全书 volume_outline 的「第零卷」，必须在开写第 1 章前完成。"
        ),
    },
    {
        "id": "plot_logic",
        "name": "剧情欲望接力",
        "what_it_does": "开篇接棒 / 欲望升级 / 章末交棒 + 章内因果五层",
        "applies_to": ["大纲", "剧情", "正文", "润色"],
        "budget_kb": 4,
        "rule": (
            "每章必须回答：(1) 这一章开头主角的「当前欲望」是什么（接棒上一章的尾巴）；"
            "(2) 这一章内欲望如何升级/受阻/转向；(3) 章末交出什么新钩子（交棒给下一章）。\n"
            "章内因果五层检查：动作→反应→后果→新选择→新代价。五层断一层就是「流水账」，必须重写。\n"
            "禁止「没有欲望驱动」的纯环境描写/纯设定说明，所有信息必须嵌在主角的行动中给出。"
        ),
    },
    {
        "id": "character_consistency",
        "name": "人物连续性",
        "what_it_does": "目标 / 情绪 / 关系 / 身体 / 声音 五类状态不崩",
        "applies_to": ["剧情", "正文", "润色"],
        "budget_kb": 2,
        "rule": (
            "每章写完后逐项核对主角唐迟的五个状态：\n"
            "(1) 目标状态——本章他要什么、上一章的目标有没有推进/放弃/转移；\n"
            "(2) 情绪状态——上章结尾的情绪是什么，本章开场是否承接（不允许突然从崩溃跳到冷静）；\n"
            "(3) 关系状态——对煤球/苏晚/本场 NPC 的态度有没有发生变化、变化是否有事件支撑；\n"
            "(4) 身体状态——侵蚀度/HP/SAN（副本内）/伤口/疲惫/有没有吃过饭睡过觉，跨章必须连贯；\n"
            "(5) 声音状态——唐迟口头禅（活着就行/别搞我/我先看下日志）、内心独白语气必须统一，不能一章嘴碎一章沉默。\n"
            "五个状态任意一项跨章断层就是人物崩了，必须修。"
        ),
    },
    {
        "id": "transition",
        "name": "场景桥梁",
        "what_it_does": "时间跳切 / 空间切换 / 情绪承接 / 视角切换",
        "applies_to": ["正文", "润色"],
        "budget_kb": 7,
        "rule": (
            "所有跳切必须用一个「桥接物」过渡：感官细节（光/声/气味）、动作（放下杯子→下一个场景他端起杯子）、"
            "或一句主角的内心独白。\n"
            "禁止无桥空行硬切（「…… / 第二天 / 与此同时」这种直接空行硬跳），除非是刻意制造断裂感（例如 SAN 滑落/被污染后的恍惚）。\n"
            "视角切换（唐迟→NPC→苏晚）必须在切换的第一段就通过一个强烈的感官锚或姓名锚让读者知道换视角了，"
            "不能写完半段才发现视角换了。"
        ),
    },
    {
        "id": "dialogue",
        "name": "对白结构",
        "what_it_does": "关系压力 / 人物声音 / 信息嵌入 / 对白刀口",
        "applies_to": ["正文", "润色"],
        "budget_kb": 2,
        "rule": (
            "对白四原则：(1) 每段对话必须承载人物之间的关系压力（说出口的话不是字面意思）；"
            "(2) 每个主要人物必须有可辨识的「声音」（口头禅/句长/用词习惯，张阿姨不能说苏晚会说的话）；"
            "(3) 信息必须嵌在冲突/情绪里，不许角色坐下来互相交换设定（「我给你讲讲规则啊」这种对白禁止）；"
            "(4) 关键对白必须有「刀口」——一句话同时推进剧情+暴露关系+扎人心，三选二才算及格，三占三才是好对白。\n"
            "唐迟的对白特征：短句、习惯反问、被问到时会下意识先看一眼旁边（找安全距离）、"
            "紧张时会用程序员术语打比方（「这相当于线上 P0 啊」）。"
        ),
    },
    {
        "id": "chapter_ending",
        "name": "章末收束",
        "what_it_does": "拉力 / 余韵 / 回钩 / 下章承接（追更感）",
        "applies_to": ["剧情", "正文"],
        "budget_kb": 2,
        "rule": (
            "每章最后 200 字必须完成四件事之一或多件：\n"
            "(1) 拉力——抛出一个新问题/新发现/新危险，让读者必须翻下一章；\n"
            "(2) 余韵——情绪停顿一拍（煤球蹭腿、苏晚的一条未读消息、窗外的光），给读者一个喘口气的时刻；"
            "(3) 回钩——回扣本章开头的某个细节/那句话，让章首尾形成闭环（猫碗满了→猫碗还是半满）；"
            "(4) 下章承接——主角做出一个决定，下一章必然兑现。\n"
            "禁止章节结束在「他累得倒头就睡/他走出了门/第二天醒来」这种软收，每章最后一句必须是「钉子」，钉在读者心里。"
        ),
    },
    {
        "id": "anti_ai_voice",
        "name": "去 AI 味（润色默认）",
        "what_it_does": "清空泛总结 / 套话氛围 / 说明书对白 / 统一腔调",
        "applies_to": ["剧情", "正文", "润色"],
        "budget_kb": 5,
        "rule": (
            "润色时必须清除四类 AI 痕迹：\n"
            "(1) 泛总结——「他感到一股说不出的恐惧涌上心头/气氛变得诡异起来」这类空泛情绪词，必须换成具体感官"
            "（脖子后面的汗毛竖起来/后槽牙不受控制地咬紧/舌尖尝到铁锈味）；\n"
            "(2) 套话氛围——「夜色如墨/月光如水/阴风阵阵」这类烂大街描写，换成桐州市桂花里具体的声音气味灯光；\n"
            "(3) 说明书对白——「你要知道/事实上/其实/也就是说」这种解释型对白，删掉重写；\n"
            "(4) 统一腔调——所有角色说话都一个味、内心独白全是文艺腔，必须拉开差异（唐迟冷幽默/张阿姨碎嘴/"
            "苏晚干脆/ NPC 按身份给音）。\n"
            "润色不是加形容词，是去空、去套、去假、去匀。"
        ),
    },
    {
        "id": "consistency_review",
        "name": "完稿审查",
        "what_it_does": "七项一致性逐项核对，两项不稳不交稿",
        "applies_to": ["剧情", "正文", "润色"],
        "budget_kb": 3,
        "rule": (
            "章节 polish 定稿前必须过七项检查：\n"
            "(1) 时间线一致（七日锚日号对不对、蓝星时间与副本时间是否错位合理）；\n"
            "(2) 人物状态一致（见 character_consistency 五项）；\n"
            "(3) 物理规则一致（有没有打破书级铁律/讣告五条铁律/神不出场）；\n"
            "(4) 信息揭示节奏一致（有没有提前剧透未来才该知道的规则）；\n"
            "(5) 伏笔/回钩一致（本章埋下的伏笔记了吗？之前埋的钩子有没有呼应？）；\n"
            "(6) 情绪档位一致（是否达到 seven_day_pulse 里对应 Day 的张力档位）；\n"
            "(7) 手艺/侵蚀一致（本章是否使用手艺、副作用有没有写、污染度有没有对应增加）。\n"
            "七项中任何两项不稳，打回 draft 重写，不给出稿。"
        ),
    },
]


# ---- 阶段 × 启用技能映射（双层：书级蓝星章 / 卷级副本章）----

BOOK_PHASE_SKILLS: dict[str, list[str]] = {
    "concept":   ["concept_planning"],
    "opening":   ["opening", "character_consistency", "anti_ai_voice"],
    "outline":   ["volume_outline", "plot_logic", "concept_planning"],
    "plot":      ["plot_logic", "character_consistency", "chapter_ending", "dialogue"],
    "draft":     ["plot_logic", "character_consistency", "chapter_ending", "dialogue", "transition", "anti_ai_voice"],
    "polish":    ["transition", "dialogue", "anti_ai_voice", "character_consistency"],
    "review":    ["consistency_review"],
}

VOLUME_PHASE_SKILLS: dict[str, list[str]] = {
    "volume_concept": ["concept_planning"],
    "volume_opening": ["opening", "character_consistency", "anti_ai_voice"],
    "volume_outline": ["volume_outline", "plot_logic"],
    "plot":           ["plot_logic", "character_consistency", "chapter_ending", "dialogue"],
    "daily_draft":    ["plot_logic", "character_consistency", "chapter_ending", "dialogue", "transition", "anti_ai_voice"],
    "polish":         ["transition", "dialogue", "anti_ai_voice", "character_consistency"],
    "settle":         ["consistency_review", "chapter_ending"],
}


# ---- 黄金三章模板（可被书级/卷级分别定制覆盖）----

DEFAULT_GOLDEN3: dict[str, Any] = {
    "chapter_1": {
        "role": "立主角 + 甩钩子",
        "must_have": [
            "前 300 字：一个正在进行的具体动作/感官细节切入（不许写景写天气开场）",
            "前 1500 字：主角亮相——身份/性格/软肋/当前困境，读者 3 秒共情",
            "前 3000 字：日常铺陈+核心卖点信号（告诉读者「这本书是什么类型」）",
            "章末：钩子钉子——一个不对劲的细节，让读者必须翻下一章（第一章结尾 = 猫碗失神）",
        ],
        "must_not": [
            "前 1000 字不许出现世界观大段解说",
            "不许主角自我介绍式独白（「我叫唐迟，今年28岁」）",
            "第一章结尾不许进入副本（保持在蓝星）",
        ],
    },
    "chapter_2": {
        "role": "入事件 + 立规则",
        "must_have": [
            "开场承接第一章的钩子（失神/睡着/醒来），不硬切",
            "主角被拉入副本/陌生躯壳醒来，Day 1 锚点建立（七日倒计时启动）",
            "通过死者字条/残页/NPC 临死只言拼出三条基础规则（七天死/死了真死/活过回得去）",
            "主角第一次怂的本能反应（想逃、不信、找 rational 解释）—— 保持他的性格一致",
            "章末：第一次直面「不对劲」（一个超出他 rational 解释的事件），钉子收尾",
        ],
        "must_not": [
            "不许 NPC 坐下来给主角上规则课",
            "不许主角立刻冷静下来像老玩家一样分析（他是怂的新人，应该慌）",
            "Day1 张力全开（按 seven_day_pulse，Day1 tension=3，只是陌生+微妙不适）",
        ],
    },
    "chapter_3": {
        "role": "立引擎 + 给爽点 + 拉长线",
        "must_have": [
            "主角第一次主动做决定（不再只是被动反应）——标志着他接受了「必须活下去」这个目标",
            "一个小爽点/小反杀/小发现（程序员职业病帮了他，比如通过细节观察发现一个规则漏洞/救下一个NPC）",
            "展示故事引擎的持续性（为什么读者要继续看——不是单纯求生，开始有「我要搞清楚这一切」的主动欲）",
            "长线伏笔埋入（苏晚/煤球/残页/神的注视 四选二埋一线）",
            "章末：一个更大的危机/真相露头，读者确认「这书值得追下去」",
        ],
        "must_not": [
            "爽点不能开挂——必须是用他的理性/怂/程序员本能换回来的小胜",
            "不能在第3章就让主角掌握手艺（按 seven_day_pulse，金手指 Day3 首次亮相，注意 Day3 ≠ 第3章）",
            "第3章结尾不能让读者觉得「已经稳了」，必须留不确定性",
        ],
    },
}


def render_craft(d: dict) -> str:
    skills = d.get("skills", CRAFT_SKILLS)
    golden3 = d.get("golden3", DEFAULT_GOLDEN3)
    book_golden3_custom = d.get("book_golden3_notes", [])
    volume_golden3_notes = d.get("volume_golden3_notes", [])

    out = ["## 写作工艺手册（双层：书级 + 卷级每场副本）\n"]

    out.append("### 启用原则\n")
    out.append(
        "- 本书是网文，必须按网文节奏写：每章都有钩子，每卷都有黄金三章，"
        "爽点与代价一体两面，章末必须追更感。\n"
        "- 双层应用：书级（蓝星开篇/全书）用一套；每卷每场副本独立再用一次整套技能——"
        "副本 Day1-Day3 就是该卷的「小黄金三章」，承担「把隔了几天没看的读者重新拽进场」的任务。\n"
        "- AI 每次生成（剧情卡/正文/润色/审稿）必须检查当前上下文所处的阶段（书/卷×DayN），"
        "自动启用对应的技能清单，按规则逐项遵守。\n"
    )

    out.append("### 工艺技能清单\n")
    for s in skills:
        out.append(f"**{s['id']} · {s['name']}**（适用：{'/'.join(s['applies_to'])}；{s['budget_kb']}KB）\n")
        out.append(f"{s['rule']}\n")

    out.append("### 阶段 → 启用技能映射\n")
    out.append("**书级（蓝星章节）**：\n")
    for phase, skills_ids in BOOK_PHASE_SKILLS.items():
        out.append(f"- {phase}：{' + '.join(skills_ids)}")
    out.append("\n**卷级（每场副本 Day1-Day7）**：\n")
    for phase, skills_ids in VOLUME_PHASE_SKILLS.items():
        out.append(f"- {phase}：{' + '.join(skills_ids)}")
    out.append("")

    out.append("### 黄金三章模板（书级 + 卷级通用）\n")
    for ch_id, ch in golden3.items():
        out.append(f"**{ch_id} · {ch['role']}**\n")
        out.append("必须有：")
        for it in ch["must_have"]:
            out.append(f"  - {it}")
        out.append("禁忌：")
        for it in ch["must_not"]:
            out.append(f"  - {it}")
        out.append("")

    if book_golden3_custom:
        out.append("**本书定制备注（书级黄金三章特别要求）**：\n")
        for note in book_golden3_custom:
            out.append(f"- {note}")
        out.append("")

    if volume_golden3_notes:
        out.append("**卷级黄金三章（每场副本 Day1-Day3）特别要求**：\n")
        for note in volume_golden3_notes:
            out.append(f"- {note}")
        out.append("")

    return "\n".join(out)
