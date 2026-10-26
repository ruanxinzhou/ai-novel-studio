"""魂穿宿主记忆 → 主角模糊记忆（视角归位）。

## 这组测试守的是什么

魂穿设定：唐迟穿进的是**别人**的身体（卷 2 是陆骁）。这具身体在被附身
之前经历过的事，不是「陆骁的记忆」，而是唐迟此刻能摸到的**残响**——
他会在后颈发凉时忽然想起「这双手好像在废染坊偷过什么东西」，
但想不起完整因果，只剩一个画面和一点生理性的恶心。

所以宿主实体的记忆碎片必须以**唐迟的视角**出现在正文提示里，
而不是挂在「陆骁」那行让模型转述。

## 为什么这是「曾经完全没接」的链路

- `protagonist.memories` 存在 `ProtagonistState` 里，但 ledger 从来没读过它
  → 主角自己的记忆**一次都没进过 prompt**；
- 宿主的碎片挂在宿主实体的角色行上，视角错位，且宿主行可能被
  快照前 20 名的截断挤掉 → 「唐迟该想起的东西」跟着宿主一起消失。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.context.ledger import (
    _host_memory_seg,
    _memory_seg,
    _protagonist_name,
    _render_memories,
    build_chapter_checklist,
)
from app.entities import EntityStore, MemoryFragment
from app.wizard import DeskStore

REPO_ROOT = Path(__file__).resolve().parents[2]
VOL2 = REPO_ROOT / "backend" / "data" / "projects" / "20261014-c6c432" / "volumes" / "2.json"


def _real_volume2():
    from app.book.volume import Volume
    return Volume.from_dict(json.loads(VOL2.read_text(encoding="utf-8")))


def _setup(tmp_path):
    """铺一套最小现场：主角唐迟 + 宿主陆骁 + 一点碎片。"""
    desk = DeskStore(tmp_path / "desk.json")
    entities = EntityStore(tmp_path / "entities.json")

    hero = entities.add("character", "唐迟", summary="魂穿者", chapter=3)
    entities.record_appearance(hero.id, 3)
    host = entities.add("character", "陆骁", summary="河道测绘员", chapter=3)
    entities.record_appearance(host.id, 3)
    desk.record(3, "plot", "第3章剧情。")
    return desk, entities, hero, host


# ============================================================
# 一、渲染层
# ============================================================

def test_render_memories_gates_by_day():
    frags = [
        MemoryFragment(text="醒来就有的记忆", recovers_at=1),
        MemoryFragment(text="第五天才想起的底牌", recovers_at=5),
    ]
    assert "醒来就有的记忆" in _render_memories(frags, 1)
    assert "第五天才想起的底牌" not in _render_memories(frags, 1)
    assert "第五天才想起的底牌" in _render_memories(frags, 5)


def test_render_memories_custom_label():
    """宿主残忆用「残忆：」，与角色自己的「忆起：」区分开。"""
    frags = [MemoryFragment(text="这双手偷过胆珠", recovers_at=1)]
    assert _render_memories(frags, 1, label="残忆：").startswith("（残忆：")


def test_render_memories_empty_returns_empty():
    assert _render_memories([], 1) == ""
    assert _render_memories([MemoryFragment(text="", recovers_at=1)], 1) == ""


def test_protagonist_name_falls_back():
    """主角名拿不到时退回「唐迟」，不能抛异常把整段注入搞挂。"""
    assert _protagonist_name(object()) == "唐迟"


# ============================================================
# 二、宿主残忆 → 主角行
# ============================================================

def test_host_memory_seg_reads_host_fragments(tmp_path):
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "他在废染坊偷走过黑胆珠。", "recovers_at": 1},
    ])
    vol = _real_volume2()
    assert vol.host_character.get("name") == "陆骁"

    seg = _host_memory_seg(entities, vol, 1, 3)
    assert seg, "宿主残忆应能取到"
    assert "废染坊" in seg
    assert seg.startswith("（残忆：")


def test_host_memory_seg_respects_day_gate(tmp_path):
    """第 1 天不该放出第 6 天的底牌。"""
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "第六天才想起的红线底牌。", "recovers_at": 6},
    ])
    vol = _real_volume2()
    assert _host_memory_seg(entities, vol, 1, 3) == ""
    assert "红线底牌" in _host_memory_seg(entities, vol, 6, 3)


def test_host_memory_seg_no_host_entity(tmp_path):
    """宿主实体不存在时不报错，静默返回空。"""
    desk, entities, hero, host = _setup(tmp_path)
    entities.delete(host.id)
    vol = _real_volume2()
    assert _host_memory_seg(entities, vol, 1, 3) == ""


def test_host_memory_seg_no_volume(tmp_path):
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[{"text": "记忆", "recovers_at": 1}])
    assert _host_memory_seg(entities, None, 1, 3) == ""


# ============================================================
# 三、集成：提示书里唐迟那一行要带宿主的残忆
# ============================================================

def test_checklist_puts_host_memory_on_protagonist_line(tmp_path):
    """核心回归：宿主的记忆要出现在**唐迟的行**上，不是陆骁的行上。"""
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "他偷走了黑胆珠。", "recovers_at": 1},
    ])
    vol = _real_volume2()
    vol.rhythm_blueprint = {"chapter_plan": []}

    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    lines = [ln for ln in m.content.splitlines() if ln.startswith("- ")]
    hero_line = next((ln for ln in lines if ln.startswith("- 唐迟")), None)
    assert hero_line, "任务书里应有唐迟这一行"
    assert "他偷走了黑胆珠" in hero_line, "宿主残忆必须挂在唐迟行上"
    assert "残忆：" in hero_line


def test_checklist_injects_protagonist_own_memories(tmp_path):
    """主角自己的记忆（存在 protagonist 里）也要能进 prompt。

    **回归**：这段链路之前完全没接——`protagonist.memories` 存了，
    但 ledger 从没读过它，所以作者配的主角记忆一次都没生效过。
    """
    desk, entities, hero, host = _setup(tmp_path)
    entities.set_memories("character-protagonist-tangchi", [
        {"text": "唐迟前世的工位上有一盏坏掉的白炽灯。", "recovers_at": 1},
    ])
    vol = _real_volume2()
    vol.rhythm_blueprint = {"chapter_plan": []}

    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    lines = [ln for ln in m.content.splitlines() if ln.startswith("- ")]
    hero_line = next((ln for ln in lines if ln.startswith("- 唐迟")), None)
    assert hero_line
    assert "坏掉的白炽灯" in hero_line


def test_checklist_does_not_duplicate_host_memory_on_host_line(tmp_path):
    """同一批碎片不能同时出现在唐迟行和陆骁行。

    两处都出现时模型分不清「这是陆骁的记忆」还是「这是唐迟的记忆」，
    而这正是魂穿写法最不能含糊的一点。
    """
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "他偷走了黑胆珠。", "recovers_at": 1},
    ])
    vol = _real_volume2()
    vol.rhythm_blueprint = {"chapter_plan": []}

    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    lines = [ln for ln in m.content.splitlines() if ln.startswith("- ")]
    hero_line = next(ln for ln in lines if ln.startswith("- 唐迟"))
    host_line = next((ln for ln in lines if ln.startswith("- 陆骁")), None)

    assert "他偷走了黑胆珠" in hero_line
    if host_line is not None:
        assert "他偷走了黑胆珠" not in host_line, "宿主行不应再重复这批碎片"


def test_host_memory_shifted_to_third_person(tmp_path):
    """宿主碎片是第一人称，但读它的是唐迟 → 必须转成第三人称残响。

    保留第一人称会让模型写成唐迟自述（「我站在纸扎铺前」），
    那就成了唐迟亲身经历，「这记忆不属于我」的错位感全丢。
    """
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "我站在陶露的纸扎铺前，开口便说自己是来求购旧术资料的河道测绘员。",
         "recovers_at": 1},
    ])
    vol = _real_volume2()

    seg = _host_memory_seg(entities, vol, 1, 3)
    assert "这具身体站在陶露的纸扎铺前" in seg
    assert "我站在陶露" not in seg


def test_host_memory_keeps_original_text_in_entity(tmp_path):
    """转换只发生在注入层，实体里存的原文不能被改写。"""
    desk, entities, hero, host = _setup(tmp_path)
    original = "我站在陶露的纸扎铺前。"
    entities.update(host.id, memories=[{"text": original, "recovers_at": 1}])
    vol = _real_volume2()

    _host_memory_seg(entities, vol, 1, 3)
    assert entities.get(host.id).memories[0].text == original


def test_host_memory_shifts_my_to_body_possessive(tmp_path):
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "我的指缝残留洗不净的黑色淤痕，贴身藏着黑胆珠。", "recovers_at": 1},
    ])
    vol = _real_volume2()

    seg = _host_memory_seg(entities, vol, 1, 3)
    assert "这具身体的指缝" in seg
    assert "我的指缝" not in seg


def test_host_memory_strips_protagonist_name(tmp_path):
    """宿主的记忆里不能出现「唐迟」——宿主不认识魂穿者。

    模型偶尔会写「唐迟一碰到它，便能确认…」，这句会让整段变成
    唐迟的亲身经历，正好毁掉「记忆不属于我」这层。
    含「唐迟」的句子要**整句**去掉，不能留半截尾巴。
    """
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "这具身体的内袋里贴着一颗黑胆珠，隔着布料持续发温。"
                 "唐迟一碰到它，便能确认这不是偶然捡来的饰物。",
         "recovers_at": 1},
    ])
    vol = _real_volume2()

    seg = _host_memory_seg(entities, vol, 1, 3)
    assert "唐迟" not in seg
    assert "黑胆珠" in seg
    # 半截尾巴（「便能确认…」）也要跟着整句走，不能悬空留下
    assert "便能确认" not in seg


def test_checklist_fallback_section_when_hero_line_absent(tmp_path):
    """主角实体不在快照里时，宿主残忆要单独兜底成一段，不能整段丢掉。

    唐迟每章都在场，「他该想起的东西」不能因为主角那一行不在就消失。
    """
    desk = DeskStore(tmp_path / "desk.json")
    entities = EntityStore(tmp_path / "entities.json")
    # 只建宿主，不建唐迟实体 → 角色快照里没有主角行
    host = entities.add("character", "陆骁", summary="河道测绘员", chapter=3)
    entities.update(host.id, memories=[
        {"text": "他偷走了黑胆珠。", "recovers_at": 1},
    ])
    entities.record_appearance(host.id, 3)
    desk.record(3, "plot", "第3章剧情。")

    vol = _real_volume2()
    vol.rhythm_blueprint = {"chapter_plan": []}

    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    assert "残忆" in m.content
    assert "他偷走了黑胆珠" in m.content


def test_checklist_host_memory_still_gated_by_chapter_day(tmp_path):
    """第 1 章（day=1）不该放出宿主第 6 天的底牌。"""
    desk, entities, hero, host = _setup(tmp_path)
    entities.update(host.id, memories=[
        {"text": "第七日红线的完整真相。", "recovers_at": 7},
    ])
    vol = _real_volume2()
    vol.rhythm_blueprint = {"chapter_plan": []}

    # 第 4 章的 desk 里没有 day → ch_day 为 None → 按第 1 天处理
    m = build_chapter_checklist(entities, desk, 4, 20, current_volume=vol)
    assert m is not None
    assert "第七日红线的完整真相" not in m.content
