"""导入拆解管线（M4）：已有正文 → 逐章实体抽取 + 反向大纲归纳。

幂等：每章两步各落一条收据（import_journal.json，原子写入），重跑已完成的
步骤直接复用，只补失败/缺失——导入几百章的小说可分多次拆完。
大纲归纳走 PromptStore "import_outline" 层（工坊可编辑）；实体抽取复用
M3b extract_entities（合并策略同库：同名只补空与章节）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.entities import EntityStore, extract_entities
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

STEP_ENTITIES = "entities"
STEP_OUTLINE = "outline"

OUTLINE_SYSTEM = "你是小说编辑，负责反向整理已有作品的大纲。只输出大纲文本本身，不带解释。"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class StepReceipt:
    chapter: int
    step: str
    status: str                 # completed | failed
    result: str = ""            # outline 步存生成的大纲文本
    error: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict:
        return {
            "chapter": self.chapter, "step": self.step, "status": self.status,
            "result": self.result, "error": self.error, "timestamp": self.timestamp,
        }


class ImportJournal:
    """按 (章号, 步骤) 键控的收据，持久化到项目目录。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.receipts: dict[str, StepReceipt] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for k, r in data.get("receipts", {}).items():
                    self.receipts[k] = StepReceipt(**r)
            except (json.JSONDecodeError, TypeError, OSError):
                self.receipts = {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"receipts": {k: r.as_dict() for k, r in self.receipts.items()}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    @staticmethod
    def key(chapter: int, step: str) -> str:
        return f"ch{chapter}-{step}"

    def get(self, chapter: int, step: str) -> StepReceipt | None:
        return self.receipts.get(self.key(chapter, step))

    def mark(self, chapter: int, step: str, status: str,
             result: str = "", error: str = "") -> StepReceipt:
        receipt = StepReceipt(chapter=chapter, step=step, status=status,
                              result=result, error=error, timestamp=_now())
        self.receipts[self.key(chapter, step)] = receipt
        self._flush()
        return receipt

    def completed_outlines(self) -> list[tuple[int, str]]:
        """已完成的章大纲，按章号升序。"""
        out = [(r.chapter, r.result) for r in self.receipts.values()
               if r.step == STEP_OUTLINE and r.status == "completed"]
        return sorted(out)

    def reset_chapter(self, chapter: int) -> None:
        for step in (STEP_ENTITIES, STEP_OUTLINE):
            self.receipts.pop(self.key(chapter, step), None)
        self._flush()


def analyze_chapter(
    provider: Provider,
    entity_store: EntityStore,
    prompt_store: PromptStore,
    journal: ImportJournal,
    chapter: int,
    text: str,
    model: str | None = None,
) -> dict:
    """拆一章：先实体后大纲；已完成步骤幂等复用。返回两步结果。"""
    text = text.strip()
    if not text:
        raise ValueError("本章正文为空，无法拆解")

    ent_receipt = journal.get(chapter, STEP_ENTITIES)
    if ent_receipt and ent_receipt.status == "completed":
        entities_result = {"reused": True}
    else:
        try:
            merged = extract_entities(provider, entity_store, prompt_store,
                                      text, chapter=chapter, model=model)
            journal.mark(chapter, STEP_ENTITIES, "completed")
            entities_result = {
                "reused": False,
                "created": [c["name"] for c in merged.created],
                "updated": [u["name"] for u in merged.updated],
                "dropped": len(merged.dropped),
            }
        except ValueError as e:
            journal.mark(chapter, STEP_ENTITIES, "failed", error=str(e))
            raise

    outline_receipt = journal.get(chapter, STEP_OUTLINE)
    if outline_receipt and outline_receipt.status == "completed":
        return {"chapter": chapter, "entities": entities_result,
                "outline": outline_receipt.result, "outline_reused": True}

    instruction = prompt_store.effective("import_outline")
    messages = [
        {"role": "system", "content": OUTLINE_SYSTEM},
        {"role": "user", "content": f"{instruction}\n\n【原文】\n{text}"},
    ]
    try:
        outline = provider.chat(messages, model).text.strip()
    except Exception as e:
        journal.mark(chapter, STEP_OUTLINE, "failed", error=str(e))
        raise
    if not outline:
        journal.mark(chapter, STEP_OUTLINE, "failed", error="大纲为空")
        raise ValueError("大纲归纳返回空文本")
    journal.mark(chapter, STEP_OUTLINE, "completed", result=outline)
    return {"chapter": chapter, "entities": entities_result,
            "outline": outline, "outline_reused": False}
