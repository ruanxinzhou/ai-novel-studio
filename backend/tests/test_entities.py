"""M3b 实体库测试：存储 CRUD / 抽取回灌 / 抽取器 / API。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore, extract_entities, reconcile_foreshadows
from app.entities.store import ENTITY_TYPES
from app.pipeline.prompts import PromptStore
from app.providers.base import LLMResponse, Usage


# ---- EntityStore ----

class TestEntityStore:
    def test_add_list_find(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "林凡", "凡人少年", "七玄门弟子")
        s.add("item", "掌天瓶", "神秘小瓶")
        assert s.get(a.id).name == "林凡"
        assert [e.name for e in s.list(type="item")] == ["掌天瓶"]
        assert s.find("character", "林凡") is not None
        assert s.find("character", "林凡儿") is None

    def test_add_rejects(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        with pytest.raises(ValueError, match="未知实体类型"):
            s.add("monster", "噬金兽")
        with pytest.raises(ValueError, match="不能为空"):
            s.add("character", "  ")
        s.add("character", "林凡")
        with pytest.raises(ValueError, match="已存在"):
            s.add("character", "林凡")

    def test_foreshadow_default_status_planted(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        f = s.add("foreshadow", "掌天瓶的来历", "小瓶来历未明")
        assert f.status == "planted"
        c = s.add("character", "韩立")
        assert c.status == "active"

    def test_update_and_conflict(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "林凡", summary="旧描述")
        s.add("character", "林凡儿")
        s.update(a.id, summary="青牛镇少年", status="retired")
        assert s.get(a.id).summary == "青牛镇少年"
        with pytest.raises(ValueError, match="同名实体已存在"):
            s.update(a.id, name="林凡儿")
        with pytest.raises(ValueError, match="非法状态"):
            s.update(a.id, status="dead")

    def test_delete_and_missing(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("item", "飞剑")
        s.delete(a.id)
        assert s.list() == []
        with pytest.raises(ValueError, match="不存在"):
            s.delete(a.id)
        with pytest.raises(ValueError, match="不存在"):
            s.get(a.id)

    def test_record_appearance_dedup_sorted(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "林凡", chapter=3)
        s.record_appearance(a.id, 1)
        s.record_appearance(a.id, 3)
        s.record_appearance(a.id, 2)
        assert s.get(a.id).appearances == [1, 2, 3]

    def test_merge_extracted_creates_and_updates(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        existing = s.add("character", "林凡", summary="主角")
        result = s.merge_extracted([
            {"type": "character", "name": "林凡", "summary": "", "details": "七玄门"},
            {"type": "item", "name": "掌天瓶", "summary": "神秘小瓶"},
            {"type": "monster", "name": "噬金兽"},
            {"type": "item", "name": "  "},
        ], chapter=3)
        assert [e["name"] for e in result["created"]] == ["掌天瓶"]
        assert [e["name"] for e in result["updated"]] == ["林凡"]
        assert len(result["dropped"]) == 2
        updated = s.get(existing.id)
        assert updated.details == "七玄门"      # 补空不覆盖
        assert updated.summary == "主角"        # 已有 summary 不动
        assert updated.appearances == [3]
        assert updated.source == "manual"       # 来源保留人工
        # 幂等：同章节重复抽取不重复记
        s.merge_extracted([{"type": "item", "name": "掌天瓶"}], chapter=3)
        assert s.find("item", "掌天瓶").appearances == [3]

    def test_persistence_roundtrip(self, tmp_path):
        path = tmp_path / "e.json"
        s1 = EntityStore(path)
        s1.add("foreshadow", "瓶中液体", status="paid")
        s2 = EntityStore(path)
        assert s2.list()[0].status == "paid"

    def test_all_types_labeled(self):
        for t in ENTITY_TYPES:
            from app.entities.store import TYPE_LABELS
            assert t in TYPE_LABELS


# ---- 抽取器 ----

class GoodProvider:
    name = "mock"
    default_model = "m"

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        return LLMResponse(
            text='{"entities": [{"type": "character", "name": "韩立", "summary": "主角"}]}',
            usage=Usage(input_tokens=1, output_tokens=1))


class BadJsonProvider:
    name = "mock"
    default_model = "m"

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        return LLMResponse(text="这不是JSON", usage=Usage(input_tokens=1, output_tokens=1))


def test_extract_entities_merges_into_store(tmp_path):
    store = EntityStore(tmp_path / "e.json")
    prompts = PromptStore()
    result = extract_entities(GoodProvider(), store, prompts, "韩立下山。", chapter=1)
    assert result.total == 1
    assert store.find("character", "韩立").appearances == [1]


def test_extract_entities_rejects_empty_text(tmp_path):
    with pytest.raises(ValueError, match="不能为空"):
        extract_entities(GoodProvider(), EntityStore(tmp_path / "e.json"), PromptStore(), "  ")


def test_extract_entities_bad_json_raises(tmp_path):
    with pytest.raises(ValueError, match="无法解析"):
        extract_entities(BadJsonProvider(), EntityStore(tmp_path / "e.json"),
                         PromptStore(), "正文")


def test_extract_uses_prompt_store_entities_layer(tmp_path):
    seen = {}

    class SpyProvider(GoodProvider):
        def chat(self, messages, model=None, max_tokens=None, extra=None):
            seen["system"] = messages[0]["content"]
            seen["user"] = messages[1]["content"]
            return super().chat(messages, model, max_tokens, extra)

    prompts = PromptStore()
    prompts.set_override("entities", "自定义抽取规则。")
    extract_entities(SpyProvider(), EntityStore(tmp_path / "e.json"), prompts, "韩立")
    assert "自定义抽取规则。" in seen["user"]
    assert "设定管理员" in seen["system"]  # 合同区不可覆盖


# ---- 伏笔兑现问询（P1② 定稿回账） ----

class ReconcileProvider:
    name = "mock"
    default_model = "m"

    def __init__(self, payload: str):
        self.payload = payload
        self.seen_user = ""

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        self.seen_user = messages[1]["content"]
        return LLMResponse(text=self.payload, usage=Usage(input_tokens=1, output_tokens=1))


def _planted_store(tmp_path):
    store = EntityStore(tmp_path / "e.json")
    store.add("foreshadow", "青铜镜来历", summary="镜子会低语", chapter=2)
    store.add("foreshadow", "床下的朋友", summary="布偶归位", chapter=1)
    store.add("foreshadow", "已收的旧坑", summary="早期伏笔", chapter=1)
    store.update(store.find("foreshadow", "已收的旧坑").id, status="paid")
    return store


def test_reconcile_marks_paid_and_advanced(tmp_path):
    store = _planted_store(tmp_path)
    p = ReconcileProvider('{"paid": ["青铜镜来历"], "advanced": ["床下的朋友"]}')
    r = reconcile_foreshadows(p, store, PromptStore(), "本章揭晓镜子来历。", chapter=5)
    assert r.paid == ["青铜镜来历"] and r.advanced == ["床下的朋友"]
    mirror = store.find("foreshadow", "青铜镜来历")
    assert mirror.status == "paid" and mirror.appearances == [2, 5]
    buddy = store.find("foreshadow", "床下的朋友")
    assert buddy.status == "planted" and buddy.appearances == [1, 5]
    # 清单只含未回收伏笔，且带上埋章
    assert "青铜镜来历：镜子会低语（第2章埋）" in p.seen_user
    assert "已收的旧坑" not in p.seen_user
    assert "本章正文" in p.seen_user


def test_reconcile_none_without_planted(tmp_path):
    store = _planted_store(tmp_path)
    store.update(store.find("foreshadow", "青铜镜来历").id, status="dropped")
    store.update(store.find("foreshadow", "床下的朋友").id, status="dropped")
    assert reconcile_foreshadows(
        ReconcileProvider("{}"), store, PromptStore(), "正文") is None


def test_reconcile_unknown_names_ignored(tmp_path):
    store = _planted_store(tmp_path)
    p = ReconcileProvider('{"paid": ["不存在的伏笔"], "advanced": ["青铜镜来历 "]}')
    r = reconcile_foreshadows(p, store, PromptStore(), "正文", chapter=3)
    assert r.paid == [] and r.advanced == ["青铜镜来历"]
    assert store.find("foreshadow", "青铜镜来历").status == "planted"
    assert store.find("foreshadow", "青铜镜来历").appearances == [2, 3]


def test_reconcile_density_warning(tmp_path):
    store = _planted_store(tmp_path)
    for i in range(10):
        store.add("foreshadow", f"坑{i}", summary="待兑现", chapter=1)
    r = reconcile_foreshadows(
        ReconcileProvider('{"paid": [], "advanced": []}'), store, PromptStore(),
        "正文", chapter=12)
    assert "伏笔过密" in r.warning and "零回收" in r.warning


def test_reconcile_bad_json_raises(tmp_path):
    store = _planted_store(tmp_path)
    with pytest.raises(ValueError, match="无法解析"):
        reconcile_foreshadows(BadJsonProvider(), store, PromptStore(), "正文", chapter=2)


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store",
                        __import__("app.projects", fromlist=["ProjectStore"]).ProjectStore(tmp_path / "projects"))
    stores: dict[str, EntityStore] = {}

    def _entities_for(pid: str) -> EntityStore:
        if pid not in stores:
            stores[pid] = EntityStore(tmp_path / "entities" / f"{pid}.json")
        return stores[pid]

    monkeypatch.setattr("app.api.main._entities_for", _entities_for)
    from app.api.main import app
    return TestClient(app)


def test_api_entities_crud(client):
    pid = client.post("/api/projects", json={"title": "测试书"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/entities",
                    json={"type": "character", "name": "林凡", "summary": "主角"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    r = client.get(f"/api/projects/{pid}/entities", params={"type": "character"})
    assert r.json()["counts"]["character"] == 1
    r = client.put(f"/api/projects/{pid}/entities/{eid}", json={"status": "retired"})
    assert r.json()["status"] == "retired"
    r = client.post(f"/api/projects/{pid}/entities",
                    json={"type": "character", "name": "林凡"})
    assert r.status_code == 422
    r = client.delete(f"/api/projects/{pid}/entities/{eid}")
    assert r.json()["deleted"] == eid
    assert client.get(f"/api/projects/{pid}/entities").json()["total"] == 0


def test_api_entities_extract(client, monkeypatch):
    pid = client.post("/api/projects", json={"title": "抽取书"}).json()["id"]

    class FakeRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            assert step == "chore"
            return LLMResponse(
                text='{"entities": [{"type": "location", "name": "七玄门", "summary": "门派"}]}',
                usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter())
    r = client.post(f"/api/projects/{pid}/entities/extract",
                    json={"text": "韩立来到七玄门。", "chapter": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"][0]["name"] == "七玄门"
    assert body["counts"]["location"] == 1


def test_api_entities_extract_bad_output_502(client, monkeypatch):
    pid = client.post("/api/projects", json={"title": "坏输出"}).json()["id"]

    class BadRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            return LLMResponse(text="乱码", usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: BadRouter())
    r = client.post(f"/api/projects/{pid}/entities/extract", json={"text": "正文"})
    assert r.status_code == 502
