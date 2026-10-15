"""M1 数据地基单测：世界状态热板 + 卡片锁 + 回转寿司候选/编辑态。"""
from __future__ import annotations

import pytest

from app.entities import (
    EntityStore, PROTAGONIST_ID, StateChange, StateDiff, NewEntitySuggestion,
    StateChangeSuggestion, Relation,
)
from app.fields import FieldStore
from app.wizard import DeskStore, Scene
from app.sessions import SessionStore, Candidate, EditingState


# ======================================================================
# EntityStore：StateChange / Relation / Protagonist / StateDiff
# ======================================================================

class TestStateChanges:
    def test_add_change_records_globally_and_on_entity(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        tang = s.add("character", "唐迟", "主角")
        ch = StateChange(
            id="", entity_id=tang.id, attr="san",
            old_value="60", new_value="55",
            cause="目睹尸体", field_no=1, chapter=1,
            source_kind="extracted", confirmed=True,
        )
        s.add_change(ch)
        assert ch.id in s.changes
        assert ch.id in tang.state_log
        assert s.current_attr(tang.id, "san") == "55"
        ch2 = StateChange(
            id="", entity_id=tang.id, attr="san",
            old_value="55", new_value="50",
            cause="噩梦", field_no=1, chapter=1,
            source_kind="extracted", confirmed=True,
        )
        s.add_change(ch2)
        assert len(tang.state_log) == 2
        assert s.current_attr(tang.id, "san") == "50"
        assert len(s.changes_for(tang.id)) == 2

    def test_protagonist_attrs_routed_to_protagonist_object(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        ch = StateChange(
            id="", entity_id=PROTAGONIST_ID, attr="location",
            old_value="", new_value="青山镇茶馆",
            cause="开场", field_no=1, chapter=1,
            source_kind="manual", confirmed=True,
        )
        s.add_change(ch)
        assert s.protagonist.current_location == "青山镇茶馆"
        ch2 = StateChange(
            id="", entity_id=PROTAGONIST_ID, attr="sanity",
            old_value="60", new_value="55",
            cause="惊吓", field_no=1, chapter=1,
            source_kind="extracted", confirmed=True,
        )
        s.add_change(ch2)
        assert s.protagonist.sanity_hint == "55"

    def test_changes_for_entity_only_returns_that_entity(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "A")
        b = s.add("character", "B")
        s.add_change(StateChange(
            id="", entity_id=a.id, attr="status",
            old_value="alive", new_value="missing",
            cause="x", field_no=1, chapter=1, source_kind="manual", confirmed=True))
        s.add_change(StateChange(
            id="", entity_id=b.id, attr="status",
            old_value="alive", new_value="dead",
            cause="y", field_no=1, chapter=1, source_kind="manual", confirmed=True))
        assert len(s.changes_for(a.id)) == 1
        assert s.changes_for(a.id)[0].new_value == "missing"

    def test_neighbors_follows_relation_edges(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "唐迟")
        b = s.add("character", "老陈")
        rel = Relation(id="", from_id=a.id, to_id=b.id,
                       rel_type="acquaintance", label="茶客")
        s.add_relation(rel)
        nbs = s.neighbors(a.id)
        assert len(nbs) == 1
        r, other = nbs[0]
        assert other.id == b.id
        assert r.rel_type == "acquaintance"
        assert len(s.neighbors(b.id)) == 1

    def test_deactivate_relation_marks_inactive(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "A")
        b = s.add("item", "X")
        rel = Relation(id="", from_id=a.id, to_id=b.id, rel_type="holds")
        s.add_relation(rel)
        assert rel.active is True
        s.deactivate_relation(rel.id, end_reason="被夺走")
        found = next(r for r in s.relations if r.id == rel.id)
        assert found.active is False
        assert found.end_reason == "被夺走"


class TestStateDiff:
    def test_apply_diff_creates_new_entity_and_state_change(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        tang = s.add("character", "唐迟", "主角")
        diff = StateDiff(
            id="d1", source="manual", source_msg_id="m1",
            field_no=1, chapter=1,
            new_entities=[NewEntitySuggestion(
                type="character", name="王掌柜",
                summary="茶馆老板", details="警惕",
            )],
            state_changes=[StateChangeSuggestion(
                entity_ref=tang.id, attr="location",
                old_value="", new_value="青山镇茶馆",
                cause="进入茶馆",
            )],
        )
        s.add_pending_diff(diff)
        assert len(s.pending_diffs) == 1
        result = s.apply_diff("d1", {
            "new_entities": {"0": True},
            "state_changes": {"0": True},
        })
        # created 返回的是 as_summary 列表
        assert len(result["created"]) == 1
        boss_summary = result["created"][0]
        assert boss_summary["name"] == "王掌柜"
        boss = s.get(boss_summary["id"])
        assert boss.name == "王掌柜"
        # 唐迟 location 变了
        loc_changes = [c for c in s.changes_for(tang.id) if c.attr == "location"]
        assert loc_changes and loc_changes[-1].new_value == "青山镇茶馆"
        assert len(s.pending_diffs) == 0
        assert len(s.applied_diffs) == 1

    def test_apply_diff_silently_skips_missing_target(self, tmp_path):
        """apply_diff 对找不到的目标 entity 采取 skip（非报错），方便 AI 容错。"""
        s = EntityStore(tmp_path / "e.json")
        diff = StateDiff(
            id="d2", source="manual", source_msg_id="m2",
            field_no=1, chapter=1,
            state_changes=[StateChangeSuggestion(
                entity_ref="not-exist", attr="san",
                old_value="60", new_value="50",
                cause="x",
            )],
        )
        s.add_pending_diff(diff)
        # 不抛异常，只是 changed 为空
        result = s.apply_diff("d2", {"state_changes": {"0": True}})
        assert result["changed"] == []
        # diff 已经 applied（从 pending 移到 applied）
        assert len(s.pending_diffs) == 0

    def test_dismiss_diff_moves_to_applied_with_dismissed_status(self, tmp_path):
        """dismiss 后 diff 进入 applied_diffs 归档（而不是物理删除），便于复盘。"""
        s = EntityStore(tmp_path / "e.json")
        diff = StateDiff(id="d3", source="manual", source_msg_id="m3")
        s.add_pending_diff(diff)
        s.dismiss_diff("d3")
        assert s.pending_diffs == []
        assert len(s.applied_diffs) == 1
        assert s.applied_diffs[0].status == "dismissed"


class TestChapterSnapshot:
    def test_snapshot_excludes_later_changes(self, tmp_path):
        s = EntityStore(tmp_path / "e.json")
        a = s.add("character", "A")
        s.add_change(StateChange(
            id="", entity_id=a.id, attr="status",
            old_value="alive", new_value="injured",
            cause="ch1", field_no=1, chapter=1, source_kind="manual", confirmed=True))
        s.add_change(StateChange(
            id="", entity_id=a.id, attr="status",
            old_value="injured", new_value="dead",
            cause="ch3", field_no=2, chapter=3, source_kind="manual", confirmed=True))
        snap2 = s.chapter_snapshot(2)
        ent2 = next(e for e in snap2["entities"] if e["id"] == a.id)
        assert ent2["state_attrs"]["status"] == "injured"
        snap3 = s.chapter_snapshot(3)
        ent3 = next(e for e in snap3["entities"] if e["id"] == a.id)
        assert ent3["state_attrs"]["status"] == "dead"


# ======================================================================
# FieldStore：CardLock / CardVersion
# ======================================================================

class TestCardLock:
    def test_lock_card_creates_then_versions(self, tmp_path):
        fs = FieldStore(tmp_path)
        fs.create("测试场")
        lock = fs.lock_card(1, "core_twist", "反转 v1", note="初版")
        assert lock.key == "core_twist"
        assert lock.text == "反转 v1"
        assert lock.versions == []
        assert fs.get_card_text(1, "core_twist") == "反转 v1"
        lock2 = fs.lock_card(1, "core_twist", "反转 v1")
        assert lock2.versions == []
        for i in range(7):
            fs.lock_card(1, "core_twist", f"反转 v{i+2}")
        lock3 = fs.lock_card(1, "core_twist", "最终")
        assert len(lock3.versions) == 5
        assert lock3.text == "最终"
        assert fs.get_card_text(1, "core_twist") == "最终"

    def test_set_core_is_special_key(self, tmp_path):
        fs = FieldStore(tmp_path)
        fs.create("测试场")
        fw = fs.set_core(1, "副本核正文")
        assert fw.core == "副本核正文"
        assert fs.get_card_text(1, "core") == "副本核正文"


# ======================================================================
# DeskStore：Scene 场景锁
# ======================================================================

class TestDeskScene:
    def test_ensure_and_lock_scene(self, tmp_path):
        ds = DeskStore(tmp_path / "desk.json")
        sc = ds.ensure_scene(1, 0, summary="开场", emotion="压抑")
        assert isinstance(sc, Scene)
        assert sc.idx == 0
        assert sc.status == "planned"   # 新建场景默认 planned
        locked = ds.lock_scene(1, 0, "唐迟在茶馆醒来。")
        assert locked.status == "locked"
        assert locked.summary == "唐迟在茶馆醒来。"
        assert locked.locked_at
        ds.save_snapshot(1, {"entities": [], "protagonist": {}})
        w = ds.get(1)
        assert w.state_snapshot_at_lock == {"entities": [], "protagonist": {}}


# ======================================================================
# SessionStore：Candidate / EditingState
# ======================================================================

class TestSushiFlow:
    def test_candidates_round_trip(self, tmp_path):
        ss = SessionStore(tmp_path)
        s = ss.create(project_id="p1", scope="field", field_no=1, title="测试")
        cands = [
            Candidate(id="", phase="kindle", card_key="core",
                      content="版本A", axis_label="氛围",
                      axis_dimension="tone", rationale="A"),
            Candidate(id="", phase="kindle", card_key="core",
                      content="版本B", axis_label="氛围",
                      axis_dimension="tone", rationale="B"),
        ]
        saved = ss.add_candidates(s.id, cands)
        assert len(saved) == 2
        assert all(c.id for c in saved)
        s2 = ss.get(s.id)
        assert len(s2.candidates) == 2

    def test_pick_then_edit_then_lock(self, tmp_path):
        ss = SessionStore(tmp_path)
        s = ss.create(project_id="p1", scope="field", field_no=1, title="测试")
        cands = ss.add_candidates(s.id, [
            Candidate(id="", phase="kindle", card_key="core",
                      content="hello", axis_label="x",
                      axis_dimension="tone", rationale="r"),
        ])
        ed = ss.start_editing(s.id, "core", candidate_id=cands[0].id)
        assert ed.card_key == "core"
        assert ed.current_text == "hello"
        assert ed.edit_round == 0
        ss.update_editing_text(s.id, "hello world")
        s2 = ss.get(s.id)
        assert s2.editing.current_text == "hello world"
        ss.bump_edit_round(s.id, suggestion_msg_id="m99")
        assert ss.get(s.id).editing.edit_round == 1
        ss.record_lock(s.id, "core", msg_id="m100")
        s3 = ss.get(s.id)
        assert s3.editing is None
        assert "core" in s3.locked_cards


# ======================================================================
# API 层新端点：通过 TestClient 走一遍主流程
# （用 tmp_path 替换 REPO，让所有 store 落在临时目录）
# ======================================================================

@pytest.fixture
def client(tmp_path, monkeypatch):
    # 让 config.REPO 指到 tmp_path/novel-studio/，这样后端会把项目数据落到临时目录
    repo = tmp_path / "novel-studio"
    (repo / "backend" / "data").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.config.REPO", repo)
    # 再以新 REPO 为根重新初始化模块级 store 工厂缓存
    import app.api.main as m
    # 重置所有模块级字典缓存
    m._project_entity_stores.clear()
    m._project_field_stores.clear()
    m._project_book_stores.clear()
    m._project_field_run_stores.clear()
    m._project_coc_run_stores.clear()
    m._project_session_stores.clear()
    m._wizard_stores.clear()
    m._desk_stores.clear()
    m._project_marker_stores.clear()
    # 重置 project_store 指向临时目录
    from app.projects import ProjectStore
    m._project_store = ProjectStore(repo / "backend" / "data" / "projects")
    # 同时重置 prompt/marker 全局 store 路径
    m._prompt_store = __import__("app.pipeline", fromlist=["PromptStore"]).PromptStore(
        repo / "backend" / "data" / "prompt_store.json")
    m._marker_store = __import__("app.review", fromlist=["MarkerStore"]).MarkerStore(
        repo / "backend" / "data" / "markers.json")

    # 假 LLM：根据 messages 里出现的关键词返回对应 JSON；M2 真接入后我们用桩
    import json as _json
    class _FakeResp:
        def __init__(self, text):
            self.text = text
            self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
    class _FakeP:
        name = "fake"
        default_model = None
        @staticmethod
        def chat(messages, model=None, max_tokens=None, extra=None):
            content = messages[-1]["content"] if messages else ""
            if "差异化候选卡" in content or "产出" in content and ("副本核" in content or "讣告" in content or "实体卡" in content):
                # generate candidates：返回 3 个 JSON 候选
                data = [
                    {"axis_value": "阴森民俗", "rationale": "阴冷开头",
                     "content": "青铜巷下了七天雨，三盏白纸灯笼挂在巷口老槐上……"},
                    {"axis_value": "黑色荒诞", "rationale": "日常错位",
                     "content": "周一早上去公司，前台递来一封信，信封上写着我的讣告……"},
                    {"axis_value": "冷硬克制", "rationale": "白描推进",
                     "content": "我是在第三天发现自己已经死的。冰箱里牛奶还没坏，桌上考勤卡还没签……"},
                ]
                return _FakeResp(_json.dumps(data, ensure_ascii=False))
            if "【当前版本】" in content and "【作者指令】" in content:
                # suggest edit
                return _FakeResp(_json.dumps(
                    {"content": "（AI改后）开头先下一场冷雨，巷口灯笼被风掀起一角。",
                     "reason": "以冷雨和灯笼压氛围"}, ensure_ascii=False))
            return _FakeResp("（桩文本）")
    def _fake_chore(step="chore"):
        return _FakeP()
    monkeypatch.setattr(m, "_chore_provider", _fake_chore)

    from fastapi.testclient import TestClient
    return TestClient(m.app)


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"title": "测试书"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_field(client, pid: str) -> int:
    r = client.post(f"/api/projects/{pid}/fields", json={"title": "测试场"})
    assert r.status_code == 200, r.text
    return r.json()["field"]["no"]


def _make_session(client, pid: str, field_no: int,
                  scope="field", phase="kindle") -> str:
    r = client.post(f"/api/projects/{pid}/sessions", json={
        "scope": scope, "field_no": field_no,
        "title": "测试", "start_phase": phase,
    })
    assert r.status_code == 200, r.text
    return r.json()["session"]["id"]


class TestM1ApiEndpoints:
    def test_state_overview_empty(self, client):
        pid = _make_project(client)
        r = client.get(f"/api/projects/{pid}/state")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "protagonist" in data
        assert data["counts"]["character"] == 0

    def test_generate_candidates_stub(self, client):
        pid = _make_project(client)
        fno = _make_field(client, pid)
        sid = _make_session(client, pid, fno)
        r = client.post(f"/api/projects/{pid}/sessions/{sid}/generate-candidates", json={
            "card_key": "core", "phase": "kindle", "n": 3,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["stub"] is False
        assert len(body["candidates"]) == 3
        # 每个候选都有 axis_value 标签在 rationale 里
        for c in body["candidates"]:
            assert c["content"]
            assert "[" in c["rationale"]
        cand_id = body["candidates"][0]["id"]
        pr = client.post(f"/api/projects/{pid}/sessions/{sid}/pick-candidate", json={
            "candidate_id": cand_id, "card_key": "core",
        })
        assert pr.status_code == 200, pr.text
        assert pr.json()["editing"]["card_key"] == "core"
        assert pr.json()["editing"]["current_text"]
        ur = client.post(f"/api/projects/{pid}/sessions/{sid}/editing/update", json={
            "text": "我改过的副本核",
        })
        assert ur.status_code == 200
        assert ur.json()["session"]["editing"]["current_text"] == "我改过的副本核"
        # AI 改稿建议
        ar = client.post(f"/api/projects/{pid}/sessions/{sid}/editing/ai-suggest", json={
            "instruction": "开头更冷", "apply": True,
        })
        assert ar.status_code == 200, ar.text
        assert ar.json()["ok"] is True
        assert ar.json()["applied"] is True
        assert ar.json()["suggested_text"]

    def test_lock_writes_to_field_store(self, client):
        pid = _make_project(client)
        fno = _make_field(client, pid)
        sid = _make_session(client, pid, fno, phase="kindle")
        r = client.post(f"/api/projects/{pid}/sessions/{sid}/lock", json={
            "card_key": "core", "text": "副本核终稿",
            "scope": "field", "field_no": fno,
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        r2 = client.post(f"/api/projects/{pid}/sessions/{sid}/lock", json={
            "card_key": "core_twist", "text": "核心反转终稿",
            "field_no": fno,
        })
        assert r2.status_code == 200, r2.text
        lock_info = r2.json()["locked_ref"]["lock"]
        assert lock_info["text"] == "核心反转终稿"
        s_get = client.get(f"/api/projects/{pid}/sessions/{sid}").json()
        assert "core" in s_get["session"]["locked_cards"]
        assert "core_twist" in s_get["session"]["locked_cards"]

    def test_pending_and_apply_diff_end_to_end(self, client):
        pid = _make_project(client)
        # 加唐迟
        r_add = client.post(f"/api/projects/{pid}/entities",
                            json={"type": "character", "name": "唐迟", "summary": "主角"})
        assert r_add.status_code == 200, r_add.text
        tang_id = r_add.json()["id"]
        # 直接通过 EntityStore 注入一个 pending diff
        from app import config
        from app.entities import EntityStore
        es_path = config.REPO / "backend" / "data" / "projects" / pid / "entities.json"
        es = EntityStore(es_path)
        diff = StateDiff(
            id="dx", source="manual", source_msg_id="m1",
            field_no=1, chapter=1,
            new_entities=[NewEntitySuggestion(
                type="character", name="老李", summary="打更人",
            )],
            state_changes=[StateChangeSuggestion(
                entity_ref=tang_id, attr="location",
                old_value="", new_value="土地庙",
                cause="到达",
            )],
        )
        es.add_pending_diff(diff)
        # 清 main 里的 EntityStore 缓存，让下次 _entities_for 重新读盘
        import app.api.main as m
        m._project_entity_stores.pop(pid, None)
        pr = client.get(f"/api/projects/{pid}/state/pending")
        assert pr.status_code == 200
        assert pr.json()["count"] == 1
        ar = client.post(f"/api/projects/{pid}/state/apply-diff/dx", json={
            "selections": {"new_entities": {"0": True},
                           "state_changes": {"0": True}},
        })
        assert ar.status_code == 200, ar.text
        m._project_entity_stores.pop(pid, None)
        es2 = EntityStore(es_path)
        assert es2.find("character", "老李") is not None
        pr2 = client.get(f"/api/projects/{pid}/state/pending")
        assert pr2.json()["count"] == 0

    def test_snapshot_includes_protagonist(self, client):
        pid = _make_project(client)
        r = client.get(f"/api/projects/{pid}/state/snapshot")
        assert r.status_code == 200
        assert "protagonist" in r.json()
