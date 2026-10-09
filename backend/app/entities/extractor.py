"""实体抽取（M3b）：LLM 从正文/剧情抽取五类实体并回灌实体库。

抽取走 chore 通道（个人成本优先）；结构化输出复用 complete_structured
（词法 JSON 修复 + 重试），修复失败抛错交上层提示重试，不静默丢弃。
抽取指令存 PromptStore "entities" 层，可在工坊编辑（合同区不可覆盖）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

ENTITY_EXTRACT_SYSTEM = """你是小说设定管理员，负责维护实体库。从给定文本中抽取实体，只输出一个 JSON 对象：
{"entities": [{"type": "character|location|item|plotline|foreshadow", "name": "实体名", "summary": "一句话描述", "details": "补充设定，可为空字符串"}]}
抽取规则：
1. character=有名字或固定称谓的角色；location=具体地点/场所/组织；item=有名字的关键道具/法宝/信物；plotline=正在推进的情节线或核心冲突；foreshadow=文中埋下或回收的伏笔；
2. 只抽文本中实际出现或明确指向的，不要臆造；无名泛指（如"路人""随从"）不抽；
3. summary 一句话说清它是什么；foreshadow 必须在 details 写明「已埋」还是「已回收」及所指内容；
4. 不要输出 JSON 以外的任何文字。"""


@dataclass
class ExtractionResult:
    created: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    attempts: int = 0

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)


def extract_entities(
    provider: Provider,
    store,                      # EntityStore
    prompt_store: PromptStore,
    text: str,
    chapter: int | None = None,
    model: str | None = None,
) -> ExtractionResult:
    """从一段文本抽取实体并合并入库；chapter 提供时记入出现章节。"""
    text = text.strip()
    if not text:
        raise ValueError("待抽取文本不能为空")
    instruction = prompt_store.effective("entities")
    messages = [
        {"role": "system", "content": ENTITY_EXTRACT_SYSTEM},
        {"role": "user", "content": f"{instruction}\n\n【待抽取文本】\n{text}"},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"抽取输出连续 {completion.attempts} 次无法解析为 JSON")
    raw = completion.data.get("entities") if isinstance(completion.data, dict) else None
    found = raw if isinstance(raw, list) else []
    merged = store.merge_extracted(found, chapter)
    return ExtractionResult(
        created=merged["created"], updated=merged["updated"],
        dropped=merged["dropped"], attempts=completion.attempts,
    )
