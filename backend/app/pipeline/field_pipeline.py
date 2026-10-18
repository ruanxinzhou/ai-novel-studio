"""场级流水线编排器（Phase 2）。

串联 12 个新增层为一条"建一场→写一场→沉淀一场→间章"的节点链。
与章级 PipelineRunner 关系：
- 场流水线负责**前置准备**（adapt→COC→tang大纲→分章）和**后置沉淀**（distill→ash→间章）
- 分章后的"逐章写作"（plot/draft/polish）复用现有章级 run_chapter_stage 流程，
  因为章级流程已经能注入书级硬卡+讣告+模块规则
- 场流水线的节点产物存在 FieldRunStore（nodes/{field_no}/{node_id}.json），
  作者选定 variant 后再写入 FieldStore（authoritative 数据）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from app.context import Material, MaterialFamily
from app.fields import (
    DEFAULT_VARIANT_COUNTS,
    FIELD_PIPELINE_NODES,
    FieldRunStore,
    FieldStore,
    NodeArtifact,
)
from app.pipeline import PipelineRunner, SPECS
from app.pipeline.prompts import PromptStore


# ============================================================
# 输出 JSON 解析（variant-pool 节点）
# ============================================================

def _try_parse_json(text: str) -> Any:
    """宽容地从 LLM 输出里提取 JSON（可能带 ```json  fences 或前后文字）。

    策略：
    1) 先找 ```json ... ``` 代码块
    2) 否则找第一个 [ 或 { 作为起点，用括号配对找对应的结束括号，截取再 json.loads
    """
    if not text:
        return None
    t = text.strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
        try:
            return json.loads(t)
        except Exception:
            pass
    # 找第一个 [ 或 {
    open_arr = t.find("[")
    open_obj = t.find("{")
    if open_arr == -1 and open_obj == -1:
        return None
    # 选择更靠前的那一个；如果位置一样（不可能），优先数组
    if open_arr != -1 and (open_obj == -1 or open_arr < open_obj):
        start = open_arr
        open_ch, close_ch = "[", "]"
    else:
        start = open_obj
        open_ch, close_ch = "{", "}"
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(t)):
        c = t[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    candidate = t[start:end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


# ============================================================
# 节点输入材料装配
# ============================================================

def _base_materials(book_store, field_store, run_store, field_no: int) -> list[Material]:
    """所有场节点共享的基础材料：书级硬卡 + 本场讣告卡。"""
    mats: list[Material] = []
    if book_store is not None:
        mats.append(Material(
            source_id="book:hardcards", family=MaterialFamily.REQUIRED,
            content=book_store.render_all(),
            title="书级硬卡（铁律/唐迟刻度/终局锚；本阶段推演原剧本，不注入七天倒计时）",
        ))
    fw = field_store.get(field_no) if field_store is not None else None
    if fw is not None:
        mats.append(Material(
            source_id=f"field:{field_no}:obituary",
            family=MaterialFamily.REQUIRED,
            content=fw.obituary.render(field_no),
            title=f"第{field_no}场讣告卡",
        ))
        if fw.world_premise:
            mats.append(Material(
                source_id=f"field:{field_no}:world",
                family=MaterialFamily.CONTEXT,
                content=f"## 本场世界设定\n\n{fw.world_premise}",
                title=f"第{field_no}场世界基础",
            ))
    return mats


def _x_for_node(node_id: str, layer: str, field_store, run_store,
                field_no: int, desk=None, **kwargs) -> str:
    """按 node 定义的 x_source 装配 x_block。"""
    fw = field_store.get(field_no)
    if node_id == "adapt_scenario":
        # x = 讣告卡文字 + 作者填的 world_premise 种子
        parts = [fw.obituary.render(field_no) if fw else ""]
        if fw and fw.title:
            parts.insert(0, f"# 本场标题：{fw.title}\n")
        if fw and fw.world_premise:
            parts.append(f"## 作者提供的世界种子\n\n{fw.world_premise}\n")
        return "\n".join(parts)

    if node_id == "coc_outcome":
        # 优先使用 COC 多轮跑团日志（若已 finalize）；否则回退到单轮结论模式
        coc_state = None
        coc_store = kwargs.get("coc_store")
        if coc_store is not None:
            try:
                coc_state = coc_store.get(field_no)
            except Exception:
                coc_state = None
        if coc_state is not None and coc_state.final_summary:
            # 已经 finalize 过：直接复用 final_summary，避免重复调 LLM
            return (
                f"# COC 多轮跑团已产出真相（{len(coc_state.turns)} 回合，结局={coc_state.status}）\n\n"
                f"{coc_state.final_summary}\n\n"
                f"# 任务\n如无新的作者指令，请原样保留上面的真相摘要作为本节点输出。"
            )
        if coc_state is not None and coc_state.turns:
            # 跑团已进行但未 finalize：把完整日志喂给 coc_outcome 做归纳
            transcript = coc_state.transcript()
            chosen = run_store.chosen_parsed(field_no, "adapt_scenario")
            chosen_text = json.dumps(chosen, ensure_ascii=False, indent=2) if chosen else ""
            return (
                f"# 选定的模组方案\n\n{chosen_text}\n\n"
                f"# 跑团完整日志（{len(coc_state.turns)} 回合）\n\n{transcript}\n\n"
                f"# 当前状态\nSAN {coc_state.san}  HP {coc_state.hp}  阶段{coc_state.current_day}/7\n\n"
                f"# 任务\n把本场跑团日志整理成「原剧本真实事件经过」小说化梗概：\n"
                f"- 按故事自然推进分成若干关键阶段，最多 7 段；每段以「DayN：」开头（N 为阶段序号 1-7，不是日历天），每段 100-200 字；\n"
                f"- 模组时间线自由（小时/天/月/年均可），文本里用模组自身时间表述，不要硬凑 7 天；若模组短于 7 段，后续 Day 留空；\n"
                f"- 写出关键 NPC 动向、调查员选择与代价、最终结局；故事中后段回收至少 2 个伏笔。\n"
                f"不要出现骰子术语或玩家术语。"
            )
        # 回退：单轮结论（未启动 coc 仿真时兼容旧流程）
        chosen = run_store.chosen_parsed(field_no, "adapt_scenario")
        if chosen is None:
            chosen_text = run_store.chosen_text(field_no, "adapt_scenario") or ""
        else:
            chosen_text = json.dumps(chosen, ensure_ascii=False, indent=2)
        return (
            f"# 选定的模组方案\n\n{chosen_text}\n\n"
            f"# 任务\n请推演这一模组的「原剧本真实事件经过」（单轮结论，不需要投骰子和 KP 轮次）：\n"
            f"- 按故事自然推进分成若干关键阶段，最多 7 段；每段以「DayN：」开头（N 为阶段序号 1-7，不是日历天），每段 100-200 字；\n"
            f"- 模组时间线自由（小时/天/月/年均可），文本里用模组自身时间表述，不要硬凑 7 天；若模组短于 7 段，后续 Day 留空；\n"
            f"- 写出关键 NPC 动向、调查员的选择与代价、金手指（若有）使用与副作用、最终结局；\n"
            f"- 注意：此时还没有唐迟魂穿，本任务只是推演副本原剧本本身的走向，不要给任何角色套上「七天倒计时」。"
        )

    if node_id == "tang_outline":
        # x = 选定改编方案 + coc_outcome 真相 + 当前唐迟刻度
        adapt = run_store.chosen_parsed(field_no, "adapt_scenario")
        outcome = run_store.chosen_text(field_no, "coc_outcome") or ""
        parts = []
        if adapt:
            parts.append(f"# 选定模组方案\n\n{json.dumps(adapt, ensure_ascii=False, indent=2)}\n")
        parts.append(f"# COC 推演真相\n\n{outcome}\n")
        return "\n".join(parts)

    if node_id == "split_chapters":
        tang = run_store.chosen_parsed(field_no, "tang_outline")
        tang_text = json.dumps(tang, ensure_ascii=False, indent=2) if tang else (
            run_store.chosen_text(field_no, "tang_outline") or ""
        )
        return (
            f"# 选定的唐迟场大纲\n\n{tang_text}\n\n"
            f"# 分章任务\n请按指令拆分为具体章节 JSON，注意 Day 张力档位与章章有钩子。"
        )

    if node_id in ("distill_world", "distill_obsession"):
        # 需要前面所有节点产出
        parts = []
        for n in ("adapt_scenario", "coc_outcome", "tang_outline"):
            t = run_store.chosen_text(field_no, n)
            if t:
                parts.append(f"# 【{n}】\n{t}\n")
        return "\n".join(parts)

    if node_id in ("distill_entity", "epilogue_ash"):
        # 需要本场定稿正文
        parts = []
        tang = run_store.chosen_parsed(field_no, "tang_outline")
        if tang:
            parts.append(f"# 选定唐迟大纲\n\n{json.dumps(tang, ensure_ascii=False, indent=2)}\n")
        if desk is not None:
            fw = field_store.get(field_no)
            chs: list[int] = []
            if fw is not None:
                for dw in fw.days.values():
                    chs.extend(dw.chapters)
            from app.wizard import work_text
            body_parts = []
            for c in sorted(set(chs)):
                w = desk.get(c)
                if w is None:
                    continue
                txt = work_text(w, "polish") or work_text(w, "draft") or work_text(w, "plot") or ""
                if txt:
                    title = f" {w.title}" if getattr(w, "title", "") else ""
                    body_parts.append(f"## 第{c}章{title}\n{txt}")
            parts.append(f"# 本场定稿正文\n\n" + "\n\n".join(body_parts))
        return "\n".join(parts)

    if node_id == "interlude_expected":
        ash = run_store.get(field_no, "epilogue_ash")
        ash_text = ""
        if ash is not None and ash.parsed:
            ash_text = json.dumps(ash.parsed, ensure_ascii=False, indent=2)
        elif ash is not None:
            ash_text = ash.raw_text
        return f"# 本场杀青记录与残页\n\n{ash_text}\n"

    if node_id == "interlude_outline":
        exp = run_store.get(field_no, "interlude_expected")
        exp_text = ""
        if exp is not None and exp.parsed:
            exp_text = json.dumps(exp.parsed, ensure_ascii=False, indent=2)
        elif exp is not None:
            exp_text = exp.raw_text
        return f"# 间章必载清单\n\n{exp_text}\n"

    return ""


def _extra_materials_for_node(node_id: str, run_store, field_store,
                               field_no: int, desk=None,
                               chapters_text: str = "") -> list[Material]:
    """某些节点需要额外上下文物料（其他节点的 raw/parsed、章正文等）。"""
    mats: list[Material] = []
    return mats


# ============================================================
# 单节点运行
# ============================================================

def _run_coc_batch_variants(
    *,
    runner,
    field_no: int,
    book_store,
    field_store: FieldStore,
    run_store: FieldRunStore,
    coc_store,
    n_variants: int,
    user_directive: str | None,
    on_event,
) -> NodeArtifact:
    """coc_outcome 节点的特殊执行路径：批量跑 N 场多轮跑团仿真，压真相，产出 5 选 1 variants。
    复用 variant-pool 的 artifact 结构（variants[].{vid,title,text,parsed}），
    chosen 默认选第一场。"""
    from app.pipeline.coc_simulator import run_coc_simulation_batch

    adapt = run_store.chosen_parsed(field_no, "adapt_scenario")
    if not adapt:
        raise ValueError("adapt_scenario 尚未选定候选，不能跑 coc_outcome")

    if on_event:
        on_event("coc_batch_start", {"sessions": n_variants})

    variants_raw = run_coc_simulation_batch(
        runner,
        book_store=book_store, field_store=field_store,
        field_no=field_no, coc_store=coc_store, field_run_store=run_store,
        adapt_parsed=adapt,
        n_sessions=n_variants,
        user_directive=user_directive,
        on_event=on_event,
    )

    # 组装成 NodeArtifact 期望的 variants 结构
    art_variants = []
    combined_raw_parts = []
    for v in variants_raw:
        vid = str(v["vid"])
        # text 放 full summary；parsed 放整个 summary 卡（方便前端渲染摘要）
        text_dump = v.get("final_summary", "")
        art_variants.append({
            "vid": vid,
            "title": v.get("title", f"候选 #{vid}"),
            "text": text_dump,
            "parsed": v,    # one_liner/ending_bias/npcs/core_twist/... 都在里面
        })
        combined_raw_parts.append(f"## {v.get('title','')}\n\n{v.get('one_liner','')}\n\n---\n\n{text_dump}")

    art = run_store.get(field_no, "coc_outcome") or NodeArtifact(
        node_id="coc_outcome", layer="coc_outcome")
    art.raw_text = "\n\n".join(combined_raw_parts)
    art.variants = art_variants
    art.chosen_vid = art_variants[0]["vid"] if art_variants else None
    art.parsed = None
    art.meta = {
        "source": "coc_simulation_batch",
        "sessions": len(art_variants),
        "variant_titles": [v["title"] for v in art_variants],
    }
    run_store.save(field_no, art)
    if on_event:
        on_event("node_done", {"node_id": "coc_outcome",
                               "raw_chars": len(art.raw_text),
                               "variants": len(art.variants),
                               "parsed_ok": True})
    return art


def run_field_node(
    runner: PipelineRunner,
    pid: str,
    field_no: int,
    node_id: str,
    *,
    book_store=None,
    field_store: FieldStore,
    run_store: FieldRunStore,
    desk=None,
    coc_store=None,
    user_directive: str | None = None,
    force: bool = False,
    n_variants: int | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> NodeArtifact:
    """跑指定节点；返回新保存的 NodeArtifact。

    variant-pool 节点：n_variants 控制在提示词后追加「生成 N 个」的指令，
    并把 LLM 返回的 JSON 数组拆进 art.variants，自动选第一个；用户可之后重选。
    非 variant 节点：直接存 raw_text + 尝试解析 JSON。
    """
    # 找到节点定义
    node_def = next((d for d in FIELD_PIPELINE_NODES if d[0] == node_id), None)
    if node_def is None:
        raise ValueError(f"未知场流水线节点：{node_id}")
    _, layer, is_variant, _ = node_def
    spec = SPECS[layer]

    fw = field_store.get(field_no)
    if fw is None:
        raise ValueError(f"场不存在：{field_no}")

    # 前置校验：节点必须按顺序跑，除非 force
    if not force:
        node_ids = [d[0] for d in FIELD_PIPELINE_NODES]
        idx = node_ids.index(node_id)
        for prev in node_ids[:idx]:
            prev_art = run_store.get(field_no, prev)
            # 分界点之后的节点不要求前置（distill_* 需要作者写完正文，由上层 API 校验）
            if prev in ("split_chapters",):
                # split_chapters 之后进入人工写作，distill_* 之前不强制要求前置
                continue
            if prev_art is None:
                raise ValueError(f"前置节点 [{prev}] 尚未完成，不能跑 [{node_id}]")
            if prev_art.variants and not prev_art.chosen_vid:
                raise ValueError(f"前置节点 [{prev}] 有候选但未选定，不能跑 [{node_id}]")

    # 组装 x 与 materials
    x_block = _x_for_node(node_id, layer, field_store, run_store, field_no, desk=desk,
                          coc_store=coc_store)
    materials = _base_materials(book_store, field_store, run_store, field_no)
    materials.extend(_extra_materials_for_node(
        node_id, run_store, field_store, field_no, desk=desk))

    # variant 节点在 user_directive 里追加候选数量
    directive = user_directive or ""
    if is_variant:
        n = n_variants or DEFAULT_VARIANT_COUNTS.get(node_id, 3)
        directive = (directive + "\n\n" if directive else "") + f"请严格生成 {n} 个候选，每个带唯一 vid（1-{n}）。"

    if on_event:
        on_event("node_start", {"node_id": node_id, "layer": layer})

    # ---- coc_outcome 特判：走多场仿真 variant-pool ----
    if node_id == "coc_outcome":
        return _run_coc_batch_variants(
            runner=runner,
            field_no=field_no,
            book_store=book_store, field_store=field_store,
            run_store=run_store, coc_store=coc_store,
            n_variants=n_variants or DEFAULT_VARIANT_COUNTS.get("coc_outcome", 5),
            user_directive=directive or None, on_event=on_event,
        )

    result = runner.run_layer(
        spec, [(x_block, "")], materials=materials,
        user_directive=directive or None,
        on_event=on_event,
    )
    raw = result.y

    art = run_store.get(field_no, node_id) or NodeArtifact(node_id=node_id, layer=layer)
    art.raw_text = raw
    art.meta = {
        "attempts": sum(c.attempts for c in result.calls),
        "calls": [{"completion_status": c.completion_status, "attempts": c.attempts}
                  for c in result.calls],
    }

    parsed = _try_parse_json(raw)
    if is_variant:
        if isinstance(parsed, list):
            art.variants = []
            for i, item in enumerate(parsed):
                if isinstance(item, dict):
                    if "vid" not in item:
                        item["vid"] = str(i + 1)
                    text_dump = json.dumps(item, ensure_ascii=False, indent=2)
                    art.variants.append({
                        "vid": str(item["vid"]),
                        "text": text_dump,
                        "parsed": item,
                    })
            # 默认选第一个
            if art.variants and not art.chosen_vid:
                art.chosen_vid = art.variants[0]["vid"]
        else:
            # 解析失败——当单文本存（用户可以重跑）
            art.variants = []
            art.chosen_vid = None
            art.meta["parse_error"] = "expected JSON array"
        art.parsed = None
    else:
        art.parsed = parsed
        art.variants = []
        art.chosen_vid = None

    run_store.save(field_no, art)
    if on_event:
        on_event("node_done", {"node_id": node_id, "raw_chars": len(raw),
                               "variants": len(art.variants),
                               "parsed_ok": parsed is not None})
    return art


def apply_split_to_field(field_store, run_store, desk, field_no: int,
                         start_chapter: int, override: dict | None = None) -> dict:
    """把 split_chapters 节点的解析结果应用到 FieldStore + DeskStore。

    override: 允许作者在 API 层传修改后的 chapters 数组（前端编辑分章结果）。
    start_chapter: 本场起始章号（由作者/上层根据当前 desk 最大章号算出）。
    返回 {assignments: [...], chapters: [...]}。
    """
    data = override if override is not None else run_store.chosen_parsed(field_no, "split_chapters")
    if not data or "chapters" not in data:
        raise ValueError("split_chapters 结果尚未就绪或解析失败")

    chapters_in = data["chapters"]
    # 重新编号（从 start_chapter 开始）
    assignments: list[dict] = []
    day_map: dict[int, list[int]] = {}
    inter_chs: list[int] = []
    chapter_meta: list[dict] = []
    chno = start_chapter
    for ch in chapters_in:
        day = ch.get("day")
        is_inter = bool(ch.get("is_interlude"))
        title = str(ch.get("title", "") or "")
        summary = str(ch.get("summary", "") or "")
        hook = str(ch.get("required_hook", "") or "")
        rec = {"chapter": chno, "title": title, "summary": summary, "required_hook": hook}
        if is_inter:
            rec["is_interlude"] = True
            inter_chs.append(chno)
        elif isinstance(day, int) and 1 <= day <= 7:
            rec["day"] = day
            day_map.setdefault(day, []).append(chno)
        else:
            rec["day"] = None
        chapter_meta.append(rec)
        chno += 1

    fw = field_store.get(field_no)
    if fw is None:
        raise ValueError(f"场不存在：{field_no}")

    # 先清空旧的章节分配
    for d in list(fw.days.keys()):
        fw.days[d].chapters = []
    fw.interlude_chapters = []
    fw.ensure_days()
    for d, chs in day_map.items():
        field_store.assign_chapters_to_day(field_no, d, chs)
        # 回填 summary/hook
        fw = field_store.get(field_no)
        dw = fw.days.get(d)
        if dw is not None:
            # 从 chapters_in 中找第一个该 day 章的 summary/hook 作为 DayWork 初值
            for ch in chapters_in:
                if int(ch.get("day", 0) or 0) == d:
                    if not dw.summary and ch.get("summary"):
                        dw.summary = f"开场：{ch['summary']}"[:200]
                    if not dw.required_hook and ch.get("required_hook"):
                        dw.required_hook = ch["required_hook"]
                    break
    field_store.assign_interlude_chapters(field_no, inter_chs)
    fw = field_store.get(field_no)
    for d, chs in day_map.items():
        field_store.assign_chapters_to_day(field_no, d, chs)
    field_store.assign_interlude_chapters(field_no, inter_chs)

    # 同步 DeskStore 章 meta
    if desk is not None:
        for rec in chapter_meta:
            c = rec["chapter"]
            w = desk.ensure(c, title=rec["title"])
            desk.assign_field(c, field_no=field_no,
                              day=rec.get("day"),
                              is_interlude=rec.get("is_interlude", False))

    return {"field": field_store.get(field_no).as_dict(),
            "chapter_meta": chapter_meta,
            "start_chapter": start_chapter,
            "end_chapter": chno - 1}


def apply_epilogue_to_book(book_store, run_store, field_no: int,
                            override: dict | None = None) -> dict:
    """把 epilogue_ash 的结果写入 BookStore：remaining_pages + finale_notes。"""
    data = override if override is not None else None
    if data is None:
        art = run_store.get(field_no, "epilogue_ash")
        if art is not None and art.parsed:
            data = art.parsed
        elif art is not None:
            data = _try_parse_json(art.raw_text)
    if not data:
        raise ValueError("epilogue_ash 结果尚未就绪")
    pages = list(book_store.remaining_pages())
    notes = list(book_store.finale_notes())
    ash = str(data.get("remaining_page_ash", "") or "").strip()
    one_liner = str(data.get("finale_one_liner", "") or "").strip()
    if ash:
        pages.append({"field_no": field_no, "ash_text": ash})
        book_store.update("remaining_pages", pages[-200:])  # 截断防无限增长
    if one_liner:
        notes.append({"field_no": field_no, "one_liner": one_liner})
        book_store.update("finale_notes", notes[-200:])
    return {"remaining_page_ash": ash, "finale_one_liner": one_liner}


def apply_obsession_to_book(book_store, run_store, field_no: int,
                             override: dict | None = None) -> dict:
    """把 distill_obsession 结果写入 cost_debts + tangchi craft/erosion。"""
    data = override if override is not None else None
    if data is None:
        art = run_store.get(field_no, "distill_obsession")
        if art is not None and art.parsed:
            data = art.parsed
        elif art is not None:
            data = _try_parse_json(art.raw_text)
    if not data:
        raise ValueError("distill_obsession 结果尚未就绪")

    tangchi = dict(book_store.tangchi_raw())
    # 追加新 craft
    crafts = list(tangchi.get("crafts") or [])
    existing_names = {c.get("name") for c in crafts}
    for name in (data.get("tangchi_crafts_added") or []):
        if name and name not in existing_names:
            crafts.append({"name": name, "unlocked_at": field_no, "cost_paid": 0})
            existing_names.add(name)
    tangchi["crafts"] = crafts
    # 调整侵蚀度
    try:
        delta = int(data.get("erosion_delta") or 0)
    except Exception:
        delta = 0
    cur = int(tangchi.get("erosion") or 0)
    tangchi["erosion"] = max(0, min(100, cur + delta))
    book_store.update("tangchi", tangchi)

    # 追加未兑现代价
    debts = list(book_store.cost_debts())
    obs_name = str(data.get("obsession_name") or f"第{field_no}场执念")
    for se in data.get("remaining_cost") or []:
        debts.append({
            "field_no": field_no,
            "obsession_name": obs_name,
            "side_effect": str(se),
            "due_hint": "近场",
            "paid": False,
        })
    book_store.update("cost_debts", debts[-200:])
    return {"erosion": tangchi["erosion"], "crafts_added": len(crafts) - len(book_store.tangchi_raw().get("crafts") or []) + len(crafts),
            "debts_added": len(data.get("remaining_cost") or [])}


def apply_world_rules(entity_store, run_store, field_no: int) -> dict:
    """把 distill_world 产出的规则卡写入 entity_store 作为 plotline 实体（跨场可复用）。
    规则名形如"伞骨缝七日"，details 放 description+evidence+reusability。"""
    art = run_store.get(field_no, "distill_world")
    if art is None or not art.parsed:
        return {"imported": 0, "created": [], "updated": [], "dropped": []}
    rules = art.parsed if isinstance(art.parsed, list) else []
    # 转换成 entity 字典喂给 merge_extracted
    items = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        desc = str(r.get("description", "")).strip()
        evid = str(r.get("evidence", "")).strip()
        reuse = str(r.get("reusability", "")).strip()
        details_parts = []
        if desc: details_parts.append(desc)
        if evid: details_parts.append(f"体现：{evid}")
        if reuse: details_parts.append(f"复用：{reuse}")
        items.append({
            "type": "plotline",
            "name": f"【世界规则】{name}",
            "summary": desc or name,
            "details": "\n".join(details_parts),
        })
    if entity_store is not None and items:
        res = entity_store.merge_extracted(items)
        return {"imported": len(res.get("created", [])) + len(res.get("updated", [])), **res}
    return {"imported": 0, "created": [], "updated": [], "dropped": []}


def apply_entities(entity_store, run_store, field_no: int) -> dict:
    """把 distill_entity 产出的实体列表合并回 EntityStore。"""
    art = run_store.get(field_no, "distill_entity")
    if art is None or not art.parsed:
        return {"created": [], "updated": [], "dropped": []}
    items = art.parsed if isinstance(art.parsed, list) else []
    if entity_store is None or not items:
        return {"created": [], "updated": [], "dropped": [{"reason": "no_store"}],
                "received": len(items)}
    return entity_store.merge_extracted(items)
