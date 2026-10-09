"""批量断点收据 + 幂等重试（M1-T9，机制源自 Vela run-journal）。

- 每块生成完成立即落一条收据；temp + os.replace 原子写，崩溃不留半截文件。
- 重跑按 (块序, x_hash) 命中复用：输入未变则不重新调用 LLM，天然幂等。
- 失败块记 failed 收据后上抛；重试只重生成无 completed 收据的块。
- run_key 摘要 layer+输入，输入一变旧收据整体作废（宁可重跑不可错拼）。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COMPLETED = "completed"
FAILED = "failed"


@dataclass
class ChunkReceipt:
    index: int
    x_hash: str
    y: str
    status: str                       # completed | failed
    completion_status: str = ""       # complete | bounded | no_progress
    attempts: int = 0
    error: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index, "x_hash": self.x_hash, "y": self.y,
            "status": self.status, "completion_status": self.completion_status,
            "attempts": self.attempts, "error": self.error, "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChunkReceipt":
        return cls(
            index=data["index"], x_hash=data["x_hash"], y=data.get("y", ""),
            status=data.get("status", COMPLETED),
            completion_status=data.get("completion_status", ""),
            attempts=data.get("attempts", 0), error=data.get("error", ""),
            timestamp=data.get("timestamp", ""),
        )


def x_fingerprint(x: str) -> str:
    return hashlib.sha256(x.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compute_run_key(layer: str, x_chunks: list[str],
                    materials: list[tuple[str, str, str]] | None = None,
                    user_directive: str | None = None) -> str:
    """确定性 run 摘要：同输入同 key（重试命中同一 journal），异输入必异 key。"""
    payload = json.dumps(
        {"layer": layer, "x": x_chunks,
         "materials": sorted(materials or []), "directive": user_directive or ""},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class RunJournal:
    """一次层运行的断点日志，持久化为 {layer, run_key, receipts[]}。"""

    def __init__(self, path: Path | str, layer: str, run_key: str):
        self.path = Path(path)
        self.layer = layer
        self.run_key = run_key
        self.receipts: list[ChunkReceipt] = []
        self.discarded_old = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # 半截/损坏文件按空日志处理，不阻塞重跑
        if data.get("run_key") != self.run_key or data.get("layer") != self.layer:
            self.discarded_old = True  # 输入变了：旧收据作废，绝不跨输入复用
            return
        self.receipts = [ChunkReceipt.from_dict(r) for r in data.get("receipts", [])]

    def match(self, index: int, x_hash: str) -> ChunkReceipt | None:
        """命中且仅命中 completed 收据；failed 块永远重生成。"""
        for r in self.receipts:
            if r.index == index and r.x_hash == x_hash and r.status == COMPLETED:
                return r
        return None

    def append(self, receipt: ChunkReceipt) -> None:
        receipt.timestamp = receipt.timestamp or _now()
        self.receipts = [r for r in self.receipts if r.index != receipt.index]
        self.receipts.append(receipt)
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "layer": self.layer, "run_key": self.run_key,
            "receipts": [r.as_dict() for r in self.receipts],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def summary(self) -> dict:
        done = [r for r in self.receipts if r.status == COMPLETED]
        failed = [r for r in self.receipts if r.status == FAILED]
        return {
            "layer": self.layer, "run_key": self.run_key,
            "path": str(self.path), "discarded_old": self.discarded_old,
            "total": len(self.receipts), "completed": len(done), "failed": len(failed),
            "receipts": [r.as_dict() for r in self.receipts],
        }
