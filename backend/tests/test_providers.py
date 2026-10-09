"""Provider 路由与 trae_exec 健壮性测试。

重点回归：trae exec 超时后孙进程持有输出管道导致 communicate
永久阻塞（事故：/api/runs/intro 挂 7 小时无响应）。
"""
from __future__ import annotations

import sys
import time

import pytest

from app.config import AppConfig
from app.providers import Router
from app.providers.base import Provider, ProviderError, StreamEvent
from app.providers.trae_exec import TraeExecProvider


class _StubProvider(Provider):
    def __init__(self, name: str):
        self.name = name

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        raise ProviderError(f"{self.name} 不可用")
        yield  # pragma: no cover

    def health(self) -> dict:
        return {"ok": True}


class _OkProvider(_StubProvider):
    """正常交付正文的假通道。"""

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        yield StreamEvent(kind="delta", text=f"来自{self.name}")
        yield StreamEvent(kind="usage", usage=None)
        yield StreamEvent(kind="done")


def _router(routing: dict | None = None) -> Router:
    cfg = AppConfig(routing=routing or {})
    return Router(cfg, trae_acp=_StubProvider("trae_acp"),
                  trae_exec=_StubProvider("trae_exec"),
                  minimax=_StubProvider("minimax"))


def test_resolve_registered_step():
    order = [p.name for p in _router({"outline": {"primary": "trae_exec",
                                                  "fallback": "minimax"}}).resolve("outline")]
    assert order == ["trae_exec", "minimax"]


def test_resolve_unregistered_step_has_default_fallback():
    """未注册步骤（如历史事故中的 intro）必须带 minimax 兜底。"""
    order = [p.name for p in _router().resolve("intro")]
    assert order == ["trae_exec", "minimax"]


def test_resolve_fallback_same_as_primary_dropped():
    order = [p.name for p in _router({"x": {"primary": "minimax"}}).resolve("x")]
    assert order == ["minimax"]


def test_resolve_debug_uses_minimax_first():
    order = [p.name for p in _router({"debug": {"primary": "minimax",
                                                "fallback": "trae_exec"}}).resolve("debug")]
    assert order == ["minimax", "trae_exec"]


def test_chat_failover_to_fallback():
    router = _router()
    router.channels["minimax"] = _OkProvider("minimax")
    events = list(router.chat_stream("intro", [{"role": "user", "content": "hi"}]))
    assert any(e.kind == "delta" and e.text == "来自minimax" for e in events)


def test_chat_all_channels_down_raises():
    with pytest.raises(ProviderError):
        list(_router().chat_stream("intro", [{"role": "user", "content": "hi"}]))


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T 为 Windows 专有")
def test_trae_exec_timeout_kills_process_tree(tmp_path):
    """超时必须杀整棵树：孙进程持管道时旧实现会永久阻塞。"""
    bat = tmp_path / "fake_trae.cmd"
    # 主进程静默挂 30s（模拟 hung traex），孙进程持有 stdout 管道 30s
    bat.write_text(
        '@echo off\r\n'
        'start "" /b cmd /c "ping -n 30 127.0.0.1"\r\n'
        'ping -n 30 127.0.0.1 > nul\r\n',
        encoding="ascii",
    )
    provider = TraeExecProvider(binary=str(bat), default_model="m", timeout_sec=3)

    start = time.monotonic()
    with pytest.raises(ProviderError, match="超时"):
        list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    elapsed = time.monotonic() - start
    assert elapsed < 15, f"杀树失败，请求仍被管道阻塞 {elapsed:.1f}s"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 专测")
def test_trae_exec_normal_exit_parses_jsonl(tmp_path):
    bat = tmp_path / "fake_trae_ok.cmd"
    bat.write_text(
        '@echo off\r\n'
        'echo {"type":"item.completed","item":{"itemType":"agent_message",'
        '"text":"hello-from-fake"}}\r\n'
        'exit /b 0\r\n',
        encoding="ascii",
    )
    provider = TraeExecProvider(binary=str(bat), default_model="m", timeout_sec=30)
    events = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert [e.kind for e in events] == ["delta", "usage", "done"]
    assert events[0].text == "hello-from-fake"
