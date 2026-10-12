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


def _make_chaishu_dir(tmp_path, n_chapters=5, labeled=(1, 3), with_body=True,
                      with_entities=True):
    d = tmp_path / "拆书-测试参考书"
    d.mkdir()
    with open(d / "chapters.jsonl", "w", encoding="utf-8") as f:
        for i in range(1, n_chapters + 1):
            row = {"no": i, "title": f"测试章{i}", "volume": "正文", "chars": 2000}
            if with_body:
                row["body"] = f"第{i}章正文内容。" * 100
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(d / "labels.jsonl", "w", encoding="utf-8") as f:
        for no in labeled:
            f.write(json.dumps({"no": no, "summary": f"第{no}章概要", "hook": True,
                                "events": [{"type": "shuangdian", "note": "打脸"},
                                           {"type": "foreshadow", "note": "神秘怀表"}]},
                               ensure_ascii=False) + "\n")
    if with_entities:
        with open(d / "entities.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"no": 1, "characters": [{"name": "林奇", "note": "主角，摄影师"}],
                                "locations": [{"name": "纸扎铺", "note": "城南老店"}],
                                "items": []}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"no": 3, "characters": [{"name": "林奇", "note": "主角，摄影师"}],
                                "locations": [], "items": [{"name": "怀表", "note": "神秘遗物"}]},
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

    def test_import_populates_plot_draft_and_entities(self, client, tmp_path):
        d = _make_chaishu_dir(tmp_path)
        r = client.post("/api/corpus/import_chaishu", json={"dir": str(d)}).json()
        pid = r["project_id"]

        # 剧情层：已标注章概要（未标注章不硬凑）
        plot = client.get(f"/api/projects/{pid}/layers/plot").json()["version"]
        assert "第1章 测试章1：第1章概要" in plot["y"]
        assert "第2章" not in plot["y"]
        # 正文层：全书原文
        draft = client.get(f"/api/projects/{pid}/layers/draft").json()["version"]
        assert "第1章正文内容。" in draft["y"] and "第5章正文内容。" in draft["y"]
        # 实体库：entities.jsonl 直转 + labels 伏笔直转；同名幂等只补出场章
        ents = client.get(f"/api/projects/{pid}/entities").json()["entities"]
        by_name = {e["name"]: e for e in ents}
        assert by_name["林奇"]["appearances"] == [1, 3]
        assert by_name["纸扎铺"]["appearances"] == [1]
        assert by_name["怀表"]["appearances"] == [3]
        assert by_name["神秘怀表"]["type"] == "foreshadow"
        assert r["entities"]["character"] == 1 and r["entities"]["foreshadow"] == 1
        assert r["entities_preextracted"] is True

    def test_legacy_dir_requires_source_txt(self, client, tmp_path):
        d = _make_chaishu_dir(tmp_path, with_body=False)
        # 旧版底料无 body 且未给原书 txt → 明确报错
        r = client.post("/api/corpus/import_chaishu", json={"dir": str(d)})
        assert r.status_code == 422
        assert "原书 txt" in r.json()["detail"]
        # 给了原书 txt → 切章按章号补齐正文层
        src = tmp_path / "测试参考书.txt"
        src.write_text("第1章 测试章1\n第1章正文内容。\n\n第2章 测试章2\n第2章正文内容。",
                       encoding="utf-8")
        r2 = client.post("/api/corpus/import_chaishu",
                         json={"dir": str(d), "source_txt": str(src)}).json()
        draft = client.get(f"/api/projects/{r2['project_id']}/layers/draft").json()["version"]
        assert "第1章正文内容。" in draft["y"] and "第2章正文内容。" in draft["y"]

    def test_missing_entities_jsonl_flags_warning(self, client, tmp_path):
        d = _make_chaishu_dir(tmp_path, with_entities=False)
        r = client.post("/api/corpus/import_chaishu", json={"dir": str(d)}).json()
        assert r["entities_preextracted"] is False
        # 伏笔仍从 labels 直转
        assert r["entities"]["foreshadow"] == 1 and r["entities"]["character"] == 0
