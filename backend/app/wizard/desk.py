"""章台单章微循环（M5 圆桌结论：连载期日更循环）。

plot → draft → polish 全部复用 PipelineRunner（幂等收据 + 有界续写）：
- draft 注入本章 pending 必现标记与上一章结尾滚动锚点；
- 方向卡作为 REFERENCE 材料随行（作者签收过的方向才注入）；
- 审稿复用七项一致性清单（第七项跨章联结对照上一章结尾）；
- finalize 转保鲜飞轮：实体抽取 + 伏笔兑现问询 + 标记核验 + 整本合成采纳 draft 版本
  （保持 Vault 导出兼容），飞轮失败不阻断定稿，错误进校正清单。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.context import Material, MaterialFamily
from app.context.ledger import build_chapter_checklist
from app.entities import extract_entities, reconcile_foreshadows
from app.pipeline import SPECS, PipelineRunner
from app.pipeline.engine import CONTINUATION_TAIL_CHARS
from app.pipeline.journal import RunJournal, compute_run_key
from app.pipeline.prompts import PromptStore
from app.review.markers import MarkerStore, markers_material
from app.providers.base import Provider
from app.skills import skill_materials
from app.wizard.store import STAGES, DeskStore, WizardStore

_CHAPTER_HEAD = re.compile(r"^#{0,4}\s*第\s*(\d+)\s*章", re.MULTILINE)
_VOLUME_HEAD = re.compile(r"^#{0,4}\s*第\s*[0-9一二三四五六七八九十百千]+\s*卷[^\n]*$", re.MULTILINE)
_SEPARATOR_LINE = re.compile(r"^[-=]{3,}\s*$")
# 书化新格式（《第七日的亡者》四层结构）
_FIELD_HEAD = re.compile(r"^#{0,4}\s*第\s*(\d+)\s*场[::\s]*([^\n]*)$", re.MULTILINE)
_DAY_HEAD = re.compile(r"^#{0,4}\s*第\s*([一二三四五六七1-7])\s*天[^\n]*$", re.MULTILINE)
_INTERLUDE_HEAD = re.compile(r"^#{0,4}\s*(?:现实|间章|休息|蓝星)(?::：|间章|休息|$)[^\n]*$", re.MULTILINE)

_CN_DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}


def _volume_title_of(line: str) -> str:
    return line.lstrip("#").strip()


def _strip_separators(chunk: str) -> str:
    """去掉 Markdown 分隔线（------ / ======），它们只是装饰不进章大纲。"""
    return "\n".join(
        ln for ln in chunk.splitlines() if not _SEPARATOR_LINE.match(ln)
    ).strip()


def parse_outline(outline_text: str) -> tuple[dict[int, str], list[dict]]:
    """大纲拆章 + 卷分组（兼容裸格式「第1章」与 Markdown 标题「## 第1章」）。

    返回 (chapters, volumes)：
    - chapters：{章号: 章大纲正文}，不含章号头与卷头（卷头是结构不是正文）；
    - volumes：[{title, note, chapters:[章号]}] 按出现顺序；首个卷头之前已有的章
      归入无名首组（前端对无名组不渲染卷头）。卷头到下一章之间的零散文字记为
      该卷 note，不丢也不污染章大纲。
    """
    chapters, volumes, _ = parse_outline_ext(outline_text)
    return chapters, volumes


def _parse_day_num(s: str) -> int | None:
    s = s.strip()
    if s.isdigit():
        n = int(s)
        return n if 1 <= n <= 7 else None
    return _CN_DAY_MAP.get(s)


def parse_outline_ext(outline_text: str) -> tuple[dict[int, str], list[dict], dict]:
    """增强版大纲解析：除了 chapters/volumes，还返回 chapter_meta（章→场/日/间章绑定）。

    返回 (chapters, volumes, chapter_meta)：
    - chapters/volumes 同 parse_outline
    - chapter_meta: {章号: {field_no, day, is_interlude}}，识别新格式才填充，老格式全为 None/False

    支持四种头部（从粗到细粒度）：
      - 卷：「第X卷」（同旧）
      - 场：「第N场：标题」——下面到下一场/卷之前的章属于该场
      - 日：「第X天」「第三天」——下面到下一日/间章/场之前的章属于该日
      - 间章：「现实」「间章」「休息」「蓝星」——下面到下一日/场之前的章为现实间章

    旧格式（只有卷+章）完全兼容：chapter_meta 里 field_no/day 全为 None，is_interlude=False。
    """
    text = outline_text or ""
    matches = list(_CHAPTER_HEAD.finditer(text))
    chapters: dict[int, str] = {}
    volumes: list[dict] = []
    chapter_meta: dict[int, dict] = {}
    current: dict | None = None
    cur_field: int | None = None
    cur_day: int | None = None
    cur_interlude: bool = False

    def open_volume(title: str) -> dict:
        v = {"title": title, "note": "", "chapters": []}
        volumes.append(v)
        return v

    def scan_context(seg: str) -> None:
        """在一段文本里扫描场/日/间章头，更新 cur_* 状态。卷头不影响场/日状态。"""
        nonlocal cur_field, cur_day, cur_interlude
        # 按在文本中的出现顺序依次处理
        events = []
        for fm in _FIELD_HEAD.finditer(seg):
            events.append((fm.start(), "field", fm))
        for dm in _DAY_HEAD.finditer(seg):
            events.append((dm.start(), "day", dm))
        for im in _INTERLUDE_HEAD.finditer(seg):
            events.append((im.start(), "interlude", im))
        for vm in _VOLUME_HEAD.finditer(seg):
            events.append((vm.start(), "volume", vm))
        events.sort(key=lambda x: x[0])
        for _, kind, m in events:
            if kind == "field":
                try:
                    cur_field = int(m.group(1))
                except (TypeError, ValueError):
                    pass
                cur_day = None
                cur_interlude = False
            elif kind == "day":
                cur_day = _parse_day_num(m.group(1))
                cur_interlude = False
            elif kind == "interlude":
                cur_interlude = True
                cur_day = None
            # volume 头不改变场/日状态，卷的处理在老逻辑（open_volume）里

    def strip_struct_heads(body: str) -> str:
        for pat in (_FIELD_HEAD, _DAY_HEAD, _INTERLUDE_HEAD, _VOLUME_HEAD):
            body = pat.sub("", body)
        return body

    def open_volume_for(vol_match, base_text: str, base_offset: int) -> None:
        """在 base_text 中找到 vol_match 后开新卷，并抽取卷 note（卷头后到下一个结构头之间）。
        base_offset 是 base_text 在全文中的起始位置，用于切 after_vol 时不做对象身份比较。"""
        nonlocal current
        title = _volume_title_of(vol_match.group(0))
        current = open_volume(title)
        after_vol = base_text[vol_match.end():]
        note_cuts = []
        for pat in (_CHAPTER_HEAD, _FIELD_HEAD, _DAY_HEAD, _INTERLUDE_HEAD, _VOLUME_HEAD):
            for sm in pat.finditer(after_vol):
                note_cuts.append(sm.start())
        note_text = after_vol[:min(note_cuts)] if note_cuts else after_vol
        note_text = strip_struct_heads(note_text)
        current["note"] = _strip_separators(note_text)

    # 处理首章之前的预文本：扫卷头 + 场/日/间章头
    pre_end = matches[0].start() if matches else len(text)
    pre = text[:pre_end]
    scan_context(pre)
    pre_vol = _VOLUME_HEAD.search(pre)
    if pre_vol:
        open_volume_for(pre_vol, pre, 0)

    # last_context_pos: 上次扫描结构头的位置（已扫到 pre_end，首章不再重复扫 pre）
    last_context_pos = pre_end
    for i, cm in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        ch_start = cm.start()
        ch_end = cm.end()

        # 1) 扫"上次扫描位置"到"本章头开始"之间所有结构头
        new_vol = None
        if ch_start > last_context_pos:
            seg_to_scan = text[last_context_pos:ch_start]
            new_vol = _VOLUME_HEAD.search(seg_to_scan)
            scan_context(seg_to_scan)

        # 2) 本章正文：章头后到下一个结构头或下一章头/段尾
        after_head = text[ch_end:end]
        cut_positions = [end - ch_end]
        for pat in (_VOLUME_HEAD, _FIELD_HEAD, _DAY_HEAD, _INTERLUDE_HEAD, _CHAPTER_HEAD):
            for sm in pat.finditer(after_head):
                cut_positions.append(sm.start())
        body_cut = min(cut_positions)
        body_chunk = after_head[:body_cut]

        # 2.5) 如果章前出现了新卷头，开新卷（章入新卷）
        if new_vol is not None:
            open_volume_for(new_vol, seg_to_scan, last_context_pos)

        no = int(cm.group(1))
        if current is None:
            current = open_volume("")
        current["chapters"].append(no)
        chapters[no] = _strip_separators(strip_struct_heads(body_chunk))
        chapter_meta[no] = {
            "field_no": cur_field,
            "day": cur_day if not cur_interlude else None,
            "is_interlude": cur_interlude,
        }

        # 3) 扫本章正文之后、下一章之前的尾部（可能有新卷/场/日头，给下一章用）
        after_body = after_head[body_cut:]
        tail_vol = _VOLUME_HEAD.search(after_body)
        scan_context(after_body)
        if tail_vol is not None:
            open_volume_for(tail_vol, after_body, ch_end + body_cut)

        last_context_pos = end
    return chapters, volumes, chapter_meta


def chapter_outlines(outline_text: str) -> dict[int, str]:
    """outline 文本按「第N章」段头拆章：{no: 该章大纲正文（不含章号头）}。"""
    return parse_outline(outline_text)[0]


def outline_map(project_store, pid: str) -> dict[int, str]:
    v = project_store.current(pid, "outline")
    return chapter_outlines(v.y) if v else {}


def _brief_outline_text(work) -> str:
    """从新工作台 ChapterWork.brief.selected 构造本章大纲输入 x。

    分层输出（圆桌结论：硬事实照抄 / 意图给方向，模型才有自由度）：
    brief.summary 里如果作者已按【硬事实】【起承转合】【必须出现】【禁止】分块，
    就保持原样透传；否则退回旧的单段拼接。
    """
    if not work or not isinstance(getattr(work, "brief", None), dict):
        return ""
    sel = work.brief.get("selected")
    if not isinstance(sel, dict):
        return ""
    lines: list[str] = []
    day = sel.get("day") or getattr(work, "day", None) or 1
    title = sel.get("title") or getattr(work, "title", "") or f"第{getattr(work, 'no', '?')}章"
    lines.append(f"【本章标题】{title}（副本 Day {day}）")
    beat = sel.get("beat_type")
    if beat:
        lines.append(f"【节奏定位】{beat}")

    # 起承转合：优先用 brief.structure，其次用 rhythm_blueprint 的 plot_beats
    struct = work.brief.get("structure")
    if isinstance(struct, dict) and any(struct.get(k) for k in ("qi", "cheng", "zhuan", "he")):
        lines.append("【本章起承转合（节奏骨架，必须体现）】")
        for k, label in (("qi", "起"), ("cheng", "承"), ("zhuan", "转"), ("he", "合")):
            v = str(struct.get(k) or "").strip()
            if v:
                lines.append(f"{label}｜{v}")
        lines.append("")

    summary = sel.get("summary")
    if summary:
        lines.append(f"【本章核心剧情方向】\n{summary}")
    extra = sel.get("extra_edits")
    if extra:
        lines.append(f"【作者追加修正要求】\n{extra}")
    hooks = sel.get("hooks")
    if isinstance(hooks, list) and hooks:
        lines.append("【章尾必留钩子】")
        for h in hooks:
            lines.append(f"- {h}")
    elif isinstance(hooks, str) and hooks.strip():
        lines.append(f"【章尾必留钩子】\n{hooks}")
    fsa = sel.get("foreshadow_advance") or sel.get("foreshadow") or []
    if isinstance(fsa, list) and fsa:
        lines.append(f"【本章需推进/埋设的伏笔】{', '.join(str(x) for x in fsa)}")
    eos = sel.get("entities_on_stage") or []
    if isinstance(eos, list) and eos:
        lines.append(f"【本章登场实体】{', '.join(str(x) for x in eos)}")
    return "\n".join(lines).strip()


def chapter_title_of(body: str) -> str:
    lines = body.strip().splitlines()
    return lines[0].strip()[:20] if lines and lines[0].strip() else ""


def work_text(work, stage: str) -> str:
    d = getattr(work, stage, None) if work else None
    return (d.get("text", "") if d else "").strip()


def previous_tail(desk: DeskStore, chapter: int) -> str:
    """上一章结尾 500 字作为滚动锚点（润色 > 正文）；审稿第七项「跨章联结」也用它对照。"""
    for no in sorted((n for n in desk.all_nos() if n < chapter), reverse=True):
        for stage in ("polish", "draft"):
            t = work_text(desk.get(no), stage)
            if t:
                return t[-CONTINUATION_TAIL_CHARS:]
    return ""


def _projects_root():
    """backend/data/projects 根目录（与 entities.json / desk.json 同级）。"""
    try:
        from .. import _config
        return _config.REPO / "backend" / "data" / "projects"
    except Exception:
        from pathlib import Path
        return Path(__file__).resolve().parents[2] / "data" / "projects"


def _load_style_dna(pid: str) -> Material | None:
    """加载本书文风卡，抽取「优先级裁决 + 十维摘要 + 不可写清单」注入 prompt。

    只注入决策相关的部分，不注入全文（style_dna.md 可能很长，全注入会挤占注意力预算）。
    注入位置在 materials[0]——REQUIRED 家族，优先级高于其他材料。
    """
    p = _projects_root() / pid / "style_dna.md"
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None

    import re as _re

    def _table_row(line: str) -> str:
        """把 `| 指标 | 目标值 | 说明 |` 压成 `指标：目标值`；表头/分隔行返回空串。"""
        if not line.startswith("|"):
            return "\x00"  # 不是表格行
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            return ""
        if cells[0] in ("指标", "位置", "类别", "字段"):
            return ""
        vals = [c for c in cells[1:] if c]
        if not vals:
            return ""
        return f"{cells[0]}：{' / '.join(vals)}"

    dims: list[str] = []
    taboos: list[str] = []
    in_dims = False
    cur_is_taboos = False
    for raw_line in raw.splitlines():
        s = raw_line.strip()
        if s.startswith("# "):
            in_dims = s.startswith("# 十维规范")
            cur_is_taboos = False
            continue
        if not in_dims:
            continue
        if s.startswith("## "):
            title = s[3:].strip()
            cur_is_taboos = "行文禁忌" in title
            # 禁忌整块单独输出，不再重复一个空标题
            if not cur_is_taboos:
                dims.append(f"\n【{title}】")
            continue
        if s.startswith("|"):
            row = _table_row(s)
            if row:
                dims.append(f"  {row[:130]}")
            continue
        if cur_is_taboos and _re.match(r"^\d+[.、]\s*\S", s):
            taboos.append(s)
            continue
        if not s or s.startswith("---") or s.strip("*") == "":
            continue
        s = _re.sub(r"\\_", "_", s)  # markdown 转义符不该进 prompt
        if s.startswith("- "):
            s = s[2:]
        dims.append(f"  {s[:130]}")

    body = (
        "【本书文风卡（最高优先级之一，冲突时听它）】\n"
        "以下不是通用建议，是这本书真实的写法。与通用去 AI 味规则冲突时，以本卡为准"
        "（lieflat：蒸馏产物记录的是目标作者的真实写法，不属生成痕迹）。\n"
        "本卡只管「怎么写」，不管「写什么」——事件/事实/日号/视角纪律仍按章前方向和硬设定。\n"
        + "\n".join(dims)
        + "\n\n【本书行文禁忌（违反即打回）】\n"
        + "\n".join(f"  {x}" for x in taboos[:14])
        + "\n\n【风格冲突裁决顺序】本次明确要求 > 本书文风卡 > 本书硬设定 > 章前方向 > 通用规则"
    )
    return Material(source_id=f"style_dna:{pid}", family=MaterialFamily.REQUIRED,
                    content=body, title="本书文风卡（风格基准）")


def _direction_material(wizard: WizardStore) -> Material | None:
    d = wizard.get()
    if not d.direction.strip():
        return None
    source = {"picked": "方向卡（作者选用）", "edited": "方向卡（作者改写）",
              "manual": "方向（作者自写）"}.get(d.direction_source, "方向卡")
    return Material(source_id="wizard:direction", family=MaterialFamily.REFERENCE,
                    content=d.direction, title=f"故事方向·{source}")


def _inject_mainline_context(materials: list, vol, chapter: int, work, entity_store, desk, stage: str):
    """蓝星主线章注入：主角状态 + 上一章尾 + 未回收伏笔前10。不注入任何副本相关材料。"""
    try:
        lines: list[str] = []
        lines.append("【本卷类型】蓝星开篇卷（day=0，尚未进入任何副本）")
        try:
            title = getattr(work, "title", "") or ""
        except Exception:
            title = ""
        lines.append(f"【本章定位】第{chapter}章（全书第{chapter}章）——{title}")
        lines.append("【写作红线】")
        lines.append("- 场景限定在蓝星现代都市（写字楼/出租屋/地铁/便利店/医院/街道等），不许出现任何副本/克苏鲁神明/魂穿宿主/NPC")
        lines.append("- 主角唐迟：理性、冷静、善归纳、对规则边界敏感的程序员；但禁止出现任何编程职业黑话（debug/Bug/栈/日志/变量/指针/越权/溢出/短路/重定向/死锁/卡bug 等）")
        lines.append("- 第1章不许穿越；第2章末才穿越坠落")

        # 主角状态（唐迟）
        if entity_store is not None:
            try:
                proto = getattr(entity_store, "protagonist", None)
                if proto is not None:
                    state_bits = []
                    loc = getattr(proto, "current_location", "") or ""
                    if loc:
                        state_bits.append(f"当前位置：{loc}")
                    try:
                        contam = getattr(proto, "contamination_level", 0) or 0
                    except Exception:
                        contam = 0
                    if contam:
                        state_bits.append(f"累积污染度：{contam}")
                    bb = getattr(proto, "blue_star_bonds", None) or []
                    if bb:
                        try:
                            bond_lines = [
                                f"- {b.get('name','?')}（{b.get('relation','')}，深度{b.get('depth','')}）{b.get('note','')}"
                                for b in bb[:5] if isinstance(b, dict)
                            ]
                            if bond_lines:
                                state_bits.append("蓝星羁绊：\n" + "\n".join(bond_lines))
                        except Exception:
                            pass
                    if state_bits:
                        lines.append("【唐迟当前状态】\n" + "\n".join(state_bits))
            except Exception:
                pass

        # 上一章尾
        try:
            if chapter > 1 and desk is not None:
                tail = previous_tail(desk, chapter - 1)
                if tail:
                    lines.append(f"【上一章末尾（第{chapter-1}章，供衔接）】\n{tail}")
        except Exception:
            pass

        # 未回收伏笔前 10 条
        if entity_store is not None:
            try:
                ents = getattr(entity_store, "entities", None)
                if ents is None and hasattr(entity_store, "list"):
                    try:
                        ents = entity_store.list(type="foreshadow", status="planted")
                    except Exception:
                        ents = []
                unpaids = []
                if ents:
                    for e in ents:
                        try:
                            if getattr(e, "type", None) == "foreshadow" and getattr(e, "status", None) == "planted":
                                unpaids.append(e)
                        except Exception:
                            pass
                if unpaids:
                    bits = []
                    for e in unpaids[:10]:
                        try:
                            sa = getattr(e, "state_attrs", None) or {}
                            plant = sa.get("plant_chapter", "?") if isinstance(sa, dict) else "?"
                            ename = getattr(e, "name", "?")
                            esum = getattr(e, "summary", "") or ""
                            bits.append(f"- {ename}（埋于第{plant}章）：{esum}")
                        except Exception:
                            pass
                    if bits:
                        lines.append("【未回收伏笔（前10）】\n" + "\n".join(bits))
            except Exception:
                pass

        content = "\n\n".join(l for l in lines if l and l.strip())
        if content:
            try:
                vno = getattr(vol, "volume_no", "?")
            except Exception:
                vno = "?"
            try:
                vtitle = getattr(vol, "title", None) or ""
            except Exception:
                vtitle = ""
            title_str = f"蓝星主线·第{vno}卷" + (f"（{vtitle}）" if vtitle else "")
            materials.append(Material(
                source_id=f"volume:v{vno}:mainline",
                family=MaterialFamily.FINALIZED,
                content=content,
                title=title_str,
            ))
    except Exception:
        pass


def _inject_interlude_context(materials: list, vol, entity_store, stage: str):
    """蓝星间章注入：杀青残页 + 侵蚀度 + 蓝星钩子。"""
    try:
        lines: list[str] = []
        lines.append("【本卷类型】蓝星间章（两场副本之间的现实缓冲）")
        # 上一场杀青归账
        try:
            settle = getattr(vol, "settle", None) or getattr(vol, "post_field_settle", None) or {}
        except Exception:
            settle = {}
        if isinstance(settle, dict) and settle:
            summary = settle.get("summary") or settle.get("text") or ""
            if not summary:
                try:
                    kills = settle.get("kill_record_line") or ""
                    contam_delta = settle.get("contamination_delta")
                    if kills or contam_delta is not None:
                        summary = f"污染+{contam_delta or 0}；杀青账：{kills}"
                except Exception:
                    pass
            if summary:
                lines.append(f"【上一场杀青归账摘要】\n{str(summary)[:800]}")
        # 唐迟状态
        if entity_store is not None:
            try:
                proto = getattr(entity_store, "protagonist", None)
                if proto is not None:
                    bits = []
                    try:
                        contam = getattr(proto, "contamination_level", 0) or 0
                    except Exception:
                        contam = 0
                    bits.append(f"累积污染度：{contam}")
                    loc = getattr(proto, "current_location", "") or ""
                    if loc:
                        bits.append(f"当前位置：{loc}")
                    bb = getattr(proto, "blue_star_bonds", None) or []
                    if bb:
                        try:
                            names = [b.get("name", "?") for b in bb[:3] if isinstance(b, dict)]
                            if names:
                                bits.append("蓝星羁绊：" + "、".join(names))
                        except Exception:
                            pass
                    bi = getattr(proto, "brought_items", None) or []
                    if bi:
                        try:
                            names = [i.get("name", "?") for i in bi[:5] if isinstance(i, dict)]
                            if names:
                                bits.append("带回复本物品：" + "、".join(names))
                        except Exception:
                            pass
                    lines.append("【唐迟状态】\n" + "\n".join(f"- {b}" for b in bits))
            except Exception:
                pass
        content = "\n\n".join(l for l in lines if l and l.strip())
        if content:
            try:
                vno = getattr(vol, "volume_no", "?")
            except Exception:
                vno = "?"
            materials.append(Material(
                source_id=f"volume:v{vno}:interlude",
                family=MaterialFamily.FINALIZED,
                content=content,
                title=f"蓝星间章·第{vno}卷",
            ))
    except Exception:
        pass


def run_chapter_stage(
    provider: Provider,
    prompt_store: PromptStore,
    project_store,
    pid: str,
    marker_store: MarkerStore | None,
    wizard: WizardStore,
    desk: DeskStore,
    chapter: int,
    stage: str,
    directive: str | None = None,
    force: bool = False,
    journal_dir: Path | str | None = None,
    extra_materials: list[Material] | None = None,
    entity_store=None,
    on_event=None,
    modules: list[str] | None = None,
    full_modules: bool = False,
    book_store=None,
    field_store=None,
    volume_store=None,
) -> dict:
    """单章单层生成：plot/draft/polish；同输入幂等回收据，force 绕过。
    extra_materials：作者勾选的跨项目参考层（REFERENCE 族，互为范文）。
    entity_store：给定时为 plot/draft 注入开章任务书（五段结构）。
    modules/full_modules：写作模块注入——未指定时按层默认集注入摘要版
    （digest，省上下文）；显式点名或 full_modules=True 时注入完整规则。
    book_store/field_store/volume_store：书化项目时传入（BookStore/FieldStore/VolumeStore），
    注入书铁律、讣告卡、当日节拍、副本魂穿/信息差/核心诡计等本场设定。"""
    if stage not in STAGES:
        raise ValueError(f"未知阶段：{stage}")
    spec = SPECS[stage]
    work = desk.get(chapter)
    o_map = outline_map(project_store, pid)

    materials: list[Material] = []
    # 写作模块：默认集摘要随行（显式点名 → 完整规则）
    materials.extend(skill_materials(stage, requested=modules,
                                     digest=(modules is None and not full_modules)))
    # 书化项目：根据章节类型自动追加书专属模块（摘要版，不挤爆上下文）
    if book_store is not None:
        auto_modules: list[str] = ["obsession_cost"]  # 所有章节都要代价意识
        ch_kind = getattr(work, "chapter_kind", None) or (
            "interlude" if getattr(work, "is_interlude", False) else "dungeon"
        )
        if ch_kind == "interlude":
            auto_modules.append("interlude_relief")
        elif ch_kind == "dungeon":
            auto_modules.append("coc_horror")
            if stage == "plot":
                auto_modules.append("infinite_loop_unit")
        elif ch_kind == "mainline":
            # 蓝星章：无 coc_horror，但 plot 阶段保留无限循环单元（唐迟理性/规则敏感）
            if stage == "plot":
                auto_modules.append("infinite_loop_unit")
        # 去重（用户若显式点名了同模块，摘要版会让位给完整版——这里通过
        # 检查 requested 列表避免重复注入）
        already = set(modules or [])
        use_digest = modules is None and not full_modules
        for am in auto_modules:
            if am in already:
                continue
            from app.skills.modules import module_rules, module_digest
            content = module_digest(am) if use_digest else module_rules(am)
            materials.append(Material(
                source_id=f"skill:{am}",
                family=MaterialFamily.REQUIRED,
                content=content,
                title=f"写作模块[{am}]" + ("（摘要）" if use_digest else ""),
            ))
    # Volume 本场设定注入：魂穿宿主/信息差/核心诡计/本场死亡-线索-高潮节点
    # 分支：dungeon（副本章）保留原竞技场拼装；mainline/interlude（蓝星章/间章）注入主线/间章材料
    ch_kind = getattr(work, "chapter_kind", None) or (
        "interlude" if getattr(work, "is_interlude", False) else "dungeon"
    )
    if volume_store is not None and getattr(work, "field_no", None) is not None:
        try:
            vol = volume_store.get(int(work.field_no))
            if vol is None:
                pass  # 卷不存在，跳过
            elif ch_kind == "dungeon":
                arena_lines: list[str] = []
                # 副本改编
                from ..book.volume import adapted_premise_of, adapted_cast_of
                premise = adapted_premise_of(vol.adapted_module)
                if premise:
                    arena_lines.append(f"【副本核文本】\n{premise}")
                cast_cards = adapted_cast_of(vol.adapted_module)
                if cast_cards:
                    cast_lines = []
                    for c in cast_cards:
                        cast_lines.append(f"- {c.get('name','?')}（{c.get('role','')}；{c.get('occupation','')}）｜秘密：{c.get('secret','')}")
                    arena_lines.append("【剧本 NPC/KPC 卡司】\n" + "\n".join(cast_lines))
                # 魂穿宿主
                hc = vol.host_character or {}
                if isinstance(hc, dict) and hc:
                    hc_name = hc.get("name") or hc.get("body_name") or "宿主"
                    hc_role = hc.get("role") or hc.get("identity") or ""
                    hc_know = hc.get("known_info") or hc.get("memory") or hc.get("backstory") or ""
                    arena_lines.append(f"【魂穿宿主·{hc_name}】\n身份：{hc_role}\n已知信息：{hc_know}")
                # 入本点
                ep = vol.entry_point or {}
                if isinstance(ep, dict) and ep:
                    arena_lines.append(f"【入本点】{ep.get('title') or ''}\n{ep.get('summary') or ep.get('description') or ''}")
                # 信息差
                if vol.info_gap:
                    arena_lines.append(f"【信息差（唐迟 vs 读者/宿主）】\n{vol.info_gap}")
                # 立场
                st = vol.stance or {}
                if isinstance(st, dict) and st:
                    arena_lines.append(f"【本场立场/抉择骨架】\n{st.get('summary') or st.get('choice') or ''}")
                # 核心诡计
                tso = vol.tangchi_story_outline or {}
                if isinstance(tso, dict) and tso:
                    arena_lines.append(f"【唐迟诡计·主线规划】\n{tso.get('summary') or ''}")
                # 节奏蓝图 - 本章对应 day 的节点
                bp = vol.rhythm_blueprint or {}
                if isinstance(bp, dict):
                    ch_no_local = None
                    plan = bp.get("chapter_plan") or []
                    try:
                        # 找当前全局章号对应的 day
                        ch_global = chapter
                        field_no = int(work.field_no)
                        offset = 0
                        for pv in volume_store.all_list():
                            if pv.volume_no == field_no:
                                break
                            pbp = pv.rhythm_blueprint or {}
                            prng = pbp.get("suggested_chapter_range") or [0, 15]
                            offset += int(prng[1]) if len(prng) >= 2 else 15
                        ch_no_local = ch_global - offset
                    except Exception:
                        ch_no_local = None
                    if ch_no_local is not None:
                        day_spec = None
                        for p in plan:
                            if isinstance(p, dict) and int(p.get("chapter") or -1) == ch_no_local:
                                day_spec = p; break
                        if day_spec:
                            arena_lines.append(f"【第{day_spec.get('day')}天·节奏卡】强度={day_spec.get('intensity') or ''}；事件：{day_spec.get('event') or ''}；死亡/高潮/线索：{day_spec.get('beat') or ''}")
                    # 6 卡详设（死亡/高潮按 fate_layer 分组，突出原剧本死劫/改命/反噬三层因果）
                    def _fmt_node(n):
                        t = n.get("title") or n.get("name") or n.get("who") or ""
                        d = n.get("description") or n.get("summary") or n.get("detail") or n.get("how") or n.get("beat") or ""
                        tease = n.get("tease") or ""
                        cost = n.get("cost") or ""
                        bits = []
                        if t:
                            bits.append(t)
                        if d:
                            bits.append(d)
                        if tease:
                            bits.append("（伏笔：" + tease + "）")
                        if cost:
                            bits.append("（代价：" + cost + "）")
                        layer = (n.get("fate_layer") or "").strip().lower()
                        prefix = {"doomed": "[原剧本死劫·DOOMED]", "diverted": "[唐迟改命·DIVERTED]",
                                  "escalated": "[改命反噬·ESCALATED]"}.get(layer, "")
                        line = " ".join(bits).strip()
                        return ("- " + prefix + " " + line) if prefix else ("- " + line)

                    def _join_node_list(lst, label):
                        if not lst: return
                        bits = []
                        for n in lst:
                            if not isinstance(n, dict): continue
                            n_day = n.get("day") or n.get("chapter")
                            if ch_no_local is not None and n_day is not None:
                                try:
                                    if int(n_day) != ch_no_local:
                                        continue
                                except Exception:
                                    pass
                            td = n.get("title") or n.get("name") or n.get("where") or ""
                            wh = n.get("what") or n.get("description") or n.get("summary") or n.get("detail") or ""
                            if td or wh:
                                bits.append(("- " + td + "：" + wh) if (td and wh) else ("- " + (td or wh)))
                        if bits:
                            arena_lines.append("【本场" + label + "】\n" + "\n".join(bits))

                    def _join_by_layer(lst, label_main):
                        if not lst:
                            return
                        todays = []
                        future_near = []
                        for n in lst:
                            if not isinstance(n, dict):
                                continue
                            n_day = n.get("day") or n.get("chapter")
                            bucket = None
                            if ch_no_local is not None and n_day is not None:
                                try:
                                    nd = int(n_day)
                                    if nd == ch_no_local:
                                        bucket = todays
                                    elif 0 < nd - ch_no_local <= 2:
                                        bucket = future_near
                                except Exception:
                                    bucket = todays
                            else:
                                bucket = todays
                            if bucket is not None:
                                bucket.append(n)
                        if not todays and not future_near:
                            return
                        rendered = []
                        if label_main in ("死亡节点", "高潮节点"):
                            groups = [
                                ("原剧本死劫（唐迟已从跑团/讣告中预见，本章信息差/先知感的核心来源——他带着预警进场，但大概率救不下来）",
                                 [n for n in todays if (n.get("fate_layer") or "").lower() == "doomed"]),
                                ("唐迟试图改命（本章他尝试介入救下/转移，要付代价或产生连锁反应）",
                                 [n for n in todays if (n.get("fate_layer") or "").lower() == "diverted"]),
                                ("改命反噬（介入后反而更糟，触发更严重污染/更多死亡/更难收场）",
                                 [n for n in todays if (n.get("fate_layer") or "").lower() == "escalated"]),
                                ("（未标 fate_layer 的节点，按默认处理）",
                                 [n for n in todays if (n.get("fate_layer") or "").lower() not in ("doomed", "diverted", "escalated")]),
                            ]
                            any_g = False
                            for gtitle, gitems in groups:
                                if not gitems:
                                    continue
                                any_g = True
                                rendered.append("  ▸ " + gtitle)
                                for n in gitems:
                                    rendered.append("  " + _fmt_node(n))
                            if not any_g:
                                for n in todays:
                                    rendered.append(_fmt_node(n))
                        else:
                            for n in todays:
                                rendered.append(_fmt_node(n))
                        sec_title = ("【本场" + label_main + "·第" + str(ch_no_local) + "天】") if ch_no_local is not None else ("【本场" + label_main + "】")
                        block = sec_title + "\n" + "\n".join(rendered)
                        if future_near:
                            near_lines = []
                            for n in future_near[:3]:
                                nd = n.get("day") or "?"
                                near_lines.append("  - 第" + str(nd) + "天：" + str(n.get("title") or n.get("who") or ""))
                            block = block + "\n  ▸ 未来1-2天预告（不要在本章提前写死，但可以埋钩子/伏笔）\n" + "\n".join(near_lines)
                        arena_lines.append(block)

                    _join_by_layer(vol.death_nodes or [], "死亡节点")
                    _join_by_layer(vol.climax_nodes or [], "高潮节点")
                    _join_node_list(vol.clue_nodes or [], "线索节点")
                    _join_node_list(vol.pasted_nodes or [], "贴肤节点")
                # scaffold 6 卡
                sc = vol.scaffold_cards or {}
                if isinstance(sc, dict) and sc:
                    card_summaries = []
                    for cname, cval in sc.items():
                        if isinstance(cval, dict):
                            t = cval.get("title") or ""
                            s = cval.get("summary") or cval.get("content") or ""
                            if t or s:
                                card_summaries.append(f"- {cname}：{t} {s}".strip())
                        elif isinstance(cval, str) and cval.strip():
                            card_summaries.append(f"- {cname}：{cval}")
                    if card_summaries:
                        arena_lines.append("【详设 6 卡（本场）】\n" + "\n".join(card_summaries))
                # 跑团模拟结果
                sr = vol.simulated_run or {}
                if isinstance(sr, dict):
                    runs = sr.get("runs") or sr.get("simulations") or []
                    if runs:
                        picked = runs[0] if isinstance(runs[0], dict) else {}
                        arena_lines.append(f"【跑团模拟（参考）】\n{picked.get('summary') or picked.get('outcome') or ''}")
                if arena_lines:
                    content = "\n\n".join(l for l in arena_lines if l and l.strip())
                    materials.append(Material(
                        source_id=f"volume:v{vol.volume_no}:arena",
                        family=MaterialFamily.FINALIZED,
                        content=content,
                        title=f"本书本场·第{vol.volume_no}卷{('（' + (vol.title or '') + '）') if vol.title else ''}",
                    ))
            elif ch_kind == "mainline":
                _inject_mainline_context(materials, vol, chapter, work, entity_store, desk, stage)
            elif ch_kind == "interlude":
                _inject_interlude_context(materials, vol, entity_store, stage)
        except Exception:
            # 本场注入失败不应阻塞主流程
            pass
    if stage == "plot":
        # 优先用章前方向卡（work.brief['selected']），这是新工作台（T1-T10）的权威输入；
        # 回退到旧 outline_map 兼容老 Workstation/纯大纲模式。
        body = _brief_outline_text(work)
        if not body:
            body = o_map.get(chapter, "")
        if not body:
            raise ValueError(f"大纲/方向卡中未找到第 {chapter} 章，请先完成章前方向卡选择")
        x = body
        source_label = "章前方向卡（本章锁定）" if _brief_outline_text(work) else f"第{chapter}章大纲"
        materials.append(Material(source_id=f"outline:ch{chapter}", family=MaterialFamily.FINALIZED,
                                  content=body, title=source_label))
        intro = project_store.current(pid, "intro")
        if intro is not None:
            materials.append(Material(source_id="intro", family=MaterialFamily.FINALIZED,
                                      content=intro.y, title="故事简介"))
    elif stage == "draft":
        x = work_text(work, "plot")
        if not x:
            raise ValueError("本章还没有剧情，请先生成剧情")
        body = o_map.get(chapter, "")
        # draft 阶段也附上方向卡供参考
        if not body:
            body = _brief_outline_text(work)
        if body:
            materials.append(Material(source_id=f"outline:ch{chapter}", family=MaterialFamily.FINALIZED,
                                      content=body, title="本章方向/大纲"))
    else:  # polish
        x = work_text(work, "draft")
        if not x:
            raise ValueError("本章还没有正文，请先生成正文")

    direction_mat = _direction_material(wizard)
    if direction_mat is not None:
        materials.append(direction_mat)
    if extra_materials:
        materials.extend(extra_materials)
    # 书化项目：REQUIRED 注入书级硬卡（铁律/七日基线/唐迟刻度/终局锚）
    if book_store is not None:
        try:
            materials.append(Material(
                source_id="book:hardcards", family=MaterialFamily.REQUIRED,
                content=book_store.render_all(),
                title="书级硬卡（铁律/七日基线/唐迟刻度/终局锚）"))
        except Exception:
            pass
    checklist_injected = False
    if entity_store is not None and stage in ("plot", "draft"):
        # 解析当前卷：优先用传入的 volume_store（单个 Volume 对象）/ 或从 volume_store 仓库按 field_no 找
        cur_vol = None
        if volume_store is not None:
            if hasattr(volume_store, "volume_no"):
                cur_vol = volume_store
            elif hasattr(volume_store, "get") and getattr(work, "field_no", None) is not None:
                try:
                    cur_vol = volume_store.get(int(work.field_no))
                except Exception:
                    cur_vol = None
        checklist = build_chapter_checklist(
            entity_store, desk, chapter, max(o_map) if o_map else 0,
            book_store=book_store, field_store=field_store,
            volume_store=volume_store if hasattr(volume_store, "all_list") else None,
            current_volume=cur_vol,
        )
        if checklist is not None:
            materials.append(checklist)
            checklist_injected = True
    anchor = ""
    if stage == "draft":
        if marker_store is not None:
            pending = marker_store.pending_for(chapter)
            if pending:
                materials.insert(0, markers_material(pending, chapter))
        anchor = previous_tail(desk, chapter)

    # 本书文风卡（style_dna.md）：风格基准，优先级高于通用去 AI 味规则。
    # 只注入「维度摘要 + 优先级裁决 + 不可写清单」，不注入全文（省 token）。
    if stage in ("draft", "polish"):
        _style = _load_style_dna(pid)
        if _style:
            materials.insert(0, _style)

    # 角色声音档案（习惯动作/口癖/口音）：角色辨识度的唯一来源，跟实体库走，
    # 不写死在文风卡里。REQUIRED——优先级高于通用去 AI 味规则。
    if entity_store is not None and stage in ("plot", "draft", "polish"):
        try:
            from app.context.ledger import _voiceprint_material
            _vp = _voiceprint_material(entity_store, chapter)
            if _vp is not None:
                materials.insert(0, _vp)
        except Exception:
            pass

    # 描写轮换账本（圆桌方案）：生成前把「你已经写过什么」当记忆注入给模型，
    # 措辞是叙事性的（记忆）而非管理性的（禁令）——这是它能起作用的关键。
    if stage in ("draft", "polish"):
        try:
            from app.generation.lexicon import load_lexicon
            lex = load_lexicon(pid, _projects_root())
            mem = lex.memory_block(chapter)
            if mem:
                materials.insert(0, Material(
                    source_id=f"lexicon:ch{chapter}", family=MaterialFamily.REQUIRED,
                    content=mem, title="描写记忆（你已用过的表达）"))
        except Exception:
            pass

    run_key = compute_run_key(stage, [x],
                              [(m.source_id, m.family.value, m.content) for m in materials],
                              directive)
    journal = None
    if journal_dir is not None and not force:
        journal = RunJournal(Path(journal_dir) / f"{run_key}.json", stage, run_key)
    runner = PipelineRunner(provider, prompt_store=prompt_store)
    result = runner.run_layer(spec, [(x, "")], materials=materials,
                              initial_anchor=anchor, user_directive=directive,
                              journal=journal, on_event=on_event,
                              project_id=pid,
                              chapter_no=chapter if stage in ("draft", "polish") else None)
    desk.record(chapter, stage, result.y, run_key, title=chapter_title_of(o_map.get(chapter, "")))
    return {
        "chapter": chapter, "stage": stage, "y": result.y, "run_id": run_key,
        "resumed_indices": result.resumed_indices,
        "checklist_injected": checklist_injected,
        "calls": [{"completion_status": c.completion_status, "attempts": c.attempts}
                  for c in result.calls],
    }


def finalize_chapter(
    provider: Provider,
    prompt_store: PromptStore,
    project_store,
    pid: str,
    entity_store,
    marker_store: MarkerStore | None,
    desk: DeskStore,
    chapter: int,
    auto_entities: bool = True,
    payoff: str | None = None,
) -> dict:
    """定稿 + 保鲜飞轮：实体核对、伏笔兑现问询、标记核验、整本合成采纳。

    注意：实体库的主入口**不在这里**。副本卷创作链路里（节奏蓝图 finalize /
    方向卡 select）就已经把本章实体落库了，所以每章写作时能注入到角色卡。
    这里的抽取降级为「核对/补漏」——只补前置落库漏掉的，正文里已声明的
    不会重复创建，也不会覆盖作者改过的档案。

    payoff：定稿时顺手点选的兑现规模四选一（none/small/medium/climax），
    供开章核对单的节奏位置与审稿的节奏饥饿检查消费。"""
    work = desk.get(chapter)
    text = work_text(work, "polish") or work_text(work, "draft")
    if not text:
        raise ValueError("本章还没有可定稿的正文，请先生成正文")
    if payoff is not None:
        desk.record_payoff(chapter, payoff)

    entities_report: dict | None = None
    foreshadow_report: dict | None = None
    if auto_entities and entity_store is not None:
        # 先记下落库前已有的实体，抽取结果只当作「补漏」来报告
        before = {e.name for e in entity_store.list(type="character")}
        before |= {e.name for e in entity_store.list(type="location")}
        before |= {e.name for e in entity_store.list(type="item")}
        before |= {e.name for e in entity_store.list(type="foreshadow")}
        try:
            r = extract_entities(provider, entity_store, prompt_store, text, chapter=chapter)
            entities_report = {
                "created": r.created, "updated": r.updated,
                "dropped": len(r.dropped), "attempts": r.attempts,
                "note": "定稿抽取仅作核对补漏；主要来源是副本卷前置落库",
                "new_names": sorted({str(e.get("name", "")) for e in r.created} - before),
            }
        except Exception as e:   # 飞轮失败不阻断定稿
            entities_report = {"error": str(e)[:300]}
        try:
            fr = reconcile_foreshadows(provider, entity_store, prompt_store, text, chapter=chapter)
            foreshadow_report = fr.as_dict() if fr is not None else None
        except Exception as e:   # 问询失败同样不阻断
            foreshadow_report = {"error": str(e)[:300]}
    marker_hits = marker_store.verify(text, chapter) if marker_store is not None else []

    parts: list[str] = []
    for no in desk.all_nos():
        w = desk.get(no)
        body = work_text(w, "polish") or work_text(w, "draft")
        if not body:
            continue
        title = f" {w.title}" if w and w.title else ""
        parts.append(f"第{no}章{title}\n{body}")
    v = project_store.adopt(pid, "draft", x_pairs=[], y="\n\n".join(parts),
                            note=f"第{chapter}章定稿（保鲜飞轮已转）")
    desk.mark_finalized(chapter)
    return {
        "chapter": chapter, "chars": len(text),
        "entities": entities_report,
        "foreshadow": foreshadow_report,
        "marker_hits": marker_hits,
        "draft_version": v.seq,
        "total_chapters": len(parts),
    }
