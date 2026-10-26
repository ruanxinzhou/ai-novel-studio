"""MiniMax OpenAI 兼容通道（调试/高频杂活/拆书并发，现金计费）。

Spike 结论（2026-10-09）：
- 端点 https://api.minimax.chat/v1/chat/completions（api.minimaxi.com 同 Key 亦通）
- 首字延迟 ~0.6s；MiniMax-M2/M1 输出含 <think> 块（流式/非流式都要剥离）
- usage: prompt_tokens / completion_tokens
"""
from __future__ import annotations

import json
import time
from typing import Iterator

import httpx

from .base import Provider, ProviderError, StreamEvent, Usage, strip_think

# 现金单价（元/百万token）。Flash 档位为快速预览版，价格按官方公告校准；其他为历史占位。
PRICE_CNY_PER_MTOK = {
    "MiniMax-M3.1-Flash-Preview": {"in": 0.1, "out": 0.4},
    "MiniMax-M3": {"in": 2.1, "out": 8.4},
    "MiniMax-M2.7": {"in": 2.1, "out": 8.4},
    "MiniMax-M2": {"in": 1.0, "out": 8.0},
    "MiniMax-M1": {"in": 1.0, "out": 8.0},
    "abab6.5s-chat": {"in": 1.0, "out": 1.0},
    "MiniMax-Text-01": {"in": 1.0, "out": 1.0},
}
DEFAULT_HOST = "https://api.minimax.chat/v1"


# reasoning_effort 按层给。
# 正文层（draft/polish）用 high：情节、伏笔、人物逻辑需要完整推敲。
# 结构层用 low：章细纲/章纲/实体抽取的推理链很短，high 会让它反复推敲几分钟，
# 把 max_tokens 全花在 reasoning_content 上，正文一个 token 都没吐出来，
# finish_reason=length → 门禁报「正文为空」→ 下一轮又从头思考，反复几分钟。
_REASONING_EFFORT_BY_LAYER: dict[str, str] = {
    "draft": "high",
    "polish": "high",
    "refine": "medium",
    "fix": "medium",
    "coc_keeper": "medium",
    "coc_investigator": "medium",
    # 跑团决策链合并拍：medium。裁决要依据 NPC 动机/秘密做判断，low 会
    # 明显变浅；但它每拍都要出，不能像 draft 那样烧几分钟推敲。
    "coc_beat": "medium",
    # 支线种子：要真正读懂副本核并想出三条不同的核心反转，需要完整推敲，
    # low 会给出三个雷同方案。high 也可以，但一次调用代价不小——
    # medium 是「能分化」与「不烧太久」之间的平衡点。
    "coc_seed": "medium",
    "coc_outcome": "medium",
    # 通稿层：8000-12000 字长文，medium 已足够（high 在这个长度上极易
    # 把 max_tokens 花在 reasoning 上导致正文被 length 截断）。
    "coc_narrative": "medium",
    "tang_outline": "medium",
    "plot": "low",
    "outline": "low",
    "intro": "low",
    "import_outline": "low",
    "entities": "low",
    "foreshadow": "low",
    "review": "low",
    "brainstorm": "low",
}


class ThinkSplitter:
    """跨 chunk 剥 <think> 块：正文与思考分流（思考透传给前端展示）。"""

    def __init__(self):
        self.in_think = False

    def feed(self, delta: str) -> tuple[str, str]:
        """喂入一个 SSE content chunk，返回 (正文, 思考) 两段。"""
        plain: list[str] = []
        think: list[str] = []
        buf = delta
        while buf:
            if self.in_think:
                end = buf.find("</think>")
                if end >= 0:
                    think.append(buf[:end])
                    buf = buf[end + 8:]
                    self.in_think = False
                else:
                    think.append(buf)
                    buf = ""
            else:
                start = buf.find("<think>")
                if start >= 0:
                    plain.append(buf[:start])
                    buf = buf[start + 7:]
                    self.in_think = True
                else:
                    plain.append(buf)
                    buf = ""
        return "".join(plain), "".join(think)


class MiniMaxProvider(Provider):
    name = "minimax"

    # 推理模型预留：reasoning_content 会占用 max_tokens 配额，需给正文留出空间。
    # reasoning_effort=high 时，长 prompt 下单轮 reasoning 可达 12k~18k tok；
    # 预留 24k 让它把 reasoning 写完再输出正文，否则会被 length 截断导致空正文。
    reasoning_reserve_tokens = 24_576

    def __init__(self, api_key: str, base_url: str | None = None,
                 default_model: str = "MiniMax-M3.1-Flash-Preview"):
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
        extra = dict(extra or {})
        layer_hint = str(extra.pop("_layer", "") or "")
        body: dict = {"model": model, "messages": messages, "stream": True,
                      "stream_options": {"include_usage": True},
                      # 推理强度按层给（见 _REASONING_EFFORT_BY_LAYER）：
                      # 正文层要 high（情节/伏笔/人物逻辑需要完整推敲）；
                      # 结构层（章细纲/章纲/抽取）用 low —— high 会让它反复推敲几分钟，
                      # 把 max_tokens 全花在 reasoning_content 上，正文一字不出，
                      # 门禁只能报「正文为空」，下一轮又从头思考一遍。
                      "reasoning_effort": _REASONING_EFFORT_BY_LAYER.get(
                          layer_hint, "high")}
        # max_tokens 不传时交给 API 模型默认（M3 ≥10K，远优于硬编码 2048 截断）
        if max_tokens:
            body["max_tokens"] = max_tokens
        body.update(extra)
        # 529/429/503 集群过载：指数退避重试，不直接丢给上层 failover
        max_retries = 3
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                with httpx.stream("POST", f"{self.base_url}/chat/completions",
                                  json=body, headers=self._headers(),
                                  timeout=httpx.Timeout(20, read=300)) as r:
                    if r.status_code in (429, 503, 529):
                        detail = r.read().decode("utf-8", errors="replace")[:200]
                        last_err = ProviderError(
                            f"MiniMax HTTP {r.status_code}: {detail}")
                        if attempt < max_retries:
                            wait = 1.5 * (2 ** attempt)
                            yield StreamEvent(kind="notice",
                                              text=f"MiniMax 过载（{r.status_code}），"
                                                   f"{wait:.0f}s 后第{attempt+1}次重试…")
                            time.sleep(wait)
                            continue
                        raise last_err
                    if r.status_code != 200:
                        detail = r.read().decode("utf-8", errors="replace")[:300]
                        raise ProviderError(f"MiniMax HTTP {r.status_code}: {detail}")
                    splitter = ThinkSplitter()
                    usage = Usage()
                    finish_reason: str | None = None
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
                            u = ev.get("usage")
                            usage = Usage(input_tokens=u.get("prompt_tokens", 0),
                                          output_tokens=u.get("completion_tokens", 0))
                        choices = ev.get("choices") or []
                        if not choices:
                            continue
                        choice0 = choices[0] or {}
                        fr = choice0.get("finish_reason")
                        if fr:
                            finish_reason = fr
                        delta_obj = choice0.get("delta", {})
                        reasoning = delta_obj.get("reasoning_content")
                        if reasoning:
                            yield StreamEvent(kind="thinking", text=reasoning)
                        delta = delta_obj.get("content")
                        if not delta:
                            continue
                        plain, think = splitter.feed(delta)
                        if think:
                            yield StreamEvent(kind="thinking", text=think)
                        if plain:
                            yield StreamEvent(kind="delta", text=plain)
                    usage.cost_cny = self._estimate_cost(model, usage)
                    yield StreamEvent(kind="usage", usage=usage,
                                      meta={"finish_reason": finish_reason or ""})
                    yield StreamEvent(kind="done", meta={"finish_reason": finish_reason or ""})
                    return
            except httpx.HTTPError as e:
                last_err = ProviderError(f"MiniMax 网络错误: {type(e).__name__}: {e}")
                last_err.__cause__ = e
                if attempt < max_retries:
                    wait = 1.5 * (2 ** attempt)
                    yield StreamEvent(kind="notice",
                                      text=f"MiniMax 网络错误，{wait:.0f}s 后重试…")
                    time.sleep(wait)
                    continue
                raise last_err
        if last_err:
            raise last_err

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
