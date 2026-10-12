"""开章核对单（P1①）测试：构建器三块内容 + 章台注入 + API 开关。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.context import MaterialFamily
from app.context.ledger import build_chapter_checklist
from app.entities import EntityStore
from app.pipeline.prompts import PromptStore
from app.projects import ProjectStore
from app.providers.base import LLMResponse, Usage
from app.review import MarkerStore
from app.wizard import DeskStore, WizardStore, run_chapter_stage


@pytest.fixture
def env(tmp_path):
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


class CaptureProvider:
    default_model = None

    def __init__(self, text: str = "本章剧情。"):
        self.text = text
        self.messages: list[dict] = []

    def chat(self, messages, model=None, max_tokens=None, extra=None):
        self.messages.extend(messages)
        return LLMResponse(text=self.text, usage=Usage(input_tokens=1, output_tokens=1))


# ---- 构建器 ----

def test_empty_book_returns_none(env):
    assert build_chapter_checklist(env.entities, env.desk, 1, 0) is None


def test_position_entities_and_foreshadow(env):
    env.desk.record(1, "plot", "第一章剧情。")
    env.desk.record(1, "draft", "第一章正文。")
    env.desk.record(2, "plot", "第二章剧情。")
    c = env.entities.add("character", "陆离", summary="主角", chapter=1)
    env.entities.record_appearance(c.id, 2)
    w = env.entities.add("character", "老王", summary="看门大爷", chapter=1)
    env.entities.update(w.id, status="retired")
    env.entities.add("character", "幕后黑手", summary="终卷BOSS")          # 未出场
    env.entities.add("location", "诡异片场", summary="首个副本", chapter=1)
    env.entities.add("item", "作死系统", summary="金手指", chapter=2)
    env.entities.add("plotline", "影视世界巡礼", summary="主线", chapter=1)
    f = env.entities.add("foreshadow", "床下的朋友", summary="布偶归位", chapter=1)
    env.entities.add("foreshadow", "面板暗纹", summary="属性异常", chapter=2)
    env.entities.update(f.id, status="paid")                              # 已回收不进到期清单

    m = build_chapter_checklist(env.entities, env.desk, 3, 50)
    assert m is not None
    assert m.family == MaterialFamily.REQUIRED and m.source_id == "checklist:ch3"
    for frag in ("【本章位置】", "第 3 章", "第 50 章", "进度约 6%",
                 "已写 2 章（最近写到第 2 章）",
                 "【角色快照】", "陆离：主角（第1-2章）", "老王：看门大爷〔已退场〕",
                 "幕后黑手：终卷BOSS〔设定中，尚未出场〕",
                 "【地点快照】", "【道具快照】", "【情节线快照】",
                 "【到期伏笔】", "面板暗纹：属性异常（第2章埋，已欠 1 章）"):
        assert frag in m.content, frag
    assert "床下的朋友" not in m.content          # 已回收不出现


def test_old_foreshadow_marked_and_gap_warning(env):
    env.desk.record(1, "plot", "一")
    env.desk.record(2, "plot", "二")
    env.entities.add("foreshadow", "青铜镜来历", summary="第1章埋", chapter=1)
    m = build_chapter_checklist(env.entities, env.desk, 12, 30)
    assert "已欠 11 章，重点兑现" in m.content
    assert "第 3-11 章尚未写，本章是跳章续写" in m.content


def test_future_only_entities_excluded(env):
    """只在本章及之后出场的实体不进快照；跨前后出场的只显示之前的章。"""
    c = env.entities.add("character", "后期角色", chapter=15)
    env.entities.record_appearance(c.id, 20)
    both = env.entities.add("character", "贯穿角色", chapter=2)
    env.entities.record_appearance(both.id, 15)
    m = build_chapter_checklist(env.entities, env.desk, 10, 30)
    assert "后期角色" not in m.content
    assert "贯穿角色（第2章）" in m.content and "第15章" not in m.content.split("贯穿角色")[1][:20]


def test_section_limits(env):
    for i in range(25):
        env.entities.add("character", f"角色{i:02d}", summary="路人", chapter=1)
    m = build_chapter_checklist(env.entities, env.desk, 5, 30)
    assert "其余 5 个略" in m.content


# ---- 章台注入 ----

def test_run_chapter_stage_injects_checklist(env):
    env.project.adopt(env.pid, "outline", [], "第1章\n开篇。\n\n第2章\n生变。")
    env.desk.record(1, "plot", "第一章剧情。")
    c = env.entities.add("character", "陆离", summary="主角", chapter=1)
    env.entities.record_appearance(c.id, 1)
    env.entities.add("foreshadow", "青铜镜", summary="来历不明", chapter=1)
    provider = CaptureProvider()

    r = run_chapter_stage(provider, env.prompts, env.project, env.pid, env.markers,
                          env.wizard, env.desk, 2, "plot", journal_dir=None,
                          entity_store=env.entities)
    assert r["checklist_injected"] is True
    prompt = "\n".join(m["content"] for m in provider.messages)
    assert "开章核对单" in prompt and "青铜镜" in prompt and "到期伏笔" in prompt


def test_run_chapter_stage_without_store_no_checklist(env):
    env.project.adopt(env.pid, "outline", [], "第1章\n开篇。")
    env.entities.add("character", "陆离", summary="主角", chapter=1)
    provider = CaptureProvider()
    r = run_chapter_stage(provider, env.prompts, env.project, env.pid, env.markers,
                          env.wizard, env.desk, 1, "plot", journal_dir=None)
    assert r["checklist_injected"] is False
    assert "开章核对单" not in "\n".join(m["content"] for m in provider.messages)


def test_polish_stage_never_injects(env):
    env.project.adopt(env.pid, "outline", [], "第1章\n开篇。")
    env.desk.record(1, "plot", "剧情。")
    env.desk.record(1, "draft", "正文。")
    env.entities.add("character", "陆离", summary="主角", chapter=1)
    provider = CaptureProvider()
    r = run_chapter_stage(provider, env.prompts, env.project, env.pid, env.markers,
                          env.wizard, env.desk, 1, "polish", journal_dir=None,
                          entity_store=env.entities)
    assert r["checklist_injected"] is False


# ---- API ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.main._project_store", ProjectStore(tmp_path / "projects"))
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


def test_api_checklist_default_on_and_toggle_off(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"title": "核对书"}).json()["id"]
    client.post(f"/api/projects/{pid}/layers/outline",
                json={"y": "第1章\n开篇。\n\n第2章\n生变。", "x_pairs": []})
    ent = EntityStore(tmp_path / "ent" / f"{pid}.json")
    ent.add("foreshadow", "青铜镜", summary="来历不明", chapter=1)
    desk = DeskStore(tmp_path / "dsk" / f"{pid}.json")
    desk.record(1, "plot", "第一章剧情。")

    captured: list[dict] = []

    class CapRouter:
        def chat(self, step, messages, model=None, max_tokens=None):
            captured.extend(messages)
            return LLMResponse(text="第二章剧情。", usage=Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("app.api.main.get_router", lambda: CapRouter())

    r1 = client.post(f"/api/projects/{pid}/desk/2/run",
                     json={"stage": "plot"}).json()
    assert r1["checklist_injected"] is True
    assert "到期伏笔" in "\n".join(m["content"] for m in captured)

    captured.clear()
    r2 = client.post(f"/api/projects/{pid}/desk/2/run",
                     json={"stage": "plot", "checklist": False, "force": True}).json()
    assert r2["checklist_injected"] is False
    assert "开章核对单" not in "\n".join(m["content"] for m in captured)
