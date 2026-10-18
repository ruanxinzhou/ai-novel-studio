# -*- coding: utf-8 -*-
"""流式生成测试：思考透传 / runner 事件回调 / SSE 端点 / 路由全 MiniMax。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.generation import COMPLETE
from app.pipeline.engine import SPECS, PipelineRunner
from app.pipeline.journal import ChunkReceipt, RunJournal, x_fingerprint
from app.projects import ProjectStore
from app.providers.base import LLMResponse, StreamEvent, Usage
from app.providers.minimax import ThinkSplitter
from app.review.markers import MarkerStore


class TestThinkSplitter:
    def test_single_chunk(self):
        plain, think = ThinkSplitter().feed("<think>推理A</think>正文B")
        assert think == "推理A" and plain == "正文B"

    def test_cross_chunk(self):
        s = ThinkSplitter()
        p1, t1 = s.feed("开头<think>思")
        p2, t2 = s.feed("考中")
        p3, t3 = s.feed("</think>结")
        assert (p1, p2, p3) == ("开头", "", "结")
        assert t1 + t2 + t3 == "思考中"

    def test_no_think(self):
        assert ThinkSplitter().feed("纯正文") == ("纯正文", "")

    def test_multiple_blocks(self):
        s = ThinkSplitter()
        plain, think = s.feed("<think>a</think>x<think>b</think>y")
        assert plain == "xy" and think == "ab"

    def test_think_at_end(self):
        s = ThinkSplitter()
        plain, think = s.feed("正文<think>未闭合")
        assert plain == "正文" and think == "未闭合"


class StreamStubProvider:
    """流式假通道：先思考后正文，逐字 delta。"""
    default_model = "m"

    def __init__(self, text: str = "韩立睁眼。"):
        self.text = text
        self.calls = 0

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        self.calls += 1
        yield StreamEvent(kind="thinking", text="先想一步")
        for ch in self.text:
            yield StreamEvent(kind="delta", text=ch)
        yield StreamEvent(kind="usage", usage=Usage(input_tokens=1, output_tokens=len(self.text)))


class TestRunnerStreamCallback:
    def test_on_event_receives_thinking_delta_block_done(self):
        runner = PipelineRunner(StreamStubProvider("正文来了"))
        events: list[tuple[str, dict]] = []
        r = runner.run_layer(SPECS["intro"], [("点子", "")],
                             on_event=lambda k, p: events.append((k, p)))
        kinds = [k for k, _ in events]
        assert kinds[0] == "thinking"
        assert "block_done" in kinds
        assert kinds[-1] == "block_done"
        deltas = "".join(p["text"] for k, p in events if k == "delta")
        assert deltas == "正文来了"
        assert r.y == "正文来了"
        assert all(p["index"] == 0 for k, p in events if k in ("delta", "thinking"))

    def test_chat_only_provider_still_works(self):
        class ChatOnly:
            default_model = "m"

            def chat(self, messages, model=None, max_tokens=None, extra=None):
                return LLMResponse(text="只支持chat", usage=Usage(1, 5))

        events: list[tuple[str, dict]] = []
        r = PipelineRunner(ChatOnly()).run_layer(
            SPECS["intro"], [("点子", "")], on_event=lambda k, p: events.append((k, p)))
        assert r.y == "只支持chat"
        # 整块文本作为一次 delta 下发
        assert any(k == "delta" and p["text"] == "只支持chat" for k, p in events)

    def test_resumed_block_emits_event_not_llm(self, tmp_path):
        journal = RunJournal(tmp_path / "r.json", "intro", "k")
        journal.append(ChunkReceipt(index=0, x_hash=x_fingerprint("点子"),
                                    y="收据里的旧文", status="completed",
                                    completion_status=COMPLETE))
        provider = StreamStubProvider("不该被调用")
        events: list[tuple[str, dict]] = []
        r = PipelineRunner(provider).run_layer(
            SPECS["intro"], [("点子", "")], journal=journal,
            on_event=lambda k, p: events.append((k, p)))
        assert r.y == "收据里的旧文"
        assert r.resumed_indices == [0]
        assert provider.calls == 0
        assert any(k == "resumed" and p["index"] == 0 for k, p in events)
        assert not any(k == "block_done" for k, _ in events)


class FakeStreamRouter:
    def __init__(self, text: str):
        self.text = text

    def chat(self, step, messages, model=None, max_tokens=None):
        return LLMResponse(text=self.text, usage=Usage(input_tokens=1, output_tokens=10))

    def chat_stream(self, step, messages, model=None, max_tokens=None):
        yield StreamEvent(kind="thinking", text="内心推演")
        yield StreamEvent(kind="delta", text=self.text)
        yield StreamEvent(kind="usage", usage=Usage(input_tokens=1, output_tokens=10))
        yield StreamEvent(kind="done")


@pytest.fixture
def sse_client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._journal_path",
                        lambda rid, project=None: tmp_path / "runs" / f"{rid}.json")
    monkeypatch.setattr("app.api.main._journal_dir", lambda pid: tmp_path / "runs" / pid)
    monkeypatch.setattr("app.api.main._markers_for",
                        lambda project: MarkerStore(tmp_path / "mk" / f"{project or 'default'}.json"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    from app.wizard import DeskStore, WizardStore
    monkeypatch.setattr("app.api.main._wizard_for",
                        lambda pid: WizardStore(tmp_path / "wiz" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    return TestClient(__import__("app.api.main", fromlist=["app"]).app)


class TestRunsStreamApi:
    def test_sse_emits_thinking_delta_result_done(self, sse_client, monkeypatch):
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeStreamRouter("流式正文"))
        with sse_client.stream("POST", "/api/runs/intro/stream",
                               json={"x_pairs": [["点子", ""]]}) as r:
            assert r.status_code == 200
            body = "".join(chunk for chunk in r.iter_text())
        assert "event: thinking" in body and "内心推演" in body
        assert "event: delta" in body and "流式正文" in body
        assert "event: result" in body
        assert "event: done" in body
        # result 事件携带完整运行结果
        for frame in body.split("\n\n"):
            if frame.startswith("event: result"):
                data = json.loads(frame.split("data: ", 1)[1])
                assert data["y"] == "流式正文"
                assert data["journal"]["completed"] == 1
                break
        else:
            pytest.fail("未找到 result 事件")

    def test_sse_fail_event_on_llm_error(self, sse_client, monkeypatch):
        class BoomRouter:
            def chat(self, *a, **k):
                raise RuntimeError("通道全挂")

            def chat_stream(self, *a, **k):
                raise RuntimeError("通道全挂")
                yield

        monkeypatch.setattr("app.api.main.get_router", lambda: BoomRouter())
        with sse_client.stream("POST", "/api/runs/intro/stream",
                               json={"x_pairs": [["点子", ""]]}) as r:
            body = "".join(chunk for chunk in r.iter_text())
        assert "event: fail" in body and "通道全挂" in body

    def test_unknown_layer_404(self, sse_client):
        r = sse_client.post("/api/runs/nope/stream", json={"x_pairs": []})
        assert r.status_code == 404


def test_routing_primary_channels():
    from app.config import load_config

    routing = load_config().routing
    # 创作主路全 MiniMax；chore（拆解抽取类）主路本地 trae_exec 不花钱、MiniMax 兜底
    for step in ["intro", "outline", "plot", "draft", "polish", "review", "debug", "refine"]:
        assert routing[step]["primary"] == "minimax", f"{step} 主路应为 minimax"
        assert routing[step]["fallback"] == "trae_exec"
    assert routing["chore"]["primary"] == "trae_exec", "chore 主路应为 trae_exec"
    assert routing["chore"]["fallback"] == "minimax"


class TestRefineStreamApi:
    def test_sse_emits_thinking_delta_result(self, sse_client, monkeypatch):
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeStreamRouter("调整后的全文"))
        with sse_client.stream("POST", "/api/refine/stream",
                               json={"text": "原始文本", "directive": "开头改成悬念"}) as r:
            assert r.status_code == 200
            body = "".join(c for c in r.iter_text())
        assert "event: thinking" in body and "内心推演" in body
        assert "event: delta" in body and "调整后的全文" in body
        assert "event: result" in body
        for frame in body.split("\n\n"):
            if frame.startswith("event: result"):
                assert json.loads(frame.split("data: ", 1)[1]) == {"y": "调整后的全文"}
                break
        else:
            pytest.fail("未找到 result 事件")

    def test_prompt_override_applies(self, sse_client, monkeypatch):
        # 工坊覆盖的 refine 提示词应进入 user 消息（FakeStreamRouter 会记录 messages）
        captured = {}

        class CaptureRouter(FakeStreamRouter):
            def chat_stream(self, step, messages, model=None, max_tokens=None):
                captured["messages"] = messages
                yield from super().chat_stream(step, messages, model, max_tokens)

        monkeypatch.setattr("app.api.main.get_router", lambda: CaptureRouter("x"))
        with sse_client.stream("POST", "/api/refine/stream",
                               json={"text": "原文", "directive": "要求"}) as r:
            "".join(c for c in r.iter_text())
        user_msg = captured["messages"][1]["content"]
        assert "【原文】" in user_msg and "【要求】" in user_msg


class TestDeskRunStreamApi:
    def _setup_book(self, client) -> str:
        pid = client.post("/api/projects", json={"title": "章台流式书"}).json()["id"]
        client.post(f"/api/projects/{pid}/layers/intro", json={"y": "简介", "x_pairs": []})
        client.post(f"/api/projects/{pid}/layers/outline",
                    json={"y": "第1章\n入门试炼。", "x_pairs": []})
        client.put(f"/api/projects/{pid}/wizard",
                   json={"direction": "凡人流", "direction_source": "picked"})
        return pid

    def test_sse_emits_thinking_delta_result(self, sse_client, monkeypatch):
        pid = self._setup_book(sse_client)
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeStreamRouter("剧情流式正文"))
        with sse_client.stream("POST", f"/api/projects/{pid}/desk/1/run/stream",
                               json={"stage": "plot"}) as r:
            assert r.status_code == 200
            body = "".join(chunk for chunk in r.iter_text())
        assert "event: thinking" in body and "内心推演" in body
        assert "event: delta" in body and "剧情流式正文" in body
        assert "event: result" in body and "event: done" in body
        for frame in body.split("\n\n"):
            if frame.startswith("event: result"):
                data = json.loads(frame.split("data: ", 1)[1])
                assert data["y"] == "剧情流式正文"
                assert data["chapter"] == 1 and data["stage"] == "plot"
                break
        else:
            pytest.fail("未找到 result 事件")
        # 生成已入章台记录
        chapter = sse_client.get(f"/api/projects/{pid}/desk/1").json()["chapter"]
        assert chapter["plot"]["text"] == "剧情流式正文"

    def test_sse_missing_outline_fails_cleanly(self, sse_client, monkeypatch):
        pid = sse_client.post("/api/projects", json={"title": "无纲书"}).json()["id"]
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeStreamRouter("x"))
        with sse_client.stream("POST", f"/api/projects/{pid}/desk/1/run/stream",
                               json={"stage": "plot"}) as r:
            body = "".join(chunk for chunk in r.iter_text())
        assert "event: fail" in body and "方向卡" in body
