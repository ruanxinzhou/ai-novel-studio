"""M5 引导式写作测试：向导状态机 / 章台簿记 / 单章微循环 / 定稿飞轮 / API。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.book.volume import VolumeStore
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
    parse_outline,
    run_chapter_stage,
)
from app.wizard.store import ChapterWork


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


_REVIEW_ALL9 = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场",
                "章末承接", "跨章联结", "信息揭露节奏", "克苏鲁白描"]

_REVIEW_PASS = json.dumps({
    "items": [{"name": n, "status": "稳", "problem": ""} for n in _REVIEW_ALL9],
    "summary": "全稳",
}, ensure_ascii=False)

_REVIEW_BLOCKED = json.dumps({
    "items": [{"name": "剧情逻辑", "status": "不稳", "problem": "因果断裂"},
              {"name": "人物目标", "status": "不稳", "problem": "动机不明"},
              *[{"name": n, "status": "稳", "problem": ""}
                for n in _REVIEW_ALL9 if n not in ("剧情逻辑", "人物目标")]],
    "summary": "两项不稳",
}, ensure_ascii=False)

_ENTITIES_JSON = json.dumps({
    "entities": [{"type": "character", "name": "韩立", "summary": "主角", "details": ""},
                 {"type": "item", "name": "掌天瓶", "summary": "小瓶", "details": "已埋"}]
}, ensure_ascii=False)


class _FinalizeProvider:
    """定稿飞轮假通道：区分实体抽取与伏笔问询两种调用，分别返回固定 payload。"""
    default_model = None

    def __init__(self, extract: str = _ENTITIES_JSON, reconcile: str = "{}"):
        self.extract = extract
        self.reconcile = reconcile
        self.reconcile_calls = 0

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        if any("未回收伏笔清单" in m["content"] for m in messages):
            self.reconcile_calls += 1
            return LLMResponse(text=self.reconcile, usage=Usage(input_tokens=1, output_tokens=1))
        return LLMResponse(text=self.extract, usage=Usage(input_tokens=1, output_tokens=1))


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

    def test_volume_heads_group_chapters(self):
        """卷头成结构：不黏章正文，parse_outline 返回卷分组。"""
        text = "第一卷 入门\n第1章\n试炼。\n第2章\n变故。\n第二卷 风起\n第3章\n新篇。\n"
        o, vols = parse_outline(text)
        assert set(o) == {1, 2, 3}
        assert o[1] == "试炼。" and o[2] == "变故。" and o[3] == "新篇。"
        assert [v["title"] for v in vols] == ["第一卷 入门", "第二卷 风起"]
        assert vols[0]["chapters"] == [1, 2] and vols[1]["chapters"] == [3]

    def test_markdown_heads_parsed(self):
        """## 第N章 / ## 第N卷 Markdown 格式与裸格式等价（作者编辑器随手改格式不炸）。"""
        text = "## 第一卷 风起\n\n## 第1章《开端》\n大纲A\n\n## 第2章《转折》\n大纲B\n"
        o, vols = parse_outline(text)
        assert o[1].startswith("《开端》") and "大纲A" in o[1]
        assert o[2].startswith("《转折》")
        assert vols[0]["title"] == "第一卷 风起" and vols[0]["chapters"] == [1, 2]

    def test_volume_note_kept_not_polluting_chapter(self):
        """卷头到下一章之间的零散引言记为卷 note，不进任何章大纲也不丢。"""
        text = "第1章\n无卷。\n第二卷 中段\n卷引言：这里开始换地图。\n第2章\n有卷。"
        o, vols = parse_outline(text)
        assert o[1] == "无卷。" and o[2] == "有卷。"
        assert vols[0]["title"] == "" and vols[0]["chapters"] == [1]   # 卷头前的章归无名首组
        assert vols[1]["title"] == "第二卷 中段"
        assert vols[1]["note"] == "卷引言：这里开始换地图。"
        assert vols[1]["chapters"] == [2]

    def test_markdown_separator_lines_stripped(self):
        text = "第1章《标题》\n-------------\n大纲正文。\n\n第2章\n=====\n第二章正文。"
        o, _ = parse_outline(text)
        assert o[1] == "《标题》\n大纲正文。"
        assert o[2] == "第二章正文。"

    def test_no_volume_heads_flat_unnamed_group(self):
        o, vols = parse_outline("第1章\n一。\n第2章\n二。")
        assert vols[0]["title"] == "" and vols[0]["chapters"] == [1, 2]


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

    def test_payoff_record_roundtrip_and_rejects(self, tmp_path):
        d = DeskStore(tmp_path / "desk.json")
        d.record(1, "draft", "正文")
        d.record_payoff(1, "small")
        assert d.get(1).payoff == "small"
        d.record_payoff(1, "climax")            # 随时改
        d2 = DeskStore(tmp_path / "desk.json")  # 盘上回读
        assert d2.get(1).payoff == "climax"
        # 改动正文不清除兑现记录
        d.record(1, "draft", "改后的正文")
        assert d.get(1).payoff == "climax"
        with pytest.raises(ValueError, match="未知兑现规模"):
            d.record_payoff(1, "big")


# ---- 单章微循环 ----

class TestRunChapterStage:
    def test_plot_requires_outline(self, env):
        with pytest.raises(ValueError, match="大纲/方向卡中未找到第"):
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

    def test_finalize_reconciles_foreshadow(self, env):
        self._prepare(env)
        env.entities.add("foreshadow", "青铜镜", summary="来历不明", chapter=1)
        provider = _FinalizeProvider(reconcile='{"paid": ["青铜镜"], "advanced": []}')
        r = finalize_chapter(provider, env.prompts, env.project, env.pid,
                             env.entities, env.markers, env.desk, 1)
        assert r["foreshadow"]["paid"] == ["青铜镜"]
        f = env.entities.find("foreshadow", "青铜镜")
        assert f.status == "paid" and provider.reconcile_calls == 1

    def test_finalize_no_planted_skips_inquiry(self, env):
        self._prepare(env)
        provider = _FinalizeProvider()
        r = finalize_chapter(provider, env.prompts, env.project, env.pid,
                             env.entities, env.markers, env.desk, 1)
        assert r["foreshadow"] is None            # 没有债就不问
        assert provider.reconcile_calls == 0

    def test_finalize_reconcile_failure_not_blocking(self, env):
        self._prepare(env)
        env.entities.add("foreshadow", "青铜镜", summary="来历不明", chapter=1)
        r = finalize_chapter(_FinalizeProvider(reconcile="乱码不是JSON"),
                             env.prompts, env.project, env.pid,
                             env.entities, env.markers, env.desk, 1)
        assert "error" in r["foreshadow"]
        assert env.desk.get(1).finalized_at      # 问询失败不阻断定稿

    def test_finalize_records_payoff(self, env):
        self._prepare(env)
        r = finalize_chapter(FakeProseProvider(), env.prompts, env.project,
                             env.pid, env.entities, env.markers, env.desk, 1,
                             auto_entities=False, payoff="medium")
        assert r["chapter"] == 1
        assert env.desk.get(1).payoff == "medium"   # 定稿顺手点选落账


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
        assert "方向卡" in r.json()["detail"]

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

    def test_payoff_endpoint_and_finalize_with_payoff(self, client, monkeypatch):
        pid = self._setup_book(client, monkeypatch)
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "plot"})
        client.post(f"/api/projects/{pid}/desk/1/run", json={"stage": "draft"})

        # 未定稿可先记、可改
        r = client.put(f"/api/projects/{pid}/desk/1/payoff", json={"payoff": "small"})
        assert r.status_code == 200 and r.json()["payoff_label"] == "小爽点"
        client.put(f"/api/projects/{pid}/desk/1/payoff", json={"payoff": "medium"})
        assert client.get(f"/api/projects/{pid}/desk/1").json()["chapter"]["payoff"] == "medium"
        # 章节总览也带 payoff
        assert client.get(f"/api/projects/{pid}/desk").json()["chapters"][0]["payoff"] == "medium"
        # 非法值 422
        assert client.put(f"/api/projects/{pid}/desk/1/payoff",
                          json={"payoff": "big"}).status_code == 422

        # 定稿时改选 → 以定稿提交为准
        fz = client.post(f"/api/projects/{pid}/desk/1/finalize",
                         json={"auto_entities": False, "payoff": "climax"})
        assert fz.status_code == 200
        assert client.get(f"/api/projects/{pid}/desk/1").json()["chapter"]["payoff"] == "climax"

    def test_review_rhythm_warning(self, client, monkeypatch):
        """连续 4 章无兑现 → 审稿附带节奏饥饿提示（不改 verdict）。"""
        pid = client.post("/api/projects", json={"title": "节奏书"}).json()["id"]
        client.post(f"/api/projects/{pid}/layers/outline",
                    json={"y": "第1章\n一。\n\n第2章\n二。\n\n第3章\n三。\n\n第4章\n四。", "x_pairs": []})
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter("正文。"))
        for no in range(1, 5):
            client.post(f"/api/projects/{pid}/desk/{no}/run", json={"stage": "plot"})
            client.post(f"/api/projects/{pid}/desk/{no}/run", json={"stage": "draft"})

        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter(_REVIEW_PASS))
        r3 = client.post(f"/api/projects/{pid}/desk/3/review").json()   # 连续 3 章无兑现：不报
        assert "rhythm_warning" not in r3
        r4 = client.post(f"/api/projects/{pid}/desk/4/review").json()   # 连续 4 章：报
        assert "节奏饥饿" in r4["rhythm_warning"]
        assert r4["verdict"] == "pass"                                  # 提示不拦稿

        # 第 2 章补记小爽点后，第 4 章再审只算 2 章无兑现：不报
        client.put(f"/api/projects/{pid}/desk/2/payoff", json={"payoff": "small"})
        r4b = client.post(f"/api/projects/{pid}/desk/4/review").json()
        assert "rhythm_warning" not in r4b

    def test_desk_overview_groups_by_volume(self, client):
        """大纲带卷头（裸/Markdown 均可）：总览返回 volumes，章带所属卷名。"""
        pid = client.post("/api/projects", json={"title": "分卷书"}).json()["id"]
        client.post(f"/api/projects/{pid}/layers/outline", json={
            "y": "第一卷 蓝星\n第1章\n一。\n第2章\n二。\n## 第二卷 戏内\n## 第3章《入戏》\n三。",
            "x_pairs": [],
        })
        r = client.get(f"/api/projects/{pid}/desk").json()
        assert r["outline_chapters"] == 3
        assert [v["title"] for v in r["volumes"]] == ["第一卷 蓝星", "第二卷 戏内"]
        assert r["volumes"][0]["chapters"] == [1, 2] and r["volumes"][1]["chapters"] == [3]
        by_no = {c["no"]: c for c in r["chapters"]}
        assert by_no[1]["volume"] == "第一卷 蓝星"
        assert by_no[3]["volume"] == "第二卷 戏内" and by_no[3]["title"] == "《入戏》"
        assert by_no[1]["outline_preview"] == "一。"      # 卷头不黏进章大纲

    def test_desk_review_injects_prev_tail(self, client, monkeypatch):
        """第七项跨章联结：章台审稿自动带上一章结尾作对照（第一章无前章不注入）。"""
        pid = client.post("/api/projects", json={"title": "接力书"}).json()["id"]
        client.post(f"/api/projects/{pid}/layers/outline",
                    json={"y": "第1章\n一。\n\n第2章\n二。", "x_pairs": []})
        monkeypatch.setattr("app.api.main.get_router", lambda: FakeRouter("正文。"))
        for no in (1, 2):
            client.post(f"/api/projects/{pid}/desk/{no}/run", json={"stage": "plot"})
            client.post(f"/api/projects/{pid}/desk/{no}/run", json={"stage": "draft"})

        captured: list[list[dict]] = []

        class CaptureRouter:
            def chat(self, step, messages, model=None, max_tokens=None):
                assert step == "review"
                captured.append(messages)
                return LLMResponse(text=_REVIEW_PASS, usage=Usage(input_tokens=1, output_tokens=1))

        monkeypatch.setattr("app.api.main.get_router", lambda: CaptureRouter())
        assert client.post(f"/api/projects/{pid}/desk/1/review").json()["verdict"] == "pass"
        # 第一章无前章：user 消息只在 review 指令里出现 2 处「【上一章结尾】」
        assert captured[0][1]["content"].count("【上一章结尾】") == 2
        assert client.post(f"/api/projects/{pid}/desk/2/review").json()["verdict"] == "pass"
        # 第二章：指令 2 处 + 注入块 1 处，注入的是第 1 章正文结尾
        user2 = captured[1][1]["content"]
        assert user2.count("【上一章结尾】") == 3
        assert "正文" in user2.split("【上一章结尾】", 3)[3]


# ---- Task 1：mainline volume_kind + start_chapter_no + ChapterWork.chapter_kind ----

class TestChapterKindAndVolumeStart:
    def test_volume_default_kind_is_dungeon(self, tmp_path):
        store = VolumeStore(tmp_path / "volumes")
        v = store.create_new_volume()
        assert v.volume_kind == "dungeon"
        assert v.start_chapter_no == 1
        assert v.title.startswith("卷")

    def test_create_mainline_volume(self, tmp_path):
        store = VolumeStore(tmp_path / "volumes")
        v = store.create_new_volume(volume_kind="mainline", start_chapter_no=1)
        assert v.volume_kind == "mainline"
        assert v.start_chapter_no == 1
        assert v.title == "蓝星开篇"

    def test_chapter_work_default_kind_dungeon(self):
        w = ChapterWork(no=1)
        assert w.chapter_kind == "dungeon"
        assert w.is_interlude is False

    def test_chapter_work_from_dict_backcompat(self):
        # 老数据只有 is_interlude=True → chapter_kind 回填为 interlude，is_interlude 保持 True
        w = ChapterWork.from_dict({"no": 1, "title": "x", "is_interlude": True})
        assert w.chapter_kind == "interlude"
        assert w.is_interlude is True
        # 显式 chapter_kind=mainline → chapter_kind=mainline, is_interlude=False
        w2 = ChapterWork.from_dict({"no": 1, "title": "x", "chapter_kind": "mainline"})
        assert w2.chapter_kind == "mainline"
        assert w2.is_interlude is False
