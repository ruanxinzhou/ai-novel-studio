"""M5 引导式写作测试：向导状态机 / 章台簿记 / 单章微循环 / 定稿飞轮 / API。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.entities import EntityStore
from app.pipeline.prompts import PromptStore
from app.projects import ProjectStore
from app.providers.base import LLMResponse, Usage
from app.review.markers import MarkerStore
from app.wizard import (
    STATIONS,
    DeskStore,
    WizardStore,
    chapter_outlines,
    finalize_chapter,
    run_chapter_stage,
)


class FakeProseProvider:
    """流水线假通道：固定文本，记录调用次数。"""
    default_model = None

    def __init__(self, text: str = "韩立睁眼，掌天瓶微光一闪。"):
        self.text = text
        self.calls = 0

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        self.calls += 1
        return LLMResponse(text=self.text, usage=Usage(input_tokens=10, output_tokens=10))


def _cards_json() -> str:
    return json.dumps([
        {"direction": f"方向{i}", "reason": f"理由{i}", "hook": f"钩子{i}", "premise": "梗概"}
        for i in range(1, 6)
    ], ensure_ascii=False)


_REVIEW_PASS = json.dumps({
    "items": [{"name": n, "status": "稳", "problem": ""}
              for n in ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场", "章末承接"]],
    "summary": "全稳",
}, ensure_ascii=False)

_REVIEW_BLOCKED = json.dumps({
    "items": [{"name": "剧情逻辑", "status": "不稳", "problem": "因果断裂"},
              {"name": "人物目标", "status": "不稳", "problem": "动机不明"},
              {"name": "情绪关系", "status": "稳", "problem": ""},
              {"name": "身体信息", "status": "稳", "problem": ""},
              {"name": "场景转场", "status": "稳", "problem": ""},
              {"name": "章末承接", "status": "稳", "problem": ""}],
    "summary": "两项不稳",
}, ensure_ascii=False)

_ENTITIES_JSON = json.dumps({
    "entities": [{"type": "character", "name": "韩立", "summary": "主角", "details": ""},
                 {"type": "item", "name": "掌天瓶", "summary": "小瓶", "details": "已埋"}]
}, ensure_ascii=False)


@pytest.fixture
def env(tmp_path):
    from types import SimpleNamespace
    project = ProjectStore(tmp_path / "projects")
    pid = project.create("测试书", "").id
    base = tmp_path / pid
    return SimpleNamespace(
        tmp=tmp_path, pid=pid, project=project,
        wizard=WizardStore(base / "wizard.json"),
        desk=DeskStore(base / "desk.json"),
        markers=MarkerStore(base / "markers.json"),
        entities=EntityStore(base / "entities.json"),
        prompts=PromptStore(),
        journal_dir=base / "runs",
    )


# ---- WizardStore ----

class TestWizardStore:
    def test_defaults_and_persistence(self, tmp_path):
        path = tmp_path / "wizard.json"
        w = WizardStore(path)
        d = w.get()
        assert d.n_chapters == 0 and d.skipped == [] and not d.gates_ok
        w.update(idea={"context": "修仙世界", "desire": "长生", "obstacle": "寿元"},
                 n_chapters=12, direction="凡人流", direction_source="picked")
        w2 = WizardStore(path)   # 新实例从盘上读
        assert w2.get().gates_ok
        assert w2.get().n_chapters == 12
        assert w2.get().direction == "凡人流"

    def test_toggle_skip_roundtrip(self, tmp_path):
        w = WizardStore(tmp_path / "wizard.json")
        w.toggle_skip("brainstorm")
        assert w.get().skipped == ["brainstorm"]
        w.toggle_skip("brainstorm")
        assert w.get().skipped == []
        with pytest.raises(ValueError, match="未知站"):
            w.toggle_skip("nope")

    def test_update_rejects_negative_chapters(self, tmp_path):
        w = WizardStore(tmp_path / "wizard.json")
        with pytest.raises(ValueError, match="不能为负"):
            w.update(n_chapters=-1)


class TestStationsState:
    def test_progression_to_graduated(self, env):
        st = env.wizard.stations_state(env.pid, env.project, env.desk)
        assert [s["station"] for s in st["stations"]] == STATIONS
        assert st["current_station"] == "topic" and not st["graduated"]

        env.project.adopt(env.pid, "intro", [], "简介")
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "brainstorm"

        env.wizard.update(direction="凡人流", direction_source="picked")
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "architecture"

        env.project.adopt(env.pid, "outline", [], "第1章\n入门。\n\n第2章\n生变。")
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "plotplan"

        env.desk.record(1, "plot", "本章剧情")
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "writing"

        env.desk.record(1, "draft", "本章正文")
        # 审稿没过 → writing 未完成
        env.desk.record_review(1, {"verdict": "blocked"})
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "writing"
        env.desk.record_review(1, {"verdict": "pass"})
        assert env.wizard.stations_state(env.pid, env.project, env.desk)["current_station"] == "polish"

        env.desk.mark_finalized(1)
        st = env.wizard.stations_state(env.pid, env.project, env.desk)
        assert st["graduated"] and st["current_station"] is None

    def test_skip_counts_as_passed(self, env):
        env.project.adopt(env.pid, "intro", [], "简介")
        env.wizard.toggle_skip("brainstorm")
        st = env.wizard.stations_state(env.pid, env.project, env.desk)
        assert st["current_station"] == "architecture"
        by_name = {s["station"]: s for s in st["stations"]}
        assert by_name["brainstorm"]["skipped"] and not by_name["brainstorm"]["done"]


# ---- 章台簿记与拆章 ----

class TestChapterOutlines:
    def test_split_and_bodies(self):
        text = "第1章\n入门试炼。\n\n第2章\n sudden 变故。\n第10章\n跳跃编号。"
        o = chapter_outlines(text)
        assert set(o) == {1, 2, 10}
        assert o[1] == "入门试炼。"
        assert o[2].startswith("sudden")

    def test_empty_and_headless(self):
        assert chapter_outlines("") == {}
        assert chapter_outlines("没有章号的大纲") == {}

    def test_volume_head_joins_next_chapter(self):
        text = "第一卷 入门\n第1章\n试炼。\n第2章\n变故。\n第二卷 风起\n第3章\n新篇。\n"
        o = chapter_outlines(text)
        assert set(o) == {1, 2, 3}
        assert o[1].startswith("第一卷")
        assert o[2] == "变故。"
        assert o[3].startswith("第二卷")


class TestDeskStore:
    def test_record_stages_and_refinalize(self, tmp_path):
        d = DeskStore(tmp_path / "desk.json")
        d.record(1, "plot", "剧情", "run1", title="入门")
        d.record(1, "draft", "正文", "run2")
        d.mark_finalized(1)
        w = d.get(1)
        assert w.title == "入门" and w.finalized_at
        # 重新生成剧情 → 退出定稿态
        d.record(1, "plot", "新剧情", "run3")
        assert not d.get(1).finalized_at
        with pytest.raises(ValueError, match="未知阶段"):
            d.record(1, "review", "x")

    def test_review_recorded(self, tmp_path):
        d = DeskStore(tmp_path / "desk.json")
        d.record_review(3, {"verdict": "pass"})
        assert d.get(3).review["verdict"] == "pass"
        assert d.all_nos() == [3]


# ---- 单章微循环 ----

class TestRunChapterStage:
    def test_plot_requires_outline(self, env):
        with pytest.raises(ValueError, match="故事架构"):
            run_chapter_stage(FakeProseProvider(), env.prompts, env.project, env.pid,
                              env.markers, env.wizard, env.desk, 1, "plot")

    def test_plot_run_and_idempotent_reuse(self, env):
        env.project.adopt(env.pid, "outline", [], "第1章\n入门试炼，遭遇劲敌。")
        provider = FakeProseProvider()
        r1 = run_chapter_stage(provider, env.prompts, env.project, env.pid,
                               env.markers, env.wizard, env.desk, 1, "plot",
                               journal_dir=env.journal_dir)
        assert env.desk.get(1).plot["text"] == provider.text
        assert r1["resumed_indices"] == [] and provider.calls == 1
        # 同输入重跑命中收据：不再调 LLM
        r2 = run_chapter_stage(provider, env.prompts, env.project, env.pid,
                               env.markers, env.wizard, env.desk, 1, "plot",
                               journal_dir=env.journal_dir)
        assert r2["resumed_indices"] == [0] and provider.calls == 1
        # force 绕过收据重新生成
        r3 = run_chapter_stage(provider, env.prompts, env.project, env.pid,
                               env.markers, env.wizard, env.desk, 1, "plot",
                               force=True, journal_dir=env.journal_dir)
        assert r3["resumed_indices"] == [] and provider.calls == 2

    def test_draft_requires_plot(self, env):
        with pytest.raises(ValueError, match="剧情"):
            run_chapter_stage(FakeProseProvider(), env.prompts, env.project, env.pid,
                              env.markers, env.wizard, env.desk, 1, "draft")

    def test_polish_requires_draft(self, env):
        with pytest.raises(ValueError, match="正文"):
            run_chapter_stage(FakeProseProvider(), env.prompts, env.project, env.pid,
                              env.markers, env.wizard, env.desk, 1, "polish")

    def test_full_chapter_loop_with_markers_and_anchor(self, env):
        env.project.adopt(env.pid, "outline", [], "第1章\n入门。\n\n第2章\n生变。")
        env.project.adopt(env.pid, "intro", [], "简介文本")
        env.wizard.update(direction="凡人流", direction_source="picked")
        env.markers.add(1, "必须出现：掌天瓶")
        provider = FakeProseProvider(text="韩立握紧掌天瓶。")

        run_chapter_stage(provider, env.prompts, env.project, env.pid,
                          env.markers, env.wizard, env.desk, 1, "plot",
                          journal_dir=env.journal_dir)
        run_chapter_stage(provider, env.prompts, env.project, env.pid,
                          env.markers, env.wizard, env.desk, 1, "draft",
                          journal_dir=env.journal_dir)
        run_chapter_stage(provider, env.prompts, env.project, env.pid,
                          env.markers, env.wizard, env.desk, 1, "polish",
                          journal_dir=env.journal_dir)
        w = env.desk.get(1)
        assert w.plot["text"] and w.draft["text"] and w.polish["text"]

        # 第 2 章正文锚点取第 1 章结尾
        env.desk.record(2, "plot", "第二章剧情")
        run_chapter_stage(provider, env.prompts, env.project, env.pid,
                          env.markers, env.wizard, env.desk, 2, "draft",
                          journal_dir=env.journal_dir)
        assert provider.calls == 4

    def test_unknown_stage(self, env):
        with pytest.raises(ValueError, match="未知阶段"):
            run_chapter_stage(FakeProseProvider(), env.prompts, env.project, env.pid,
                              env.markers, env.wizard, env.desk, 1, "review")


# ---- 定稿飞轮 ----

class TestFinalize:
    def _prepare(self, env):
        env.desk.record(1, "draft", "韩立握紧掌天瓶，掌天瓶微光一闪。")
        env.desk.record(2, "draft", "第二章正文。")

    def test_finalize_full_loop(self, env):
        self._prepare(env)
        env.markers.add(1, "掌天瓶")
        r = finalize_chapter(FakeProseProvider(_ENTITIES_JSON), env.prompts, env.project,
                             env.pid, env.entities, env.markers, env.desk, 1)
        assert r["chars"] > 0 and r["total_chapters"] == 2
        assert r["entities"]["created"][0]["name"] == "韩立"
        hits = {h["marker"]["content"]: h["hit"] for h in r["marker_hits"]}
        assert hits["掌天瓶"] is True
        v = env.project.current(env.pid, "draft")
        assert v is not None and "第1章" in v.y and "第2章" in v.y
        assert env.desk.get(1).finalized_at

    def test_finalize_without_entities(self, env):
        self._prepare(env)
        r = finalize_chapter(FakeProseProvider(), env.prompts, env.project,
                             env.pid, env.entities, env.markers, env.desk, 1,
                             auto_entities=False)
        assert r["entities"] is None

    def test_finalize_survives_extractor_failure(self, env):
        self._prepare(env)
        r = finalize_chapter(FakeProseProvider("乱码不是JSON"), env.prompts, env.project,
                             env.pid, env.entities, env.markers, env.desk, 1)
        assert "error" in r["entities"]
        assert env.desk.get(1).finalized_at   # 飞轮失败不阻断定稿

    def test_finalize_requires_text(self, env):
        with pytest.raises(ValueError, match="正文"):
            finalize_chapter(FakeProseProvider(), env.prompts, env.project,
                             env.pid, env.entities, env.markers, env.desk, 9)


# ---- API ----

class FakeRouter:
    def __init__(self, text: str):
        self.text = text

    def chat(self, step, messages, model=None, max_tokens=None):
        return LLMResponse(text=self.text, usage=Usage(input_tokens=1, output_tokens=10))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr("app.api.main._journal_path",
                        lambda rid, project=None: tmp_path / "runs" / f"{rid}.json")
    monkeypatch.setattr("app.api.main._journal_dir", lambda pid: tmp_path / "runs" / pid)
    monkeypatch.setattr("app.api.main._markers_for",
                        lambda project: MarkerStore(tmp_path / "mk" / f"{project or 'default'}.json"))
    monkeypatch.setattr("app.api.main._entities_for",
                        lambda pid: EntityStore(tmp_path / "ent" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._wizard_for",
                        lambda pid: WizardStore(tmp_path / "wiz" / f"{pid}.json"))
    monkeypatch.setattr("app.api.main._desk_for",
                        lambda pid: DeskStore(tmp_path / "dsk" / f"{pid}.json"))
    from app.api.main import app
    return TestClient(app)


class TestWizardApi:
    def test_wizard_flow(self, client):
        pid = client.post("/api/projects", json={"title": "向导书"}).json()["id"]

        st = client.get(f"/api/projects/{pid}/wizard").json()
        assert st["current_station"] == "topic" and not st["gates_ok"]

        r = client.put(f"/api/projects/{pid}/wizard", json={
            "idea": {"context": "修仙世界", "desire": "长生", "obstacle": "寿元大限"},
            "seed": "凡人流种田", "n_chapters": 3})
        assert r.status_code == 200
        assert client.get(f"/api/projects/{pid}/wizard").json()["gates_ok"] is True

        # 空方向拒收
        assert client.put(f"/api/projects/{pid}/wizard",
                          json={"direction": "  "}).status_code == 422

        # 跳站可来回
        r = client.post(f"/api/projects/{pid}/wizard/brainstorm/skip").json()
        assert r["wizard"]["skipped"] == ["brainstorm"]
        assert client.post(f"/api/projects/{pid}/wizard/brainstorm/skip"
                           ).json()["wizard"]["skipped"] == []
        assert client.post(f"/api/projects/{pid}/wizard/nope/skip").status_code == 404

        # 采纳 intro → topic 完成；未签收方向时当前站是脑洞
        client.post(f"/api/projects/{pid}/layers/intro",
                    json={"y": "简介", "x_pairs": []})
        st = client.get(f"/api/projects/{pid}/wizard").json()
        assert st["current_station"] == "brainstorm"

        # 签收方向 + 采纳 outline → 推进到剧情规划
        client.put(f"/api/projects/{pid}/wizard",
                   json={"direction": "凡人流", "direction_source": "picked"})
        client.post(f"/api/projects/{pid}/layers/outline",
                    json={"y": "第1章\n入门。", "x_pairs": []})
        st = client.get(f"/api/projects/{pid}/wizard").json()
        assert st["current_station"] == "plotplan"

        # 项目列表带进度摘要
        summary = client.get("/api/projects").json()["projects"][0]["wizard_summary"]
        assert summary["done"] == 3 and summary["current_station"] == "plotplan"

    def test_brainstorm_cards(self, client, monkeypatch):
        pid = client.post("/api/projects", json={"title": "脑洞书"}).json()["id"]
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter(_cards_json()))
        r = client.post(f"/api/projects/{pid}/wizard/brainstorm")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["cards"]) == 5
        assert body["cards"][0]["direction"] == "方向1"

    def test_brainstorm_bad_output_502(self, client, monkeypatch):
        pid = client.post("/api/projects", json={"title": "坏卡书"}).json()["id"]
        monkeypatch.setattr("app.api.main.get_router",
                            lambda: FakeRouter("not a json"))
        assert client.post(f"/api/projects/{pid}/wizard/brainstorm").status_code == 502


class TestDeskApi:
    def _setup_book(self, client, monkeypatch, prose="韩立握紧掌天瓶。"):
        """开书：intro/outline 已采纳 + 方向已签收，直接可进章台。"""
        pid = client.post("/api/projects", json={"title": "章台书"}).json()["id"]
        client.post(f"/api/projects/{pid}/layers/intro", json={"y": "简介", "x_pairs": []})
        client.post(f"/api/projects/{pid}/layers/outline",
                    json={"y": "第1章\n入门试炼。", "x_pairs": []})
        client.put(f"/api/projects/{pid}/wizard",
                   json={"direction": "凡人流", "direction_source": "picked"})
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter(prose))
        return pid

    def test_desk_flow_to_graduated(self, client, monkeypatch):
        pid = self._setup_book(client, monkeypatch)

        desk = client.get(f"/api/projects/{pid}/desk").json()
        assert desk["total"] == 1 and desk["chapters"][0]["has_outline"]

        r = client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})
        assert r.status_code == 200, r.text
        assert r.json()["y"] == "韩立握紧掌天瓶。"
        # 幂等重跑
        r2 = client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"}).json()
        assert r2["resumed_indices"] == [0]

        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "draft"})
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "polish"})

        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter(_REVIEW_PASS))
        rv = client.post(f"/api/projects/{pid}/desk/1/review")
        assert rv.status_code == 200
        assert rv.json()["verdict"] == "pass"

        fz = client.post(f"/api/projects/{pid}/desk/1/finalize", json={"auto_entities": False})
        assert fz.status_code == 200, fz.text
        assert fz.json()["total_chapters"] == 1

        desk = client.get(f"/api/projects/{pid}/desk").json()
        assert desk["finalized"] == 1
        summary = client.get("/api/projects").json()["projects"][0]["wizard_summary"]
        assert summary["graduated"] is True

        # 定稿产生 draft 层版本（整本合成，Vault 导出兼容）
        v = client.get(f"/api/projects/{pid}/layers/draft").json()["version"]
        assert "第1章" in v["y"]

    def test_desk_finalize_with_flywheel(self, client, monkeypatch):
        pid = self._setup_book(client, monkeypatch)
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "draft"})
        # chore 通道返回实体 JSON → 飞轮抽取入库
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter(_ENTITIES_JSON))
        fz = client.post(f"/api/projects/{pid}/desk/1/finalize")
        assert fz.status_code == 200, fz.text
        assert fz.json()["entities"]["created"][0]["name"] == "韩立"
        ents = client.get(f"/api/projects/{pid}/entities").json()
        assert ents["counts"]["character"] == 1 and ents["counts"]["item"] == 1

    def test_desk_run_missing_outline_422(self, client, monkeypatch):
        pid = client.post("/api/projects", json={"title": "无纲书"}).json()["id"]
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter("x"))
        r = client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})
        assert r.status_code == 422
        assert "故事架构" in r.json()["detail"]

    def test_desk_chapter_view_and_manual_edit(self, client, monkeypatch):
        pid = self._setup_book(client, monkeypatch)
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})

        v = client.get(f"/api/projects/{pid}/desk/1").json()
        assert v["outline"] == "入门试炼。"
        assert v["chapter"]["plot"]["text"] == "韩立握紧掌天瓶。"

        # 作者直接自写剧情（不跑模型）
        r = client.put(f"/api/projects/{pid}/desk/1/plot", json={"text": "我自己写的剧情。"})
        assert r.status_code == 200
        v = client.get(f"/api/projects/{pid}/desk/1").json()
        assert v["chapter"]["plot"]["text"] == "我自己写的剧情。"
        assert client.put(f"/api/projects/{pid}/desk/1/review",
                          json={"text": "x"}).status_code == 422   # review 不是可写阶段

    def test_desk_review_flow(self, client, monkeypatch):
        pid = self._setup_book(client, monkeypatch)
        # 没有正文不能审
        assert client.post(f"/api/projects/{pid}/desk/1/review").status_code == 422

        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "draft"})

        monkeypatch.setattr("app.api.main.get_router",
                            lambda: FakeRouter(_REVIEW_BLOCKED))
        r = client.post(f"/api/projects/{pid}/desk/1/review")
        assert r.json()["verdict"] == "blocked"
        desk = client.get(f"/api/projects/{pid}/desk").json()
        assert desk["chapters"][0]["review_verdict"] == "blocked"

        # 审稿输出坏 JSON → 502
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter("乱码"))
        assert client.post(f"/api/projects/{pid}/desk/1/review").status_code == 502
