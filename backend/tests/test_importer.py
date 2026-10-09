"""M4 导入拆解测试：中文数字切分 / 收据幂等 / 拆解管线 / API。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.importer import ImportJournal, analyze_chapter, cn_num_to_int, split_import
from app.pipeline.prompts import PromptStore
from app.providers.base import LLMResponse, Usage


# ---- 中文数字 ----

@pytest.mark.parametrize("raw,expect", [
    ("12", 12), ("一", 1), ("十", 10), ("十一", 11), ("二十", 20),
    ("一百二十三", 123), ("一千零一", 1001), ("一万零一章", None),
    ("两", 2), ("两百", 200), ("一万二千三百四十五", 12345),
])
def test_cn_num_to_int(raw, expect):
    if expect is None:  # 带干扰字
        with pytest.raises(ValueError):
            cn_num_to_int(raw)
    else:
        assert cn_num_to_int(raw) == expect


def test_cn_num_rejects_garbage():
    with pytest.raises(ValueError):
        cn_num_to_int("章")
    with pytest.raises(ValueError):
        cn_num_to_int("")


# ---- 切分 ----

def test_split_import_mixed_numerals():
    text = ("开头语\n"
            "第一章 出山\n正文一。\n"
            "第2章 入门\n正文二。\n"
            "第一百二十三章 终局\n正文三。")
    parts = split_import(text)
    assert [p["no"] for p in parts] == [0, 1, 2, 123]
    assert parts[0]["title"] == "前言"
    assert parts[1]["title"] == "出山"
    assert parts[3]["chars"] == len("第一百二十三章 终局\n正文三。")


def test_split_import_no_headings():
    parts = split_import("整篇没有章节标签的文本")
    assert parts == [{"no": 0, "title": "", "body": "整篇没有章节标签的文本",
                      "chars": len("整篇没有章节标签的文本")}]
    assert split_import("  ") == []


# ---- 收据 ----

def test_journal_roundtrip_and_reset(tmp_path):
    path = tmp_path / "j.json"
    j1 = ImportJournal(path)
    j1.mark(1, "entities", "completed")
    j1.mark(1, "outline", "completed", result="本章大纲")
    j1.mark(2, "entities", "failed", error="超时")
    j2 = ImportJournal(path)
    assert j2.get(1, "outline").result == "本章大纲"
    assert j2.get(2, "entities").status == "failed"
    assert j2.completed_outlines() == [(1, "本章大纲")]
    j2.reset_chapter(1)
    assert j2.get(1, "entities") is None
    assert j2.get(1, "outline") is None


# ---- 拆解管线 ----

class FakeProvider:
    name = "mock"
    default_model = "m"

    def __init__(self):
        self.n = 0

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        self.n += 1
        if "抽取实体" in messages[0]["content"] or "设定管理员" in messages[0]["content"]:
            return LLMResponse(
                text='{"entities": [{"type": "character", "name": "韩立", "summary": "主角"}]}',
                usage=Usage(input_tokens=1, output_tokens=1))
        return LLMResponse(text="韩立下山，初入七玄门。", usage=Usage(input_tokens=1, output_tokens=1))


def test_analyze_chapter_runs_two_steps_and_idempotent(tmp_path):
    es = EntityStore(tmp_path / "e.json")
    journal = ImportJournal(tmp_path / "j.json")
    provider = FakeProvider()
    r1 = analyze_chapter(provider, es, PromptStore(), journal, 1, "韩立下山。")
    assert r1["entities"]["created"] == ["韩立"]
    assert r1["outline"] == "韩立下山，初入七玄门。"
    assert not r1["outline_reused"]
    assert provider.n == 2
    # 重跑：两步都复用，不再调 LLM
    r2 = analyze_chapter(provider, es, PromptStore(), journal, 1, "韩立下山。")
    assert r2["entities"] == {"reused": True}
    assert r2["outline_reused"]
    assert provider.n == 2


def test_analyze_chapter_empty_text_rejected(tmp_path):
    with pytest.raises(ValueError, match="为空"):
        analyze_chapter(FakeProvider(), EntityStore(tmp_path / "e.json"),
                        PromptStore(), ImportJournal(tmp_path / "j.json"), 1, "  ")


def test_analyze_chapter_records_failure(tmp_path):
    class BadProvider(FakeProvider):
        def chat(self, messages, model=None, max_tokens=None, extra=None):
            self.n += 1
            if "设定管理员" in messages[0]["content"]:
                return LLMResponse(text="乱码", usage=Usage(input_tokens=1, output_tokens=1))
            return LLMResponse(text="大纲", usage=Usage(input_tokens=1, output_tokens=1))

    es = EntityStore(tmp_path / "e.json")
    journal = ImportJournal(tmp_path / "j.json")
    with pytest.raises(ValueError, match="无法解析"):
        analyze_chapter(BadProvider(), es, PromptStore(), journal, 1, "正文")
    assert journal.get(1, "entities").status == "failed"
    assert journal.get(1, "outline") is None  # 实体失败则大纲不跑


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    from app.projects import ProjectStore as PS
    monkeypatch.setattr("app.api.main._project_store", PS(tmp_path / "projects"))
    entity_stores: dict[str, EntityStore] = {}
    journals: dict[str, ImportJournal] = {}

    def _entities_for(pid: str) -> EntityStore:
        if pid not in entity_stores:
            entity_stores[pid] = EntityStore(tmp_path / "entities" / f"{pid}.json")
        return entity_stores[pid]

    def _journal_for(pid: str) -> ImportJournal:
        if pid not in journals:
            journals[pid] = ImportJournal(tmp_path / "journals" / f"{pid}.json")
        return journals[pid]

    monkeypatch.setattr("app.api.main._entities_for", _entities_for)
    monkeypatch.setattr("app.api.main._import_journal_for", _journal_for)
    from app.api.main import app
    return TestClient(app)


def _make_project(client, title: str) -> str:
    return client.post("/api/projects", json={"title": title}).json()["id"]


def test_api_import_preview_and_adopt(client):
    pid = _make_project(client, "导入书")
    body = {"text": "第一章 出山\n正文。\n\n第2章 入门\n正文二。", "dry_run": True}
    r = client.post(f"/api/projects/{pid}/import/text", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["adopted"] is False and data["total"] == 2
    assert client.get(f"/api/projects/{pid}/layers/draft").json()["version"] is None
    body["dry_run"] = False
    r = client.post(f"/api/projects/{pid}/import/text", json=body)
    assert r.json()["adopted"] is True and r.json()["draft_version"] == 1
    detail = client.get(f"/api/projects/{pid}/layers/draft").json()
    assert detail["version"]["seq"] == 1


def test_api_import_empty_rejected(client):
    pid = _make_project(client, "空导入")
    r = client.post(f"/api/projects/{pid}/import/text", json={"text": "  "})
    assert r.status_code == 422


def test_api_analyze_from_draft_and_status(client, monkeypatch):
    pid = _make_project(client, "拆解书")
    client.post(f"/api/projects/{pid}/import/text",
                json={"text": "第1章 出山\n韩立下山。\n\n第2章 入门\n韩立入门。", "dry_run": False})
    monkeypatch.setattr("app.api.main.get_router", lambda: _FakeRouter())
    r = client.post(f"/api/projects/{pid}/import/analyze_chapter", json={"chapter": 1})
    assert r.status_code == 200, r.text
    assert r.json()["outline"]
    r = client.post(f"/api/projects/{pid}/import/analyze_chapter", json={"chapter": 99})
    assert r.status_code == 404
    status = client.get(f"/api/projects/{pid}/import/status").json()
    assert status["total"] == 2 and status["done"] == 1
    assert status["chapters"][0]["entities_status"] == "completed"
    assert status["chapters"][1]["outline_status"] is None
    # force 重拆
    r = client.post(f"/api/projects/{pid}/import/analyze_chapter",
                    json={"chapter": 1, "force": True})
    assert r.status_code == 200
    assert not r.json()["outline_reused"]


class _FakeRouter:
    def chat(self, step, messages, model=None, max_tokens=None):
        assert step == "chore"
        if "设定管理员" in messages[0]["content"]:
            return LLMResponse(
                text='{"entities": [{"type": "character", "name": "韩立", "summary": "主角"}]}',
                usage=Usage(input_tokens=1, output_tokens=1))
        return LLMResponse(text="韩立本章推进。", usage=Usage(input_tokens=1, output_tokens=1))


def test_api_adopt_outline_requires_completed(client, monkeypatch):
    pid = _make_project(client, "汇总书")
    client.post(f"/api/projects/{pid}/import/text",
                json={"text": "第1章 出山\n韩立下山。", "dry_run": False})
    r = client.post(f"/api/projects/{pid}/import/adopt_outline")
    assert r.status_code == 422
    monkeypatch.setattr("app.api.main.get_router", lambda: _FakeRouter())
    client.post(f"/api/projects/{pid}/import/analyze_chapter", json={"chapter": 1})
    r = client.post(f"/api/projects/{pid}/import/adopt_outline")
    assert r.status_code == 200
    body = r.json()
    assert body["chapters"] == 1 and body["seq"] == 1
    detail = client.get(f"/api/projects/{pid}/layers/outline").json()
    assert "韩立本章推进" in detail["version"]["y"]
    assert "第1章" in detail["version"]["y"]
