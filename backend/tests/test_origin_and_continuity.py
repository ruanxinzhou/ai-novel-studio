"""原剧本注入 + 记忆碎片 + 跨章连贯（前2后2）测试。

三组：
- test_origin_novel.py：Volume.origin_novel 归一化与惰性兜底
- test_memory_fragments.py：记忆碎片数据结构与按天闸门
- test_continuity_window.py：跨章连贯窗口
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.book.volume import (
    Volume,
    build_origin_novel,
    origin_novel_of,
    _split_acts,
)
from app.context.ledger import (
    CONTINUITY_AFTER,
    CONTINUITY_BEFORE,
    _continuity_window_material,
    _memory_seg,
    build_chapter_checklist,
    build_origin_material,
)
from app.entities import EntityStore, MemoryFragment, memories_from_list

REPO_ROOT = Path(__file__).resolve().parents[2]
VOL2 = REPO_ROOT / "backend" / "data" / "projects" / "20261014-c6c432" / "volumes" / "2.json"


# ============================================================
# 一、origin_novel 归一化
# ============================================================

def _real_volume2() -> Volume:
    """用真实项目数据做测试——这是刻意的：归一化逻辑必须对真实形状成立，
    而不只是对构造出来的理想 dict 成立。"""
    return Volume.from_dict(json.loads(VOL2.read_text(encoding="utf-8")))


def test_lazy_rebuild_from_real_volume():
    """真实卷没有 origin_novel（旧数据），读时必须能从 selected_variant 重建。"""
    vol = _real_volume2()
    assert vol.origin_novel is None, "前置：真实卷确实没有 origin_novel"

    on = origin_novel_of(vol)
    assert on, "惰性重建失败"
    assert on["built_from"] == "lazy"
    # 实测：通稿 13532 字 / 梗概 1534 字 / 实况 3171 字
    assert len(on["full_text"]) > 13_000
    assert len(on["summary"]) > 1_500
    assert len(on["transcript"]) > 3_000
    # 7 幕 + 7 天，实测都有
    assert len(on["acts"]) == 7
    assert len(on["days"]) == 7
    assert [d["day"] for d in on["days"]] == [1, 2, 3, 4, 5, 6, 7]
    assert on["acts"][0]["text"].startswith("第1幕")
    assert on["survivors"] == ["谭月娥", "柏绍安", "严启澄"]


def test_cached_origin_novel_wins():
    """已有 origin_novel 时直接用，不重算。"""
    vol = _real_volume2()
    vol.origin_novel = {"full_text": "缓存的原文", "summary": "", "built_from": "pick_one"}
    on = origin_novel_of(vol)
    assert on["full_text"] == "缓存的原文"
    assert on["built_from"] == "pick_one"


def test_acts_missing_markers_is_empty_not_error():
    """没有幕标记（短稿/fallback 稿）→ acts 为空，不抛异常。"""
    assert _split_acts("就是一段没有幕标记的梗概。") == []
    assert _split_acts("") == []


def test_summary_falls_back_to_full_log_section():
    """final_summary 缺失时，从 full_log 的「小说化梗概」段兜底。"""
    full_log = (
        "===== 跑团完整日志（守密人 / 调查员对话）=====\n\n回合1内容\n\n"
        "===== 小说化梗概（原剧本经过）=====\n\n第1幕：黑红纸帖在雨夜抵达钟楼。\n\n"
        "===== 通稿（本场完整故事）=====\n\n通稿正文开头。\n\n通稿正文结尾。\n"
    )
    variant = {"full_narrative": "通稿正文开头。\n\n通稿正文结尾。", "final_summary": ""}
    on = build_origin_novel(variant, full_log=full_log)
    assert on["summary"] == "第1幕：黑红纸帖在雨夜抵达钟楼。"
    assert on["full_text"] == "通稿正文开头。\n\n通稿正文结尾。"


def test_build_origin_novel_tolerates_empty():
    """全空输入返回空结构，不抛异常。"""
    for arg in (None, {}, {"selected_variant": {}}):
        on = build_origin_novel(arg)
        assert on["full_text"] == "" and on["acts"] == [] and on["days"] == []


def test_origin_novel_round_trips_through_volume_dict():
    """origin_novel 能随 Volume 序列化往返。"""
    vol = _real_volume2()
    vol.origin_novel = origin_novel_of(vol)
    again = Volume.from_dict(vol.as_dict())
    assert len(origin_novel_of(again)["full_text"]) > 13_000


# ============================================================
# 二、build_origin_material
# ============================================================

def test_origin_material_contains_all_four_blocks():
    m = build_origin_material(_real_volume2(), ch_day=3)
    assert m is not None
    assert m.family.value == "required"
    for token in ("原剧本·唐迟魂穿之前的完整故事", "小说化梗概",
                  "通稿原文节选", "原剧本逐日推进", "怎么用这份材料"):
        assert token in m.content, f"缺 {token}"


def test_origin_material_day_window_marks_today_and_future():
    m = build_origin_material(_real_volume2(), ch_day=3)
    assert "（← 本章对应日）" in m.content
    assert "（原剧本之后会发生）" in m.content
    # 窗口 ±1 → 只出现 2/3/4 天，不出现第 5 天
    assert "第2天" in m.content and "第4天" in m.content
    assert "第5天（原剧本之后会发生）" not in m.content


def test_origin_material_forbids_spoiling_future():
    """未来日必须明确写出「不能提前写出来」，否则模型会照着写。"""
    m = build_origin_material(_real_volume2(), ch_day=2)
    assert "绝不能在本章把它们原样写出来" in m.content


def test_origin_material_respects_narrative_budget():
    """通稿 13532 字必须被截到头尾两段，整段塞进来会占掉 20% 预算。"""
    m = build_origin_material(_real_volume2(), ch_day=3)
    assert "（原剧本中段已略" in m.content
    assert m.byte_size < 20_000, f"超预算：{m.byte_size} 字节"
    # 梗概是全给的（1534 字，它是骨架）
    assert "第7幕" in m.content


def test_origin_material_none_without_data():
    assert build_origin_material(None) is None
    assert build_origin_material(Volume(volume_no=9, field_no=9)) is None


# ============================================================
# 三、记忆碎片：数据结构与按天闸门
# ============================================================

def test_day_gate_hides_future_memories():
    m5 = MemoryFragment(text="他三天前在废染坊偷走了那颗黑胆珠。", recovers_at=5)
    assert m5.unlocked_at(1) is False
    assert m5.unlocked_at(4) is False
    assert m5.unlocked_at(5) is True
    assert m5.unlocked_at(7) is True


def test_revealed_overrides_plan_and_never_recedes():
    """已写出的记忆以实际章号为准，且不因计划日靠后而消失。"""
    m = MemoryFragment(text="他做过的那件最致命的事。", recovers_at=7, revealed_at=2)
    assert m.unlocked_at(1, ch_no=5) is True     # 第2章已写出，第5章仍在
    assert m.unlocked_at(1, ch_no=1) is False    # 第1章时还没写到


def test_no_day_falls_back_to_day_one_not_empty():
    """项目没填 day 时按第 1 天闸门：底牌照样拦住，但第一天的事实要给。

    这条是防静默故障——按「全不给」实现的话，功能看起来上线了，
    实际在没填 day 的项目里一次都注不进去。
    """
    body = MemoryFragment(text="醒来就能想起的一件小事。", recovers_at=1)
    ace = MemoryFragment(text="第七天才想起的底牌。", recovers_at=7)
    assert body.unlocked_at(None) is True
    assert ace.unlocked_at(None) is False


def test_memories_from_list_dedupes_but_keeps_short_author_text():
    """归一只去重和空值，**不**按长度丢弃——长度下限只作用于 AI 草稿。

    作者手写「他怕水」三个字是有意为之的表达权；模型吐「他怕水」则是没写完。
    把两者用同一条规则处理，会让作者写好的短记忆在保存时凭空消失。
    长度下限在 memory_gen.normalize_draft 里，只管 AI 草稿。
    """
    out = memories_from_list([
        {"text": "手指摸向左手无名指，那里有戴了多年戒指的白痕。", "recovers_at": 1},
        {"text": "手指摸向左手无名指，那里有戴了多年戒指的白痕。", "recovers_at": 3},
        {"text": "他怕水", "recovers_at": 2},
        {"text": "", "recovers_at": 2},
    ])
    assert len(out) == 2, "应保留 1 条去重后 + 1 条短文本"
    assert all(isinstance(m, MemoryFragment) for m in out)
    assert {m.text for m in out} == {
        "手指摸向左手无名指，那里有戴了多年戒指的白痕。", "他怕水"}


def test_memory_gen_drops_short_ai_drafts():
    """AI 草稿里过短的碎片要丢掉（那是模型没写完，不是表达）。"""
    from app.entities import memory_gen
    out = memory_gen.normalize_draft([
        {"text": "他怕水", "recovers_at": 1},
        {"text": "黑胆珠贴在胸口发热，那晚雨下得很大，他记得自己撬开了染坊的侧门。",
         "recovers_at": 3},
    ])
    assert len(out) == 1
    assert "黑胆珠" in out[0]["text"]


def test_day_clamped_to_range():
    assert MemoryFragment(text="x" * 40, recovers_at=99).recovers_at == 7
    assert MemoryFragment(text="x" * 40, recovers_at=0).recovers_at == 1
    assert MemoryFragment(text="x" * 40, recovers_at="abc").recovers_at == 1


def test_legacy_entity_loads_without_memories(tmp_path):
    """旧 JSON 没有 memories 键，加载后必须为空列表而不是报错。"""
    p = tmp_path / "entities.json"
    p.write_text(json.dumps({
        "entities": [{"id": "c1", "type": "character", "name": "陆骁"}],
        "changes": [], "relations": [], "protagonist": None,
        "pending_diffs": [], "applied_diffs": [],
    }, ensure_ascii=False), encoding="utf-8")
    store = EntityStore(p)
    assert store.get("c1").memories == []


def test_non_character_cannot_hold_memories():
    from app.entities import Entity
    loc = Entity(id="l1", type="location", name="钟楼",
                 memories=[{"text": "不该出现", "recovers_at": 1}])
    assert loc.memories == []


def test_memory_seg_only_shows_unlocked():
    class FakeEnt:
        memories = [
            MemoryFragment(text="醒来就有的记忆。", recovers_at=1),
            MemoryFragment(text="第五天才想起的底牌。", recovers_at=5),
        ]
    assert "醒来就有的记忆" in _memory_seg(FakeEnt(), 1)
    assert "第五天才想起的底牌" not in _memory_seg(FakeEnt(), 1)
    both = _memory_seg(FakeEnt(), 5)
    assert "醒来就有的记忆" in both and "第五天才想起的底牌" in both


def test_memory_seg_empty_when_no_memories():
    class FakeEnt:
        memories = []
    assert _memory_seg(FakeEnt(), 3) == ""


def test_memory_seg_lists_unrevealed_count():
    class FakeEnt:
        memories = [MemoryFragment(text=f"记忆{i}。", recovers_at=1) for i in range(6)]
    seg = _memory_seg(FakeEnt(), 1)
    assert "另有 2 条尚未想起" in seg


def test_protagonist_memories_round_trip(tmp_path):
    p = tmp_path / "entities.json"
    store = EntityStore(p)
    store.set_memories("character-protagonist-tangchi",
                       [{"text": "唐迟自己的记忆碎片。", "recovers_at": 3}])
    again = EntityStore(p)
    assert len(again.protagonist.memories) == 1
    assert again.protagonist.memories[0].recovers_at == 3


def test_set_memories_rejects_non_character(tmp_path):
    store = EntityStore(tmp_path / "e.json")
    loc = store.add("location", "钟楼")
    with pytest.raises(ValueError):
        store.set_memories(loc.id, [{"text": "x" * 40, "recovers_at": 1}])


# ============================================================
# 四、跨章连贯窗口（前 2 后 2）
# ============================================================

@pytest.fixture
def desk_env(tmp_path):
    return SimpleNamespace(desk=_FakeDesk())


class _FakeWork:
    def __init__(self, plot="", draft="", polish="", title="", field_no=None, day=None, brief=None):
        self.plot = {"text": plot} if plot else None
        self.draft = {"text": draft} if draft else None
        self.polish = {"text": polish} if polish else None
        self.title = title
        self.field_no = field_no
        self.day = day
        self.brief = brief
        self.chapter_kind = "dungeon"


class _FakeDesk:
    def __init__(self, chapters: dict[int, _FakeWork] | None = None):
        self._chapters = chapters or {}

    def all_nos(self):
        return list(self._chapters.keys())

    def get(self, no):
        return self._chapters.get(no)


def _vol_with_plan():
    """构造一个带 rhythm_blueprint.chapter_plan 的卷（形状对齐真实数据）。"""
    plan = []
    for n, day in ((5, 2), (6, 3), (7, 3), (8, 4), (9, 4), (10, 5), (11, 5)):
        plan.append({
            "chapter": n, "day": day, "title": f"第{n}章标题", "beat_type": "hook",
            "focus": f"第{n}章的核心方向",
            "plot_beats": {"qi": f"{n}起", "cheng": f"{n}承",
                           "zhuan": f"{n}转", "he": f"{n}合"},
            "foreshadow_seeds": [f"{n}号伏笔"],
        })
    return SimpleNamespace(volume_no=2, rhythm_blueprint={"chapter_plan": plan})


def test_two_before_two_after():
    desk = _FakeDesk({
        5: _FakeWork(plot="第5章剧情。", draft="第5章正文。"),
        6: _FakeWork(plot="第6章剧情。", draft="第6章正文。"),
    })
    m = _continuity_window_material(desk, _vol_with_plan(), chapter=7)
    assert m is not None
    # 前 2 章取全文（不是 160 字摘要）
    assert "第5章剧情" in m.content and "第6章剧情" in m.content
    # 后 2 章来自 blueprint（第 7 章的后 2 章 = 8 和 9），含起承转合与伏笔种子
    assert "第8章《" in m.content and "第9章《" in m.content
    assert "起：8起" in m.content and "合：8合" in m.content
    assert "第10章" not in m.content        # 只后看 2 章，不越界
    assert "8号伏笔" in m.content
    # 接缝三律必须在
    assert "接缝三律" in m.content
    assert "1. 接住" in m.content and "2. 不越" in m.content and "3. 留口" in m.content


def test_window_sizes_are_two():
    assert CONTINUITY_BEFORE == 2
    assert CONTINUITY_AFTER == 2


def test_skipped_chapter_rolls_backward():
    """跳章（第7章没写）时前两章自动往前顺延，并说明跳章。"""
    desk = _FakeDesk({
        5: _FakeWork(plot="第5章剧情。"),
        6: _FakeWork(plot="第6章剧情。"),
        8: _FakeWork(plot="第8章剧情。"),      # 第7章故意缺失
    })
    m = _continuity_window_material(desk, _vol_with_plan(), chapter=8)
    assert "第6章剧情" in m.content and "第5章剧情" in m.content
    assert "尚未写" in m.content


def test_before_ascending_after_descending_not_required():
    """前 N 章必须按时间升序排列（先发生的在前）。"""
    desk = _FakeDesk({
        5: _FakeWork(plot="AAA第5章。"),
        6: _FakeWork(plot="BBB第6章。"),
    })
    m = _continuity_window_material(desk, _vol_with_plan(), chapter=7)
    assert m.content.index("AAA第5章") < m.content.index("BBB第6章")


def test_no_blueprint_falls_back_to_outline_map():
    """蓝星章/间章没有 blueprint，退化为取大纲层后两章。"""
    desk = _FakeDesk({})
    o_map = {8: "第8章大纲标题\n这一章发生了一件事。", 9: "第9章大纲标题"}
    m = _continuity_window_material(desk, SimpleNamespace(volume_no=1, rhythm_blueprint=None),
                                    chapter=7, outline_map=o_map)
    assert m is not None
    assert "第8章" in m.content and "第9章" in m.content


def test_returns_none_when_no_data_at_all():
    """开书第 1 章：没有已写章、也没有计划范围 → 返回 None（不硬塞纪律）。

    这是既有契约（test_empty_book_returns_none 依赖它）：
    没有前后文可接的时候纪律无处落地，只会污染开书第一章。
    卷末最后几章不属于这种情况——它有 before_parts，只是没有 after_parts。
    """
    desk = _FakeDesk({})
    assert _continuity_window_material(desk, None, chapter=1) is None


def test_volume_end_chapter_still_gets_rules():
    """卷末：只有前 2 章、没有后 2 章 → 仍然带纪律（只是数据少一半）。"""
    desk = _FakeDesk({
        10: _FakeWork(plot="第10章剧情。"),
        11: _FakeWork(plot="第11章剧情。"),
    })
    m = _continuity_window_material(desk, _vol_with_plan(), chapter=12)
    assert m is not None
    assert "第10章剧情" in m.content and "第11章剧情" in m.content
    assert "接缝三律" in m.content


def test_beyond_plan_range_keeps_rules():
    """超出 chapter_plan 范围（无 after、无 before）→ 返回 None，不编造后章。"""
    desk = _FakeDesk({})
    assert _continuity_window_material(desk, _vol_with_plan(), chapter=11) is None


# ============================================================
# 五、与开章任务书的集成
# ============================================================

def test_checklist_includes_continuity_and_origin():
    """任务书里同时出现跨章接缝与原剧本两段。"""
    from app.entities import EntityStore
    from app.wizard import DeskStore

    tmp = Path(pytest.importorskip("tempfile").mkdtemp())
    desk = DeskStore(tmp / "desk.json")
    entities = EntityStore(tmp / "entities.json")
    desk.record(3, "plot", "第3章剧情。", title="雨夜来电")
    desk.record(4, "plot", "第4章剧情。", title="追查")

    vol = _real_volume2()
    vol.rhythm_blueprint = _vol_with_plan().rhythm_blueprint
    m = build_chapter_checklist(entities, desk, 5, 20, current_volume=vol)
    assert m is not None
    assert "跨章接缝" in m.content
    assert "原剧本" in m.content


def test_memory_appears_on_character_line_in_checklist():
    from app.wizard import DeskStore

    tmp = Path(pytest.importorskip("tempfile").mkdtemp())
    desk = DeskStore(tmp / "desk.json")
    entities = EntityStore(tmp / "entities.json")
    host = entities.add("character", "陆骁", summary="河道测绘员", chapter=3)
    entities.update(host.id, memories=[{"text": "他偷走了黑胆珠。", "recovers_at": 1}])
    entities.record_appearance(host.id, 3)      # 已出场，否则不进实体快照
    desk.record(3, "plot", "第3章剧情。")

    vol = _real_volume2()
    vol.rhythm_blueprint = _vol_with_plan().rhythm_blueprint
    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    assert "忆起：" in m.content
    assert "他偷走了黑胆珠" in m.content


# ============================================================
# 六、回归：volume_store 传单个 Volume 时竞技场注入曾静默失效
# ============================================================

def test_arena_injects_when_volume_object_passed_directly(tmp_path):
    """传单个 Volume 对象（desk_run 走的就是这条）时，副本本场设定必须注入。

    **回归**：volume_store 之前一律按仓库处理（`volume_store.get(vn)`），
    而 desk_run 传的是 `_volume_for_chapter(...)` 返回的单个 Volume，
    Volume 没有 .get 方法 → AttributeError → 被外层 `except Exception: pass`
    静默吞掉 → 副本章的竞技场材料（魂穿宿主档案 / 死亡线索高潮节点 /
    详设 6 卡）**一次都没进过 prompt，且没有任何报错**。
    """
    from app.providers.base import LLMResponse, Usage
    from app.wizard import DeskStore, WizardStore, run_chapter_stage
    from app.pipeline.prompts import PromptStore
    from app.projects import ProjectStore
    from app.review import MarkerStore

    project = ProjectStore(tmp_path / "projects")
    pid = project.create("测试书", "").id        # pid 是自动生成的，必须用返回值
    base = tmp_path / "projects" / pid
    desk = DeskStore(base / "desk.json")
    wizard = WizardStore(base / "wizard.json")
    markers = MarkerStore(base / "markers.json")
    entities = EntityStore(base / "entities.json")

    vol = _real_volume2()
    desk.record(3, "plot", "第3章剧情。")
    desk.assign_field(3, 2, None)      # 竞技场注入以 work.field_no 为闸门，必须挂到场 2

    captured: list[str] = []

    class Cap:
        default_model = None
        name = "cap"

        def chat(self, messages, model=None, max_tokens=None, extra=None):
            captured.append("\n".join(m["content"] for m in messages))
            return LLMResponse(text="正文。", usage=Usage(1, 1))

    run_chapter_stage(
        Cap(), PromptStore(), project, pid, markers, wizard, desk,
        3, "draft", force=True,
        entity_store=entities,
        volume_store=vol,          # ← 传对象，不是仓库
    )
    # 一次 draft 可能触发多轮重写（质量门禁/去 AI 味），逐轮断言而不是只看最后一轮
    assert captured, "模型一次都没被调用"
    assert any("魂穿宿主" in p for p in captured), "传单个 Volume 时魂穿宿主没注入（回归）"
    assert any("身体状态" in p for p in captured)
    assert any("第七日死状" in p for p in captured)
    assert any("原剧本" in p for p in captured), "原剧本没注入"