"""trae-cli exec 子进程封装：独立拆书工具的本地 LLM 引擎（零边际成本）。

复用后端 app/providers/trae_exec.py 的实战结论：
- 命令: traex.exe exec --ephemeral --skip-git-repo-check -s read-only --json -m <模型> <prompt>
- Windows 直调 traex.exe 绕过 .cmd shim（编码/缓冲坑）
- 超时必须杀整棵进程树（孙进程持管道会让 communicate 永久挂死，真实事故：挂 7h）
- JSONL 事件流取 item.completed 的 agent_message 正文
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_binary(configured: str | None = None) -> str:
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
        default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/TraeCLI/bin/traex.exe"
        if default.exists():
            return str(default)
    raise RuntimeError("未找到 trae-cli（trae-cli/traecli/traex 均不在 PATH）。请安装 TraeCode CLI 并登录。")


def _kill_tree(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass


def _extract_text(stdout: str, out_file: Path) -> str:
    """从 JSONL 事件流提取正文；事件流缺失时回退 -o 输出文件。"""
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
            if item.get("itemType") == "agent_message" or item.get("type") == "agent_message":
                text = item.get("text") or text
    if not text and out_file.exists():
        text = out_file.read_text(encoding="utf-8", errors="replace").strip()
    return text


def trae_exec(prompt: str, model: str = "GLM-5.3", timeout_sec: int = 300,
              binary: str | None = None) -> str:
    """单次调用，返回正文文本。失败抛 RuntimeError。"""
    exe = resolve_binary(binary)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="chaishu_") as td:
        out_file = Path(td) / "out.txt"
        cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check",
               "-s", "read-only", "--color", "never", "--json",
               "-m", model, "-o", str(out_file), prompt]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace")
        except OSError as e:
            raise RuntimeError(f"trae exec 启动失败：{e}") from e
        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            raise RuntimeError(f"trae exec 超时({timeout_sec}s)，已终止进程树") from None
        if proc.returncode != 0:
            tail = (stderr or stdout or "")[-300:]
            raise RuntimeError(f"trae exec 退出码 {proc.returncode}: {tail}")
        text = _extract_text(stdout or "", out_file)
        if not text:
            raise RuntimeError("trae exec 未产出正文")
        return text
