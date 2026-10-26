"""描写语义场账本（lexicon.json）：跨章记录「同一语义场」的重复。

设计依据（圆桌 + xt-webnovel-writing/oh-story 双向实证）：
- 圆桌：生成前注入「你已经写过什么」当记忆，不是禁令；带上下文而非只给频次
- xt-webnovel-writing B-补充：同一语义场 500 字窗内最多保留 1 次
- oh-story banned-words.md：「禁止同义词轮换规避——同义词轮换消除不了模板」

⚠️ 关键纠偏（2026-10-21）：
前一版按「词」记账（嗓子/喉咙/舌根是三条），等价于鼓励同义词轮换——
这在检测器眼里全是同一个「吞咽」语义场，属于第二层 AI 味。
本版改为按**语义场**记账：同场的成员词共享一个额度。

落盘：backend/data/projects/{pid}/lexicon.json（与 entities.json 同级）
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ============================================================
# 语义场定义：同一感受/动作的不同写法，共享一个额度
# 场名 -> 成员词（成员之间互相顶替额度，不是互相补充）
# ============================================================
#
# ⚠️ **成员词必须能独立表达那个感受**（2026-10-24 修正）
#
# 上一版塞了大量单字：「压」「重」「沉」「热」「温」「湿」「潮」「灯」。
# 这些字在正常中文里根本不表达那个感受：
#   「压低声音」「血压高」「她压了一下他的肩」——「压」是普通动词，不是「沉重」的描写；
#   「温度」「温和」「室温」——「温」是普通名词；
#   「灯」更是任何场景都会出现的普通物件。
# 账本把它们全记成「沉重/温热/潮湿/灯具」的复用，
# 于是一章下来报出「沉重 已用 12 次」这种毫无意义的提示，
# 作者以为自己的描写出问题了，其实是他写了「他压低声音」而已。
#
# 判定标准：**这个词单独出现，读者能读出那个感受吗？**
# 不能 → 换成必须成词才成立的写法（两字词优先）。
# ============================================================

SEMANTIC_FIELDS: dict[str, list[str]] = {
    # ── 身体：吞咽/干呕 ──
    "吞咽紧张": ["喉咙发紧", "嗓子发干", "嗓子眼", "舌根发紧", "咽了口唾沫",
                 "口腔发干", "唾沫", "干呕", "喉结滚动", "吞咽"],
    # ── 身体：手部动作 ──
    "手部小动作": ["指腹", "指尖", "手指", "指节", "指缝", "拇指", "掌心", "手心",
                   "手背", "虎口", "腕骨", "手腕"],
    # ── 身体：视线 ──
    "视线打量": ["眼神", "目光", "视线", "盯着", "扫了一眼", "打量"],
    # ── 身体：僵/紧 ──
    "肌肉紧绷": ["发僵", "僵住", "绷紧", "紧绷", "发紧", "僵硬"],
    # ── 感官：冷 ──
    # 只收**成词**的冷感：「凉」单独太泛（「凉了」是时间/语气），
    # 「冷」同理（「冷了」「冷笑」「冷场」都是别的意思）。
    "寒冷": ["湿冷", "透凉", "冰凉", "寒意", "起鸡皮疙瘩", "冻僵", "冷得", "发冷",
             "冷水", "冰冷"],
    # ── 感官：潮/湿 ──
    # 「湿」单字在「湿了」「湿地」里不一定是潮湿感，但成词的「潮湿/返潮/潮气」很准；
    # 保留「湿漉漉」「湿透」这类必然是潮感的。
    "潮湿": ["潮湿", "潮气", "返潮", "发潮", "黏腻", "湿漉漉", "湿透", "水汽"],
    # ── 感官：干燥 ──
    "干燥": ["干涩", "发干", "口干", "干裂", "干燥", "嗓子发干"],
    # ── 感官：热 ──
    # 「热」单字在「热了」「热点」里不表达温度感；只收成词的。
    "温热": ["滚烫", "灼热", "发烫", "温热", "烫手", "温吞", "热腾腾", "冒热气"],
    # ── 感官：重/沉 ──
    # **「压」已移除**：它是普通动词（压低声音/按压/血压），不是沉重感的描写。
    # 「沉」单字在「沉下脸」里是表情，不是重量感；只收成词的。
    "沉重": ["沉重", "沉甸甸", "坠着", "压得喘不过气", "重得", "千斤重", "坠下去"],
    # ── 情绪：抽象概括（AI 高发区） ──
    "抽象情绪": ["涌上", "涌起", "五味杂陈", "百感交集", "难以言喻", "不寒而栗",
                 "毛骨悚然", "心口一紧", "泛起"],
    # ── 环境：门/入口 ──
    #
    # ⚠️ **「防盗门」「门缝」已移除**（2026-10-26 定稿反推）
    # 实测：第一章定稿里「防盗门」出现 4 次、「门缝」2 次，全都在**同一场**
    # （主角半夜起来看声控灯）——那是这一场的**签名道具**，不是复读。
    # 之前把这几个词记进门框语义场，于是定稿被报成「门口 2 次窗内复读」。
    # 保留「门口/门槛」这类真正指位置的名词。
    "门口": ["门口", "门前", "门槛", "单元门", "大门"],
    # ── 环境：灯 ──
    # **「灯」单字已移除**：普通名词，几乎每场都有，记账无意义。
    # 只收具体的灯具名。
    "灯具": ["声控灯", "灯管", "灯泡", "路灯", "台灯", "应急灯", "走廊灯", "吊灯"],
    # ── 环境：窗 ──
    "窗户": ["窗外", "窗台", "窗帘", "窗玻璃", "窗户"],
    # ── 环境：楼道 ──
    "楼道": ["楼道", "走廊", "过道", "楼梯", "台阶"],
}

# 抽象气场词（xt B-补充「气场词簇」）：这些是模板复读的重灾区
AURA_TOKENS = [
    "掠食者", "猩红", "宿命", "战栗", "冷冽", "深渊", "幽暗", "诡异", "可怖",
    "压迫感", "窒息", "凝固", "弥漫", "笼罩", "弥漫着",
]

_ACTION_VERBS = [
    "起来", "过来", "下去", "上去", "走了", "站着", "坐着", "看着", "听着",
    "伸手", "收回", "转身", "抬头", "低头", "点头", "摇头", "抓起", "放下",
    "摸出", "掏出", "递", "推", "拉", "打开", "关上", "起身", "蹲下", "弯腰",
    "按住", "踩", "盯着", "扫了眼", "顿了顿", "皱了", "叹了",
]

# 一个语义场的额度窗口（字）；超出即视为语义场复读
FIELD_WINDOW_CHARS = 500


# ============================================================
# 推进位 / 驻留位：把「重复」和「复读」分开
# ============================================================
#
# ⚠️ 为什么必须分开（2026-10-26，定稿反推）
#
# 人工定稿里同一个词大量重复：「泥」在第一章出现 9 次，
# 「水」「潮」「雨」「腥」也反复出现——但每次**所指不同**
# （靴筒的泥 / 指缝的泥 / 泥里的细砂 / 指甲里的泥 / 纸上的黑泥）。
# 人工写作里这是**抓住一个词不放**，因为那个词就是他在想的东西。
#
# 而人工定稿里真正该被拦下来的是另一种：
# 同一个**信息**换个近义词说第二遍（「喉咙发紧」→「嗓子发干」）。
#
# 字面去重测的是前者，信息级去重测的是后者。
# 上一版按字面去重，会把定稿判成复读——这就是规则和定稿打架的地方。
#
# **推进位（advance）**：这次调用让状态**发生了变化**（东西被拿起/打开/看见/知道）。
# **驻留位（dwell）**：这次调用只是氛围（又写了一遍同样的感受）。
#
# 判定：**驻留位才计数，推进位不计数。**
# 这样手部动作可以连着做八次（每次都推进了一步），
# 而「又写了一遍同样的感受」会被报出来。
#
# 怎么判推进：段落里出现了**状态变化动词**或**新信息**就算推进。
# 判不准就往「推进」那边靠——宁可漏报，不要误伤定稿。
#
# ⚠️ 关键：必须先看「驻留信号」，再看「推进信号」。
# 段落里同时有两者时，**驻留优先**——
# 因为「他又按了按门把手」这种句子，动作本身是新的，但**写的是同一件事**
# （还是那个门把手、还是那个动作），属于复读。
# 反过来「他手指敲了下桌面」→「他的指腹按在门把手上」，
# 动词一样（敲/按）但**所指不同**，是推进。
ADVANCE_VERBS = [
    # 物件状态变化
    "拿起", "放下", "掏出", "摸出", "递", "塞", "抓", "拽", "推", "拉", "掀",
    "拆", "翻", "打开", "合上", "拧", "按", "敲", "踩", "拎", "端", "掰",
    "折", "撕", "捏", "搓", "揉", "塞进", "取出", "收好", "扔", "丢", "接住",
    # 感知状态变化
    "看见", "看到", "看清", "认出", "发现", "听见", "听到", "闻见", "摸到",
    "撞见", "瞥见", "盯住",
    # 认知状态变化
    "想起", "明白", "意识到", "想起来",
    # 位置/朝向变化
    "起身", "坐下", "站起", "转身", "上前", "凑近", "挪",
    # 交互变化
    "挂断", "接通", "拔掉", "插上",
]

#: 明确是「驻留」的信号：只是重复渲染同一状态，没有任何新东西。
DWELL_MARKERS = [
    "又", "还是", "依旧", "仍然", "依然", "照样", "再度", "再次",
]


def classify_hits(para: str) -> str:
    """判断这个段落里对某语义场的调用是「推进」还是「驻留」。

    返回 "advance" / "dwell"。

    判据（从定稿反推）：
    1. 段落里有**驻留信号**（又/还是/依旧…）→ dwell
       （哪怕同时有动作动词——「他又按了按门把手」写的是同一件事）
    2. 段落里有**状态变化动词**（拿起/打开/看见/知道）→ advance
    3. 判不准 → advance（宁可漏报，不要误伤定稿）
    """
    if any(m in para for m in DWELL_MARKERS):
        return "dwell"
    if any(v in para for v in ADVANCE_VERBS):
        return "advance"
    # 无信号：默认推进。理由——定稿里绝大多数重复词都是「抓着不放」，
    # 只有明显是「又写一遍」时才需要报。
    return "advance"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _scene_label(para: str) -> str:
    """段落场景标签（用于 Top6 提示的可读性）。"""
    has_dialog = ("「" in para) or ("」" in para)
    has_action = any(v in para for v in _ACTION_VERBS)
    has_digit = bool(re.search(r"\d", para))
    if has_dialog and has_action:
        return "对白+动作"
    if has_dialog:
        return "对白"
    if has_action:
        return "动作"
    if has_digit:
        return "含数字锚点"
    if len(para) <= 12:
        return "极短段/停顿"
    return "叙述"


# 场 -> 成员词的快速索引
_TERM_TO_FIELD: dict[str, str] = {}
for _fname, _members in SEMANTIC_FIELDS.items():
    for _m in _members:
        _TERM_TO_FIELD.setdefault(_m, _fname)
for _a in AURA_TOKENS:
    _TERM_TO_FIELD.setdefault(_a, "抽象气场词")


@dataclass
class FieldUsage:
    """一个语义场在某一章的使用记录。"""
    field: str
    chapter: int
    hits: list[dict] = field(default_factory=list)   # [{para, char_pos, term, scene}]

    def as_dict(self) -> dict:
        return {"field": self.field, "chapter": self.chapter, "hits": self.hits}

    @classmethod
    def from_dict(cls, d: dict) -> "FieldUsage":
        return cls(field=d.get("field", ""), chapter=int(d.get("chapter", 0) or 0),
                   hits=list(d.get("hits") or []))


class LexiconStore:
    """语义场账本：按语义场记账 + 窗口内重复判定 + 记忆式注入。"""

    # 单章内某语义场命中次数 ≥ 此值才纳入账本（避免噪声）
    MIN_HITS_PER_CHAPTER = 3
    # Top-N 提示数量
    TOP_N = 6
    # 注入时每个场最多列几个成员词
    INJECT_MEMBERS = 4
    # **窗口内复读多少次才算模板复读**（2026-10-24）
    # 同一语义场在 500 字窗内被调用 1-2 次是正常描写密度；
    # ≥3 次才说明模型在复读同一个模板。
    MIN_REPEAT_CALLS = 3
    # 跨章累计多少次才算全书级别的复读（配合 MIN_REPEAT_CALLS=2 时用）
    MIN_REPEAT_TOTAL = 8

    def __init__(self, path: Path | str):
        self.path = Path(path)
        # field_name -> {chapter -> FieldUsage}
        self.data: dict[str, dict[int, FieldUsage]] = defaultdict(dict)
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                payload = json.loads(raw)
                for fname, chs in (payload.get("fields") or {}).items():
                    for chs_i, u in (chs or {}).items():
                        fu = FieldUsage.from_dict(u)
                        self.data[fname][int(chs_i)] = fu
            except (json.JSONDecodeError, TypeError, OSError, AttributeError, ValueError):
                pass
        self._drop_stale_hits()

    def _drop_stale_hits(self) -> None:
        """清掉「成员词已从语义场移除」的历史命中。

        ## 为什么需要（2026-10-24）

        语义场成员词会随修正而变（「压/重/沉」这类高歧义单字被移除）。
        旧账本里存着按旧成员词记录的命中——那些命中**不再代表**当前语义场的语义，
        却仍会被算进「全书累计」和「窗内复读」，于是账本继续报
        「沉重 已用 12 次」这种基于旧口径的数字。

        修法：加载时按**当前**成员表过滤一遍。
        旧成员词记录的历史命中直接丢弃——因为这些章会重新生成，
        新一轮 record_chapter 会按新口径重新记账。
        """
        changed = False
        for fname, chs in list(self.data.items()):
            members = set(SEMANTIC_FIELDS.get(fname, []))
            if not members:
                # 整个场被删了 → 整场丢弃
                del self.data[fname]
                changed = True
                continue
            for ch, fu in list(chs.items()):
                kept = [h for h in fu.hits if h.get("term") in members]
                if len(kept) != len(fu.hits):
                    changed = True
                    if kept:
                        fu.hits = kept
                    else:
                        del chs[ch]
        if changed:
            try:
                self._flush()
            except OSError:
                pass

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "note": "语义场账本：同场成员词共享额度，禁止同义词轮换规避",
            "updated_at": _now(),
            "field_window_chars": FIELD_WINDOW_CHARS,
            "fields": {
                fname: {str(ch): fu.as_dict() for ch, fu in sorted(chs.items())}
                for fname, chs in sorted(self.data.items())
            },
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 记录 ----

    def record_chapter(self, text: str, chapter: int) -> dict:
        """扫描一章，按语义场记账（不是按词）。返回本章摘要。"""
        if not text or not text.strip():
            return {"recorded": 0, "fields": [], "violations": []}
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        # 累计每个场在本章的命中（按字符位置记录，用于窗口判定）
        field_hits: dict[str, list[dict]] = defaultdict(list)
        offset = 0
        for pi, para in enumerate(paras):
            # 这一段对所有语义场的调用是「推进」还是「驻留」——决定它算不算复读
            slot = classify_hits(para)
            for term, fname in _TERM_TO_FIELD.items():
                start = 0
                while True:
                    idx = para.find(term, start)
                    if idx < 0:
                        break
                    field_hits[fname].append({
                        "para": pi,
                        "char_pos": offset + idx,
                        "term": term,
                        "scene": _scene_label(para),
                        "slot": slot,
                    })
                    start = idx + len(term)
            offset += len(para) + 1  # +1 for newline

        recorded = []
        for fname, hits in field_hits.items():
            if len(hits) < self.MIN_HITS_PER_CHAPTER:
                continue
            hits.sort(key=lambda h: h["char_pos"])
            self.data[fname][chapter] = FieldUsage(field=fname, chapter=chapter, hits=hits)
            recorded.append(fname)

        # 窗口内重复检测（同一场内两次命中间隔 < 500 字 = 复读）
        #
        # **同段内的多次命中不算复读**（2026-10-24 修正）：
        # 一句描写里出现多个同类部位词（「手背」「手指」「掌心」）是**一次**描写，
        # 不是多次复读。上一版把它们逐对算成违规，
        # 于是「他低头看自己的手背、手指、掌心」这样一句正常描写
        # 会被报成 3 次复读——这就是「手部小动作 已用 17 次」的来源。
        #
        # **只有「驻留位」才算复读**（2026-10-26 修正，定稿反推）：
        # 人工定稿里「泥」出现 9 次但每次所指不同——那是作者抓着这个词不放，
        # 不是模板复读。真正的复读是**同一个信息换个近义词说第二遍**。
        # 所以推进位（东西被拿起/打开/知道了）不计数，只有驻留位计数。
        #
        # 复读的定义：**同一语义场在两个不同段落里被反复「驻留」**。
        # 判定时按段落去重，而不是按命中次数。
        violations = []
        for fname in recorded:
            fu = self.data[fname][chapter]
            # 按段落归组：每段只算一次出现；推进位不参与
            by_para: dict[int, list[dict]] = defaultdict(list)
            for h in fu.hits:
                if h.get("slot", "advance") == "dwell":
                    by_para[h["para"]].append(h)
            para_poses = sorted(
                ((p, min(h["char_pos"] for h in hs), hs)
                 for p, hs in by_para.items()),
                key=lambda x: x[1])
            for (pa, pos_a, hits_a), (pb, pos_b, hits_b) in zip(para_poses, para_poses[1:]):
                if pos_b - pos_a < FIELD_WINDOW_CHARS:
                    violations.append({
                        "field": fname,
                        "gap": pos_b - pos_a,
                        "a": {"para": pa, "term": hits_a[0]["term"]},
                        "b": {"para": pb, "term": hits_b[0]["term"]},
                    })

        if recorded:
            self._flush()
        return {
            "recorded": len(recorded),
            "fields": sorted(recorded, key=lambda f: -len(self.data[f][chapter].hits)),
            "violations": violations,
        }

    # ---- 查询 ----

    def window_violations(self, chapter: int) -> list[dict]:
        """本章内所有语义场窗口复读违规（跨章累计次数也一并看）。

        与 record_chapter 同规则：
        1. **按段落去重**——同一段里多次命中同一语义场算一次出现；
        2. **只算驻留位**——推进位（东西被拿起/打开/知道了）不计入复读，
           因为人工定稿里「泥」这类词反复出现是作者在抓着它不放，
           不是模板复读。
        """
        out = []
        for fname, chs in self.data.items():
            fu = chs.get(chapter)
            if not fu:
                continue
            total_across = sum(len(v.hits) for v in chs.values())
            by_para: dict[int, list[dict]] = defaultdict(list)
            for h in fu.hits:
                if h.get("slot", "advance") == "dwell":
                    by_para[h["para"]].append(h)
            para_poses = sorted(
                ((p, min(h["char_pos"] for h in hs), hs)
                 for p, hs in by_para.items()),
                key=lambda x: x[1])
            for (pa, pos_a, hits_a), (pb, pos_b, hits_b) in zip(para_poses, para_poses[1:]):
                if pos_b - pos_a < FIELD_WINDOW_CHARS:
                    out.append({
                        "field": fname,
                        "gap": pos_b - pos_a,
                        "term_a": hits_a[0]["term"],
                        "para_a": pa,
                        "term_b": hits_b[0]["term"],
                        "para_b": pb,
                        "total_across_book": total_across,
                    })
        out.sort(key=lambda x: x["gap"])
        return out

    def top_n_for_chapter(self, chapter: int, n: int | None = None) -> list[dict]:
        """Top-N 提示：按「本章违规次数 + 累计次数」排序。

        **只报真正构成「模板复读」的场**（2026-10-24）：
        同一语义场在窗口内被调用 1 次是正常的（「他手心有点汗」后文又写一次手），
        只有**反复调用**才是模板复读。所以设一个下限：
          - 窗口内复读 ≥ MIN_REPEAT_CALLS（3）次 → 报；
          - 或 窗口内复读 ≥ 2 次 且全书累计 ≥ MIN_REPEAT_TOTAL（8）次 → 报（跨章复读）。

        否则作者会收到一堆「用了一次也叫复读」的噪音，
        真正的问题反而被淹没了——这是「描写账本提示太频繁」的直接原因。
        """
        n = n or self.TOP_N
        agg: dict[str, dict] = {}
        for v in self.window_violations(chapter):
            f = v["field"]
            if f not in agg:
                agg[f] = {
                    "field": f, "violations": 0, "min_gap": v["gap"],
                    "total_across_book": v["total_across_book"],
                    "samples": [], "members": SEMANTIC_FIELDS.get(f, [])[:6],
                }
            agg[f]["violations"] += 1
            agg[f]["min_gap"] = min(agg[f]["min_gap"], v["gap"])
            if len(agg[f]["samples"]) < 3:
                agg[f]["samples"].append(
                    f"第{v['para_a']}段「{v['term_a']}」/ 第{v['para_b']}段「{v['term_b']}」")
        out = sorted(agg.values(), key=lambda x: (-x["violations"], x["min_gap"]))
        # 过滤掉「用了一次也叫复读」的噪音
        return [t for t in out
                if t["violations"] >= self.MIN_REPEAT_CALLS
                or (t["violations"] >= 2
                    and t["total_across_book"] >= self.MIN_REPEAT_TOTAL)][:n]

    # ---- 生成前注入（给模型当记忆） ----

    def memory_block(self, chapter: int, max_chars: int = 900) -> str:
        """生成注入 prompt 的记忆块：叙事性措辞，不是禁令。

        关键：不说「这些词不要用」，而说「这些感受你已经写过，读者记得；
        再写同一类感受时，换成具体的动作或场景证据，不要换个近义词再写一遍」。
        """
        prev: dict[str, int] = defaultdict(int)
        samples: dict[str, list[str]] = defaultdict(list)
        for fname, chs in self.data.items():
            for ch, fu in chs.items():
                if ch >= chapter:
                    continue
                total = len(fu.hits)
                prev[fname] += total
                for h in fu.hits[:2]:
                    if len(samples[fname]) < 3:
                        samples[fname].append(
                            f"第{ch}章·{h['scene']}·{h['term']}")
        if not prev:
            return ""
        ranked = sorted(prev.items(), key=lambda x: -x[1])[:8]
        if not ranked:
            return ""

        lines = ["【你已经写过的感受与场景（供你回忆）】"]
        for fname, cnt in ranked:
            members = SEMANTIC_FIELDS.get(fname, [])
            mem = "/".join(members[: self.INJECT_MEMBERS])
            samp = "、".join(samples[fname][:2]) if samples.get(fname) else "前面章节"
            lines.append(f"· {fname}（{mem}）：已写 {cnt} 次，例如 {samp}")
        lines.append(
            "写作提示：读者已经记住这些感受了。同一段场景里如果要写同一类感受，"
            "**不要换个近义词再写一遍**（那只会变成套路的另一种说法）——"
            "改用具体的动作、物件变化或环境证据来承担。"
            "签名级的重复（某个角色的专属习惯、某个线索的固定意象）可以保留，"
            "但不要在 500 字内重复同一类抽象感受。"
            "**注意：同一个词反复出现不一定是复读**——如果每次它指的不是同一件事"
            "（不同的泥、不同的水、不同的光），那是人在抓着一个词不放，"
            "**要保留**。只有当你在用不同的词说**同一件事**时，才是复读。"
        )
        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars] + "…"
        return block

    # ---- 生成后 Top-N 提示（给人看） ----

    def report_block(self, chapter: int, n: int | None = None) -> str:
        tops = self.top_n_for_chapter(chapter, n)
        if not tops:
            return ""
        lines = [f"【语义场复读提示·第{chapter}章】以下感受在本章 500 字窗内重复出现，"
                 f"请确认是刻意签名还是模板复读："]
        for t in tops:
            mem = "/".join(t["members"][:5])
            lines.append(
                f"- {t['field']}（{mem}）：{t['violations']} 次窗内复读"
                f"（最近间隔 {t['min_gap']} 字，全书累计 {t['total_across_book']} 次）；"
                f"例如 {'；'.join(t['samples'][:2])}"
            )
        lines.append(
            "→ 若是签名（角色标识/线索意象），保持；"
            "若是模板复读，**把其中 1-2 处换成具体的动作或环境证据，"
            "不要只换一个近义词**。"
        )
        return "\n".join(lines)


# ---- 工厂 ----

def load_lexicon(pid: str, projects_root: Path | str) -> LexiconStore:
    return LexiconStore(Path(projects_root) / pid / "lexicon.json")


def mechanical_redundancy(text: str) -> list[dict]:
    """纯机械冗余：同一表达在同一段内出现 >=2 次（笔误级，无需审美判断）。"""
    out: list[dict] = []
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    for i, para in enumerate(paras):
        for term in _TERM_TO_FIELD:
            c = para.count(term)
            if c >= 2:
                out.append({"term": term, "field": _TERM_TO_FIELD[term],
                            "para": i, "count": c})
    return out
