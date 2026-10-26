"""COC 多轮跑团会话存储（Phase 3 + 多 PL 扩展）。

每场跑团是一个独立会话（per field_no），包含：
- setup: 模组方案 / 玩家人物卡（支持 2-4 个 PL）/ 初始 SAN / HP
- turns: 回合序列，KP 与 PL 交替（PL 回合带 player_idx 指向当前行动玩家）
- rolls: 骰子记录（由 KP 文本解析出）
- clues: 已发现线索（按 day 聚合）
- players_state: 每个玩家独立的 {san, hp, mp, name,...}
- active_player_idx: 当前该谁行动（KP 回应后轮转到下一位 PL）
- finale: 跑团结束后产出的真相摘要（喂给 coc_outcome 单轮总结层）
- status: pending/running/paused/finalized/aborted

产物落到 <project>/fields/coc/<field_no>.json，与 FieldRunStore 的 nodes/ 并存但独立：
coc 是一个"可中断可重开"的交互过程，产物是可变的；NodeArtifact 是节点单次跑完即冻结。

向后兼容：
- 旧代码仍然可以读写 state.san / state.hp / state.mp / state.player_card：
  它们都通过 @property 代理到 players_state[active_player_idx] 与 player_cards[0]。
- player_card_render() 保留为等价于 players_render()，调用方无需改。
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


# 一轮里的单条发言（KP 或 PL 或 author 介入）
@dataclass
class CocTurn:
    turn_no: int                   # 1-based，全局递增
    role: str                      # "keeper" | "investigator" | "author" | "system"
    text: str
    meta: dict = field(default_factory=dict)  # dice rolls, day, sanity_loss, player_idx, etc.
    created_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CocTurn":
        return cls(
            turn_no=int(d.get("turn_no", 0)),
            role=str(d.get("role", "")),
            text=str(d.get("text", "") or ""),
            meta=dict(d.get("meta") or {}),
            created_at=str(d.get("created_at", "") or ""),
        )


@dataclass
class CocRoll:
    """解析自 KP 文本的一次骰子。"""
    skill: str       # 侦查/灵感/SAN/意志/...
    rolled: int      # 1D100 结果
    target: int | None  # 目标值
    result: str      # "success" | "failure" | "hard_success" | "fumble" | "unknown"
    san_loss: int = 0  # SAN 检定失败时的损失
    turn_no: int = 0
    player_idx: int | None = None  # 若可归属到具体玩家则记录

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_PLAYER_CARD = {
    "name": "唐迟",
    "occupation": "送伞人（市井怂人）",
    "san_max": 99,
    "hp_max": 12,
    "mp_max": 12,
    "luck": 50,
    # 关键技能
    "skills": {
        "侦查": 50,
        "灵感": 45,
        "聆听": 40,
        "图书馆": 30,
        "话术": 35,
        "闪避": 40,
        "斗殴": 25,
        "意志": 50,
    },
}


def make_player_card(
    name: str,
    occupation: str,
    *,
    san_max: int = 70,
    hp_max: int = 12,
    mp_max: int = 12,
    luck: int = 50,
    skills: dict | None = None,
) -> dict:
    """辅助函数：构造一张调查员卡。

    - san_max 默认 70（常规 COC 调查员，区别于唐迟默认 99）
    - skills 若不传则使用一套通用技能基线
    """
    base_skills = {
        "侦查": 40,
        "灵感": 45,
        "聆听": 40,
        "图书馆": 35,
        "话术": 30,
        "闪避": 35,
        "斗殴": 30,
        "意志": 50,
    }
    if skills:
        merged = dict(base_skills)
        merged.update(skills)
    else:
        merged = base_skills
    return {
        "name": name,
        "occupation": occupation,
        "san_max": int(san_max),
        "hp_max": int(hp_max),
        "mp_max": int(mp_max),
        "luck": int(luck),
        "skills": merged,
    }


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_player_cards(raw_cards) -> list[dict]:
    """把输入的 player_cards 统一成 list[dict]。

    - 传入 None / 空 → 返回 [DEFAULT_PLAYER_CARD 拷贝]
    - 传入单个 dict → 包装成 [{...}]
    - 传入 list → 每张卡补齐 name/occupation 等默认字段
    """
    if raw_cards is None:
        return [dict(DEFAULT_PLAYER_CARD)]
    if isinstance(raw_cards, dict):
        # 旧单卡格式
        card = dict(DEFAULT_PLAYER_CARD)
        card.update(raw_cards)
        card.setdefault("name", card.get("name") or "唐迟")
        return [card]
    if isinstance(raw_cards, list):
        out: list[dict] = []
        for c in raw_cards:
            if not isinstance(c, dict):
                continue
            card = dict(DEFAULT_PLAYER_CARD)
            card.update(c)
            card.setdefault("name", card.get("name") or f"调查员{len(out)+1}")
            card.setdefault("occupation", card.get("occupation") or "路人")
            out.append(card)
        if not out:
            out = [dict(DEFAULT_PLAYER_CARD)]
        return out
    return [dict(DEFAULT_PLAYER_CARD)]


def _init_players_state(player_cards: list[dict],
                        san_overrides: dict[int, int] | None = None,
                        hp_overrides: dict[int, int] | None = None,
                        mp_overrides: dict[int, int] | None = None,
                        san_start_penalty: int = 0) -> list[dict]:
    """根据玩家卡初始化每个玩家的运行时状态。"""
    states: list[dict] = []
    for idx, card in enumerate(player_cards):
        san_max = int(card.get("san_max", 99))
        hp_max = int(card.get("hp_max", 12))
        mp_max = int(card.get("mp_max", 12))
        san = int(san_overrides.get(idx)) if san_overrides and idx in san_overrides else max(0, san_max - san_start_penalty)
        hp = int(hp_overrides.get(idx)) if hp_overrides and idx in hp_overrides else hp_max
        mp = int(mp_overrides.get(idx)) if mp_overrides and idx in mp_overrides else mp_max
        states.append({
            "name": card.get("name", f"调查员{idx+1}"),
            "san": san,
            "hp": hp,
            "mp": mp,
            "san_max": san_max,
            "hp_max": hp_max,
            "mp_max": mp_max,
            "alive": hp > 0 and san > 0,
            "insane": False,
        })
    return states


@dataclass
class CocRunState:
    field_no: int
    # ---- setup ----
    adapt_vid: str | None = None          # 选定的 adapt_scenario vid
    setup_meta: dict = field(default_factory=dict)
    # 多玩家卡（推荐使用）：2-4 张
    player_cards: list[dict] = field(default_factory=lambda: [dict(DEFAULT_PLAYER_CARD)])
    variant_seed: dict = field(default_factory=dict)  # 每场仿真的随机扰动种子（NPC/反转/伏笔/心境）
    session_index: int = 1                # 1..N，表示第几场候选（用于日志/标题）
    novelized: bool = True                # 是否中等小说化笔调（新默认 True，旧路径不感知即可）
    # ---- 多玩家运行时状态 ----
    players_state: list[dict] = field(default_factory=list)
    active_player_idx: int = 0            # 当前该谁行动
    # ---- 全局状态 ----
    current_day: int = 1
    clues_found: list[dict] = field(default_factory=list)   # [{day, text, turn_no}]
    npcs: dict = field(default_factory=dict)                # name -> {status:"alive/hurt/dead",...}
    obsession_uses: int = 0
    obsession_cost_notes: list[str] = field(default_factory=list)
    # ---- 回合 ----
    turns: list[CocTurn] = field(default_factory=list)
    next_role: str = "keeper"   # 下一步该谁发言：keeper 开场
    # ---- beat（第二步）----
    # beat = 一次完整因果循环（PL 试探 → KP 裁决 → 剧情状态变化 → PL 再试探），
    # 也就是从前的「回合对」。**改名不改行为**：一个 beat = 一次
    # step_keeper + step_investigator，所以它与旧的 steps_done 数值一致。
    #
    # 为什么要有 beats：旧实现只留 turns（纯文本），KP 下一轮拿不到
    # 「已经查清了什么」，导致 PL 在已确认的事上重复试探。beats 存结构化
    # 裁决记录（known_now/unknown_now），并在下一轮回注给 KP 当约束。
    beats: list[dict] = field(default_factory=list)
    beat_budget: int = 24        # 语义预算：故事该讲多长（作者可覆盖）
    beats_done: int = 0          # 已完成的 beat 数
    last_story_phase: str = ""   # 展开中|转折点|已收束；留空=按展开中处理
    consecutive_transition: int = 0   # 连续「转折点」的 beat 数
    # ---- 决策链（第三步）----
    # roll_log：真骰子记录。每次判定一条，可回放、可审计。
    # 旧实现（parse_dice_rolls）是从 KP 中文文本里正则抠出来的，
    # 骰子本质是模型"想"的——不可复现，且模型倾向让 PL 成功。
    roll_log: list[dict] = field(default_factory=list)
    # storyboard：决策链分镜表。每拍一行，是这一层真正的产物。
    storyboard: list[dict] = field(default_factory=list)
    # no_progress_streak：连续「没有信息增量」的拍数，用于掐断空转。
    no_progress_streak: int = 0
    # ---- meta ----
    # status 语义（failed 是本次新增，与 paused 刻意区分）：
    #   pending          还没跑
    #   running          跑团中
    #   paused           跑过、被作者叫停，可续跑
    #   awaiting_author  跑够数了，等作者决定（正常终点）
    #   failed           出错了，作者需要决定重跑还是放弃（异常终点）
    #   finalized        已定稿
    #   aborted          已放弃
    # paused 与 failed 必须分开：前者是「正常的暂停」，后者是「需要重跑」，
    # 前端要显示不同的操作入口（续跑 vs 重跑）。
    status: str = "pending"
    max_turns: int = 40         # 硬上限，防止死循环
    # final_summary 只装**真正的梗概**。异常文本一律进state.error，
    # 绝不能写进这里——写进去等于把失败伪装成成功产物（历史事故：
    # 前端拿这个字段当卡片正文，于是 traceback 被当成「一份有内容的梗概」）。
    final_summary: str = ""
    # 通稿层产物（第二步）：8000–12000 字连续叙事，供作者「读懂 + 直接当创作底本」。
    # 与 final_summary **并存不替代**——后者是「第N幕」结构体，
    # 下游（EmbodyView / day_breakdown）在读它，格式不能动。
    full_narrative: str = ""
    final_parsed: Any = None
    final_day_breakdown: list[dict] = field(default_factory=list)  # [{day, summary}]
    survivors: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None

    # ---- 向后兼容属性：代理到 active player ----
    @property
    def player_card(self) -> dict:
        """[向后兼容] 返回第一个玩家的卡片（旧单玩家场景等价）。"""
        return self.player_cards[0] if self.player_cards else dict(DEFAULT_PLAYER_CARD)

    @player_card.setter
    def player_card(self, val: dict) -> None:
        """[向后兼容] 直接赋单卡 → 重置为单玩家模式。"""
        cards = _normalize_player_cards(val)
        self.player_cards = cards
        self.players_state = _init_players_state(cards)
        self.active_player_idx = 0

    @property
    def san(self) -> int:
        """[向后兼容] 当前行动玩家的 SAN。"""
        if not self.players_state:
            return int(self.player_card.get("san_max", 99))
        return int(self.players_state[self.active_player_idx]["san"])

    @san.setter
    def san(self, v: int) -> None:
        if self.players_state:
            p = self.players_state[self.active_player_idx]
            p["san"] = max(0, int(v))
            p["insane"] = p["san"] <= 0
            p["alive"] = p["hp"] > 0 and p["san"] > 0

    @property
    def hp(self) -> int:
        """[向后兼容] 当前行动玩家的 HP。"""
        if not self.players_state:
            return int(self.player_card.get("hp_max", 12))
        return int(self.players_state[self.active_player_idx]["hp"])

    @hp.setter
    def hp(self, v: int) -> None:
        if self.players_state:
            p = self.players_state[self.active_player_idx]
            p["hp"] = max(0, int(v))
            p["alive"] = p["hp"] > 0 and p["san"] > 0

    @property
    def mp(self) -> int:
        """[向后兼容] 当前行动玩家的 MP。"""
        if not self.players_state:
            return int(self.player_card.get("mp_max", 12))
        return int(self.players_state[self.active_player_idx]["mp"])

    @mp.setter
    def mp(self, v: int) -> None:
        if self.players_state:
            self.players_state[self.active_player_idx]["mp"] = max(0, int(v))

    # ---- 方法 ----
    def __post_init__(self) -> None:
        # 兜底：若 players_state 为空则按 player_cards 初始化
        if not self.players_state and self.player_cards:
            self.players_state = _init_players_state(self.player_cards)

    def add_turn(self, role: str, text: str, meta: dict | None = None) -> CocTurn:
        t = CocTurn(
            turn_no=len(self.turns) + 1,
            role=role,
            text=text,
            meta=dict(meta or {}),
            created_at=_now(),
        )
        # PL 回合自动记录当前行动玩家（即便调用方已传 player_idx，也要补 player_name）
        if role == "investigator":
            if "player_idx" not in t.meta:
                t.meta["player_idx"] = self.active_player_idx
            pidx = t.meta.get("player_idx", self.active_player_idx)
            if not t.meta.get("player_name"):
                try:
                    t.meta["player_name"] = (
                        (self.players_state[pidx].get("name") if self.players_state and pidx < len(self.players_state) else None)
                        or (self.player_cards[pidx].get("name") if self.player_cards and pidx < len(self.player_cards) else None)
                        or f"调查员{pidx+1}"
                    )
                except Exception:
                    t.meta["player_name"] = "调查员"
        self.turns.append(t)
        self.updated_at = _now()
        return t

    def advance_player(self) -> int:
        """KP 回应后调用：轮转到下一个存活玩家。返回新的 active_player_idx。"""
        if not self.players_state:
            return 0
        n = len(self.players_state)
        nxt = (self.active_player_idx + 1) % n
        # 最多循环一圈，跳过已死亡/疯狂的玩家；若全员倒下就停在当前
        for _ in range(n):
            p = self.players_state[nxt]
            if p.get("alive", True) and not p.get("insane", False):
                self.active_player_idx = nxt
                return nxt
            nxt = (nxt + 1) % n
        self.active_player_idx = nxt
        return nxt

    def current_player_name(self) -> str:
        if not self.players_state:
            return (self.player_cards[0].get("name") if self.player_cards else None) or "调查员1"
        return self.players_state[self.active_player_idx].get("name",
               (self.player_cards[self.active_player_idx].get("name") if self.player_cards and self.active_player_idx < len(self.player_cards) else None)
               or f"调查员{self.active_player_idx+1}")

    def current_player_card(self) -> dict:
        if self.player_cards:
            return self.player_cards[self.active_player_idx]
        return self.player_card

    def apply_san_loss_to_active(self, loss: int) -> int:
        """给当前行动玩家扣 SAN，返回扣后剩余。"""
        if not self.players_state or loss <= 0:
            return self.san
        self.san = self.san - loss
        return self.san

    def transcript(self, last_n: int | None = None) -> str:
        """把回合拼成跑团日志（喂给下一层 / 喂给 outcome）。

        - keeper 回合：标注【守密人】
        - investigator 回合：标注具体角色名（优先 player_name meta，回退到 player_cards 名）
        """
        turns = self.turns if last_n is None else self.turns[-last_n:]
        lines: list[str] = []
        for t in turns:
            if t.role == "keeper":
                lines.append(f"【守密人 回合 {t.turn_no}】\n{t.text}")
            elif t.role == "investigator":
                pname = t.meta.get("player_name")
                if not pname:
                    pidx = t.meta.get("player_idx")
                    if isinstance(pidx, int) and self.player_cards and pidx < len(self.player_cards):
                        pname = self.player_cards[pidx].get("name") or f"调查员{pidx+1}"
                    else:
                        pname = "调查员"
                lines.append(f"【{pname} 行动 {t.turn_no}】\n{t.text}")
            elif t.role == "author":
                lines.append(f"【作者介入】{t.text}")
            elif t.role == "system":
                lines.append(f"【系统】{t.text}")
        return "\n\n".join(lines)

    def players_render(self) -> str:
        """渲染所有调查员卡+当前状态（多玩家版本）。"""
        if not self.player_cards:
            return "# 无调查员"
        lines = ["# 调查员团队状态"]
        lines.append(f"当前天数：Day{self.current_day}  金手指已用 {self.obsession_uses} 次")
        lines.append(f"当前行动方：{self.current_player_name()}")
        lines.append("")
        for idx, (card, ps) in enumerate(zip(self.player_cards, self.players_state or [])):
            marker = " ▶" if idx == self.active_player_idx else ""
            name = ps.get("name") or card.get("name", f"调查员{idx+1}")
            lines.append(f"## {idx+1}. {name}（{card.get('occupation','')}）{marker}")
            san_max = int(ps.get("san_max") or card.get("san_max", 99))
            hp_max = int(ps.get("hp_max") or card.get("hp_max", 12))
            mp_max = int(ps.get("mp_max") or card.get("mp_max", 12))
            status_flags = []
            if ps.get("insane"):
                status_flags.append("疯狂")
            if not ps.get("alive", True):
                status_flags.append("死亡")
            flag_str = f"  [{'/'.join(status_flags)}]" if status_flags else ""
            lines.append(
                f"HP {ps.get('hp', hp_max)}/{hp_max}  "
                f"MP {ps.get('mp', mp_max)}/{mp_max}  "
                f"SAN {ps.get('san', san_max)}/{san_max}{flag_str}"
            )
            sk = card.get("skills") or {}
            if sk:
                sk_str = "、".join(f"{k}{v}" for k, v in sk.items())
                lines.append(f"技能：{sk_str}")
        if self.obsession_cost_notes:
            lines.append("")
            lines.append("## 团队已承受代价")
            for n in self.obsession_cost_notes[-5:]:
                lines.append(f"- {n}")
        if self.clues_found:
            lines.append("")
            lines.append("## 已发现线索")
            for c in self.clues_found[-10:]:
                lines.append(f"- Day{c.get('day','?')}：{c.get('text','')}")
        return "\n".join(lines)

    def player_card_render(self) -> str:
        """[向后兼容] 旧接口，等价于 players_render()。"""
        return self.players_render()

    def as_dict(self) -> dict:
        d = asdict(self)
        d["turns"] = [t.as_dict() if isinstance(t, CocTurn) else t for t in self.turns]
        # 为了旧读取方不崩，同时导出 player_card（取第一个）与 san/hp/mp（取 active）
        d["player_card"] = self.player_card
        d["san"] = self.san
        d["hp"] = self.hp
        d["mp"] = self.mp
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CocRunState":
        # 兼容旧存档：只有 player_card 没有 player_cards
        raw_cards = d.get("player_cards")
        if not raw_cards:
            legacy_card = d.get("player_card")
            raw_cards = [legacy_card] if legacy_card else None
        player_cards = _normalize_player_cards(raw_cards)

        # 兼容旧 players_state：若无则从卡初始化，并用顶层 san/hp/current_day 覆盖 idx=0
        legacy_san = d.get("san")
        legacy_hp = d.get("hp")
        legacy_mp = d.get("mp")
        san_start_penalty = int((d.get("variant_seed") or {}).get("san_start_penalty", 0) or 0)
        ps_raw = d.get("players_state")
        if ps_raw and isinstance(ps_raw, list) and len(ps_raw) == len(player_cards):
            players_state: list[dict] = []
            for i, p in enumerate(ps_raw):
                card = player_cards[i]
                san_max = int(card.get("san_max", 99))
                hp_max = int(card.get("hp_max", 12))
                mp_max = int(card.get("mp_max", 12))
                players_state.append({
                    "name": p.get("name") or card.get("name", f"调查员{i+1}"),
                    "san": int(p.get("san", san_max - san_start_penalty)),
                    "hp": int(p.get("hp", hp_max)),
                    "mp": int(p.get("mp", mp_max)),
                    "san_max": int(p.get("san_max", san_max)),
                    "hp_max": int(p.get("hp_max", hp_max)),
                    "mp_max": int(p.get("mp_max", mp_max)),
                    "alive": bool(p.get("alive", True)),
                    "insane": bool(p.get("insane", False)),
                })
        else:
            san_ov: dict[int, int] = {}
            hp_ov: dict[int, int] = {}
            mp_ov: dict[int, int] = {}
            if legacy_san is not None:
                try:
                    san_ov[0] = int(legacy_san)
                except Exception:
                    pass
            if legacy_hp is not None:
                try:
                    hp_ov[0] = int(legacy_hp)
                except Exception:
                    pass
            if legacy_mp is not None:
                try:
                    mp_ov[0] = int(legacy_mp)
                except Exception:
                    pass
            players_state = _init_players_state(
                player_cards,
                san_overrides=san_ov, hp_overrides=hp_ov, mp_overrides=mp_ov,
            )

        turns = [CocTurn.from_dict(t) for t in (d.get("turns") or [])]
        return cls(
            field_no=int(d.get("field_no", 0)),
            adapt_vid=d.get("adapt_vid"),
            setup_meta=dict(d.get("setup_meta") or {}),
            player_cards=player_cards,
            variant_seed=dict(d.get("variant_seed") or {}),
            session_index=int(d.get("session_index", 1)),
            novelized=bool(d.get("novelized", True)),
            players_state=players_state,
            active_player_idx=int(d.get("active_player_idx", 0)),
            current_day=int(d.get("current_day", 1)),
            clues_found=[dict(c) for c in (d.get("clues_found") or [])],
            npcs=dict(d.get("npcs") or {}),
            obsession_uses=int(d.get("obsession_uses", 0)),
            obsession_cost_notes=[str(s) for s in (d.get("obsession_cost_notes") or [])],
            turns=turns,
            next_role=str(d.get("next_role", "keeper")),
            status=str(d.get("status", "pending")),
            max_turns=int(d.get("max_turns", 40)),
            final_summary=str(d.get("final_summary", "") or ""),
            final_parsed=d.get("final_parsed"),
            final_day_breakdown=[dict(x) for x in (d.get("final_day_breakdown") or [])],
            survivors=[str(s) for s in (d.get("survivors") or [])],
            created_at=str(d.get("created_at", "") or ""),
            updated_at=str(d.get("updated_at", "") or ""),
            error=d.get("error"),
        )


class CocRunStore:
    """CocRunState 的原子存取。root = <project>/fields/coc/"""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, field_no: int) -> Path:
        return self.root / f"{field_no}.json"

    def exists(self, field_no: int) -> bool:
        return self._path(field_no).exists()

    def get(self, field_no: int) -> CocRunState | None:
        p = self._path(field_no)
        if not p.exists():
            return None
        return CocRunState.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, state: CocRunState) -> None:
        state.updated_at = _now()
        if not state.created_at:
            state.created_at = state.updated_at
        p = self._path(state.field_no)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def delete(self, field_no: int) -> None:
        p = self._path(field_no)
        if p.exists():
            p.unlink()


# ============================================================
# KP 文本骰子解析
# ============================================================

# 匹配形如「投掷 侦查：42/50 成功」「侦查检定 1D100=73 vs 50 失败」「SAN 检定 1d100=95/60 大失败，失去 1d6=4 SAN」
_ROLL_RE = re.compile(
    r"(?:投掷|投|检定|roll)?"
    r"\s*"
    r"(?P<skill>[\u4e00-\u9fa5A-Za-z]{2,12})"   # 技能名
    r"(?:检定)?"
    r"[:：\s]*"
    r"(?:1[dD]100\s*[=＝]?)?\s*"
    r"(?P<rolled>\d{1,3})"
    r"\s*[/／vsVS]\s*"
    r"(?P<target>\d{1,3})"
    r"[^\n。；]*?"
    r"(?P<result>大失败|大成功|极难成功|困难成功|成功|失败)"
)

# 专门在 KP 文本里找 SAN 损失：支持「失去 1d6 SAN」「失去 1d4+1=3 SAN」「失去 5 SAN」
_SAN_LOSS_RE = re.compile(
    r"失去\s*(?:(?:1[dD](?P<dice>[346]))(?:\s*[=＝]\s*(?P<dice_rolled>\d{1,2}))?|(?P<fixed>\d{1,2}))\s*SAN",
    re.IGNORECASE,
)


def parse_dice_rolls(text: str) -> list[CocRoll]:
    """从 KP 描述里抽取骰子事件。best-effort——解析不到不影响运行。
    每个 dice roll 之后扫描到下一个「。」或换行或下一次「投掷/检定」之前的窗口，
    从中提取 SAN 损失（固定值或 1d3/1d4/1d6）。

    注意：NOVELIZED 模式下 KP 文本不应出现骰子术语，本函数在那种模式下大概率返回空列表，是正常现象。
    """
    rolls: list[CocRoll] = []
    if not text:
        return rolls
    for m in _ROLL_RE.finditer(text):
        try:
            rolled = int(m.group("rolled"))
            target = int(m.group("target"))
        except Exception:
            continue
        result_label = m.group("result") or ""
        result_map = {
            "大成功": "critical",
            "极难成功": "extreme",
            "困难成功": "hard",
            "成功": "success",
            "失败": "failure",
            "大失败": "fumble",
        }
        result = result_map.get(result_label, "unknown")
        san_loss = 0
        skill = m.group("skill") or ""
        if "SAN" in skill.upper():
            # 在当前位置后至下一句/下一投掷之间搜 SAN 损失
            window_end_candidates = []
            for pat in (r"[。\n]", r"(?:投掷|投|检定)"):
                nm = re.search(pat, text[m.end():])
                if nm:
                    window_end_candidates.append(m.end() + nm.start())
            end = min(window_end_candidates) if window_end_candidates else min(len(text), m.end() + 80)
            window = text[m.start():end]
            sm = _SAN_LOSS_RE.search(window)
            if sm:
                fixed = sm.group("fixed")
                if fixed:
                    try:
                        san_loss = int(fixed)
                    except Exception:
                        san_loss = 0
                else:
                    dr = sm.group("dice_rolled")
                    if dr:
                        try:
                            san_loss = int(dr)
                        except Exception:
                            san_loss = 0
                    else:
                        try:
                            dice = int(sm.group("dice"))
                            san_loss = random.randint(1, dice)
                        except Exception:
                            san_loss = random.randint(1, 6)
        rolls.append(CocRoll(
            skill=skill,
            rolled=rolled,
            target=target,
            result=result,
            san_loss=san_loss,
        ))
    return rolls


# 自动检测阶段推进（支持「Day3」「第3天」「第三日」「终局/第七日」——兼容旧日志与新双轨语义）
_DAY_RE = re.compile(r"(?:Day\s*([1-7])|第\s*([1-7一二三四五六七])\s*[天日])")
_CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7}


def detect_day(text: str) -> int | None:
    m = _DAY_RE.search(text or "")
    if not m:
        return None
    a = m.group(1) or m.group(2)
    try:
        return int(a)
    except Exception:
        return _CN_NUM.get(a)


# ============================================================
# 每场仿真的随机种子：决定本场额外 NPC / 伏笔 / 反转 / 唐迟心境
# ============================================================

NPC_POOL = [
    {"name": "送伞的哑巴阿婆", "secret": "她就是上一场留下的执念残响", "stance": "看似慈祥实则引诱"},
    {"name": "穿中山装的修伞匠", "secret": "知道终局那日伞骨接法但要拿一条回忆换", "stance": "市侩、守旧"},
    {"name": "穿红衣的小女孩", "secret": "她是故事开场就死了的人，一直在找哥哥", "stance": "天真、执着、令人心疼"},
    {"name": "打油纸伞的卖花姑娘", "secret": "花是用亡者的指甲种的", "stance": "笑里藏刀"},
    {"name": "巡夜的老警察", "secret": "故事开始前就死了，反复巡逻同一条街", "stance": "僵硬、机械、会重复同一句话"},
    {"name": "巷口算命的瞎子", "secret": "他能看见每个入巷者的死期", "stance": "慈悲但不肯直说"},
    {"name": "开裁缝铺的旗袍女人", "secret": "她缝的寿衣用的是活人皮肤", "stance": "客气、诡异"},
    {"name": "修自行车的外地青年", "secret": "他是唯一的活人但已经困在巷里很久", "stance": "慌张、想逃"},
    {"name": "井边打水的白发老太", "secret": "井水是忘川，喝一口就忘了自己是谁", "stance": "沉默、招手"},
    {"name": "卖馄饨的夜摊老板", "secret": "馄饨馅是人肉但他自己不知道", "stance": "热情、健谈"},
]

TWIST_POOL = [
    "NPC 在关键时刻背叛众人——他/她其实是实体的化身",
    "死者死前留了反转让附身者踩中陷阱",
    "某个 NPC 其实是其中一位调查员自己前几场遗留的执念",
    "时间在故事中段循环了两次，有人发现口袋里多了一把伞",
    "终局反转：杀死实体的代价是成为下一任守巷人",
    "大家以为救了的人其实早死了，众人一直在跟空气说话",
    "金手指代价翻倍——每用一次听忆，遗忘一段人生",
    "实体用调查员最在意的人的声音诱他/她入瓮",
    "巷子里出现第二个某调查员，声称自己才是真的",
    "看似安全的庇护所（裁缝铺/馄饨摊）到故事后段变成致命陷阱",
]

FORESHADOW_POOL = [
    "开场某扇门上的划痕正好对应终局的死亡方式",
    "童谣歌词藏着实体的真名",
    "NPC 反复哼的调子是送葬曲，哼到最后一段就收命",
    "伞骨的裂纹随故事推进逐条增加，裂纹满数对应人亡",
    "井水能照出死人，第一次照见的是主角自己",
    "镜子里众人的倒影比真人慢半拍",
    "巷子里多出来的门牌号码是上一位死者的卒年",
    "每个 NPC 都少一根手指，凑起来正好开实体所在的锁",
    "雨停的瞬间是终局零时",
    "铜纽扣上的花纹是实体的眼睛，谁捡到就被盯上",
]

TANGCHI_MOODS = [
    "今日唐迟格外怂，想直接跑路但被执念拉住",
    "今日唐迟被刚失去的亲人回忆灼着，敢冒险但会做蠢事",
    "今日唐迟理智在线，会先观察再行动",
    "今日唐迟睡眠不足，SAN 阈值偏低、易怒",
    "今日唐迟刚救过人，带着盲目的正义感",
]

ENTRANCE_HOOKS = [
    "众人是被一个递伞的哑巴阿婆引入巷中的",
    "众人从一场梦里醒来，发现自己站在巷口",
    "众人追一个偷伞的小孩跑进巷子",
    "众人为了躲雨自己走进巷子",
    "众人是被一张夹在伞骨里的旧照片吸引过来的",
]


def generate_variant_seed(rng: random.Random | None = None,
                          *, flavor: str | None = None) -> dict:
    """生成一场仿真的差异化种子。返回字典可直接写入 CocRunState.variant_seed，
    并作为 REQUIRED material 喂给 KP 开场。

    flavor: 可选，"good" / "bad" / "distinct" 之类，用于批量时控制基调；
            None 则保持原随机行为。
    """
    r = rng or random
    extra_npcs = r.sample(NPC_POOL, k=r.randint(2, 3))
    twist = r.choice(TWIST_POOL)
    foreshadow = r.sample(FORESHADOW_POOL, k=r.randint(2, 3))
    mood = r.choice(TANGCHI_MOODS)
    hook = r.choice(ENTRANCE_HOOKS)

    if flavor == "good":
        ending_bias = "narrow_escape"
        san_pen = r.randint(0, 3)
        obs_start = 0
    elif flavor == "bad":
        ending_bias = r.choice(["bad", "costly"])
        san_pen = r.randint(5, 12)
        obs_start = r.randint(0, 1)
    elif flavor == "distinct":
        ending_bias = r.choice(["bittersweet", "tradeoff"])
        san_pen = r.randint(2, 8)
        obs_start = r.randint(0, 1)
    else:
        ending_bias = r.choice(["bad", "bittersweet", "costly", "tradeoff", "narrow_escape"])
        san_pen = r.randint(0, 10)
        obs_start = r.randint(0, 1)

    ending_label = {
        "bad": "黑暗结局：众人未能破局，巷子里多了一把伞",
        "bittersweet": "惨胜：破了局但付出不可逆的代价",
        "costly": "金手指代价型：靠执念硬换一线生机",
        "tradeoff": "交换结局：救了别人自己留下",
        "narrow_escape": "险胜：最后一秒逃出但心锚留下",
    }[ending_bias]
    return {
        "flavor": flavor or "random",
        "extra_npcs": extra_npcs,
        "core_twist": twist,
        "planted_foreshadows": foreshadow,
        "tangchi_mood": mood,
        "entrance_hook": hook,
        "ending_bias": ending_bias,
        "ending_label": ending_label,
        "san_start_penalty": san_pen,
        "obsession_start_uses": obs_start,
    }


def render_variant_seed(seed: dict) -> str:
    """把 seed 渲染成文本，作为 KP 开场的 REQUIRED 材料注入。

    **结局给的是「方向」不是「命令」**：方案 B 之后 seed 由 LLM 从副本核
    生成，KP 必须保留推翻权——故事该走向哪是跑出来的，不是预先钦定的。
    旧实现写死五种结局标签（「你这场必须险胜」），等于命令 KP 去凑框架。
    """
    if not seed:
        return ""
    lines = ["# 本场仿真差异化设定（守密人内部参考，不要直接念给玩家）", ""]
    lines.append(f"- 入场钩子：{seed.get('entrance_hook','')}")
    lines.append(f"- 主角开场心境：{seed.get('tangchi_mood','')}")
    lines.append(f"- 开场 SAN 扣减：{seed.get('san_start_penalty',0)}")
    lines.append(f"- 金手指已被动触发次数：{seed.get('obsession_start_uses',0)}")
    lines.append("")
    if seed.get("extra_npcs"):
        lines.append("## 本场额外登场 NPC")
        for n in seed["extra_npcs"]:
            lines.append(f"- {n['name']}（{n['stance']}；隐藏：{n['secret']}）")
        lines.append("")
    if seed.get("core_twist"):
        lines.append("## 本线核心反转（KP 在中后段逐步揭开）")
        lines.append(f"- {seed['core_twist']}")
        lines.append("")
    if seed.get("planted_foreshadows"):
        lines.append("## 要埋的伏笔（至少埋 2 个，回收要自然）")
        for f in seed["planted_foreshadows"]:
            lines.append(f"- {f}")
        lines.append("")
    # 结局：**方向 + 明确授权推翻**。没有 ending_direction 时（兜底 seed）
    # 整段省略，让 KP 完全自由发挥。
    direction = seed.get("ending_direction") or seed.get("ending_label") or ""
    if direction:
        lines.append("## 结局走向（这是倾向，不是命令）")
        lines.append(f"- {direction}")
        lines.append(
            "- **如果故事跑出了另一个更成立的结局，KP 可以推翻这条倾向**——"
            "结局由跑团决定，不由本设定决定。"
        )
    return "\n".join(lines)
