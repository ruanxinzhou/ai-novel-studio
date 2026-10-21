"""描写轮换账本（lexicon.json）：跨章记录高频表达的位置与间隔。

设计依据（圆桌共识）：
- 生成前注入给「模型」当记忆，而不是禁令（叙事性注入 vs 管理性注入）
- 每条记录带上下文（在第几段、什么场景），因为孤立频次会让模型收缩
- 不做「签名/惰性」分类（同一批词两个属性），只按间隔排序
- 不自动替换用词，只做排序 + Top6 提示

数据落盘：backend/data/projects/{pid}/lexicon.json（与 entities.json 平级）
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---- 描写类别词表（零 token，纯正则/词表匹配） ----

# 身体部位与感官动作（AI 最爱复用的那一批）
BODY_PARTS = [
    "指腹", "指尖", "手指", "指节", "指缝", "掌心", "手心", "手背", "虎口", "腕骨",
    "手腕", "手肘", "拳头", "指头", "拇指", "小指",
    "喉咙", "嗓子", "嗓子眼", "舌根", "舌头", "咽口", "口腔", "下颌", "下巴", "牙关",
    "脖颈", "脖子", "后颈", "颈椎", "肩膀", "肩胛", "锁骨", "胸口", "心口", "后背",
    "脊背", "腰椎", "肋骨", "后腰", "小腹", "胃部", "太阳穴", "额头", "眉心", "眼角",
    "眼皮", "眼睫", "瞳孔", "眼球", "脸颊", "腮帮",
]

SENSATION_WORDS = [
    "凉", "冷", "冰", "湿", "潮", "干", "热", "烫", "温", "暖", "燥", "黏", "涩", "滑",
    "麻", "痒", "疼", "痛", "胀", "紧", "松", "沉", "轻", "空", "满",
]

# 环境与物件（场景锚点，AI 会反复抓同一个）
ENV_WORDS = [
    "窗外", "门口", "门缝", "门框", "走廊", "楼道", "楼梯", "台阶", "电梯", "天花板",
    "地板", "墙面", "墙角", "窗台", "阳台", "路灯", "声控灯", "灯管", "灯泡",
    "走廊灯", "应急灯", "扶手", "栏杆", "通风口", "垃圾袋", "快递箱", "鞋柜",
    "楼道灯", "单元门", "铁门", "木门", "防盗门", "窗玻璃", "窗帘",
]

# 比喻句式模板（结构级重复，不是用词）
SIMILE_PATTERNS = [
    ("像+程序/代码类", re.compile(r"像[^，。！？]{0,10}(程序|代码|报错|指令|参数|bug|系统|数据|算法)")),
    ("像+自然类", re.compile(r"像[^，。！？]{0,8}(风|浪|水|雪|火|冰|雷|雨|云|树|山)")),
    ("像+人/角色类", re.compile(r"像[^，。！？]{0,8}(人|母亲|父亲|老师|医生|孩子|老人|傻子)")),
    ("仿佛/仿似", re.compile(r"(仿佛|仿似|好像|似乎)[^，。！？]{1,16}")),
]

# 副词堆叠检测（程度副词）
DEGREE_ADVERBS = [
    "很", "非常", "十分", "极其", "极为", "格外", "尤其", "相当", "颇", "尤其",
    "有些", "略微", "稍稍", "极度", "无比", "异常", "格外地",
]

_ACTION_VERBS = [
    "起来", "过来", "下去", "上去", "走了", "站着", "坐着", "看着", "听着",
    "伸手", "收回", "转身", "抬头", "低头", "点头", "摇头", "抓起", "放下",
    "摸出", "掏出", "递", "推", "拉", "打开", "关上", "起身", "蹲下", "弯腰",
    "按住", "踩", "盯着", "扫了眼", "顿了顿", "皱了", "叹了",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _han(n: int) -> str:
    return "[\u4e00-\u9fff]"


def _count_terms(text: str, terms: list[str]) -> dict[str, int]:
    return {t: text.count(t) for t in terms if text.count(t) > 0}


# ---- 场景标注（启发式，零 token） ----

def _scene_label(para: str) -> str:
    """用启发式给段落打一个粗粒度场景标签（用于 Top6 提示的可读性）。"""
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


@dataclass
class LexEntry:
    """一个高频表达的跨章记录。"""
    term: str
    category: str          # body / sensation / env / simile
    total: int = 0
    chapters: set[int] = field(default_factory=set)
    # chapter_no -> list of (para_idx, scene_label)
    spots: dict[int, list[tuple[int, str]]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "term": self.term,
            "category": self.category,
            "total": self.total,
            "chapters": sorted(self.chapters),
            "spots": {str(k): [[i, s] for i, s in v] for k, v in sorted(self.spots.items())},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LexEntry":
        spots = {}
        for k, v in (d.get("spots") or {}).items():
            try:
                spots[int(k)] = [(int(a), str(b)) for a, b in v]
            except (TypeError, ValueError):
                continue
        return cls(
            term=d.get("term", ""),
            category=d.get("category", "body"),
            total=int(d.get("total", 0) or 0),
            chapters=set(int(c) for c in (d.get("chapters") or [])),
            spots=spots,
        )

    def last_para_in(self, chapter: int) -> int:
        lst = self.spots.get(chapter) or []
        return max((i for i, _ in lst), default=-1)

    def density_in(self, chapter: int) -> int:
        return len(self.spots.get(chapter) or [])


class LexiconStore:
    """描写轮换账本：跨章累积 + Top6 排序 + 记忆式注入文本生成。"""

    # 单章内单术语出现次数超过此值，才纳入账本（避免噪声）
    MIN_PER_CHAPTER = 3
    # 进入 Top6 提示的门槛（单章内次数）
    TOP_N = 6
    # 「密集」判定：同一章内出现 >= 此值，且占该章段落比例 > ratio
    DENSE_COUNT = 4
    DENSE_RATIO = 0.06
    # 注入时每个类别最多列几个（控 token）
    INJECT_PER_CATEGORY = 6

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: dict[str, LexEntry] = {}
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8-sig")
                data = json.loads(raw)
                for d in data.get("entries", []):
                    e = LexEntry.from_dict(d)
                    if e.term:
                        self.entries[e.term] = e
            except (json.JSONDecodeError, TypeError, OSError, AttributeError):
                pass

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now(),
            "entries": [e.as_dict() for e in
                        sorted(self.entries.values(), key=lambda x: -x.total)],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 记录 ----

    def record_chapter(self, text: str, chapter: int) -> dict:
        """扫描一章正文，更新账本。返回本章的统计摘要。"""
        if not text or not text.strip():
            return {"recorded": 0, "terms": []}
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        n_paras = max(len(paras), 1)
        found: dict[str, tuple[str, list[tuple[int, str]]]] = {}

        for cat, terms in (("body", BODY_PARTS), ("sensation", SENSATION_WORDS),
                           ("env", ENV_WORDS)):
            for t in terms:
                cnt = text.count(t)
                if cnt < self.MIN_PER_CHAPTER:
                    continue
                spots: list[tuple[int, str]] = []
                for i, para in enumerate(paras):
                    c = para.count(t)
                    if c:
                        spots.append((i, _scene_label(para)))
                if spots:
                    found[t] = (cat, spots)

        for t, (cat, spots) in found.items():
            e = self.entries.get(t)
            if e is None:
                e = LexEntry(term=t, category=cat)
                self.entries[t] = e
            # 若已存在于其它章，重置本章 spots 但累计 total
            e.total += sum(1 for _ in spots)
            e.chapters.add(chapter)
            e.spots[chapter] = spots

        if found:
            self._flush()
        return {
            "recorded": len(found),
            "terms": sorted(found.keys(), key=lambda t: -len(found[t][1])),
            "para_count": n_paras,
        }

    # ---- 查询 ----

    def dense_terms(self, chapter: int, exclude_current: bool = False) -> list[dict]:
        """返回该章（或历史）内「密集」的表达：单章次数高且分布集中。"""
        out = []
        for e in self.entries.values():
            if exclude_current and chapter in e.spots:
                continue
            for ch, spots in e.spots.items():
                if exclude_current and ch == chapter:
                    continue
                if len(spots) < self.DENSE_COUNT:
                    continue
                idxs = [i for i, _ in spots]
                span = max(idxs) - min(idxs) + 1
                ratio = len(spots) / max(span, 1)
                if ratio >= self.DENSE_RATIO or len(spots) >= self.DENSE_COUNT + 2:
                    out.append({
                        "term": e.term,
                        "category": e.category,
                        "chapter": ch,
                        "count": len(spots),
                        "span": span,
                        "ratio": round(ratio, 3),
                        "samples": spots[:3],
                        "total_across_book": e.total,
                    })
        out.sort(key=lambda x: (-x["count"], x["span"]))
        return out

    def top_n_for_chapter(self, chapter: int, n: int | None = None) -> list[dict]:
        """给作者看的 Top-N 提示：按「单章次数 + 分布集中度」排序。"""
        n = n or self.TOP_N
        cands = self.dense_terms(chapter)
        # 同 term 多次出现的只保留最密集的一次
        seen: set[str] = set()
        uniq: list[dict] = []
        for c in cands:
            if c["term"] in seen:
                continue
            seen.add(c["term"])
            uniq.append(c)
            if len(uniq) >= n:
                break
        return uniq

    # ---- 生成前注入（给模型当记忆） ----

    def memory_block(self, chapter: int, max_chars: int = 900) -> str:
        """生成注入 prompt 的记忆块：只讲「你已经写过什么」，不带禁令口吻。

        关键：措辞是叙事性的（记忆），不是管理性的（禁令）。
        """
        if not self.entries:
            return ""
        by_cat: dict[str, list[LexEntry]] = defaultdict(list)
        for e in self.entries.values():
            # 只统计本章之前出现的（不含本章刚写的）
            prev_chs = [c for c in e.spots if c < chapter]
            if not prev_chs:
                continue
            total = sum(len(e.spots[c]) for c in prev_chs)
            if total < self.MIN_PER_CHAPTER:
                continue
            by_cat[e.category].append(e)
        if not by_cat:
            return ""

        cat_label = {
            "body": "身体部位与身体动作",
            "sensation": "感官与体感形容",
            "env": "环境与场景物件",
        }
        lines = ["【你在这个故事里已经用过的描写（供你回忆，不是禁用清单）】"]
        for cat in ("body", "sensation", "env"):
            items = sorted(by_cat.get(cat, []), key=lambda x: -x.total)[: self.INJECT_PER_CATEGORY]
            if not items:
                continue
            descs = []
            for e in items:
                # 给出 1-2 个实际用过的位置，带场景，模型才能回忆而不是被禁
                samples = []
                for c in sorted(e.spots):
                    if c >= chapter:
                        continue
                    for i, s in e.spots[c][:1]:
                        samples.append(f"第{c}章·{s}")
                    if len(samples) >= 2:
                        break
                loc = "、".join(samples) if samples else "前面章节"
                descs.append(f"{e.term}（{loc}）")
            lines.append(f"- {cat_label[cat]}：{'；'.join(descs)}")
        lines.append(
            "写作提示：这些是你已经写过的，读者的印象里已经有了。"
            "同一段场景里不必反复重申它们；"
            "如果这次情绪确实需要更强的力度，可以换一个更具体的说法，"
            "或者干脆用动作/物件的变化来承担，而不是重复同一个词。"
        )
        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars] + "…"
        return block

    # ---- 生成后 Top6 提示（给人看） ----

    def report_block(self, chapter: int, n: int | None = None) -> str:
        tops = self.top_n_for_chapter(chapter, n)
        if not tops:
            return ""
        lines = [f"【重复描写提示·第{chapter}章】以下表达在本章出现较密，请确认是刻意重复（签名/线索）还是无意复读："]
        for t in tops:
            locs = "、".join(f"第{i}段（{s}）" for i, s in t["samples"][:3])
            lines.append(
                f"- 「{t['term']}」本章 {t['count']} 处（{locs}），"
                f"全书累计 {t['total_across_book']} 次"
            )
        lines.append("→ 若是刻意的（角色标识/悬念线索/仪式感节奏），保持；若是无意复读，替换其中 1-2 处为不同的写法即可。")
        return "\n".join(lines)


# ---- 工厂 ----

def load_lexicon(pid: str, projects_root: Path | str) -> LexiconStore:
    return LexiconStore(Path(projects_root) / pid / "lexicon.json")


def mechanical_redundancy(text: str) -> list[dict]:
    """纯机械冗余：同一表达在同一段内出现 >=2 次（无需任何审美判断）。

    这是唯一可以自动改的情况——它不是审美问题，是笔误级冗余。
    """
    out: list[dict] = []
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    for i, para in enumerate(paras):
        for cat, terms in (("body", BODY_PARTS), ("sensation", SENSATION_WORDS),
                           ("env", ENV_WORDS)):
            for t in terms:
                c = para.count(t)
                if c >= 2:
                    out.append({"term": t, "category": cat, "para": i, "count": c})
    return out
