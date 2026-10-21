"""质量门禁 + 章后评审（mimoCode-story 适配）。

两层门禁：
1. hard_gate(text, prev_tail, target_chars) —— 零 token 机器判定：
   - 残留半角/全角引号、破折号（postprocess 后不应再有）
   - 字数是否达标（target_chars ±30%）
   - 与上一 chunk 末 200 字重复率（< 50% 视为严重重复）
   - 命中一级禁用词数、命中 40 句式黑名单数
   返回 HardGateResult{p0: [...], p1: [...], score_dict}，P0 触发重写。

2. soft_review(text, context) —— 一次非流式 LLM 调用，按 8 维打分，
   返回 SoftReviewResult{total, scores, p0, p1, fix_hints}，供前端展示；
   P0 问题触发带 fix_hints 的重写。

仅对 prose 层（正文）启用；plot/outline/rhythm 层不走评审（那是结构层，
由作者自己把控）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.generation.humanize import (
    _DASH_RE,
    _FULL_DOUBLE_RE,
    _HALF_DOUBLE_RE,
    _SAFE_REPLACES,
    _burstiness_stats,
    _rhythm_metrics,
    diagnosis_score,
)

log = logging.getLogger(__name__)


# ---------- 硬门禁 ----------

# mimoCode 40 条 AI 句式黑名单对应的（更全）正则，用于机器判分；
# 注意：这里只做"命中计数"用于评分/报警，不强制全部替换（有些句式在特定
# 上下文是自然的）。postprocess 的安全替换仍只做保守子集。
_AI_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("深吸一口气", re.compile(r"(深|缓)(吸|呼)(了)?一(口|口气|气)")),
    ("那一刻意识到", re.compile(r"那一[瞬刹]?[刻间]，?[^，。]{0,6}(意识|明白|懂)[^，。]{0,2}到")),
    ("空气凝固", re.compile(r"空气(仿佛|好像|瞬间|一下子|骤然)?(凝固|安静|死寂|沉默)(了)?")),
    ("涌上心头", re.compile(r"(一股|一阵|莫名(的|地))?\s{0,4}(凉意|寒意|恐惧|愤怒|不安|烦躁|烦躁|冲动|怒火).{0,6}(涌上|涌上|窜上|袭上)心头")),
    ("眼中闪过", re.compile(r"眼(神|中|里|眸)(一闪(而(过|逝))?|闪过|掠过)(一丝|一抹|一道|几分|一抹)")),
    ("嘴角勾起", re.compile(r"嘴角(勾起|扬起|微扬|勾起一抹|勾出一抹|挑起一抹?)(冷笑|弧度|戏谑|笑意)")),
    ("心提到嗓子眼", re.compile(r"心(一下子|猛地)?(提到|沉到)(了)?(嗓子眼|谷底|冰点)")),
    ("如遭雷击", re.compile(r"如遭雷击|大脑一片空白|整个人僵在原地")),
    ("声音响起", re.compile(r"(一道|一个|清冷|低沉|冰冷|熟悉|陌生)(的)?(声音|嗓音).{0,6}(响起|传来|飘来|落下)")),
    ("倒吸凉气", re.compile(r"倒吸(一口|了一口)凉气|倒吸一口冷气")),
    ("全场寂静", re.compile(r"全场(一片|死一般)?(寂静|沉寂|鸦雀无声|针落可闻|默然)")),
    ("瞳孔收缩", re.compile(r"瞳孔(骤缩|猛缩|一缩|收缩)")),
    ("眉头紧锁", re.compile(r"眉头(紧锁|微蹙|紧蹙|一蹙|皱起)")),
    ("如坠冰窟", re.compile(r"如坠冰窟|如芒在背|如临大敌|如释重负")),
    ("指节发白", re.compile(r"指节(发白|泛白)|指尖泛白|拳头攥得(咯咯|咔咔)作响")),
    ("夜色月光套话", re.compile(r"夜色如墨|月光如水|骄阳似火|寒风刺骨|夕阳西下，.*如血")),
    ("不知道过了多久", re.compile(r"(也)?不知道过了多久|不知过了多久|时间仿佛(在这一刻)?(静止|凝固|停滞)")),
    ("面无表情", re.compile(r"(面无表情|脸上没有一丝表情|毫无表情)(地|的)(说|道|开口)")),
    ("鸦雀无声", re.compile(r"鸦雀无声|针落可闻|落针可闻")),
    ("映入眼帘", re.compile(r"映入眼帘|目光所及(之处)?|扑入眼帘")),
    ("威压袭来", re.compile(r"(一股|一股强大|恐怖的)(威压|气势|气场|气息).{0,6}(扑面|席卷|袭来|压迫)")),
    ("脸色煞白", re.compile(r"脸色(煞白|铁青|瞬间变得难看|一下子沉下来)")),
    ("一字一句", re.compile(r"一字一句(地|的)(说|道|开口)|从牙缝里挤出")),
    ("与此同时", re.compile(r"与此同时|就在这时|就在这一刻|话音刚落|话音未落")),
    ("没有人知道", re.compile(r"没有人知道|谁也没(有)?(想|料)到|所有人都没(有)?想到")),
    ("只见/却见", re.compile(r"(^|\n)\s*(只见|却见|但见)")),
    ("莫名熟悉感", re.compile(r"莫名(的|地)?熟悉感|似曾相识(的感觉)?")),
    ("大步流星", re.compile(r"大步流星|健步如飞|落荒而逃|逃也似的(离开|跑开|走了|离去)")),
    ("嘴角上扬", re.compile(r"嘴角(微微|不自觉|下意识)?(上扬|勾起|弯起)")),
    ("气氛尴尬", re.compile(r"气氛(一时)?(有些|瞬间)?(尴尬|凝固|降到冰点|紧张)")),
    # fiction-humanizer-zh 章末空预告（P0 钩子偷懒）
    ("空章末钩子", re.compile(r"(他|她|他们|众人)(还)?不知道的是|真正的危机才刚刚开始|命运的齿轮(开始转动)?|一(场|个)?更大的风暴(即将|就要|马上)到来|故事(才|这才|才刚)刚刚开始")),
    # lieflat 译文句式（实证区分度 R≥2）
    ("然而句首", re.compile(r"(^|\n)\s*然而，")),
    ("对举结构", re.compile(r"不是[^，。\n]{2,10}而是[^，。\n]{2,20}|与其说[^，。\n]{2,10}不如说[^，。\n]{2,20}|看似[^，。\n]{2,10}实则[^，。\n]{2,20}")),
    ("这意味着", re.compile(r"这意味着[^。\n]{0,30}。")),
    ("对于来说", re.compile(r"对于[^，。\n]{1,10}来说[，。]")),
    ("段首零回指评论", re.compile(r"(^|\n)\s*(这太荒唐了|太荒唐了|真正可怕的还在后面|事情没那么简单|噩梦才刚刚开始|这才是开始|真正的恐惧|问题才刚刚浮现)[。！？]?")),
]


@dataclass
class HardGateResult:
    p0: list[str] = field(default_factory=list)   # 必须重写的硬问题
    p1: list[str] = field(default_factory=list)   # 建议修的问题
    stats: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.p0


def hard_gate(
    text: str,
    prev_tail: str = "",
    target_chars: int = 0,
    min_ratio: float = 0.6,
    max_ratio: float = 1.8,
) -> HardGateResult:
    """零 token 硬门禁。"""
    p0: list[str] = []
    p1: list[str] = []
    stats: dict = {}
    if not text or not text.strip():
        p0.append("正文为空")
        return HardGateResult(p0=p0, p1=p1, stats=stats)

    chars = len(text.strip())
    stats["chars"] = chars
    if target_chars > 0:
        lo = int(target_chars * min_ratio)
        hi = int(target_chars * max_ratio)
        if chars < lo:
            p0.append(f"字数不足：{chars} / 目标 {target_chars}（下限 {lo}）")
        elif chars > hi:
            p1.append(f"字数偏长：{chars} / 目标 {target_chars}（上限 {hi}）")

    # postprocess 后不应再残留的标点
    half_dq = len(_HALF_DOUBLE_RE.findall(text))
    full_dq = len(_FULL_DOUBLE_RE.findall(text))
    dashes = len(_DASH_RE.findall(text))
    stats["half_double_quotes"] = half_dq
    stats["full_double_quotes"] = full_dq
    stats["dashes"] = dashes
    if half_dq + full_dq > 0:
        p1.append(f"残留半/全角双引号 {half_dq + full_dq} 处（应用「」）")
    if dashes > 0:
        # 破折号按用途管，不按密度管（跨模型差异 40 倍）——用途错的已被 postprocess 换成逗号/句号。
        # 这里只做统计提示，不卡 P0/P1。
        per_k_d = dashes * 1000 / max(chars, 1)
        stats["dashes_per_k"] = round(per_k_d, 2)

    # AI 句式命中数
    ai_hits: dict[str, int] = {}
    total_ai = 0
    for name, pat in _AI_PATTERNS:
        n = len(pat.findall(text))
        if n:
            ai_hits[name] = n
            total_ai += n
    stats["ai_pattern_hits"] = total_ai
    stats["ai_pattern_detail"] = ai_hits
    per_k = total_ai * 1000 / max(chars, 1)
    if per_k > 5:
        p0.append(f"AI 模板句式密度过高：{total_ai} 处（每千字 {per_k:.1f}）")
    elif per_k > 2:
        p1.append(f"AI 模板句式较多：{total_ai} 处（每千字 {per_k:.1f}）")

    # 禁用词命中（来自 humanize._SAFE_REPLACES 的模式；postprocess 已替换一批，
    # 残留的属于更顽固的 AI 痕迹）
    banned_hits = 0
    for pat, _ in _SAFE_REPLACES:
        banned_hits += len(pat.findall(text))
    stats["banned_word_hits"] = banned_hits
    per_k_b = banned_hits * 1000 / max(chars, 1)
    if per_k_b > 3:
        p0.append(f"一级禁用词残留 {banned_hits} 处（每千字 {per_k_b:.1f}）")
    elif per_k_b > 1:
        p1.append(f"一级禁用词残留 {banned_hits} 处（每千字 {per_k_b:.1f}）")

    # 感叹号密度（千字 ≤ 8）
    exclaim = len(re.findall(r"[！!]", text))
    stats["exclaim_marks"] = exclaim
    per_k_e = exclaim * 1000 / max(chars, 1)
    if per_k_e > 12:
        p1.append(f"感叹号过密：{exclaim} 个（每千字 {per_k_e:.1f}）")

    # 连续重复段落（前 prev_tail 末尾 200 字与新开头 200 字相似度）
    if prev_tail and len(prev_tail) > 60 and len(text) > 60:
        overlap = SequenceMatcher(
            None, prev_tail[-200:], text[:200]
        ).ratio()
        stats["prev_overlap_ratio"] = round(overlap, 3)
        if overlap > 0.6:
            p0.append(f"与上一 chunk 开头高度重复（相似度 {overlap:.0%}）")
        elif overlap > 0.4:
            p1.append(f"与上一 chunk 开头相似度较高（{overlap:.0%}，注意去重）")

    # 长段落无对白（整段 > 300 字无「」，可能是信息倾倒 P1）
    long_para_no_dialog = 0
    for para in re.split(r"\n+", text):
        if len(para) > 300 and "「" not in para and "」" not in para:
            long_para_no_dialog += 1
    stats["long_para_no_dialog"] = long_para_no_dialog
    if long_para_no_dialog >= 2:
        p1.append(f"存在 {long_para_no_dialog} 段超过 300 字无对白的长段（可能信息倾倒）")

    # burstiness/材料密度指标（基于 lieflat 283 万字人机对照实证）
    burst = _burstiness_stats(text)
    stats["burstiness"] = burst
    q_d = burst["question_density"]
    cv = burst["sentence_cv"]
    ct = burst["char_type_density"]
    cm = burst["comma_density"]
    num_d = burst["number_density"]
    t_d = burst["time_anchor_density"]
    za = burst["zero_anaphora_hits"]
    ps = burst["personified_simile"]
    # 问号密度 < 2/千字 P0（R=0.05，人机差 17 倍，区分力最强），< 5 P1
    if q_d < 2.0:
        p0.append(f"问号密度过低：{q_d:.1f}/千字（< 2，人类稿是 AI 的 17 倍，请加入内心反问/对白问句/不确定自问）")
    elif q_d < 5.0:
        p1.append(f"问号偏少：{q_d:.1f}/千字（建议 5-8，加入内心反问/对白问句）")
    elif q_d > 15.0:
        p1.append(f"问号过密：{q_d:.1f}/千字（建议 5-8，过多像审讯）")
    # 句长 CV —— 撤回 P0（lieflat 实证 R=0.87 人机差异不显著），< 0.2 才给 P1 提示
    if cv < 0.2:
        p1.append(f"句长极度均匀：CV={cv:.2f}（< 0.2，建议在情绪转折点砍 2-5 字极短句打断，但不要为变速而变速）")
    elif cv < 0.4:
        pass  # 不提示，避免误伤（0.4-0.7 都在人机重叠区）
    # 字种密度 < 0.25 P0（套话堆砌），< 0.35 P1
    if ct < 0.25:
        p0.append(f"字种密度过低：{ct:.2f}（< 0.25，套话/高频重复词堆砌，请用具体名词动词替换）")
    elif ct < 0.35:
        p1.append(f"字种偏低：{ct:.2f}（建议 > 0.35，增加具体名词/动词，减少套话）")
    # 逗号密度
    if cm < 25:
        p1.append(f"逗号偏少：{cm:.1f}/千字（可能短句过少或无停顿）")
    elif cm > 100:
        p1.append(f"逗号过密：{cm:.1f}/千字（可能长句连写，请适当断句）")
    # 数字密度 < 2/千字 P1（人类 17.9 vs AI 6.3，R=2.8×）
    if num_d < 2.0:
        p1.append(f"具体数字偏少：{num_d:.1f}/千字（建议 ≥ 2，加入房号/时间/价格/年龄/楼层/次数等具体数字，避免「很久/不少」等模糊词）")
    # 时间锚点密度 < 1/千字 P1（人类是 AI 的 2.6 倍）
    if t_d < 1.0:
        p1.append(f"时间锚点偏少：{t_d:.1f}/千字（建议 ≥ 1，加入三点钟/第二天/上周/分钟/秒等具体时间词）")
    # 拟人化褒义喻体（像一位智慧的导师）命中即 P1（R=12.6× AI 重灾区）
    if ps > 0:
        p1.append(f"拟人化褒义喻体 {ps} 处（AI 高频特征，改写为具体动作/感官描写）")
    # 段首零回指命中（postprocess 已自动修复；残留 > 2 处提示）
    if za > 2:
        p1.append(f"段首零回指评论 {za} 处（已自动补'但'字衔接，建议下次写时直接带回指）")

    # 块间接缝（与 prev_tail 衔接）：首句用空泛过渡词强行衔接 = 接缝断裂信号
    if prev_tail:
        first_sent = re.split(r"[。！？!?\n]", text.lstrip(), maxsplit=1)[0].strip()
        _SEAM_BAD_STARTS = re.compile(
            r"^(与此同时|话音刚落|话音未落|就在这时|就在这一刻|霎时间|刹那间|下一秒|紧接着|随后)"
        )
        if _SEAM_BAD_STARTS.match(first_sent):
            p1.append(f"块间接缝：本段以空泛过渡词「{first_sent[:10]}…」开头，建议改为动作/物件/时间锚点承接上一段")

    # 活人节奏红线：润色层最容易磨掉这些（合并段落、砍掉极短段）
    _rhythm = _rhythm_metrics(text)
    stats["rhythm"] = _rhythm
    if _rhythm["段落数"] >= 8:
        if _rhythm["极短段占比"] < 20:
            p1.append(
                f"活人节奏：极短段（≤12字）仅 {_rhythm['极短段占比']}%（{_rhythm['极短段(≤12字)']} 段），"
                f"建议 ≥20%——在情绪转折点砍出 2-5 字极短句独立成段；"
                f"**如果原文有更多极短段而这里变少，说明润色把它们合并了，必须还原**"
            )
        if _rhythm["动作收尾占比"] < 8:
            p1.append(
                f"活人节奏：动作收尾段仅 {_rhythm['动作收尾占比']}%（建议 ≥10%）"
                f"——段尾爱落在抽象总结，改为落在具体动作/物件/短句"
            )
        if _rhythm["段落均长"] > 60:
            p1.append(
                f"活人节奏：段落均长 {_rhythm['段落均长']} 字（>60 偏长），适当把长段拆出短段"
            )

    return HardGateResult(p0=p0, p1=p1, stats=stats)


# ---------- 软评审（LLM 8 维打分）----------

SOFT_REVIEW_SYSTEM = """你是中文克苏鲁无限流小说的资深编审，只做评分与问题诊断，不代写、不润色。
评审对象：作者刚写完的一章正文。你将收到：章节大纲（plot）、前章结尾摘要（prev_tail）、本章正文（text）。
请按下述 13 个维度为正文打分（每项 0-10 整数，10 为完美）：
1. foreshadowing 伏笔：是否埋下具体的钩子/异常/悬念，不是空泛预告
2. consistency 一致性：与大纲/前文/人设是否一致，有没有 OOC 或自相矛盾
3. character_voice 角色声口：对白/反应是否符合角色身份，主角是否有辨识度
4. cthulhu_vibe 克味：未知感/日常崩坏感/不可名状感是否到位（不滥用鬼形容词）
5. pacing 节奏：长短句/段落/张弛是否得当，情绪高点有铺垫与喘息
6. imagery 画面感：是否有具体五感细节（声音/触感/气味/温度），不是抽象描述
7. dialogue 对白：是否口语化、有潜台词、能推剧情/做事，不做设定解说
8. brevity 简洁度：是否没有废话/重复/解释性旁白，信息密度合格
9. scene_arc 场景完整：是否走完铺垫→过程→余波，没有梗概感；人物是否有目标-阻碍-选择-代价-后果
10. agency 人物能动性：关键选择是否有代价；人物不是被剧情推着走，而是做了推动剧情的决定
11. hook_quality 钩子质量：章末/段末钩子是具体事件/发现/动作，不是空泛预告（"真正的危机才刚开始""他不知道的是"为 0 分）
12. pov_discipline 视角纪律：细节是否跟着视角走，没有上帝视角越界；设定是否等到人物碰到才讲
13. sentence_variance 句长节律【负向扣分项】：
   - 10 分：长短句错落明显，情绪转折点有 2-5 字极短句打断，有反问/自问，有半截话/沉默/口癖停顿，有具体数字/时间锚点，段首评论有回指（"但奇怪的是"而非悬空"奇怪的是"），读起来像人说话喘气
   - 7-8 分：整体有节奏，偶有匀速段但不致命
   - 4-6 分：缺问号/缺极短句/缺具体锚点，段首有悬空评论，读起来像在匀速写作文
   - 0-3 分（P0）：全程匀速且缺问号/缺数字锚点/段首大量零回指评论/无打断无半截话；或长句一逗到底；AI 流水账感明显
   评分要点：这一项判的是"节律像不像人"，不是"文笔好不好"。注意：单纯的句长均匀不是 AI 强特征（人机差异只有 13%）；真正的强信号是"问号少+材料稀薄（无具体数字/时间）+段首零回指+无打断无半截话"的组合。请按综合信号打分。

同时检测下列问题：
- p0_fatal（必须重写的致命问题，字符串数组）：
  a. 降智打击：主角/关键角色做出不符合人设的愚蠢决定以推动剧情
  b. 情绪疲劳：连续 3 段以上高强度恐惧/压迫，无喘息
  c. 重复循环：同一种"看到异象→怀疑自己→否定→再看到"节奏出现 2 次以上
  d. 空章末钩子：章末用"他不知道的是/更大的风暴/命运的齿轮/故事才刚刚开始"等空预告收尾
  e. 信息倾倒：>300 字纯旁白解释设定/世界观，无动作或对白打断
  f. AI 节律：sentence_variance ≤ 3，全程匀速流水账，缺问号/缺具体锚点/段首零回指/无打断
  g. 块间接缝断裂（本章为分段生成）：本段开头与上一段结尾节奏/语气/视角突变，或开头用空泛过渡句强行衔接（"与此同时""话音刚落"），或与上一段末句的动作/情绪不连贯
- p1_warnings（建议修改但不致命的问题，字符串数组）：
  a. 万能反应：同一情绪多次用同一生理反应（心跳加速/后背发凉/瞳孔收缩）
  b. 对白标签单调：连 5 句以上"XX 说"无穿插
  c. 对白汇报：人物互相说双方已知信息只为给读者解释
  d. 译文句式：句首"然而"/"不是A而是B"/"这意味着"/"对于…来说"出现 ≥2 次
  e. 句长偏匀：sentence_variance ≤ 6，部分段落缺少短句变速或反问
  f. 其他具体可改进点
- fix_hints（给重写模型的具体修复建议，字符串数组；只写"怎么改"，不要复述问题；
  必须针对 sentence_variance 给出可操作建议：哪里插入极短句/哪里加反问/哪里用半截话打断/哪里把长句砍开）

只输出一个 JSON 对象，字段为：
{"scores":{foreshadowing,consistency,character_voice,cthulhu_vibe,pacing,imagery,dialogue,brevity,scene_arc,agency,hook_quality,pov_discipline,sentence_variance},
 "total":0-100整数（13项均值×10，四舍五入）,
 "p0_fatal":[...], "p1_warnings":[...], "fix_hints":[...]}
不要输出 JSON 以外的任何字符、不要使用 ```json 标记。"""


@dataclass
class SoftReviewResult:
    total: int = 0
    scores: dict[str, int] = field(default_factory=dict)
    p0: list[str] = field(default_factory=list)
    p1: list[str] = field(default_factory=list)
    fix_hints: list[str] = field(default_factory=list)
    raw: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.total >= 80 and not self.p0


def build_soft_review_messages(
    plot_brief: str,
    prev_tail: str,
    text: str,
    chapter_no: int | None = None,
    chapter_title: str = "",
) -> list[dict]:
    """构造软评审用的 messages（非流式 chat）。"""
    user_block = []
    if chapter_no:
        user_block.append(f"章节：第 {chapter_no} 章《{chapter_title}》")
    user_block.append("【本章剧情大纲】")
    user_block.append(plot_brief or "(无)")
    user_block.append("【前章结尾摘要（用于判断衔接）】")
    user_block.append((prev_tail or "(无)")[-600:])
    user_block.append("【本章正文】")
    user_block.append(text)
    return [
        {"role": "system", "content": SOFT_REVIEW_SYSTEM},
        {"role": "user", "content": "\n\n".join(user_block)},
    ]


def parse_soft_review(raw: str) -> SoftReviewResult:
    """解析 LLM 返回的 JSON 文本为 SoftReviewResult。"""
    res = SoftReviewResult(raw=raw)
    # 简单容错：截取首个 { 到最后一个 }
    s = raw.find("{")
    e = raw.rfind("}")
    if s == -1 or e == -1 or e <= s:
        res.error = f"返回无 JSON 结构：{raw[:120]}"
        return res
    try:
        data = json.loads(raw[s:e + 1])
    except json.JSONDecodeError as exc:
        res.error = f"JSON 解析失败：{exc}"
        return res
    scores = data.get("scores") or {}
    # 规范化 scores 键名
    key_map = {
        "foreshadowing": "伏笔", "consistency": "一致性",
        "character_voice": "角色声口", "cthulhu_vibe": "克味",
        "pacing": "节奏", "imagery": "画面感",
        "dialogue": "对白", "brevity": "简洁度",
        "scene_arc": "场景完整", "agency": "人物能动性",
        "hook_quality": "钩子质量", "pov_discipline": "视角纪律",
        "sentence_variance": "句长节律",
    }
    for en, zh in key_map.items():
        v = scores.get(en)
        try:
            res.scores[zh] = max(0, min(10, int(v)))
        except (TypeError, ValueError):
            res.scores[zh] = 0
    total = data.get("total")
    try:
        res.total = max(0, min(100, int(total)))
    except (TypeError, ValueError):
        res.total = int(sum(res.scores.values()) * 10 / 13) if res.scores else 0
    res.p0 = [str(x) for x in (data.get("p0_fatal") or []) if str(x).strip()]
    res.p1 = [str(x) for x in (data.get("p1_warnings") or []) if str(x).strip()]
    res.fix_hints = [str(x) for x in (data.get("fix_hints") or []) if str(x).strip()]
    return res


def rewrite_prompt_with_hints(original_prompt: str, review: SoftReviewResult) -> str:
    """把评审 P0 + fix_hints 拼成续写/重写的补充指令，追加到 user_directive。"""
    if not review.p0 and not review.fix_hints:
        return original_prompt
    parts = ["【上次生成未通过质量门禁，请按下列反馈重写】"]
    if review.p0:
        parts.append("致命问题（必须修正）：")
        parts.extend(f"- {x}" for x in review.p0)
    if review.fix_hints:
        parts.append("具体修改方向：")
        parts.extend(f"- {x}" for x in review.fix_hints[:6])
    parts.append(f"当前总分 {review.total}/100，目标 ≥ 80。请严格保留剧情骨架，只修正以上问题。")
    return original_prompt + "\n\n" + "\n".join(parts)
