"""Phase 3/4: COC 多轮跑团仿真 + 批量 5 选 1 variant-pool 的单元测试。"""
from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from app.fields.coc_run import (
    CocRunState, CocRunStore, CocTurn, DEFAULT_PLAYER_CARD,
    detect_day, parse_dice_rolls,
)
from app.pipeline.coc_simulator import (
    _check_termination, apply_state_patch, init_run, intervene,
)


# ---------------------------------------------------------------------------
# 骰子解析
# ---------------------------------------------------------------------------

class TestParseDiceRolls:
    def test_success_roll(self):
        text = "唐迟推开木门，投掷 侦查：42/50 成功，在地板缝里发现一枚铜纽扣。"
        rolls = parse_dice_rolls(text)
        assert len(rolls) == 1
        r = rolls[0]
        assert r.skill == "侦查"
        assert r.rolled == 42
        assert r.target == 50
        assert r.result == "success"
        assert r.san_loss == 0

    def test_san_loss_roll(self):
        text = "墙角老照片里的人对他眨了眨眼。投掷 SAN：95/60 大失败，失去 5 SAN。"
        rolls = parse_dice_rolls(text)
        assert len(rolls) == 1
        r = rolls[0]
        assert r.skill == "SAN"
        assert r.result == "fumble"
        assert r.san_loss == 5

    def test_san_with_dice(self):
        # 1d6 → 随机 1-6
        text = "他看见不可名状之物，投掷 SAN：80/50 失败，失去 1d6 SAN。"
        rolls = parse_dice_rolls(text)
        assert len(rolls) == 1
        assert 1 <= rolls[0].san_loss <= 6

    def test_failure(self):
        text = "唐迟屏住呼吸贴墙，投掷 聆听：73/40 失败，脚步声已经到了身后。"
        rolls = parse_dice_rolls(text)
        assert len(rolls) == 1
        assert rolls[0].result == "failure"

    def test_multiple_rolls_in_one_text(self):
        text = ("先投掷 侦查：30/50 成功发现暗门；"
                "推门时一股寒意扑面，投掷 SAN：20/60 成功但仍失去 1 SAN。")
        rolls = parse_dice_rolls(text)
        assert len(rolls) == 2
        assert rolls[0].skill == "侦查"
        assert rolls[1].skill == "SAN"
        assert rolls[1].san_loss == 1

    def test_no_roll_returns_empty(self):
        assert parse_dice_rolls("唐迟躲在柜子里不敢出声。") == []

    def test_day_detection(self):
        assert detect_day("Day3 清晨，雨下得急") == 3
        assert detect_day("第七日零时") == 7
        assert detect_day("第 5 天夜里") == 5
        assert detect_day("什么都没发生") is None


# ---------------------------------------------------------------------------
# CocRunStore 存取
# ---------------------------------------------------------------------------

@pytest.fixture
def coc_store(tmp_path):
    return CocRunStore(tmp_path / "coc")


class TestCocRunStore:
    def test_init_and_save_roundtrip(self, coc_store):
        # 显式传空 seed，避免 san_start_penalty 随机扣减
        state = init_run(coc_store, 1, {"vid": "v1", "title": "青铜巷雨夜"},
                         variant_seed={"san_start_penalty": 0, "obsession_start_uses": 0,
                                       "extra_npcs": [], "planted_foreshadows": []})
        assert state.status == "pending"
        assert state.san == DEFAULT_PLAYER_CARD["san_max"]
        assert state.hp == DEFAULT_PLAYER_CARD["hp_max"]
        assert state.next_role == "keeper"
        assert coc_store.exists(1)

        loaded = coc_store.get(1)
        assert loaded is not None
        assert loaded.field_no == 1
        assert loaded.player_card["name"] == "唐迟"

    def test_add_turns_builds_transcript(self, coc_store):
        state = init_run(coc_store, 1, {"vid": "v1", "title": "x"})
        state.add_turn("keeper", "KP 开场描述。", meta={})
        state.add_turn("investigator", "唐迟后退一步。", meta={})
        state.add_turn("system", "系统提示。", meta={})
        coc_store.save(state)

        tr = state.transcript()
        assert "KP 回合" in tr
        assert "唐迟行动" in tr
        assert "系统" in tr
        assert len(state.turns) == 3

    def test_player_card_render(self, coc_store):
        state = init_run(coc_store, 1, {"vid": "v1"})
        r = state.player_card_render()
        assert "唐迟" in r
        assert "SAN" in r and "/99" in r     # 随机开场扣 0-10 SAN，但上限始终 99
        assert "Day1" in r


# ---------------------------------------------------------------------------
# 终止判定 + 状态补丁
# ---------------------------------------------------------------------------

class TestTermination:
    def _fresh(self, tmp_path):
        cs = CocRunStore(tmp_path / "coc")
        return init_run(cs, 1, {"vid": "v1"}), cs

    def test_dead_on_zero_hp(self, tmp_path):
        s, _ = self._fresh(tmp_path)
        s.hp = 0
        assert _check_termination(s) == "dead"

    def test_insane_on_zero_san(self, tmp_path):
        s, _ = self._fresh(tmp_path)
        s.san = 0
        assert _check_termination(s) == "insane"

    def test_max_turns(self, tmp_path):
        s, _ = self._fresh(tmp_path)
        for i in range(s.max_turns):
            s.add_turn("keeper", f"k{i}", meta={})
        assert _check_termination(s) == "max_turns"

    def test_day7_resolved_needs_enough_turns(self, tmp_path):
        s, _ = self._fresh(tmp_path)
        s.current_day = 7
        # 回合不足 14 时不算 resolved
        assert _check_termination(s) is None
        for i in range(14):
            s.add_turn("keeper" if i % 2 == 0 else "investigator", f"t{i}", meta={})
        s.next_role = "keeper"
        assert _check_termination(s) == "day7_resolved"


class TestApplyStatePatch:
    def test_san_clamped_and_clue_added(self, tmp_path):
        cs = CocRunStore(tmp_path / "coc")
        s = init_run(cs, 1, {"vid": "v1"})
        applied = apply_state_patch(s, {"san": 150, "clue": "地上有半枚铜纽扣",
                                         "cost_note": "右耳开始听不清高频"}, cs)
        assert applied["san"] == 99           # clamped
        assert s.san == 99
        assert s.clues_found[-1]["text"] == "地上有半枚铜纽扣"
        assert s.obsession_cost_notes[-1] == "右耳开始听不清高频"
        # 应有一条 system turn
        assert any(t.role == "system" for t in s.turns[-2:])

    def test_npc_status_update(self, tmp_path):
        cs = CocRunStore(tmp_path / "coc")
        s = init_run(cs, 1, {"vid": "v1"})
        apply_state_patch(s, {"npcs": {"借伞婆婆": {"status": "dead"}}}, cs)
        assert s.npcs["借伞婆婆"]["status"] == "dead"


class TestIntervene:
    def test_author_intervene_preserves_next_role(self, tmp_path):
        cs = CocRunStore(tmp_path / "coc")
        s = init_run(cs, 1, {"vid": "v1"})
        s.next_role = "investigator"
        t = intervene(s, "这里让唐迟捡到伞。", cs, as_role="author")
        assert t.role == "author"
        assert s.next_role == "investigator"     # unchanged

    def test_investigator_intervene_switches_to_keeper(self, tmp_path):
        cs = CocRunStore(tmp_path / "coc")
        s = init_run(cs, 1, {"vid": "v1"})
        s.next_role = "investigator"
        t = intervene(s, "唐迟转身就跑。", cs, as_role="investigator")
        assert t.role == "investigator"
        assert t.meta.get("source") == "author"
        assert s.next_role == "keeper"


# ---------------------------------------------------------------------------
# API 端点（无 LLM 调用：只验证 start/get/intervene/patch/reset 链路）
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app.projects import ProjectStore
    from app.api import main as api

    monkeypatch.setattr(api, "_project_store", ProjectStore(tmp_path / "projects"))
    monkeypatch.setattr(api, "_project_marker_stores", {})
    monkeypatch.setattr(api, "_project_entity_stores", {})
    monkeypatch.setattr(api, "_project_book_stores", {})
    monkeypatch.setattr(api, "_project_field_stores", {})
    monkeypatch.setattr(api, "_project_field_run_stores", {})
    monkeypatch.setattr(api, "_project_coc_run_stores", {})
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


def _setup_field(client) -> str:
    r = client.post("/api/projects", json={"title": "CocTest", "genre": "克苏鲁无限流"})
    pid = r.json()["id"]
    obit = {
        "field_no": 1, "title": "青铜巷雨夜", "location": "榕城",
        "timer": "七日", "death_condition": "第七日亡",
        "entrants": [{"name": "唐祈", "role": "送伞人"}],
        "entity_hint": "借伞婆婆",
    }
    client.post(f"/api/projects/{pid}/fields",
                json={"field_no": 1, "title": "青铜巷雨夜", "obituary_card": obit})
    # 手动塞一个 adapt_scenario 选定候选，绕过 LLM
    from app.api.main import _field_run_for
    from app.fields import NodeArtifact
    rs = _field_run_for(pid)
    art = NodeArtifact(
        node_id="adapt_scenario", layer="adapt_scenario",
        raw_text="x",
        variants=[{"vid": "1", "text": "{}", "parsed": {"vid": "1", "title": "青铜巷雨夜"}}],
        chosen_vid="1",
    )
    rs.save(1, art)
    return pid


class TestCocApi:
    def test_start_requires_adapt(self, api_client):
        client = api_client
        r = client.post("/api/projects", json={"title": "X", "genre": "a"})
        pid = r.json()["id"]
        client.post(f"/api/projects/{pid}/fields",
                    json={"field_no": 1, "title": "y",
                          "obituary_card": {"field_no": 1, "title": "y",
                                            "entrants": [{"name": "a"}],
                                            "timer": "7日", "death_condition": "x",
                                            "entity_hint": "z", "location": "a"}})
        r = client.post(f"/api/projects/{pid}/fields/1/coc/start")
        assert r.status_code == 422

    def test_start_get_reset_flow(self, api_client):
        client = api_client
        pid = _setup_field(client)
        # get -> not exists
        r = client.get(f"/api/projects/{pid}/fields/1/coc")
        assert r.status_code == 200
        assert r.json()["exists"] is False

        # start
        r = client.post(f"/api/projects/{pid}/fields/1/coc/start",
                        json={"san": 80, "max_turns": 20})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["reset"] is True
        assert body["state"]["san"] == 80
        assert body["state"]["max_turns"] == 20
        assert body["state"]["status"] == "pending"

        # 再次 start 不重置
        r = client.post(f"/api/projects/{pid}/fields/1/coc/start")
        assert r.json()["reset"] is False

        # intervene
        r = client.post(f"/api/projects/{pid}/fields/1/coc/intervene",
                        json={"text": "让借伞婆婆先咳嗽一声。", "as_role": "author"})
        assert r.status_code == 200
        turns = r.json()["state"]["turns"]
        assert turns[-1]["role"] == "author"

        # patch
        r = client.patch(f"/api/projects/{pid}/fields/1/coc/state",
                         json={"san": 50, "current_day": 2, "clue": "墙角有伞"})
        assert r.status_code == 200
        s = r.json()["state"]
        assert s["san"] == 50
        assert s["current_day"] == 2
        assert s["clues_found"][-1]["text"] == "墙角有伞"

        # transcript
        r = client.post(f"/api/projects/{pid}/fields/1/coc/transcript")
        assert r.status_code == 200
        assert "KP" not in r.json()["transcript"] or "作者" in r.json()["transcript"]

        # reset
        r = client.delete(f"/api/projects/{pid}/fields/1/coc")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = client.get(f"/api/projects/{pid}/fields/1/coc")
        assert r.json()["exists"] is False


# ---------------------------------------------------------------------------
# Phase 4：批量仿真 variant-pool
# ---------------------------------------------------------------------------

class TestVariantSeed:
    def test_generated_seed_has_required_fields(self):
        from app.fields.coc_run import generate_variant_seed
        for _ in range(20):
            seed = generate_variant_seed()
            assert 2 <= len(seed["extra_npcs"]) <= 3
            for n in seed["extra_npcs"]:
                assert {"name", "secret", "stance"} <= set(n.keys())
            assert isinstance(seed["core_twist"], str) and seed["core_twist"]
            assert 2 <= len(seed["planted_foreshadows"]) <= 3
            assert 0 <= seed["san_start_penalty"] <= 10
            assert seed["ending_bias"] in {"bad", "bittersweet", "costly", "tradeoff", "narrow_escape"}
            assert seed["entrance_hook"]
            assert seed["tangchi_mood"]

    def test_fixed_rng_produces_deterministic_seed(self):
        from app.fields.coc_run import generate_variant_seed, NPC_POOL
        rng = random.Random(42)
        s1 = generate_variant_seed(rng)
        rng = random.Random(42)
        s2 = generate_variant_seed(rng)
        # 相同种子 → 同样的 extra_npcs 名字序列
        assert [n["name"] for n in s1["extra_npcs"]] == [n["name"] for n in s2["extra_npcs"]]
        assert s1["core_twist"] == s2["core_twist"]

    def test_render_variant_seed_contains_all_sections(self):
        from app.fields.coc_run import generate_variant_seed, render_variant_seed
        seed = generate_variant_seed(random.Random(1))
        txt = render_variant_seed(seed)
        assert "入场钩子" in txt
        assert "本场额外登场 NPC" in txt
        assert "核心反转" in txt
        assert "要埋的伏笔" in txt
        # 方案 B：结局改成「走向 + 授权 KP 推翻」，不再是硬性命令。
        # 旧文案「结局倾向」已随render_variant_seed 一起改掉。
        assert "结局走向" in txt
        assert "可以推翻" in txt
        # 所有 NPC 名字都出现
        for n in seed["extra_npcs"]:
            assert n["name"] in txt

    def test_five_seeds_are_diverse(self):
        from app.fields.coc_run import generate_variant_seed
        # 连续跑 20 个种子，结局倾向和核心反转要有多样性（避免全部一样）
        twists = {generate_variant_seed()["core_twist"] for _ in range(20)}
        biases = {generate_variant_seed()["ending_bias"] for _ in range(20)}
        assert len(twists) >= 3
        assert len(biases) >= 3


class TestSummaryCard:
    def test_summary_card_shape(self, tmp_path):
        from app.pipeline.coc_simulator import _summary_card_from_state, init_run
        from app.fields import CocRunStore
        cs = CocRunStore(tmp_path / "coc")
        state = init_run(cs, 1, {"vid": "v1"}, variant_seed={
            "extra_npcs": [{"name": "A", "secret": "x", "stance": "y"}],
            "core_twist": "t", "planted_foreshadows": ["f1", "f2"],
            "tangchi_mood": "怂", "entrance_hook": "h",
            "ending_bias": "bad", "ending_label": "黑暗结局",
            "san_start_penalty": 0, "obsession_start_uses": 0,
        }, session_index=3)
        state.san = 55; state.hp = 3
        card = _summary_card_from_state(state, "Day1 唐迟走进巷子。\nDay2 ...", 12)
        assert card["vid"] == "3"
        assert "候选 #3" in card["title"]
        assert card["ending_bias"] == "bad"
        assert card["final_san"] == 55
        assert card["final_hp"] == 3
        assert card["turns"] == 12
        assert card["extra_npcs"] == ["A"]
        assert card["one_liner"].startswith("Day1 唐迟走进巷子")


class TestPipelineNodeConfig:
    def test_coc_outcome_is_variant_pool_with_default_5(self):
        from app.fields import FIELD_PIPELINE_NODES, DEFAULT_VARIANT_COUNTS
        node = next(n for n in FIELD_PIPELINE_NODES if n[0] == "coc_outcome")
        assert node[2] is True           # is_variant
        assert DEFAULT_VARIANT_COUNTS["coc_outcome"] == 5
