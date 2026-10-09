"""trae-cli exec 通道：单次子进程，批处理/兜底（公司额度）。

Spike 结论（2026-10-09）：
- 命令: traecli exec --ephemeral --skip-git-repo-check -s read-only -m <模型> -o <文件> [--json]
- 事件流: thread.started → turn.started → item.completed(reasoning/message) → turn.completed(usage)
- 正文无逐字流（整块交付）；单次固定 ~22k input tokens；24-64s/次
- Windows 直调 traex.exe 绕过 .cmd shim（编码/缓冲坑）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

from .base import Provider, ProviderError, StreamEvent, Usage


def resolve_trae_binary(configured: str | None = None) -> str:
    """解析 trae-cli 可执行文件。Windows 优先直调 traex.exe。"""
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("trae-cli") or shutil.which("traecli") or shutil.which("traex")
    if found and sys.platform == "win32" and found.lower().endswith(".cmd"):
        sibling = Path(found).with_name("traex.exe")
        if sibling.exists():
            return str(sibling)
    if found:
        return found
    if sys.platform == "win32":
        default = Path(__import__("os").environ.get("LOCALAPPDATA", "")) \
            / "Programs/TraeCLI/bin/traex.exe"
        if default.exists():
            return str(default)
    raise ProviderError("未找到 trae-cli，请安装 TraeCode CLI 并登录")


def _kill_tree(pid: int) -> None:
    """终止整棵进程树。taskkill /T 按 PID 父子关系连杀，孙进程也不留。"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            import signal, os
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass  # 尽力而为：失败也不阻塞超时路径，上方 communicate(15) 兜底


class TraeExecProvider(Provider):
    name = "trae_exec"

    def __init__(self, binary: str | None = None,
                 default_model: str = "GLM-5.3",
                 timeout_sec: int = 300):
        self.binary = resolve_trae_binary(binary)
        self.default_model = default_model
        self.timeout_sec = timeout_sec

    def chat_stream(self, messages: list[dict], model: str | None = None,
                    max_tokens: int | None = None,
                    extra: dict | None = None) -> Iterator[StreamEvent]:
        model = model or self.default_model
        prompt = "\n\n".join(
            f"[{m['role']}]\n{m['content']}" for m in messages)

        with tempfile.TemporaryDirectory(prefix="trae_exec_",
                                         ignore_cleanup_errors=True) as td:
            out_file = str(Path(td) / "last_message.txt")
            cmd = [self.binary, "exec", "--ephemeral", "--skip-git-repo-check",
                   "-s", "read-only", "--color", "never", "--json",
                   "-m", model, "-o", out_file, prompt]
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, text=True,
                    encoding="utf-8", errors="replace")
            except OSError as e:
                raise ProviderError(f"trae exec 启动失败：{e}") from e

            usage = Usage()
            try:
                stdout, stderr = proc.communicate(timeout=self.timeout_sec)
            except subprocess.TimeoutExpired:
                # 只 kill 主进程不够：孙进程持有输出管道会让 communicate
                # 永久阻塞（真实事故：traex 挂 7h，请求无限等待），
                # 必须杀整棵进程树再收管道
                _kill_tree(proc.pid)
                try:
                    stdout, stderr = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    stdout, stderr = "", ""  # 放弃管道，宁可泄漏句柄不卡请求
                raise ProviderError(
                    f"trae exec 超时({self.timeout_sec}s)，已终止进程树") from None

            if proc.returncode != 0:
                tail = (stderr or stdout or "")[-300:]
                raise ProviderError(f"trae exec 退出码 {proc.returncode}: {tail}")

            # JSONL 事件：item.completed 取正文，turn.completed 取用量
            text = ""
            for line in (stdout or "").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "item.completed":
                    item = ev.get("item", {})
                    if item.get("itemType") == "agent_message" or \
                       item.get("type") == "agent_message":
                        text = item.get("text") or text
                elif ev.get("type") == "turn.completed":
                    u = ev.get("usage", {})
                    usage = Usage(input_tokens=u.get("input_tokens", 0),
                                  output_tokens=u.get("output_tokens", 0),
                                  cached_tokens=u.get("cached_input_tokens", 0))

            if not text and Path(out_file).exists():
                text = Path(out_file).read_text(encoding="utf-8").strip()
            if not text:
                raise ProviderError("trae exec 未产出正文（详见 --json 输出）")

            yield StreamEvent(kind="delta", text=text)
            yield StreamEvent(kind="usage", usage=usage)
            yield StreamEvent(kind="done")

    def health(self) -> dict:
        try:
            resolved = resolve_trae_binary(self.binary if Path(self.binary).exists()
                                           else None)
            return {"ok": True, "binary": resolved}
        except ProviderError as e:
            return {"ok": False, "error": str(e)}
