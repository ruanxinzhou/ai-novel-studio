"""节奏骨架（Rhythm）路由：7 天强度曲线 + 密度分析 + 章节分配建议 + 告警。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.rhythm")

_RHYTHM_SYSTEM = """你是一名克苏鲁风格无限流小说的节奏设计师，正在为《第七日的亡者》某一卷做"节奏骨架"推演。
前序产物（副本核、跑团走向、宿主、魂穿、唐迟七日历、详设 6 卡已锁定的死亡/高潮/线索/核心反转/起点/代价上限）已全部完成，
你的任务是根据已锁定的唐迟入戏第1-7天（T-6→T-0）节点分布，给出一份整卷节奏蓝图。

【双轨时间线提醒】
- day=1~7 指「唐迟入戏第1-7天」（他自己的T-6→T-0倒计时），不是原剧本的Day1-Day7；
- 原剧本是独立故事（可能是几小时、几天、几个月、几代人），它有自己的事件流；
- 做节奏规划时，key_beats_progress 和 chapter_plan 的 day 字段必须指唐迟入戏第几天，
  并在 beats/chapter focus 里同时标注「原剧本此刻正在哪个事件」，让作者清楚两条时间线并行。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。返回格式：

{
  "pacing_ratio": {"setup": 0.18, "build": 0.32, "climax": 0.35, "cooldown": 0.15},
  "key_beats_progress": [
    {"day": 1, "intensity": 2, "label": "入本惊悚", "original_timeline_anchor": "原剧本此刻事件简述（10-30字）"},
    {"day": 2, "intensity": 3, "label": "第一滴血", "original_timeline_anchor": "..."},
    {"day": 3, "intensity": 4, "label": "规则显形", "original_timeline_anchor": "..."},
    {"day": 4, "intensity": 3, "label": "喘息/误导", "original_timeline_anchor": "..."},
    {"day": 5, "intensity": 5, "label": "中盘反转", "original_timeline_anchor": "..."},
    {"day": 6, "intensity": 6, "label": "同伴接连殒落", "original_timeline_anchor": "..."},
    {"day": 7, "intensity": 7, "label": "T-0终极高潮", "original_timeline_anchor": "原剧本此刻可能已过终点/或正在终点"}
  ],
  "warnings": [
    {"type": "dearth", "day": 4, "msg": "第4天无高潮节点，节奏可能塌腰"},
    {"type": "cluster", "day": 5, "msg": "第5天同时有死亡+高潮+3条线索，信息过载"}
  ],
  "suggested_length": 95000,
  "suggested_chapter_range": [12, 16],
  "chapter_plan": [
    {"chapter": 1, "day": 1, "focus": "入本+第一异常（原剧本：xx事件）", "words": 6500, "beat_type": "hook"},
    {"chapter": 2, "day": 1, "focus": "宿管之死+规则建立（原剧本：xx）", "words": 7000, "beat_type": "sell"}
  ],
  "notes": "整体节奏建议：前3章黄金三章拉满恐怖密度，第4章给主角短暂喘息+误导红鲱鱼..."
}

字段约束（必须严格遵守）：
- pacing_ratio 四段 setup/build/climax/cooldown 加起来必须等于 1.0，每段 0.05~0.55。
- key_beats_progress 长度必须是 7，day=1~7 各一条（唐迟入戏第1-7天），intensity 为 1~7 的整数（7=T-0终极高潮），label 4-8 字；每条必须带 original_timeline_anchor 标注原剧本此刻事件。
- warnings 是数组，可以为空。每条必须带 type（dearth=真空日，cluster=过载日，front_load=前重后轻，back_load=前轻后重）、day（除 front_load/back_load 外必填）、msg（中文一句话描述问题）。
- suggested_length：整卷总字数（整数，50000~150000）。
- suggested_chapter_range：[min, max] 两个整数，10~25 之间，min<=max。
- chapter_plan：章节分配数组，长度必须在 [min, max] 之间。每条包含 chapter（从 1 开始递增）、day（1~7，指唐迟入戏第几天）、focus（一句话 10-30 字本章焦点，必须在括号里标注原剧本此刻事件）、words（本章预估字数，整数 4000~9000）、beat_type（hook/sell/engine/climax/cooldown 之一）。
- notes：一段 100-200 字的整体节奏建议，克苏鲁阴冷基调。

注意：节点密度图会由服务端本地计算，你无需重复统计，但要基于这些密度信息给出合理的 warnings、intensity 曲线与章节分配。
"""


@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable
    chore_provider: Callable


class _AnalyzeIn(BaseModel):
    rhythm_directive: str = ""


class _FinalizeIn(BaseModel):
    pass


def _is_mock(vol) -> bool:
    try:
        from ..book.volume import adapted_premise_of
        premise = adapted_premise_of(vol.adapted_module)
        if isinstance(premise, str) and premise.startswith("测试副本核文本"):
            return True
        hc = vol.host_character or {}
        if isinstance(hc, dict) and "林薇" in (hc.get("name") or ""):
            return True
    except Exception:
        return False
    return False


def _compute_density_map(vol) -> dict:
    """纯本地计算 7 天节点密度：每天 death/climax/clue/total 计数。"""
    density: dict[int, dict] = {}
    for d in range(1, 8):
        density[d] = {"death": 0, "climax": 0, "clue": 0, "total": 0}
    for n in (vol.death_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["death"] += 1
    for n in (vol.climax_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["climax"] += 1
    for n in (vol.clue_nodes or []):
        if not isinstance(n, dict):
            continue
        try:
            day = int(n.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7:
            density[day]["clue"] += 1
    for d in range(1, 8):
        density[d]["total"] = density[d]["death"] + density[d]["climax"] + density[d]["clue"]
    # 键转成字符串以便 JSON（前端按数字/字符串都能取）
    return {str(d): density[d] for d in range(1, 8)}


def _all_scaffold_locked(vol) -> bool:
    """校验 6 张详设卡是否都已 locked。"""
    cards = vol.scaffold_cards or {}
    all_keys = ["death_nodes", "climax_nodes", "clue_nodes",
                "core_twist", "tangchi_start", "cost_cap"]
    return all((cards.get(k) or {}).get("locked_at") for k in all_keys)


def _mock_blueprint() -> dict:
    """克苏鲁基调硬编码 mock 节奏蓝图（14 章在 [12,16] 区间内，含 3 条 warnings）。"""
    return {
        "pacing_ratio": {"setup": 0.18, "build": 0.30, "climax": 0.38, "cooldown": 0.14},
        "key_beats_progress": [
            {"day": 1, "intensity": 2, "label": "入本惊悚"},
            {"day": 2, "intensity": 4, "label": "宿管缢亡"},
            {"day": 3, "intensity": 5, "label": "报纸睁眼"},
            {"day": 4, "intensity": 3, "label": "喘息/红鲱"},
            {"day": 5, "intensity": 5, "label": "影子先行"},
            {"day": 6, "intensity": 6, "label": "同伴相继"},
            {"day": 7, "intensity": 7, "label": "海床点名"},
        ],
        "warnings": [
            {"type": "dearth", "day": 4, "msg": "第4天无高潮节点，中盘可能塌腰，建议用误导/红鲱鱼维持张力"},
            {"type": "cluster", "day": 5, "msg": "第5天同时有死亡+高潮+3条线索，信息过载，建议分散线索到第6天"},
            {"type": "front_load", "msg": "前3天死亡/线索密度过高，若不控制字数会让读者审美疲劳"},
        ],
        "suggested_length": 98000,
        "suggested_chapter_range": [12, 16],
        "chapter_plan": [
            {"chapter": 1,  "day": 1, "focus": "唐迟醒来/宿舍异象/倒计时出现",          "words": 6500, "beat_type": "hook"},
            {"chapter": 2,  "day": 1, "focus": "宿管缢亡/咸水规则初显",                "words": 7000, "beat_type": "sell"},
            {"chapter": 3,  "day": 2, "focus": "刘保安溺亡/海水折进校园的认知冲击",    "words": 7200, "beat_type": "engine"},
            {"chapter": 4,  "day": 2, "focus": "周凯半条微信/轮回疑云",                "words": 6800, "beat_type": "engine"},
            {"chapter": 5,  "day": 3, "focus": "校史馆报纸睁眼/陈阿妹之名",            "words": 7200, "beat_type": "engine"},
            {"chapter": 6,  "day": 3, "focus": "盐圈困影/第一次主动求生",              "words": 7000, "beat_type": "engine"},
            {"chapter": 7,  "day": 4, "focus": "短暂喘息/虚假安全感/红鲱鱼误导",        "words": 6500, "beat_type": "cooldown"},
            {"chapter": 8,  "day": 4, "focus": "苏雅血字童谣/点名开始",                "words": 7000, "beat_type": "engine"},
            {"chapter": 9,  "day": 5, "focus": "林薇坠楼/影子真相/中盘反转",           "words": 7500, "beat_type": "climax"},
            {"chapter": 10, "day": 5, "focus": "无脚合唱/镜中自己/辅导员的鳃",         "words": 7200, "beat_type": "engine"},
            {"chapter": 11, "day": 6, "focus": "苏雅撞钟失败/队友接连殒落",            "words": 7000, "beat_type": "climax"},
            {"chapter": 12, "day": 6, "focus": "七次迭代刻名/破局决断",                "words": 7000, "beat_type": "climax"},
            {"chapter": 13, "day": 7, "focus": "暴雨夜/47亡魂点名/最终合唱",           "words": 8000, "beat_type": "climax"},
            {"chapter": 14, "day": 7, "focus": "拒绝开口/折痕关闭/余韵",                "words": 6100, "beat_type": "cooldown"},
        ],
        "notes": "整体节奏呈「陡升-微落-炸尾」的心电图形态：前两章黄金三章拉满惊悚密度（咸水缢亡+空气溺亡），第7章短暂给主角一口喘息但埋童谣钩子，第9章林薇坠楼做中盘反转把强度重新顶起，11-13章连续高潮无喘息；终章 cooldown 段控制在6000字以内，给余韵但不拖沓。建议第4天（第7-8章）用红鲱鱼（合唱队招新海报）制造误导，避免中段塌腰；第5天线索分散两章释放，防止信息过载。",
    }


def register_rhythm_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable,
    chore_provider: Callable,
) -> None:
    deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        chore_provider=chore_provider,
    )
    router = APIRouter(tags=["rhythm"])
    prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/rhythm"

    # ----- POST /rhythm/analyze -----
    def _rhythm_analyze(
        pid: str,
        vol_no: int,
        body: _AnalyzeIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if not _all_scaffold_locked(vol):
            raise HTTPException(422, "请先完成副本详设：6 张卡必须全部锁定")

        density_map = _compute_density_map(vol)
        is_mock = _is_mock(vol)

        if is_mock:
            blueprint = _mock_blueprint()
        else:
            from ..generation import complete_structured
            parts: list[str] = []
            # 组装上下文
            cards = vol.scaffold_cards or {}
            def _card_text(key: str) -> str:
                entry = cards.get(key) or {}
                return entry.get("text") or ""
            parts.append("===== 已锁定副本核心设定 =====")
            parts.append(f"【核心反转】{_card_text('core_twist')}")
            parts.append(f"【唐迟起点】{_card_text('tangchi_start')}")
            parts.append(f"【代价上限】{_card_text('cost_cap')}")

            hc = vol.host_character or {}
            if hc:
                parts.append("\n===== 宿主角色 =====")
                parts.append(json.dumps(hc, ensure_ascii=False))
            stance = vol.stance or {}
            if stance:
                parts.append("\n===== 宿主姿态 =====")
                parts.append(json.dumps(stance, ensure_ascii=False))

            tso = vol.tangchi_story_outline
            if isinstance(tso, dict):
                parts.append("\n===== 唐迟七天求生大纲（入戏第1-7天 / T-6→T-0，含原剧本时间锚点）=====")
                parts.append(tso.get("outline_text") or json.dumps(tso, ensure_ascii=False)[:1500])
            elif isinstance(tso, str) and tso:
                parts.append(f"\n===== 唐迟七天求生大纲 =====\n{tso[:1500]}")

            parts.append("\n===== 已选定死亡节点 =====")
            parts.append(json.dumps(vol.death_nodes or [], ensure_ascii=False))
            parts.append("\n===== 已选定高潮节点 =====")
            parts.append(json.dumps(vol.climax_nodes or [], ensure_ascii=False))
            parts.append("\n===== 已选定线索节点 =====")
            parts.append(json.dumps(vol.clue_nodes or [], ensure_ascii=False))
            parts.append("\n===== 本地密度统计（death/climax/clue per day）=====")
            parts.append(json.dumps(density_map, ensure_ascii=False))

            rd = (body.rhythm_directive or "").strip()
            if rd:
                parts.append(f"\n===== 作者对节奏的额外要求 =====\n{rd}")

            context = "\n".join(parts)

            def _call() -> str:
                return deps.chore_provider("rhythm").chat([
                    {"role": "system", "content": _RHYTHM_SYSTEM},
                    {"role": "user", "content": context + "\n\n请严格按 system 给定 JSON schema 返回节奏蓝图（pacing_ratio/key_beats_progress/warnings/suggested_length/suggested_chapter_range/chapter_plan/notes），不要输出任何其他文字。"},
                ]).text

            try:
                comp = complete_structured(_call, max_regenerations=2)
                data = comp.data if isinstance(comp.data, dict) else {}
            except Exception as _e:
                log.warning("rhythm LLM 失败，回退 mock：%s", _e)
                data = _mock_blueprint()

            # 保底校验：关键字段缺失就回退 mock
            required_keys = {"pacing_ratio", "key_beats_progress", "warnings",
                             "suggested_length", "suggested_chapter_range",
                             "chapter_plan", "notes"}
            if not required_keys.issubset(data.keys()):
                log.warning("rhythm LLM 返回缺字段，回退 mock")
                data = _mock_blueprint()
            blueprint = data

        # 写入 volume.rhythm_blueprint
        vols.update_planning(vol_no, rhythm_blueprint=blueprint)
        return {
            "ok": True,
            "blueprint": blueprint,
            "density_map": density_map,
            "is_mock": is_mock,
        }

    # ----- POST /rhythm/finalize -----
    def _rhythm_finalize(
        pid: str,
        vol_no: int,
        body: _FinalizeIn = Body(...),
    ):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        if not vol.rhythm_blueprint:
            raise HTTPException(422, "请先推演节奏蓝图（POST /analyze）")
        sc = dict(vol.stage_completed or {})
        sc["rhythm"] = True
        vols.update_planning(vol_no, stage_completed=sc)
        vol = vols.get(vol_no)
        return {"ok": True, "stage_completed": vol.stage_completed, "volume": vol.as_dict()}

    # ----- GET /rhythm/ -----
    def _rhythm_get(pid: str, vol_no: int):
        deps.require_project(pid)
        vols = deps.volumes_for(pid)
        vol = vols.get(vol_no)
        if vol is None:
            raise HTTPException(404, "卷不存在")
        density_map = _compute_density_map(vol)
        return {
            "volume": vol.as_dict(),
            "blueprint": vol.rhythm_blueprint,
            "density_map": density_map,
        }

    # ---- 注册路由 ----
    router.add_api_route(f"{prefix}/analyze", _rhythm_analyze, methods=["POST"])
    router.add_api_route(f"{prefix}/finalize", _rhythm_finalize, methods=["POST"])
    router.add_api_route(f"{prefix}", _rhythm_get, methods=["GET"])

    app.include_router(router)
