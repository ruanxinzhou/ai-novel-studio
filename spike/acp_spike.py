#!/usr/bin/env python3
"""ACP 通道 Spike：验证 trae-cli `acp serve` 的流式 delta 与会话保温（跨平台）。

验证点：
  1. initialize / session/new / session/prompt 三步握手（ACP = JSON-RPC over stdio，按行分帧）
  2. 逐字流式：session/update 通知中 agent_message_chunk 的到达时序
  3. 会话保温：同一 session 第二次 prompt 的耗时/事件对比
产物：spike/acp_transcript.jsonl（完整原始报文，供协议分析）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRANSCRIPT = Path(__file__).resolve().parent / "acp_transcript.jsonl"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trae_spike import resolve_trae_cli  # noqa: E402


class AcpClient:
    """最小 ACP JSON-RPC 客户端：读线程 + 消息队列。"""

    def __init__(self, binary: str, cwd: Path, transcript_path: Path):
        self.proc = subprocess.Popen(
            [binary, "acp", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(cwd),
        )
        self.t_start = time.perf_counter()
        self.msgs: list[dict] = []          # 全部消息（含通知），带相对时间
        self.lock = threading.Lock()
        self.transcript = transcript_path.open("w", encoding="utf-8")
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _log(self, direction: str, raw: str) -> None:
        t = time.perf_counter() - self.t_start
        with self.lock:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = {"_unparsed": raw[:400]}
            self.msgs.append({"t": round(t, 3), "dir": direction, "msg": obj})
            self.transcript.write(json.dumps(self.msgs[-1], ensure_ascii=False) + "\n")
            self.transcript.flush()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._log("recv", line)

    def send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        raw = json.dumps(obj, ensure_ascii=False)
        self._log("send", raw)
        self.proc.stdin.write((raw + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def wait_response(self, msg_id: int, timeout: float) -> dict | None:
        """在 msgs 中等待 id 匹配的 response（result 或 error 都算）。"""
        deadline = time.time() + timeout
        seen = 0
        while time.time() < deadline:
            with self.lock:
                snap = self.msgs[seen:]
                seen = len(self.msgs)
            for item in snap:
                m = item["msg"]
                if m.get("id") == msg_id and ("result" in m or "error" in m):
                    return m
            time.sleep(0.05)
        return None

    def updates_for(self, since_idx: int = 0) -> list[dict]:
        with self.lock:
            return [x for x in self.msgs[since_idx:]
                    if x["msg"].get("method") == "session/update"]

    def close(self) -> None:
        try:
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
        self.transcript.close()


def text_chunks(updates: list[dict]) -> list[dict]:
    """抽取 agent_message_chunk 类文本增量。"""
    out = []
    for x in updates:
        u = x["msg"].get("params", {}).get("update", {})
        su = u.get("sessionUpdate", "")
        if su == "agent_message_chunk" or (not su and u.get("content")):
            c = u.get("content", {})
            if c.get("text"):
                out.append(x)
    return out


def run_prompt(cli: AcpClient, session_id: str, prompt: str, next_id: int,
               timeout: float = 240.0) -> tuple[int, dict | None, dict]:
    """执行一次 prompt，返回 (新消息游标, prompt响应, 统计)。"""
    mark = len(cli.msgs)
    cli.send({"jsonrpc": "2.0", "id": next_id, "method": "session/prompt",
              "params": {"sessionId": session_id,
                         "prompt": [{"type": "text", "text": prompt}]}})
    t0 = time.perf_counter()
    resp = cli.wait_response(next_id, timeout)
    elapsed = time.perf_counter() - t0
    ups = cli.updates_for(mark)
    chunks = text_chunks(ups)
    text = "".join(x["msg"]["params"]["update"]["content"]["text"]
                   for x in chunks if x["msg"]["params"].get("update", {}).get("content"))
    kinds = {}
    for x in ups:
        k = x["msg"].get("params", {}).get("update", {}).get("sessionUpdate", "?")
        kinds[k] = kinds.get(k, 0) + 1
    stats = {"elapsed": round(elapsed, 1), "update_events": len(ups),
             "text_chunks": len(chunks), "chars": len(text),
             "update_kinds": kinds, "first_chunk_s": chunks[0]["t"] if chunks else None}
    return mark, resp, stats


def main() -> None:
    binary = resolve_trae_cli()
    print(f"trae-cli: {binary}")
    cli = AcpClient(binary, REPO, TRANSCRIPT)
    try:
        time.sleep(0.8)  # 给进程一点启动时间
        # 1. initialize
        cli.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": 1, "clientCapabilities": {}}})
        init = cli.wait_response(1, 30)
        print(f"\n[initialize] {json.dumps(init, ensure_ascii=False)[:500] if init else 'TIMEOUT'}")
        if init and init.get("error"):
            # 某些实现要求 protocolVersion 为字符串，重试一次
            cli.send({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                      "params": {"protocolVersion": "1", "clientCapabilities": {}}})
            init = cli.wait_response(2, 30)
            print(f"[initialize retry] {json.dumps(init, ensure_ascii=False)[:500] if init else 'TIMEOUT'}")
            if not init or init.get("error"):
                raise SystemExit("initialize 失败，详见 transcript")
        proto_ver = (init or {}).get("result", {}).get("protocolVersion", 1)

        # 2. session/new
        cli.send({"jsonrpc": "2.0", "id": 100, "method": "session/new",
                  "params": {"cwd": str(REPO), "mcpServers": []}})
        new_s = cli.wait_response(100, 60)
        print(f"\n[session/new] {json.dumps(new_s, ensure_ascii=False)[:400] if new_s else 'TIMEOUT'}")
        session_id = ((new_s or {}).get("result") or {}).get("sessionId")
        if not session_id:
            raise SystemExit("session/new 失败，详见 transcript")
        print(f"sessionId = {session_id}   protocolVersion = {proto_ver}")

        # 3. 第一次 prompt（冷启动）
        p1 = ("你现在是网文作者，只输出正文，禁止工具调用。写一段120字左右的网文片段："
              "少年把小瓶收进怀里，快步下山。白话短句，有画面感。")
        _, resp1, s1 = run_prompt(cli, session_id, p1, 200)
        print(f"\n[prompt#1 冷启动] stats={json.dumps(s1, ensure_ascii=False)}")
        print(f"  stopReason={((resp1 or {}).get('result') or {}).get('stopReason')}")

        # 4. 第二次 prompt（同会话保温）
        p2 = ("继续写下一段90字左右：山脚的集市，他打听碎片消息。只输出正文。")
        _, resp2, s2 = run_prompt(cli, session_id, p2, 201)
        print(f"[prompt#2 保温会话] stats={json.dumps(s2, ensure_ascii=False)}")

        # 5. 结论
        print("\n===> 结论")
        print(f"  流式: {'是（' + str(s1['text_chunks']) + ' 个增量块）' if s1['text_chunks'] > 1 else '否（整块交付）'}")
        print(f"  会话保温收益: {s1['elapsed']}s -> {s2['elapsed']}s")
        print(f"  完整报文已存: {TRANSCRIPT}")
    finally:
        cli.close()


if __name__ == "__main__":
    main()
