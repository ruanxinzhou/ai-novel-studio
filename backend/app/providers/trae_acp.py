"""trae-cli ACP 通道：常驻子进程，主力生成通道（公司额度）。

Spike 结论（2026-10-09）：
- `traecli acp serve` = JSON-RPC over stdio（按行分帧）
- initialize(protocolVersion=1) → session/new(cwd) → session/prompt(prompt=[{type:text}])
- 流式通知 session/update：agent_thought_chunk（思考流，逐块）/ agent_message_chunk
  （正文，实测当前版本整块交付，接口仍按增量流处理以向前兼容）
- usage_update: {used, size} 会话上下文占用；会话保温后单次延迟 12.5s → 7.8s
- session/new 返回 configOptions（含 model 选择器，可会话级切模型）
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

from .base import Provider, ProviderError, StreamEvent, Usage
from .trae_exec import resolve_trae_binary

INIT_TIMEOUT = 30.0
NEW_SESSION_TIMEOUT = 60.0
PROMPT_TIMEOUT = 300.0


class AcpProcess:
    """一个 trae-cli acp serve 子进程 + JSON-RPC 客户端（线程安全读取）。"""

    def __init__(self, binary: str, cwd: Path):
        self.proc = subprocess.Popen(
            [binary, "acp", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(cwd))
        self._msgs: list[dict] = []
        self._lock = threading.Lock()
        self._next_id = 1
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self._msgs.append(obj)

    def call(self, method: str, params: dict, timeout: float) -> dict:
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            start = len(self._msgs)
        raw = json.dumps({"jsonrpc": "2.0", "id": msg_id,
                          "method": method, "params": params}, ensure_ascii=False)
        assert self.proc.stdin is not None
        self.proc.stdin.write((raw + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for m in self._msgs[start:]:
                    if m.get("id") == msg_id and ("result" in m or "error" in m):
                        if "error" in m:
                            raise ProviderError(f"ACP {method} 错误: "
                                                f"{json.dumps(m['error'], ensure_ascii=False)[:300]}")
                        return m["result"]
            time.sleep(0.05)
        raise ProviderError(f"ACP {method} 超时({timeout}s)")

    def updates_since(self, start: int) -> list[dict]:
        with self._lock:
            return [m for m in self._msgs[start:] if m.get("method") == "session/update"]

    def mark(self) -> int:
        with self._lock:
            return len(self._msgs)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def shutdown(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class TraeAcpProvider(Provider):
    """常驻 ACP 进程，按 novel 会话维持上下文保温。

    用法：with provider.session(cwd) as s: yield from s.chat_stream(...)
    或者直接用 chat_stream（惰性建会话，cwd 取当前工作目录）。
    """

    name = "trae_acp"

    def __init__(self, binary: str | None = None,
                 default_model: str = "glm-5.3-flash", cwd: Path | None = None):
        self.binary = resolve_trae_binary(binary)
        self.default_model = default_model
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self._proc: AcpProcess | None = None
        self._session_id: str | None = None
        self._last_ctx_used = 0

    def _ensure_session(self) -> tuple[AcpProcess, str]:
        if self._proc and self._proc.alive() and self._session_id:
            return self._proc, self._session_id
        self.shutdown()
        self._proc = AcpProcess(self.binary, self.cwd)
        init = self._proc.call("initialize",
                               {"protocolVersion": 1, "clientCapabilities": {}},
                               INIT_TIMEOUT)
        if isinstance(init.get("protocolVersion"), str):
            pass  # 字符串版本号，已接受
        result = self._proc.call("session/new",
                                 {"cwd": str(self.cwd), "mcpServers": []},
                                 NEW_SESSION_TIMEOUT)
        self._session_id = result.get("sessionId")
        if not self._session_id:
            raise ProviderError("ACP session/new 未返回 sessionId")
        self._try_set_model()
        return self._proc, self._session_id

    def _try_set_model(self) -> None:
        """尝试会话级切模型（configOptions.model）。失败不致命——用默认模型。"""
        assert self._proc and self._session_id
        try:
            self._proc.call("session/set_config_option",
                            {"sessionId": self._session_id,
                             "configId": "model", "value": self.default_model},
                            10.0)
        except ProviderError:
            pass  # 方法名或参数在后续版本可能变化；默认模型仍可用

    def chat_stream(self, messages: list[dict], model: str | None = None,
                    max_tokens: int | None = None,
                    extra: dict | None = None) -> Iterator[StreamEvent]:
        model = model or self.default_model
        if model != self.default_model:
            self.default_model = model
            if self._proc and self._proc.alive() and self._session_id:
                self._try_set_model()
        proc, session_id = self._ensure_session()
        prompt_text = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        mark = proc.mark()
        proc.call("session/prompt",
                  {"sessionId": session_id,
                   "prompt": [{"type": "text", "text": prompt_text}]},
                  PROMPT_TIMEOUT)
        text_parts: list[str] = []
        for u in proc.updates_since(mark):
            update = u.get("params", {}).get("update", {})
            kind = update.get("sessionUpdate", "")
            if kind == "usage_update":
                self._last_ctx_used = update.get("used", 0)
            elif kind == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("text"):
                    text_parts.append(content["text"])
                    yield StreamEvent(kind="delta", text=content["text"])
        usage = Usage(input_tokens=self._last_ctx_used)
        yield StreamEvent(kind="usage", usage=usage)
        yield StreamEvent(kind="done")

    def shutdown(self) -> None:
        if self._proc:
            self._proc.shutdown()
            self._proc = None
            self._session_id = None

    def health(self) -> dict:
        base = {"binary": self.binary}
        try:
            resolved = resolve_trae_binary(self.binary if Path(self.binary).exists() else None)
            return {"ok": True, "session_alive": bool(self._proc and self._proc.alive()),
                    "binary": resolved, "ctx_used": self._last_ctx_used}
        except ProviderError as e:
            return {"ok": False, **base, "error": str(e)}
