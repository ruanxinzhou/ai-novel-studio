"""MiniMax OpenAI 兼容通道（调试/高频杂活/拆书并发，现金计费）。

Spike 结论（2026-10-09）：
- 端点 https://api.minimax.chat/v1/chat/completions（api.minimaxi.com 同 Key 亦通）
- 首字延迟 ~0.6s；MiniMax-M2/M1 输出含 <think> 块（流式/非流式都要剥离）
- usage: prompt_tokens / completion_tokens
"""
from __future__ import annotations

import json
from typing import Iterator

import httpx

from .base import Provider, ProviderError, StreamEvent, Usage, strip_think

# 现金单价（元/百万token）。占位值，接入账单后校准。
PRICE_CNY_PER_MTOK = {
    "MiniMax-M2": {"in": 1.0, "out": 8.0},
    "MiniMax-M1": {"in": 1.0, "out": 8.0},
    "abab6.5s-chat": {"in": 1.0, "out": 1.0},
    "MiniMax-Text-01": {"in": 1.0, "out": 1.0},
}
DEFAULT_HOST = "https://api.minimax.chat/v1"


class MiniMaxProvider(Provider):
    name = "minimax"

    def __init__(self, api_key: str, base_url: str | None = None,
                 default_model: str = "MiniMax-M2"):
        if not api_key or "在此填入" in api_key:
            raise ProviderError("MiniMax api_key 未配置（config.local.yaml）")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_HOST).rstrip("/")
        self.default_model = default_model

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _estimate_cost(self, model: str, u: Usage) -> float:
        p = PRICE_CNY_PER_MTOK.get(model, {"in": 1.0, "out": 8.0})
        return (u.input_tokens * p["in"] + u.output_tokens * p["out"]) / 1e6

    def chat_stream(self, messages: list[dict], model: str | None = None,
                    max_tokens: int | None = None,
                    extra: dict | None = None) -> Iterator[StreamEvent]:
        model = model or self.default_model
        body: dict = {"model": model, "messages": messages,
                      "max_tokens": max_tokens or 2048, "stream": True,
                      "stream_options": {"include_usage": True}}
        body.update(extra or {})
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions",
                              json=body, headers=self._headers(),
                              timeout=httpx.Timeout(20, read=300)) as r:
                if r.status_code != 200:
                    detail = r.read().decode("utf-8", errors="replace")[:300]
                    raise ProviderError(f"MiniMax HTTP {r.status_code}: {detail}")
                in_think = False
                usage = Usage()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("usage"):
                        u = ev["usage"]
                        usage = Usage(input_tokens=u.get("prompt_tokens", 0),
                                      output_tokens=u.get("completion_tokens", 0))
                    delta = (ev.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if not delta:
                        continue
                    # <think> 块不透传（推理过程不进正文）；跨 chunk 状态机
                    if "<think>" in delta:
                        before, _, delta = delta.partition("<think>")
                        in_think = True
                        if before:
                            yield StreamEvent(kind="delta", text=before)
                    if in_think:
                        if "</think>" not in delta:
                            continue
                        delta = delta.split("</think>", 1)[1]
                        in_think = False
                    if delta:
                        yield StreamEvent(kind="delta", text=delta)
                usage.cost_cny = self._estimate_cost(model, usage)
                yield StreamEvent(kind="usage", usage=usage)
                yield StreamEvent(kind="done")
        except httpx.HTTPError as e:
            raise ProviderError(f"MiniMax 网络错误: {type(e).__name__}: {e}") from e

    def health(self) -> dict:
        try:
            r = httpx.post(f"{self.base_url}/chat/completions",
                           json={"model": self.default_model,
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "max_tokens": 1},
                           headers=self._headers(),
                           timeout=httpx.Timeout(10, read=30))
            if r.status_code == 200:
                return {"ok": True}
            return {"ok": False, "http": r.status_code, "detail": r.text[:150]}
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e)[:150]}
