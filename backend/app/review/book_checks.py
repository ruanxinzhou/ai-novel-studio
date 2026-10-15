"""《第七日的亡者》书专属审稿检查（纯数据推导，不走 LLM）。

六条书规硬检：
1. 七日锚：章节标注 Day=N 时，上下文/字里不能出现 Day=N+1 及以上的事件
2. 8:2 间章脊柱：is_interlude 的章节必须出现暗线（残页/其他选中者/下一场暗示）关键词
3. 执念代价延迟：同场使用执念金手指后 1-2 章内必须有副作用迹象
4. 唐迟 POV 泄漏：非间章正文不能出现唐迟视角以外的全知叙述（软检：点名检查"他不知道/他没发现"类叙述者越界词）
5. 终局种子审计：每场至少埋一颗对应层级的终局种子（按 endgame 锚点 pending 数）
6. 力量升级克制：侵蚀度 ≥40 后不能再出现唐迟单方面轻松碾压的描写（强金手指必有代价描写）

所有检查返回 string | None：None=通过，string=报警内容。与 rhythm_hunger/strand_warnings 同构。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.book import BookStore
    from app.fields import FieldStore
    from app.wizard import DeskStore


# ---- 4) POV 越界关键词（非间章出现 → 报警） ----
_POV_LEAK_PATTERNS = [
    (re.compile(r"他(?:却|并)?不知道"), "POV 越界：「他不知道」类叙述者全知——唐迟限知视角不应由叙述者替读者揭秘"),
    (re.compile(r"他(?:完全|丝毫)?没有 (?:发现|察觉|意识到|注意到)"), "POV 越界：「他没发现」类全知提示——限知视角下读者应和主角一起被蒙在鼓里"),
    (re.compile(r" (?:然而|可是|但是)他(?:并|却)?不知道"), "POV 越界：转折式全知揭秘，违反限知"),
    (re.compile(r"^他(?:至死|到最后)都没"), "POV 越界：未来剧透式全知，限知视角不应出现"),
]

# ---- 6) 爽文碾压检测词（侵蚀度 ≥40 时出现且无代价词 → 报警） ----
_OVERPOWER_PATTERNS = [
    re.compile(r"轻松(?:解决|碾压|击退|秒杀|摆平)"),
    re.compile(r"不费吹灰之力"),
    re.compile(r"一招(?:秒杀|制敌|击溃)"),
    re.compile(r"毫不费力"),
    re.compile(r"如入无人之境"),
]
_COST_SIGNAL_WORDS = ("代价", "反噬", "头痛", "晕眩", "鼻血", "恶心", "恍惚",
                     "一阵(?:眩晕|恶寒|恶心|刺痛)", "冷汗", "颤抖", "虚脱",
                     "记忆", "遗忘", "模糊", "失控")
_COST_PATTERN = re.compile("|".join(_COST_SIGNAL_WORDS))

# ---- 2) 间章暗线关键词（必须命中其一） ----
_INTERLUDE_HOOK_PATTERNS = [
    re.compile(r"讣告"),
    re.compile(r"残页"),
    re.compile(r"灰(?:烬|色|白)"),
    re.compile(r"其他(?:被选|幸存|逃生)者"),
    re.compile(r"旧楼|医院|电梯"),           # 可按需扩展
    re.compile(r"第[一二三四五六七1-7]天"),
    re.compile(r"神|祂|那个(?:东西|存在)"),
    re.compile(r"循环"),
    re.compile(r"下一场"),
    re.compile(r"邮(?:件|箱|包)"),           # 讣告送达的现实痕迹
    re.compile(r"黑(?:色|暗)?邮(?:票|戳)"),
    re.compile(r"异(?:味|响|动|常)"),
]


def _work_stage_text(work, stage: str) -> str:
    d = getattr(work, stage, None) if work else None
    return (d.get("text", "") if d else "").strip()


def _chapter_text(desk, chapter: int) -> str:
    w = desk.get(chapter)
    return _work_stage_text(w, "polish") or _work_stage_text(w, "draft") or _work_stage_text(w, "plot") or ""


# ---- 具体检查函数 ----

def check_seven_day_anchor(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查1：七日锚——本章标 Day=N 时，正文不应出现"第N+1天"之后的明确日号。"""
    w = desk.get(chapter)
    if w is None:
        return None
    day = getattr(w, "day", None)
    if day is None or not (1 <= day <= 7):
        return None
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    violations = []
    for m in re.finditer(r"第([一二三四五六七1-7])天", text):
        ch = m.group(1)
        n = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}.get(ch)
        if n is None and ch.isdigit():
            n = int(ch)
        if n is not None and n > day:
            violations.append(m.group(0))
    if violations:
        return (f"七日锚违反：本章是 Day{day}，正文出现了未来日号 {sorted(set(violations))}——"
                "日号必须按 Day1→Day7 顺序推进，不能提前出现未来日的事件")
    return None


def check_interlude_spine(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查2：间章必须 8:2——不能纯日常，必须至少出现一个暗线钩子词。"""
    w = desk.get(chapter)
    if w is None or not bool(getattr(w, "is_interlude", False)):
        return None
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    hit = any(p.search(text) for p in _INTERLUDE_HOOK_PATTERNS)
    if not hit:
        # 至少要有 800 字以上才报警，避免半成品误报
        if len(text) < 800:
            return None
        return ("间章8:2脊柱报警：未检测到暗线痕迹（讣告/残页/其他选中者/下一场暗示/诡异异象等）。"
                "现实间章不能写成纯日常流水，必须在 8 成回血里织入 2 成刺——"
                "哪怕是街角一闪而过的黑邮票、咖啡店窗外不自然的凝视、"
                "回家后桌上多出来的灰烬。")
    return None


def check_obsession_cost_delay(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查3：执念代价延迟——同场使用金手指后 2 章内应有副作用迹象。
    简化策略：在正文里检测"使用了执念/金手指"类表达（如能力词+动作），
    向后查 2 章（同一 field 内）是否有代价词；没有则提示。"""
    w = desk.get(chapter)
    if w is None:
        return None
    field_no = getattr(w, "field_no", None)
    day = getattr(w, "day", None)
    is_inter = bool(getattr(w, "is_interlude", False))
    if field_no is None or is_inter:
        return None
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    # 简化：从 field_store 取 obsession_power 关键词
    power_kw = ""
    if field_store is not None:
        fw = field_store.get(field_no)
        if fw is not None:
            power_kw = (fw.obituary.obsession_power or "").strip()
    use_signals = []
    # 显式使用信号词
    for kw in ("用(?:了|起)?(?:这|那)?(?:股|份|种)?力量", "催动", "发动", "借(?:着|了|用)",
               "金手指", "执念(?:之|的)力", "那股(?:冰冷|灼热|阴寒|诡异)"):
        if re.search(kw, text):
            use_signals.append(kw)
            break
    if power_kw:
        # 取能力描述前 6 字作为关键词
        short_kw = re.sub(r"[的了是在和与及而，。；、]", "", power_kw)[:6]
        if short_kw and short_kw in text:
            use_signals.append(short_kw)
    if not use_signals:
        return None
    # 检查本章+后续2章（同 field 内）是否有代价词
    cost_found = bool(_COST_PATTERN.search(text))
    if not cost_found:
        # 向后查最多 2 章同 field 正文（如果存在）
        for later in range(chapter + 1, chapter + 3):
            lw = desk.get(later)
            if lw is None:
                continue
            if getattr(lw, "field_no", None) != field_no:
                break
            lt = _chapter_text(desk, later)
            if lt and _COST_PATTERN.search(lt):
                cost_found = True
                break
    if not cost_found:
        return ("执念代价延迟报警：本章出现了使用执念金手指的迹象（" + "/".join(use_signals)[:40]
                + "…），但本章及后续 2 章内尚未检测到副作用描写。"
                "克苏鲁铁律——每次借手艺都必须有代价：头痛/鼻血/记忆丢失/情绪失控/被误认……"
                "否则爽点会积累成主角光环，违反「什么都不是」的主角定位。")
    return None


def check_tang_pov(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查4：唐迟 POV 泄漏——非间章不能出现全知叙述。"""
    w = desk.get(chapter)
    if w is None:
        return None
    if bool(getattr(w, "is_interlude", False)):
        return None     # 间章允许 NPC 视角/全知
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    for pat, msg in _POV_LEAK_PATTERNS:
        if pat.search(text):
            return f"POV 泄漏报警：{msg}。非间章必须严格锁定唐迟限知视角。"
    return None


def check_endgame_seeds(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查5：终局种子审计——按已写场数提醒应埋的种子数（软提醒，不判死）。"""
    if book_store is None or field_store is None:
        return None
    w = desk.get(chapter)
    if w is None or bool(getattr(w, "is_interlude", False)):
        return None
    field_no = getattr(w, "field_no", None)
    if field_no is None:
        return None
    # 只在 Day7 章节（场收束）才审计种子
    day = getattr(w, "day", None)
    if day != 7:
        return None
    try:
        endgame = book_store.endgame_raw()
        layers = endgame.get("layers", []) if isinstance(endgame, dict) else []
    except Exception:
        return None
    # 按场号算到了哪一层（每 3 场解锁一层，起点层 1）
    target_layer_idx = min((field_no - 1) // 3, len(layers) - 1) if layers else -1
    if target_layer_idx < 0:
        return None
    layer = layers[target_layer_idx]
    seeds = layer.get("seeds", []) if isinstance(layer, dict) else []
    required = int(layer.get("required_seeds", 1)) if isinstance(layer, dict) else 1
    if len(seeds) < required:
        name = layer.get("name", f"第{target_layer_idx+1}层") if isinstance(layer, dict) else f"第{target_layer_idx+1}层"
        misdirection = layer.get("misdirection", "") if isinstance(layer, dict) else ""
        return (f"终局种子提醒：本场（第{field_no}场）是 Day7 收束场，应对终局「{name}」层埋 1 颗种子"
                f"（该层要求 {required} 颗，已有 {len(seeds)} 颗）。"
                + (f" 误导方向参考：{misdirection}" if misdirection else ""))
    return None


def check_power_escalation(desk, chapter: int, book_store=None) -> str | None:
    """检查6：力量升级克制——侵蚀度≥40后不能无代价爽文。"""
    if book_store is None:
        return None
    w = desk.get(chapter)
    if w is None:
        return None
    if bool(getattr(w, "is_interlude", False)):
        return None
    try:
        tangchi = book_store.tangchi_raw()
        erosion = int(tangchi.get("erosion_level", 0))
    except Exception:
        return None
    if erosion < 40:
        return None
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    op_hit = any(p.search(text) for p in _OVERPOWER_PATTERNS)
    if op_hit and not _COST_PATTERN.search(text):
        return (f"力量升级克制报警：唐迟侵蚀度已达 {erosion}/100（临近或越过"
                "「情感钝化」里程碑），但本章出现了轻松碾压式描写，"
                "且未同步兑现代价（副作用/自我消融/记忆缺失/情绪空洞）。"
                "高侵蚀段的「强」必须伴随明显的「人在变少」——"
                "手艺越厚，唐迟本人越薄。")
    return None


def book_review_checks(desk, chapter: int,
                       book_store=None, field_store=None) -> list[str]:
    """运行全部六条书规检查，返回报警列表（空=全部通过）。"""
    warnings: list[str] = []
    for fn in (check_seven_day_anchor, check_interlude_spine,
               check_obsession_cost_delay, check_tang_pov,
               check_endgame_seeds, check_power_escalation):
        try:
            msg = fn(desk, chapter, book_store=book_store, field_store=field_store)
        except Exception:
            msg = None
        if msg:
            warnings.append(msg)
    return warnings
