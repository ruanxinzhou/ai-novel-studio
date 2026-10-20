"""节奏骨架（Rhythm）路由：7 天强度曲线 + 密度分析 + 章节分配建议 + 告警。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.rhythm")

_RHYTHM_SYSTEM = """你是一名克苏鲁风格无限流小说的节奏设计师，正在为《第七日的亡者》某一卷做"节奏骨架"推演。
前序产物（副本核、跑团走向、宿主、魂穿、唐迟七日历、详设 6 卡已锁定的死亡/高潮/线索/核心反转/起点/代价上限）已全部完成，
你的任务是根据已锁定的唐迟入戏第1-7天（T-6→T-0）节点分布，给出一份整卷节奏蓝图。

【双轨时间线提醒】
- day=1~7 指「唐迟入戏第1-7天」（他自己的T-6→T-0倒计时），不是原剧本的Day1-Day7；
- 原剧本是独立故事（可能是几小时、几天、几个月、几代人），它有自己的事件流；
- 做节奏规划时，key_beats_progress 和 chapter_plan 的 day 字段必须指唐迟入戏第几天，
  并在 beats/chapter focus 里同时标注「原剧本此刻正在哪个事件」，让作者清楚两条时间线并行。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。返回格式：

{
  "pacing_ratio": {"setup": 0.18, "build": 0.32, "climax": 0.35, "cooldown": 0.15},
  "key_beats_progress": [
    {"day": 1, "intensity": 2, "label": "入本惊悚", "original_timeline_anchor": "原剧本此刻事件简述（10-30字）"},
    {"day": 2, "intensity": 3, "label": "第一滴血", "original_timeline_anchor": "..."},
    {"day": 3, "intensity": 4, "label": "规则显形", "original_timeline_anchor": "..."},
    {"day": 4, "intensity": 3, "label": "喘息/误导", "original_timeline_anchor": "..."},
    {"day": 5, "intensity": 5, "label": "中盘反转", "original_timeline_anchor": "..."},
    {"day": 6, "intensity": 6, "label": "同伴接连殒落", "original_timeline_anchor": "..."},
    {"day": 7, "intensity": 7, "label": "T-0终极高潮", "original_timeline_anchor": "原剧本此刻可能已过终点/或正在终点"}
  ],
  "warnings": [
    {"type": "dearth", "day": 4, "msg": "第4天无高潮节点，节奏可能塌腰"},
    {"type": "cluster", "day": 5, "msg": "第5天同时有死亡+高潮+3条线索，信息过载"}
  ],
  "volume_hooks": {
    "opening_hook": "卷首钩子：第一章开场第一幕的强视觉/强异常/强悬念点，30-60字，必须让读者在首屏就感到不对劲（例：宿舍天花板渗出咸水、宿管阿姨用脚跟走路）",
    "ending_hook": "卷尾钩子：最后一章收尾时留给下一卷/结算前的余韵悬念/代价锚点，30-60字；可能是'折痕合上，但唐迟口袋里多了一枚不属于任何副本的铜扣'这种回收伏笔的悬念"
  },
  "suggested_length": 63000,
  "suggested_chapter_range": [14, 20],
  "chapter_plan": [
    {
      "chapter": 1, "day": 1,
      "title": "章节小标题（6-12字，克苏鲁/冷峻风格，不带章节号）",
      "focus": "一句话10-30字本章焦点（括号内标注原剧本此刻事件）",
      "words": 4500,
      "beat_type": "hook",
      "time_window": "入戏第1天·深夜23:17-23:40（必须精确到时间段，指唐迟主观时间）",
      "location": "宿舍302门口+走廊",
      "characters_on_stage": ["唐迟", "宿管王姨（半透明）"],
      "plot_beats": {
        "qi":   "起：开场状态/触发事件（20-50字）",
        "cheng": "承：信息展开/冲突升级（30-60字）",
        "zhuan": "转：异常升级/认知反转/规则出现（30-60字）",
        "he":    "合：本章收尾状态/悬念落点（20-50字）"
      },
      "foreshadow_seeds": ["伏笔点1：出现一枚刻着学号的铜扣，暂不解释", "伏笔点2：走廊尽头有缓慢的水滴声"],
      "foreshadow_payback": ["回收上一卷/上一章的伏笔：第1章讣告里提到的'咸水'此刻具象化"],
      "sensory_hook": "感官锚点：咸腥海风+湿冷触感，让读者像泡在海水里"
    }
  ],
  "notes": "整体节奏建议：前3章黄金三章拉满恐怖密度，第4章给主角短暂喘息+误导红鲱鱼..."
}

字段约束（必须严格遵守）：
- pacing_ratio 四段 setup/build/climax/cooldown 加起来必须等于 1.0，每段 0.05~0.55。
- key_beats_progress 长度必须是 7，day=1~7 各一条（唐迟入戏第1-7天），intensity 为 1~7 的整数（7=T-0终极高潮），label 4-8 字；每条必须带 original_timeline_anchor 标注原剧本此刻事件。
- warnings 是数组，可以为空。每条必须带 type（dearth=真空日，cluster=过载日，front_load=前重后轻，back_load=前轻后重）、day（除 front_load/back_load 外必填）、msg（中文一句话描述问题）。
- volume_hooks 是必填对象，包含 opening_hook（卷首钩子，30-60字）和 ending_hook（卷尾钩子，30-60字）；opening_hook 要落到 chapter_plan[0] 的 qi（起）开场，ending_hook 要落到 chapter_plan 最后一章的 he（合）收尾。
- suggested_length：整卷总字数（整数，建议 63000~90000，即 14~20 章 × 4500 字）。
- suggested_chapter_range：[min, max] 两个整数，14~20 之间，min<=max。
- chapter_plan：章节分配数组，长度必须在 [min, max] 之间；每章 4000-5000 字，不短于 4000、不超过 5000，推荐 4500。
  每章必须包含以下字段，缺一不可：
  * chapter：从 1 开始递增的卷内相对章号（后端自动加全局偏移）；
  * day：1~7，唐迟入戏第几天；
  * title：章节小标题 6-12 字（克苏鲁/冷峻，不要带"第x章"）；
  * focus：一句话 10-30 字本章焦点（括号内标注原剧本此刻事件）；
  * words：本章预估字数，整数 4000~5000，推荐 4500；
  * beat_type：hook/sell/engine/climax/cooldown 之一；
  * time_window：本章发生的主观时间段（精确到小时/分钟，例"入戏第1天·深夜23:17-23:40"）；
  * location：主要场景地点（一个为主，可带副场景，不超过 20 字）；
  * characters_on_stage：本章出场人物数组（含主角唐迟、NPC、怪物/异常体；用字符串名；不要遗漏关键角色）；
  * plot_beats：起承转合四段，每段 20-80 字中文：
      - qi（起）：开场状态/触发事件/钩子落地；
      - cheng（承）：信息展开/冲突升级/探索推进；
      - zhuan（转）：异常升级/认知反转/规则/死劫显形；
      - he（合）：本章收尾状态/悬念落点/钩子引出下一章；
  * foreshadow_seeds：本章埋下的新伏笔点（数组，每项 15-40 字，可空数组[]但字段必须存在）；
  * foreshadow_payback：本章回收/推进的旧伏笔（数组，每项 15-40 字，可空数组[]但字段必须存在）；
  * sensory_hook：本章核心感官锚点（10-30字，选最强烈的一种体感：触觉/嗅觉/听觉/视觉之一）。
- 【关于章号】chapter_plan[*].chapter 从 1 开始递增，表示卷内相对章号，不要加全局偏移；后端落盘时会根据 Volume.start_chapter_no 自动转为全书章号。
- notes：一段 100-200 字的整体节奏建议，克苏鲁阴冷基调。

注意：节点密度图会由服务端本地计算，你无需重复统计，但要基于这些密度信息给出合理的 warnings、intensity 曲线与章节分配。
"""


@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable
    desk_for: Callable
    chore_provider: Callable


class _AnalyzeIn(BaseModel):
    rhythm_directive: str = ""


class _FinalizeIn(BaseModel):
    pass


def _is_mock(vol) -> bool:
    try:
        from ..book.volume import adapted_premise_of
        premise = adapted_premise_of(vol.adapted_module)
        if isinstance(premise, str) and premise.startswith("测试副本核文本"):
            return True
        hc = vol.host_character or {}
        if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
            return True
    except Exception:
        return False
    return False


def _compute_density_map(vol) -> dict:
    """纯本地计算 7 天节点密度：每天 death/climax/clue/total 计数。"""
    density: dict[int, dict] = {}
    for d in range(1, 8):
        density[d] = {"death": 0, "climax": 0, "clue": 0, "total": 0}
    for n in (vol.death_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["death"] += 1
    for n in (vol.climax_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["climax"] += 1
    for n in (vol.clue_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["clue"] += 1
    for d in range(1, 8):
        density[d]["total"] = density[d]["death"] + density[d]["climax"] + density[d]["clue"]
    # 键转成字符串以便 JSON（前端按数字/字符串都能取）
    return {str(d): density[d] for d in range(1, 8)}


def _all_scaffold_locked(vol) -> bool:
    """校验 6 张详设卡是否都已 locked。"""
    cards = vol.scaffold_cards or {}
    all_keys = ["death_nodes", "climax_nodes", "clue_nodes",
                "core_twist", "tangchi_start", "cost_cap"]
    return all((cards.get(k) or {}).get("locked_at") for k in all_keys)


def _mock_blueprint() -> dict:
    """克苏鲁基调硬编码 mock 节奏蓝图（14 章，每章 4500 字，含六要素+起承转合+卷首卷尾钩子）。"""
    def _ch(rel, day, title, focus, beat, time_window, location, chars, qi, cheng, zhuan, he,
            seeds, payback, sense):
        return {
            "chapter": rel, "day": day, "title": title, "focus": focus, "words": 4500,
            "beat_type": beat, "time_window": time_window, "location": location,
            "characters_on_stage": chars,
            "plot_beats": {"qi": qi, "cheng": cheng, "zhuan": zhuan, "he": he},
            "foreshadow_seeds": seeds, "foreshadow_payback": payback, "sensory_hook": sense,
        }
    return {
        "pacing_ratio": {"setup": 0.18, "build": 0.30, "climax": 0.38, "cooldown": 0.14},
        "key_beats_progress": [
            {"day": 1, "intensity": 2, "label": "入本惊悚", "original_timeline_anchor": "原剧本：深夜302宿舍唐迟从床上睁眼，闻到咸腥海风"},
            {"day": 2, "intensity": 4, "label": "宿管缢亡", "original_timeline_anchor": "原剧本：宿管王姨在走廊悬挂，脚下没有凳子"},
            {"day": 3, "intensity": 5, "label": "报纸睁眼", "original_timeline_anchor": "原剧本：校史馆里一张1997年校报的照片在动"},
            {"day": 4, "intensity": 3, "label": "喘息/红鲱", "original_timeline_anchor": "原剧本：白天校园平静，合唱队招新海报出现"},
            {"day": 5, "intensity": 5, "label": "影子先行", "original_timeline_anchor": "原剧本：林薇从6楼坠下，但影子还在走"},
            {"day": 6, "intensity": 6, "label": "同伴相继", "original_timeline_anchor": "原剧本：苏雅在钟楼顶喊不出声，辅导员的鳃在领口翕动"},
            {"day": 7, "intensity": 7, "label": "海床点名", "original_timeline_anchor": "原剧本：暴雨淹过3楼，47个名字被一一点到"},
        ],
        "warnings": [
            {"type": "dearth", "day": 4, "msg": "第4天无高潮节点，中盘可能塌腰，建议用误导/红鲱鱼维持张力"},
            {"type": "cluster", "day": 5, "msg": "第5天同时有死亡+高潮+3条线索，信息过载，建议分散线索到第6天"},
            {"type": "front_load", "msg": "前3天死亡/线索密度过高，若不控制字数会让读者审美疲劳"},
        ],
        "volume_hooks": {
            "opening_hook": "唐迟在302宿舍睁开眼，天花板正一滴一滴往下渗咸水，水滴落在他枕头上——那是海水，而这座城市离海1400公里。",
            "ending_hook": "折痕在他掌心合上的瞬间，他摸到口袋里多了一枚锈得发绿的铜扣，扣面上刻着他自己的学号。",
        },
        "suggested_length": 63000,
        "suggested_chapter_range": [14, 14],
        "chapter_plan": [
            _ch(1,1,"咸水叩枕","入本+第一异常+倒计时出现（原剧本：唐迟入戏T-6深夜醒来）","hook",
                "入戏第1天·深夜23:00-23:40","302宿舍",["唐迟"],
                "唐迟在自己床上醒来，枕头湿了一片，以为是空调漏水。",
                "开灯发现水渍从天花板缝隙渗出，尝了一口——是咸的，像海水。手机没有信号，电子钟闪着T-6的红字。",
                "走廊传来缓慢的拖地声，脚步声却没有重量；门底下漫进来半寸海水，倒影里有一个弯腰的人影。",
                "唐迟缩回床上贴紧墙，海水停在床沿外一寸；他摸到枕下那张折痕，这才意识到自己已经不在原世界。",
                ["天花板水渍的盐分含微量血迹（第9章回收）","电子钟T-6倒计时刻度比正常少一秒（第13章回收）"],
                ["衔接上卷结尾'折痕发烫'，落地入戏"],
                "咸腥海风灌进鼻腔，舌尖有铁锈味"),
            _ch(2,1,"缢影巡廊","宿管缢亡+咸水规则初显（原剧本：宿管王姨第一夜即死）","sell",
                "入戏第1天·凌晨00:00-01:20","3楼走廊+水房",["唐迟","宿管王姨（已死）","刘保安（声音）"],
                "唐迟鼓起勇气拉开门，走廊灯忽明忽灭，尽头301门口吊着一个人影。",
                "走近发现是宿管王姨，脖子上没有勒痕，但脚跟离地十公分，脚下没有任何垫脚物；水房门后传来刘保安微弱的求救声。",
                "刘保安被关在水房的镜子里，脸贴在镜面上朝外喊'别数脚步'；王姨的尸体突然转头看向唐迟，嘴角裂开笑。",
                "唐迟缩回302锁门，在书桌抽屉翻到一张皱巴巴的《宿舍须知》，第一条写着'深夜听见拖地声，不要数'。",
                ["《宿舍须知》第二条缺失（第6章补齐）","刘保安在镜子里举着半张湿透的饭卡（第3章回收）"],
                ["第一章海水倒影人影→对应王姨尸体"],
                "走廊空气冷得像浸在冰水里，呼气有白雾"),
            _ch(3,2,"镜中溺亡","刘保安溺亡+海水折进校园的认知冲击（原剧本：T-5第一滴血）","engine",
                "入戏第2天·清晨06:30-08:00","宿舍一楼大厅+校门外",["唐迟","刘保安（尸体）","周凯"],
                "天亮后唐迟下楼，整栋宿舍楼静悄悄，大厅地砖上覆着一层潮湿盐霜。",
                "刘保安的尸体趴在大厅前台，脸朝下泡在一滩清澈海水里，死因是溺亡——周围没有一滴水能没过脸。同寝周凯从楼梯上下来，面如土色。",
                "两人推门出去，校园门口竟然是一片灰色的海，校门牌坊半淹在海里，浪拍在台阶上；周凯突然大喊'不对，这所学校在内陆！'",
                "唐迟拉起周凯退回宿舍楼，把《宿舍须知》抄在本子上；周凯颤抖着说他昨晚收到刘保安半条微信，只有'别照镜'三个字。",
                ["校门外那片海里漂着撕碎的校报碎片（第5章回收）","周凯手腕上多了一圈潮湿红痕（第7章回收）"],
                ["第二章镜子里的刘保安→现实中溺亡的刘保安"],
                "海水拍打台阶发出闷响，像有人在水下敲门"),
            _ch(4,2,"半条微信","周凯半条微信/轮回疑云（原剧本：T-5日白天查手机）","engine",
                "入戏第2天·上午09:00-11:30","302宿舍+走廊",["唐迟","周凯"],
                "周凯把手机递给唐迟，聊天记录里刘保安的对话框停在00:47，内容只有语音转文字失败的一串乱码。",
                "唐迟让周凯戴上耳机听语音，里面只有海浪声和一个女声反复数'一、二、三……'；数到十七的时候戛然而止。",
                "走廊再次传来拖地声，两人从猫眼往外看，空无一人，但湿漉漉的脚印正一个一个印到302门口停下。",
                "唐迟决定下楼找更多学生求证，嘱咐周凯不要数脚步不要照镜子，周凯点头答应，但转身时唐迟看见他眼睛里有一瞬间的海。",
                ["周凯瞳孔里的倒影不是唐迟（第7章回收为被附身前兆）","语音里女声数到17=47亡魂的前17个（第13章）"],
                ["刘保安半条微信'别照镜'→第二章镜中刘保安"],
                "耳机里海水声贴着耳膜，冰凉湿滑"),
            _ch(5,3,"校报睁眼","校史馆报纸睁眼/陈阿妹之名（原剧本：T-4日校史馆线索）","engine",
                "入戏第3天·下午14:00-16:00","校史馆一楼+档案柜",["唐迟","苏雅（初遇）"],
                "唐迟摸索到行政楼后的校史馆，门没锁，里面落满盐粒灰尘。",
                "在档案柜翻到1997年的校报合订本，7月14日的头版是47名学生集体失踪；苏雅从后门走进来，手里拿着一本同样的合订本。",
                "两人对视一眼同时翻到头版照片，1997年的毕业合影里47张脸都睁着眼，但最末一排最角落一个女生的眼睛正在慢慢转向镜头；照片底下写着她的名字——陈阿妹。",
                "苏雅低声说她昨晚也听到了点名；两人交换情报，约定今晚一起去钟楼。唐迟把陈阿妹这个名字折角记在笔记本里。",
                ["陈阿妹就是宿管王姨年轻时的名字（第8章回收）","47人毕业照有48个影子（第13章回收）"],
                ["第三章校门外漂的校报碎片→校史馆校报"],
                "旧报纸纸张潮软发黏，像泡过尸水"),
            _ch(6,3,"盐圈困影","盐圈困影/第一次主动求生（原剧本：T-4日夜首次求生）","engine",
                "入戏第3天·深夜22:00-23:50","钟楼前空地+回廊",["唐迟","苏雅","辅导员（远影）"],
                "夜里两人摸到钟楼，钟楼门锁着，但门下缝里透出一种发绿的光。",
                "苏雅掏出一袋粗盐在地上画圈，说这是她家乡赶海时老人教的；两人站进盐圈，绿光里慢慢走出一个穿辅导员衬衫的男人。",
                "男人走近但踩不进盐圈，领口掀开露出下面三对正在翕动的鳃；他张嘴说话却是唐迟自己的声音，叫他'出来'。",
                "两人熬到鸡叫（虽然这里没有鸡），绿光消散，辅导员的身影退进钟楼；唐迟在回廊墙上看到《宿舍须知》第二条：盐圈只能挡一次。",
                ["三对鳃对应三天后辅导员的真正死因（第10章回收）","钟楼门内绿光来自钟摆上挂着的铜扣（第14章回收）"],
                ["《宿舍须知》第二条缺失→第二条补全"],
                "粗盐粒在脚下硌脚，冷汗把后背衬衫泡透"),
            _ch(7,4,"虚昼假安","短暂喘息/虚假安全感/红鲱鱼误导（原剧本：T-3虚假平静日）","cooldown",
                "入戏第4天·白天10:00-15:00","操场+食堂+合唱队招新海报栏",["唐迟","周凯","苏雅"],
                "天亮后校园居然放晴，食堂开门，几个学生在操场上体育课——一切看起来像一所正常大学。",
                "三人在食堂吃饭，周凯说他收到了家人的微信，想今天就回宿舍收拾东西回家；唐迟觉得哪里不对但说不出。",
                "海报栏贴出'校园合唱队招新'，要求报名者在指定时间到礼堂集合，附47个姓名，最后一行赫然印着'唐迟'。",
                "苏雅立刻指出47个姓名正是1997年失踪名单；唐迟回头找周凯，周凯已经不在座位上，他的餐盘里没有动过的饭，碗底沉着一枚铜扣。",
                ["合唱队报名=点名仪式的入口（第8章回收）","周凯餐盘里的铜扣与卷尾铜扣是同一枚（第14章闭环）"],
                ["第四章周凯眼里的海→周凯被替换/诱走"],
                "阳光照在皮肤上却没有温度，所有声音都像隔着一层水"),
            _ch(8,4,"童谣点名","苏雅血字童谣/点名开始（原剧本：T-3夜点名仪式启动）","engine",
                "入戏第4天·深夜23:00-01:00","大礼堂",["唐迟","苏雅","陈阿妹/王姨（显现）","合唱队"],
                "唐迟和苏雅连夜赶到大礼堂，舞台上站着46个学生，面朝他们笑。",
                "46人开始用童声齐唱一首闽南语童谣，每唱一句台下就浮起一个湿漉漉的名字；唐迟数了数，还差一个名字没点——他自己的。",
                "舞台幕布后走出宿管王姨，她的脸慢慢变成1997年的陈阿妹，手里拿着一本点名册朝唐迟递来；苏雅突然把唐迟推开，自己接过了点名册。",
                "苏雅在点名册上划掉自己的名字替唐迟挡下一轮，但她的脚开始变透明；她塞给唐迟一张钟楼顶的钥匙，说'钟声响七次之前，把名字刻到钟上'。",
                ["钥匙其实是铜扣变的（第12章回收）","童谣倒放就是召唤词（第13章回收）"],
                ["陈阿妹之名→王姨就是陈阿妹"],
                "童声像水灌进耳朵，礼堂里弥漫海腥气"),
            _ch(9,5,"坠影离身","林薇坠楼/影子真相/中盘反转（原剧本：T-2中盘反转，林薇之死）","climax",
                "入戏第5天·凌晨02:00-04:30","女生宿舍6号楼+楼下地面",["唐迟","林薇","影子"],
                "唐迟从礼堂冲出去赶去女生6号楼，林薇已经站在天台边缘。",
                "他从楼道往上冲，林薇在天台上哭着说'它跟了我七年，不是我'；唐迟冲上顶楼时看见她的影子从她脚下分离，像个人一样站着。",
                "影子伸手把林薇推了下去；唐迟扑到栏杆边，林薇坠到一楼却没有血——她落地的瞬间融进地面的海水里，影子留在了楼顶，朝唐迟鞠了一躬然后走下楼梯。",
                "唐迟瘫坐在楼顶，手里还握着苏雅的钥匙；他终于明白——'死'不是结束，被影子替换才是真正的死亡。",
                ["影子鞠躬是在致谢唐迟让它解脱（第10章辅导员影子同样行为）","林薇融入海水时手里攥着半张咸水车票（第11章回收）"],
                ["天花板水渍里的微量血迹→林薇坠楼的血已在入戏前预告"],
                "林薇坠楼时的风带着咸雨，打在脸上疼得像挨耳光"),
            _ch(10,5,"无脚合唱","无脚合唱/镜中自己/辅导员的鳃（原剧本：T-2辅导员之死）","engine",
                "入戏第5天·清晨05:00-07:00","男生宿舍澡堂+镜子",["唐迟","辅导员（尸体）","辅导员影子"],
                "唐迟回到男生宿舍想洗把脸冷静，澡堂里传来整齐的合唱声——46个人无伴奏齐唱童谣。",
                "他顺着声音走到最里面的镜子前，镜子里辅导员正站在他身后微笑，领口下三对鳃一张一合；现实中辅导员的尸体泡在澡堂水池里，早已没气。",
                "镜中辅导员伸出手想把唐迟拉进镜里，唐迟抄起灭火器砸碎镜子；镜子碎裂那一刻辅导员影子在碎片里朝他鞠了一躬，和林薇的影子一样。",
                "唐迟从碎镜里看到自己的影子也在微微颤动，他意识到自己的影子已经开始不听话了。",
                ["自己影子颤动=下一个将被替换的是唐迟（第12章）","澡堂水池底沉着第47枚铜扣（第13章集齐）"],
                ["第六章辅导员三对鳃→辅导员尸体显鳃"],
                "澡堂热水管喷出的却是冰海水，刺骨"),
            _ch(11,6,"破钟失败","苏雅撞钟失败/队友接连殒落（原剧本：T-1钟楼顶失败）","climax",
                "入戏第6天·深夜22:00-00:30","钟楼顶层+大钟前",["唐迟","苏雅（半透明）","周凯（影子伪装）"],
                "唐迟在钟楼顶找到苏雅，她已经半透明，手里抓着钟锤。",
                "苏雅说必须在七声钟响之内把47个名字刻到钟内壁才能破局；她已经刻了46个，最后一个'唐迟'她刻不动了。",
                "周凯从楼梯口冲上来要'帮忙'，唐迟看穿他眼睛里的海，把盐撒过去——'周凯'的脸塌陷成海水，手里却多了一把刀刺伤苏雅。",
                "苏雅用最后力气把刻刀塞到唐迟手里，自己倒在钟旁变透明消失；钟声响了第一声。",
                ["钟内壁的字需要用自己的血写（第12章发现）","刺伤苏雅的刀是食堂的餐刀（呼应第七章食堂）"],
                ["第八章苏雅给的钥匙→苏雅刻字的钟锤"],
                "钟响震得胸腔发闷，像被人用手攥住心脏"),
            _ch(12,6,"七次刻名","七次迭代刻名/破局决断（原剧本：T-1唐迟决断）","climax",
                "入戏第6天·凌晨00:30-03:00","钟楼顶层+钟内壁",["唐迟","自己的影子","陈阿妹（声音）"],
                "钟声响第二声，唐迟拿起刻刀在钟内壁找自己的位置，但铜壁坚硬刻不动。",
                "他尝试了六次，刀尖折断六根，手被划破流血；血滴在铜壁上居然自动沁成了'唐迟'两个字的笔画——要血。",
                "他的影子在墙角站起来阻拦，和他扭打在一起；扭打中他听到陈阿妹在耳边说'刻上名字就是我们的人，不刻就是第七日的人'。",
                "唐迟咬破自己的左手腕把血抹到最后一笔上，'唐迟'两字刻完；钟声正好停在第六响。影子在他身后慢慢重新伏回脚下。",
                ["血写名字的代价是'出本后会被记住'（对接卷尾铜扣伏笔）","陈阿妹的话是双重误导（第13章破解）"],
                ["苏雅的钥匙在扭打中掉落露出本来面目→铜扣"],
                "刀尖断茬扎进指骨，血是热的，钟声是冷的"),
            _ch(13,7,"暴雨海床","暴雨夜/47亡魂点名/最终合唱（原剧本：T-0终极高潮）","climax",
                "入戏第7天·暴雨夜19:00-22:00","整个被淹的校园",["唐迟","47亡魂","陈阿妹","影子们"],
                "第七天入夜下起暴雨，几个小时内海水淹到三楼，整个校园变成海床。",
                "47个亡魂从水下浮起站在水面上，合唱童谣第七段；陈阿妹穿着1997年的校服飘在半空，开始最后一次点名，前46个名字都应声'到'。",
                "陈阿妹点到'唐迟'，唐迟手里紧握刻完名字的钟锤不答；他用血把铜扣按进钟壁中央的凹槽——那才是真正的'刻名'，不是签亡魂册，是把自己的名字刻进折痕本身。",
                "海水以肉眼可见的速度退去，47个亡魂一个接一个化成光点消散；陈阿妹在消散前对他笑了一下，那是宿管王姨第一次像活人一样笑。",
                ["钟壁凹槽本来就是放铜扣的位置（第14章补全）","折痕在这一刻被烫出第二个印记（过渡章用）"],
                ["第八章童谣倒放→倒放童谣反诵退潮；澡堂底第47枚铜扣→集齐"],
                "暴雨打在脸上像盐粒，海水淹到胸口又迅速退去"),
            _ch(14,7,"折痕余温","拒绝开口/折痕关闭/余韵（原剧本：T-0余韵，副本结算）","cooldown",
                "入戏第7天·深夜23:00-23:40","302宿舍+折痕前",["唐迟"],
                "海水退尽后唐迟走回302宿舍，一切摆设像他刚入戏那晚，但所有水渍都干了，留下一层薄薄的盐霜。",
                "折痕在他掌心发烫，提示副本即将关闭；他回头看了一眼空荡荡的宿舍，周凯的座位、林薇的水杯、苏雅借过的笔，全都还在，但人都不在了。",
                "折痕问他'是否愿意留下做48号'，唐迟摇头拒绝；他摸口袋想摸出手机，指尖却触到一枚不属于他的铜扣，上面刻着他自己的学号。",
                "折痕合上，唐迟回到了原世界；但那枚铜扣还在他口袋里，温热的，像还带着某个人的心跳。",
                ["铜扣上的学号暗示唐迟已经被'刻'进了下一个副本（对接过渡章）","盐霜不会洗去（下卷主线钩子）"],
                ["卷首天花板渗海水→卷尾折痕合上但海水盐霜残留；第七章周凯的铜扣→最后手里的铜扣闭环"],
                "折痕合上的瞬间掌心灼热，铜扣带着另一个人的体温"),
        ],
        "notes": "整体节奏呈「陡升-微落-炸尾」的心电图形态：前两章黄金三章拉满惊悚密度（咸水缢亡+镜中溺亡），第7章白天给主角一口喘息但用合唱队海报埋点名钩子，第9章林薇坠楼做中盘反转把强度重新顶起，11-13章连续高潮无喘息；终章 cooldown 段同样 4500 字左右，给余韵但留铜扣钩子接过渡章。注意每章 sensory_hook 都选单一强感官不重复，帮助写作者落笔时先落体感再落叙事。",
    }


def register_rhythm_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable,
    desk_for: Callable,
    chore_provider: Callable,
) -> None:
    deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        desk_for=desk_for,
        chore_provider=chore_provider,
    )
    router = APIRouter(tags=["rhythm"])
    prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/rhythm"

    # ----- POST /rhythm/analyze -----
    def _rhythm_analyze(
        pid: str,
        vol_no: int,
        body: _AnalyzeIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if not _all_scaffold_locked(vol):
            raise HTTPException(422, "请先完成副本详设：6 张卡必须全部锁定")

        density_map = _compute_density_map(vol)
        is_mock = _is_mock(vol)

        if is_mock:
            blueprint = _mock_blueprint()
        else:
            from ..generation import complete_structured
            parts: list[str] = []
            # 组装上下文
            cards = vol.scaffold_cards or {}
            def _card_text(key: str) -> str:
                entry = cards.get(key) or {}
                return entry.get("text") or ""
            parts.append("===== 已锁定副本核心设定 =====")
            parts.append(f"【核心反转】{_card_text('core_twist')}")
            parts.append(f"【唐迟起点】{_card_text('tangchi_start')}")
            parts.append(f"【代价上限】{_card_text('cost_cap')}")

            hc = vol.host_character or {}
            if hc:
                parts.append("\n===== 宿主角色 =====")
                parts.append(json.dumps(hc, ensure_ascii=False))
            stance = vol.stance or {}
            if stance:
                parts.append("\n===== 宿主姿态 =====")
                parts.append(json.dumps(stance, ensure_ascii=False))

            tso = vol.tangchi_story_outline
            if isinstance(tso, dict):
                parts.append("\n===== 唐迟七天求生大纲（入戏第1-7天 / T-6→T-0，含原剧本时间锚点）=====")
                parts.append(tso.get("outline_text") or json.dumps(tso, ensure_ascii=False)[:1500])
            elif isinstance(tso, str) and tso:
                parts.append(f"\n===== 唐迟七天求生大纲 =====\n{tso[:1500]}")

            parts.append("\n===== 已选定死亡节点 =====")
            parts.append(json.dumps(vol.death_nodes or [], ensure_ascii=False))
            parts.append("\n===== 已选定高潮节点 =====")
            parts.append(json.dumps(vol.climax_nodes or [], ensure_ascii=False))
            parts.append("\n===== 已选定线索节点 =====")
            parts.append(json.dumps(vol.clue_nodes or [], ensure_ascii=False))
            parts.append("\n===== 本地密度统计（death/climax/clue per day）=====")
            parts.append(json.dumps(density_map, ensure_ascii=False))

            rd = (body.rhythm_directive or "").strip()
            if rd:
                parts.append(f"\n===== 作者对节奏的额外要求 =====\n{rd}")

            context = "\n".join(parts)

            def _call() -> str:
                return deps.chore_provider("rhythm").chat([
                    {"role": "system", "content": _RHYTHM_SYSTEM},
                    {"role": "user", "content": context + "\n\n请严格按 system 给定 JSON schema 返回节奏蓝图（pacing_ratio/key_beats_progress/warnings/suggested_length/suggested_chapter_range/chapter_plan/notes），不要输出任何其他文字。"},
                ]).text

            try:
                comp = complete_structured(_call, max_regenerations=2)
                data = comp.data if isinstance(comp.data, dict) else {}
            except Exception as _e:
                log.warning("rhythm LLM 失败，回退 mock：%s", _e)
                data = _mock_blueprint()

            # 保底校验：关键字段缺失就回退 mock
            required_keys = {"pacing_ratio", "key_beats_progress", "warnings",
                             "suggested_length", "suggested_chapter_range",
                             "chapter_plan", "notes"}
            if not required_keys.issubset(data.keys()):
                log.warning("rhythm LLM 返回缺字段，回退 mock")
                data = _mock_blueprint()
            blueprint = data

        # 卷内相对章号 → 全局章号偏移（写入 volume 之前）
        offset = (getattr(vol, "start_chapter_no", None) or 1) - 1
        if offset and isinstance(blueprint, dict):
            plan = blueprint.get("chapter_plan")
            if isinstance(plan, list):
                for p in plan:
                    if isinstance(p, dict) and isinstance(p.get("chapter"), int):
                        p["chapter"] = p["chapter"] + offset
            # suggested_length / suggested_chapter_range 保持卷内范围，不偏移

        # 写入 volume.rhythm_blueprint
        vols.update_planning(vol_no, rhythm_blueprint=blueprint)
        return {
            "ok": True,
            "blueprint": blueprint,
            "density_map": density_map,
            "is_mock": is_mock,
        }

    # ----- POST /rhythm/finalize -----
    def _rhythm_finalize(
        pid: str,
        vol_no: int,
        body: _FinalizeIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if not vol.rhythm_blueprint:
            raise HTTPException(422, "请先推演节奏蓝图（POST /analyze）")
        sc = dict(vol.stage_completed or {})
        sc["rhythm"] = True
        vols.update_planning(vol_no, stage_completed=sc)

        # 根据 chapter_plan 批量 ensure 章节 + 预填方向卡（source=rhythm），
        # 这样用户从节奏骨架出来就能直接写第一章，不用再抽3版方向卡。
        desk = deps.desk_for(pid)
        bp = vol.rhythm_blueprint or {}
        plan = bp.get("chapter_plan") if isinstance(bp, dict) else []
        created: list[int] = []
        is_interlude = (vol.volume_kind == "interlude")
        ch_kind = "interlude" if is_interlude else vol.volume_kind
        field_no_val = vol.field_no if getattr(vol, "field_no", None) is not None else vol.volume_no
        start_no = int(getattr(vol, "start_chapter_no", 1) or 1)
        _now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        beat_label = {"hook": "黄金钩子", "sell": "卖设定", "engine": "主线推进",
                      "climax": "高潮爆发", "cooldown": "余韵收束"}
        vol_hooks = bp.get("volume_hooks") if isinstance(bp, dict) else {}
        if not isinstance(vol_hooks, dict):
            vol_hooks = {}

        def _sel_from_item(item: dict, gno: int) -> dict:
            rel_no = gno - start_no + 1
            focus = (item.get("focus") or "").strip() or f"第{rel_no}节按节奏蓝图推进"
            words = int(item.get("words") or 4500)
            beat = (item.get("beat_type") or "engine")
            title_in = (item.get("title") or "").strip()
            beats = item.get("plot_beats") if isinstance(item.get("plot_beats"), dict) else {}
            chars = item.get("characters_on_stage") or []
            if not isinstance(chars, list):
                chars = []
            seeds = item.get("foreshadow_seeds") or []
            if not isinstance(seeds, list):
                seeds = []
            payback = item.get("foreshadow_payback") or []
            if not isinstance(payback, list):
                payback = []
            try:
                day = int(item.get("day") or 1)
            except (TypeError, ValueError):
                day = 1
            sel = {
                "id": f"rhythm-{gno}",
                "title": title_in or f"第{rel_no}节·{beat_label.get(beat, beat)}",
                "summary": focus,
                "hooks": "",
                "day": day,
                "beat_type": beat,
                "foreshadow_advance": payback,
                "entities_on_stage": [str(c) for c in chars if c],
                "source": "rhythm",
                "chapter_words_target": words,
                # 六要素 + 起承转合：供章细纲直接展示
                "time_window": str(item.get("time_window") or f"入戏第{day}天"),
                "location": str(item.get("location") or ""),
                "plot_beats": {
                    "qi": str(beats.get("qi") or ""),
                    "cheng": str(beats.get("cheng") or ""),
                    "zhuan": str(beats.get("zhuan") or ""),
                    "he": str(beats.get("he") or ""),
                },
                "foreshadow_seeds": [str(s) for s in seeds if s],
                "sensory_hook": str(item.get("sensory_hook") or ""),
                "chapter_title": title_in,
            }
            # 第一章/最后一章额外带卷首/卷尾钩子
            if rel_no == 1 and vol_hooks.get("opening_hook"):
                sel["volume_opening_hook"] = str(vol_hooks["opening_hook"])
            return sel

        if isinstance(plan, list):
            total = len(plan)
            for idx, item in enumerate(plan):
                if not isinstance(item, dict):
                    continue
                try:
                    raw_no = int(item.get("chapter") or 0)
                except (TypeError, ValueError):
                    continue
                if raw_no <= 0:
                    continue
                # 兼容两种情况：analyze 阶段已经把 chapter 偏移到全局章号（>=start_no）
                # 或仍然是卷内相对号（<= 计划总章数 < start_no）。
                if raw_no >= start_no:
                    gno = raw_no
                else:
                    gno = raw_no - 1 + start_no  # 卷内相对章号 → 全书章号
                w = desk.ensure(gno)
                w.field_no = field_no_val
                w.chapter_kind = ch_kind
                w.is_interlude = is_interlude
                try:
                    w.day = int(item.get("day") or w.day or 1)
                except (TypeError, ValueError):
                    pass
                title_in = (item.get("title") or "").strip()
                if title_in and not w.title:
                    w.title = title_in
                already = w.brief if isinstance(w.brief, dict) else None
                if not (already and already.get("selected")):
                    sel = _sel_from_item(item, gno)
                    # 最后一章补卷尾钩子
                    if idx == total - 1 and vol_hooks.get("ending_hook"):
                        sel["volume_ending_hook"] = str(vol_hooks["ending_hook"])
                    w.brief = {
                        "selected": sel,
                        "selected_at": _now(),
                        "chapter_no": gno,
                        "day": sel["day"],
                        "chapter_words_target": sel["chapter_words_target"],
                        "source": "rhythm",
                    }
                created.append(gno)
            desk._flush()

        vol = vols.get(vol_no)
        return {"ok": True, "stage_completed": vol.stage_completed,
                "volume": vol.as_dict(), "chapters_created": created}

    # ----- GET /rhythm/ -----
    def _rhythm_get(pid: str, vol_no: int):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        density_map = _compute_density_map(vol)
        return {
            "volume": vol.as_dict(),
            "blueprint": vol.rhythm_blueprint,
            "density_map": density_map,
        }

    # ---- 注册路由 ----
    router.add_api_route(f"{prefix}/analyze", _rhythm_analyze, methods=["POST"])
    router.add_api_route(f"{prefix}/finalize", _rhythm_finalize, methods=["POST"])
    router.add_api_route(f"{prefix}", _rhythm_get, methods=["GET"])

    app.include_router(router)
