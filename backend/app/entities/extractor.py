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


# ---- 伏笔兑现问询（P1② 定稿回账：账上欠了什么，本章还了没有） ----

FORESHADOW_RECONCILE_SYSTEM = """你是小说伏笔账房。对照【未回收伏笔清单】逐条核对【本章正文】，判断每一条在本章的状态，只输出一个 JSON 对象：
{"paid": ["本章兑现的伏笔名", ...], "advanced": ["本章推进但未兑现的伏笔名", ...]}
判断规则：
1. paid=本章正文正面回收了该伏笔：谜底揭晓、伏笔物件发挥关键作用、该悬念被解决——作者顺手为之也算兑现；
2. advanced=本章再次提及或部分推进，但尚未到兑现；
3. 拿不准是否兑现时列 advanced，确实正面处理了就大胆列 paid——报错作者可改回，漏报账就烂了；
4. 名字必须与清单完全一致，不要自创、缩写或改写；
5. 两者都不是的条目直接略过；不要输出 JSON 以外的任何文字。"""


@dataclass
class ForeshadowReport:
    paid: list[str] = field(default_factory=list)
    advanced: list[str] = field(default_factory=list)
    attempts: int = 0
    warning: str = ""

    def as_dict(self) -> dict:
        return {"paid": self.paid, "advanced": self.advanced,
                "attempts": self.attempts, "warning": self.warning}


def reconcile_foreshadows(
    provider: Provider,
    store,                      # EntityStore
    prompt_store: PromptStore,
    text: str,
    chapter: int | None = None,
    model: str | None = None,
) -> ForeshadowReport | None:
    """定稿回账：对照账上未回收伏笔问询本章兑现情况（chore 通道）。

    账上没有 planted 伏笔时返回 None（没有债就不问，防注意力摊薄）。
    paid → 状态改 paid 并记本章出场；advanced → 只记出场。
    """
    planted = store.list(type="foreshadow", status="planted")
    if not planted:
        return None
    lines = []
    for e in planted:
        seg = e.name
        if e.summary:
            seg += f"：{e.summary}"
        if e.appearances:
            seg += f"（第{min(e.appearances)}章埋）"
        lines.append(f"- {seg}")
    instruction = prompt_store.effective("foreshadow")
    messages = [
        {"role": "system", "content": FORESHADOW_RECONCILE_SYSTEM},
        {"role": "user", "content": instruction
            + "\n\n【未回收伏笔清单】\n" + "\n".join(lines)
            + "\n\n【本章正文】\n" + text.strip()},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"伏笔问询输出连续 {completion.attempts} 次无法解析为 JSON")
    data = completion.data if isinstance(completion.data, dict) else {}

    def _names(key: str) -> set[str]:
        raw = data.get(key)
        return {str(n).strip() for n in raw if isinstance(n, str) and n.strip()} \
            if isinstance(raw, list) else set()

    paid, advanced = _names("paid"), _names("advanced")
    report = ForeshadowReport(attempts=completion.attempts)
    for e in planted:
        if e.name in paid:
            store.update(e.id, status="paid")
            if chapter is not None:
                store.record_appearance(e.id, chapter)
            report.paid.append(e.name)
        elif e.name in advanced:
            if chapter is not None:
                store.record_appearance(e.id, chapter)
            report.advanced.append(e.name)

    # 反向提醒（乌贼规则）：埋坑密而零回收，账在烂掉——宁可慢，不可假
    paid_total = len(store.list(type="foreshadow", status="paid"))
    planted_total = len(store.list(type="foreshadow", status="planted"))
    ch = chapter or 0
    if paid_total == 0 and planted_total >= 10 and ch >= 10:
        report.warning = (f"伏笔过密：已埋 {planted_total} 个坑、零回收"
                          f"（写到第 {ch} 章）——挖坑容易填坑难，建议尽快安排首个兑现")
    return report
