"""COC 真骰子引擎（决策链第三步）。

**为什么要真掷**：旧实现用正则从 KP 的中文文本里抠「投掷 侦查：63/100」，
骰子其实是模型"想"出来的。两个硬伤：

1. **不可复现** —— 同一模组两次跑，骰子不同且无法重放，事后追不了
   「为什么这场是这个结果」；
2. **判定不可信** —— 模型倾向让 PL 成功（戏剧性需要），实际成功率虚高，
   三条平行世界的分叉因此不是真的分叉，而是模型的叙事偏好。

真掷 = 代码掷 = 可复现 + 可审计 + 不受模型倾向影响。

**判定与 LLM 的分工**：KP 只提供**成功/失败两套后果文本**，代码掷骰决定
走哪一套。KP 无法决定成败 —— 这是本模块存在的根本理由。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, asdict

# ---- COC 七级难度（供 KP 声明用，代码不强制校验）----
DIFFICULTY_LABELS = ("极难", "困难", "常规", "容易")

# 七级结果，从高到低；判定时按此顺序命中即停
RESULT_CRITICAL = "critical"          # 大成功
RESULT_EXTREME = "extreme"            # 极难成功
RESULT_HARD = "hard"                  # 困难成功
RESULT_SUCCESS = "success"            # 成功
RESULT_FAILURE = "failure"            # 失败
RESULT_HARD_FAIL = "hard_fail"        # 困难失败
RESULT_EXTREME_FAIL = "extreme_fail"  # 极难失败
RESULT_FUMBLE = "fumble"              # 大失败

RESULT_LABELS = {
    RESULT_CRITICAL: "大成功",
    RESULT_EXTREME: "极难成功",
    RESULT_HARD: "困难成功",
    RESULT_SUCCESS: "成功",
    RESULT_FAILURE: "失败",
    RESULT_HARD_FAIL: "困难失败",
    RESULT_EXTREME_FAIL: "极难失败",
    RESULT_FUMBLE: "大失败",
}

# 大成功/大失败这一档需要成功/失败各自的极端条件单独处理，
# 放在普通线性规则之外。
_IS_SUCCESS = (RESULT_CRITICAL, RESULT_EXTREME, RESULT_HARD, RESULT_SUCCESS)
_IS_FAILURE = (RESULT_FUMBLE, RESULT_EXTREME_FAIL, RESULT_HARD_FAIL, RESULT_FAILURE)


@dataclass
class DiceCheck:
    """一次判定的完整记录。落进 state.roll_log，可回放。"""

    beat: int
    player_idx: int | None
    skill: str
    skill_value: int
    difficulty: str
    rolled: int            # 1D100 实际点数
    bonus: int             # 技能之外的情境加成（正负）
    target: int            # 实际比对的目标值 = skill_value + bonus
    result: str            # 七级结果之一
    player_idx_name: str = ""

    @property
    def ok(self) -> bool:
        return self.result in _IS_SUCCESS

    @property
    def label(self) -> str:
        return RESULT_LABELS.get(self.result, self.result)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        d["label"] = self.label
        return d

    def one_line(self) -> str:
        """作者可读的一行：技能值/点数/难度/结果。"""
        who = f"{self.player_idx_name} " if self.player_idx_name else ""
        diff = f"·{self.difficulty}" if self.difficulty else ""
        return f"{who}{self.skill} {self.rolled}/{self.target}{diff} → {self.label}"


def coc_check(rolled: int, skill_value: int) -> str:
    """COC 七级判定。给定 1D100 点数与目标值，返回结果等级。

    规则（**成功梯度优先**，从高到低匹配，命中即停）：
      大成功   r <= s/5
      极难成功 r <= s/2
      困难成功 r <= s
      成功     r <= s*0.8+20
      大失败   r == 100
      极难失败 r >= 96
      困难失败 r >= s*1.2+10
      失败     其余

    两个容易搞反的点：
    1. **成功梯度必须在 96+ 判定之前**。技能 90 的调查员掷出 96 时，
       按标准规则仍可能是「困难失败」而不是「大失败」——大失败只在
       高技能者也能被一击致命的场景成立。先判成功才不会把高技能的
       合理成功误判成灾难。
    2. 100 是极难失败（大失败的下限之上），96-99 是大失败。
    """
    r = int(rolled)
    s = int(skill_value)
    # —— 成功梯度（先判）——
    if r <= s / 5:
        return RESULT_CRITICAL
    if r <= s / 2:
        return RESULT_EXTREME
    if r <= s:
        return RESULT_HARD
    if r <= s * 0.8 + 20:
        return RESULT_SUCCESS
    # —— 失败梯度 ——
    if r == 100:
        return RESULT_EXTREME_FAIL
    if r >= 96:
        return RESULT_FUMBLE
    if r >= s * 1.2 + 10:
        return RESULT_HARD_FAIL
    return RESULT_FAILURE


class DiceEngine:
    """确定性骰子引擎。

    seed 从 (field_no, session_index, run_seed) 派生；**同 seed 重跑得到
    完全相同的骰子序列**，这是「可复盘」的技术保证 —— 作者回头质疑
    「这场为什么判成功」时，可以原样重放整场。
    """

    def __init__(self, seed: int | str):
        # str seed 用内置 hash 是不稳定的（PYTHONHASHSEED 每次进程不同），
        # 必须自己算一个稳定哈希，否则「同 seed 可复现」在跨进程时失效。
        if isinstance(seed, str):
            h = 0
            for ch in seed:
                h = (h * 131 + ord(ch)) & 0xFFFFFFFF
            seed = h
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self._log: list[DiceCheck] = []

    # ---- 掷骰 ----
    def roll(self, *, beat: int, skill: str, skill_value: int,
             difficulty: str = "", player_idx: int | None = None,
             player_name: str = "", bonus: int = 0) -> DiceCheck:
        """掷一次 1D100 判定。skill_value 取自人物卡，bonus 为情境修正。"""
        target = max(1, min(99, int(skill_value) + int(bonus)))
        rolled = self._rng.randint(1, 100)
        check = DiceCheck(
            beat=int(beat),
            player_idx=player_idx,
            skill=str(skill or "判定"),
            skill_value=int(skill_value),
            difficulty=str(difficulty or ""),
            rolled=rolled,
            bonus=int(bonus),
            target=target,
            result=coc_check(rolled, target),
            player_idx_name=str(player_name or ""),
        )
        self._log.append(check)
        return check

    # ---- 日志 ----
    @property
    def log(self) -> list[DiceCheck]:
        return list(self._log)

    def log_as_dicts(self) -> list[dict]:
        return [c.as_dict() for c in self._log]

    def in_beats(self, beat: int) -> list[DiceCheck]:
        return [c for c in self._log if c.beat == beat]

    def clear_log(self) -> None:
        self._log.clear()


def make_dice_engine(field_no: int, session_index: int = 0, extra: str = "") -> DiceEngine:
    """按场次派生引擎。同一 (field_no, session_index, extra) → 同一序列。"""
    return DiceEngine(f"{field_no}:{session_index}:{extra}")


def skill_value_of(player_card: dict | None, skill: str) -> int:
    """从人物卡取技能值；查不到时给一个保守基线，不让判定崩掉。"""
    card = player_card or {}
    skills = card.get("skills") or {}
    if isinstance(skills, dict):
        val = skills.get(skill)
        if val is not None:
            try:
                return max(1, min(99, int(val)))
            except (TypeError, ValueError):
                pass
    # 常见技能名兜底：COC 里技能值低于 25 通常按 25 计（ untrained 取值）
    return 25


def parse_bonus(text: str | None) -> int:
    """从 KP 的 difficulty 声明里解析情境加成。

    KP 用的是**中文难度词**（极难/困难/常规/容易），所以中文映射是主路径；
    英文与数字写法作为兼容兜底。
    """
    if not text:
        return 0
    t = str(text).strip()
    # ---- 中文难度词（KP 实际输出的形式）----
    cn = {
        "极难": -20, "困难": -10, "较难": -10, "普通": 0, "常规": 0, "中等": 0,
        "容易": 20, "简单": 20, "很容易": 30,
    }
    for k, v in cn.items():
        if k in t:
            return v
    # ---- 英文难度词 ----
    t_en = t.lower()
    en = {
        "extreme_hard": -20, "extreme": -20, "very_hard": -20,
        "hard": -10, "regular": 0, "normal": 0,
        "easy": 20, "simple": 20,
    }
    for k, v in en.items():
        if k in t_en:
            return v
    # ---- 纯数字 / 带符号数字 / 十进制度 ----
    try:
        val = float(t)
        if -1.0 <= val <= 1.0 and val != int(val):
            return int(val * 100)      # 0.2 → 20
        return int(val)
    except ValueError:
        return 0