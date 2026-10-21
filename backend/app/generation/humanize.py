"""正向脚手架 + burstiness 统计校准——活人感写作规则 v2。

三部分职责：
1. system_prompt_addendum() —— 追加到 SYSTEM_CONTRACT 的正向写作铁律 +
   少量负向兜底 + burstiness 量化锚点 + few-shot 对照。
2. postprocess(text) —— 生成完成后的确定性后处理：引号/破折号用途纪律/
   顽固零容忍 P0 词/重复标点。**不删生理反应**。
3. diagnosis_score(text) —— 机器可判硬指标：引号/破折号/禁用词 +
   burstiness 四项（问号密度/句长CV/字种密度/逗号密度）。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

_ASSETS_DIR = Path(__file__).parent / "humanize_assets"


# ---------- system prompt 片段 ----------

HUMANIZE_CONTRACT = """【活人感写作铁律（正向脚手架 v2）】
以下规则优先级等同于系统合同，写剧情/正文/润色必须严格执行。

=== 一、标点纪律 ===
1. 对话一律用直角引号「」，嵌套对话用『』；严禁半角""、中文""包裹对话。
2. 省略号统一用六个点"……"，不用"..."或"。。。"；每千字省略号 ≤ 3 个。

=== 二、破折号用途纪律（按用途管，不按密度管）===
中文网文破折号只在两种情况下保留——
① 对话被打断或话没说完：「我还没——」「你听我解——」
② 解释性插入语：半句中间插入补充，再回到正题。
其他位置（句间转折代替逗号/句号、段首起笔、行首、连续三个及以上）一律替换为逗号或句号。
注意：破折号跨模型差异极大（有的模型几乎不用、有的模型千字 4+），因此不设千字号
密度硬阈值——用途对了 1-3 个都可以，用途错了 1 个都要改。

=== 三、正向写作铁律 10 条（必须遵守）===
1. Deep POV 切过滤词：删除"他看见/她感到/他注意到/她觉得/他发现"等叙事中介；感官直接写入叙述句，让读者与 POV 角色共享同一具身体。
2. 情绪生理化 + 人物专属小动作/口癖（从角色卡读，不要硬编码）：
   - 愤怒/悲伤/紧张/恐惧/喜悦/绝望等情绪标签词禁止直写，换成可观察的生理反应或具体动作；
   - 每个出场 POV 角色必须有**3-5 个专属小动作**和**1-2 个标志性口癖**（从【人物卡/角色设定】中读取；若角色卡未指定，先在剧情层给该角色设计再写）；这些动作必须具体、可执行、与身份/经历相关（烟民摸口袋、老兵数步数、前会计记账时舔拇指、学生转笔），不要给所有角色分配同一个动作库；
   - 同一场景内同一动作不重复使用超过 2 次；
   - 禁用万能反应（后背发凉/头皮发麻/心跳如鼓/指尖一僵/呼吸一窒/空气凝固/瞳孔骤缩/嘴角勾起）。
3. 动词先行，单句≤1形容词：每句必须有主动词；单句最多 1 个修饰性形容词；同一场景感官细节 ≤ 3 处；用具体名词替代抽象形容词（"褪色的帆布鞋"比"破旧的鞋"好）。
4. 五感轮换，触觉/嗅觉优先：每段场景不得只写视觉；必须轮换触觉（温度/质感/痛觉）、听觉（具体声响而非"一阵响声"）、嗅觉；情绪紧张时优先写躯体反应（心跳/口干/指节/胃酸/汗）；环境细节选与情绪共振的小点，不堆砌。
5. 段尾落在动作/物件/短句：每段结尾必须是可视的具体动作、具体物件、短台词或感官细节；禁止以"这让他感到""他心中百感交集""一切都结束了""五味杂陈""感慨万千"等抽象总结句收段。
6. 动作锚定对白：两句对白之间插入 ≤ 7 字说话者身体动作（与情绪相关，且是该人物专属动作库内的动作）；禁止连续 3 句以上纯对白无动作锚点；**允许打断、半截话、沉默、答非所问、口癖停顿**（"你——""算了。"沉默三秒。）。
7. 长短句变速：铺垫/交代用正常句；冲突/情绪爆发/关键动作处砍成 2-5 字短句（可一句一段），删掉多余"的/了/着"；情绪越强句子越短；禁止全程匀速。注意：句长均匀度本身**不是**AI 与人类的强区分特征（人机差异只有 13%），但关键处的极短句/打断确实能制造活人呼吸感——短句应该放在情绪转折点，不要为了"变速"刻意在每段都插。
8. 钩子落动作/物件/半截话：章末/段末钩子禁止落在心理总结（"他隐隐觉得事情没那么简单"）或作者旁白；必须落在：未完成动作、异常物件、半截话、反预期动作（"她低声答应，同时把刀藏进袖子"）。
9. 场景微观目标 + 情绪四拍：每场隐含 POV 角色的具体微观目标（想拿到/想让对方松口/想离开/想假装镇定）；情绪段按"铺垫→顶→翻→收"四拍走，不一拍宣泄完。
10. 信息差张力：悬念/紧张场景优先用信息不对称（读者知道角色不知道的危险/真相）；用环境细节（脚步声逼近/倒计时/手机电量/秒针）持续加压，不靠角色大喊"危险"。

=== 四、材料密度与节律锚点（实证有效指标）===
注意：以下指标中，**问号密度、段首零回指、数字/时间锚点密度**是经大规模人机对照验证区分度最高的；
句长均匀度（CV）区分力弱，不再作为硬性 P0 门禁，只做参考。
- 问号密度 5-8 个/千字（**这是人机差异最大的单指标之一，人类是 AI 的 17 倍**；内心反问、对白问句、不确定的自问必须自然出现，但不要每段都问）
- 段首零回指评论必须修复：段首若直接以评论性形容词/副词起笔（"奇怪的是""诡异的是""荒唐的是""可怕的是""值得注意的是""更要命的是"），必须补指示代词主语改写为"这事儿奇怪""这就很荒唐""真正可怕的还在后面"这类带回指的形式，不能让评论直接悬空
- 数字/时间锚点密度：人类稿具体数字（房号/时间/价格/年龄/次数/公里数/楼层）是 AI 的 2.8 倍，时间词（三点钟/第二天/上周/分钟/秒）是 2.6 倍；每 500 字至少 1 个具体数字或时间锚点（不要用"很久""一会儿""不少"这种模糊词，除非人物 POV 本来就不知道）
- 字种密度（前 1500 字）> 0.35（用具体名词动词，避免套话堆砌）
- 千字感叹号 ≤ 8 个；千字成语 ≤ 4 个；禁止对偶/排比句；禁止"像一位智慧的导师"这类带褒义拟人化喻体。

=== 五、风格对照示例（必须匹配"好例"的写法密度、节奏与情绪表达）===
【差例】他感到一阵彻骨的寒意涌上心头，眼中闪过一丝惊恐，嘴角微微颤抖，不禁后退了一步。
【好例】他往后退了半步。后颈的汗毛竖起来。

【差例】"我真的没有做过那件事，你相信我，我对你是真心的！"他愤怒地辩解道。
【好例】"我——"他张了张嘴，别过脸去，"你爱怎么想怎么想吧。"

=== 六、零容忍 P0 禁词（命中即换）===
- 空泛喻词：仿佛→好像、犹如/宛如/俨然/恍若/好似→像
- AI 叙事虚词：赫然、陡然、蓦然、骤然、倏地、蓦地、刹那间、顷刻间、电光石火间、说时迟那时快
- 文艺腔连接词：不言而喻、毋庸置疑、值得一提的是、不得不说、综上所述、无独有偶、果不其然、无巧不成书
- 典型 AI 模板句式（后处理正则替换兜底）：深吸一口气、那一刻意识到、空气凝固、涌上心头、眼中闪过一丝、嘴角勾起、心提到嗓子眼、如遭雷击、声音响起、倒吸凉气、全场寂静、瞳孔骤缩、眉头紧锁、如坠冰窟、指节发白、夜色月光套话（夜色如墨/月光如水）、不知道过了多久、面无表情地说、鸦雀无声、映入眼帘、威压袭来、脸色煞白、一字一句地说、与此同时、没有人知道、只见/却见、莫名熟悉感、大步流星、嘴角上扬、气氛尴尬
- 章末空预告钩子（P0 致命）："他/她/他们不知道的是""真正的危机才刚刚开始""命运的齿轮开始转动""更大的风暴即将到来""故事才刚刚开始"

=== 七、视角/设定/对白纪律 ===
1. 细节跟着视角走：只写当前视角人物能看到/听到/摸到/猜到的；禁止上帝视角越界。
2. 设定等人物碰到再讲：不要在人物还没接触某设定时大段解释世界观；每章新设定信息不超过 2 处。
3. 对白要"做事"：每句对白都在推进意图（试探/施压/隐藏/哄骗/求安慰）；禁止人物互相汇报彼此已知信息。
4. 每场戏至少改变一点东西：人物关系、信息状态、物理位置、情绪势能必须有变化。
5. 译文句式黑名单：过长前置定语拆短句；"当…时"千字 ≤2；"对于…来说"删掉壳；句首"然而"改"可/但/不过"放句中；"这意味着"复述句删；段首零回指评论（"这太荒唐了"/"真正可怕的还在后面"）删或补主语；对举结构"不是A而是B/与其说A不如说"每章 ≤1。

=== 八、骨架不改 ===
剧情/人设/事实/核心意思必须严格按输入大纲，不许 OOC，不许自行加戏或改结局；
"怎么说"可以打散重写、注入生活颗粒。
"""


def system_prompt_addendum() -> str:
    return HUMANIZE_CONTRACT


# ---------- 后处理：确定性替换 ----------

_HALF_DOUBLE_RE = re.compile(r'"([^"\n]*)"')
_FULL_DOUBLE_RE = re.compile(r'\u201c([^\u201c\u201d\n]*)\u201d')


def _replace_quotes(text: str) -> str:
    def _full(m: re.Match) -> str:
        return f"「{m.group(1)}」"
    text = _FULL_DOUBLE_RE.sub(_full, text)
    def _half(m: re.Match) -> str:
        return f"「{m.group(1)}」"
    text = _HALF_DOUBLE_RE.sub(_half, text)
    return text


# 破折号用途纪律（六步处理）：
# 1) 处理"句末标点+破折号"紧邻组合
# 2) 删除行首/段首破折号
# 3) 连续 3+ 破折号压缩为 1 个
# 4) 「...——...」 对话引号内的破折号暂存为占位符，等全部替换后恢复
# 5) 两侧各超过 8 字的句间破折号转为句号或逗号
# 6) 恢复占位符
_DASH_CHARSET = r"\u2014\u2015\u2500\u2501\-"
_DASH_RE = re.compile(f"[{_DASH_CHARSET}]{{2,}}|---+|——+")
_DASH_PLACEHOLDER = "\x00DASH\x00"


def _replace_dashes(text: str) -> str:
    # Step 4: 暂存对话内破折号（「...」 之间的破折号）为占位符，避免被后续规则误伤
    def _stash_dialog(m: re.Match) -> str:
        inner = m.group(1)
        new_inner = _DASH_RE.sub(_DASH_PLACEHOLDER, inner)
        return f"「{new_inner}」"
    text = re.sub(r"「([^」\n]*)」", _stash_dialog, text)

    # Step 1+2: 仅删除句末标点/换行/段首紧邻的破折号（不碰句中破折号，留给 step5 处理）
    # 注意：必须带标点/行首锚定，否则会误删句中破折号
    text = re.sub(rf"([。！？!?…])\s*[{_DASH_CHARSET}]{{2,}}", r"\1", text)
    text = re.sub(rf"([。！？!?…])\s*——+", r"\1", text)
    text = re.sub(rf"(^|\n)\s*[{_DASH_CHARSET}]{{2,}}", r"\1", text)
    text = re.sub(rf"(^|\n)\s*——+", r"\1", text)

    # Step 3: 连续破折号归一为标准双破折号
    text = _DASH_RE.sub("——", text)

    # Step 5: 根据上下文决定剩余（非对话内）破折号保留或替换
    def _decide(m: re.Match) -> str:
        s, e = m.start(), m.end()
        prev = text[s - 1] if s > 0 else ""
        nxt = text[e] if e < len(text) else ""
        if prev in "。！？!?…\n" or prev == "":
            return ""
        # 紧邻引号（插入语场景）保留
        if prev in "「『" or nxt in "」』":
            return "——"
        # 其他一律转逗号
        return "，"
    text = re.sub(r"——", _decide, text)

    # Step 6: 恢复对话内占位符为标准双破折号「——」
    text = text.replace(_DASH_PLACEHOLDER, "——")
    return text


# 段首零回指评论自动修复（R=4.4，区分力第一）：
# 段首直接用评论性词起笔（"奇怪的是/诡异的是/值得一提的是/更要命的是/有意思的是/
# 荒唐的是/可怕的是/讽刺的是/不巧的是"）会悬空；在前面补一个"可"或"但"恢复指代表面衔接
# 即可把误判率压到人类水平。
# 注意："然而/但是/可是/不过"已经是带连接的，不动；"奇怪。""有意思。"这种短句独立感叹不动。
_ZERO_ANAPHORA_RE = re.compile(
    r"(^|\n)\s*"
    r"(奇怪|诡异|荒唐|可怕|恐怖|要命|有意思|有趣|讽刺|不巧|遗憾|可惜|耐人寻味)"
    r"(的是[，、]?)"
)
_ZERO_ANAPHORA_PLAIN_RE = re.compile(
    r"(^|\n)\s*"
    r"(更让人|更令人|令人|让人|使人)"
    r"(不安|在意|费解|毛骨悚然|不寒而栗|细思极恐|绝望)"
    r"[，。]?"
)


def _fix_zero_anaphora(text: str) -> str:
    """段首零回指评论前补'但'字，恢复衔接（lieflat R=4.4 区分力第一）。"""
    def _fix1(m: re.Match) -> str:
        head, adj, tail = m.group(1), m.group(2), m.group(3)
        return f"{head}但{adj}{tail}"
    def _fix2(m: re.Match) -> str:
        head, prefix, adj = m.group(1), m.group(2), m.group(3)
        return f"{head}但{prefix}{adj}"
    text = _ZERO_ANAPHORA_RE.sub(_fix1, text)
    text = _ZERO_ANAPHORA_PLAIN_RE.sub(_fix2, text)
    return text


# 顽固零容忍 P0 词/句式的安全替换（保守，只替换命中率高、规则无歧义的项）。
# 注意：**不再删除生理反应**（后背发凉/头皮发麻等），这些由正向铁律 §三-2
# 在生成时引导模型换成人物专属动作，事后删除会留下空洞。
_SAFE_REPLACES: list[tuple[re.Pattern, str]] = [
    # 空泛喻词
    (re.compile(r"仿佛(?!在)"), "好像"),
    (re.compile(r"犹如"), "像"),
    (re.compile(r"宛如"), "像"),
    (re.compile(r"恍然(?!大悟)"), "好像"),
    # AI 叙事虚词
    (re.compile(r"赫然"), ""),
    (re.compile(r"陡然"), "突然"),
    (re.compile(r"蓦然"), "突然"),
    (re.compile(r"骤然"), "突然"),
    (re.compile(r"倏地"), "一下"),
    (re.compile(r"蓦地"), "突然"),
    (re.compile(r"刹那间|顷刻间|电光石火间|说时迟那时快"), "一瞬间"),
    # 文艺腔连接词（删/换）
    (re.compile(r"不言而喻[，。]?"), ""),
    (re.compile(r"毋庸置疑[，。]?"), ""),
    (re.compile(r"值得一提的是[，。]?"), ""),
    (re.compile(r"不得不说[，。]?"), ""),
    (re.compile(r"综上所述[，。]?"), ""),
    (re.compile(r"果不其然[，。]?"), "果然"),
    # AI 心理揭示前缀（保留下文）
    (re.compile(r"那一刻，?(他|她|我|唐迟|陆知返|江喻|年糕)(才|终于|忽然)?(意识|明白|懂)[到过]?，?"), "他"),
    (re.compile(r"(他|她|我|唐迟|陆知返|江喻)(突然)?(意识|明白)到，"), ""),
    (re.compile(r"不由得"), ""),
    (re.compile(r"忍不住"), ""),
    (re.compile(r"不禁"), ""),
    # 万能 AI 模板句式（后处理兜底替换）
    (re.compile(r"(他|她|我|唐迟|陆知返)(深|缓)(吸|呼)(了)?(?:一口气|一口|口气|气)[，。]?"), ""),
    (re.compile(r"空气(?:仿佛|好像|瞬间|一下子|骤然)?(?:凝固|安静|死寂|沉默)(?:了)?[，。]?"), ""),
    (re.compile(r"(?:一股|一阵|莫名(?:的|地))?\s{0,4}(?:凉意|寒意|恐惧|愤怒|不安|烦躁|冲动|怒火).{0,6}(?:涌上|窜上|袭上)心头[，。]?"), ""),
    (re.compile(r"眼(?:神|中|里|眸)(?:一闪(?:而(?:过|逝))?|闪过|掠过)(?:一丝|一抹|一道|几分)"), ""),
    (re.compile(r"嘴角(?:勾起|扬起|微扬|勾起一抹|勾出一抹|挑起一抹?)(?:冷笑|弧度|戏谑|笑意)"), ""),
    (re.compile(r"心(?:一下子|猛地)?(?:提到|沉到)(?:了)?(?:嗓子眼|谷底|冰点)[，。]?"), ""),
    (re.compile(r"如遭雷击|大脑一片空白|整个人僵在原地"), ""),
    (re.compile(r"倒吸(?:一口|了一口)凉(?:气|冷气)[，。]?"), ""),
    (re.compile(r"全场(?:一片|死一般)?(?:寂静|沉寂|鸦雀无声|针落可闻|默然)[，。]?"), ""),
    (re.compile(r"瞳孔(?:骤缩|猛缩|一缩|收缩)[，。]?"), ""),
    (re.compile(r"眉头(?:紧锁|微蹙|紧蹙|一蹙|皱起)[，。]?"), ""),
    (re.compile(r"如坠冰窟|如芒在背|如临大敌|如释重负[，。]?"), ""),
    (re.compile(r"指节(?:发白|泛白)|指尖泛白|拳头攥得(?:咯咯|咔咔)作响[，。]?"), ""),
    (re.compile(r"夜色如墨|月光如水|骄阳似火|寒风刺骨"), ""),
    (re.compile(r"(?:也)?不知道过了多久|不知过了多久|时间仿佛(?:在这一刻)?(?:静止|凝固|停滞)[，。]?"), ""),
    (re.compile(r"(?:面无表情|脸上没有一丝表情|毫无表情)(?:地|的)(?:说|道|开口)"), ""),
    (re.compile(r"鸦雀无声|针落可闻|落针可闻[，。]?"), ""),
    (re.compile(r"映入眼帘|目光所及(?:之处)?|扑入眼帘[，。]?"), ""),
    (re.compile(r"(?:一股|一股强大|恐怖的)(?:威压|气势|气场|气息).{0,6}(?:扑面|席卷|袭来|压迫)[，。]?"), ""),
    (re.compile(r"脸色(?:煞白|铁青|瞬间变得难看|一下子沉下来)[，。]?"), ""),
    (re.compile(r"一字一句(?:地|的)(?:说|道|开口)|从牙缝里挤出[，。]?"), ""),
    (re.compile(r"话音刚落|话音未落|话音刚起"), ""),
    (re.compile(r"与此同时|就在这时|就在这一刻"), ""),
    (re.compile(r"没有人知道|谁也没(?:有)?(?:想|料)到|所有人都没(?:有)?想到"), ""),
    (re.compile(r"(^|\n)\s*(?:只见|却见|但见)"), r"\1"),
    (re.compile(r"莫名(?:的|地)?熟悉感|似曾相识(?:的感觉)?"), ""),
    (re.compile(r"大步流星|健步如飞"), ""),
    (re.compile(r"嘴角(?:微微|不自觉|下意识)?(?:上扬|勾起|弯起)[，。]?"), ""),
    (re.compile(r"气氛(?:一时)?(?:有些|瞬间)?(?:尴尬|凝固|降到冰点|紧张)[，。]?"), ""),
    # 章末空预告钩子（P0 致命，删掉避免污染）
    (re.compile(r"(?:他|她|他们|众人)(?:还)?不知道的是[，。]?"), ""),
    (re.compile(r"真正的危机(?:才|才刚|刚刚)开始[，。]?"), ""),
    (re.compile(r"命运的齿轮(?:开始转动)?[，。]?"), ""),
    (re.compile(r"一(?:场|个)?更大的风暴(?:即将|就要|马上)到来[，。]?"), ""),
    (re.compile(r"故事(?:才|这才|才刚)刚刚开始[，。]?"), ""),
    # 万能副词/总结虚词（保守替换，避免过度）
    (re.compile(r"一丝(?!不挂|不苟)"), "一点"),
]


def _replace_banned(text: str) -> str:
    for pat, rep in _SAFE_REPLACES:
        text = pat.sub(rep, text)
    # 清理替换后遗症
    text = re.sub(r"，+", "，", text)
    text = re.sub(r"，([。！？!?])", r"\1", text)
    text = re.sub(r"([。！？!?])，", r"\1", text)
    text = re.sub(r"(^|\n)\s*，", r"\1", text)
    text = re.sub(r" {2,}", " ", text)
    return text


_PUNCT_REPEAT_RE = re.compile(r"([。！？!?,，、；：])\1+")


def _normalize_punct(text: str) -> str:
    return _PUNCT_REPEAT_RE.sub(r"\1", text)


def postprocess(text: str) -> str:
    """确定性后处理：引号/破折号用途纪律/段首零回指/顽固P0词/重复标点。不删生理反应。"""
    if not text:
        return text
    text = _replace_quotes(text)
    text = _replace_dashes(text)
    text = _fix_zero_anaphora(text)
    text = _replace_banned(text)
    text = _normalize_punct(text)
    return text


# ---------- 诊断（含 burstiness 四项） ----------

# 分句用句末标点。注意省略号"……"不算句末（它是句内停顿），简化处理。
_SENT_SPLIT_RE = re.compile(r"[。！？!?]+")
_CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


# 数字（阿拉伯/中文小写/中文大写，不含"一"作泛指/虚词的情况做粗匹配）
# 含阿拉伯 0-9、中文数字 零〇一二三四五六七八九十百千万亿两
# 注意"一"在汉语里极常见且多为虚词（一个/一会儿/一点），会让数字密度虚高，
# 但作为粗指标仍然可以反映锚点浓度——阈值设得保守即可。
_NUMBER_RE = re.compile(r"[0-9零〇一二三四五六七八九十百千万亿两]{1,}[点半]?[0-9零〇一二三四五六七八九十百千万亿两]?")
# 时间锚点词（粗筛）
_TIME_WORDS_RE = re.compile(
    r"(点钟|点半|分(?:钟)?|秒(?:钟)?|小时|时辰|刻钟|凌晨|清晨|早晨|上午|中午|下午|傍晚|晚上|夜里|午夜|"
    r"今天|明天|昨天|前天|后天|这周|本周|这周|上周|下周|这月|本月|上月|下月|今年|去年|明年|前年|"
    r"周一|周二|周三|周四|周五|周六|周日|星期[一二三四五六日天]|"
    r"日号月年世纪|岁|第[一二三四五六七八九十0-9]+[天次章回节天日])"
)
# 拟人化褒义喻体（带褒义"像一位 X"，lieflat 测 R=12.6× 是 AI 重灾区）
_PERSONIFIED_SIMILE_RE = re.compile(
    r"像(?:一位|一名|一个)?(?:智慧|仁慈|慈祥|严厉|公正|博学|温柔|善良|慷慨|高贵|优雅|伟大|慈爱|耐心|忠诚)"
    r"(?:的)?(?:导师|老人|长者|母亲|父亲|朋友|使者|守护者|引路人|先知)"
)


# 段尾动作落点特征（段尾落在具体动作上 = 活人感）
_ACTION_TAIL_RE = re.compile(
    r"(起来|过来|下去|上去|走了|站着|坐着|看着|听着|伸手|收回|转身|抬头|低头|点头|摇头|"
    r"抓起|放下|摸了摸|摸出|掏出|递|推|拉|打开|关上|起身|蹲下|弯腰|按住|踩|点了|盯着|扫了眼)"
)


def _rhythm_metrics(text: str) -> dict:
    """活人节奏指标：段落长度分布、极短段占比、动作收尾占比。

    这是润色层最容易磨掉的东西（合并段落、砍极短段），故单独度量。
    """
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    n = len(paras)
    if n == 0:
        return {"段落数": 0, "段落均长": 0.0, "极短段(≤12字)": 0, "极短段占比": 0.0,
                "超短段(2-6字)": 0, "动作收尾占比": 0.0, "段落CV": 0.0}
    lens = [len(p) for p in paras]
    mean = sum(lens) / n
    var = sum((x - mean) ** 2 for x in lens) / n
    cv = math.sqrt(var) / mean if mean else 0.0
    short = sum(1 for L in lens if L <= 12)
    tiny = sum(1 for L in lens if 2 <= L <= 6)
    action_tail = sum(1 for p in paras if _ACTION_TAIL_RE.search(p.rstrip("。！？!?…")))
    return {
        "段落数": n,
        "段落均长": round(mean, 1),
        "段落CV": round(cv, 3),
        "极短段(≤12字)": short,
        "极短段占比": round(short * 100 / n, 1),
        "超短段(2-6字)": tiny,
        "动作收尾占比": round(action_tail * 100 / n, 1),
    }


def _burstiness_stats(text: str) -> dict:
    chars = len(text.strip())
    if chars == 0:
        return {
            "question_density": 0.0,
            "sentence_cv": 0.0,
            "char_type_density": 0.0,
            "comma_density": 0.0,
            "number_density": 0.0,
            "time_anchor_density": 0.0,
            "zero_anaphora_hits": 0,
            "personified_simile": 0,
        }
    # 问号密度（含中英文问号）
    q = len(re.findall(r"[？?]", text))
    question_density = q * 1000 / chars
    # 逗号密度
    c = len(re.findall(r"[，,]", text))
    comma_density = c * 1000 / chars
    # 句长 CV（按句末标点切，滤掉空串）
    sents = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    sent_lens = [len(s.strip()) for s in sents if len(s.strip()) > 0]
    if len(sent_lens) < 2:
        sentence_cv = 0.0
    else:
        mean = sum(sent_lens) / len(sent_lens)
        var = sum((x - mean) ** 2 for x in sent_lens) / len(sent_lens)
        std = math.sqrt(var)
        sentence_cv = std / mean if mean > 0 else 0.0
    # 字种密度（前 1500 字）
    head = text[:1500]
    cn_chars = _CHINESE_CHAR_RE.findall(head)
    if cn_chars:
        char_type_density = len(set(cn_chars)) / len(cn_chars)
    else:
        char_type_density = 0.0
    # 数字密度（具体阿拉伯/中文数字出现次数/千字）
    nums = len(_NUMBER_RE.findall(text))
    number_density = nums * 1000 / chars
    # 时间锚点密度（时间词出现次数/千字）
    times = len(_TIME_WORDS_RE.findall(text))
    time_anchor_density = times * 1000 / chars
    # 段首零回指命中数（postprocess 修复前的统计指标）
    za = len(_ZERO_ANAPHORA_RE.findall(text)) + len(_ZERO_ANAPHORA_PLAIN_RE.findall(text))
    # 拟人化褒义喻体
    ps = len(_PERSONIFIED_SIMILE_RE.findall(text))
    return {
        "question_density": round(question_density, 2),
        "sentence_cv": round(sentence_cv, 3),
        "char_type_density": round(char_type_density, 3),
        "comma_density": round(comma_density, 2),
        "number_density": round(number_density, 2),
        "time_anchor_density": round(time_anchor_density, 2),
        "zero_anaphora_hits": za,
        "personified_simile": ps,
    }


def load_skill_md() -> str:
    p = _ASSETS_DIR / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def diagnosis_score(text: str) -> dict:
    """返回诊断计数：引号/破折号/禁用词 + burstiness 四项。"""
    half_dq = len(_HALF_DOUBLE_RE.findall(text))
    full_dq = len(_FULL_DOUBLE_RE.findall(text))
    dashes = len(_DASH_RE.findall(text))
    banned_hits = 0
    for pat, _ in _SAFE_REPLACES:
        banned_hits += len(pat.findall(text))
    burst = _burstiness_stats(text)
    return {
        "half_double_quotes": half_dq,
        "full_double_quotes": full_dq,
        "dashes": dashes,
        "banned_hits": banned_hits,
        **burst,
    }


if __name__ == "__main__":
    import sys
    src = sys.stdin.read()
    print(postprocess(src))
