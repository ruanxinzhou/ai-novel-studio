"""去AI 味护栏：postprocess 的否决权。

问题
----
`postprocess()`（humanize.py:328）是无条件全量替换链，跑完直接覆盖原文
（engine.py:501），没有 diff、没有校验、没有回滚。实测破坏性：

    空气仿佛凝固了。            -> ''（整句消失，删除 100%）
    他感到恐惧涌上心头。        -> '他感到'（残句，删除 53.8%）
    他眼中闪过一丝复杂的情绪。  -> '他复杂的情绪'（语法崩，删除 30.0%）

信息守恒也是破的：`眼中闪过一丝` 被规则删掉后，`一丝 → 一点` 这条规则
又把剩下的部分改成「他眼中闪过一点笑意」——原句里根本没有「一点」。
这是多规则接力产生的凭空注入。

设计原则：护栏只做否决权，不做修改权
------------------------------------
三条护栏全部是「检测到崩坏 → 退回原文」，**不存在「修复」这个动作**。
最坏情况是这次去味没生效，而不是把正文改坏。这比「检测 + 尝试修复」
安全得多——修复需要中文语法判断，误伤风险高于收益。

为什么不碰破折号规则
--------------------
实测 `_replace_dashes` 会把作者的插入语转逗号
（「他停在门口——那扇门昨夜还在响——伸手推了一下。」会变成两个逗号），
但 engine.py 注释明确写了「破折号按用途管不按密度管，跨模型差异 40 倍」，
这是**有意的取舍**，不是 bug。护栏不介入。

为什么不改 _SAFE_REPLACES
-------------------------
这批规则是长期积累出来的，有些看起来激进（比如删整个「空气仿佛凝固了」）
但在 AI 味正文里是对的。删规则是独立判断，需要真实语料支撑。
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# 单章删除比例超过此值 → 整体可疑，直接回滚。
# 实测单个坏句可达 100%，正常去味在 5-15%。60% 是宽松阈值，
# 只拦「大面积蚕食」，不拦「删掉一整句」那种已被护栏 1 覆盖的情况。
DELETE_RATIO_ROLLBACK = 0.60
# 比例判定的最小文本长度。低于此值时短句天然比例极高（「他瞳孔骤缩。」→「他」
# 删 71%），但那是**单句**而非整章，语料本来就这么短，不该按章级阈值判。
# 实测：真删空的坏句由护栏 1（残句）与护栏 3（段落守恒）覆盖，不依赖比例。
DELETE_RATIO_MIN_CHARS = 200
# 段落级守恒阈值（护栏 3）
PARA_DELETE_RATIO = 0.50

# 悬空心理动词：动词后必须紧跟句末/标点，中间不能有宾语。
# 匹配「感到」出现在子句开头且该子句到此为止的情形（如「……，他感到。」）。
_DANGLING_VERB_RE = re.compile(
    r"(?:^|[，。！？、\n])(?:感到|觉得|意识到|体会到|领悟到|感受到)"
    r"[，。！？、\n]"
)
# 短残句：以心理动词**结尾**且整句极短（「他感到」「她觉得」）。
# 必须锚定行尾——否则「他感到恐惧」这种正常句会被误判（实测 23 例误杀全来自这里）。
# 行尾允许一个句末标点：「他感到。」也是残句（动词被删光后剩下的壳），
# 但「他感到恐惧。」不会命中（恐惧不是标点）。
_SHORT_FRAGMENT_RE = re.compile(r"^.{0,4}(?:感到|觉得|意识到|体会到)[。！？!?]?$")

# 信息守恒用：中文单字的**多重集差分**。
#
# 为什么不用词/连续串/bigram：任何编辑都会改变切分边界，于是删除本身就会
# 造出新的「词」或「bigram」（「眼中闪过一丝笑意」→「笑意」会产生 junction
# bigram「他笑」），把每一次删除都误报成凭空注入（实测 20 例误杀全来自这里）。
#
# 字符多重集是唯一不受边界影响的粒度：
# - 删除 → 只减不增，必然守恒；
# - 替换（仿佛→好像、一丝→一点）→ 旧字减少、新字增加，正是要抓的；
# - 合法替换的新字用 _SAFE_REPLACES 白名单放行。
_CN_CHAR_RE = re.compile(r"[一-鿿]")
from collections import Counter


def char_multiset(text: str) -> Counter:
    """中文字符多重集。"""
    return Counter(_CN_CHAR_RE.findall(text or ""))


def new_chars(before: str, after: str) -> set[str]:
    """after 中出现次数多于 before 的字，即「凭空多出来的字」。

    这是信息守恒的硬指标：改写只能删、只能换等义字，不能凭空造出原文没有的字。
    例：「一丝」→「一点」凭空多出「点」；「眼中闪过一丝笑意」→「笑意」不多字，不误报。
    """
    b, a = char_multiset(before), char_multiset(after)
    return {ch for ch, n in a.items() if n > b.get(ch, 0)}


def content_words(text: str) -> set[str]:
    """保留旧接口：返回字符集合（供白名单派生与外部调试用）。"""
    return set(_CN_CHAR_RE.findall(text or ""))


def _legal_replacement_words() -> set[str]:
    """从 _SAFE_REPLACES 提取全部合法的替换目标词（白名单）。

    白名单必须**自动派生**而不是手写维护：手写必然漏项或过期，
    而漏项的后果是把合法替换判成违规回滚——这比不检测更糟。
    """
    from .humanize import _SAFE_REPLACES
    allowed: set[str] = set()
    for _pat, rep in _SAFE_REPLACES:
        if not rep:
            continue
        # 反向引用（\1）要先剥掉，否则 r"\1但" 会被当成词
        clean = re.sub(r"\\(\d)", "", rep)
        allowed.update(content_words(clean))
    return allowed


@dataclass
class GuardResult:
    """护栏裁决。"""
    rolled_back: bool = False
    reason: str = ""
    added_words: list[str] = field(default_factory=list)
    delete_ratio: float = 0.0
    lost_ratio: float = 0.0
    fragments: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """落账本用（spec 1 的 guard 字段）。"""
        return {
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "added_words": self.added_words[:10],
            "delete_ratio": round(self.delete_ratio, 3),
            "lost_ratio": round(self.lost_ratio, 3),
            "fragments": self.fragments[:5],
        }


def _delete_ratio(before: str, after: str) -> float:
    if not before:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, before, after, autojunk=False).ratio()


# 空壳/ 崩坏：去掉标点后只剩 1 个字（「他」「。」「了」），
# 或出现「他他他」这类**主语堆积**——多条规则各自删掉谓语宾语后，
# 主语被留下来首尾相接。这是实测最隐蔽的崩坏形态：
#   「他瞳孔骤缩。他脸色铁青。他指节发白。」→「他他他。」(语法成立但完全不是人话)
# 单看每条都「只是删除」，只有看结果才发现整句垮了。
_DEAD_RE = re.compile(r"(?:他|她|我|它){2,}")

#: **单字成句的合法情形**（2026-10-24 修正）。
#:
#: 「空壳」规则（去掉标点后只剩 1 个字）本意是抓被删到只剩主语的崩坏句，
#: 但它把**合法的单字成段**也一起打掉了：
#:   「谁？」「走。」「嗯。」「不。」「对。」「冷。」
#: 这些是中文里完全正常的极短句/停顿，是活人痕迹的一部分。
#: 一旦命中，护栏会**整篇回滚**——作者看到的却是
#: 「[去AI味护栏] 后处理产生崩坏，已退回原文：改写产生残句：谁？」，
#: 完全看不出问题在哪，只能干瞪眼。
#:
#: 判据：单字**疑问词/语气词**成句 → 合法；单字**代词/连词**成句 → 崩坏。
_LEGAL_SHORT_SENTENCE = frozenset({
    # 疑问词
    "谁", "啥", "哪", "怎", "为什么", "凭什么", "干嘛", "多少", "几点",
    # 语气/应答词
    "嗯", "哦", "啊", "呀", "吧", "吗", "呢", "哈", "嘿", "喂",
    # 判断词
    "不", "是", "对", "错", "好", "行", "嗯嗯",
    # 动词（对话里单独成句的命令/应答）
    "走", "停", "来", "去", "坐", "起", "说", "听", "看", "等",
    # 名词（对话里单独指代）
    "钱", "人", "命", "枪", "刀", "门", "车", "路",
})


def _is_legal_short_sentence(core: str) -> bool:
    """单字成句是否属于合法用法（疑问词/语气词/判断词）。"""
    return core in _LEGAL_SHORT_SENTENCE


def _find_fragments(after: str) -> list[str]:
    """找残句：悬空心理动词 + 短碎片 + 被删到只剩主语/标点的空壳 + 主语堆积。"""
    out: list[str] = []
    for line in after.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _DANGLING_VERB_RE.search(s) or _SHORT_FRAGMENT_RE.match(s):
            out.append(s[:40])
            continue
        # 主语堆积：「他他他。」
        if _DEAD_RE.search(s):
            out.append(s[:40])
            continue
        # 空壳：去掉标点后只剩 1 个字（「他」「。」「了」）
        core = re.sub(r"""[，。！？、；：""''（）…—\s]""", "", s)
        if len(core) <= 1 and not _is_legal_short_sentence(core):
            out.append(s[:40])
    return out


def _para_lost_ratio(before: str, after: str) -> tuple[float, list[str]]:
    """段落级守恒：返回最严重删空的段落比例 + 该段原文。"""
    b_paras = [p for p in before.split("\n") if p.strip()]
    a_paras = [p for p in after.split("\n") if p.strip()]
    if not b_paras:
        return 0.0, []
    a_set = set(a_paras)
    worst, worst_p = 0.0, ""
    for p in b_paras:
        if p in a_set:
            continue
        # 该段在after 里不存在或被改写过：用字符重叠粗估保留比例
        best = 0.0
        for q in a_paras:
            r = difflib.SequenceMatcher(None, p, q, autojunk=False).ratio()
            if r > best:
                best = r
        lost = 1.0 - best
        if lost > worst:
            worst, worst_p = lost, p
    return worst, [worst_p] if worst_p else []


def check(before: str, after: str) -> GuardResult:
    """核心裁决：对比 postprocess 前后，判断是否该回滚。

    顺序刻意如此——先查最便宜的（比例）、再查语法残句、最后查守恒，
    因为比例检查是纯数值运算且能挡掉大面积异常。
    """
    res = GuardResult()
    if not before:
        return res
    # after 为空 = postprocess 把整段吃光了。这是**最坏情形**（比残句更糟：
    # 残句至少还剩字），必须回滚，不能因为「无内容可比」而放过去。
    if not after.strip():
        res.rolled_back = True
        res.delete_ratio = 1.0
        res.reason = "改写后内容为空（整段被吃光）"
        return res
    if before == after:
        return res

    res.delete_ratio = _delete_ratio(before, after)

    # 护栏 1a：整体删除过多（仅对足够长的文本判定，见 DELETE_RATIO_MIN_CHARS）
    if len(before.strip()) >= DELETE_RATIO_MIN_CHARS and res.delete_ratio > DELETE_RATIO_ROLLBACK:
        res.rolled_back = True
        res.reason = (f"删除比例 {res.delete_ratio:.0%} 超过 "
                      f"{DELETE_RATIO_ROLLBACK:.0%} 红线（整章被大面积蚕食）")
        return res

    # 护栏 1b：残句
    frags = _find_fragments(after)
    if frags:
        res.fragments = frags
        res.rolled_back = True
        # 提示要说人话：作者看到「改写产生残句：谁？」完全不知道发生了什么，
        # 也不知道该改哪。这里把「哪一句出了问题 + 为什么」讲清楚。
        res.reason = (
            f"去 AI 味改写把某一句删成了不完整的残句（例：{frags[0][:20]}），"
            "已整段退回原文。这一句通常是被「删掉心理动词/情绪词」这条规则清空的。"
        )
        return res

    # 护栏 2：新增实词（信息守恒）。合法替换词在白名单内不算违规。
    added = new_chars(before, after)
    if added:
        allowed = _legal_replacement_words()
        unexpected = sorted(added - allowed)
        if unexpected:
            res.added_words = unexpected
            res.rolled_back = True
            res.reason = f"凭空注入实词：{'/'.join(unexpected[:3])}（原文无此词）"
            return res

    # 护栏 3：段落级守恒
    worst, lost = _para_lost_ratio(before, after)
    if worst > PARA_DELETE_RATIO:
        res.rolled_back = True
        res.lost_ratio = worst
        res.reason = f"某段被删掉 {worst:.0%}（超过 {PARA_DELETE_RATIO:.0%}）"
        return res

    return res


def guarded_postprocess(before: str) -> tuple[str, GuardResult]:
    """包一��postprocess：返回 (最终文本, 裁决)。

    回滚时返回**原文**，而不是「原文 + 未处理的残留」——
    宁可这次去味没生效，也不要给作者一段语法崩坏的文本。
    """
    from .humanize import postprocess
    after = postprocess(before)
    res = check(before, after)
    if res.rolled_back:
        log.debug("deai guard rolled back: %s", res.reason)
        return before, res
    return after, res