"""写前人工确认门（spec 4）：风险评估 + 作者表态账本。

为什么需要它
------------
门早就存在，只是作者看不见。`hard_gate` 在生成**之后**判定 P0，重试 2 轮
后除格式类外**静默放行落库**（engine.py:584-597）；`onQuality` 事件把 P0/P1
推给了 SSE，但前端没接；审稿 `verdict=blocked` 也只记录不拦定稿。

结果是：作者点了「生成」，烧了 3 轮 token，只看到正文，不知道它被扣过分，
也不知道审稿判了 blocked。本模块把已经在算的数据送到作者眼前，并要求表态。

三个面，共享一个表态记录
------------------------
    生成前 ──── 风险触发时：先看委托书再开写 ──┐
                                              ├─→ 作者表态 → gate_decisions.jsonl
    门禁后 ──── P0 不再静默放行：收下/重写/手改 ─┤
                                              │
    定稿前 ──── blocked 必须逐条表态 ──────────┘

三者不是三个功能，是同一条链上的三个观测点。共用一个追加式账本后，
质量分析能回答一个比「指标是否退化」更有用的问题：
**哪些章作者反复选择收下**——那是真实的写作偏好信号。

与 spec 1 的 commits.jsonl 是两种东西，不要混：

    commits.jsonl            gate_decisions.jsonl
    ---------                ------------------
    记什么    机器指标        记什么    人的判断
    谁写的    hard_gate       谁写的    作者
    用途      跨章趋势        用途      风险漏斗 + 偏好画像

设计约束
--------
1. **风险判定零 LLM。** 全部纯数据推导。加 LLM 预检层预准虽高，但每次写前
   都烧 token，且误报一次作者就再也不信这个门了——天天误报的警告等于没有警告。
2. **只在风险时弹，不每次弹。** 每次必确认的确认门三章后就变成肌肉记忆，
   作者闭眼点确认，制造「我已经确认过了」的假安全感，比不做更糟。
3. **账本写失败绝不阻断写作。** 与 spec 1 同约束：账本是记录，不是门禁。
4. **追加写、绝不原地改。** 同 spec 1，能看出同一章被反复表了几次态。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ---- 门类型 ----
GATE_PRE_WRITE = "pre_write"      # 生成前风险确认
GATE_POST_GATE = "post_gate"      # 门禁后 P0 表态
GATE_FINALIZE = "finalize"        # 定稿前 blocked 表态

GATE_LABELS = {
    GATE_PRE_WRITE: "写前风险确认",
    GATE_POST_GATE: "门禁后表态",
    GATE_FINALIZE: "定稿表态",
}

# ---- 决策类型 ----
DECISION_ACCEPT = "accept"            # 收下（明知有问题就这样）
DECISION_REWRITE = "rewrite"          # 再重写一次
DECISION_MANUAL = "manual"            # 我自己改
DECISION_ACKNOWLEDGE = "acknowledge"  # 逐条确认收下

DECISION_LABELS = {
    DECISION_ACCEPT: "收下",
    DECISION_REWRITE: "再重写一次",
    DECISION_MANUAL: "我自己改",
    DECISION_ACKNOWLEDGE: "逐条确认收下",
}

# ---- 风险类型（仅账本类，零 LLM） ----
RISK_FORESHADOW_DUE = "foreshadow_due"        # 到期伏笔
RISK_FORESHADOW_HARD = "foreshadow_hard"      # 硬债务伏笔
RISK_STRAND_GAP = "strand_gap"                # 三线断档
RISK_RHYTHM_HUNGER = "rhythm_hunger"          # 节奏饥饿
RISK_KNOWLEDGE_GAP = "knowledge_gap"          # 知情边界只声明一半

RISK_LABELS = {
    RISK_FORESHADOW_DUE: "到期伏笔",
    RISK_FORESHADOW_HARD: "硬债务伏笔",
    RISK_STRAND_GAP: "三线断档",
    RISK_RHYTHM_HUNGER: "节奏饥饿",
    RISK_KNOWLEDGE_GAP: "知情边界未声明完整",
}


@dataclass
class Risk:
    """一条写前风险。无风险不弹窗，所以这里每条都必须能一句话说清。"""
    kind: str                # RISK_* 之一
    detail: str              # 给人看的一句话
    items: list[str] = field(default_factory=list)   # 具体条目（伏笔名等）

    @property
    def label(self) -> str:
        return RISK_LABELS.get(self.kind, self.kind)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


@dataclass
class GatePreview:
    """写前预演结果。brief 是模型将看到的开章任务书全文（零成本，本来就要注入）。"""
    chapter: int
    stage: str
    risks: list[Risk] = field(default_factory=list)
    brief: str = ""

    @property
    def blocked(self) -> bool:
        """有风险才需要确认。无风险直接跑——每次必确认会退化成多余的点击。"""
        return bool(self.risks)

    def as_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "stage": self.stage,
            "blocked": self.blocked,
            "risks": [r.as_dict() for r in self.risks],
            "brief": self.brief,
        }

    def notice_text(self) -> str:
        """渲染成给作者看的一段话（也用作 SSE notice 文案）。"""
        if not self.risks:
            return ""
        lines = [f"【写前风险确认】第 {self.chapter} 章 {self.stage} 层开写前有 "
                 f"{len(self.risks)} 项需要注意："]
        for r in self.risks:
            lines.append(f"· {r.label}：{r.detail}")
        return "\n".join(lines)


# ============================================================
# 风险评估（纯数据推导，零 LLM）
# ============================================================

def assess_write_risks(entity_store, desk, chapter: int, stage: str = "") -> list[Risk]:
    """评估写第 N 章前的账本类风险。

    全部纯数据推导，不调 LLM、不读正文——所以可以在点「生成」之前
    零成本地跑一遍来决定要不要弹窗。

    参数都可为 None：缺哪个依赖就跳过哪类风险，绝不因为数据不全而报错
    （旧项目没有实体库/账本时不该把整个门卡死）。
    """
    risks: list[Risk] = []

    # ---- 到期伏笔 / 硬债务（spec 3 的静默口径） ----
    if entity_store is not None:
        risks.extend(_foreshadow_risks(entity_store, chapter))

    # ---- 知情边界只声明了一半 ----
    if entity_store is not None:
        risks.extend(_knowledge_risks(entity_store, chapter))

    # ---- 三线断档 / 节奏饥饿（复用账本已有推导，不重写） ----
    if desk is not None:
        risks.extend(_rhythm_risks(desk, chapter))

    return risks


def _foreshadow_risks(entity_store, chapter: int) -> list[Risk]:
    """到期伏笔风险。

    口径与开章任务书完全一致（同一套阈值常量），否则会出现
    「弹窗说有 3 条到期，任务书里却一条没有」的自相矛盾。
    """
    from ..context.ledger import (
        FORESHADOW_DUE_AFTER,
        FORESHADOW_HARD_AFTER,
        _foreshadow_anchor,
    )

    due: list[str] = []
    hard: list[str] = []
    for e in entity_store.list(type="foreshadow", status="planted"):
        if not e.appearances or min(e.appearances) >= chapter:
            continue                      # 本章及之后才埋的，不算本章的债
        anchor = _foreshadow_anchor(e)
        if not anchor:
            continue
        silent = chapter - anchor
        if silent >= FORESHADOW_HARD_AFTER:
            hard.append(f"{e.name}（静默 {silent} 章）")
        elif silent >= FORESHADOW_DUE_AFTER:
            due.append(f"{e.name}（静默 {silent} 章）")

    risks: list[Risk] = []
    if hard:
        risks.append(Risk(
            kind=RISK_FORESHADOW_HARD,
            detail=f"{len(hard)} 条伏笔已静默 ≥{FORESHADOW_HARD_AFTER} 章，"
                   f"拖到极限：{'、'.join(hard[:3])}"
                   + ("…" if len(hard) > 3 else ""),
            items=hard,
        ))
    if due:
        risks.append(Risk(
            kind=RISK_FORESHADOW_DUE,
            detail=f"{len(due)} 条伏笔静默 ≥{FORESHADOW_DUE_AFTER} 章，"
                   f"本章应推进或兑现：{'、'.join(due[:3])}"
                   + ("…" if len(due) > 3 else ""),
            items=due,
        ))
    return risks


def _knowledge_risks(entity_store, chapter: int) -> list[Risk]:
    """知情边界只声明了一半的角色。

    只查「声明了 blinded_to 却没声明 knows」这一种——它是纯粹的数据不完整，
    零误判。**不推断「他应该知道什么」**：凭空推断会把正确信息也判成风险。
    """
    half: list[str] = []
    for e in entity_store.list(type="character"):
        attrs = getattr(e, "state_attrs", None) or {}
        blind = str(attrs.get("blinded_to") or "").strip()
        knows = str(attrs.get("knows") or "").strip()
        if blind and not knows:
            half.append(f"{e.name}（只声明了不知情，没声明已知）")
    if not half:
        return []
    return [Risk(
        kind=RISK_KNOWLEDGE_GAP,
        detail=f"{len(half)} 个角色声明了「不知情」但没声明「已知」——"
               f"任务书里只会给他一半的信息：{'、'.join(half[:3])}"
               + ("…" if len(half) > 3 else ""),
        items=half,
    )]


def _rhythm_risks(desk, chapter: int) -> list[Risk]:
    """三线断档 / 节奏饥饿——直接复用账本的推导，不重写判定逻辑。

    注意 `rhythm_hunger` 的入参必须是**上一章**：它从传入章号往回数连续无兑现，
    而写前门禁时本章还没有内容，直接传 chapter 会在第一行就 break，
    导致节奏饥饿**永远不会触发**（写前调用与审稿调用的语境不同）。
    """
    from ..context.ledger import rhythm_hunger, strand_warnings

    risks: list[Risk] = []
    try:
        gaps = strand_warnings(desk, chapter)
    except Exception:
        gaps = []
    if gaps:
        risks.append(Risk(
            kind=RISK_STRAND_GAP,
            detail=gaps[0] + (f"（共 {len(gaps)} 条）" if len(gaps) > 1 else ""),
            items=gaps,
        ))
    try:
        hunger = rhythm_hunger(desk, chapter - 1)
    except Exception:
        hunger = None
    if hunger:
        risks.append(Risk(
            kind=RISK_RHYTHM_HUNGER,
            detail=hunger.replace("本章宜安排兑现", "开写前请先安排兑现"),
            items=[hunger],
        ))
    return risks


def build_preview(entity_store, desk, chapter: int, stage: str = "",
                  outline_total: int = 0, book_store=None, field_store=None,
                  volume_store=None, current_volume=None,
                  project_id: str = "") -> GatePreview:
    """写前预演：风险 + 本章将要注入的任务书全文。

    brief 复用 `build_chapter_checklist` 的产出——零成本，本来就要注入给模型。
    作者在弹窗里看到的**就是**模型将看到的，这是「透明」而非「拦截」。
    """
    risks = assess_write_risks(entity_store, desk, chapter, stage)
    brief = ""
    try:
        from ..context.ledger import build_chapter_checklist
        m = build_chapter_checklist(
            entity_store, desk, chapter, outline_total,
            book_store=book_store, field_store=field_store,
            volume_store=volume_store, current_volume=current_volume,
            project_id=project_id,
        )
        if m is not None:
            brief = m.content
    except Exception as e:
        # 任务书构建失败绝不能阻断预演——风险判定已经拿到结果了
        log.debug("gate preview brief failed: %s", e)
    return GatePreview(chapter=chapter, stage=stage, risks=risks, brief=brief)


# ============================================================
# 表态账本 gate_decisions.jsonl
# ============================================================

def _projects_root() -> Path:
    """backend/data/projects 根目录（与 ledger._projects_root 同构）。"""
    try:
        from .. import _config
        return _config.REPO / "backend" / "data" / "projects"
    except Exception:
        return Path(__file__).resolve().parents[3] / "data" / "projects"


def _decisions_path(pid: str, root: Path | None = None) -> Path:
    base = root if root is not None else _projects_root()
    return Path(base) / str(pid) / "gate_decisions.jsonl"


def append_decision(pid: str, chapter: int, gate: str, decision: str,
                    risk: str = "", detail: str = "", stage: str = "",
                    p0: list[str] | None = None,
                    root: Path | None = None) -> bool:
    """追加一条作者表态。返回是否成功——**调用方必须静默处理 False**。

    账本是记录不是门禁：写失败绝不能阻断写作流程（与 spec 1 同约束）。
    """
    entry = {
        "ch": chapter,
        "gate": gate,
        "decision": decision,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if risk:
        entry["risk"] = risk
    if detail:
        entry["detail"] = detail[:300]
    if stage:
        entry["stage"] = stage
    if p0:
        entry["p0"] = [str(x)[:200] for x in p0[:10]]
    path = _decisions_path(pid, root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as e:
        log.debug("gate decision append failed: %s", e)
        return False


def load_decisions(pid: str, root: Path | None = None) -> list[dict]:
    """读取全部表态行。损坏行跳过而非抛错——一行坏不该让整个功能失效。"""
    path = _decisions_path(pid, root)
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
                    log.debug("gate decisions line %d malformed, skipped", ln)
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except Exception as e:
        log.debug("gate decisions read failed: %s", e)
    return out


def decisions_by_gate(pid: str, gate: str, root: Path | None = None) -> list[dict]:
    """按门类型过滤。"""
    return [d for d in load_decisions(pid, root) if d.get("gate") == gate]


def accept_streaks(pid: str, root: Path | None = None) -> dict[str, int]:
    """统计每类风险被「收下」的次数。

    这是账本相对于 spec 1 的独特价值：commits.jsonl 只知道指标退化，
    这里知道**作者在明知有问题时反复选择收下**——那是真实的写作偏好，
    也是判断「这个阈值是不是该放宽」的唯一依据。
    """
    counts: dict[str, int] = {}
    for d in load_decisions(pid, root):
        if d.get("decision") in (DECISION_ACCEPT, DECISION_ACKNOWLEDGE):
            risk = str(d.get("risk") or "")
            if risk:
                counts[risk] = counts.get(risk, 0) + 1
    return counts


def unconfirmed_chapters(pid: str, gate: str = GATE_PRE_WRITE,
                         root: Path | None = None) -> list[int]:
    """被风险门拦过、但账本里找不到放行记录的章（漏斗诊断）。

    正常流程不该出现这种章——出现说明前端绕过了确认门或生成中途断开。
    返回它是为了让这类漏网之鱼能被事后查出来。

    实现口径：把每章的行按时间序折叠，只看**最后一条**表态。
    中间表过态又改主意不算漏——只有从头到尾没有任何放行表态才算。
    """
    last: dict[int, dict] = {}
    for d in load_decisions(pid, root):
        if d.get("gate") != gate:
            continue
        ch = d.get("ch")
        if not isinstance(ch, int):
            continue
        prev = last.get(ch)
        if prev is None or str(d.get("ts", "")) >= str(prev.get("ts", "")):
            last[ch] = d

    released = {DECISION_ACCEPT, DECISION_MANUAL, DECISION_REWRITE,
                DECISION_ACKNOWLEDGE}
    stuck: list[int] = []
    for ch, d in sorted(last.items()):
        if d.get("risk") and d.get("decision") not in released:
            stuck.append(ch)
    return stuck