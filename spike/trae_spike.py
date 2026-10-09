#!/usr/bin/env python3
"""trae-cli exec 通道 Spike 验证脚本（跨平台：Windows / macOS）。

验证点：
  1. trae-cli 可执行文件解析（Win 直调 traex.exe，Mac 走 PATH）
  2. exec 非交互文本生成（--ephemeral --skip-git-repo-check -s read-only）
  3. JSONL 事件流捕获（turn.completed 的 usage 计量）
  4. 耗时测量（启动开销 vs 生成时间）

用法：
  python trae_spike.py                 # 默认提示词
  python trae_spike.py "你的提示词"     # 自定义提示词
  python trae_spike.py --model GLM-5.3 "提示词"
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SPIKE_PROMPT = (
    "你现在是网文作者。只输出正文本身，禁止任何解释、标题、工具调用。"
    "写一段150字左右的网文片段：少年捏着神秘小瓶走进山洞。"
    "文风要求：白话、短句、有画面感、不要AI套话。"
)


def resolve_trae_cli() -> str:
    """解析 trae-cli 可执行路径。

    Windows: bin 目录下是 32 字节的 .cmd shim + 真身 traex.exe，
             直调 exe 可避开 cmd shim 与 stdout 编码/缓冲的坑。
    macOS:   通常是 PATH 中的原生二进制（trae-cli / traex）。
    """
    exe = shutil.which("trae-cli") or shutil.which("traecli") or shutil.which("traex")
    if exe and sys.platform == "win32" and exe.lower().endswith(".cmd"):
        sibling = Path(exe).with_name("traex.exe")
        if sibling.exists():
            return str(sibling)
    if exe:
        return exe
    win_default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/TraeCLI/bin/traex.exe"
    if sys.platform == "win32" and win_default.exists():
        return str(win_default)
    raise SystemExit("未找到 trae-cli，请先安装 TraeCode CLI")


def run_exec(binary: str, prompt: str, model: str | None, json_mode: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="trae_spike_") as td:
        last_msg = str(Path(td) / "last_message.txt")
        cmd = [binary, "exec", "--ephemeral", "--skip-git-repo-check",
               "-s", "read-only", "--color", "never"]
        if json_mode:
            cmd.append("--json")
        if model:
            cmd += ["-m", model]
        cmd += ["-o", last_msg, prompt]

        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        elapsed = time.perf_counter() - t0

        print(f"[exit={proc.returncode}] 耗时 {elapsed:.1f}s")
        if json_mode and proc.stdout.strip():
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  [非JSON行] {line[:120]}")
                    continue
                if ev.get("type") == "turn.completed":
                    u = ev.get("usage", {})
                    print(f"  usage: input={u.get('input_tokens')} "
                          f"cached={u.get('cached_input_tokens')} "
                          f"output={u.get('output_tokens')} "
                          f"reasoning={u.get('reasoning_output_tokens')}")
                else:
                    print(f"  event: type={ev.get('type')}")
        if Path(last_msg).exists():
            text = Path(last_msg).read_text(encoding="utf-8").strip()
            print(f"--- 生成结果（{len(text)} 字符）---")
            print(text)
        else:
            print("!! 未产出 last_message 文件")
            print("stdout 尾部:", proc.stdout[-300:])
            print("stderr 尾部:", proc.stderr[-300:])
        return proc.returncode


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default=SPIKE_PROMPT)
    ap.add_argument("-m", "--model", default=None)
    ap.add_argument("--json", action="store_true", help="打印 JSONL 事件与 usage")
    args = ap.parse_args()

    binary = resolve_trae_cli()
    print(f"trae-cli: {binary}")
    sys.exit(run_exec(binary, args.prompt, args.model, args.json))


if __name__ == "__main__":
    main()
