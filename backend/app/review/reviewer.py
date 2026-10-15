"""逐项目标审稿（M2，清单源自 Chinese-WebNovel-Skill consistency_review 模块）。

八项一致性逐项给出 稳/不稳 + 问题；任意两项不稳 → blocked（不交稿）。
第七项「跨章联结」对照上一章结尾审查欲望接力；无前章材料（第一章/无上下文）判稳。
第八项「信息揭露节奏」审查 NPC 对白是否出现 info dump。
结构化输出走 complete_structured + 词法修复 JSON；审稿指令存 PromptStore
"review" 层，可在工坊编辑。

审稿是独立任务类型：不使用写作 SYSTEM_CONTRACT，使用 REVIEW_SYSTEM。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

REVIEW_SYSTEM = """你是中文网文交稿前的终审编辑。你只做一致性审查，不改写正文。
对给定章节，按八项清单逐项审查，每项给出"稳"或"不稳"，不稳时写明问题与证据句。
只输出一个 JSON 对象，格式：
{"items": [{"name": "剧情逻辑", "status": "稳", "problem": ""}, ...共八项],
 "summary": "一句话总评"}
八项 name 依次为：剧情逻辑、人物目标、情绪关系、身体信息、场景转场、章末承接、跨章联结、信息揭露节奏。
跨章联结的审法：对照【上一章结尾】——本章开篇是否接住其变化、主角欲望是否比之前更具体或更急、章末是否留有下一章去向。
若未提供【上一章结尾】（如第一章），该项判"稳"并在 problem 里注明"无上一章可比"。
信息揭露节奏：NPC 对白是否出现大段世界观/真相倾倒（info dump），单段对白超过 150 字且连续解释设定视为不稳。
不要输出 JSON 以外的任何文字。"""

ITEM_NAMES = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场", "章末承接", "跨章联结", "信息揭露节奏"]
BLOCK_THRESHOLD = 2  # 任意两项不稳 → 不交稿


@dataclass(frozen=True)
class ReviewItem:
    name: str
    status: str          # 稳 | 不稳
    problem: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "problem": self.problem}


@dataclass(frozen=True)
class ChapterReview:
    items: list[ReviewItem]
    verdict: str                  # pass | blocked
    summary: str = ""
    attempts: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def unstable_items(self) -> list[ReviewItem]:
        return [i for i in self.items if i.status == "不稳"]

    def as_dict(self) -> dict:
        return {
            "items": [i.as_dict() for i in self.items],
            "verdict": self.verdict,
            "summary": self.summary,
            "unstable_count": len(self.unstable_items),
            "attempts": self.attempts,
        }


def _normalize(data: object) -> list[ReviewItem] | None:
    """模型输出 → 七项 ReviewItem；缺项/坏名补不稳，防静默放水。"""
    if not isinstance(data, dict):
        return None
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return None
    by_name: dict[str, dict] = {}
    for it in raw_items:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            by_name[it["name"].strip()] = it
    items: list[ReviewItem] = []
    for name in ITEM_NAMES:
        it = by_name.get(name)
        if it is None:
            items.append(ReviewItem(name=name, status="不稳", problem="模型未输出该项，视为未通过"))
            continue
        status = "不稳" if it.get("status") != "稳" else "稳"
        problem = it.get("problem") if isinstance(it.get("problem"), str) else ""
        if status == "不稳" and not problem:
            problem = "未说明问题"
        items.append(ReviewItem(name=name, status=status, problem=problem))
    return items


def review_chapter(
    provider: Provider,
    store: PromptStore,
    outline: str,
    prose: str,
    model: str | None = None,
    prev_tail: str = "",
) -> ChapterReview:
    """审一章：outline 为本章大纲/剧情（x），prose 为正文。
    prev_tail 为上一章结尾（滚动锚点），给定时作为第七项「跨章联结」的对照材料。"""
    instruction = store.effective("review")
    parts = [instruction]
    if prev_tail.strip():
        parts.append(f"【上一章结尾】\n{prev_tail.strip()}")
    parts.append(f"【本章大纲】\n{outline or '（无大纲，按上下文自洽审查）'}")
    parts.append(f"【待审正文】\n{prose}")
    user = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": user},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"审稿输出连续 {completion.attempts} 次无法解析为 JSON")
    items = _normalize(completion.data)
    if items is None:
        raise ValueError("审稿 JSON 缺少 items 数组")
    summary = ""
    if isinstance(completion.data, dict) and isinstance(completion.data.get("summary"), str):
        summary = completion.data["summary"]
    unstable = sum(1 for i in items if i.status == "不稳")
    return ChapterReview(
        items=items,
        verdict="blocked" if unstable >= BLOCK_THRESHOLD else "pass",
        summary=summary,
        attempts=completion.attempts,
        raw=completion.data if isinstance(completion.data, dict) else {},
    )
