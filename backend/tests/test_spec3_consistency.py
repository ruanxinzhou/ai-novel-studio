"""spec 3 · 一致性引擎测试：伏笔「推进」三态落地 + 角色知情边界。

对应设计文档：docs/superpowers/specs/2026-10-22-consistency-engine-design.md

三条核心断言：
1. 推进是**正交维度**不是第三状态——status 仍是 planted，历史不被抹掉；
2. 账龄改用「最近一次真正推进」，被反复提起却不推进的伏笔必须排到最前；
3. 知情边界只查显式声明，空值不报、短文本不报、困惑表现不报（宁漏勿误）。
"""
from __future__ import annotations

import pytest

from app.context.ledger import (
    FORESHADOW_DUE_AFTER,
    _foreshadow_anchor,
    _knowledge_seg,
    build_chapter_checklist,
)
from app.entities import EntityStore
from app.entities.store import FORESHADOW_OPS, OP_LABELS
from app.review.book_checks import (
    KNOWLEDGE_MIN_CHARS,
    _knowledge_keywords,
    check_knowledge_boundary,
)
from app.wizard import DeskStore


@pytest.fixture
def env(tmp_path):
    return SimpleEnv(tmp_path)


class SimpleEnv:
    def __init__(self, tmp_path):
        self.desk = DeskStore(tmp_path / "desk.json")
        self.entities = EntityStore(tmp_path / "entities.json")


def _long_text(n: int = KNOWLEDGE_MIN_CHARS + 200, seed: str = "他把杯子放回原处") -> str:
    """够长的成稿文本，用来越过 KNOWLEDGE_MIN_CHARS 门槛。"""
    body = (seed + "。") * 200
    return body[:n] if len(body) >= n else body + "补" * (n - len(body))


# ============================================================
# 一、伏笔推进：数据层
# ============================================================

def test_ops_enum_is_not_a_status():
    """FORESHADOW_OPS 与 ENTITY_STATUSES 并列但不相交——推进不是状态。"""
    from app.entities.store import ENTITY_STATUSES
    assert set(FORESHADOW_OPS) == {"plant", "advance", "pay", "drop"}
    assert "advance" not in ENTITY_STATUSES
    assert OP_LABELS["advance"] == "推进"


def test_advance_writes_last_advanced_ch(env):
    """测试1：推进落库正确。"""
    e = env.entities.add("foreshadow", "鞋柜第二层的信封", summary="没拆的旧信", chapter=3)
    env.entities.note_foreshadow_advance(e.id, 7)
    e = env.entities.find("foreshadow", "鞋柜第二层的信封")
    assert e.last_advanced_ch == 7
    assert e.advance_count == 1


def test_advance_does_not_change_status(env):
    """测试2：推进不改变 status——仍是 planted，不是 paid。"""
    e = env.entities.add("foreshadow", "猫眼里的倒影", chapter=5)
    env.entities.note_foreshadow_advance(e.id, 6)
    env.entities.note_foreshadow_advance(e.id, 9)
    e = env.entities.find("foreshadow", "猫眼里的倒影")
    assert e.status == "planted"
    assert e.advance_count == 2 and e.last_advanced_ch == 9


def test_payoff_keeps_advance_history(env):
    """测试3：回收后推进历史不被抹掉（否则账龄会跳回 0 变成「刚埋」）。"""
    e = env.entities.add("foreshadow", "青铜镜来历", chapter=1)
    env.entities.note_foreshadow_advance(e.id, 4)
    env.entities.update(e.id, status="paid")
    e = env.entities.find("foreshadow", "青铜镜来历")
    assert e.status == "paid"
    assert e.last_advanced_ch == 4 and e.advance_count == 1


def test_advance_is_idempotent_per_chapter(env):
    """同章重复推进只累加一次——重跑定稿不能把推进次数刷爆。"""
    e = env.entities.add("foreshadow", "墙上的划痕", chapter=2)
    env.entities.note_foreshadow_advance(e.id, 8)
    env.entities.note_foreshadow_advance(e.id, 8)
    env.entities.note_foreshadow_advance(e.id, 8)
    e = env.entities.find("foreshadow", "墙上的划痕")
    assert e.advance_count == 1


def test_apply_diff_advances_bucket(env):
    """apply_diff 的 foreshadow_advances 分支真的写库（旧代码声明了但从不读）。"""
    env.entities.add("foreshadow", "重复的脚步", chapter=1)
    diff = env.entities.add_pending_diff(StateDiffLite(
        chapter=6, advances=[("重复的脚步", "")]))
    res = env.entities.apply_diff(diff.id, {"foreshadow_advances": {0: True}})
    e = env.entities.find("foreshadow", "重复的脚步")
    assert e.last_advanced_ch == 6 and e.advance_count == 1
    assert e.status == "planted"
    assert res["advanced"] and res["advanced"][0]["name"] == "重复的脚步"


def test_apply_diff_advance_unselected_is_noop(env):
    """没勾选的推进项不写库（selections 语义）。"""
    env.entities.add("foreshadow", "没被勾的伏笔", chapter=1)
    diff = env.entities.add_pending_diff(StateDiffLite(
        chapter=6, advances=[("没被勾的伏笔", "")]))
    env.entities.apply_diff(diff.id, {})
    e = env.entities.find("foreshadow", "没被勾的伏笔")
    assert e.last_advanced_ch == 0 and e.advance_count == 0


def test_apply_diff_advance_unknown_name_skipped(env):
    """AI 幻觉出一个不存在的伏笔名 → 跳过，不能凭空建实体。"""
    diff = env.entities.add_pending_diff(StateDiffLite(
        chapter=6, advances=[("根本没埋过的伏笔", "")]))
    res = env.entities.apply_diff(diff.id, {"foreshadow_advances": {0: True}})
    assert res["advanced"] == []
    assert env.entities.find("foreshadow", "根本没埋过的伏笔") is None


def test_shift_chapter_refs_moves_last_advanced(env):
    """删章后 last_advanced_ch 必须跟着偏移，否则账龄凭空多一章。"""
    e = env.entities.add("foreshadow", "要被删章的伏笔", chapter=5)
    env.entities.note_foreshadow_advance(e.id, 8)
    env.entities.shift_chapter_refs(from_no=6, delta=-1)
    e = env.entities.find("foreshadow", "要被删章的伏笔")
    assert e.last_advanced_ch == 7


def test_state_diff_roundtrip_keeps_advances(env):
    """StateDiff 序列化/反序列化保住 foreshadow_advances（前端回传不会丢字段）。"""
    from app.entities.store import ForeshadowSuggestion, StateDiff
    d = StateDiff(id="x", source="manual", chapter=3)
    d.foreshadow_advances = [ForeshadowSuggestion(name="倒影", kind="advanced")]
    back = StateDiff.from_dict(d.as_dict())
    assert [a.name for a in back.foreshadow_advances] == ["倒影"]


# ============================================================
# 二、账龄排序：用「最近一次真正推进」
# ============================================================

def test_anchor_prefers_last_advanced_over_first_appearance(env):
    """测试4的核心：老坑但刚推进过 → 账龄小；新坑但从没推进 → 排最前。"""
    old = env.entities.add("foreshadow", "老坑", chapter=1)
    for ch in range(2, 15):                    # 每章都被提到一句
        env.entities.record_appearance(old.id, ch)
    env.entities.note_foreshadow_advance(old.id, 14)

    new = env.entities.add("foreshadow", "新坑", chapter=13)
    env.entities.note_foreshadow_advance(new.id, 13)
    fresh = env.entities.add("foreshadow", "从没推进过的坑", chapter=13)

    o = env.entities.find("foreshadow", "老坑")
    n = env.entities.find("foreshadow", "新坑")
    f = env.entities.find("foreshadow", "从没推进过的坑")
    assert _foreshadow_anchor(o) == 14
    assert _foreshadow_anchor(n) == 13
    assert _foreshadow_anchor(f) == 13
    # 关键：老坑每章都出场，但旧排序 min(appearances)=1 会算成账龄 13 章，
    # 排到最前并被当成硬债务；按推进算它账龄只有 1 章，不该被催。
    assert o.appearances and min(o.appearances) == 1


def test_checklist_orders_never_advanced_first(env):
    """从未推进的伏笔必须排在到期清单最前，哪怕它埋得更晚。

    这正是旧排序的致命处：老坑 appearances 从第 1 章起每章都有，
    min(appearances)=1 → 账龄 13 章 → 被当成最该处理的硬债务，
    而它在第 13 章刚推进过；反倒真正死掉的第 2 章埋坑没人管。
    """
    env.desk.record(1, "plot", "一")
    env.desk.record(13, "plot", "一三")
    old = env.entities.add("foreshadow", "被反复提起的老坑", chapter=1)
    for ch in range(1, 14):
        env.entities.record_appearance(old.id, ch)
    env.entities.note_foreshadow_advance(old.id, 13)
    dead = env.entities.add("foreshadow", "从没推进的坑", chapter=2)

    m = build_chapter_checklist(env.entities, env.desk, 14, 30)
    assert m is not None
    body = m.content
    assert "从没推进的坑" in body and "被反复提起的老坑" in body
    # 老坑账龄 1 章（第 13 章推进过），死坑账龄 12 章（从未推进）→ 死坑排前
    assert "被反复提起的老坑（第1章埋，已静默 1 章" in body
    assert "从没推进的坑（第2章埋，已静默 12 章，从未推进，本章必须处理）" in body
    assert body.index("从没推进的坑") < body.index("被反复提起的老坑"), \
        "从未推进的伏笔必须排在被反复提起但刚推进过的前面"
    assert dead.status == "planted"


def test_checklist_wording_distinguishes_two_states(env):
    """测试5：文案区分「从未推进」与「N 章前推进过」。"""
    env.desk.record(1, "plot", "一")
    env.desk.record(5, "plot", "五")
    env.desk.record(12, "plot", "一二")
    a = env.entities.add("foreshadow", "信封", chapter=3)
    env.entities.record_appearance(a.id, 9)
    b = env.entities.add("foreshadow", "倒影", chapter=5)
    env.entities.note_foreshadow_advance(b.id, 5)
    env.entities.note_foreshadow_advance(b.id, 11)

    m = build_chapter_checklist(env.entities, env.desk, 13, 30)
    assert "从未推进" in m.content
    assert "上次推进在第11章（累计 2 次）" in m.content
    assert "已静默" in m.content and "本章必须处理" in m.content
    assert f"静默 ≥{FORESHADOW_DUE_AFTER} 章" in m.content


def test_state_ledger_marks_never_advanced(env):
    """实体账本（续写大纲注入）也用同一口径标注推进状态，且死坑排在前面。"""
    from app.context.ledger import build_state_materials

    class FakeProject:
        def current(self, pid, stage):
            return None

    env.desk.record(1, "plot", "一")
    pushed = env.entities.add("foreshadow", "倒影", summary="猫眼反光", chapter=1)
    env.entities.note_foreshadow_advance(pushed.id, 1)
    dead = env.entities.add("foreshadow", "信封", summary="没拆的旧信", chapter=1)

    mats = build_state_materials(FakeProject(), env.entities, env.desk, "p1")
    ledger = next(m for m in mats if m.source_id == "state:entities")
    assert "倒影：猫眼反光（第1章埋，第1章推进过）" in ledger.content
    assert "信封：没拆的旧信（第1章埋，从未推进）" in ledger.content
    # 同一埋设章 → 锚点相同（都退回到埋设章），按名字定序
    assert ledger.content.index("从未推进") < ledger.content.index("倒影")


# ============================================================
# 三、知情边界：注入 + 检查
# ============================================================

def test_knowledge_attrs_whitelisted():
    """knows / blinded_to 进 ENTITY_ATTRS 白名单，UI 与抽取都能用。"""
    from app.entities.store import ENTITY_ATTRS
    attrs = ENTITY_ATTRS["character"]
    assert "knows" in attrs and "blinded_to" in attrs
    assert attrs["blinded_to"].startswith("不知情")


def test_knowledge_seg_only_when_declared(env):
    """没声明的角色不加知情段（不自动推断「他应该知道什么」）。"""
    class E:
        name = "路人"
        state_attrs = {}
    assert _knowledge_seg(E()) == ""
    E.state_attrs = {"knows": "唐迟是第七个住客"}
    assert _knowledge_seg(E()) == "（已知：唐迟是第七个住客）"
    E.state_attrs = {"knows": "他是谁", "blinded_to": "猫眼里的东西"}
    assert _knowledge_seg(E()) == "（已知：他是谁；不知情：猫眼里的东西）"


def test_knowledge_seg_ignores_blank_values(env):
    class E:
        name = "路人"
        state_attrs = {"knows": "   ", "blinded_to": ""}
    assert _knowledge_seg(E()) == ""


def test_knowledge_injected_into_checklist(env):
    """测试4段：知情边界进开章任务书（写的时候就该知道，不是事后才报警）。"""
    env.desk.record(1, "plot", "一")
    c = env.entities.add("character", "老陈", summary="看门人", chapter=1)
    c.state_attrs["knows"] = "唐迟是第七个住客"
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    env.entities._flush()

    m = build_chapter_checklist(env.entities, env.desk, 2, 30)
    assert "老陈" in m.content
    assert "已知：唐迟是第七个住客" in m.content
    assert "不知情：猫眼里的东西" in m.content


def test_knowledge_keywords_splitting():
    """blinded_to 自由文本按顿号/逗号/分号切分，长度越界的不查。"""
    kws = _knowledge_keywords("猫眼里的东西、第七封信，还有 唐迟的身份")
    assert "猫眼里的东西" in kws and "第七封信" in kws and "唐迟的身份" in kws
    assert _knowledge_keywords("命") == []            # 单字不查
    assert _knowledge_keywords("很长很长很长很长很长很长很长的一句话内容") == []  # 超长不查
    assert _knowledge_keywords("") == []
    assert _knowledge_keywords("   ") == []


def test_knowledge_violation_detected(env):
    """测试6：被声明不知情的角色表现出知道 → 报警。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    env.desk.record(1, "draft", _long_text(
        seed="老陈其实早就知道猫眼里的东西是什么"))
    env.desk.record(1, "polish", _long_text(
        seed="老陈其实早就知道猫眼里的东西是什么"))

    msg = check_knowledge_boundary(env.desk, 1, entity_store=env.entities)
    assert msg is not None
    assert "知情边界" in msg and "老陈" in msg and "猫眼里的东西" in msg


def test_knowledge_no_false_positive_when_confused(env):
    """不知情角色的合理表现（困惑/追问）必须放过。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    seed = ("老陈不知道猫眼里的东西是什么，"
            "他一头雾水地看着门，压根不明白刚才那声响")
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))

    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_no_false_positive_other_character_knows(env):
    """同一个秘密别的角色知道是合法的——知识边界是「谁」的边界。"""
    knower = env.entities.add("character", "唐迟", chapter=1)
    blind = env.entities.add("character", "老陈", chapter=1)
    blind.state_attrs["blinded_to"] = "猫眼里的东西"
    seed = "唐迟早就知道猫眼里的东西是什么，老陈在旁边听着"
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))

    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_no_false_positive_empty_declaration(env):
    """测试7：空 blinded_to 不检查。"""
    env.entities.add("character", "老陈", chapter=1)     # 无 state_attrs
    seed = "老陈其实早就知道猫眼里的东西是什么"
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))
    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_short_text_not_flagged(env):
    """测试8：半成品（<600 字）不误报。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    env.desk.record(1, "draft", "老陈早就知道猫眼里的东西")
    assert len("老陈早就知道猫眼里的东西") < KNOWLEDGE_MIN_CHARS
    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_none_without_entity_store(env):
    """没传实体库 → 直接放行（旧项目兼容）。"""
    env.desk.record(1, "draft", _long_text(seed="老陈早就知道猫眼里的东西"))
    assert check_knowledge_boundary(env.desk, 1, entity_store=None) is None


def test_knowledge_keyword_absent_no_flag(env):
    """关键词根本没出现 → 不报（不检查「应该知道」）。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    seed = "老陈早就知道隔壁的钥匙放在哪里，他把门锁上了"
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))
    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_check_survives_garbage_attr(env):
    """脏数据（None / 非字符串）不能让检查炸掉。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = None
    c.state_attrs["knows"] = 123
    seed = "老陈早就知道猫眼里的东西是什么"
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))
    assert check_knowledge_boundary(env.desk, 1, entity_store=env.entities) is None


def test_knowledge_warning_never_blocks_review(env):
    """知情边界是软提醒，不进 book_warnings 以外的任何硬门禁。"""
    c = env.entities.add("character", "老陈", chapter=1)
    c.state_attrs["blinded_to"] = "猫眼里的东西"
    seed = "老陈早就知道猫眼里的东西是什么"
    env.desk.record(1, "draft", _long_text(seed=seed))
    env.desk.record(1, "polish", _long_text(seed=seed))
    msg = check_knowledge_boundary(env.desk, 1, entity_store=env.entities)
    assert msg is not None and "软提醒" in msg


# ---- helper ----

def StateDiffLite(chapter: int, advances: list[tuple[str, str]]):
    from app.entities.store import ForeshadowSuggestion, StateDiff
    return StateDiff(
        id="dx-adv", source="manual", chapter=chapter,
        foreshadow_advances=[
            ForeshadowSuggestion(name=n, summary=s, kind="advanced")
            for n, s in advances
        ],
    )