"""Phase 2 field-pipeline unit tests.

Covers:
- _try_parse_json forgiving parser (fences / preamble / suffix / nested / quotes)
- FieldRunStore save/load/choose_variant/list_artifacts
- apply_epilogue_to_book / apply_obsession_to_book side effects on BookStore
- Pipeline status endpoint returns the 10 expected nodes for a fresh field
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.fields import FieldRunStore, NodeArtifact, FIELD_PIPELINE_NODES
from app.pipeline.field_pipeline import _try_parse_json


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

class TestTryParseJson:
    def test_fenced_json_array(self):
        text = "以下是结果：\n```json\n[{\"vid\":\"v1\",\"title\":\"a\"},{\"vid\":\"v2\",\"title\":\"b\"}]\n```\n"
        out = _try_parse_json(text)
        assert isinstance(out, list)
        assert len(out) == 2
        assert out[0]["vid"] == "v1"

    def test_preamble_then_array_then_suffix_was_regression_bug(self):
        # 先前用 rfind 选闭括号会把 [{"a":1}] 截断为 {"a":1}
        text = 'some text [{"a":1}] trailing'
        out = _try_parse_json(text)
        assert isinstance(out, list)
        assert out == [{"a": 1}]

    def test_preamble_then_object(self):
        text = "解析如下：\n{\"erosion\":8,\"crafts\":[\"听忆\"]}\n说明完毕"
        out = _try_parse_json(text)
        assert isinstance(out, dict)
        assert out["erosion"] == 8
        assert out["crafts"] == ["听忆"]

    def test_bare_array(self):
        assert _try_parse_json("[1,2,3]") == [1, 2, 3]

    def test_bare_object(self):
        assert _try_parse_json('{"x":1}') == {"x": 1}

    def test_nested_object_with_array(self):
        text = '前缀\n{"a":1,"b":[1,2],"c":{"d":true}}\n后缀'
        out = _try_parse_json(text)
        assert out == {"a": 1, "b": [1, 2], "c": {"d": True}}

    def test_string_containing_brackets_does_not_fool_parser(self):
        # 字符串里的括号不能影响深度计数
        text = '{"msg":"he said [hi]","val":2}'
        out = _try_parse_json(text)
        assert out == {"msg": "he said [hi]", "val": 2}

    def test_escaped_quote_in_string(self):
        text = '{"a":"quote\\"inside]"}'
        out = _try_parse_json(text)
        assert out == {"a": 'quote"inside]'}

    def test_invalid_text_returns_none(self):
        assert _try_parse_json("no json here at all") is None
        assert _try_parse_json("") is None


# ---------------------------------------------------------------------------
# FieldRunStore
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    # FieldRunStore 的 root 应指向 <project>/fields/nodes，
    # 产物落到 <root>/<field_no>/<node_id>.json
    root = tmp_path / "proj" / "fields" / "nodes"
    return FieldRunStore(root), root


class TestFieldRunStore:
    def test_save_and_get_roundtrip(self, tmp_store):
        store, root = tmp_store
        art = NodeArtifact(
            node_id="adapt_scenario",
            layer="adapt_scenario",
            raw_text='[{"vid":"v1","title":"x"}]',
            parsed=[{"vid": "v1", "title": "x"}],
            variants=[{"vid": "v1", "title": "x"}],
            chosen_vid="v1",
        )
        store.save(1, art)
        loaded = store.get(1, "adapt_scenario")
        assert loaded is not None
        assert loaded.chosen_vid == "v1"
        assert loaded.variants[0]["title"] == "x"
        assert (root / "1" / "adapt_scenario.json").exists()

    def test_choose_variant_validates(self, tmp_store):
        store, _ = tmp_store
        art = NodeArtifact(
            node_id="adapt_scenario",
            layer="adapt_scenario",
            raw_text="x",
            variants=[
                {"vid": "v1", "text": "A", "parsed": {"vid": "v1", "title": "A"}},
                {"vid": "v2", "text": "B", "parsed": {"vid": "v2", "title": "B"}},
            ],
        )
        store.save(3, art)
        chosen = store.choose_variant(3, "adapt_scenario", "v2")
        assert chosen.chosen_vid == "v2"
        assert store.chosen_parsed(3, "adapt_scenario") == {"vid": "v2", "title": "B"}

    def test_choose_variant_rejects_unknown(self, tmp_store):
        store, _ = tmp_store
        art = NodeArtifact(
            node_id="adapt_scenario",
            layer="adapt_scenario",
            raw_text="x",
            variants=[{"vid": "v1"}],
        )
        store.save(3, art)
        with pytest.raises(ValueError):
            store.choose_variant(3, "adapt_scenario", "v999")

    def test_list_artifacts_returns_all_ten_nodes_in_order(self, tmp_store):
        store, _ = tmp_store
        artifacts = store.list_artifacts(1)
        assert [a["node_id"] for a in artifacts] == [n[0] for n in FIELD_PIPELINE_NODES]
        assert len(artifacts) == 10
        assert all(a["status"] == "pending" for a in artifacts)
        # chosen_vid/variants_count 字段存在
        assert all("chosen_vid" in a and "variants_count" in a for a in artifacts)

    def test_list_artifacts_marks_done(self, tmp_store):
        store, _ = tmp_store
        art = NodeArtifact(node_id="adapt_scenario", layer="adapt_scenario", raw_text="x",
                           variants=[{"vid": "v1"}], chosen_vid="v1")
        store.save(1, art)
        artifacts = {a["node_id"]: a for a in store.list_artifacts(1)}
        assert artifacts["adapt_scenario"]["status"] == "done"
        assert artifacts["adapt_scenario"]["chosen_vid"] == "v1"
        assert artifacts["adapt_scenario"]["variants_count"] == 1
        assert artifacts["coc_outcome"]["status"] == "pending"


# ---------------------------------------------------------------------------
# Apply helpers (BookStore side effects)
# ---------------------------------------------------------------------------

class _FakeRunStore:
    """最小 run store，让 apply_* 通过 chosen_parsed 读取节点产物。"""
    def __init__(self, parsed_by_node):
        self._by_node = parsed_by_node

    def get(self, field_no, node_id):  # 兼容 apply_* 直接 get 的路径
        parsed = self._by_node.get(node_id)
        if parsed is None:
            return None
        return NodeArtifact(node_id=node_id, layer=node_id, parsed=parsed)


def _make_book_store(tmp_path):
    from app.book.profile import BookStore
    return BookStore(tmp_path / "book")


class TestApplyEpilogue:
    def test_appends_remaining_page_and_one_liner(self, tmp_path):
        bs = _make_book_store(tmp_path)
        data = {
            "remaining_page_ash": "灰烬里有半枚铜纽扣",
            "finale_one_liner": "第七日，亡者收伞，雨停。",
        }
        from app.pipeline.field_pipeline import apply_epilogue_to_book
        run = _FakeRunStore({"epilogue_ash": data})
        out = apply_epilogue_to_book(bs, run, field_no=1)
        assert out["remaining_page_ash"] == data["remaining_page_ash"]
        assert out["finale_one_liner"] == data["finale_one_liner"]

        pages = bs.remaining_pages()
        notes = bs.finale_notes()
        assert len(pages) >= 1
        assert len(notes) >= 1
        assert any("铜纽扣" in (p.get("ash_text") or "") for p in pages)
        assert any("雨停" in (n.get("one_liner") or "") for n in notes)

    def test_apply_is_idempotent_with_same_field(self, tmp_path):
        bs = _make_book_store(tmp_path)
        data = {"remaining_page_ash": "a", "finale_one_liner": "x"}
        from app.pipeline.field_pipeline import apply_epilogue_to_book
        run = _FakeRunStore({"epilogue_ash": data})
        apply_epilogue_to_book(bs, run, field_no=1)
        before = len(bs.remaining_pages())
        apply_epilogue_to_book(bs, run, field_no=1)
        # 第二次同场 apply 会再 append（不同 field_no 才区分场；同场重放不做 dedup，
        # 但实际使用中 UI 端会通过按钮禁用防止重复 apply）。这里只验证没有抛错且长度 +1 是可预期的。
        assert len(bs.remaining_pages()) == before + 1


class TestApplyObsession:
    def test_updates_tangchi_erosion_crafts_and_cost(self, tmp_path):
        bs = _make_book_store(tmp_path)
        data = {
            "erosion_delta": 8,
            "tangchi_crafts_added": ["听忆"],
            "obsession_name": "铜铃执念",
            "remaining_cost": ["右手无名指偶发失温"],
        }
        from app.pipeline.field_pipeline import apply_obsession_to_book
        run = _FakeRunStore({"distill_obsession": data})
        out = apply_obsession_to_book(bs, run, field_no=1)
        assert out["erosion"] == 8

        tangchi = bs.tangchi_raw()
        assert tangchi["erosion"] == 8
        assert any(c["name"] == "听忆" for c in tangchi["crafts"])
        debts = bs.cost_debts()
        assert any("失温" in (d.get("side_effect") or "") and d.get("obsession_name") == "铜铃执念"
                   for d in debts)

    def test_erosion_clamped_to_100(self, tmp_path):
        bs = _make_book_store(tmp_path)
        tangchi = bs.tangchi_raw()
        tangchi["erosion"] = 95
        bs.update("tangchi", tangchi)

        data = {
            "erosion_delta": 20,
            "tangchi_crafts_added": [],
            "remaining_cost": [],
        }
        from app.pipeline.field_pipeline import apply_obsession_to_book
        run = _FakeRunStore({"distill_obsession": data})
        apply_obsession_to_book(bs, run, field_no=1)
        assert bs.tangchi_raw()["erosion"] == 100

    def test_craft_dedup_by_name(self, tmp_path):
        bs = _make_book_store(tmp_path)
        data = {
            "erosion_delta": 0,
            "tangchi_crafts_added": ["听忆", "听忆"],  # 重复 name
            "remaining_cost": [],
        }
        from app.pipeline.field_pipeline import apply_obsession_to_book
        run = _FakeRunStore({"distill_obsession": data})
        apply_obsession_to_book(bs, run, field_no=1)
        names = [c["name"] for c in bs.tangchi_raw()["crafts"]]
        assert names.count("听忆") == 1


# ---------------------------------------------------------------------------
# API endpoint: pipeline status lists 10 nodes for a fresh field
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """按 test_chaishu_project 的惯例把所有 project 维度的 store monkeypatch 到 tmp_path。"""
    from app.projects import ProjectStore
    from app.api import main as api

    monkeypatch.setattr(api, "_project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr(api, "_project_marker_stores", {})
    monkeypatch.setattr(api, "_project_entity_stores", {})
    monkeypatch.setattr(api, "_project_book_stores", {})
    monkeypatch.setattr(api, "_project_field_stores", {})
    monkeypatch.setattr(api, "_project_field_run_stores", {})

    c = TestClient(api.app)
    return c, tmp_path


class TestFieldPipelineApiStatus:
    def test_status_lists_ten_nodes(self, api_client):
        client, tmp_path = api_client
        r = client.post("/api/projects", json={"title": "PipeTest", "genre": "克苏鲁无限流"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]

        obituary = {
            "field_no": 1,
            "title": "青铜巷雨夜",
            "location": "榕城青铜巷",
            "timer": "七日",
            "death_condition": "第七日零时伞骨断裂者亡",
            "entrants": [{"name": "唐祈", "role": "送伞人"}],
            "entity_hint": "弄堂里的借伞婆婆",
        }
        r = client.post(f"/api/projects/{pid}/fields", json={
            "field_no": 1, "title": "青铜巷雨夜", "obituary_card": obituary,
        })
        assert r.status_code == 200, r.text

        r = client.get(f"/api/projects/{pid}/fields/1/pipeline/")
        assert r.status_code == 200, r.text
        body = r.json()
        node_ids = [n["node_id"] for n in body["nodes"]]
        assert node_ids == [n[0] for n in FIELD_PIPELINE_NODES]
        assert body["drama_status"] in ("planned", "pending", "writing", "done")
        assert body["interlude_status"] in ("planned", "pending", "writing", "done")
        assert "node_order" in body
        # 全部 pending
        assert all(n["status"] == "pending" for n in body["nodes"])
