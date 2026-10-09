"""M3a 小说项目测试：版本链 / 采纳流 / API / 标记项目隔离。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.projects import ProjectStore
from app.review import MarkerStore


# ---- ProjectStore ----

def test_create_list_delete_roundtrip(tmp_path):
    s = ProjectStore(tmp_path)
    meta = s.create("凡人修仙传", "一个凡人的修仙故事")
    assert meta.id and meta.title == "凡人修仙传"
    assert [p["id"] for p in s.list()] == [meta.id]
    s.update(meta.id, title="凡人修仙", intro="改过的简介")
    assert s.get(meta.id).title == "凡人修仙"
    s.delete(meta.id)
    assert s.list() == []
    with pytest.raises(FileNotFoundError):
        s.get(meta.id)


def test_create_rejects_empty_title(tmp_path):
    with pytest.raises(ValueError):
        ProjectStore(tmp_path).create("  ")


def test_adopt_builds_version_chain(tmp_path):
    s = ProjectStore(tmp_path)
    pid = s.create("测试书").id
    v1 = s.adopt(pid, "outline", [("第1章", "")], "第一版大纲", note="初稿")
    v2 = s.adopt(pid, "outline", [("第1章", "")], "第二版大纲", note="调整节奏")
    assert (v1.seq, v2.seq) == (1, 2)
    assert s.current(pid, "outline").y == "第二版大纲"
    assert [v.seq for v in s.history(pid, "outline")] == [1, 2]
    # 版本文件独立持久化，新实例可读
    s2 = ProjectStore(tmp_path)
    assert s2.current(pid, "outline").y == "第二版大纲"
    assert s2.version(pid, "outline", 1).note == "初稿"


def test_layers_are_independent(tmp_path):
    s = ProjectStore(tmp_path)
    pid = s.create("书").id
    s.adopt(pid, "intro", [], "简介v1")
    s.adopt(pid, "draft", [("x", "")], "正文v1")
    s.adopt(pid, "draft", [("x", "")], "正文v2")
    assert s.current(pid, "intro").y == "简介v1"
    assert s.current(pid, "draft").y == "正文v2"
    assert s.current(pid, "plot") is None


def test_rollback_creates_new_version_not_rewrite(tmp_path):
    s = ProjectStore(tmp_path)
    pid = s.create("书").id
    s.adopt(pid, "plot", [], "v1")
    s.adopt(pid, "plot", [], "v2")
    v = s.rollback(pid, "plot", 1)
    assert v.seq == 3 and v.y == "v1" and "回滚" in v.note
    assert [x.y for x in s.history(pid, "plot")] == ["v1", "v2", "v1"]  # 历史完整保留


def test_unknown_layer_rejected(tmp_path):
    s = ProjectStore(tmp_path)
    pid = s.create("书").id
    with pytest.raises(ValueError):
        s.adopt(pid, "nope", [], "y")


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    from app.api.main import app
    return TestClient(app)


def test_api_project_crud_and_adopt_flow(client):
    r = client.post("/api/projects", json={"title": "我的小说", "intro": "一句话点子"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # 空项目：各层无 current
    d = client.get(f"/api/projects/{pid}").json()
    assert d["title"] == "我的小说"
    assert d["layers"]["intro"]["current"] is None

    # 采纳两版大纲 + 一版正文
    client.post(f"/api/projects/{pid}/layers/outline",
                json={"y": "大纲v1", "x_pairs": [["第1章", ""]], "note": "初稿"})
    client.post(f"/api/projects/{pid}/layers/outline", json={"y": "大纲v2", "note": "改"})
    client.post(f"/api/projects/{pid}/layers/draft", json={"y": "第一章正文"})

    layer = client.get(f"/api/projects/{pid}/layers/outline").json()
    assert layer["version"]["y"] == "大纲v2"
    assert [h["seq"] for h in layer["history"]] == [1, 2]

    # 查看历史版本
    old = client.get(f"/api/projects/{pid}/layers/outline", params={"seq": 1}).json()
    assert old["version"]["y"] == "大纲v1"

    # 回滚到 v1（成为 v3）
    rb = client.post(f"/api/projects/{pid}/layers/outline/rollback", json={"seq": 1})
    assert rb.status_code == 200 and rb.json()["seq"] == 3
    assert client.get(f"/api/projects/{pid}/layers/outline").json()["version"]["y"] == "大纲v1"

    # 列表与删除
    assert client.get("/api/projects").json()["projects"][0]["id"] == pid
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_api_adopt_rejects_empty(client):
    pid = client.post("/api/projects", json={"title": "书"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/layers/draft", json={"y": "  "}).status_code == 422


def test_api_markers_project_isolation(client):
    pid = client.post("/api/projects", json={"title": "书A"}).json()["id"]
    pid2 = client.post("/api/projects", json={"title": "书B"}).json()["id"]
    client.post("/api/markers", json={"target_chapter": 3, "content": "全局标记"})
    client.post(f"/api/markers?project={pid}", json={"target_chapter": 3, "content": "A的标记"})
    client.post(f"/api/markers?project={pid}", json={"target_chapter": 3, "content": "A的标记"})  # 幂等
    g = client.get("/api/markers").json()
    a = client.get("/api/markers", params={"project": pid}).json()
    b = client.get("/api/markers", params={"project": pid2}).json()
    assert [m["content"] for m in g["markers"]] == ["全局标记"]
    assert [m["content"] for m in a["markers"]] == ["A的标记"]  # 同内容未重复
    assert b["markers"] == []


def test_api_run_with_unknown_project_404(client):
    r = client.post("/api/runs/draft", json={"x_pairs": [["x", ""]], "project": "ghost"})
    assert r.status_code == 404
