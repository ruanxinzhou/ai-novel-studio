"""项目状态账本：把「已写状态」实时推导成可注入材料。

续写大纲时作者只给新方向，机器必须先知道前面实际写成了什么样：
- 已写剧情（FINALIZED）：章台逐章 plot 梗概——实际发生了什么
- 正文近况（FINALIZED）：最近已写正文的结尾——文风与进度衔接
- 实体账本（REQUIRED）：角色/地点/道具/情节线清单 + 未回收伏笔债务

全部从项目数据实时推导、不落快照——账实永不分离。
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.context.assembler import truncate_tail_at_sentence
from app.context.material import Material, MaterialFamily
from app.entities.store import TYPE_LABELS, EntityStore

if TYPE_CHECKING:      # 仅类型标注；运行时由调用方传入，避免 wizard→context→wizard 环
    from app.wizard.store import DeskStore

PLOT_DIGEST_MAX_CHAPTERS = 40      # 剧情梗概最多带多少章（超出留最近段）
PLOT_PER_CHAPTER_CHARS = 160       # 每章梗概截断
DRAFT_RECENT_CHARS = 6_000         # 正文近况字符上限
ENTITY_SECTION_LIMITS = {"character": 40, "location": 25, "item": 25, "plotline": 15}
FORESHADOW_LISTED = 40             # 未回收伏笔最多列多少条
_SUMMARY_CHARS = 40                # 实体简介截断


def _stage_text(work, stage: str) -> str:
    d = getattr(work, stage, None) if work else None
    return (d.get("text", "") if d else "").strip()


def _ch_span(chapters: list[int]) -> str:
    if not chapters:
        return ""
    if len(chapters) == 1:
        return f"第{chapters[0]}章"
    return f"第{min(chapters)}-{max(chapters)}章"


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _plot_digest(desk: DeskStore) -> Material | None:
    lines: list[str] = []
    for no in desk.all_nos():
        work = desk.get(no)
        plot = _stage_text(work, "plot")
        if not plot:
            continue
        title = f" {work.title}" if work and work.title else ""
        lines.append(f"第{no}章{title}：{_clean(plot, PLOT_PER_CHAPTER_CHARS)}")
    if not lines:
        return None
    total = len(lines)
    if total > PLOT_DIGEST_MAX_CHAPTERS:
        lines = lines[-PLOT_DIGEST_MAX_CHAPTERS:]
    head = f"已写章节的实际剧情（章台逐章记录，共 {total} 章"
    if total > PLOT_DIGEST_MAX_CHAPTERS:
        head += f"，此处列最近 {len(lines)} 章，更早章节见已有大纲"
    content = head + "）：\n" + "\n".join(lines)
    return Material(source_id="state:plot-digest", family=MaterialFamily.FINALIZED,
                    content=content, title="已写剧情梗概")


def _draft_recent(project_store, desk: DeskStore, pid: str) -> Material | None:
    for no in sorted(desk.all_nos(), reverse=True):
        work = desk.get(no)
        text = _stage_text(work, "polish") or _stage_text(work, "draft")
        if not text:
            continue
        title = f" {work.title}" if work and work.title else ""
        body = truncate_tail_at_sentence(text, DRAFT_RECENT_CHARS)
        return Material(source_id="state:draft-tail", family=MaterialFamily.FINALIZED,
                        content=f"第{no}章{title}\n{body}",
                        title=f"正文近况（第{no}章，实际写到这）")
    v = project_store.current(pid, "draft")
    if v is not None and v.y.strip():
        return Material(source_id="state:draft-tail", family=MaterialFamily.FINALIZED,
                        content=truncate_tail_at_sentence(v.y, DRAFT_RECENT_CHARS),
                        title="正文近况（正文层结尾）")
    return None


def _entity_lines(entities: list, limit: int) -> list[str]:
    lines: list[str] = []
    for e in entities[:limit]:
        seg = e.name
        if e.summary:
            seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
        if e.appearances:
            seg += f"（{_ch_span(e.appearances)}）"
        if e.status == "retired":
            seg += "〔已退场〕"
        lines.append(f"- {seg}")
    if len(entities) > limit:
        lines.append(f"（其余 {len(entities) - limit} 个略）")
    return lines


def _entity_ledger(store: EntityStore) -> Material | None:
    sections: list[str] = []
    for etype in ("character", "location", "item", "plotline"):
        ents = store.list(type=etype)
        if not ents:
            continue
        ents.sort(key=lambda e: (-len(e.appearances), e.name))
        sections.append(f"【{TYPE_LABELS[etype]}】\n"
                        + "\n".join(_entity_lines(ents, ENTITY_SECTION_LIMITS[etype])))

    planted = store.list(type="foreshadow", status="planted")
    paid = len(store.list(type="foreshadow", status="paid"))
    dropped = len(store.list(type="foreshadow", status="dropped"))
    if planted:
        planted.sort(key=lambda e: (min(e.appearances) if e.appearances else 10**9, e.name))
        lines = []
        for e in planted[:FORESHADOW_LISTED]:
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            if e.appearances:
                seg += f"（第{min(e.appearances)}章埋）"
            lines.append(f"- {seg}")
        if len(planted) > FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - FORESHADOW_LISTED} 条略）")
        if paid or dropped:
            lines.append(f"（另有已回收 {paid} 条、已弃 {dropped} 条，不再列出）")
        sections.append("【未回收伏笔】（已埋未收——续写时优先安排推进或兑现）\n" + "\n".join(lines))

    if not sections:
        return None
    head = ("本书当前实体与伏笔账本（实时状态）。续写新章必须延续：已出场角色、地点、道具与设定"
            "不得重设或自相矛盾；未回收伏笔是新章的待办债务，应在新段中安排推进或兑现。")
    return Material(source_id="state:entities", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n\n".join(sections),
                    title="实体账本（角色/地点/道具/情节线/未回收伏笔）")


def build_state_materials(project_store, entity_store: EntityStore,
                          desk: DeskStore, pid: str,
                          book_store=None, field_store=None) -> list[Material]:
    """项目已写状态 → 三类材料（有什么带什么，没有的不硬造）。
    book_store/field_store 传 None 时不注入书卡（旧项目/通用项目兼容）。"""
    mats: list[Material] = []
    for m in (_plot_digest(desk), _draft_recent(project_store, desk, pid),
              _entity_ledger(entity_store)):
        if m is not None:
            mats.append(m)
    if book_store is not None:
        try:
            mats.append(Material(
                source_id="book:hardcards", family=MaterialFamily.REQUIRED,
                content=book_store.render_all(), title="书级硬卡（铁律/双轨时间线锚/唐迟刻度/终局锚）",
            ))
        except Exception:
            pass
    return mats


# ---- 开章任务书（章台写第 N 章前的机器自检，五段结构） ----

CHECKLIST_SECTION_LIMITS = {"character": 20, "location": 10, "item": 12, "plotline": 8}
CHECKLIST_FORESHADOW_LISTED = 15     # 到期伏笔最多列多少条
FORESHADOW_DUE_AFTER = 8             # 账龄超过多少章 → 本章必须处理
FORESHADOW_HARD_AFTER = 12           # 账龄超过多少章 → 硬债务（最老一批尽快处理）
OPENING_TAIL_CHARS = 400             # 【开篇委托】带的上一章结尾长度

# 兑现节奏（P2③）：空账期用拆书经验目标线垫底，攒了自家数据用自家的
PAYOFF_TARGET_SMALL = 3              # 小爽点目标章距（天蚕土豆：2-3 章一次）
PAYOFF_TARGET_MEDIUM = 12            # 中兑现目标章距（10-15 章一次取中值）
PAYOFF_HUNGER_AFTER = 4              # 连续多少章无兑现报「节奏饥饿」
_PAID_SCALES = ("small", "medium", "climax")

# 三线节奏（Strand）：quest 主线 / fire 冲突 / bond 羁绊——常量归本模块，
# wizard.store 仅引用做入参校验（运行时反向 import 会成环）
STRANDS = ("quest", "fire", "bond")
STRAND_LABELS = {"quest": "主线", "fire": "冲突", "bond": "羁绊"}
STRAND_WINDOW = 20                   # 占比统计窗口：最近多少个已写章
STRAND_TARGET_SHARES = {"quest": 0.6, "fire": 0.2, "bond": 0.2}
STRAND_GAP_WARNS = {"quest": 5, "fire": 10, "bond": 15}   # 断档红线：连续 N 个已写章未推进


def _payoff_records(desk: DeskStore, chapter: int) -> list[tuple[int, str]]:
    """截至上一章的兑现记录 [(章号, 规模)]，按章号升序；排除本章保证幂等。"""
    out = []
    for no in desk.all_nos():
        if no >= chapter:
            continue
        w = desk.get(no)
        if w is not None and w.payoff:
            out.append((no, w.payoff))
    return out


def _prior_written(desk: DeskStore, chapter: int) -> list[int]:
    """截至上一章、已有剧情或正文的章号升序列表（plot 算开工，draft 算成文）。"""
    out = []
    for no in desk.all_nos():
        if no >= chapter:
            continue
        work = desk.get(no)
        if work is not None and (_stage_text(work, "plot") or _stage_text(work, "draft")):
            out.append(no)
    return out


def _strand_tag_map(desk: DeskStore, nos: list[int]) -> dict[int, list[str]]:
    return {no: list(getattr(desk.get(no), "strands", []) or []) for no in nos}


def _rhythm_section(desk: DeskStore, chapter: int, written: bool) -> str | None:
    """节奏位置块：距上次兑现几章、该蓄还是该爆；无自家数据退化为目标线。"""
    records = _payoff_records(desk, chapter)
    if records:
        last_no, last_scale = max(records)
        gap = chapter - last_no
        seg = f"距上次兑现已 {gap} 章（第 {last_no} 章"
        if last_scale in ("medium", "climax"):
            seg += "，中兑现以上"
        seg += "）"
        if gap >= PAYOFF_HUNGER_AFTER:
            seg += f"——超过 {PAYOFF_HUNGER_AFTER} 章未喂，本章宜安排小爽点及以上兑现"
        medium_last = max((no for no, s in records if s in ("medium", "climax")), default=0)
        if medium_last:
            seg += f"；距上次中兑现 {chapter - medium_last} 章"
        return "【节奏位置】\n" + seg
    if not written:
        return None
    return ("【节奏位置】（本书尚无兑现记录，先用目标线垫底：小爽点约 "
            f"{PAYOFF_TARGET_SMALL} 章一次、中兑现约 {PAYOFF_TARGET_MEDIUM} 章一次；"
            "定稿时点选「本章兑现」四选一，攒 20 章即有自家量纲）")


def rhythm_hunger(desk: DeskStore, chapter: int) -> str | None:
    """节奏饥饿（审稿附加项，纯数据推导不走 LLM）：从本章往回数连续无兑现的
    已写章数（none 或未记录都算无兑现），≥ PAYOFF_HUNGER_AFTER 报警。"""
    streak = 0
    for no in range(chapter, 0, -1):
        w = desk.get(no)
        if w is None or not (_stage_text(w, "plot") or _stage_text(w, "draft")):
            break                       # 未写的章不算
        if w.payoff in _PAID_SCALES:
            break
        streak += 1
    if streak >= PAYOFF_HUNGER_AFTER:
        return (f"节奏饥饿：已连续 {streak} 章无兑现"
                f"（目标线：小爽点约 {PAYOFF_TARGET_SMALL} 章一次），本章宜安排兑现")
    return None


# ---- 三线节奏（纯数据推导） ----

def strand_warnings(desk: DeskStore, chapter: int) -> list[str]:
    """三线断档/占比报警（审稿附加项，不走 LLM）。

    断档：某线连续 STRAND_GAP_WARNS 个已写章未推进 → 报警。
    占比：近 STRAND_WINDOW 个已写章中某线覆盖率不足目标一半（≥10 章样本）→ 报警。
    打卡率低（近 20 章超三成没点三线标签）→ 提示补打卡。
    """
    prior = _prior_written(desk, chapter)
    if not prior:
        return []
    tag_map = _strand_tag_map(desk, prior)
    warns: list[str] = []
    for strand in STRANDS:
        streak = 0
        for no in reversed(prior):
            if strand in tag_map[no]:
                break
            streak += 1
        if streak >= STRAND_GAP_WARNS[strand]:
            warns.append(f"{STRAND_LABELS[strand]}线断档：已连续 {streak} 章未推进"
                         f"（红线 {STRAND_GAP_WARNS[strand]} 章），本章宜安排")
    window = prior[-STRAND_WINDOW:]
    if len(window) >= 10:
        untagged = sum(1 for no in window if not tag_map[no])
        if untagged * 3 > len(window):
            warns.append(f"近 {len(window)} 章有 {untagged} 章未点三线标签——"
                         "定稿时顺手点选本章推进了 主线/冲突/羁绊 哪些线，占比账才准")
        for strand in STRANDS:
            share = sum(1 for no in window if strand in tag_map[no]) / len(window)
            target = STRAND_TARGET_SHARES[strand]
            if share < target / 2:
                warns.append(f"{STRAND_LABELS[strand]}线近 {len(window)} 章覆盖率 {share:.0%}"
                             f"（目标约 {target:.0%}）——偶尔回头推一推，别一直冷着")
    return warns


def _strand_section(desk: DeskStore, chapter: int, written_nos: list[int]) -> str | None:
    """【怎么写更顺】里的三线近况块：每条线距上次推进几章 + 本章宜覆盖提示。"""
    prior = [no for no in written_nos if no < chapter]
    if not prior:
        return None
    tag_map = _strand_tag_map(desk, prior)
    if not any(tag_map.values()):
        return ("三线近况：本书尚未记录三线标签（定稿时点选本章推进了 主线/冲突/羁绊 哪些线）。"
                "参考配比：主线约六成章节要推，冲突与羁绊各约两成——主线断 5 章、冲突断 10 章、"
                "羁绊断 15 章就算断档。")
    lines = []
    for strand in STRANDS:
        label = STRAND_LABELS[strand]
        lasts = [no for no in prior if strand in tag_map[no]]
        if lasts:
            last = max(lasts)
            gap = chapter - last
            seg = f"- {label}线：距上次推进 {gap} 章（第 {last} 章）"
            if gap > STRAND_GAP_WARNS[strand]:
                seg += "——已超断档红线，本章宜安排推进"
        else:
            seg = f"- {label}线：本书尚未推进过"
        lines.append(seg)
    return "三线近况（参考配比：主线约六成、冲突与羁绊各约两成）：\n" + "\n".join(lines)


def _opening_commission(desk: DeskStore, chapter: int) -> str | None:
    """【开篇委托】：上一章实际结尾 + 接棒要求。"""
    for no in sorted((n for n in desk.all_nos() if n < chapter), reverse=True):
        work = desk.get(no)
        tail = _stage_text(work, "polish") or _stage_text(work, "draft")
        if not tail:
            continue
        body = truncate_tail_at_sentence(tail, OPENING_TAIL_CHARS)
        return ("【开篇委托】（上一章实际写到这——本章开篇必须接住它的变化，不另起炉灶）\n"
                f"第{no}章结尾：{body}")
    return None


def _written_nos(desk: DeskStore) -> list[int]:
    """已有剧情或正文的章（plot 阶段算开工，draft 算成文）。"""
    out = []
    for no in desk.all_nos():
        work = desk.get(no)
        if _stage_text(work, "plot") or _stage_text(work, "draft"):
            out.append(no)
    return out


def _build_volume_and_shader_context(volume, cur_day: int | None, tangchi_data: dict | None) -> str:
    """把 Volume 的魂穿宿主/信息差/核心诡计/当日节点 + 唐迟污染度感官滤镜拼为 REQUIRED 段落。
    这是 v2.0「唐迟七天求生追查死因」核心叙事引擎的上下文锚：AI 必须时刻知道唐迟顶着谁的壳、
    此刻是他入戏第几天、离 T-0 死劫还有几天、什么是他以为的「假死因」什么是「真死因底牌」、
    他现在异化到什么程度、以及【原剧本故事】此刻正在哪个事件（双轨时间线并行）。
    注意：cur_day 指唐迟入戏第几天（T-7+cur_day），不是原剧本的 Day N；原剧本自身的时间线按副本改编逻辑独立推进。"""
    parts: list[str] = []
    if volume is not None:
        parts.append(f"**当前副本卷**：第 {volume.volume_no} 卷《{volume.title or '未命名副本'}》")
        hc = volume.host_character or {}
        if isinstance(hc, dict) and hc.get("name"):
            identity = hc.get("identity") or hc.get("role") or ""
            death = hc.get("seventh_day_death") or hc.get("original_fate") or "（讣告未明确）"
            module_pos = hc.get("original_story_position", "")
            pos_str = f"｜原剧本入戏点：{module_pos}" if module_pos else ""
            parts.append(
                f"**唐迟本卷魂穿宿主**：{hc.get('name')}"
                f"（表面身份：{identity}｜T-0 死劫：{death}{pos_str}）"
            )
        if volume.info_gap:
            ig = volume.info_gap if isinstance(volume.info_gap, str) else json.dumps(volume.info_gap, ensure_ascii=False)
            parts.append(f"**唐迟当前信息差与伪装困境**：{ig}")
        st = volume.stance
        if st:
            attitude = st.get("title") or st.get("attitude") if isinstance(st, dict) else str(st)
            parts.append(f"**本卷求生与查因策略**：{attitude}")
        if volume.entry_point:
            parts.append(f"**本章切入点**：{volume.entry_point}")

        cards = volume.scaffold_cards or {}
        def _card_text(key: str) -> str:
            c = cards.get(key)
            if isinstance(c, dict):
                t = c.get("text")
                if t:
                    return str(t)
                vs = c.get("variants") or []
                pi = c.get("picked_idx")
                if isinstance(pi, int) and 0 <= pi < len(vs):
                    picked = vs[pi]
                    if isinstance(picked, str):
                        return picked
            return ""

        twist = _card_text("core_twist")
        cost_cap = _card_text("cost_cap")
        start = _card_text("tangchi_start")
        if start:
            parts.append(f"**【卷首锚·唐迟睁眼第一秒】**：\n{start}")
        if twist:
            parts.append(
                f"**【全卷底牌·原主在原剧本中必死的真正原因】（全局硬约束：严禁让唐迟在入戏第1-3天提前看穿，"
                f"必须经过第4-5天反转才揭晓；但写作时你心里必须始终装着这个底）**：\n{twist}"
            )
        if cost_cap:
            parts.append(f"**【本卷代价上限】（T-0破局必须付出的对等代价，不可减免）**：{cost_cap}")

        # 当日节点：death/climax/clue；注意 death_nodes/climax_nodes 可能是三维数组
        # （[版A, 版B, 版C] 每版一个 list），desk._join_by_layer 用的是 picked 版；这里做扁平提取
        def _picked_list(key: str) -> list[dict]:
            c = cards.get(key)
            arr = getattr(volume, key, None) or []
            if isinstance(c, dict):
                pi = c.get("picked_idx")
                vs = c.get("variants") or []
                if isinstance(pi, int) and 0 <= pi < len(vs) and isinstance(vs[pi], list):
                    return [n for n in vs[pi] if isinstance(n, dict)]
            # fallback：扁平提取所有版中 day == cur_day 的节点
            picked: list[dict] = []
            for ver in (arr or []):
                if not isinstance(ver, list):
                    continue
                for n in ver:
                    if isinstance(n, dict) and int(n.get("day") or 0) == (cur_day or 0):
                        picked.append(n)
            return picked

        if cur_day is not None:
            day_deaths = _picked_list("death_nodes")
            day_climaxes = _picked_list("climax_nodes")
            # clue_nodes 二维
            c_clue = cards.get("clue_nodes") if isinstance(cards.get("clue_nodes"), dict) else None
            day_clues: list[dict] = []
            if c_clue:
                pi = c_clue.get("picked_idx")
                vs = c_clue.get("variants") or []
                if isinstance(pi, int) and 0 <= pi < len(vs) and isinstance(vs[pi], list):
                    day_clues = [n for n in vs[pi] if isinstance(n, dict) and int(n.get("day") or 0) == cur_day]
            if not day_clues:
                for ver in (getattr(volume, "clue_nodes", None) or []):
                    if isinstance(ver, list):
                        for n in ver:
                            if isinstance(n, dict) and int(n.get("day") or 0) == cur_day:
                                day_clues.append(n)

            day_lines: list[str] = [f"**【副本 Day {cur_day} 锁定节点】**："]
            # fate_layer 前缀
            layer_label = {
                "doomed": "[原剧本死劫·DOOMED · 唐迟预见但大概率救不下——先知感/信息差核心]",
                "diverted": "[唐迟改命·DIVERTED · 他介入救下/转移，但必须当场付代价]",
                "escalated": "[改命反噬·ESCALATED · 他以为救成了，反而触发更严重污染/更多死亡]",
            }
            seen_layers: set[str] = set()
            for d in day_deaths:
                layer = str(d.get("fate_layer") or "").strip().lower()
                pref = layer_label.get(layer, "")
                who = d.get("who") or "？"
                how = d.get("how") or ""
                cost = d.get("cost") or ""
                tease = d.get("tease") or ""
                line = f"- [死亡] {pref} {who}：{how}"
                if cost:
                    line += f"\n    死因启示/代价：{cost}"
                if tease:
                    line += f"\n    事前预兆：{tease}"
                day_lines.append(line)
                if layer:
                    seen_layers.add(layer)
            for c in day_climaxes:
                layer = str(c.get("fate_layer") or "").strip().lower()
                pref = layer_label.get(layer, "")
                title = c.get("title") or "？"
                beat = c.get("beat") or ""
                w = c.get("weight")
                weight_tag = f"（权重 {w}）" if w else ""
                day_lines.append(f"- [高潮转折] {pref} {title}{weight_tag}：{beat}")
                if layer:
                    seen_layers.add(layer)
            for cl in day_clues:
                tag = "【红鲱鱼/假死因误导】" if cl.get("is_red_herring") else "【真死因线索】"
                where = cl.get("where") or ""
                what = cl.get("what") or ""
                leads = cl.get("leads_to") or ""
                day_lines.append(f"- {tag}（地点：{where}）：{what} ➔ 指向：{leads}")
            if len(day_lines) > 1:
                parts.append("\n".join(day_lines))
            if not seen_layers.intersection({"doomed", "diverted", "escalated"}) and cur_day:
                # 数据缺 fate_layer 时加一句软提醒，不报错
                parts.append(
                    f"（提醒：Day {cur_day} 节点暂未标注 fate_layer——请按「原剧本死劫/唐迟改命/改命反噬」"
                    f"三类各自至少一个来组织本章戏剧冲突）"
                )

    # 污染度与旧卷残影感官滤镜
    td = tangchi_data or {}
    erosion = int(td.get("erosion_level") or td.get("contamination_level") or 0)
    echoes = td.get("echoes") or []
    crafts = td.get("crafts") or []
    shader_lines: list[str] = [f"**【唐迟感官滤镜（当前污染度 {erosion}%）】**："]
    shader_lines.append(
        "- 思维底色：唐迟是程序员，面对诡异现象时不尖叫、不抒情，本能地把它拆成"
        "「触发条件（Trigger）/判定边界（Boundary）/状态死锁（Deadlock）」来推理。"
    )
    if erosion >= 30:
        shader_lines.append(
            f"- 生理异化（≥30%）：日常描写中自然植入 1-2 处局部生理错位（皮肤盐晶/关节藤壶/视野延迟/听觉海声），"
            "唐迟已习以为常，不向旁人解释，按压或遮住就继续干活。"
        )
    elif erosion >= 10 or echoes:
        sample = "；".join(
            str(e.get("name", e) if isinstance(e, dict) else e) for e in echoes[-2:]
        ) if echoes else "镜面或阴影偶尔慢 0.3 秒"
        shader_lines.append(
            f"- 污染残影渗透（≥10%）：环境或动作描写中自然带出 1 处旧污染残留（如：{sample}），"
            "唐迟顺手按压或无视，绝不主动解释。"
        )
    if crafts:
        recent = ", ".join(str(c.get("name", c) if isinstance(c, dict) else c) for c in crafts[-2:])
        shader_lines.append(f"- 已积累手艺：{recent}——遇到相似场景时他会下意识复用这些活命手艺。")
    parts.append("\n".join(shader_lines))
    return "\n\n".join(p for p in parts if p)


def build_chapter_checklist(entity_store: EntityStore, desk: DeskStore,
                            chapter: int, outline_total: int,
                            book_store=None, field_store=None,
                            volume_store=None, current_volume=None) -> Material | None:
    """开章任务书：写第 N 章前的 REQUIRED 自检材料，五段结构。

    ①开篇委托（上一章结尾，开篇接住变化）②本章故事（进度与跳章提醒）
    ③本书本场（书铁律+双轨时间线节拍+讣告卡——book/field 有传才注入）
    ④本章人物与设定（截至上一章的实体快照，含已退场/未出场标注）
    ⑤怎么写更顺（节奏位置 + 三线近况 + 到期伏笔按账龄排急缓）
    ⑥收在哪里（章末收束要求）。六段全空（开书第一章且无设定）返回 None。
    book_store/field_store 传 None 时退化为五段通用任务书（旧项目兼容）。
    """
    sections: list[str] = []

    # 排除本章：写完本章剧情后重跑同阶段，任务书不变才能命中幂等收据
    written = [no for no in _written_nos(desk) if no != chapter]
    prior_written = [no for no in written if no < chapter]

    # ① 开篇委托
    opening = _opening_commission(desk, chapter)
    if opening is not None:
        sections.append(opening)

    # ② 本章故事（位置）
    pos = [f"本章为第 {chapter} 章"]
    if outline_total > 0:
        pos.append(f"大纲已排至第 {outline_total} 章")
        if outline_total >= chapter and chapter > 0:
            pos.append(f"进度约 {chapter * 100 // outline_total}%")
    if written:
        pos.append(f"已写 {len(written)} 章（最近写到第 {max(written)} 章）")
        if prior_written and max(prior_written) < chapter - 1:
            pos.append(f"注意：第 {max(prior_written) + 1}-{chapter - 1} 章尚未写，本章是跳章续写")
    if len(pos) > 1:  # 只有「本章为第 N 章」一行时不单独成节
        sections.append("【本章故事】（本章位置）\n" + "；".join(pos))

    # ③ 本书本场（书铁律+双轨时间线节拍+讣告卡）——仅在传了 book_store/field_store 时注入
    book_field_parts: list[str] = []
    if book_store is not None:
        try:
            premise = book_store.premise()
            book_field_parts.append(
                "**铁律**：双轨时间线——副本原剧本时间线独立推进（自由跨度，不要求七日），"
                "唐迟个人入戏第1→7天（T-6→T-0）倒计时锚从不跳、不倒退；"
                "讣告三功能（入场/机制/出场）；神不出场不对话；唐迟T-0死劫必降临但可截断。\n"
                f"**主角**：{_clean(premise.data.get('protagonist', ''), 120)}"
            )
            # 唐迟当前侵蚀度提醒
            tangchi = book_store.tangchi_raw()
            erosion = tangchi.get("erosion_level", 0)
            crafts = tangchi.get("crafts", []) or []
            next_ms = None
            for ms in tangchi.get("madness_milestones", []) or []:
                if erosion < ms.get("threshold", 100):
                    next_ms = ms
                    break
            tang_line = f"**唐迟刻度**：当前侵蚀度 {erosion}/100"
            if crafts:
                tang_line += f"；已累积手艺 {len(crafts)} 项（{', '.join(c['name'] for c in crafts[-3:])}）"
            if next_ms:
                tang_line += f"；下一里程碑：{next_ms['threshold']}→{next_ms.get('sign','')}"
            book_field_parts.append(tang_line)
        except Exception:
            pass

    # 从章节读取 field_no/day/is_interlude
    cur_field_no = None
    cur_day = None
    cur_is_interlude = False
    if field_store is not None:
        try:
            w = desk.get(chapter)
            if w is not None:
                cur_field_no = getattr(w, "field_no", None)
                cur_day = getattr(w, "day", None)
                cur_is_interlude = bool(getattr(w, "is_interlude", False))
        except Exception:
            pass

    # 如果 chapter work 里还没写 meta（新解析的章尚未记录到 desk），fallback 到 desk 里找最近有 meta 的前章
    if cur_field_no is None and field_store is not None:
        for no in sorted((n for n in desk.all_nos() if n <= chapter), reverse=True):
            w = desk.get(no)
            if w is not None and getattr(w, "field_no", None) is not None:
                # 只在 chapter 自己的 meta 缺失时才借用（章本身是新增章时会用到）
                break

    field_context = ""
    if field_store is not None and cur_field_no is not None:
        try:
            fw = field_store.get(cur_field_no)
            if fw is not None:
                obit_lines = [f"**第{fw.no}场·{fw.title or '（未命名）'}**（{fw.world_type}）"]
                if fw.world_premise:
                    obit_lines.append(f"世界设定：{_clean(fw.world_premise, 160)}")
                obit_lines.append(fw.obituary.render(fw.no))
                if cur_is_interlude:
                    obit_lines.append(
                        "**本章是现实间章**：8:2 节奏——八成日常回血、两成暗线带刺；"
                        "必须出现本场讣告残页/其他选中者痕迹/对下一场的暗示之一；"
                        "不能写成纯日常流水。")
                elif cur_day is not None:
                    # 当日节拍：优先本场 override，否则全局基线
                    beats = None
                    if fw.obituary.day_beats_override:
                        for b in fw.obituary.day_beats_override:
                            if int(b.get("day", 0)) == cur_day:
                                beats = b
                                break
                    if beats is None and book_store is not None:
                        from app.book.pulse import get_beat
                        beats = get_beat(book_store.pulse_raw().get("day_beats", []), cur_day)
                    if beats:
                        obit_lines.append(
                            f"\n**Day{cur_day}·{beats.get('name','')}（张力 {beats.get('tension','?')}/10）**\n"
                            f"- 本日任务：{beats.get('mission','')}\n"
                            f"- 必须节拍：{beats.get('required_beat','')}\n"
                            f"- 常见陷阱：{beats.get('trap','')}"
                        )
                    # 当日 DayWork（作者填的细纲）
                    dw = fw.days.get(cur_day) if fw.days else None
                    if dw and (dw.summary or dw.required_hook):
                        seg = f"\n**Day{cur_day}作者设定**："
                        if dw.summary:
                            seg += dw.summary
                        if dw.required_hook:
                            seg += f"｜必现钩子：{dw.required_hook}"
                        obit_lines.append(seg)
                field_context = "\n".join(obit_lines)
        except Exception:
            field_context = ""

    if book_field_parts or field_context:
        sec = ["【本书本场】（本章所属场/日位置 + 必须遵守的硬约束）"]
        sec.extend(book_field_parts)
        if field_context:
            sec.append(field_context)
        sections.append("\n\n".join(sec))

    # ③-b 副本卷魂穿与死因追查档案 + 污染度感官滤镜（v2.0 核心）
    vol = None
    ch_day = None
    w = desk.get(chapter)
    if w is not None:
        ch_day = getattr(w, "day", None)
        br = getattr(w, "brief", None)
        if isinstance(br, dict):
            override = br.get("story_day_override")
            sel = br.get("selected") if isinstance(br.get("selected"), dict) else None
            if isinstance(override, int) and 0 <= override <= 7:
                ch_day = override if override > 0 else ch_day
            elif ch_day is None and isinstance(sel, dict) and sel.get("day"):
                ch_day = sel.get("day")
    if current_volume is not None:
        vol = current_volume
    elif volume_store is not None:
        try:
            vol_no = None
            if w is not None and getattr(w, "field_no", None):
                vol_no = int(w.field_no)
            if vol_no is None:
                vols = volume_store.all_list() if hasattr(volume_store, "all_list") else []
                dungeon_vols = [v for v in vols if getattr(v, "volume_kind", "dungeon") == "dungeon"]
                if dungeon_vols:
                    vol_no = max(dungeon_vols, key=lambda v: v.volume_no).volume_no
            if vol_no is not None and hasattr(volume_store, "get"):
                vol = volume_store.get(vol_no)
        except Exception:
            vol = None
    if vol is not None:
        try:
            tangchi_raw = book_store.tangchi_raw() if book_store is not None else {}
            vol_ctx = _build_volume_and_shader_context(vol, ch_day, tangchi_raw)
            if vol_ctx:
                sections.append(
                    "【本卷魂穿与死因追查档案 + 唐迟感官滤镜】"
                    "（你必须时刻记住唐迟此刻顶着谁的壳、这是他入戏第几天、离 T-0 死劫还有几天、"
                    "原剧本故事此刻正在哪个事件、他以为的死因是真是假、他的身体现在异化到什么程度——"
                    "这些信息必须自然渗入视角、推理与生理感受描写中；记住是双轨时间线并行）\n" + vol_ctx
                )
        except Exception as _e:
            import logging as _logging
            _logging.getLogger(__name__).warning("volume context 注入失败：%s", _e)

    # ④ 本章人物与设定（实体快照）
    for etype in ("character", "location", "item", "plotline"):
        ents = []
        for e in entity_store.list(type=etype):
            prior = [a for a in e.appearances if a < chapter]
            if not e.appearances or prior:
                ents.append((e, prior))
        if not ents:
            continue
        ents.sort(key=lambda t: (-(max(t[1]) if t[1] else 0), t[0].name))
        lines = []
        shown = ents[:CHECKLIST_SECTION_LIMITS[etype]]
        for e, prior in shown:
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            if e.status == "retired":
                seg += f"〔已退场〕（{_ch_span(prior)}）" if prior else "〔已退场，尚未出场〕"
            elif not prior:
                seg += "〔设定中，尚未出场〕"
            else:
                seg += f"（{_ch_span(prior)}）"
            lines.append(f"- {seg}")
        if len(ents) > CHECKLIST_SECTION_LIMITS[etype]:
            lines.append(f"（其余 {len(ents) - CHECKLIST_SECTION_LIMITS[etype]} 个略）")
        sections.append(f"【{TYPE_LABELS[etype]}快照】（截至上一章的既有设定，不得重设或矛盾）\n"
                        + "\n".join(lines))

    # ④ 怎么写更顺：节奏位置 + 三线近况 + 到期伏笔
    smooth_parts: list[str] = []
    rhythm = _rhythm_section(desk, chapter, bool(written))
    if rhythm is not None:
        smooth_parts.append(rhythm)
    strand = _strand_section(desk, chapter, written)
    if strand is not None:
        smooth_parts.append(strand)

    planted = [e for e in entity_store.list(type="foreshadow", status="planted")
               if e.appearances and min(e.appearances) < chapter]
    if planted:
        planted.sort(key=lambda e: (min(e.appearances), e.name))
        due_n = sum(1 for e in planted
                    if chapter - min(e.appearances) >= FORESHADOW_DUE_AFTER)
        hard_n = sum(1 for e in planted
                     if chapter - min(e.appearances) >= FORESHADOW_HARD_AFTER)
        head = (f"【到期伏笔】（未回收共 {len(planted)} 条，按账龄排序——账龄 ≥{FORESHADOW_DUE_AFTER} 章"
                f"的 {due_n} 条是本章硬债务，必须安排推进或兑现其一")
        if hard_n:
            head += f"；其中 ≥{FORESHADOW_HARD_AFTER} 章的 {hard_n} 条已拖到极限，最老的优先处理"
        head += "）"
        lines = []
        for e in planted[:CHECKLIST_FORESHADOW_LISTED]:
            age = chapter - min(e.appearances)
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            seg += f"（第{min(e.appearances)}章埋，已欠 {age} 章"
            if age >= FORESHADOW_DUE_AFTER:
                seg += "，本章必须处理"
            seg += "）"
            lines.append(f"- {seg}")
        if len(planted) > CHECKLIST_FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - CHECKLIST_FORESHADOW_LISTED} 条略）")
        smooth_parts.append(head + "\n" + "\n".join(lines))
    if smooth_parts:
        sections.append("【怎么写更顺】\n\n" + "\n\n".join(smooth_parts))

    if not sections:
        return None

    # ⑤ 收在哪里（章末收束要求）
    sections.append(
        "【收在哪里】本章收尾要求：章末必须落在「变化」上——局势、关系或认知至少一处"
        "不可逆转；结尾留下一章去向（钩子/悬念/未决动作），不要收成总结陈述，"
        "也不要在本章内把下一章的爆点提前泄掉。")

    head = (f"【开章任务书·第{chapter}章】写本章前的机器自检与委托。新章必须与下列既有状态一致："
            "已出场角色/地点/道具/情节线沿用既有设定，不得重设或自相矛盾；到期伏笔是本章的待办债务，"
            "应在剧情中安排推进或兑现。材料冲突时按此优先级取舍：作者指令 > 本章大纲 > "
            "本任务书既有状态 > 其他参考材料。")
    return Material(source_id=f"checklist:ch{chapter}", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n\n".join(sections),
                    title=f"开章任务书（第{chapter}章）")
