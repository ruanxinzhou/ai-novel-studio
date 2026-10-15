"""七日情绪张力基线：Day1-Day7 每日情绪档位/必须节拍/常见失误。审稿时卡档位。"""
from __future__ import annotations

# Day1-Day7 默认情绪基线
DEFAULT_PULSE = [
    {
        "day": 1,
        "name": "苏醒·陌生",
        "tension": 3,
        "mission": "在陌生躯壳中醒来，发现自己变成讣告上的人；接触陌生环境；初步建立死者身份认知",
        "required_beat": "必须有至少一个「不对劲」的瞬间（哪怕只是一个眼神、一个异样反应），给读者悬疑钩子",
        "trap": "不要 Day1 就把诡异全开，要有陌生感和微妙不适打底；不要 Day1 就让主角完全相信/理解现状",
    },
    {
        "day": 2,
        "name": "摸牌·不安",
        "tension": 4,
        "mission": "尝试扮演死者身份；接触死者的人际/生活；第一次用死者视角看世界；收集基础信息",
        "required_beat": "主角第一次感受到死者执念的牵引（莫名情绪/冲动/闪回）",
        "trap": "不要急于揭示真相；不要让主角太快适应身份（他是怂的，应该是慌张的）",
    },
    {
        "day": 3,
        "name": "试水·第一血",
        "tension": 5,
        "mission": "第一次被迫使用执念金手指；遭遇第一场小诡异/小危机；知道这个世界确实不对劲",
        "required_beat": "执念金手指首次亮相，必须有明确的人间副作用（哪怕很小、主角当时没察觉）",
        "trap": "首次使用金手指不能太爽；副作用要自然、不喧宾夺主；不要 Day3 就出大恐怖",
    },
    {
        "day": 4,
        "name": "深入·直面",
        "tension": 7,
        "mission": "主动调查真相；深入死者卷入的事件核心；遭遇第一次直面死局（第一次生死一线）",
        "required_beat": "主角第一次正面遭遇不可名状之物/规则诡异；SAN 初次滑落；开始理解七天倒计时",
        "trap": "恐怖场景不要用形容词堆，要通过主角的感官错位和认知失调来传递",
    },
    {
        "day": 5,
        "name": "拆局·真相",
        "tension": 8,
        "mission": "拼出真相轮廓；发现死者为何而死；发现自己被卷入的是什么；代价开始累计",
        "required_beat": "核心真相揭露（至少 70%）；主角面临道德抉择或生存抉择",
        "trap": "真相揭露段不能用大段解说，必须通过发现和拼凑；不要把邪神讲明白",
    },
    {
        "day": 6,
        "name": "崩坏·抉择",
        "tension": 9,
        "mission": "绝望感压顶；所有准备指向最后一搏；可能失去重要NPC/盟友；做出无法回头的选择",
        "required_beat": "代价集中兑现（金手指副作用累积到无法忽视）；主角被逼到墙角，怂人必须做狠决定",
        "trap": "不要开挂救场；怂人不应该突然变英雄，他的狠是被逼出来的；Day6 结尾必须留 Day7 钩子",
    },
    {
        "day": 7,
        "name": "第七日·死期",
        "tension": 10,
        "mission": "最终生死一搏；直面死期；讣告上的死因要么正面撞上要么被改写；要么活要么死",
        "required_beat": "死期兑现；散场钩子（讣告自焚、残页飘落）；主角死里逃生后的虚脱感；若改命必须有代价",
        "trap": "第七日必须到张力顶点；但不要写主角战胜邪神——他只是在人间尺度活下来；结尾要留余味和淡淡苍凉",
    },
]


def render_pulse(day_beats: list[dict]) -> str:
    beats = day_beats or DEFAULT_PULSE
    out = ["## 七日情绪张力基线（锚·不可跳日）\n"]
    out.append("| Day | 名称 | 张力 | 本日任务 | 必须节拍 |")
    out.append("|-----|------|------|----------|----------|")
    for b in beats:
        out.append(
            f"| {b['day']} | {b['name']} | {b['tension']}/10 | "
            f"{b['mission'][:40]}… | {b['required_beat'][:30]}… |"
        )
    out.append(
        "\n**写作提醒**：非线性叙事（闪回/NPC视角/时间诡计）允许，"
        "但每章标注的 Day 必须和实际推进一致；"
        "审稿会检查本章情绪张力档位是否达到 Day 要求，"
        "Day7 章节张力不到 8 分会报警。"
    )
    return "\n".join(out) + "\n"


def get_beat(day_beats: list[dict], day: int) -> dict | None:
    for b in day_beats or DEFAULT_PULSE:
        if int(b["day"]) == day:
            return b
    return None
