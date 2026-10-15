"""创作会话：作者与 AI 协作完成一场副本的全流程。

创作流程（对齐作者 v3 设定）：
1. settle_up   上一场杀青+残页+蓝星间章（读残页/杀青账/爽点/污染到账/卷级伏笔推进）
2. kindle      PDF 上传→版权规避改编 5 版→作者挑一版→回转寿司产出副本核（选基调）
3. scaffold    COC 跑团模拟（2 同 PL 不同骰运 + 1 不同 PL，中等细度，纯交付）+
               唐迟替换视角大纲 5 版→作者选手动精校定稿
4. walkthrough 章节细纲：按章扩写场景/时间/人物/情节推进/情绪节点
5. schedule    章节分配+命名（按大纲分配到合适章节数）
6. daily_write 日更正文（章台）+ 润色定稿（含章三问+弄坏了什么）

（章节分配和细纲的顺序实际是：先粗分配章节，再逐章写细纲。但为兼容既有 phase id，
 我们把章分配仍放在 schedule，细纲在 walkthrough；UI 上作者可以灵活使用。）

数据落地：backend/data/projects/{pid}/sessions/{session_id}.json
会话列表索引：backend/data/projects/{pid}/sessions/index.json
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


# ---- 阶段定义 ----

class SessionPhase(str, Enum):
    SETTLE_UP = "settle_up"           # 上月结账
    KINDLE = "kindle"                 # 副本点火
    SCAFFOLD = "scaffold"             # 骨架焊接
    WALKTHROUGH = "walkthrough"       # 细纲走查
    SCHEDULE = "schedule"             # 章节排期
    DAILY_WRITE = "daily_write"       # 日更单章


class SessionScope(str, Enum):
    FIELD = "field"       # 副本级（前 5 个阶段），绑定 field_no
    CHAPTER = "chapter"   # 章节级（日更），绑定 chapter


# 阶段元数据：scope / 标题 / 提示 / 下一阶段 / 必填产物门
PHASE_META: dict[str, dict] = {
    SessionPhase.SETTLE_UP.value: {
        "scope": SessionScope.FIELD.value,
        "title": "杀青间章",
        "prompt": (
            "上一场副本结束，唐失神一瞬回到蓝星，猫碗还是满的，后背已经凉透。"
            "本阶段做三件事：①写杀青/残页卡——回蓝星瞬间、读残页（后日谈：原命运被改成什么样、留下什么线索）、"
            "本场杀青账（借了什么手艺/带回什么物品/叠了什么污染/欠了什么债）、蓝星小钩子（爽点兑现+污染征兆）；"
            "②蓝星间章 2-5 章规划——轻松回血日常、爽点（带回来的手艺在日常里不小心用出来）、"
            "卷级伏笔推进（猫/邻居/其他扮演者伪装成普通人/蓝星污染小征兆）；"
            "③确认上一场账已结、蓝星章已规划，点「账已结」等待下一封讣告送达，进入副本点火。"
        ),
        "ai_mode": "review",      # AI 帮对账、提醒漏掉的伏笔/污染、给蓝星爽点灵感
        "next_phase": SessionPhase.KINDLE.value,
        "required_outputs": {"settlement_confirmed": "作者确认杀青账+蓝星章规划"},
    },
    SessionPhase.KINDLE.value: {
        "scope": SessionScope.FIELD.value,
        "title": "副本点火",
        "prompt": (
            "新副本的起点：你准备好一个克苏鲁跑团剧本（PDF），上传后 AI 会做版权规避改编产出 5 版骨架候选，"
            "你挑一版（或揉几版自己改），然后通过回转寿司锁定副本核（三档基调任选：民俗阴冷/规则冷峻/诡异日常）。"
            "副本核写的是副本本身的故事世界（题材外壳+地点+风貌+异常+规则+钩子+唐迟入场锚），"
            "不是唐迟的传记——唐迟穿入留给正文写。没有 PDF 也可以直接在聊天区让 AI 给灵感。"
            "锁完副本核进入跑团阶段。"
        ),
        "ai_mode": "diverge",     # AI 发散灵感、给5版改编候选、三档基调寿司
        "next_phase": SessionPhase.SCAFFOLD.value,
        "required_outputs": {"core_pitch": "副本核（作者亲笔锁定）"},
    },
    SessionPhase.SCAFFOLD.value: {
        "scope": SessionScope.FIELD.value,
        "title": "跑团与大纲",
        "prompt": (
            "分两步：①COC 跑团模拟——AI 按 COC 规则（KP+PL 骰子/SAN/HP/Day推进）自动跑 3 场"
            "（2 场用同一组 PL 配不同骰运/选择走向不同结局，1 场换一组不同身份的 PL），中等细度小说化笔调，"
            "跑完压成 3 份不同的 Day1-7 故事脉络（不需要你中间干预，纯交付），你可以对比看哪条走向最有戏；"
            "②唐迟视角大纲——你手动调整故事（加必现情节/伏笔）、指定唐迟替换哪个角色"
            "（PL/NPC/Boss/幕后黑手都行，诡计副本里还可以玩身份错配/虚假记忆），"
            "AI 产出 5 版不同节奏的大纲，你精校手动敲定最终版。"
            "定稿后进入章节分配。"
        ),
        "ai_mode": "challenge",   # AI 跑团+给大纲+挑漏洞问为什么
        "next_phase": SessionPhase.WALKTHROUGH.value,
        "required_outputs": {
            "obituary_card": "讣告卡（公函+死者+死期+进场第一眼）",
            "entity_card": "出场角色/其他扮演者/邪物（可多张）",
            "truth_outline": "Day1-7 故事真相梗概（作者定稿，基于选定跑团+唐迟大纲）",
            "tangchi_start": "唐迟进场状态（替换谁/知道多少/SAN/可用手艺/开场情绪）",
            "core_twist": "核心反转（Day4-6 揭开；诡计副本要标明虚假层/真实层）",
            "foreshadows": "本场伏笔+回收点（至少 2 条，可含跨场）",
            "cost_cap": "代价上限（本场最多叠什么污染/失去什么/带回什么邪物风险）",
        },
    },
    SessionPhase.WALKTHROUGH.value: {
        "scope": SessionScope.FIELD.value,
        "title": "章节细纲",
        "prompt": (
            "AI 按已分配好的章节表，逐章扩写成细纲：每场/每场景的时间、地点、人物、情节推进、"
            "情绪节点、伏笔埋/收、唐迟金手指使用与副作用、SAN/污染在这一章的变化。"
            "你可以随时喊停修改、加情绪/加钩子/加蓝星段的细节。所有章细纲确认完进入日更。"
        ),
        "ai_mode": "narrate",     # AI 逐章扩写细纲、朗读、复述
        "next_phase": SessionPhase.SCHEDULE.value,
        "required_outputs": {"walkthrough_confirmed": "全章细纲作者确认"},
    },
    SessionPhase.SCHEDULE.value: {
        "scope": SessionScope.FIELD.value,
        "title": "章节分配",
        "prompt": (
            "AI 根据定稿大纲按 Day1-7 自动拆成章节（副本七天 2-5 章/每日，蓝星间章 2-5 章），"
            "给出每章的章号、暂定标题、情绪标签、字数目标、钩子位置、伏笔回收点；"
            "你拖动合并拆分、标注爽章/刀章/恐怖章/回血章。锁定后章节写入章台。"
        ),
        "ai_mode": "plan",        # AI 给排期/章节建议
        "next_phase": SessionPhase.DAILY_WRITE.value,
        "required_outputs": {
            "chapter_plan": "章节表（list[{no,title,day,summary,is_interlude}]）",
            "chapter_plan_locked": "锁定章节表",
        },
    },
    SessionPhase.DAILY_WRITE.value: {
        "scope": SessionScope.CHAPTER.value,
        "title": "日更",
        "prompt": (
            "打开即见今日任务卡（章号/目标字数/情绪/钩子/待回收伏笔/当前唐迟状态），编辑器默认 AI 闭嘴；"
            "卡壳时点召唤给 2-3 个短小选项（对话走向/动作选择/氛围描写/下一段开头）。"
            "写完后填本章三问（读者以为/实际/结尾钩子）+ 敲一句「今天弄坏了什么」（新伏笔/污染/人物状态变化），"
            "点完成后 AI 自动归账（侵蚀/手艺/污染/伏笔/NPC 状态更新到主角档案和卷板）。"
            "最后一章写完有润色定稿环节（通读、改语病、检查伏笔一致性、AI 给润色建议）。"
        ),
        "ai_mode": "silent",      # 默认闭嘴，召唤才说话
        "next_phase": None,
        "required_outputs": {
            "chapter_three_qs": "本章三问（读者以为/实际/结尾钩子）",
            "damage_note": "弄坏了什么（作者亲笔一句话，归账用）",
            "draft_word_count": "正文达到字数下限",
        },
    },
}


# ---- 消息 ----

# 消息 kind 白名单：
# chat         : 普通对话（作者或 AI 的普通文本）
# diverge      : AI 发散素材（原 suggestion 重命名；M1 兼容旧数据）
# challenge    : AI 挑刺/追问
# narration    : AI 朗读/复述
# plan         : AI 排期建议
# card_edit    : 作者对卡片的编辑操作记录（系统消息）
# gate         : 阶段推进/门控系统消息
# candidates   : AI 一次性发回 3 个候选（meta.candidate_ids）
# suggestion   : 【编辑态】AI 返回修改后全文建议
# fork         : 【编辑态】AI 返回两个可选改法（meta.fork_ids）
# question     : 【编辑态】AI 问一个具体必要问题
VALID_MSG_KINDS = {
    "chat", "diverge", "challenge", "narration", "plan",
    "card_edit", "gate", "candidates", "suggestion", "fork", "question",
}

MAX_CANDIDATES_PER_SESSION = 30


@dataclass
class Candidate:
    """候选卡片（回转寿司传送带上的一盘）。"""
    id: str
    phase: str
    card_key: str
    content: str
    axis_label: str = ""
    axis_dimension: str = ""
    twist: str = ""              # 差异点自陈标签（v2.9 替代旧 axis_value）
    rationale: str = ""
    created_at: str = ""

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(
            id=str(d.get("id") or _new_id()),
            phase=str(d.get("phase", "")),
            card_key=str(d.get("card_key", "")),
            content=str(d.get("content", "") or ""),
            axis_label=str(d.get("axis_label", "") or ""),
            axis_dimension=str(d.get("axis_dimension", "") or ""),
            twist=str(d.get("twist", "") or d.get("axis_value", "") or ""),
            rationale=str(d.get("rationale", "") or ""),
            created_at=str(d.get("created_at", "") or ""),
        )


@dataclass
class EditingState:
    """当前编辑态：选中的候选 + 当前文本。"""
    candidate_id: str | None = None
    card_key: str = ""
    current_text: str = ""
    started_at: str = ""
    edit_round: int = 0
    last_suggestion_msg_id: str | None = None
    selected_fork: str | None = None

    def as_dict(self) -> dict: return asdict(self)
    @classmethod
    def from_dict(cls, d: dict | None) -> "EditingState | None":
        if not d:
            return None
        return cls(
            candidate_id=d.get("candidate_id"),
            card_key=str(d.get("card_key", "") or ""),
            current_text=str(d.get("current_text", "") or ""),
            started_at=str(d.get("started_at", "") or ""),
            edit_round=int(d.get("edit_round", 0) or 0),
            last_suggestion_msg_id=d.get("last_suggestion_msg_id"),
            selected_fork=d.get("selected_fork"),
        )


@dataclass
class SessionMessage:
    id: str
    role: str                 # "author" | "ai" | "system"
    kind: str = "chat"        # "chat" | "suggestion" | "challenge" | "narration" | "plan" | "card_edit" | "gate"
    text: str = ""
    meta: dict = field(default_factory=dict)
    edited_from: str | None = None   # 若为作者编辑消息，指向原 AI msg id
    created_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMessage":
        kind = str(d.get("kind", "chat"))
        # 兼容旧数据：kind=="suggestion" 且 meta.context=="diverge" 时视为发散消息（原 kindle 变体建议）
        if kind == "suggestion" and (d.get("meta") or {}).get("context") == "diverge":
            kind = "diverge"
        if kind not in VALID_MSG_KINDS:
            kind = "chat"
        return cls(
            id=str(d.get("id") or _new_id()),
            role=str(d.get("role", "author")),
            kind=kind,
            text=str(d.get("text", "") or ""),
            meta=dict(d.get("meta") or {}),
            edited_from=d.get("edited_from"),
            created_at=str(d.get("created_at", "") or ""),
        )


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- 会话本身 ----

@dataclass
class SessionState:
    id: str
    project_id: str
    scope: str                            # field | chapter
    field_no: int | None = None
    chapter: int | None = None
    volume_no: int | None = None      # 该会话绑定的卷号（M3：卷规划板注入）
    title: str = ""
    # 当前阶段进度
    current_phase: str = SessionPhase.SETTLE_UP.value
    phase_status: dict = field(default_factory=dict)
    # 各阶段作者产出（gate 检查的就是这里）
    outputs: dict = field(default_factory=dict)
    # 对话消息
    messages: list[SessionMessage] = field(default_factory=list)
    # 状态
    status: str = "active"                # active | completed | abandoned
    created_at: str = ""
    updated_at: str = ""
    # 元数据：coc 仿真结果、骨架卡、排期表等结构化数据
    artifacts: dict = field(default_factory=dict)
    # M1 新增：回转寿司候选+编辑态
    candidates: list[Candidate] = field(default_factory=list)
    editing: EditingState | None = None
    locked_cards: dict[str, str] = field(default_factory=dict)  # card_key → msg_id
    # M2: 作者上传的素材（跑团剧本 PDF 改编稿等）
    source_material: dict = field(default_factory=dict)  # {kind:"pdf_adapt", premise, renamed_cast, changed_elements, risk_flags, original_excerpt, original_chars, filename}

    # ---- helpers ----
    def add_message(self, role: str, text: str, *, kind: str = "chat",
                    meta: dict | None = None, edited_from: str | None = None) -> SessionMessage:
        msg = SessionMessage(
            id=_new_id(), role=role, kind=kind, text=text,
            meta=dict(meta or {}), edited_from=edited_from, created_at=_now(),
        )
        self.messages.append(msg)
        self.updated_at = _now()
        return msg

    def set_output(self, key: str, value: Any) -> None:
        self.outputs[key] = value
        self.updated_at = _now()

    def missing_outputs(self) -> list[str]:
        """当前阶段缺少的必填产物（人类可读描述列表）。"""
        meta = PHASE_META.get(self.current_phase, {})
        req = meta.get("required_outputs", {}) or {}
        missing = []
        for k, desc in req.items():
            v = self.outputs.get(k)
            if v is None:
                missing.append(desc)
            elif isinstance(v, str) and not v.strip():
                missing.append(desc)
            elif isinstance(v, (list, dict)) and len(v) == 0:
                missing.append(desc)
            elif isinstance(v, bool) and v is False:
                missing.append(desc)
        return missing

    def can_advance(self) -> tuple[bool, list[str]]:
        miss = self.missing_outputs()
        return (len(miss) == 0, miss)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["messages"] = [m.as_dict() if isinstance(m, SessionMessage) else m for m in self.messages]
        d["candidates"] = [c.as_dict() if isinstance(c, Candidate) else c for c in self.candidates]
        d["editing"] = self.editing.as_dict() if self.editing else None
        d["phase_meta"] = PHASE_META.get(self.current_phase, {})
        d["missing_outputs"] = self.missing_outputs()
        d["can_advance"] = len(d["missing_outputs"]) == 0
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        msgs = [SessionMessage.from_dict(m) for m in (d.get("messages") or [])]
        cands = [Candidate.from_dict(c) for c in (d.get("candidates") or [])]
        return cls(
            id=str(d.get("id") or _new_id()),
            project_id=str(d.get("project_id", "")),
            scope=str(d.get("scope", "field")),
            field_no=d.get("field_no"),
            chapter=d.get("chapter"),
            volume_no=d.get("volume_no"),
            title=str(d.get("title", "") or ""),
            current_phase=str(d.get("current_phase", SessionPhase.SETTLE_UP.value)),
            phase_status=dict(d.get("phase_status") or {}),
            outputs=dict(d.get("outputs") or {}),
            messages=msgs,
            status=str(d.get("status", "active")),
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
            artifacts=dict(d.get("artifacts") or {}),
            candidates=cands,
            editing=EditingState.from_dict(d.get("editing")),
            locked_cards=dict(d.get("locked_cards") or {}),
            source_material=dict(d.get("source_material") or {}),
        )


class SessionStore:
    """SessionState 的原子存取。root = <project>/sessions/"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"

    # ---- index ----
    def _load_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        try:
            return list(json.loads(self._index_path.read_text(encoding="utf-8")) or [])
        except Exception:
            return []

    def _save_index(self, items: list[dict]) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    def _upsert_index(self, s: SessionState) -> None:
        items = self._load_index()
        found = False
        for i, it in enumerate(items):
            if it.get("id") == s.id:
                items[i] = {
                    "id": s.id, "title": s.title, "scope": s.scope,
                    "field_no": s.field_no, "chapter": s.chapter, "volume_no": s.volume_no,
                    "current_phase": s.current_phase, "status": s.status,
                    "updated_at": s.updated_at, "can_advance": s.can_advance()[0],
                }
                found = True
                break
        if not found:
            items.append({
                "id": s.id, "title": s.title, "scope": s.scope,
                "field_no": s.field_no, "chapter": s.chapter, "volume_no": s.volume_no,
                "current_phase": s.current_phase, "status": s.status,
                "created_at": s.created_at, "updated_at": s.updated_at,
                "can_advance": s.can_advance()[0],
            })
        self._save_index(items)

    # ---- CRUD ----
    def _path(self, sid: str) -> Path:
        return self.root / f"{sid}.json"

    def get(self, sid: str) -> SessionState | None:
        p = self._path(sid)
        if not p.exists():
            return None
        try:
            return SessionState.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, s: SessionState) -> None:
        s.updated_at = _now()
        if not s.created_at:
            s.created_at = s.updated_at
        p = self._path(s.id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        self._upsert_index(s)

    def list_sessions(self, *, scope: str | None = None,
                      field_no: int | None = None,
                      chapter: int | None = None,
                      status: str | None = None) -> list[dict]:
        items = self._load_index()
        out = []
        for it in items:
            if scope and it.get("scope") != scope:
                continue
            if field_no is not None and it.get("field_no") != field_no:
                continue
            if chapter is not None and it.get("chapter") != chapter:
                continue
            if status and it.get("status") != status:
                continue
            out.append(it)
        out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return out

    def create(self, *, project_id: str, scope: str,
               field_no: int | None = None, chapter: int | None = None,
               title: str = "", start_phase: str | None = None) -> SessionState:
        """创建新会话。默认从该 scope 的第一个阶段开始。"""
        if scope == SessionScope.FIELD.value:
            start = start_phase or SessionPhase.SETTLE_UP.value
        else:
            start = start_phase or SessionPhase.DAILY_WRITE.value
        s = SessionState(
            id=_new_id(), project_id=project_id, scope=scope,
            field_no=field_no, chapter=chapter, title=title or f"会话 {scope}",
            current_phase=start,
        )
        s.add_message("system", f"会话已创建，当前阶段：{PHASE_META[start]['title']}。",
                      kind="gate", meta={"phase": start})
        self.save(s)
        return s

    def advance_phase(self, sid: str) -> SessionState:
        """推进到下一阶段。当前阶段门控未通过时抛 ValueError。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        ok, miss = s.can_advance()
        if not ok:
            raise ValueError(f"门控未通过，还差：{'; '.join(miss)}")
        meta = PHASE_META[s.current_phase]
        nxt = meta.get("next_phase")
        if nxt is None:
            s.status = "completed"
            s.add_message("system", f"阶段「{meta['title']}」完成，会话结束。",
                          kind="gate", meta={"phase": s.current_phase, "final": True})
        else:
            prev = s.current_phase
            s.current_phase = nxt
            s.add_message("system",
                          f"已完成「{PHASE_META[prev]['title']}」，进入「{PHASE_META[nxt]['title']}」。",
                          kind="gate", meta={"from_phase": prev, "to_phase": nxt})
        self.save(s)
        return s

    def rollback_phase(self, sid: str, to_phase: str) -> SessionState:
        """回退到前一阶段（允许作者改完再前进）。不清空已填产物。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        if to_phase not in PHASE_META:
            raise ValueError(f"未知阶段：{to_phase}")
        prev = s.current_phase
        s.current_phase = to_phase
        s.add_message("system",
                      f"作者要求回退：从「{PHASE_META[prev]['title']}」回到「{PHASE_META[to_phase]['title']}」。",
                      kind="gate", meta={"from_phase": prev, "to_phase": to_phase})
        if s.status == "completed":
            s.status = "active"
        self.save(s)
        return s

    # ---- M1 新增：候选 / 编辑态 / 锁记录 ----

    def add_candidates(self, sid: str, cands: list[Candidate]) -> list[Candidate]:
        """把一批候选加入会话；超过上限时清理最早的旧候选（避免文件膨胀）。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        now = _now()
        for c in cands:
            if not c.id:
                c.id = _new_id()
            if not c.created_at:
                c.created_at = now
            s.candidates.append(c)
        # 保留最近 MAX_CANDIDATES_PER_SESSION 个
        if len(s.candidates) > MAX_CANDIDATES_PER_SESSION:
            s.candidates = s.candidates[-MAX_CANDIDATES_PER_SESSION:]
        # 发一条系统消息记录候选 id
        s.add_message(
            "ai",
            f"【候选生成】为「{cands[0].card_key if cands else '卡片'}」准备了 {len(cands)} 个版本，请挑选。",
            kind="candidates",
            meta={
                "card_key": cands[0].card_key if cands else "",
                "candidate_ids": [c.id for c in cands],
            },
        )
        self.save(s)
        return cands

    def start_editing(self, sid: str, card_key: str,
                      candidate_id: str | None = None,
                      initial_text: str = "") -> EditingState:
        """进入某张卡的编辑态；candidate_id=None 表示从空白/手动输入开始。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        text = initial_text
        if candidate_id:
            cand = next((c for c in s.candidates if c.id == candidate_id), None)
            if cand is None:
                raise ValueError(f"候选不存在：{candidate_id}")
            if not text:
                text = cand.content
        s.editing = EditingState(
            candidate_id=candidate_id,
            card_key=card_key,
            current_text=text,
            started_at=_now(),
            edit_round=0,
        )
        s.add_message("system",
                      f"开始编辑「{card_key}」" + ("（来自候选）" if candidate_id else "（手动开编）"),
                      kind="card_edit",
                      meta={"card_key": card_key, "candidate_id": candidate_id, "action": "start"})
        self.save(s)
        return s.editing

    def update_editing_text(self, sid: str, text: str) -> SessionState:
        """作者手动修改编辑态文本（不记录消息，避免刷；调用方自己控制提示）。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        if s.editing is None:
            raise ValueError("当前没有正在编辑的卡片")
        s.editing.current_text = text
        self.save(s)
        return s

    def bump_edit_round(self, sid: str, *, suggestion_msg_id: str | None = None,
                        fork_taken: str | None = None) -> SessionState:
        """AI 给出建议/两版改法后，编辑轮次+1；记录最近建议消息 id。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        if s.editing is None:
            raise ValueError("当前没有正在编辑的卡片")
        s.editing.edit_round += 1
        if suggestion_msg_id:
            s.editing.last_suggestion_msg_id = suggestion_msg_id
        if fork_taken:
            s.editing.selected_fork = fork_taken
        self.save(s)
        return s

    def end_editing(self, sid: str) -> SessionState:
        """退出编辑态但不锁（用户切换卡片/关闭会话）。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        if s.editing is not None:
            s.add_message("system",
                          f"暂停编辑「{s.editing.card_key}」（内容已保留）。",
                          kind="card_edit",
                          meta={"card_key": s.editing.card_key, "action": "pause"})
        s.editing = None
        self.save(s)
        return s

    def record_lock(self, sid: str, card_key: str, msg_id: str) -> SessionState:
        """卡片锁定后记录索引；不做真正写入（写入由 API 层调 FieldStore/DeskStore）。"""
        s = self.get(sid)
        if s is None:
            raise ValueError("会话不存在")
        s.locked_cards[card_key] = msg_id
        s.add_message("system", f"卡片「{card_key}」已锁定。",
                      kind="card_edit",
                      meta={"card_key": card_key, "action": "lock", "msg_id": msg_id})
        # 锁完退出编辑态
        s.editing = None
        self.save(s)
        return s
