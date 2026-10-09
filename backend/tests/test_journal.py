"""T9 批量断点收据 + 幂等重试测试。"""
from __future__ import annotations

import pytest

from app.pipeline import (
    SPECS,
    PipelineRunner,
    RunJournal,
    compute_run_key,
    x_fingerprint,
)
from app.pipeline.journal import ChunkReceipt
from app.providers.base import LLMResponse, Provider, ProviderError, Usage


class CountingProvider(Provider):
    """记录调用次数与输入，y 带块序号便于断言。"""

    name = "counting"
    default_model = "mock"

    def __init__(self, fail_on_indices: set[int] | None = None):
        self.calls: list[list[dict]] = []
        self.fail_on_indices = fail_on_indices or set()
        self.call_seq = 0

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        self.call_seq += 1
        self.calls.append(messages)
        # 块序号藏在 x 里：测试用「第N块」标记
        x_text = messages[-1]["content"]
        index = next((i for i in range(20) if f"第{i + 1}块" in x_text), 0)
        if index in self.fail_on_indices:
            raise ProviderError(f"模拟块 {index + 1} 生成失败")
        yield type("Ev", (), {"kind": "delta", "text": f"Y{index + 1}。", "usage": None, "meta": {}})()
        yield type("Ev", (), {"kind": "usage",
                              "usage": Usage(input_tokens=10, output_tokens=5),
                              "text": "", "meta": {}})()


@pytest.fixture
def tmp_journal(tmp_path):
    def make(layer: str, run_key: str) -> RunJournal:
        return RunJournal(tmp_path / f"{run_key}.json", layer, run_key)
    return make


def test_receipt_roundtrip_and_atomic_file(tmp_journal):
    j = tmp_journal("draft", "key1")
    j.append(ChunkReceipt(index=0, x_hash="h0", y="成文", status="completed",
                          completion_status="complete", attempts=1))
    j.append(ChunkReceipt(index=1, x_hash="h1", y="", status="failed", error="boom"))
    reloaded = RunJournal(j.path, "draft", "key1")
    assert [r.index for r in reloaded.receipts] == [0, 1]
    assert reloaded.receipts[0].y == "成文"
    assert reloaded.receipts[1].status == "failed"
    assert not j.path.with_suffix(".tmp").exists()  # 原子替换后无残留


def test_run_key_mismatch_discards_old_receipts(tmp_journal):
    j = tmp_journal("draft", "old_key")
    j.append(ChunkReceipt(index=0, x_hash="h", y="旧", status="completed"))
    assert RunJournal(j.path, "draft", "old_key").match(0, "h") is not None
    assert RunJournal(j.path, "draft", "new_key").match(0, "h") is None
    assert RunJournal(j.path, "draft", "new_key").discarded_old
    assert RunJournal(j.path, "plot", "old_key").match(0, "h") is None  # 层也不同


def test_match_ignores_failed_receipts(tmp_journal):
    j = tmp_journal("draft", "k")
    j.append(ChunkReceipt(index=0, x_hash="h", y="", status="failed"))
    assert j.match(0, "h") is None


def test_resume_skips_completed_chunks(tmp_journal):
    spec = SPECS["draft"]
    provider1 = CountingProvider()
    runner1 = PipelineRunner(provider1, limits=None, assembler_max_bytes=10**9)
    j = tmp_journal("draft", "rk")
    r1 = runner1.run_layer(spec, [("第1块", ""), ("第2块", ""), ("第3块", "")],
                           x_chunk_length=2, journal=j)
    assert provider1.call_seq == 3
    assert r1.resumed_indices == []
    assert j.summary()["completed"] == 3

    provider2 = CountingProvider()
    runner2 = PipelineRunner(provider2, limits=None, assembler_max_bytes=10**9)
    r2 = runner2.run_layer(spec, [("第1块", ""), ("第2块", ""), ("第3块", "")],
                           x_chunk_length=2, journal=tmp_journal("draft", "rk"))
    assert provider2.call_seq == 0          # 全命中，零调用
    assert r2.resumed_indices == [0, 1, 2]
    assert r2.y == r1.y


def test_retry_regenerates_only_failed_chunk(tmp_journal):
    spec = SPECS["draft"]
    j = tmp_journal("draft", "rk")
    bad = CountingProvider(fail_on_indices={1})  # 第2块失败
    with pytest.raises(ProviderError):
        PipelineRunner(bad, limits=None, assembler_max_bytes=10**9).run_layer(
            spec, [("第1块", ""), ("第2块", "")], x_chunk_length=2, journal=j)
    assert j.summary()["completed"] == 1
    assert j.summary()["failed"] == 1

    good = CountingProvider()
    r = PipelineRunner(good, limits=None, assembler_max_bytes=10**9).run_layer(
        spec, [("第1块", ""), ("第2块", "")], x_chunk_length=2, journal=j)
    assert good.call_seq == 1               # 只重生成失败块
    assert r.resumed_indices == [0]
    assert r.y.count("Y") == 2


def test_x_drift_invalidates_receipt(tmp_journal):
    spec = SPECS["draft"]
    j = tmp_journal("draft", "rk")
    p1 = CountingProvider()
    PipelineRunner(p1, limits=None, assembler_max_bytes=10**9).run_layer(
        spec, [("第1块", "")], x_chunk_length=2, journal=j)

    p2 = CountingProvider()
    PipelineRunner(p2, limits=None, assembler_max_bytes=10**9).run_layer(
        spec, [("第1块已修改", "")], x_chunk_length=4, journal=j)
    assert p2.call_seq == 1                 # x 变了 → x_hash 不匹配 → 重生成


def test_compute_run_key_determinism_and_sensitivity():
    a = compute_run_key("draft", ["x1", "x2"], [("m", "reference", "c")], "d")
    b = compute_run_key("draft", ["x1", "x2"], [("m", "reference", "c")], "d")
    assert a == b and len(a) == 16
    assert a != compute_run_key("draft", ["x1", "x9"], [("m", "reference", "c")], "d")
    assert a != compute_run_key("plot", ["x1", "x2"], [("m", "reference", "c")], "d")
    assert a != compute_run_key("draft", ["x1", "x2"], [("m", "finalized", "c")], "d")
    assert a != compute_run_key("draft", ["x1", "x2"], [("m", "reference", "c")], "改")


def test_no_journal_behaves_as_before():
    provider = CountingProvider()
    runner = PipelineRunner(provider, limits=None, assembler_max_bytes=10**9)
    r = runner.run_layer(SPECS["plot"], [("第1块", "")], x_chunk_length=2)
    assert r.resumed_indices == []
    assert provider.call_seq == 1
