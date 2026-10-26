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

from app.generation.archive import archive_density
from app.generation.humanize import (
    _DASH_RE,
    _FULL_DOUBLE_RE,
    _HALF_DOUBLE_RE,
    _SAFE_REPLACES,
    _burstiness_stats,
    _detect_opening_template,
    _detect_vague_transitions,
    _detect_idle_time_anchor,
    _lieflat_ai_signals,
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
    stage: str = "",
) -> HardGateResult:
    """零 token 硬门禁。stage='plot' 时额外校验章细纲格式。"""
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

    # 章细纲格式门禁（仅 plot 层）：必须是编号场景块，不能是散文。
    # 同时：plot 是结构化编号块，短句/低问号/低逗号是格式特性而非质量问题，
    # 所以正文专属的节奏/密度/burstiness 检查对 plot 层全部跳过。
    is_outline = (stage == "plot")
    if is_outline:
        import re as _re
        # 场标记要兼容三种写法：「第1场：」「1. xxx」「场1」——
        # 只认「第N场」会把「场1：客厅」这种合法编号判成散文。
        # 必须锚到行首（可带 #/列表符）：否则场内任何一处「第N场」（比如落点里写
        # 「收在第3场之后」）都会被计成一场，场数虚高后 fields < scenes*4 会误报「字段不全」。
        _scenes = _re.findall(
            r"^\s*(?:[-·*>]\s*|#{1,4}\s*)?(?:第\s*\d+\s*场|场景\s*\d+|场\s*\d+)", text, _re.M)
        _blocks = _re.findall(r"^\s*[-·*]?\s*(?:场景|微观目标|阻力|信息差|关键对白|关键动作|感官锚点|情绪四拍|落点|POV|转场桥|本场变化)\s*[：:]", text, _re.M)
        # 连续场（同一时空的下一拍）标记为「同场续」，它们不需要五类转场桥
        _cont = _re.findall(r"^\s*[-·*]?\s*转场桥\s*[：:]\s*同场续", text, _re.M)
        _delta = _re.findall(r"^\s*[-·*]?\s*本场变化\s*[：:]", text, _re.M)
        stats["outline_format"] = {
            "scenes": len(_scenes), "fields": len(_blocks),
            "同场续": len(_cont), "本场变化": len(_delta),
        }
        # 场数下限 2：连续场合并后总块数会自然下降，短章写 2 场是正常的。
        # 上限不设门禁——超长由字数门禁（2600）兜住，硬卡场数只会逼模型凑数。
        if len(_scenes) < 2:
            p0.append(
                f"章细纲格式错误：只识别到 {len(_scenes)} 个场景标记（至少 2 个）。"
                f"章细纲必须是编号场景块（每场含 场景/微观目标/阻力/关键对白/关键动作/落点 等字段），"
                f"**不能写成连续散文或正文**——你输出的是细纲，不是正文。"
                f"格式：第1场：<场名>\n- 场景：…\n- 微观目标：…\n- 阻力：…\n- 关键对白：「…」\n- 关键动作：A→B→C\n- 落点：…"
            )
        elif len(_blocks) < len(_scenes) * 4:
            # 字段太少 = 没写成施工图，P0 而非 P1：这种稿子正文层没法直接用
            p0.append(
                f"章细纲字段不全：{len(_scenes)} 个场景只有 {len(_blocks)} 个字段"
                f"（每场至少 4 个，建议 6 个：场景/微观目标/阻力/信息差/关键对白/关键动作/感官锚点/情绪四拍/落点）"
            )
        # 标了「同场续」就必须写「本场变化」——否则正文层无从判断这一场推进了什么
        if len(_cont) > len(_delta):
            p0.append(
                f"章细纲连续场缺「本场变化」：{len(_cont)} 场标了「同场续」但只有 {len(_delta)} 个「本场变化」。"
                f"连续场必须写清相比上一场推进了什么（视线移动/手上动作换一步/对方换一句话），"
                f"否则正文层只能把同一件事写两遍"
            )
        # 细纲只做格式 + 字数校验，正文质量门禁不适用
        if chars > 2600:
            # 过长是「写成正文」最强信号，判 P0 逼重写（原来判 P1 等于放过）
            p0.append(
                f"章细纲过长：{chars} 字（上限 2600）——"
                f"**这几乎可以确定你在写正文而不是细纲**。细纲是编号块，每场 6-10 行。"
                f"内容写不下说明该拆成更多场，而不是写得更长"
            )
        elif chars < 900 and len(_scenes) >= 2:
            p1.append(f"章细纲过短：{chars} 字（建议 1200-1900，字段写不全正文就没东西写）")

    # ---- 以下全部是正文层专属检查，plot（章细纲）不适用 ----
    if not is_outline:
        # 章首入场模板池（xt A 条款）：连续同模板 = AI 味（仅正文层）
        tmpl_name, tmpl_ev = _detect_opening_template(text)
        if tmpl_name:
            stats["opening_template"] = {"name": tmpl_name, "evidence": tmpl_ev}
            if tmpl_name == "醒来开头":
                p1.append(
                    f"章首入场模板：识别为「醒来开头」（{tmpl_ev[:20]}…）。"
                    f"这是最典型的 AI 开场——近 10 章「醒来开头」≥4 即 FAIL。"
                    f"换一个入场方式：从动作中途开始、从对话开始、从某个具体物件开始，"
                    f"或直接跳到最有戏的那一刻"
                )
            else:
                p1.append(
                    f"章首入场模板：识别为「{tmpl_name}」（{tmpl_ev[:20]}…）。"
                    f"注意不要连续多章用同一种入场模板"
                )

        # 空泛过渡词零容忍（xt Q 条款）：模型不搭桥就拿这类词糊过去
        _vague = _detect_vague_transitions(text)
        if _vague:
            stats["vague_transitions"] = _vague
            _vtotal = sum(_vague.values())
            _vdesc = "、".join(f"「{w}」×{n}" for w, n in
                              sorted(_vague.items(), key=lambda x: -x[1])[:6])
            if _vtotal >= 3:
                p0.append(
                    f"转场回滚级：空泛过渡词 {_vtotal} 处（{_vdesc}）。"
                    f"**这类词是 AI 转场味的核心指纹**——用它们等于没搭桥。"
                    f"必须改用五类转场桥：Q1 感官延续 / Q2 物件带过去（用途不同）/ "
                    f"Q3 对话打断 / Q4 摩擦点（小意外/延迟/错位）/ Q5 情绪错位。"
                    f"或用「直接标时 + 立刻给后果」的写法"
                )
            elif _vtotal >= 1:
                p1.append(
                    f"转场：空泛过渡词 {_vtotal} 处（{_vdesc}）——"
                    f"换成具体的时间锚 + 后果，或用感官/物件桥接上"
                )
        _idle_anchor = _detect_idle_time_anchor(text)
        if _idle_anchor:
            stats["idle_time_anchor"] = _idle_anchor[:5]
            _idesc = "、".join(f"「{a['anchor']}」后接{a['tail'][:10]}"
                              for a in _idle_anchor[:3])
            p1.append(
                f"转场空转：时间锚后没有内容（{_idesc}）——"
                f"标时之后要立刻给具体后果（「第二天我睡到天光大亮。/ 我打开手机，来电有十七通」），"
                f"不要只标时间不写事情"
            )

        # lieflat 11 项 AI 痕迹检测（283 万字人机对照实证，R = 生成 ÷ 人类）
        lieflat = _lieflat_ai_signals(text)
        if lieflat:
            stats["lieflat"] = lieflat
            per_k_lf = {k: round(v * 1000 / max(chars, 1), 2) for k, v in lieflat.items()}
            stats["lieflat_per_k"] = per_k_lf
            # 回滚级：R≥7 的强区分项
            for k, v in lieflat.items():
                if k == "冒号-空转句引列表" and v >= 2:
                    p0.append(f"AI 痕迹回滚级·冒号空转句引列表 {v} 处（R=9.4）：「我见过的几种典型场景：」"
                              f"这种总起句是 AI 强指纹——直接给内容，不要预告")
                elif k == "拟人化褒义喻体" and v >= 1:
                    p1.append(f"AI 痕迹·拟人化褒义喻体 {v} 处（R=7.3，带褒义 12.6）："
                              f"「像一位智慧的导师」这类，改写为具体动作或感官")
                elif k == "对举结构(不是A而是B)" and v >= 3:
                    p1.append(f"AI 痕迹·对举结构「不是A而是B」{v} 处（R=3.4，上限 2）——"
                              f"这是命中率最高的 AI 指纹之一")
                elif k == "冒号-提示语引出内容" and v >= 2:
                    p1.append(f"AI 痕迹·冒号提示语 {v} 处（R=3.8）：「他解释道：」"
                              f"用提示语代替了动作锚定，改成 ≤7 字身体动作")
                elif k == "起首语(说白了)" and v >= 2:
                    p1.append(f"AI 痕迹·起首语 {v} 处（R=3.2）：「说白了」「也就是说」——直接说，不用预告")
                elif k == "序数词当小标题" and v >= 2:
                    p1.append(f"AI 痕迹·序数词当小标题 {v} 处（R=3.1）：正文里出现「第一」「首先」当段落序号")
                elif k == "相邻句结构同构" and v >= 4:
                    p1.append(f"AI 痕迹·相邻句结构同构 {v} 处（R=2.0）：连续两句逗号数相同且长度接近——"
                              f"打乱句法骨架，别让节奏变成等距")
                elif k.startswith("译文-") and v >= 2:
                    p1.append(f"AI 痕迹·{k} {v} 处（R=2.6-5.3）：译文腔，改成中文自然语序")

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

        # 机械冗余（圆桌：唯一可以自动改的情况——同段内同词重复，笔误级，无需审美判断）
        from app.generation.lexicon import mechanical_redundancy
        _redundant = mechanical_redundancy(text)
        if _redundant:
            stats["mechanical_redundancy"] = _redundant
            _red_desc = "、".join(
                f"「{r['term']}」第{r['para'] + 1}段出现{r['count']}次"
                for r in _redundant[:6])
            p1.append(f"机械冗余：同一段内重复用词 {_red_desc}——同一段里同一个表达出现两次以上属于笔误级冗余，请改掉其中一处")


        # 节奏门禁：段长阶梯 + 单句段瀑布
        # 依据 xt-webnovel-writing K/C 条款：句长均匀度人机无差异（R=0.87），
        # 「拆短句制造节奏」是错的去味手段；真正的匀速机感来自「全章没有长段」。
        _rhythm = _rhythm_metrics(text)
        stats["rhythm"] = _rhythm
        if _rhythm["段落数"] >= 8:
            if _rhythm["长段数(>80字)"] < 3:
                p0.append(
                    f"节奏回滚级：全章只有 {_rhythm['长段数(>80字)']} 个 >80 字的长段（要求 ≥3，其中至少 1 段 >120 字）。"
                    f"全是中短段拼起来的匀速机感——请补写承担描写/心理/环境的长段，"
                    f"不要靠拆短句制造节奏（句长均匀度人机无差异，拆短句无效）"
                )
            elif _rhythm["超长段数(>120字)"] == 0:
                p1.append(
                    f"节奏：{_rhythm['长段数(>80字)']} 个长段但没有 >120 字的（建议至少 1 段），"
                    f"长段要真正承担描写/心理/环境，不是拉长句子"
                )
            if _rhythm["最长连续单句段"] >= 6:
                p0.append(
                    f"节奏回滚级：连续 {_rhythm['最长连续单句段']} 段单句成段（上限 3，≥6 是典型 AI 瀑布体）。"
                    f"把其中至少一半并入相邻段——单句成段必须有显式理由（情绪顶点/打断/转场锚点/对话重击）"
                )
            elif _rhythm["单句成段占比"] > 50:
                p1.append(
                    f"节奏：单句成段占比 {_rhythm['单句成段占比']}%（上限 30%），全章像流水账"
                )
            # 气息长度（2026-10-26 定稿反推新增）
            #
            # 人工定稿的段是「一口气」——一段里连着走 3-6 个动作，用逗号接。
            # 实测第一组：机器稿段内逗号 1.08/段，定稿 2.06/段。
            # 全章都在 1.2 以下 = 全是「一句一段」的摄影机切法。
            if _rhythm["段内逗号均数"] < 1.2 and _rhythm["段落数"] >= 12:
                p1.append(
                    f"气息：段内逗号仅 {_rhythm['段内逗号均数']} 个/段"
                    f"（人工定稿实测 1.5-2.1）——每段基本只有一句，是摄影机切法。"
                    f"把相邻的动作/感官用逗号接进同一段，让一段是一口气说完的。"
                )
            if _rhythm["动作收尾占比"] < 8:
                p1.append(
                    f"活人节奏：动作收尾段仅 {_rhythm['动作收尾占比']}%（建议 ≥10%）"
                    f"——段尾爱落在抽象总结，改为落在具体动作/物件/短句"
                )
            # 档案密度（2026-10-26 定稿反推新增）
            #
            # 数字要挂在人/时/物上：定稿第三章的硬挂靠从 7 处涨到 13 处，
            # 差别在「邵广勤今早猝死」→「四十七岁，住在二楼最里面那间，
            # 今早七点十分，脸朝下扣在积水里」——**给现场，不给摘要**。
            _arch = archive_density(text)
            stats["archive_density"] = _arch
            if _arch["per_k"] < 2.0:
                p1.append(
                    f"档案密度 {_arch['per_k']} 个/千字（建议 ≥2）"
                    f"——数字还停在装饰层面。**恐怖信息要给现场，不要给摘要**："
                    f"「脸朝下扣在积水里」比「猝死，死因没查清」有画面得多。"
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
        if v is None:
            # 模型有时直接用中文键名返回（「伏笔」「节奏」…）。
            # 旧代码只看英文键，取不到就记 0 分——
            # 于是 13 项全 0 → total=0 → 报「0 分」，
            # 看起来像正文极差，实际是键名没对上。
            v = scores.get(zh)
        try:
            res.scores[zh] = max(0, min(10, int(v)))
        except (TypeError, ValueError):
            res.scores[zh] = 0
    total = data.get("total")
    try:
        res.total = max(0, min(100, int(total)))
    except (TypeError, ValueError):
        res.total = int(sum(res.scores.values()) * 10 / 13) if res.scores else 0
    # total=0 但 13 项分数之和明显 > 0 → 说明模型没给 total，
    # 用分数均值补一个，别让作者看到「0 分」这种假信号。
    if res.total == 0 and sum(res.scores.values()) > 0:
        res.total = int(sum(res.scores.values()) * 10 / 13)
    res.p0 = [str(x) for x in (data.get("p0_fatal") or []) if str(x).strip()]
    res.p1 = [str(x) for x in (data.get("p1_warnings") or []) if str(x).strip()]
    res.fix_hints = [str(x) for x in (data.get("fix_hints") or []) if str(x).strip()]
    return res


def rewrite_prompt_with_hints(original_prompt: str, review: SoftReviewResult) -> str:
    """把评审 P0 + P1 + fix_hints 拼成续写/重写的补充指令，追加到 user_directive。

    ## 为什么 P1 也要带上（2026-10-24）

    旧代码只传 P0 和 fix_hints，P1 被完全丢掉。
    结果是：评审报了「8 条建议」，重写时模型一条都没收到，
    改完还是同样 8 条——作者看到问题列表反复出现，
    只觉得「这工具在瞎报」，其实问题从没被传达过去。

    P1 是「建议修改但不致命」，不是「不用改」。
    """
    if not review.p0 and not review.p1 and not review.fix_hints:
        return original_prompt
    parts = ["【上次生成未通过质量门禁，请按下列反馈重写】"]
    if review.p0:
        parts.append("致命问题（必须修正）：")
        parts.extend(f"- {x}" for x in review.p0)
    if review.p1:
        parts.append("建议级问题（同样要修，只是不致命）：")
        parts.extend(f"- {x}" for x in review.p1)
    if review.fix_hints:
        parts.append("具体修改方向：")
        parts.extend(f"- {x}" for x in review.fix_hints[:6])
    parts.append(f"当前总分 {review.total}/100，目标 ≥ 80。请严格保留剧情骨架，只修正以上问题。")
    return original_prompt + "\n\n" + "\n".join(parts)
