"""作者批注（【】标注）：从正文里提取、标定作用域、生成锚点。

## 为什么需要这一层

作者想手动改正文，但机器润色对「流畅度」的提升有限——
因为机器不知道作者想改什么。作者的直觉是：
**直接在正文里用【】写清楚要改什么**，润色时机器连同其他注入的细节一起处理。

所以润色这一步要能：
1. **认出**正文里的【…】是作者批注（不是正文内容）
2. **标定作用域**——这条批注到底作用于哪
3. **传下去**，让模型既知道位置也知道内容
4. **校验**批注有没有被落实（模型漏了要能看出来）

## 作用域怎么标定（核心）

「新增」和「修改」指向的东西**根本不同**：

- **修改类**（「改成」「这一段有问题」「删掉」）指向一个**对象**：
  要改的是**某一段**。作者读着正文，看到一段有问题，写下意见。
  中文写作习惯是「先看到内容，再写意见」→ 所以独占成行的修改类批注
  作用于**它上面那一段**。
- **新增类**（「这里要增加」「补一段」）指向一个**位置**：
  要加的内容落在**某个点上**。所以独占成行的新增类批注
  作用在**它所在的那个位置**（上一段之后）。

行内批注（跟在句末）没有歧义：一定是改**本段**。

另外支持**显式锚定**：【第5段：改成…】——作者想指远处的段落时用。

## 位置怎么传给模型

不能只给「第N段」这种弱定位——模型数不准段落。
所以在正文里放**锚点标记** `⟦A1⟧`，同时给一份清单：

    正文：  他推开门。⟦A1⟧
           屋里没人。⟦A2⟧

    清单：  A1（改写本段）改成他踹开门
           A2（在此处新增）补一段他检查弹匣

模型既知道精确位置（锚点在哪），又知道完整内容（清单里）。
锚点用 `⟦⟧`（生僻符号）而不是【】，因为【】在中文正文里会被
去 AI 味后处理当成「AI 模板句式」删掉，护栏还会整段回滚。

## 为什么不让【】留在正文里

【】留在正文里有两个坑：
1. 模型可能原样抄进成稿；
2. 去 AI 味后处理会把【…】当成 AI 模板句式删掉 → 护栏整段回滚
   → 作者看到「改写产生残句」的莫名报错。

所以必须在进模型之前替换成锚点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 批注本体：【…】。允许跨行（作者可能写得很长）。
#:
#: 用 DOTALL 让内容匹配换行；用 `[^【】]` 排除嵌套括号。
ANNOTATION_RE = re.compile(r"【([^【】]{1,400})】|\[([^\[\]]{1,80})\]", re.DOTALL)

#: 全角【】是作者批注的主形式；半角 [] 只在内容像指令时才认，
#: 否则正文里的 [1] 引用、方括号符号会被误当批注。
_HALF_BRACKET_HINT_RE = re.compile(
    r"(改成|改写|删|删除|去掉|补|加|增加|写|添加|插入|调整|改|替换|"
    r"这里|此处|这段|这句|注意|要|应该|展开|扩写|精简|压缩)"
)

#: 明确**不是**批注的内容——正文里本来就有的方括号用法。
_NOT_ANNOTATION = re.compile(r"^\s*[\d\s\[\]]*\s*$")

#: 显式意图前缀（作者可以自己标类型，覆盖关键词推断）。
#:
#: 前缀写法：`【新增：xxx】`、`【修改：xxx】`、`【删除：xxx】`
_PREFIX_RE = re.compile(
    r"^\s*(新增|补充|添加|加|修改|改成|改写|调整|替换|删除|删掉|去掉)\s*[:：]\s*")

#: 显式段落锚定：`【第5段：xxx】` / `【第5段 xxx】` / `【@5 xxx】`
_PARA_ANCHOR_RE = re.compile(r"^\s*(?:第\s*(\d+)\s*段|@\s*(\d+))\s*[:：]?\s*")

#: 意图关键词（按判定优先级从高到低）。
#:
#: 为什么要分「删除 / 新增 / 修改」三类：
#: 它们的作用对象不同——
#:   删除 = 移除某个对象（某段）
#:   新增 = 在某个位置插入内容
#:   修改 = 改写某个对象（某段）
#: 作用域标定完全依赖这个分类。
#:
#: 「修改」为什么拆成强/弱两档：
#: 强词（改成/改写/替换）明确在说「重写」，
#: 弱词（这段/这里/有问题）只是作者在指位置，语义偏弱。
#: 顺序上先判强修改，再判新增，最后才用弱修改——
#: 否则「这里要有一场雨」会被弱词「这里」抢走，误判成改写。
_INTENT_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("delete", re.compile(
        r"(删除|删掉|删了|删这段|去掉|砍掉|移除|"
        r"这段不要|不要写|不用写|整段删)")),
    # 强修改：明确要重写
    ("replace", re.compile(
        r"(改成|改写|换成|替换|重写|改为|调整|修改)")),
    ("insert", re.compile(
        r"(增加|增补|补充|补上|补一段|补一句|添加|插入|加一段|加一句|加个|"
        r"加上|加点|加些|加进|多加|另加|"
        r"写一段|写个|要多写|加内容|这里加|要有)")),
    # 弱修改：只是指位置 + 有问题
    ("replace", re.compile(
        r"(这一段|这段|这里|此处|有问题|不对|改一下)")),
]

#: 意图 → 中文标签
INTENT_LABELS = {
    "insert": "新增",
    "replace": "修改",
    "delete": "删除",
}

#: 锚点标记：正文里替换【】的位置。
#:
#: 用 `⟦A1⟧` 而不是【】：生僻符号不会出现在中文小说正文里，
#: 也躲开了去 AI 味后处理的「AI 模板句式」删除规则。
MARKER_RE = re.compile(r"⟦(A\d+)⟧")


@dataclass
class Annotation:
    """一条作者批注（含作用域标定结果）。"""

    text: str                 # 批注内容（不含括号、不含前缀/锚定）
    intent: str = "replace"   # insert / replace / delete
    marker: str = ""          # 锚点编号（A1 / A2…）
    target_para: int = -1     # 目标段落序号（-1 = 位置型，指插入点）
    para: int = -1            # 批注原本所在的段落序号
    line: int = -1            # 批注原本所在的行号
    inline: bool = False      # 是否行内（跟在正文后）
    # 作用域对应的**原文**——后置校验靠它判断「这句是不是本来就在」。
    # 不存这个，关键词命中「开门」这种原文已有的词，就会误判成「已落实」。
    source_text: str = ""
    # 作用域被调整过时的说明（比如段号超出范围）——给作者看。
    anchor_note: str = ""

    @property
    def scope_label(self) -> str:
        """给模型看的作用域描述。"""
        if self.intent == "insert":
            # 显式锚定了段号的新增 → 说清是「在第N段新增」，
            # 不然模型会以为要另起一段新内容。
            if self.target_para >= 0:
                return f"第{self.target_para + 1}段内新增"
            return "在此处新增"
        if self.target_para >= 0:
            return f"第{self.target_para + 1}段"
        return "此处"

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "intent": self.intent,
            "marker": self.marker,
            "target_para": self.target_para,
            "para": self.para,
            "line": self.line,
            "inline": self.inline,
            "scope_label": self.scope_label,
            "source_text": self.source_text[:200],
            "anchor_note": self.anchor_note,
        }


@dataclass
class AnnotationScan:
    """扫描结果：批注 + 带锚点的正文。"""

    annotations: list[Annotation] = field(default_factory=list)
    clean_text: str = ""       # 带 ⟦A1⟧ 锚点的正文
    count: int = 0

    def by_intent(self) -> dict[str, list[Annotation]]:
        out: dict[str, list[Annotation]] = {}
        for a in self.annotations:
            out.setdefault(a.intent, []).append(a)
        return out


# ============================================================
# 意图与作用域
# ============================================================

def _detect_intent(text: str) -> tuple[str, str]:
    """判定意图。返回 (intent, 去掉前缀/锚定后的正文)。

    顺序：显式前缀 > 显式段落锚定 > 关键词。
    """
    body = text.strip()

    # 1) 显式前缀：`【新增：xxx】`
    m = _PREFIX_RE.match(body)
    if m:
        word = m.group(1)
        rest = body[m.end():].strip()
        if word in ("新增", "补充", "添加", "加"):
            return "insert", rest
        if word in ("删除", "删掉", "去掉"):
            return "delete", rest
        return "replace", rest

    # 2) 显式段落锚定：`【第5段：xxx】` → 剥掉锚定部分，
    #    剩下的内容再判意图（锚定本身不决定改还是增）
    m = _PARA_ANCHOR_RE.match(body)
    if m:
        rest = body[m.end():].strip()
        for intent, pat in _INTENT_KEYWORDS:
            if pat.search(rest):
                return intent, rest
        return "replace", rest

    # 3) 关键词
    for intent, pat in _INTENT_KEYWORDS:
        if pat.search(body):
            return intent, body
    return "replace", body


def _parse_para_anchor(text: str) -> int:
    """从批注里读出显式的段落锚定（1-based → 0-based）。返回 -1 表示没有。"""
    m = _PARA_ANCHOR_RE.match(text.strip())
    if not m:
        return -1
    num = m.group(1) or m.group(2)
    try:
        return max(0, int(num) - 1)
    except (TypeError, ValueError):
        return -1


def _is_annotation(content: str) -> bool:
    """判断方括号里的内容是不是批注（而不是正文里的引用/符号）。"""
    s = content.strip()
    if not s:
        return False
    if _NOT_ANNOTATION.match(s):
        return False
    return True


def _line_annotations(raw_line: str) -> list[re.Match[str]]:
    """找出这一行里所有批注（按出现顺序）。"""
    out: list[re.Match[str]] = []
    for m in ANNOTATION_RE.finditer(raw_line):
        content = m.group(1) if m.group(1) is not None else m.group(2)
        if content is None:
            continue
        if m.group(2) is not None and not _HALF_BRACKET_HINT_RE.search(content):
            # 半角括号且不像指令 → 正文里的方括号，不算批注
            continue
        if not _is_annotation(content):
            continue
        out.append(m)
    return out


def _is_annotation_only(raw_line: str, matches: list[re.Match[str]]) -> bool:
    """这一行除了批注之外还有没有别的内容？"""
    rest = raw_line
    for m in matches:
        rest = rest.replace(m.group(0), "", 1)
    return not rest.strip()


# ============================================================
# 主入口
# ============================================================

def scan_annotations(text: str) -> AnnotationScan:
    """扫描正文，标定每条批注的作用域，生成带锚点的正文。

    ## 步骤

    1. **逐行找批注**——分出行内批注与独占行批注
    2. **算段落表**——排除「只有批注」的行，得到真正的段落序列
    3. **标定作用域**——
       - 行内 → 本段
       - 独占行·修改/删除 → 上一段（作者先看到内容再写意见）
       - 独占行·新增 → 该位置（插入点）
       - 显式【第N段】→ 第 N 段
    4. **放锚点**——
       - 修改/删除类 → 锚点挪到**目标段落末尾**（模型一眼看到要改哪段）
       - 新增类 → 锚点留在**原位**（插入点就是锚点位置）
       - 行内 → 锚点留在段落内原位
    """
    if not text:
        return AnnotationScan(clean_text=text or "")

    lines = text.split("\n")

    # ---- 步骤 1：逐行找批注 ----
    line_matches: list[list[re.Match[str]]] = [
        _line_annotations(raw) for raw in lines
    ]
    # 独占行 = 这一行只有批注
    standalone: list[bool] = [
        bool(ms) and _is_annotation_only(lines[i], ms)
        for i, ms in enumerate(line_matches)
    ]

    # ---- 步骤 2：算段落表 ----
    # 段落 = 非空行 且 不是「只有批注」的行
    para_of_line: dict[int, int] = {}      # 行号 → 段落序号
    para_texts: list[str] = []             # 段落序号 → 段落原文
    for i, raw in enumerate(lines):
        if not raw.strip():
            continue
        if standalone[i]:
            continue                        # 纯批注行，不成段
        para_of_line[i] = len(para_texts)
        para_texts.append(raw.strip())

    # ---- 步骤 3：标定作用域 + 分配锚点 ----
    annotations: list[Annotation] = []
    marker_no = 0
    # 段落序号 → 追加在段落末尾的锚点（修改类）
    para_tail_markers: dict[int, list[str]] = {}
    # 段落序号 → 放在段落开头的锚点（删除类）
    para_head_markers: dict[int, list[str]] = {}
    # 行号 → 单独成行的锚点（新增类独占行）
    own_line_markers: dict[int, list[str]] = {}
    # 行号 → 行内锚点（行内批注，锚点留在原位）
    # 存 (start, end, marker)：start/end 是批注在**原始行**里的跨度，
    # 替换时要把整段批注换成锚点（不是只在位置插入）。
    inline_markers: dict[int, list[tuple[int, int, str]]] = {}
    # 落到文末的锚点（段号超出范围时用）——不能丢，否则模型看不到这条批注。
    end_markers: list[str] = []

    for i, ms in enumerate(line_matches):
        if not ms:
            continue
        # 上一段（修改类默认作用域）
        prev_para = -1
        for j in range(i - 1, -1, -1):
            if j in para_of_line:
                prev_para = para_of_line[j]
                break

        for m in ms:
            content = (m.group(1) if m.group(1) is not None else m.group(2)) or ""
            intent, body = _detect_intent(content)
            explicit_para = _parse_para_anchor(content)
            marker_no += 1
            marker = f"A{marker_no}"

            ann = Annotation(
                text=body,
                intent=intent,
                marker=marker,
                line=i + 1,
                inline=not standalone[i],
            )

            if explicit_para >= 0:
                # 显式锚定：目标就是那一段
                ann.target_para = explicit_para
                ann.para = explicit_para
                if explicit_para >= len(para_texts):
                    # 作者写了个不存在的段号（比如【第9段】但只有 3 段）。
                    # 绝不能把这批注丢掉——锚点必须落地，否则模型根本看不到它，
                    # 作者会以为「我明明写了怎么没改」。
                    # 超出范围时退到文末：修改/删除落到最后一段，新增挂到文末。
                    ann.anchor_note = (
                        f"原文只有 {len(para_texts)} 段，第 {explicit_para + 1} 段不存在，"
                        "已退到文末处理")
                    ann.target_para = len(para_texts) - 1
                    ann.para = ann.target_para
                    if intent == "insert":
                        ann.intent = "insert"
                        ann.target_para = -1
                        end_markers.append(marker)
                    elif intent == "delete":
                        para_head_markers.setdefault(ann.target_para, []).append(marker)
                    else:
                        para_tail_markers.setdefault(ann.target_para, []).append(marker)
                elif intent == "delete":
                    para_head_markers.setdefault(explicit_para, []).append(marker)
                else:
                    para_tail_markers.setdefault(explicit_para, []).append(marker)
            elif not standalone[i]:
                # 行内：改本段，锚点留在原位
                ann.para = para_of_line.get(i, -1)
                ann.target_para = ann.para
                inline_markers.setdefault(i, []).append((m.start(), m.end(), marker))
            elif intent == "insert":
                # 独占行·新增：位置型，锚点留在原位（独占一行）
                ann.target_para = -1
                ann.para = prev_para
                own_line_markers.setdefault(i, []).append(marker)
            else:
                # 独占行·修改/删除：指向上一段
                ann.target_para = prev_para
                ann.para = prev_para
                if prev_para >= 0:
                    if intent == "delete":
                        # 删除整段 → 锚点放在段首（模型看到就知道这一段要拿掉）
                        para_head_markers.setdefault(prev_para, []).append(marker)
                    else:
                        para_tail_markers.setdefault(prev_para, []).append(marker)
                else:
                    # 上一段不存在（批注在文首）→ 退化成位置型
                    ann.intent = "insert"
                    ann.target_para = -1
                    own_line_markers.setdefault(i, []).append(marker)

            annotations.append(ann)
            # 记下作用域对应的原文——后置校验用它区分
            # 「模型新写的」和「原文本来就有的」。
            if ann.target_para >= 0 and ann.target_para < len(para_texts):
                ann.source_text = para_texts[ann.target_para]

    # ---- 步骤 4：重建正文 ----
    out_lines: list[str] = []
    for i, raw in enumerate(lines):
        if standalone[i]:
            # 纯批注行 → 整行删掉；新增类的锚点单独成行插在这里
            for marker in own_line_markers.get(i, []):
                out_lines.append(f"⟦{marker}⟧")
            continue

        # 行内批注 → 整段替换成锚点（从后往前替换，避免位移）
        line = raw
        for start, end, marker in sorted(inline_markers.get(i, []),
                                         key=lambda x: -x[0]):
            line = line[:start] + f"⟦{marker}⟧" + line[end:]

        if i in para_of_line:
            _p = para_of_line[i]
            # 段落开头锚点（删除类）
            head = "".join(f"⟦{m}⟧" for m in para_head_markers.get(_p, []))
            if head:
                line = head + line.lstrip()
            # 段落末尾锚点（修改类）
            for marker in para_tail_markers.get(_p, []):
                line = line.rstrip() + f"⟦{marker}⟧"

        out_lines.append(line)

    # 段号超出范围而退到文末的锚点——必须落地，不能丢
    for marker in end_markers:
        out_lines.append(f"⟦{marker}⟧")

    return AnnotationScan(
        annotations=annotations,
        clean_text="\n".join(out_lines).strip(),
        count=len(annotations),
    )


# ============================================================
# 注入与校验
# ============================================================

def render_annotation_block(annotations: list[Annotation]) -> str:
    """把批注渲染成注入 prompt 的一段。

    清单按锚点编号列出——正文里的 ⟦A1⟧ 与清单里的 A1 一一对应，
    模型不用数段落。
    """
    if not annotations:
        return ""
    lines = [
        "【作者批注·最高优先级·必须逐条落实】",
        "作者在正文里用【】写了修改意见，正文里对应的位置留了 ⟦A1⟧ 这样的锚点标记。",
        "每一条都要在锚点处落实，然后把锚点标记本身删掉——**成稿里不能出现 ⟦⟧ 或【】**。",
        "",
        "批注清单：",
    ]
    for a in annotations:
        line = (f"{a.marker}（{INTENT_LABELS.get(a.intent, a.intent)}·{a.scope_label}）"
                f"{a.text}")
        if a.anchor_note:
            line += f"  〔注：{a.anchor_note}〕"
        lines.append(line)
    lines.append("")
    lines.append(
        "落实方式："
        "「新增」= 在锚点位置插入内容（承接上下文，别突兀）；"
        "「修改」= 只重写锚点所属那一段里相关的部分；"
        "「删除」= 整段删掉（含它的锚点）。"
        "**没有被批注的段落保持原样**——不要借机大改别处。"
        "全文输出改完后的完整正文，不要留任何锚点标记，也不要输出说明。"
    )
    return "\n".join(lines)


def strip_leftover_markers(text: str) -> tuple[str, int]:
    """清掉成稿里残留的锚点标记。返回 (清理后文本, 清掉的数量)。

    模型有时会忘记删锚点。残留的 ⟦A1⟧ 在正文里很扎眼，
    必须清掉——但要告诉作者「有几条批注可能被漏改」。
    """
    if not text:
        return text, 0
    cleaned, n = MARKER_RE.subn("", text)
    # 顺带清掉漏网的【】（模型可能把批注原样抄了回来）
    cleaned = ANNOTATION_RE.sub("", cleaned)
    return cleaned, n


def unmet_annotations(original: list[Annotation], polished: str) -> list[Annotation]:
    """检查哪些批注在成稿里没被落实。

    ## 怎么判断「落实了」

    不能靠字面比对——模型会用自己的话改写。作者写「改成踹开门」，
    成稿里是「他一脚踹开」，字面一个字都对不上。

    所以用**关键词覆盖**：从批注里抽出实词（去掉「改成」「这里」这类虚词），
    看成稿里有没有出现其中一部分。

    ## 关键：实词必须是**原文没有的**

    只看「成稿里有没有这个词」会误判：
    批注「改成踹开门」，原文是「他推开门」——
    成稿「他推开门。屋里没人。」里也有「开门」，
    但这条**显然没落实**（模型压根没动这句）。
    所以要先排除掉原文本来就有的片段，剩下的才是
    「模型新写出来的证据」。

    ## 删除类单独判

    删除批注的落实标志是**目标段消失了**，不是出现了什么词。
    所以看目标段里的特征词还在不在。

    这是**启发式**，只用来提示作者「这条可能没改」，不是判定失败。
    宁可漏报（模型确实改了但用词不同），不要误报（作者白白再改一遍）。
    """
    if not original:
        return []
    if not polished:
        return list(original)

    unmet: list[Annotation] = []
    for a in original:
        if a.intent == "delete":
            # 删除类：目标段的特征词应该不见了。
            # 只用 3 字以上的片段——「一声」「响了」这种 2 字词在别的段落
            # 也会出现，拿它判「段还在」会误报。
            src_tokens = _tokenize(a.source_text)
            distinctive = [w for w in src_tokens if len(w) >= 3]
            if not distinctive:
                continue
            if any(w in polished for w in distinctive):
                unmet.append(a)
            continue

        source = a.source_text or ""
        # 实词 = 批注内容去掉虚词后的片段
        words = [w for w in _tokenize(_strip_directive(a.text)) if w not in _STOP_WORDS]
        if not words:
            continue    # 没有实词可查——不判未落实
        # 只保留「原文里没有」的片段——它们才是模型新写的证据
        novel = [w for w in words if w not in source]
        if not novel:
            # 批注的关键词原文全有——字面判不了，不报（避免误报）
            continue
        if not any(w in polished for w in novel):
            unmet.append(a)
    return unmet


#: 虚词——只表达指令方式，不承载「要写什么」的信息。
_STOP_WORDS = {
    "这里", "此处", "这段", "这句", "那个", "这个", "改成", "改为", "改写",
    "调整", "替换", "换成", "一下", "一点", "一些", "补充", "增加", "添加",
    "写个", "写一段", "写一句", "加个", "加一段", "加一句", "删掉", "删除",
    "去掉", "不要", "应该", "需要", "可以", "最好", "记得", "注意", "感觉",
    "像是", "有点", "更", "很", "太", "把", "让", "的", "了", "是", "在",
    "和", "与", "就", "都", "也", "还", "要", "有", "个", "上", "下",
    "第", "段", "这", "那", "他", "她", "它", "我", "你",
}

#: 指令前缀——分词前先剥掉，否则「改成踹开门」会被切成「改成踹」这种废片段。
_LEAD_DIRECTIVE_RE = re.compile(
    r"^\s*(?:"
    r"改成|改为|改写|换成|替换|重写|调整|修改|删掉|删除|去掉|"
    r"增加|增补|补充|补上|添加|插入|加上|加|"
    r"这里要|这里|此处|这一段|这段|这句|"
    r"要有|要写|要|写一个|写一段|写个|写|"
    r"有问题|不对|不好|"
    r")"
)


def _strip_directive(text: str) -> str:
    """剥掉批注开头的指令词，留下真正要写的内容。

    「改成踹开门」→「踹开门」
    「这里要增加他检查弹匣」→「他检查弹匣」
    """
    out = (text or "").strip()
    for _ in range(6):        # 剥多层：「这里要增加」要剥两轮
        m = _LEAD_DIRECTIVE_RE.match(out)
        if not m:
            break
        out = out[m.end():].strip()
    return out


def _tokenize(text: str) -> list[str]:
    """从中文里抽出可检索的词块。

    不做真正的分词——中文分词需要词典，这里用「连续汉字块」+「长词优先」：
    「踹开门」→ ["踹开门", "踹开", "开门"]，
    这样成稿里出现任何一个片段都能命中。
    """
    chunks = re.findall(r"[一-鿿]{1,}", text or "")
    out: list[str] = []
    for c in chunks:
        out.append(c)
        if len(c) > 2:
            for i in range(len(c) - 1):
                out.append(c[i:i + 2])
        if len(c) > 3:
            for i in range(len(c) - 2):
                out.append(c[i:i + 3])
    return out
