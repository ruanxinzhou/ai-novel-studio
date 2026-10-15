"""Phase 5: 创作会话（session）+ 阶段门控 API 测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app.projects import ProjectStore
    from app.api import main as api

    monkeypatch.setattr(api, "_project_store", ProjectStore(tmp_path / "projects"))
    for k in ["_project_marker_stores", "_project_entity_stores", "_project_book_stores",
              "_project_field_stores", "_project_field_run_stores", "_project_coc_run_stores",
              "_project_session_stores"]:
        monkeypatch.setattr(api, k, {})
    from app.providers.base import LLMResponse, Usage

    def fake_chore(step="chore"):
        class _P:
            name = f"fake:{step}"
            default_model = None
            @staticmethod
            def chat(messages, model=None, max_tokens=None, extra=None):
                return LLMResponse(text="（桩回复）", usage=Usage(input_tokens=1, output_tokens=1))
        return _P()
    monkeypatch.setattr(api, "_chore_provider", fake_chore)

    return TestClient(api.app)


def _mk_project(client, title="SessTest") -> str:
    r = client.post("/api/projects", json={"title": title, "genre": "x"})
    return r.json()["id"]


class TestSessionPhases:
    def test_all_phases_registered(self):
        from app.sessions import PHASE_META, SessionPhase
        expected = {"settle_up", "kindle", "scaffold", "walkthrough", "schedule", "daily_write"}
        assert set(PHASE_META.keys()) == expected
        for k, v in PHASE_META.items():
            assert "title" in v and "prompt" in v and "ai_mode" in v and "required_outputs" in v

    def test_create_field_session_requires_field_no(self, api_client):
        c = api_client
        pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field"})
        assert r.status_code == 422

    def test_create_and_advance_flow(self, api_client):
        c = api_client; pid = _mk_project(c)
        # 创建一个 field 级会话，默认从 settle_up 开始
        r = c.post(f"/api/projects/{pid}/sessions",
                   json={"scope": "field", "field_no": 1, "title": "第一场规划"})
        assert r.status_code == 200, r.text
        s = r.json()["session"]
        sid = s["id"]
        assert s["current_phase"] == "settle_up"
        assert s["status"] == "active"
        # 开场应该有一条 ai suggestion 消息
        assert any(m["role"] == "ai" and m["kind"] == "suggestion" for m in s["messages"])

        # 未填必填产物不能 advance
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        assert r.status_code == 422
        assert "账已结" in r.json()["detail"] or "还差" in r.json()["detail"] or "settlement" in r.json()["detail"]

        # 通过 gate 接口填 settlement_confirmed=true
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/gate",
                   json={"key": "settlement_confirmed", "value": True})
        assert r.status_code == 200
        assert r.json()["can_advance"] is True

        # 推进到 kindle
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        assert r.status_code == 200
        s = r.json()["session"]
        assert s["current_phase"] == "kindle"
        # 应该插入新阶段的 ai 欢迎消息
        ai_msgs = [m for m in s["messages"] if m["role"] == "ai"]
        assert any("副本点火" in m["text"] for m in ai_msgs)

    def test_send_message_triggers_ai_reply_and_updates_output(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions",
                   json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        # 先填 gate 让 kindle 阶段 core_pitch 是唯一缺项
        c.post(f"/api/projects/{pid}/sessions/{sid}/gate",
               json={"key": "settlement_confirmed", "value": True})
        c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        # 在 kindle 阶段发 author 消息，并通过 output_key 写入 core_pitch
        pitch = "雨夜巷子里借伞的婆婆其实是实体化身，唐迟是来送伞的"
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/send",
                   json={"text": pitch, "output_key": "core_pitch"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session"]["outputs"]["core_pitch"] == pitch
        # 最后一条应该是 ai 回应
        last = body["session"]["messages"][-1]
        assert last["role"] == "ai"
        # 填够了应该 can_advance
        # kindle 还需要 core_pitch ≥300 字？不——我们只要求非空 str，门控看 truthiness
        # 先确认 kindle 的 required_outputs 只要求 core_pitch
        from app.sessions import PHASE_META
        assert list(PHASE_META["kindle"]["required_outputs"].keys()) == ["core_pitch"]
        # 注意门控只检查非空，不卡字数；字数卡留给后续版本
        r2 = c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        assert r2.status_code == 200
        assert r2.json()["session"]["current_phase"] == "scaffold"

    def test_rollback_resets_phase(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        c.post(f"/api/projects/{pid}/sessions/{sid}/gate",
               json={"key": "settlement_confirmed", "value": True})
        c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        # 回退到 settle_up
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/rollback",
                   json={"to_phase": "settle_up"})
        assert r.status_code == 200
        assert r.json()["session"]["current_phase"] == "settle_up"

    def test_chapter_session_defaults_to_daily_write(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions",
                   json={"scope": "chapter", "chapter": 3, "title": "第3章日更"})
        assert r.status_code == 200
        s = r.json()["session"]
        assert s["scope"] == "chapter"
        assert s["current_phase"] == "daily_write"
        assert s["chapter"] == 3
        # daily_write 需要三问+damage+字数
        miss = set(s["missing_outputs"])
        assert "本章三问（读者以为/实际/结尾钩子）" in miss
        assert "弄坏了什么（作者亲笔一句话）" in miss

    def test_abandon_marks_status(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        r = c.delete(f"/api/projects/{pid}/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["session"]["status"] == "abandoned"
        # 不能再发消息
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/send", json={"text": "x"})
        assert r.status_code == 422

    def test_list_filters(self, api_client):
        c = api_client; pid = _mk_project(c)
        c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        c.post(f"/api/projects/{pid}/sessions", json={"scope": "chapter", "chapter": 1})
        r = c.get(f"/api/projects/{pid}/sessions", params={"scope": "field"})
        assert r.status_code == 200
        assert len(r.json()["sessions"]) == 1
        assert r.json()["sessions"][0]["scope"] == "field"
        r = c.get(f"/api/projects/{pid}/sessions")
        assert len(r.json()["sessions"]) == 2

    def test_unknown_phase_rollback_rejected(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/rollback", json={"to_phase": "nope"})
        assert r.status_code == 422


class TestSessionAction:
    def _to_kindle(self, c, pid, sid):
        """helper：填 settle_up 的 gate 并推进到 kindle。"""
        c.post(f"/api/projects/{pid}/sessions/{sid}/gate",
               json={"key": "settlement_confirmed", "value": True})
        c.post(f"/api/projects/{pid}/sessions/{sid}/advance")

    def test_action_unknown_rejected(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action", json={"action": "nope"})
        assert r.status_code == 422

    def test_daily_summon_on_chapter_session(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions",
                   json={"scope": "chapter", "chapter": 1})
        sid = r.json()["session"]["id"]
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action",
                   json={"action": "daily_summon"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "options" in body
        assert len(body["options"]) == 3
        # 应写入一条 ai suggestion 消息
        ai_msgs = [m for m in body["session"]["messages"] if m["role"] == "ai"
                   and m.get("meta", {}).get("action") == "daily_summon"]
        assert len(ai_msgs) == 1

    def test_daily_summon_rejected_on_field_session(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action",
                   json={"action": "daily_summon"})
        assert r.status_code == 422

    def test_kindle_diverge_requires_core_pitch(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        self._to_kindle(c, pid, sid)
        # 没写 core_pitch → 422
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action",
                   json={"action": "kindle_diverge"})
        assert r.status_code == 422
        assert "副本核" in r.json()["detail"]

    def test_kindle_diverge_runs_batch_with_fake_llm(self, api_client):
        """用桩 LLM 跑 kindle_diverge，验证：写入 artifacts + AI 摘要消息 + 返回 variants。"""
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        self._to_kindle(c, pid, sid)
        # 先通过 /send 写一个 core_pitch
        c.post(f"/api/projects/{pid}/sessions/{sid}/send",
               json={"text": "雨夜巷子里借伞的婆婆其实是实体化身，唐迟来送伞",
                     "output_key": "core_pitch"})
        # kindle_diverge（n_variants=2 让测试快一点）
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action",
                   json={"action": "kindle_diverge", "n_variants": 2})
        # 桩 LLM 只会返回"（桩回复）"，不会让 coc 解析到有效骰子/天，
        # 但 run_coc_simulation_batch 内部有 try/except，单场失败不影响整体
        assert r.status_code == 200, r.text
        body = r.json()
        assert "variants" in body
        # 即使 LLM 是桩，也应返回 n_variants 个 variant 结构
        assert len(body["variants"]) == 2
        # artifacts 里也应该有
        assert "kindle_variants" in body["session"]["artifacts"]
        # 应有 action=kindle_diverge 的 ai 消息
        ai_msgs = [m for m in body["session"]["messages"]
                   if m.get("meta", {}).get("action") == "kindle_diverge"]
        assert len(ai_msgs) == 1

    def test_walkthrough_step_rejected_wrong_phase(self, api_client):
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action",
                   json={"action": "walkthrough_step"})
        assert r.status_code == 422

    def test_plan_chapters_writes_chapter_plan(self, api_client):
        """schedule 阶段 plan_chapters action 应该写入 outputs.chapter_plan。"""
        c = api_client; pid = _mk_project(c)
        r = c.post(f"/api/projects/{pid}/sessions", json={"scope": "field", "field_no": 1})
        sid = r.json()["session"]["id"]
        # 直接推进到 schedule（填沿途 gate）
        for phase, gate_key in [
            ("settle_up", "settlement_confirmed"),
            ("kindle", "core_pitch"),
            ("scaffold", None),
            ("walkthrough", "walkthrough_confirmed"),
        ]:
            if phase == "scaffold":
                # scaffold 有 7 个必填项，逐个填
                for k in ["obituary_card", "entity_card", "truth_outline", "tangchi_start",
                          "core_twist", "foreshadows", "cost_cap"]:
                    c.post(f"/api/projects/{pid}/sessions/{sid}/gate", json={"key": k, "value": "x"})
            elif gate_key == "core_pitch":
                c.post(f"/api/projects/{pid}/sessions/{sid}/send",
                       json={"text": "副本核一句话", "output_key": gate_key})
            elif gate_key:
                c.post(f"/api/projects/{pid}/sessions/{sid}/gate", json={"key": gate_key, "value": True})
            c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        # 现在应该在 schedule 阶段
        cur = c.get(f"/api/projects/{pid}/sessions/{sid}").json()["session"]
        assert cur["current_phase"] == "schedule"
        # plan_chapters
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/action", json={"action": "plan_chapters"})
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        assert isinstance(plan, list)
        assert len(plan) >= 7   # 至少 7 章
        assert all("no" in x and "title" in x for x in plan)
        # chapter_plan_locked 后 advance 应该在 desk 建章
        c.post(f"/api/projects/{pid}/sessions/{sid}/gate", json={"key": "chapter_plan_locked", "value": True})
        r = c.post(f"/api/projects/{pid}/sessions/{sid}/advance")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session"]["status"] == "completed"
        # desk 应该能看到新章位（取 desk overview）
        desk = c.get(f"/api/projects/{pid}/desk").json()
        plan_nos = {x["no"] for x in plan}
        desk_nos = {ch["no"] for ch in desk["chapters"]}
        assert plan_nos.issubset(desk_nos)


class TestSessionStoreDirectly:
    def test_add_output_can_advance_roundtrip(self, tmp_path):
        from app.sessions import SessionStore, SessionPhase
        ss = SessionStore(tmp_path / "ss")
        s = ss.create(project_id="p", scope="field", field_no=1)
        ok, miss = s.can_advance()
        assert ok is False
        assert len(miss) >= 1
        s.set_output("settlement_confirmed", True)
        ss.save(s)
        ok2, miss2 = s.can_advance()
        assert ok2 is True
        # advance
        s2 = ss.advance_phase(s.id)
        assert s2.current_phase == SessionPhase.KINDLE.value
        assert s2.status == "active"
        # schedule 是最后阶段，advance 到最后会 completed
        from app.sessions import PHASE_META as _PM
        s2.current_phase = "schedule"
        for k in _PM["schedule"]["required_outputs"]:
            s2.set_output(k, True)
        ss.save(s2)
        s3 = ss.advance_phase(s2.id)
        assert s3.status == "completed"
