#!/usr/bin/env python3
"""MiniMax API Spike：端点/模型探测 + 流式验证 + 延迟与用量测量（跨平台）。

自动从 config.local.yaml 读取 api_key（也可用环境变量 MINIMAX_API_KEY 或 --key 覆盖）。
探测顺序：api.minimax.chat -> api.minimaxi.com，模型 MiniMax-M2 -> M1 -> abab6.5s-chat -> MiniMax-Text-01。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
HOSTS = ["https://api.minimax.chat/v1", "https://api.minimaxi.com/v1"]
MODELS = ["MiniMax-M2", "MiniMax-M1", "abab6.5s-chat", "MiniMax-Text-01"]


def load_key() -> str:
    for arg in sys.argv:
        if arg.startswith("--key="):
            return arg.split("=", 1)[1]
    env = os.environ.get("MINIMAX_API_KEY")
    if env:
        return env
    cfg = REPO / "config.local.yaml"
    if cfg.exists():
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        key = (data.get("providers") or {}).get("minimax", {}).get("api_key", "")
        if key and "在此填入" not in key:
            return key
    raise SystemExit("未找到 MiniMax api_key：请填写 config.local.yaml 或设 MINIMAX_API_KEY")


def probe(host: str, model: str, key: str) -> tuple[bool, str, dict]:
    """非流式小请求探测。返回 (成功?, 诊断信息, usage)。"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：成功"}],
        "max_tokens": 16,
        "stream": False,
    }
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{host}/chat/completions", json=body,
                       headers={"Authorization": f"Bearer {key}"},
                       timeout=httpx.Timeout(15, read=60))
    except httpx.HTTPError as e:
        return False, f"网络错误: {type(e).__name__}", {}
    elapsed = time.perf_counter() - t0
    if r.status_code == 200:
        data = r.json()
        usage = data.get("usage", {})
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return True, f"{elapsed:.1f}s 回复={text!r}", usage
    snippet = r.text[:200].replace("\n", " ")
    return False, f"HTTP {r.status_code} ({elapsed:.1f}s) {snippet}", {}


def stream_test(host: str, model: str, key: str, n_out: int = 200) -> None:
    body = {
        "model": model,
        "messages": [{"role": "user", "content":
                      "用网文白话短句写一段120字左右：少年捏着小瓶走进山洞。只输出正文。"}],
        "max_tokens": n_out,
        "stream": True,
    }
    t0 = time.perf_counter()
    first = None
    chunks = 0
    text_parts: list[str] = []
    usage = {}
    with httpx.stream("POST", f"{host}/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {key}"},
                      timeout=httpx.Timeout(15, read=120)) as r:
        r.raise_for_status()
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
                usage = ev["usage"]
            delta = (ev.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                chunks += 1
                if first is None:
                    first = time.perf_counter() - t0
                text_parts.append(delta)
    total = time.perf_counter() - t0
    text = "".join(text_parts)
    print(f"\n[流式验证] 首字 {first:.1f}s / 总 {total:.1f}s / {chunks} 个增量块 / {len(text)} 字符")
    if usage:
        print(f"  usage: {json.dumps(usage, ensure_ascii=False)}")
    print("--- 生成内容 ---")
    print(text)


def main() -> None:
    key = load_key()
    print(f"Key: {key[:10]}...{key[-4:]} (长度 {len(key)})")
    winner = None
    for host in HOSTS:
        for model in MODELS:
            ok, msg, usage = probe(host, model, key)
            tag = "PASS" if ok else "fail"
            print(f"[{tag}] {host}  {model:20s}  {msg}"
                  + (f"  usage={json.dumps(usage, ensure_ascii=False)}" if ok else ""))
            if ok and winner is None:
                winner = (host, model)
        if winner:
            break
    if not winner:
        raise SystemExit("\n所有候选端点/模型均失败，请核对 Key 与平台文档")
    host, model = winner
    print(f"\n===> 胜出组合: {host}  model={model}")
    stream_test(host, model, key)


if __name__ == "__main__":
    main()
