"""M3b Obsidian Vault 导出测试：结构 / frontmatter / 互链 / API。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.entities.vault import export_vault, linkify, split_chapters
from app.projects import ProjectStore


# ---- 纯函数 ----

def test_split_chapters_by_headings():
    text = "开头引言\n第1章 出山\n正文一。\n\n第2章 入门\n正文二。\n第12章 结局\n正文三。"
    parts = split_chapters(text)
    assert [no for no, _ in parts] == [0, 1, 2, 12]
    assert parts[0][1] == "开头引言"
    assert parts[1][1].startswith("第1章 出山")


def test_split_chapters_no_heading_returns_whole():
    assert split_chapters("没有标签的全文") == [(0, "没有标签的全文")]
    assert split_chapters("   ") == []


def test_linkify_longest_first():
    out = linkify("林凡儿看着林凡。", ["林凡", "林凡儿"])
    assert out == "[[林凡儿]]看着[[林凡]]。"


# ---- 导出 ----

@pytest.fixture
def seeded(tmp_path):
    ps = ProjectStore(tmp_path / "projects")
    pid = ps.create("凡人修仙传", "一个凡人的修仙故事").id
    ps.adopt(pid, "intro", [], "韩立的故事。")
    ps.adopt(pid, "outline", [("第1章", "")],
             "第1章 出山\n韩立下山。\n\n第2章 入门\n韩立入门。")
    ps.adopt(pid, "draft", [("第1章", "")],
             "第1章 出山\n韩立告别林凡儿，得到掌天瓶。\n\n第2章 入门\n韩立入门七玄门。")
    es = EntityStore(tmp_path / "projects" / pid / "entities.json")
    es.add("character", "韩立", "主角", chapter=1)
    es.add("character", "林凡儿", "故人", chapter=1)
    es.add("item", "掌天瓶", "神秘小瓶", chapter=1)
    es.add("foreshadow", "掌天瓶的来历", "未明", chapter=1)
    return ps, es, pid


def test_export_vault_structure(seeded, tmp_path):
    ps, es, pid = seeded
    out_root = tmp_path / "vault"
    result = export_vault(ps, es, pid, out_root)
    book_dir = out_root / "凡人修仙传"
    assert result.entities == 4 and result.chapters == 2
    assert set(result.layers_exported) == {"intro", "outline", "draft"}
    # MOC 首页
    moc = (book_dir / "凡人修仙传.md").read_text(encoding="utf-8")
    assert "# 凡人修仙传" in moc
    assert "[[目录/大纲|大纲]]" in moc
    assert "[[章节/第001章|第1章]]" in moc
    assert "[[韩立]]" in moc
    # 层级文件带 frontmatter
    outline = (book_dir / "目录" / "大纲.md").read_text(encoding="utf-8")
    assert outline.startswith("---\n")
    assert "layer: outline" in outline and "version: 1" in outline
    # 章节切分 + 正文互链
    ch1 = (book_dir / "章节" / "第001章.md").read_text(encoding="utf-8")
    assert "chapter: 1" in ch1
    assert "[[韩立]]" in ch1 and "[[林凡儿]]" in ch1 and "[[掌天瓶]]" in ch1
    # 实体页 frontmatter + 出现章节互链
    ent = (book_dir / "实体" / "角色" / "韩立.md").read_text(encoding="utf-8")
    assert "type: character" in ent
    assert "appearances: [1]" in ent
    assert "[[章节/第001章|第1章]]" in ent
    fo = (book_dir / "实体" / "伏笔" / "掌天瓶的来历.md").read_text(encoding="utf-8")
    assert "status: planted" in fo


def test_export_vault_links_to_chapter_entities_may_not_exist(seeded, tmp_path):
    """章节正文出现但未入库的名字不生成死链——linkify 只用已入库实体名。"""
    ps, es, pid = seeded
    out_root = tmp_path / "vault"
    export_vault(ps, es, pid, out_root)
    ch2 = (out_root / "凡人修仙传" / "章节" / "第002章.md").read_text(encoding="utf-8")
    assert "[[七玄门]]" not in ch2  # 七玄门未入库


def test_export_vault_empty_project(tmp_path):
    ps = ProjectStore(tmp_path / "projects")
    pid = ps.create("空书").id
    es = EntityStore(tmp_path / "projects" / pid / "entities.json")
    result = export_vault(ps, es, pid, tmp_path / "vault")
    assert result.chapters == 0 and result.entities == 0
    assert result.layers_exported == []
    assert "（实体库为空" in (tmp_path / "vault" / "空书" / "空书.md").read_text(encoding="utf-8")


def test_export_vault_falls_back_to_plot_when_no_draft(tmp_path):
    ps = ProjectStore(tmp_path / "projects")
    pid = ps.create("剧情书").id
    ps.adopt(pid, "plot", [], "第1章 开端\n剧情一。")
    es = EntityStore(tmp_path / "projects" / pid / "entities.json")
    result = export_vault(ps, es, pid, tmp_path / "vault")
    assert result.chapters == 1
    assert any("剧情层" in w for w in result.warnings)


def test_export_vault_unsafe_names_sanitized(tmp_path):
    ps = ProjectStore(tmp_path / "projects")
    pid = ps.create('书名:带/非法*字符').id
    es = EntityStore(tmp_path / "projects" / pid / "entities.json")
    es.add("item", "剑:之/名", "带非法字符")
    result = export_vault(ps, es, pid, tmp_path / "vault")
    assert result.entities == 1
    files = [str(p) for p in (tmp_path / "vault").rglob("*.md")]
    assert any("剑_之_名" in f for f in files)


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    from app.projects import ProjectStore as PS
    monkeypatch.setattr("app.api.main._project_store", PS(tmp_path / "projects"))
    stores: dict[str, EntityStore] = {}

    def _entities_for(pid: str) -> EntityStore:
        if pid not in stores:
            stores[pid] = EntityStore(tmp_path / "entities" / f"{pid}.json")
        return stores[pid]

    monkeypatch.setattr("app.api.main._entities_for", _entities_for)
    from app.api.main import app
    return TestClient(app)


def test_api_export_vault(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"title": "导出书", "intro": "简介"}).json()["id"]
    client.post(f"/api/projects/{pid}/layers/outline",
                json={"x_pairs": [["第1章", ""]], "y": "第1章 开端\n韩立入门。", "note": ""})
    client.post(f"/api/projects/{pid}/entities",
                json={"type": "character", "name": "韩立", "summary": "主角"})
    out_root = tmp_path / "api-vault"
    r = client.post(f"/api/projects/{pid}/export/vault", json={"out_dir": str(out_root)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] >= 3 and body["entities"] == 1
    assert (out_root / "导出书" / "导出书.md").exists()
    r = client.post(f"/api/projects/nonexistent/export/vault", json={})
    assert r.status_code == 404
