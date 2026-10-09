"""M2 逐项目标审稿 + 必现标记测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.context import MaterialAssembler, MaterialFamily
from app.pipeline import PromptStore
from app.review import (
    MarkerStore,
    extract_markers,
    markers_material,
    review_chapter,
)
from app.providers.base import LLMResponse, Provider, Usage


# ---- 审稿器 ----

def _review_json(items: list[tuple[str, str, str]], summary: str = "总评") -> str:
    return '{"items": [' + ",".join(
        f'{{"name": "{n}", "status": "{s}", "problem": "{p}"}}' for n, s, p in items
    ) + f'], "summary": "{summary}"}}'


class JsonProvider(Provider):
    name = "json-mock"
    default_model = None

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls: list[list[dict]] = []

    def chat_stream(self, messages, model=None, max_tokens=None, extra=None):
        self.calls.append(messages)
        raw = self.outputs.pop(0)
        for ch in [raw[i:i + 5] for i in range(0, len(raw), 5)]:
            yield type("Ev", (), {"kind": "delta", "text": ch, "usage": None, "meta": {}})()
        yield type("Ev", (), {"kind": "usage",
                              "usage": Usage(input_tokens=10, output_tokens=10),
                              "text": "", "meta": {}})()


def _six(all_stable: bool = True) -> list[tuple[str, str, str]]:
    names = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场", "章末承接"]
    return [(n, "稳" if all_stable else "不稳", "" if all_stable else f"{n}有问题") for n in names]


def test_review_all_stable_passes():
    p = JsonProvider([_review_json(_six(True), "可以交稿")])
    r = review_chapter(p, PromptStore(), "大纲", "正文")
    assert r.verdict == "pass" and len(r.items) == 6
    assert r.summary == "可以交稿"


def test_review_one_unstable_still_passes():
    items = _six(True)
    items[2] = ("情绪关系", "不稳", "转折无铺垫")
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    assert r.verdict == "pass" and len(r.unstable_items) == 1


def test_review_two_unstable_blocks():
    items = _six(True)
    items[0] = ("剧情逻辑", "不稳", "因果断裂")
    items[5] = ("章末承接", "不稳", "结尾平淡")
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    assert r.verdict == "blocked"


def test_review_missing_item_counts_as_unstable():
    # 模型只回了 5 项：缺的第 6 项必须判不稳，防静默放水（1 项不稳仍放行）
    items = _six(True)[:5]
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    missing = [i for i in r.items if i.name == "章末承接"][0]
    assert missing.status == "不稳"
    assert r.verdict == "pass"
    # 缺两项 → 两项不稳 → blocked
    r2 = review_chapter(JsonProvider([_review_json(_six(True)[:4])]), PromptStore(), "o", "p")
    assert r2.verdict == "blocked"


def test_review_repairs_truncated_json():
    good = _review_json(_six(True))
    truncated = good[: len(good) // 2]  # 截断（可词法修复：补闭合）
    r = review_chapter(JsonProvider([truncated, good]), PromptStore(), "o", "p")
    assert r.verdict == "pass" and r.attempts == 2


def test_review_uses_store_instruction_and_review_system():
    p = JsonProvider([_review_json(_six(True))])
    review_chapter(p, PromptStore(), "本章大纲文", "正文文")
    assert "终审编辑" in p.calls[0][0]["content"]
    assert "六项清单" in p.calls[0][1]["content"]
    assert "本章大纲文" in p.calls[0][1]["content"]


def test_review_store_layer_editable():
    store = PromptStore()
    store.set_override("review", "审查重点改为动作戏", "测试")
    assert "动作戏" in store.effective("review")
    store.reset("review")


# ---- 必现标记 ----

def test_extract_markers_pattern():
    text = "第3章【第3章必现】主角觉醒金手指。第5章【第5章必现】黑衣人身份揭晓\n【第7章必现】伏笔回收"
    marks = extract_markers(text)
    assert [(m.target_chapter, m.content) for m in marks] == [
        (3, "主角觉醒金手指"), (5, "黑衣人身份揭晓"), (7, "伏笔回收"),
    ]


def test_marker_store_persistence_and_dedup(tmp_path):
    s1 = MarkerStore(tmp_path / "m.json")
    s1.add(3, "金手指觉醒")
    s1.add(3, "金手指觉醒")  # 幂等
    assert len(s1.markers) == 1
    s2 = MarkerStore(tmp_path / "m.json")
    assert len(s2.markers) == 1 and s2.markers[0].status == "pending"


def test_marker_status_and_pending_for(tmp_path):
    s = MarkerStore(tmp_path / "m.json")
    m = s.add(3, "金手指觉醒")
    s.add(3, "黑衣人现身")
    s.set_status(m.id, "done")
    assert len(s.pending_for(3)) == 1
    with pytest.raises(ValueError):
        s.set_status(m.id, "bogus")


def test_markers_material_and_assembly_section(tmp_path):
    s = MarkerStore(tmp_path / "m.json")
    s.add(3, "金手指觉醒")
    mat = markers_material(s.pending_for(3), 3)
    assert mat.family is MaterialFamily.REQUIRED
    assert mat.source_id == "markers:ch3"
    sections = MaterialAssembler(max_bytes=10**7).assemble([mat]).render_sections()
    assert "必现标记" in sections and "金手指觉醒" in sections["必现标记"]
    assert markers_material([], 3) is None


def test_marker_verify_substring(tmp_path):
    s = MarkerStore(tmp_path / "m.json")
    s.add(3, "金手指觉醒")
    s.add(3, "黑衣人现身")
    results = s.verify("他终于完成了金手指觉醒，全场哗然。", 3)
    assert [r["hit"] for r in results] == [True, False]


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._journal_path", lambda rid, project=None: tmp_path / f"{rid}.json")
    monkeypatch.setattr("app.api.main._marker_store", MarkerStore(tmp_path / "markers.json"))
    from app.api.main import app
    return TestClient(app)


def test_api_markers_crud_and_extract(client):
    r = client.post("/api/markers", json={"target_chapter": 3, "content": "金手指觉醒"})
    assert r.status_code == 200 and r.json()["pending_count"] == 1
    r = client.post("/api/markers/extract",
                    json={"text": "【第5章必现】黑衣人身份揭晓"})
    assert r.json()["added"][0]["source"] == "extracted"
    mid = r.json()["markers"][0]["id"]
    r = client.put(f"/api/markers/{mid}/status", json={"status": "done"})
    assert r.json()["markers"][0]["status"] == "done"


def test_api_review_endpoint(client, monkeypatch):
    class FakeRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            assert step == "review"
            names = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场", "章末承接"]
            items = ",".join(f'{{"name": "{n}", "status": "稳", "problem": ""}}' for n in names)
            return LLMResponse(text=f'{{"items": [{items}], "summary": "ok"}}',
                               usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter())
    r = client.post("/api/review/chapter", json={"prose": "正文", "outline": "大纲"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass" and body["unstable_count"] == 0


def test_api_run_injects_pending_markers(client, monkeypatch):
    client.post("/api/markers", json={"target_chapter": 2, "content": "伏笔回收"})

    class FakeRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            return LLMResponse(text="正文", usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter())
    r = client.post("/api/runs/draft", json={
        "x_pairs": [["第2章", ""]], "x_chunk_length": 500, "chapter_no": 2,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["markers_injected"][0]["content"] == "伏笔回收"
