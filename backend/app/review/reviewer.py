"""逐项目标审稿（M2，清单源自 Chinese-WebNovel-Skill consistency_review 模块）。

六项一致性逐项给出 稳/不稳 + 问题；任意两项不稳 → blocked（不交稿）。
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
对给定章节，按六项清单逐项审查，每项给出"稳"或"不稳"，不稳时写明问题与证据句。
只输出一个 JSON 对象，格式：
{"items": [{"name": "剧情逻辑", "status": "稳", "problem": ""}, ...共六项],
 "summary": "一句话总评"}
六项 name 依次为：剧情逻辑、人物目标、情绪关系、身体信息、场景转场、章末承接。
不要输出 JSON 以外的任何文字。"""

ITEM_NAMES = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场", "章末承接"]
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
    """模型输出 → 六项 ReviewItem；缺项/坏名补不稳，防静默放水。"""
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
) -> ChapterReview:
    """审一章：outline 为本章大纲/剧情（x），prose 为正文。"""
    instruction = store.effective("review")
    user = (
        f"{instruction}\n\n"
        f"【本章大纲】\n{outline or '（无大纲，按上下文自洽审查）'}\n\n"
        f"【待审正文】\n{prose}"
    )
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
