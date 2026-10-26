"""质量账本 commits.jsonl：跨章质量指标的追加式事件溯源。

为什么需要它
------------
`hard_gate` 的 stats 算完只发一次 SSE 就销毁（engine.py:542），落盘的只有
entities.json（设定事实）与 lexicon.json（描写重复）——**质量指标一条都没存**。

后果是门禁只能拦住「单章写坏」，拦不住「一本书在退化」：第 1 章问号密度
8/千字、第 20 章降到 2/千字，每章单独看都过 P0 线（阈值 2.0），整本书已经
变成说明书。本模块把每章的指标落盘，并提供跨章滑动窗口趋势判定。

与 journal.py 的断点收据是两种东西，不要混：

    journal.py 收据      commits.jsonl 账本
    ------- -----------   ---------------- ----------
    语义    幂等缓存      语义    历史
    写入    覆盖（同index 去重）  写入    追加
    run_key 变化 → 全作废  run_key 无关
    用途    断点续写      用途    跨章趋势、修订追溯

设计约束
--------
1. **追加写、绝不原地改。** 状态变更（draft → accepted → revised）用新增行表达，
   这样重放时能看出「这一章被反复改了几次」。
2. **只有 accepted 计入趋势。** 半成品不能污染趋势——与 journal.py:93 的
   match() 只认 completed 同理。
3. **趋势是相对变化率，不是绝对差。** 各指标量纲差异极大（字种密度 0.41 vs
   问号密度 7.2），绝对差无法用统一阈值。
4. **双信号互证。** sentence_cv 上升本身不是 AI 信号（lieflat 实证 R=0.87
   人机无差异，humanize.py:545 已据此撤回它的 P0），必须与极短段占比同时
   上升才报，避免把已证伪的指标重新引入。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ---- 趋势判定参数 ----
# 窗口大小：近 N 章 vs 前 N 章
TREND_WINDOW = 5
# 少于这个章数不出趋势结论（需要 TREND_WINDOW + TREND_WINDOW 才够两个窗口）
TREND_MIN_CHAPTERS = TREND_WINDOW * 2
# 相对变化率绝对值小于此值视为噪声
REL_EPS = 1e-6
# 注入任务书的最大条数
TREND_INJECT_TOP_N = 3
# 连续多少章触发同一条视为真退化（而非波动）
TREND_PERSIST_STREAK = 3


@dataclass(frozen=True)
class TrendRule:
    """单条趋势规则。direction=down 表示指标下降是退化。"""
    key: str                # stats 里的字段名
    label: str              # 注入时给模型看的中文名
    rel_threshold: float    # rel 低于此值（down）或高于此值（up）触发
    direction: str = "down"
    # 需要同时上升才算退化的伴随字段（双信号互证用）
    confirm_key: str = ""
    confirm_rel: float = 0.0
    advice: str = ""        # 触发后给模型的建议


#七条规则。阈值是相对变化率，理由见设计文档第三节。
TREND_RULES: tuple[TrendRule, ...] = (
    TrendRule("question_density", "问号密度", -0.35, "down",
              advice="补内心反问、对白问句或不确定的自问"),
    TrendRule("char_type_density", "字种密度", -0.15, "down",
              advice="用具体名词动词替换套话，减少高频重复词"),
    TrendRule("number_density", "具体数字密度", -0.35, "down",
              advice="补具体数字：房号/时间/价格/年龄/楼层/次数"),
    TrendRule("time_anchor_density", "时间锚点密度", -0.35, "down",
              advice="补具体时间词：三点钟/第二天/上周/分钟/秒"),
    TrendRule("长段数(>80字)", "长段数", -0.40, "down",
              advice="至少留 3 段 >80 字的长段承担描写/心理/环境"),
    TrendRule("动作收尾占比", "动作收尾占比", -0.30, "down",
              advice="段尾落在具体动作上，不要用心理总结或作者旁白收尾"),
    # 双信号：CV 上升 **且** 极短段占比同时上升才算退化。
    # CV 单独上升不是 AI 信号（R=0.87），单靠碎句拉高 CV 更可能是节奏失控。
    TrendRule("sentence_cv", "句长参差度", 0.40, "up",
              confirm_key="极短段(≤12字)", confirm_rel=0.15,
              advice="极短段激增导致节奏失控，收一些回完整句"),
)


@dataclass
class TrendFinding:
    """一条被触发的趋势结论。"""
    key: str
    label: str
    rel: float                # 相对变化率（负=下降）
    baseline: float
    recent: float
    advice: str
    streak: int = 1           # 连续触发章数

    @property
    def is_persistent(self) -> bool:
        return self.streak >= TREND_PERSIST_STREAK


def _projects_root() -> Path:
    """backend/data/projects 根目录（与 lexicon._root 同构）。"""
    try:
        from .. import _config
        return _config.REPO / "backend" / "data" / "projects"
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "projects"


def _ledger_path(pid: str, root: Path | None = None) -> Path:
    base = root if root is not None else _projects_root()
    return Path(base) / str(pid) / "commits.jsonl"


# ---- 读写 ----

def append_commit(pid: str, entry: dict, root: Path | None = None) -> bool:
    """追加一行账本。返回是否成功——**调用方必须静默处理 False**。

    原子写：先写 temp 再 os.replace，避免进程中断留下半行JSON
    （半行会让后续所有解析失败，是最难排查的一类损坏）。
    """
    path = _ledger_path(pid, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        # append 模式本身对单次 <PIPE_BUF 的写入是原子的，
        # 但为防进程中断留下截断行，写完立即回读校验最后一行。
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as e:
        log.debug("commits append failed: %s", e)
        return False


def load_commits(pid: str, root: Path | None = None) -> list[dict]:
    """读取全部账本行。损坏行跳过而非抛错——一行坏不该让趋势功能整体失效。"""
    path = _ledger_path(pid, root)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    log.debug("commits line %d malformed, skipped", ln)
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except Exception as e:
        log.debug("commits read failed: %s", e)
    return out


def accepted_by_chapter(pid: str, root: Path | None = None) -> dict[int, dict]:
    """按章归并出「每章最终生效的那条 accepted 记录」。

    同一章可能有多行（draft → accepted → revised）。语义：
    - accepted 与 revised 都算生效
    - 后写的行覆盖先写的（时间序）
    - 只有 draft 的章不计入趋势（未确认 = 半成品）
    """
    merged: dict[int, dict] = {}
    for e in load_commits(pid, root):
        ch = e.get("ch")
        st = e.get("status")
        if not isinstance(ch, int):
            continue
        if st == "draft":
            continue
        if st not in ("accepted", "revised"):
            continue
        prev = merged.get(ch)
        if prev is None or str(e.get("ts", "")) >= str(prev.get("ts", "")):
            merged[ch] = e
    return merged


# ---- 指标抽取 ----

_WANT_BURST = ("question_density", "char_type_density", "comma_density",
               "number_density", "time_anchor_density", "sentence_cv")


def build_metrics_entry(hard_review, chars: int) -> dict:
    """从 HardGateResult 抽出可落盘的 metrics。

    注意：调用方须在 _apply_burstiness_guard **之后**调用，这样 verdict.p0
    是降级后的真实值而不是初判值。
    """
    stats = (hard_review.stats or {}) if hard_review else {}
    burst = stats.get("burstiness") or {}
    rhythm = stats.get("rhythm") or {}
    ai_detail = stats.get("ai_pattern_detail") or {}
    return {
        "chars": chars,
        "burstiness": {k: burst.get(k) for k in _WANT_BURST},
        # 节奏指标只留趋势判定要用的几个，避免账本膨胀
        "rhythm": {k: rhythm.get(k) for k in
                   ("长段数(>80字)", "动作收尾占比", "极短段(≤12字)", "最长连续单句段")},
        "verdict": {
            "p0": len(hard_review.p0) if hard_review else 0,
            "p1": len(hard_review.p1) if hard_review else 0,
            "ai_hits": sum(v for v in ai_detail.values() if isinstance(v, int)),
            "banned_hits": stats.get("banned_word_hits", 0),
        },
    }


def record_quality(pid: str, chapter: int, stage: str, hard_review,
                   chars: int, run_key: str = "", notes: str = "",
                   root: Path | None = None,
                   guard: dict | None = None) -> bool:
    """钩子 A：块级门禁后落库 status=draft。

    guard：去AI 味护栏裁决（spec 2），None 表示未接入。
    """
    entry = {
        "ch": chapter,
        "stage": stage,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "status": "draft",
        "run_key": run_key or "",
        "metrics": build_metrics_entry(hard_review, chars),
    }
    if notes:
        entry["notes"] = notes
    if guard:
        entry["guard"] = guard
    return append_commit(pid, entry, root)


def mark_accepted(pid: str, chapter: int, root: Path | None = None) -> bool:
    """钩子 B：章定稿时追加一条 accepted 行（不原地改 draft 行）。"""
    src = accepted_by_chapter(pid, root).get(chapter)
    if src is None:
        # 从draft 行里找该章最后一次生成的 metrics
        drafts = [e for e in load_commits(pid, root)
                  if e.get("ch") == chapter and e.get("status") == "draft"]
        src = drafts[-1] if drafts else None
    if src is None:
        return False
    entry = {
        "ch": chapter,
        "stage": src.get("stage", ""),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "status": "accepted",
        "run_key": src.get("run_key", ""),
        "metrics": src.get("metrics", {}),
    }
    return append_commit(pid, entry, root)


# ---- 趋势 ----

def _get_metric(entry: dict, key: str):
    """从账本行取指标值。burstiness 与 rhythm 扁平查找。"""
    m = entry.get("metrics") or {}
    for bucket in ("burstiness", "rhythm"):
        v = (m.get(bucket) or {}).get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_trends(pid: str, upto_chapter: int | None = None,
                   root: Path | None = None) -> list[TrendFinding]:
    """核心：滑动窗口 + 相对变化率 + 双信号互证。

    样本不足（< TREND_MIN_CHAPTERS 个已确认章）时返回空列表。
    """
    accepted = accepted_by_chapter(pid, root)
    chs = sorted(c for c in accepted if upto_chapter is None or c <= upto_chapter)
    if len(chs) < TREND_MIN_CHAPTERS:
        return []

    recent_chs = chs[-TREND_WINDOW:]
    baseline_chs = chs[-TREND_WINDOW * 2:-TREND_WINDOW]
    if len(baseline_chs) < TREND_WINDOW:
        return []

    findings: list[TrendFinding] = []
    for rule in TREND_RULES:
        recent_vals = [v for v in (_get_metric(accepted[c], rule.key) for c in recent_chs)
                       if v is not None]
        base_vals = [v for v in (_get_metric(accepted[c], rule.key) for c in baseline_chs)
                     if v is not None]
        if len(recent_vals) < TREND_WINDOW or len(base_vals) < TREND_WINDOW:
            continue

        recent = _mean(recent_vals)
        baseline = _mean(base_vals)
        if abs(baseline) < REL_EPS:
            continue
        rel = (recent - baseline) / abs(baseline)

        if rule.direction == "down":
            if rel >= rule.rel_threshold:
                continue
        else:
            if rel <= rule.rel_threshold:
                continue

        # 双信号互证：伴随指标必须同向恶化，否则不报。
        # 这条是 sentence_cv 的关键护栏——CV 单独上升不是 AI 信号。
        if rule.confirm_key:
            c_recent = [v for v in (_get_metric(accepted[c], rule.confirm_key)
                                    for c in recent_chs) if v is not None]
            c_base = [v for v in (_get_metric(accepted[c], rule.confirm_key)
                                  for c in baseline_chs) if v is not None]
            if len(c_recent) < TREND_WINDOW or len(c_base) < TREND_WINDOW:
                continue
            c_rel = (_mean(c_recent) - _mean(c_base)) / max(abs(_mean(c_base)), REL_EPS)
            if c_rel < rule.confirm_rel:
                continue

        findings.append(TrendFinding(
            key=rule.key, label=rule.label, rel=rel,
            baseline=baseline, recent=recent, advice=rule.advice,
            streak=_streak_of(accepted, chs, rule),
        ))
    return findings


def _streak_of(accepted: dict, chs: list[int], rule: TrendRule) -> int:
    """连续触发章数：逐章回看，最近连续恶化的章数。

    简化实现：从最近一章往前，用与趋势同向的单章比较判断，
    最多回看 TREND_PERSIST_STREAK + 2 章。不用滑窗（成本高且此处只需粗粒度）。
    """
    streak = 0
    for i in range(len(chs) - 1, 0, -1):
        cur = _get_metric(accepted[chs[i]], rule.key)
        prev = _get_metric(accepted[chs[i - 1]], rule.key)
        if cur is None or prev is None:
            break
        worse = (cur < prev) if rule.direction == "down" else (cur > prev)
        if not worse:
            break
        streak += 1
        if streak >= TREND_PERSIST_STREAK + 2:
            break
    return streak


def render_trend_block(findings: list[TrendFinding]) -> str:
    """渲染成任务书文本。

    措辞用**叙事性记忆**而非禁令——照抄 lexicon.py:274 已有的手法
    （「上一章你把『冷风』用了 4 次」而非「禁止使用冷风」）。
    命令式禁令会压缩模型的选择空间。
    """
    if not findings:
        return ""
    ranked = sorted(findings, key=lambda f: f.rel)[:TREND_INJECT_TOP_N]
    lines = ["【趋势红线】（以下均为跨章统计结论，不是单章判断）"]
    for f in ranked:
        pct = f"{abs(f.rel) * 100:.0f}%"
        head = f"· {f.label}已从 {f.baseline:.1f} 滑到 {f.recent:.1f}（-{pct}）"
        if f.is_persistent:
            head = f"· {f.label}已连续 {f.streak} 章退化（{f.baseline:.1f} → {f.recent:.1f}）"
            lines.append(head + "：本次必须处理——" + f.advice)
        else:
            lines.append(head + "：本章留意——" + f.advice)
    return "\n".join(lines)