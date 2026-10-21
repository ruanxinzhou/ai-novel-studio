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

SEMANTIC_FIELDS: dict[str, list[str]] = {
    # ── 身体：吞咽/干呕 ──
    "吞咽紧张": ["喉咙", "嗓子", "嗓子眼", "舌根", "咽口", "吞咽", "咽了口唾沫",
                 "口腔发干", "唾沫", "干呕", "喉结"],
    # ── 身体：手部动作 ──
    "手部小动作": ["指腹", "指尖", "手指", "指节", "指缝", "拇指", "掌心", "手心",
                   "手背", "虎口", "腕骨", "手腕"],
    # ── 身体：视线 ──
    "视线打量": ["眼神", "目光", "视线", "盯着", "看着", "扫了一眼", "打量"],
    # ── 身体：僵/紧 ──
    "肌肉紧绷": ["发僵", "僵住", "绷紧", "紧绷", "发紧", "僵硬"],
    # ── 感官：冷 ──
    "寒冷": ["凉", "冷", "冰", "湿冷", "透凉", "冰凉", "寒意", "起鸡皮疙瘩"],
    # ── 感官：潮/湿 ──
    "潮湿": ["湿", "潮", "潮气", "返潮", "发潮", "黏"],
    # ── 感官：干燥 ──
    "干燥": ["干", "燥", "干涩", "发干", "口干"],
    # ── 感官：热 ──
    "温热": ["热", "烫", "温", "暖", "灼"],
    # ── 感官：重/沉 ──
    "沉重": ["沉", "重", "坠", "压"],
    # ── 情绪：抽象概括（AI 高发区） ──
    "抽象情绪": ["涌上", "涌起", "五味杂陈", "百感交集", "难以言喻", "不寒而栗",
                 "毛骨悚然", "心口一紧", "泛起"],
    # ── 环境：门/入口 ──
    "门口": ["门口", "门缝", "门框", "门前", "门槛", "防盗门", "单元门", "大门"],
    # ── 环境：灯 ──
    "灯具": ["声控灯", "灯管", "灯泡", "路灯", "台灯", "应急灯", "走廊灯", "灯"],
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
        violations = []
        for fname in recorded:
            fu = self.data[fname][chapter]
            positions = [h["char_pos"] for h in fu.hits]
            for a, b in zip(positions, positions[1:]):
                if b - a < FIELD_WINDOW_CHARS:
                    hit_a = fu.hits[positions.index(a)]
                    hit_b = fu.hits[positions.index(b)]
                    violations.append({
                        "field": fname,
                        "gap": b - a,
                        "a": {"para": hit_a["para"], "term": hit_a["term"]},
                        "b": {"para": hit_b["para"], "term": hit_b["term"]},
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
        """本章内所有语义场窗口复读违规（跨章累计次数也一并看）。"""
        out = []
        for fname, chs in self.data.items():
            fu = chs.get(chapter)
            if not fu:
                continue
            positions = [h["char_pos"] for h in fu.hits]
            total_across = sum(len(v.hits) for v in chs.values())
            for i, (a, b) in enumerate(zip(positions, positions[1:])):
                if b - a < FIELD_WINDOW_CHARS:
                    out.append({
                        "field": fname,
                        "gap": b - a,
                        "term_a": fu.hits[i]["term"],
                        "para_a": fu.hits[i]["para"],
                        "term_b": fu.hits[i + 1]["term"],
                        "para_b": fu.hits[i + 1]["para"],
                        "total_across_book": total_across,
                    })
        out.sort(key=lambda x: x["gap"])
        return out

    def top_n_for_chapter(self, chapter: int, n: int | None = None) -> list[dict]:
        """Top-N 提示：按「本章违规次数 + 累计次数」排序。"""
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
        return out[:n]

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
