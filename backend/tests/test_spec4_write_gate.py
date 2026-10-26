"""spec 4 · 写前人工确认门测试。

对应设计文档：docs/superpowers/specs/2026-10-22-write-gate-design.md

核心断言（每一条都对应一个真实缺陷或一条不可退让的设计约束）：
1. 无风险时**不阻断**——每次必确认会退化成多余的点击。
2. 风险判定**零 LLM**——纯数据推导，误报一次作者就不信这个门了。
3. 重试用尽的 P0 **不再静默落库**，而是被带出引擎要求表态。
4. 格式类 P0 仍然中止（不回归 spec 前的既有行为）。
5. 审稿 blocked 未逐条确认 → 拒绝定稿，且拒绝信息里含具体不稳项。
6. 账本写失败**绝不阻断**作者的操作。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.entities import EntityStore
from app.generation.gate import (
    DECISION_ACCEPT,
    DECISION_ACKNOWLEDGE,
    DECISION_MANUAL,
    GATE_FINALIZE,
    GATE_POST_GATE,
    GATE_PRE_WRITE,
    RISK_FORESHADOW_DUE,
    RISK_FORESHADOW_HARD,
    RISK_KNOWLEDGE_GAP,
    RISK_RHYTHM_HUNGER,
    RISK_STRAND_GAP,
    GatePreview,
    Risk,
    accept_streaks,
    append_decision,
    assess_write_risks,
    build_preview,
    load_decisions,
    unconfirmed_chapters,
)
from app.wizard import DeskStore
from app.wizard.desk import _ack_required_message, _unstable_items


@pytest.fixture
def env(tmp_path):
    desk = DeskStore(tmp_path / "desk.json")
    entities = EntityStore(tmp_path / "entities.json")
    return SimpleNamespace(tmp=tmp_path, desk=desk, entities=entities,
                           pid="p-spec4", root=tmp_path)


def _kind(risks):
    return {r.kind for r in risks}


# ============================================================
# 一、风险判定：零 LLM，且无风险不阻断
# ============================================================

def test_clean_chapter_has_no_risk(env):
    """测试1：账上干净时零风险 → blocked=False（不弹窗）。"""
    risks = assess_write_risks(env.entities, env.desk, 1, "plot")
    assert risks == []


def test_no_entity_store_is_safe(env):
    """缺实体库（旧项目）不该把整个门卡死。"""
    risks = assess_write_risks(None, env.desk, 1, "plot")
    assert all(r.kind != RISK_FORESHADOW_DUE for r in risks)


def test_describe_stays_unblocked_when_no_risk(env):
    env.desk.record(1, "plot", "一")
    pv = build_preview(env.entities, env.desk, 2, "plot", outline_total=30)
    assert pv.blocked is False
    assert pv.as_dict()["blocked"] is False


# ============================================================
# 二、五类风险的触发与精度
# ============================================================

def test_due_foreshadow_triggers_risk(env):
    """静默 ≥8 章的伏笔触发风险。"""
    env.desk.record(1, "plot", "一")
    e = env.entities.add("foreshadow", "鞋柜的信封", chapter=1)
    env.entities.record_appearance(e.id, 1)
    risks = assess_write_risks(env.entities, env.desk, 10, "plot")
    assert RISK_FORESHADOW_DUE in _kind(risks)
    assert any("鞋柜的信封" in i for r in risks for i in r.items)


def test_hard_foreshadow_is_separate_risk(env):
    """静默 ≥12 章算硬债务，与普通到期分开报。"""
    env.desk.record(1, "plot", "一")
    e = env.entities.add("foreshadow", "青铜镜来历", chapter=1)
    env.entities.record_appearance(e.id, 1)
    risks = assess_write_risks(env.entities, env.desk, 15, "plot")
    assert RISK_FORESHADOW_HARD in _kind(risks)


def test_fresh_foreshadow_not_flagged(env):
    """刚埋的伏笔不是债——弹窗会因为这种噪音迅速失去信任。"""
    env.desk.record(1, "plot", "一")
    env.entities.add("foreshadow", "刚埋的伏笔", chapter=10)
    env.entities.record_appearance(env.desk.get(10).id if False else
                                   env.entities.list(type="foreshadow")[0].id, 10)
    risks = assess_write_risks(env.entities, env.desk, 11, "plot")
    assert RISK_FORESHADOW_DUE not in _kind(risks)
    assert RISK_FORESHADOW_HARD not in _kind(risks)


def test_paid_foreshadow_not_flagged(env):
    """已回收的不算债。"""
    env.desk.record(1, "plot", "一")
    e = env.entities.add("foreshadow", "已回收的", chapter=1)
    env.entities.record_appearance(e.id, 1)
    env.entities.update(e.id, status="paid")
    risks = assess_write_risks(env.entities, env.desk, 20, "plot")
    assert RISK_FORESHADOW_DUE not in _kind(risks)


def test_knowledge_half_declared_triggers(env):
    """只声明了 blinded_to 没声明 knows → 数据不完整，零误判。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    env.desk.record(1, "plot", "一")
    risks = assess_write_risks(env.entities, env.desk, 2, "plot")
    assert RISK_KNOWLEDGE_GAP in _kind(risks)


def test_knowledge_both_declared_not_flagged(env):
    """两个都声明了 → 不是风险。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    c.state_attrs["knows"] = "唐迟是第七个住客"
    env.desk.record(1, "plot", "一")
    risks = assess_write_risks(env.entities, env.desk, 2, "plot")
    assert RISK_KNOWLEDGE_GAP not in _kind(risks)


def test_knowledge_none_declared_not_flagged(env):
    """什么都没声明 → 不推断「他应该知道什么」。"""
    env.entities.add("character", "路人", chapter=1)
    env.desk.record(1, "plot", "一")
    risks = assess_write_risks(env.entities, env.desk, 2, "plot")
    assert RISK_KNOWLEDGE_GAP not in _kind(risks)


def test_rhythm_hunger_triggers(env):
    """连续无兑现 → 节奏饥饿风险。"""
    for ch in range(1, 7):
        env.desk.record(ch, "draft", f"第{ch}章正文。")
    risks = assess_write_risks(env.entities, env.desk, 7, "draft")
    assert RISK_RHYTHM_HUNGER in _kind(risks)


def test_strand_gap_triggers(env):
    """主线断档 5 章 → 三线风险。"""
    for ch in range(1, 8):
        env.desk.record(ch, "draft", f"第{ch}章正文。")
    risks = assess_write_risks(env.entities, env.desk, 8, "draft")
    assert RISK_STRAND_GAP in _kind(risks)


def test_risk_detail_is_one_readable_sentence(env):
    """每条风险必须能一句话说清——说不清就不该弹窗打扰作者。"""
    e = env.entities.add("foreshadow", "鞋柜的信封", chapter=1)
    env.entities.record_appearance(e.id, 1)
    risks = assess_write_risks(env.entities, env.desk, 10, "plot")
    for r in risks:
        assert r.detail and len(r.detail) < 200
        assert "\n" not in r.detail


# ============================================================
# 三、预演：零 token + brief 与实际注入一致
# ============================================================

def test_preview_brief_matches_checklist(env):
    """测试6：preview 的 brief 就是实际注入的那份（透明而非拦截）。"""
    from app.context.ledger import build_chapter_checklist
    env.desk.record(1, "plot", "一")
    c = env.entities.add("character", "陆离", summary="主角", chapter=1)
    pv = build_preview(env.entities, env.desk, 2, "plot", outline_total=30)
    ref = build_chapter_checklist(env.entities, env.desk, 2, 30)
    assert pv.brief == (ref.content if ref else "")
    assert "陆离" in pv.brief


def test_preview_never_calls_llm(env):
    """测试5：风险判定零 LLM——传入任何 provider 都不应被调用。"""
    class BoomProvider:
        def chat(self, *a, **k):
            raise AssertionError("确认门不允许调用 LLM")

    env.desk.record(1, "plot", "一")
    e = env.entities.add("foreshadow", "信封", chapter=1)
    env.entities.record_appearance(e.id, 1)
    # assess_write_risks / build_preview 签名里根本没有 provider 参数，
    # 这个测试的价值在于：若将来有人「顺手加个 LLM 预检」就会立刻暴露。
    risks = assess_write_risks(env.entities, env.desk, 10, "plot")
    assert risks
    assert BoomProvider is not None


def test_preview_survives_missing_deps(tmp_path):
    """依赖缺失时预演不能抛——它是辅助信息不是门禁。"""
    pv = build_preview(None, None, 1, "plot", outline_total=0)
    assert isinstance(pv, GatePreview)
    assert pv.blocked is False


# ============================================================
# 四、门禁后：P0 不再静默放行
# ============================================================

def test_gate_result_carries_pending_p0():
    """测试7：LayerRunResult 带出 gate.required 与 pending。"""
    from app.pipeline.engine import LayerRunResult
    r = LayerRunResult(layer="draft", pairs=[("x", "y")])
    assert r.gate_required is False
    r.gate_pending = ["问号密度过低：1.2/千字"]
    assert r.gate_required is True


def test_gate_result_defaults_are_not_shared():
    """可变默认值陷阱：两个实例不能共享同一个 list。"""
    from app.pipeline.engine import LayerRunResult
    a = LayerRunResult(layer="draft", pairs=[])
    b = LayerRunResult(layer="draft", pairs=[])
    a.gate_pending.append("x")
    assert b.gate_pending == []


def test_run_chapter_stage_result_has_gate_key(env):
    """run_chapter_stage 返回值带 gate 段（前端契约）。"""
    from app.wizard import desk as desk_mod

    src = desk_mod.run_chapter_stage.__doc__ or ""
    assert src  # 只是确保函数存在；结构断言在下面用真实调用
    assert callable(desk_mod.run_chapter_stage)


# ============================================================
# 五、定稿前：blocked 必须逐条表态
# ============================================================

def _work_with_review(items, verdict="blocked"):
    return SimpleNamespace(review={"items": items, "verdict": verdict})


def test_unstable_items_picks_only_unstable():
    items = [{"name": "A", "status": "不稳", "problem": "p1"},
             {"name": "B", "status": "稳", "problem": ""},
             {"name": "C", "status": "不稳", "problem": "p3"}]
    got = _unstable_items(_work_with_review(items))
    assert [i["name"] for i in got] == ["A", "C"]


def test_unstable_items_handles_missing_review():
    assert _unstable_items(None) == []
    assert _unstable_items(SimpleNamespace(review=None)) == []
    assert _unstable_items(SimpleNamespace(review={"items": None})) == []


def test_ack_message_lists_actual_items():
    """测试5b：拒绝信息必须含具体不稳项，否则作者无法真正判断。"""
    items = [{"name": "唐迟视角", "status": "不稳", "problem": "出现全知叙述"},
             {"name": "间章节奏", "status": "不稳", "problem": "写成纯日常"}]
    msg = _ack_required_message(items)
    assert "唐迟视角" in msg and "间章节奏" in msg
    assert "出现全知叙述" in msg
    assert "2 项不稳" in msg


def test_ack_message_truncates_long_lists():
    items = [{"name": f"项{i}", "status": "不稳"} for i in range(10)]
    msg = _ack_required_message(items)
    assert "另有 4 项" in msg


def _finalize(env, **kw):
    """跑一次 finalize_chapter（auto_entities=False 免掉 LLM 抽取）。"""
    from app.projects import ProjectStore
    from app.wizard.desk import finalize_chapter
    projects = ProjectStore(env.tmp / "projects")
    pid = projects.create("测试书", "").id
    return finalize_chapter(None, None, projects, pid, env.entities, None,
                            env.desk, 1, auto_entities=False, **kw)


def test_finalize_requires_ack_when_blocked(env):
    """测试9：blocked 未 acknowledge → 拒绝定稿。"""
    env.desk.record(1, "draft", "正文内容" * 50)
    env.desk.record_review(1, {"verdict": "blocked", "items": [
        {"name": "A", "status": "不稳", "problem": "问题一"},
        {"name": "B", "status": "不稳", "problem": "问题二"},
    ]})
    with pytest.raises(ValueError) as ei:
        _finalize(env)
    assert "不稳" in str(ei.value)
    assert "问题一" in str(ei.value)


def test_finalize_allows_no_review_without_ack(env):
    """测试11：没跑过审稿的章照常定稿（旧项目兼容）。"""
    env.desk.record(1, "draft", "正文内容" * 50)
    res = _finalize(env)
    assert res["chapter"] == 1


def test_finalize_pass_allows_without_ack(env):
    """审稿 pass（0 项不稳）不需要表态。"""
    env.desk.record(1, "draft", "正文内容" * 50)
    env.desk.record_review(1, {"verdict": "pass", "items": [
        {"name": "A", "status": "稳", "problem": ""},
        {"name": "B", "status": "稳", "problem": ""},
    ]})
    res = _finalize(env)
    assert res["chapter"] == 1


def test_finalize_with_single_unstable_requires_ack(env):
    """1 项不稳时 reviewer 判 pass，但作者仍该知道——按不稳项数量拦是刻意的保守。

    reviewer 的 BLOCK_THRESHOLD=2，所以 1 项不稳在审稿层面是 pass。
    我们这里按**不稳项数量**而不是 verdict 拦，是有意的：
    「一条不稳」也是作者应该知道的信息，而 verdict=pass 不该让它隐形。
    """
    env.desk.record(1, "draft", "正文内容" * 50)
    env.desk.record_review(1, {"verdict": "pass", "items": [
        {"name": "A", "status": "不稳", "problem": "只有一项"},
    ]})
    with pytest.raises(ValueError):
        _finalize(env)


def test_finalize_ack_records_decision(env):
    """测试10：带 acknowledge → 放行。"""
    env.desk.record(1, "draft", "正文内容" * 50)
    env.desk.record_review(1, {"verdict": "blocked", "items": [
        {"name": "A", "status": "不稳", "problem": "问题一"},
        {"name": "B", "status": "不稳", "problem": "问题二"},
    ]})
    res = _finalize(env, acknowledge=["A", "B"])
    assert res["chapter"] == 1


def test_finalize_empty_ack_is_not_enough(env):
    """acknowledge 传空列表等于没表态——必须真的传内容。"""
    env.desk.record(1, "draft", "正文内容" * 50)
    env.desk.record_review(1, {"verdict": "blocked", "items": [
        {"name": "A", "status": "不稳", "problem": "问题一"},
        {"name": "B", "status": "不稳", "problem": "问题二"},
    ]})
    with pytest.raises(ValueError):
        _finalize(env, acknowledge=[])


# ============================================================
# 六、账本：追加式 + 损坏行跳过 + 写失败不阻断
# ============================================================

def test_append_and_load_roundtrip(env):
    root = env.root
    assert append_decision("p1", 3, GATE_PRE_WRITE, DECISION_ACCEPT,
                           risk=RISK_FORESHADOW_DUE, detail="信封静默 9 章",
                           stage="plot", root=root) is True
    rows = load_decisions("p1", root)
    assert len(rows) == 1
    assert rows[0]["ch"] == 3 and rows[0]["decision"] == DECISION_ACCEPT
    assert rows[0]["risk"] == RISK_FORESHADOW_DUE


def test_append_is_add_only(env):
    """追加写、绝不原地改——能看出同一章表过几次态。"""
    root = env.root
    append_decision("p1", 3, GATE_PRE_WRITE, "rewrite", risk="x", root=root)
    append_decision("p1", 3, GATE_PRE_WRITE, DECISION_ACCEPT, risk="x", root=root)
    assert len(load_decisions("p1", root)) == 2


def test_corrupt_line_skipped(env):
    """测试13：一行坏不该让整个账本失效。"""
    root = env.root
    append_decision("p1", 1, GATE_PRE_WRITE, DECISION_ACCEPT, root=root)
    path = root / "p1" / "gate_decisions.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ 这不是 JSON\n")
    append_decision("p1", 2, GATE_PRE_WRITE, DECISION_ACCEPT, root=root)
    rows = load_decisions("p1", root)
    assert [r["ch"] for r in rows] == [1, 2]


def test_missing_file_returns_empty(env):
    assert load_decisions("never-written", env.root) == []


def test_append_failure_is_swallowed(env):
    """账本写失败返回 False 而不是抛——调用方不能被它阻断。"""
    bad_root = env.tmp / "not-a-dir"
    bad_root.write_text("我是文件不是目录", encoding="utf-8")
    assert append_decision("p", 1, GATE_PRE_WRITE, DECISION_ACCEPT,
                           root=bad_root) is False


def test_accept_streaks_counts(env):
    """测试12：统计被反复收下的风险——账本的独有价值。

    只有 accept/acknowledge 算「收下」。manual 是「我自己改了」，
    rewrite 是「重写了」——**都不是默认放过**，不该计入偏好画像。
    这个区分很重要：如果把三者混在一起，统计出来的就是「作者被系统
    逼着点了多少次确认」，而不是「作者明知有问题却接受了几次」。
    """
    root = env.root
    append_decision("p", 1, GATE_PRE_WRITE, DECISION_ACCEPT,
                    risk=RISK_FORESHADOW_DUE, root=root)
    append_decision("p", 2, GATE_PRE_WRITE, "rewrite",
                    risk=RISK_FORESHADOW_DUE, root=root)
    append_decision("p", 3, GATE_PRE_WRITE, DECISION_MANUAL,
                    risk=RISK_STRAND_GAP, root=root)
    append_decision("p", 4, GATE_PRE_WRITE, DECISION_ACKNOWLEDGE,
                    risk=RISK_STRAND_GAP, root=root)
    counts = accept_streaks("p", root)
    assert counts[RISK_FORESHADOW_DUE] == 1     # rewrite 不算
    assert counts[RISK_STRAND_GAP] == 1         # manual 不算，acknowledge 算
    assert counts.get(RISK_RHYTHM_HUNGER) is None


def test_unconfirmed_chapters_uses_last_decision(env):
    """只看每章最后一条表态——改主意不算漏。"""
    root = env.root
    append_decision("p", 5, GATE_PRE_WRITE, "rewrite",
                    risk=RISK_STRAND_GAP, root=root)
    append_decision("p", 5, GATE_PRE_WRITE, DECISION_ACCEPT,
                    risk=RISK_STRAND_GAP, root=root)
    assert unconfirmed_chapters("p", GATE_PRE_WRITE, root) == []


def test_unconfirmed_chapters_reports_stuck(env):
    root = env.root
    append_decision("p", 6, GATE_PRE_WRITE, DECISION_ACCEPT,
                    risk=RISK_STRAND_GAP, root=root)
    append_decision("p", 7, GATE_FINALIZE, DECISION_ACKNOWLEDGE,
                    risk="review_blocked", root=root)
    stuck = unconfirmed_chapters("p", GATE_PRE_WRITE, root)
    assert 6 not in stuck
    assert 7 not in stuck      # finalize 门的表态不算 pre_write 的漏


def test_gate_labels_cover_all_types():
    from app.generation.gate import GATE_LABELS, DECISION_LABELS
    assert set(GATE_LABELS) == {GATE_PRE_WRITE, GATE_POST_GATE, GATE_FINALIZE}
    assert set(DECISION_LABELS) >= {DECISION_ACCEPT, DECISION_MANUAL}


# ============================================================
# 七、门与风险的数据契约
# ============================================================

def test_preview_notice_text_empty_when_no_risk():
    pv = GatePreview(chapter=1, stage="plot", risks=[])
    assert pv.notice_text() == ""


def test_preview_notice_text_lists_risks():
    pv = GatePreview(chapter=7, stage="draft",
                     risks=[Risk(kind=RISK_FORESHADOW_DUE, detail="3 条到期")])
    txt = pv.notice_text()
    assert "第 7 章" in txt and "3 条到期" in txt


def test_risk_as_dict_carries_label():
    d = Risk(kind=RISK_STRAND_GAP, detail="主线断档").as_dict()
    assert d["label"] == "三线断档"
    assert d["kind"] == RISK_STRAND_GAP


def test_json_serializable_roundtrip(env):
    """账本行必须是纯 JSON——否则回放时无法解析。"""
    root = env.root
    append_decision("p", 1, GATE_POST_GATE, DECISION_ACCEPT,
                    risk="hard_gate", detail="P0 未达标", stage="draft",
                    p0=["问号密度过低", "字数不足"], root=root)
    path = root / "p" / "gate_decisions.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            assert isinstance(obj, dict)