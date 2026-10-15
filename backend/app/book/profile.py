"""书级硬骨架 Profile 主模块：BookProfile + BookStore。

BookStore 管理 book/ 目录下 7 张卡的读写/渲染。每张卡对应一个 JSON 文件，
通过 get_card/update_card 读写；render_all 把所有卡渲染为 Markdown 段，
作为 REQUIRED 材料注入流水线。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# ---- 默认世界观公理（《第七日的亡者》宪法）----

DEFAULT_PREMISE = {
    "title": "第七日的亡者",
    "genre": "克苏鲁神话恐怖 × 无限流单元剧长篇",
    "tone": "戏内高压恐怖、戏外轻松回血；悬疑感贯穿，爽点克制",
    "pov": "第三人称限知（唐迟视角为主，允许间章/关键章插 NPC 视角）",
    "tense": "过去时",
    "core_premise": (
        "【三条不可打破的铁律】\n"
        "1. 七日锚：每场戏的主事件线严格按第一天到第七天推进，第七日=死期永不打破。"
        "叙述时间可以非线性（回忆/闪回/时空交错/NPC视角/时间叙述诡计），但非线性必须服务于七日死期这根锚，"
        "日号绝对不能错、不能跳、不能提前。\n"
        "2. 讣告三功能合一：每场戏由一封无邮票无邮戳的公函讣告开启——"
        "（入场端）作为邀请函送达，主角作为讣告上的死者回到其死亡七天前；"
        "（机制端）写明死者姓名+死期+死因（可能涂黑■■■■）；"
        "（出场端）散场时讣告自焚成灰，背面残页烧不掉，留在主角家中揭示后日谈。\n"
        "3. 神不出场：克苏鲁底层逻辑——有一位以人间死局为乐的存在（非人、不可见、不对话），"
        "每场戏是祂的节目，被选中的人是戏子。祂的态度只通过事件兑现（有趣=慢半拍、无聊=杀意快三分），"
        "不显形、不开口、从不与主角对话。讣告是祂唯一的话语。"
    ),
    "protagonist": (
        "【主角：唐迟】二十多岁社畜，五年前从平行世界"
        "「地球」穿越到蓝星，无系统无金手指，唯一本事是不引人注目。"
        "惜命、怂、肯记账、不狠不酷。成长来自每场借来的手艺，"
        "在人间尺度逐卷叠加成传奇，在神话尺度始终什么都不是。"
        "他在每场戏附身刚死之人，活过第七日即可回家，"
        "死者执念会「上身」成金手指但使用有代价（人间副作用）。"
    ),
    "reader_pact": (
        "【读者契约】\n"
        "- 每场单元剧独立可读，但全书伏笔串联；\n"
        "- 克苏鲁恐怖基调——未知、不可名状、SAN滑落；不解释邪神，不正面写神；\n"
        "- 爽点有但克制——主角手艺累积带来小处爽，但每次爽都伴随自我侵蚀；\n"
        "- 戏内高压戏外回血，节奏松紧交替；\n"
        "- 终局不是战胜神，而是以「让神觉得无聊」的方式逃离循环，主角带诡异痕迹半疯地活着。"
    ),
    "world_gradient": (
        "【按卷梯度解锁世界观】\n"
        "卷一前段：只露「原本故事被改变」的余味，不提其他选中者；\n"
        "卷二起：出现其他被选中者的痕迹，暗示主角不是唯一；\n"
        "中后期：逐渐揭露选中者分几派——逃离派/皈依派/滞留派等；\n"
        "底牌揭示节奏由作品决定，不在第一卷明说。"
    ),
    "taboos": [
        "神绝不显形不对话，只通过事件流露态度",
        "不写直白的大段邪神设定解说，用暗示和碎片",
        "主角不能龙傲天；他的成长是借的不是自己的",
        "第七日=死期这条永不打破（即使终局反转也不打这条脸）",
        "现实间章不能写成纯日常流水，必须8:2带刺",
    ],
    "reference_corpus": [],  # 默认绑定的语料 ID 列表
}


@dataclass
class BookProfile:
    data: dict = field(default_factory=lambda: dict(DEFAULT_PREMISE))

    def as_dict(self) -> dict:
        return asdict(self)["data"] if "data" not in self.data else self.data

    @classmethod
    def from_dict(cls, d: dict | None) -> "BookProfile":
        base = dict(DEFAULT_PREMISE)
        if d:
            base.update({k: v for k, v in d.items() if k in DEFAULT_PREMISE})
        return cls(data=base)

    def render(self) -> str:
        d = self.data
        return (
            f"# 书籍档案：{d['title']}\n\n"
            f"**题材**：{d['genre']}\n\n"
            f"**基调**：{d['tone']}\n\n"
            f"**视角/时态**：{d['pov']}；{d['tense']}\n\n"
            f"## 核心设定（铁律·不可违反）\n\n{d['core_premise']}\n\n"
            f"## 主角\n\n{d['protagonist']}\n\n"
            f"## 读者契约\n\n{d['reader_pact']}\n\n"
            f"## 世界观解锁梯度\n\n{d['world_gradient']}\n\n"
            f"## 禁区\n\n" + "\n".join(f"- {t}" for t in d["taboos"]) + "\n"
        )


class BookStore:
    """管理 book/ 目录下所有硬卡。统一原子读写。"""

    CARDS = (
        "premise",           # BookProfile
        "tangchi",           # TangchiScale
        "endgame",           # EndgameAnchors
        "cost_debts",        # list[dict]
        "remaining_pages",   # list[dict]
        "finale_notes",      # list[dict]
        "seven_day_pulse",   # SevenDayPulse
        "craft",             # 网文写作工艺手册（双层：书级+卷级）
    )

    def __init__(self, book_dir: Path | str):
        self.dir = Path(book_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}

    # ---- 通用读写 ----

    def _path(self, card: str) -> Path:
        if card not in self.CARDS:
            raise ValueError(f"未知书卡：{card}（可选 {self.CARDS}）")
        return self.dir / f"{card}.json"

    def _read(self, card: str) -> Any:
        if card in self._cache:
            return self._cache[card]
        p = self._path(card)
        if not p.exists():
            data = self._default(card)
            self._write(card, data)
            self._cache[card] = data
            return data
        data = json.loads(p.read_text(encoding="utf-8"))
        self._cache[card] = data
        return data

    def _write(self, card: str, data: Any) -> None:
        p = self._path(card)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        self._cache[card] = data

    def update(self, card: str, data: Any) -> Any:
        self._write(card, data)
        return data

    # ---- 默认值（首次初始化落盘）----

    def _default(self, card: str) -> Any:
        if card == "premise":
            return dict(DEFAULT_PREMISE)
        if card == "tangchi":
            from app.book.tangchi import DEFAULT_TANGCHI
            return dict(DEFAULT_TANGCHI)
        if card == "endgame":
            from app.book.endgame import DEFAULT_ENDGAME
            return dict(DEFAULT_ENDGAME)
        if card == "seven_day_pulse":
            from app.book.pulse import DEFAULT_PULSE
            return {"day_beats": DEFAULT_PULSE}
        if card == "craft":
            from app.book.craft import CRAFT_SKILLS, DEFAULT_GOLDEN3
            return {
                "skills": CRAFT_SKILLS,
                "golden3": DEFAULT_GOLDEN3,
                "book_golden3_notes": [],
                "volume_golden3_notes": [],
            }
        # 其他卡默认空列表
        return []

    # ---- 类型化访问器 ----

    def premise(self) -> BookProfile:
        return BookProfile.from_dict(self._read("premise"))

    def tangchi_raw(self) -> dict:
        return dict(self._read("tangchi"))

    def endgame_raw(self) -> dict:
        return dict(self._read("endgame"))

    def cost_debts(self) -> list[dict]:
        return list(self._read("cost_debts"))

    def remaining_pages(self) -> list[dict]:
        return list(self._read("remaining_pages"))

    def finale_notes(self) -> list[dict]:
        return list(self._read("finale_notes"))

    def pulse_raw(self) -> dict:
        return dict(self._read("seven_day_pulse"))

    def craft_raw(self) -> dict:
        return dict(self._read("craft"))

    # ---- 全书材料渲染（注入流水线 REQUIRED）----

    def render_all(self) -> str:
        """把所有书卡渲染为 Markdown，作为 REQUIRED 材料。"""
        from app.book.tangchi import render_tangchi
        from app.book.endgame import render_endgame
        from app.book.pulse import render_pulse
        from app.book.craft import render_craft

        parts: list[str] = [self.premise().render()]

        # 七日情绪基线
        pulse = self.pulse_raw()
        parts.append(render_pulse(pulse.get("day_beats", [])))

        # 唐迟自我刻度
        parts.append(render_tangchi(self.tangchi_raw()))

        # 未兑现代价账
        debts = self.cost_debts()
        if debts:
            pending = [d for d in debts if not d.get("paid")]
            parts.append(
                "## 未兑现代价账（执念副作用延迟兑现）\n\n"
                + ("以下代价尚未兑现，写作时必须在本场或近场安排副作用：\n"
                   if pending else "当前无未兑现代价。\n")
                + "\n".join(
                    f"- 第{d['field_no']}场·{d['obsession_name']}：{d['side_effect']}"
                    f"（应在 {d.get('due_hint','近期')} 兑现）"
                    for d in pending)
                + "\n"
            )

        # 残页集（最近 5 张提示）
        pages = self.remaining_pages()
        if pages:
            recent = pages[-5:]
            parts.append(
                "## 近期讣告残页（暗线串联）\n\n"
                + "\n".join(f"- 第{p['field_no']}场残页：{p['ash_text']}" for p in recent)
                + "\n"
            )

        # 杀青记录（最近 5 条）
        notes = self.finale_notes()
        if notes:
            recent = notes[-5:]
            parts.append(
                "## 近场杀青记录\n\n"
                + "\n".join(f"- 第{n['field_no']}场：{n['one_liner']}" for n in recent)
                + "\n"
            )

        # 终局锚点待埋提醒
        parts.append(render_endgame(self.endgame_raw()))

        # 网文写作工艺手册（双层）
        parts.append(render_craft(self.craft_raw()))

        return "\n".join(parts)
