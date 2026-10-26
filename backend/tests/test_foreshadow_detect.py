"""定稿时伏笔自动校验：新伏笔检出（补登记）测试。

reconcile_foreshadows 只能问「账上这些还了没有」，
detect_new_foreshadows 补的是另一半：「本章正文里现挖了哪些账上没有的坑」。
"""
from __future__ import annotations

import json

import pytest

from app.entities import EntityStore
from app.entities.extractor import (
    FORESHADOW_DETECT_SYSTEM,
    ForeshadowDetect,
    detect_new_foreshadows,
)
from app.pipeline.prompts import PromptStore
from app.providers.base import LLMResponse, Usage


class FakeProvider:
    """按调用顺序返回预设回复；用尽后重复最后一条（模拟模型卡在同一个错上）。"""

    default_model = None
    name = "fake"

    def __init__(self, replies: list[str], *, sticky_last: bool = True):
        self.replies = list(replies)
        self.sticky_last = sticky_last
        self.calls: list[list[dict]] = []

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        self.calls.append(messages)
        if self.replies:
            text = self.replies.pop(0)
        elif self.sticky_last and self._last is not None:
            text = self._last      # 重试时还是同一个坏输出 → complete_structured 会判定无进展
        else:
            text = '{"planted": []}'
        self._last = text
        return LLMResponse(text=text, usage=Usage(1, 1))


TEXT = "对方袖口沾着和唐迟一样的黑淤泥，他没问。邮戳的日期比唐迟进镇还早三天。"


@pytest.fixture
def store(tmp_path):
    return EntityStore(tmp_path / "entities.json")


def test_detects_and_registers_new_foreshadows(store):
    payload = json.dumps({"planted": [
        {"name": "黑淤泥同源", "summary": "两人袖口淤泥同源", "evidence": "对方袖口沾着和唐迟一样的黑淤泥"},
        {"name": "邮戳日期", "summary": "借运帖早于进镇", "evidence": "邮戳的日期比唐迟进镇还早三天"},
    ]}, ensure_ascii=False)
    r = detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)

    assert isinstance(r, ForeshadowDetect)
    assert len(r.planted) == 2
    names = {e.name for e in store.list(type="foreshadow")}
    assert names == {"黑淤泥同源", "邮戳日期"}
    for e in store.list(type="foreshadow"):
        assert e.status == "planted"
        assert e.source == "auto_detected"
        assert 3 in e.appearances


def test_evidence_is_kept_in_summary(store):
    """evidence 要落进 summary，作者才能核对模型不是在编。"""
    payload = json.dumps({"planted": [
        {"name": "黑淤泥同源", "summary": "同源", "evidence": "袖口的黑淤泥"}]}, ensure_ascii=False)
    detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)
    e = store.list(type="foreshadow")[0]
    assert "袖口的黑淤泥" in e.summary
    assert "同源" in e.summary


def test_does_not_duplicate_existing(store):
    store.add("foreshadow", "黑淤泥同源", summary="已存在", chapter=1)
    payload = json.dumps({"planted": [
        {"name": "黑淤泥同源", "summary": "重复", "evidence": "x"},
        {"name": "全新坑", "summary": "新", "evidence": "y"},
    ]}, ensure_ascii=False)
    r = detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)
    assert len(r.planted) == 1
    assert r.planted[0]["name"] == "全新坑"
    assert len(store.list(type="foreshadow")) == 2


def test_dedupe_within_one_response(store):
    payload = json.dumps({"planted": [
        {"name": "同名坑", "summary": "a", "evidence": "x"},
        {"name": "同名坑", "summary": "b", "evidence": "y"},
    ]}, ensure_ascii=False)
    r = detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)
    assert len(r.planted) == 1
    assert len(store.list(type="foreshadow")) == 1


def test_empty_result_is_not_an_error(store):
    r = detect_new_foreshadows(FakeProvider(['{"planted": []}']), store, PromptStore(), TEXT, chapter=3)
    assert r.planted == []
    assert not r.warning
    assert store.list(type="foreshadow") == []


def test_bad_payload_does_not_raise(store):
    """模型连续吐了别的东西：记 warning，不抛——检出失败不该阻断定稿。"""
    r = detect_new_foreshadows(FakeProvider(["这里没有 JSON"]), store, PromptStore(),
                               TEXT, chapter=3)
    assert r.warning, "解析失败必须留下 warning，否则作者以为账已对平"
    assert r.planted == []
    assert store.list(type="foreshadow") == []


def test_malformed_rows_are_skipped(store):
    payload = json.dumps({"planted": [
        "不是 dict", {"summary": "没有 name"}, {"name": "", "summary": "空名"},
        {"name": "有效", "summary": "好", "evidence": "z"},
    ]}, ensure_ascii=False)
    r = detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)
    assert [p["name"] for p in r.planted] == ["有效"]


def test_prompt_includes_text_and_has_no_forced_answer(store):
    """检出 prompt 必须带正文，且 system 里明确「拿不准就不要报」。"""
    prov = FakeProvider(['{"planted": []}'])
    detect_new_foreshadows(prov, store, PromptStore(), TEXT, chapter=3)
    user = prov.calls[0][1]["content"]
    assert TEXT.strip()[:20] in user
    sys_msg = prov.calls[0][0]["content"]
    assert sys_msg == FORESHADOW_DETECT_SYSTEM
    assert "拿不准" in sys_msg


def test_new_foreshadow_shows_up_in_due_list(store):
    """新登记的伏笔下一章就会进「到期伏笔」清单——这是补登记的全部意义。"""
    payload = json.dumps({"planted": [
        {"name": "黑淤泥同源", "summary": "同源", "evidence": "x"}]}, ensure_ascii=False)
    detect_new_foreshadows(FakeProvider([payload]), store, PromptStore(), TEXT, chapter=3)
    planted = store.list(type="foreshadow", status="planted")
    assert len(planted) == 1
    # 账龄锚点：appearance 最小值 = 埋设章 3
    assert min(planted[0].appearances) == 3


def test_finalize_reports_both_halves(tmp_path, monkeypatch):
    """定稿返回值里要同时有 reconcile 和 detect 两份报告。"""
    from app.wizard import DeskStore, WizardStore, finalize_chapter
    from app.projects import ProjectStore
    from app.review import MarkerStore

    base = tmp_path / "projects"
    project = ProjectStore(base)
    pid = project.create("测试", "").id
    pdir = base / pid
    desk = DeskStore(pdir / "desk.json")
    entities = EntityStore(pdir / "entities.json")
    entities.add("foreshadow", "旧坑", summary="账上已有的", chapter=1)
    desk.record(1, "draft", TEXT)
    desk.record(1, "polish", TEXT)

    calls = {"n": 0}

    class P:
        default_model = None
        name = "p"

        def chat(self, messages, model=None, max_tokens=None, extra=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # extract_entities
                return LLMResponse(text='{"entities": []}', usage=Usage(1, 1))
            if calls["n"] == 2:
                # reconcile_foreshadows
                return LLMResponse(text='{"paid": [], "advanced": []}', usage=Usage(1, 1))
            # detect_new_foreshadows
            return LLMResponse(
                text='{"planted": [{"name": "正文新坑", "summary": "s", "evidence": "e"}]}',
                usage=Usage(1, 1))

    r = finalize_chapter(P(), PromptStore(), project, pid, entities,
                         MarkerStore(pdir / "markers.json"), desk, 1,
                         auto_entities=True)
    assert "foreshadow" in r
    assert "foreshadow_detected" in r
    assert [p["name"] for p in r["foreshadow_detected"]["planted"]] == ["正文新坑"]
    assert "正文新坑" in {e.name for e in entities.list(type="foreshadow")}