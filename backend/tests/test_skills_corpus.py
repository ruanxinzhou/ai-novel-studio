"""T10 语料检索 + 写作模块注入测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.context import Material, MaterialAssembler, MaterialFamily
from app.corpus import list_tags, list_types, search
from app.skills import (
    MODULES,
    available_modules,
    module_rules,
    resolve_for_layer,
    skill_materials,
)


# ---- 语料检索 ----

def test_corpus_loaded_and_nonempty():
    assert len(list_types()) > 0
    assert sum(c for _, c in list_types()) > 100
    assert list_tags()


def test_corpus_search_by_type():
    hits = search(excerpt_type="开头钩子", limit=5)
    assert 0 < len(hits) <= 5
    assert all("开头钩子" in h.excerpt_type for h in hits)


def test_corpus_search_by_tag_and_keyword():
    tag_hits = search(tag="人设亮相", limit=5)
    assert tag_hits and all("人设亮相" in h.tags for h in tag_hits)
    kw_hits = search(keyword="系统", limit=5)
    assert kw_hits
    none = search(excerpt_type="不存在的类型")
    assert none == []


def test_excerpt_to_material_fields():
    hit = search(limit=1)[0]
    d = hit.to_material_dict()
    m = Material(source_id=d["source_id"], family=MaterialFamily.REFERENCE,
                 content=d["content"], title=d["title"], keywords=d["keywords"])
    assert m.source_id.startswith("corpus:")
    assert m.byte_size > 0


# ---- 写作模块 ----

def test_modules_all_have_rules():
    for name in MODULES:
        rules = module_rules(name)
        assert rules.strip(), f"{name} 规则为空"
        assert "runtime" not in rules[:20] or rules  # 文件可读


def test_unknown_module_rejected():
    with pytest.raises(ValueError):
        module_rules("no_such_module")
    with pytest.raises(ValueError):
        resolve_for_layer("draft", ["no_such_module"])


def test_layer_defaults():
    assert resolve_for_layer("draft", None) == ["anti_ai_voice"]
    assert resolve_for_layer("polish", None) == ["anti_ai_voice"]
    assert resolve_for_layer("outline", None) == ["volume_outline"]
    assert resolve_for_layer("plot", None) == ["plot_logic"]
    assert resolve_for_layer("intro", None) == []
    explicit = resolve_for_layer("draft", ["dialogue", "chapter_ending"])
    assert explicit == ["dialogue", "chapter_ending"]  # 显式请求覆盖默认


def test_skill_materials_are_required_with_prefix():
    mats = skill_materials("draft", None)
    assert [m.source_id for m in mats] == ["skill:anti_ai_voice"]
    assert all(m.family is MaterialFamily.REQUIRED for m in mats)


# ---- 装配分区渲染 ----

def test_skill_materials_render_into_dedicated_section():
    assembler = MaterialAssembler(max_bytes=10**7)
    mats = [
        *skill_materials("draft", None),
        Material(source_id="doc", family=MaterialFamily.REQUIRED, content="作者资料内容"),
        Material(source_id="corpus:X", family=MaterialFamily.REFERENCE, content="范文片段"),
    ]
    sections = assembler.assemble(mats).render_sections()
    assert "写作模块" in sections
    assert "写作模块[anti_ai_voice]" in sections["写作模块"]
    assert "作者资料内容" not in sections["写作模块"]
    assert "作者资料内容" in sections["必需材料"]
    assert "范文片段" in sections["参考资料"]


def test_module_rules_bloat_causes_explicit_capacity_error():
    # 规则是 REQUIRED：装不下必须显式失败，绝不静默截断
    tiny = MaterialAssembler(max_bytes=500)
    with pytest.raises(Exception, match="必需材料超出预算"):
        tiny.assemble(skill_materials("draft", None))


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    from app.api.main import app
    return TestClient(app)


def test_api_skills_endpoints(client):
    r = client.get("/api/skills/modules")
    assert r.status_code == 200
    names = [m["name"] for m in r.json()["modules"]]
    assert "anti_ai_voice" in names and "consistency_review" in names
    r2 = client.get("/api/skills/modules/anti_ai_voice")
    assert r2.status_code == 200 and "去 AI 味" in r2.json()["title"]
    assert client.get("/api/skills/modules/nope").status_code == 404


def test_api_corpus_endpoints(client):
    assert client.get("/api/corpus/types").status_code == 200
    assert client.get("/api/corpus/tags").status_code == 200
    r = client.get("/api/corpus/search", params={"excerpt_type": "开头钩子", "limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert 0 < body["total"] <= 2
    assert body["excerpts"][0]["excerpt_id"].startswith("A")


def test_run_injects_default_module_and_corpus(client, monkeypatch):
    calls: list[list[dict]] = []

    class FakeResp:
        text = "正文。"
        usage = type("U", (), {"output_tokens": 10})()

    class FakeRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            calls.append(messages)
            return FakeResp()

    monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter())
    body = {
        "x_pairs": [["第一场戏：主角进赌坊", ""]],
        "x_chunk_length": 500,
        "corpus_query": {"excerpt_type": "开头钩子", "limit": 2},
    }
    r = client.post("/api/runs/draft", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["skills_injected"] == ["skill:anti_ai_voice"]
    assert len(data["corpus_injected"]) == 2
    user_msg = calls[0][1]["content"]
    assert "【写作模块】" in user_msg
    assert "【参考资料】" in user_msg


def test_run_rejects_unknown_module(client):
    r = client.post("/api/runs/draft", json={"x_pairs": [["x", ""]], "modules": ["nope"]})
    assert r.status_code == 422
