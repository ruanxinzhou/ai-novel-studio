"""拆书产物项目化测试：导入建参考项目 + 层版本更新复用 + 章台参考材料透传。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.projects import ProjectStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 语料写入打桩到临时路径，不污染真实 excerpts.csv
    written: dict[str, list] = {}

    def fake_replace(article_id, rows, path=None):
        written[article_id] = rows
        return len(rows)

    monkeypatch.setattr("app.api.main.corpus_replace_article", fake_replace)
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._journal_dir", lambda pid: tmp_path / "runs" / pid)
    monkeypatch.setattr("app.api.main._markers_for",
                        lambda project: __import__("app.review", fromlist=["MarkerStore"]).MarkerStore(
                            tmp_path / "mk" / f"{project or 'default'}.json"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: __import__("app.entities", fromlist=["EntityStore"]).EntityStore(
                            tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._wizard_for",
                        lambda pid: __import__("app.wizard", fromlist=["WizardStore"]).WizardStore(
                            tmp_path / "wiz" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: __import__("app.wizard", fromlist=["DeskStore"]).DeskStore(
                            tmp_path / "dsk" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


def _make_chaishu_dir(tmp_path, n_chapters=5, labeled=(1, 3)):
    d = tmp_path / "拆书-测试参考书"
    d.mkdir()
    with open(d / "chapters.jsonl", "w", encoding="utf-8") as f:
        for i in range(1, n_chapters + 1):
            f.write(json.dumps({"no": i, "title": f"测试章{i}", "volume": "正文",
                                "chars": 2000}, ensure_ascii=False) + "\n")
    with open(d / "labels.jsonl", "w", encoding="utf-8") as f:
        for no in labeled:
            f.write(json.dumps({"no": no, "summary": f"第{no}章概要", "hook": True,
                                "events": [{"type": "shuangdian", "note": "打脸"}]},
                               ensure_ascii=False) + "\n")
    (d / "report.md").write_text("# 节奏体检表\n\n总章数 5 章，爽点章距中位 3 章。", encoding="utf-8")
    return d


class TestChaishuReferenceProject:
    def test_import_creates_reference_project(self, client, tmp_path):
        d = _make_chaishu_dir(tmp_path)
        r = client.post("/api/corpus/import_chaishu", json={"dir": str(d)})
        assert r.status_code == 200
        body = r.json()
        assert body["project_id"]

        projects = client.get("/api/projects").json()["projects"]
        ref = [p for p in projects if p["id"] == body["project_id"]]
        assert ref and ref[0]["kind"] == "reference"
        assert ref[0]["title"] == "测试参考书"

        # 简介层 = 体检表；大纲层 = 全书脉络（每章一行，已标注章带概要）
        pid = body["project_id"]
        intro = client.get(f"/api/projects/{pid}/layers/intro").json()["version"]
        assert "节奏体检表" in intro["y"]
        outline = client.get(f"/api/projects/{pid}/layers/outline").json()["version"]
        assert "第1章 测试章1：第1章概要" in outline["y"]
        assert "第2章 测试章2" in outline["y"] and "第2章概要" not in outline["y"]

    def test_reimport_reuses_project_and_bumps_version(self, client, tmp_path):
        d = _make_chaishu_dir(tmp_path)
        r1 = client.post("/api/corpus/import_chaishu", json={"dir": str(d)}).json()
        r2 = client.post("/api/corpus/import_chaishu", json={"dir": str(d)}).json()
        assert r1["project_id"] == r2["project_id"]
        # 项目不重复建
        projects = client.get("/api/projects").json()["projects"]
        refs = [p for p in projects if p.get("kind") == "reference"]
        assert len(refs) == 1
        # outline 层版本递增（重导 = 更新）
        pid = r1["project_id"]
        v2 = client.get(f"/api/projects/{pid}/layers/outline").json()["version"]
        assert v2["seq"] >= 2

    def test_own_projects_unaffected(self, client, tmp_path):
        own = client.post("/api/projects", json={"title": "我的书"}).json()
        assert own["kind"] == "own"
        d = _make_chaishu_dir(tmp_path)
        ref = client.post("/api/corpus/import_chaishu", json={"dir": str(d)}).json()
        assert ref["project_id"] != own["id"]
        # 自己的项目不会被标题匹配搞混
        store_list = client.get("/api/projects").json()["projects"]
        kinds = {p["title"]: p["kind"] for p in store_list}
        assert kinds["我的书"] == "own" and kinds["测试参考书"] == "reference"
