"""Provider 统一抽象：所有通道（trae ACP / trae exec / MiniMax）实现同一接口。

业务代码只依赖这里的数据结构与接口，通道差异（子进程、SSE、think 剥离、
failover）全部关在各自实现内。
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_cny: float = 0.0     # 仅 MiniMax 等现金通道填写

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens, "cost_cny": round(self.cost_cny, 6)}


@dataclass
class StreamEvent:
    kind: str              # "delta" | "usage" | "done" | "error"
    text: str = ""
    usage: Usage | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    raw: dict = field(default_factory=dict)


class ProviderError(Exception):
    """通道级故障（可触发 failover）。"""


class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def chat_stream(self, messages: list[dict], model: str | None = None,
                    max_tokens: int | None = None,
                    extra: dict | None = None) -> Iterator[StreamEvent]:
        """流式生成。messages 为 OpenAI 风格 [{role, content}]。"""

    def chat(self, messages: list[dict], model: str | None = None,
             max_tokens: int | None = None,
             extra: dict | None = None) -> LLMResponse:
        """非流式生成（默认实现：聚合流式）。"""
        text_parts: list[str] = []
        usage = Usage()
        for ev in self.chat_stream(messages, model, max_tokens, extra):
            if ev.kind == "delta":
                text_parts.append(ev.text)
            elif ev.kind == "usage" and ev.usage:
                usage = ev.usage
            elif ev.kind == "error":
                raise ProviderError(ev.text)
        return LLMResponse(text="".join(text_parts), usage=usage,
                           provider=self.name, model=model or "")

    def chat_json(self, messages: list[dict], model: str | None = None,
                  max_tokens: int | None = None) -> dict:
        """结构化输出：生成后解析 JSON（剥 <think>），失败抛 ProviderError。"""
        resp = self.chat(messages, model, max_tokens)
        return parse_json_loose(resp.text)


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.DOTALL)


def strip_think(text: str) -> str:
    """剥离推理模型（MiniMax-M2/M1 等）输出的 <think> 块（含未闭合块）。"""
    cleaned = _THINK_RE.sub("", text)
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def parse_json_loose(text: str) -> dict:
    """宽松 JSON 解析：剥 think / ```json 围栏 / 前后缀。"""
    cleaned = strip_think(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    start = min([i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0], default=-1)
    if start > 0:
        cleaned = cleaned[start:]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ProviderError(f"JSON 解析失败: {e}: {cleaned[:200]}") from e
