"""项目状态账本（续写大纲注入）测试：已写剧情 / 正文近况 / 实体账本三材料 + API 注入。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.context import MaterialFamily
from app.context.ledger import (
    ENTITY_SECTION_LIMITS,
    FORESHADOW_LISTED,
    PLOT_DIGEST_MAX_CHAPTERS,
    build_state_materials,
)
from app.entities import EntityStore
from app.projects import ProjectStore
from app.providers.base import LLMResponse, Usage
from app.wizard import DeskStore


# ---- 构建器单测 ----

@pytest.fixture
def env(tmp_path):
    project = ProjectStore(tmp_path / "projects")
    pid = project.create("测试书", "").id
    desk = DeskStore(tmp_path / "desk.json")
    store = EntityStore(tmp_path / "entities.json")
    return project, pid, desk, store


def test_empty_project_returns_no_materials(env):
    project, pid, desk, store = env
    assert build_state_materials(project, store, desk, pid) == []


def test_three_materials_from_written_state(env):
    project, pid, desk, store = env
    desk.record(1, "plot", "陆离觉醒作死系统。", title="迟来的系统")
    desk.record(1, "draft", "陆离睁眼，面板浮现。")
    desk.record(2, "plot", "床下的布偶归位。")
    desk.record(2, "draft", "布偶睁眼，陆离后退一步。")
    c = store.add("character", "陆离", summary="主角，剪辑师", chapter=1)
    store.record_appearance(c.id, 2)
    w = store.add("character", "老王", summary="看门大爷")
    store.update(w.id, status="retired")
    store.add("location", "诡异片场", summary="首个副本", chapter=1)
    store.add("item", "作死系统", summary="金手指", chapter=1)
    store.add("plotline", "影视世界巡礼", summary="主线", chapter=1)
    store.add("foreshadow", "床下的朋友", summary="布偶诡异归位", chapter=2)
    f = store.add("foreshadow", "面板暗纹", summary="属性面板异常", chapter=1)
    store.update(f.id, status="paid")

    mats = build_state_materials(project, store, desk, pid)
    by_id = {m.source_id: m for m in mats}
    assert set(by_id) == {"state:plot-digest", "state:draft-tail", "state:entities"}

    digest = by_id["state:plot-digest"]
    assert digest.family == MaterialFamily.FINALIZED
    assert "第1章 迟来的系统：陆离觉醒作死系统。" in digest.content
    assert "第2章：床下的布偶归位。" in digest.content

    tail = by_id["state:draft-tail"]
    assert "第2章" in tail.content and "布偶睁眼" in tail.content

    ledger = by_id["state:entities"]
    assert ledger.family == MaterialFamily.REQUIRED
    for frag in ("【角色】", "陆离：主角，剪辑师（第1-2章）", "老王", "〔已退场〕",
                 "【地点】", "【道具】", "【情节线】", "【未回收伏笔】",
                 "床下的朋友：布偶诡异归位（第2章埋，从未推进）", "已回收 1 条"):
        assert frag in ledger.content, frag
    # 已回收的不进未回收清单
    assert "面板暗纹" not in ledger.content


def test_caps_keep_ledger_bounded(env):
    project, pid, desk, store = env
    for i in range(1, 46):  # 45 章剧情，超摘要上限
        desk.record(i, "plot", f"第{i}章剧情梗概。")
    for i in range(ENTITY_SECTION_LIMITS["character"] + 10):
        store.add("character", f"角色{i:02d}", summary="路人", chapter=1)
    for i in range(FORESHADOW_LISTED + 5):
        store.add("foreshadow", f"伏笔{i:02d}", summary="待兑现", chapter=1)

    mats = {m.source_id: m for m in build_state_materials(project, store, desk, pid)}
    digest = mats["state:plot-digest"].content
    assert f"共 45 章" in digest and f"列最近 {PLOT_DIGEST_MAX_CHAPTERS} 章" in digest
    assert "第45章" in digest and "第1章" not in digest  # 留最近段

    ledger = mats["state:entities"].content
    assert f"其余 10 个略" in ledger
    assert f"其余 5 条略" in ledger


def test_draft_layer_fallback_when_no_desk_works(env):
    project, pid, desk, store = env
    project.adopt(pid, "draft", [], "第1章 老书正文\n陆离睁眼。")
    mats = {m.source_id: m for m in build_state_materials(project, store, desk, pid)}
    assert "state:draft-tail" in mats
    assert "正文层结尾" in mats["state:draft-tail"].title
    assert "陆离睁眼" in mats["state:draft-tail"].content


def test_ledger_families_assemble_without_capacity_error(env):
    """REQUIRED 账本必须装得下装配器预算（200KB）。"""
    from app.context import MaterialAssembler

    project, pid, desk, store = env
    for i in range(ENTITY_SECTION_LIMITS["character"] + 5):
        store.add("character", f"角色{i}", summary="梗" * 20, chapter=1)
    mats = build_state_materials(project, store, desk, pid)
    snap = MaterialAssembler(max_bytes=200_000).assemble(mats)
    assert any(m.source_id == "state:entities" for m in snap.included)


# ---- API 注入 ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


def test_run_outline_injects_project_state(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"title": "状态书"}).json()["id"]
    client.post(f"/api/projects/{pid}/layers/outline",
                json={"y": "第1章 觉醒\n主角觉醒。", "note": "已有大纲"})
    desk = DeskStore(tmp_path / "dsk" / f"{pid}.json")
    desk.record(1, "plot", "陆离觉醒作死系统。")
    desk.record(1, "draft", "陆离睁眼，面板浮现。")
    ent = EntityStore(tmp_path / "ent" / f"{pid}.json")
    ent.add("character", "陆离", summary="主角", chapter=1)
    ent.add("foreshadow", "床下的朋友", summary="布偶归位", chapter=1)

    captured: list[list[dict]] = []

    class CapRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            captured.append(messages)
            return LLMResponse(text="第2章 续写：陆离再入诡异片场。",
                               usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: CapRouter())
    r = client.post("/api/runs/outline", json={
        "x_pairs": [["第2章\n", ""]],
        "materials": [{"source_id": "outline:current", "family": "finalized",
                       "content": "第1章 觉醒\n主角觉醒。", "title": "已有大纲"}],
        "project": pid,
        "project_state": True,
        "user_directive": "已有大纲 1 章见材料。本次续写第 2 章。",
        "x_chunk_length": 200,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("实体账本" in t for t in body["state_injected"])
    assert any("已写剧情梗概" in t for t in body["state_injected"])
    assert any("正文近况" in t for t in body["state_injected"])
    prompt = "\n".join(m["content"] for msgs in captured for m in msgs)
    assert "未回收伏笔" in prompt and "床下的朋友" in prompt
    assert "陆离觉醒作死系统" in prompt


def test_run_outline_without_flag_no_state(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"title": "干净书"}).json()["id"]
    desk = DeskStore(tmp_path / "dsk" / f"{pid}.json")
    desk.record(1, "plot", "剧情存在但不该被注入。")

    captured: list[list[dict]] = []

    class CapRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            captured.append(messages)
            return LLMResponse(text="第1章 大纲。", usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: CapRouter())
    r = client.post("/api/runs/outline", json={
        "x_pairs": [["第1章\n", ""]],
        "project": pid,
        "x_chunk_length": 200,
    })
    assert r.status_code == 200, r.text
    assert r.json()["state_injected"] == []
    prompt = "\n".join(m["content"] for msgs in captured for m in msgs)
    assert "不该被注入" not in prompt
