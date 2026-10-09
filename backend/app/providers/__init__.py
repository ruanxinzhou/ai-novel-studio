"""Provider 注册与路由（含 failover）。"""
from __future__ import annotations

from typing import Iterator

from ..config import AppConfig, load_config
from .base import LLMResponse, Provider, ProviderError, StreamEvent, Usage, strip_think
from .minimax import MiniMaxProvider
from .trae_acp import TraeAcpProvider
from .trae_exec import TraeExecProvider

__all__ = ["Provider", "ProviderError", "StreamEvent", "LLMResponse", "Usage",
           "strip_think", "Router", "get_router"]


class Router:
    """按流水线步骤路由到 primary/fallback 通道。"""

    def __init__(self, cfg: AppConfig, trae_acp: TraeAcpProvider,
                 trae_exec: TraeExecProvider, minimax: MiniMaxProvider):
        self.cfg = cfg
        self.channels: dict[str, Provider] = {
            "trae_acp": trae_acp, "trae_exec": trae_exec, "minimax": minimax,
        }
        self.trae_acp = trae_acp

    def resolve(self, step: str) -> list[Provider]:
        """返回该步骤的通道尝试顺序 [primary, fallback]。"""
        route = self.cfg.routing.get(step, {})
        primary = route.get("primary", "trae_exec")
        fallback = route.get("fallback")
        order = [primary] + ([fallback] if fallback and fallback != primary else [])
        return [self.channels[name] for name in order if name in self.channels]

    def chat_stream(self, step: str, messages: list[dict], model: str | None = None,
                    max_tokens: int | None = None) -> Iterator[StreamEvent]:
        """带 failover 的流式生成：primary 抛 ProviderError 时切 fallback。"""
        attempts = self.resolve(step)
        last_err: ProviderError | None = None
        for i, provider in enumerate(attempts):
            try:
                yield from provider.chat_stream(messages, model, max_tokens)
                return
            except ProviderError as e:
                last_err = e
                if i < len(attempts) - 1:
                    # 通知上层发生了 failover（meta 不进正文）
                    yield StreamEvent(kind="error", text=f"[failover {provider.name}] {e}",
                                      meta={"failover_from": provider.name})
        raise last_err or ProviderError("无可用通道")

    def chat(self, step: str, messages: list[dict], model: str | None = None,
             max_tokens: int | None = None) -> LLMResponse:
        text_parts: list[str] = []
        usage = Usage()
        for ev in self.chat_stream(step, messages, model, max_tokens):
            if ev.kind == "delta":
                text_parts.append(ev.text)
            elif ev.kind == "usage" and ev.usage:
                usage = ev.usage
        return LLMResponse(text="".join(text_parts), usage=usage,
                           model=model or "", raw={"step": step})


_router: Router | None = None


def get_router() -> Router:
    global _router
    if _router is None:
        cfg = load_config()
        _router = Router(
            cfg,
            trae_acp=TraeAcpProvider(binary=cfg.trae.binary,
                                     default_model=cfg.trae.default_model),
            trae_exec=TraeExecProvider(binary=cfg.trae.binary,
                                       default_model=cfg.trae.default_model,
                                       timeout_sec=cfg.trae.exec_timeout_sec),
            minimax=MiniMaxProvider(api_key=cfg.minimax.api_key,
                                    base_url=cfg.minimax.base_url,
                                    default_model=cfg.minimax.default_model),
        )
    return _router
