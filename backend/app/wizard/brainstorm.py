"""脑洞方向卡生成（M5 圆桌结论：AI 出多样本附理由，先问后给）。

走 chore 便宜通道（模型路由三问：杂活不烧贵模型）；结构化输出复用
complete_structured（词法修复 + 重试）。方向卡只是候选，采纳必须经
作者三出口（选一个 / 改一个 / 自己写）落进 wizard.direction。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

BRAINSTORM_SYSTEM = """你是网文选题策划，给作者提供开局方向备选。只输出一个 JSON 数组，
每个元素格式：{"direction": "方向名（10字内）", "reason": "一句话理由：好在哪里、爽点在哪",
"hook": "金手指或核心冲突（30字内）", "premise": "三句话梗概：起因、能力或转机、首个冲突"}。
方向之间必须在题材基调或核心爽点上拉开差距。不要输出数组以外的任何文字。"""

CARD_FIELDS = ("direction", "reason", "hook", "premise")


@dataclass
class DirectionCards:
    cards: list[dict]
    attempts: int


def _normalize(data: object) -> list[dict] | None:
    if not isinstance(data, list):
        return None
    cards = []
    for item in data:
        if not isinstance(item, dict):
            continue
        card = {k: str(item.get(k, "") or "").strip() for k in CARD_FIELDS}
        if card["direction"] or card["premise"]:
            cards.append(card)
    return cards or None


def generate_direction_cards(
    provider: Provider,
    prompt_store: PromptStore,
    seed: str,
    idea_context: str = "",
) -> DirectionCards:
    """根据作者种子生成 5 张方向卡；种子为空也能给差异化方向。"""
    instruction = prompt_store.effective("brainstorm")
    seed_block = seed.strip() or "（作者未填种子，按差异化题材给5个方向）"
    context_block = f"\n【三格背景】\n情境：{idea_context}" if idea_context.strip() else ""
    messages = [
        {"role": "system", "content": BRAINSTORM_SYSTEM},
        {"role": "user", "content": f"{instruction}\n\n【种子】\n{seed_block}{context_block}"},
    ]

    def generate() -> str:
        return provider.chat(messages).text

    completion = complete_structured(generate)
    cards = _normalize(completion.data) if completion.data is not None else None
    if not cards:
        raise ValueError(f"方向卡输出连续 {completion.attempts} 次无法解析")
    return DirectionCards(cards=cards, attempts=completion.attempts)
