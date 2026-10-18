"""M2 逐项目标审稿 + 必现标记测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.context import MaterialAssembler, MaterialFamily
from app.pipeline import PromptStore
from app.review import (
    MarkerStore,
    extract_markers,
    find_cliche_horror_hits,
    markers_material,
    review_chapter,
)
from app.providers.base import LLMResponse, Provider, Usage


# ---- 审稿器 ----

_ALL9 = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场",
         "章末承接", "跨章联结", "信息揭露节奏", "克苏鲁白描"]


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


def _items(all_stable: bool = True) -> list[tuple[str, str, str]]:
    return [(n, "稳" if all_stable else "不稳", "" if all_stable else f"{n}有问题") for n in _ALL9]


def test_review_all_stable_passes():
    p = JsonProvider([_review_json(_items(True), "可以交稿")])
    r = review_chapter(p, PromptStore(), "大纲", "正文")
    assert r.verdict == "pass" and len(r.items) == 9
    assert r.summary == "可以交稿"
    assert r.cliche_hits == []


def test_review_one_unstable_still_passes():
    items = _items(True)
    items[2] = ("情绪关系", "不稳", "转折无铺垫")
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    assert r.verdict == "pass" and len(r.unstable_items) == 1


def test_review_two_unstable_blocks():
    items = _items(True)
    items[0] = ("剧情逻辑", "不稳", "因果断裂")
    items[5] = ("章末承接", "不稳", "结尾平淡")
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    assert r.verdict == "blocked"


def test_review_missing_item_counts_as_unstable():
    # 模型只回了 7 项：缺两项必须判不稳 → blocked
    items = _items(True)[:7]
    r = review_chapter(JsonProvider([_review_json(items)]), PromptStore(), "o", "p")
    missing_names = {i.name for i in r.items if i.status == "不稳"}
    assert "信息揭露节奏" in missing_names
    assert "克苏鲁白描" in missing_names
    assert r.verdict == "blocked"


def test_review_repairs_truncated_json():
    good = _review_json(_items(True))
    # 半截字符串 → 词法无法修复为合法 JSON → complete_structured 会重试第二次，拿到 good 即通过
    truncated = '{"items": [{"name": "剧情逻辑", "status": "稳", "problem'  # 明显不闭合
    r = review_chapter(JsonProvider([truncated, good]), PromptStore(), "o", "p")
    assert r.verdict == "pass" and r.attempts == 2


def test_review_uses_store_instruction_and_review_system():
    p = JsonProvider([_review_json(_items(True))])
    review_chapter(p, PromptStore(), "本章大纲文", "正文文")
    assert "终审编辑" in p.calls[0][0]["content"]
    assert "九项清单" in p.calls[0][0]["content"]
    assert "克苏鲁白描" in p.calls[0][0]["content"]
    assert "本章大纲文" in p.calls[0][1]["content"]


def test_review_prev_tail_injected():
    """prev_tail 给定时作为第七项对照材料注入；不给时不硬造。"""
    p = JsonProvider([_review_json(_items(True))])
    review_chapter(p, PromptStore(), "o", "p", prev_tail="上一章结尾五百字。")
    user = p.calls[0][1]["content"]
    assert "上一章结尾五百字" in user
    assert user.rindex("【上一章结尾】") < user.rindex("【白描锁命中清单】") < user.rindex("【待审正文】")

    p2 = JsonProvider([_review_json(_items(True))])
    review_chapter(p2, PromptStore(), "o", "p", prev_tail="   ")
    # 空 prev_tail 不应该插入【上一章结尾】块；user 里的 REVIEW_SYSTEM 本身不含该标签
    assert "上一章结尾" not in p2.calls[0][1]["content"] or "上一章结尾五百字" not in p2.calls[0][1]["content"]


def test_review_store_layer_editable():
    store = PromptStore()
    store.set_override("review", "审查重点改为动作戏", "测试")
    assert "动作戏" in store.effective("review")
    store.reset("review")


# ---- 克苏鲁白描锁 ----

def test_find_cliche_horror_hits_basic():
    prose = "走廊尽头传来不可名状的低语，他只觉毛骨悚然，san值狂掉。"
    hits = find_cliche_horror_hits(prose)
    words = [h["word"] for h in hits]
    assert words == ["不可名状", "毛骨悚然", "san值狂掉"]
    for h in hits:
        assert "context" in h and h["context"].startswith("…") and h["context"].endswith("…")


def test_find_cliche_horror_hits_clean():
    assert find_cliche_horror_hits("海风咸湿，指缝结了半透明的盐晶，他数着自己的心跳，每秒一次。") == []
    assert find_cliche_horror_hits("") == []


def test_review_cliche_lock_forces_unstable():
    """白描锁命中 → 即使 LLM 九项全判稳，第九项「克苏鲁白描」也必须被强制覆写为不稳，
    并且 cliche_hits 要在结果里带出来。"""
    prose = "他推开木门，一股令人窒息的恐惧扑面而来。"
    p = JsonProvider([_review_json(_items(True))])
    r = review_chapter(p, PromptStore(), "o", prose)
    coc = [i for i in r.items if i.name == "克苏鲁白描"][0]
    assert coc.status == "不稳"
    assert "令人窒息的恐惧" in coc.problem
    assert any(h["word"] == "令人窒息的恐惧" for h in r.cliche_hits)
    # 只命中 1 处白描 → 其他 8 项稳 → 总共 1 项不稳 → 仍 pass（阈值是 2）
    assert r.verdict == "pass"
    # 白描锁命中清单必须注入 user prompt，强制模型按此清单判
    user_msg = p.calls[0][1]["content"]
    assert "【白描锁命中清单（强制不稳项" in user_msg
    assert "令人窒息的恐惧" in user_msg


def test_review_cliche_lock_two_hits_blocks():
    """两处白描锁命中 + 无其他不稳 → 仍只有 1 个 item 不稳（第九项），verdict=pass；
    但若再加一项其他不稳，就 blocked。"""
    prose = "不可名状的黑影让他倒吸一口凉气，毛骨悚然。"
    items = _items(True)
    items[3] = ("身体信息", "不稳", "手部异化前后矛盾")  # 身体信息也不稳
    p = JsonProvider([_review_json(items)])
    r = review_chapter(p, PromptStore(), "o", prose)
    assert r.verdict == "blocked"
    assert len(r.cliche_hits) >= 2


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
            items = ",".join(f'{{"name": "{n}", "status": "稳", "problem": ""}}' for n in _ALL9)
            return LLMResponse(text=f'{{"items": [{items}], "summary": "ok"}}',
                               usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter())
    r = client.post("/api/review/chapter",
                    json={"prose": "正文", "outline": "大纲", "prev_tail": "上一章结尾。"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "pass" and body["unstable_count"] == 0
    assert len(body["items"]) == 9 and body["items"][-1]["name"] == "克苏鲁白描"
    assert "cliche_hits" in body


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
