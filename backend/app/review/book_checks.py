"""《第七日的亡者》书专属审稿检查（纯数据推导，不走 LLM）。

八条书规硬检：
1. 唐迟入戏日号锚：章节标注 day=N（指唐迟入戏第 N 天 / T-(7-N)）时，正文不能出现「第N+1天」及之后的入戏日号
2. 8:2 间章脊柱：is_interlude 的章节必须出现暗线（残页/其他选中者/下一场暗示）关键词
3. 执念代价延迟：同场使用执念金手指后 1-2 章内必须有副作用迹象
4. 唐迟 POV 泄漏：正文（含间章）不能出现唐迟视角以外的全知叙述（软检：点名检查"他不知道/他没发现"类叙述者越界词）
5. 终局种子审计：每场至少埋一颗对应层级的终局种子（按 endgame 锚点 pending 数）
6. 力量升级克制：侵蚀度 ≥40 后不能再出现唐迟单方面轻松碾压的描写（强金手指必有代价描写）
7. 伏笔兑现兜底：章前方向卡声明的 foreshadow_advance 子串命中 <50% → 软提醒（不阻断交稿）
8. 角色知情边界：声明为「不知情」的角色不得在本章表现出知道（软提醒，spec 3）

注：副本原剧本时间线（小时/天/月/年自由跨度）与此锚无关——"入戏第 N 天"是唐迟自己的倒计时，
   不是原剧本的日历天；原剧本事件可按自身节奏在任何入戏日推进。

所有检查返回 string | None：None=通过，string=报警内容。与 rhythm_hunger/strand_warnings 同构。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.book import BookStore
    from app.entities.store import EntityStore
    from app.fields import FieldStore
    from app.wizard import DeskStore

# 知情边界检查的成稿门槛（同 check_foreshadow_payoff 的先例）：
# 启发式关键词匹配天然误判率高，半成品/draft 阶段一律不报。
KNOWLEDGE_MIN_CHARS = 600

# ---- 8) 知情边界：角色「表现出知道」他被声明为不知情的东西 ----
# 越界信号必须**紧挨角色名**：知识边界是「谁」的边界，同一个秘密在场上有
# 别的角色知道是合法的，只有「被声明不知情的那个人表现出知道」才叫越界。
_KNOWLEDGE_LEAK_TMPL = (
    r"{name}[^。！？\n]{{0,20}}?"
    r"(?:竟然|居然|知道|清楚|明白|认定|看穿|看破|洞穿|心里有数|早就|不就是)"
)
# 反向信号：不知情角色的合理表现（困惑、被蒙在鼓里、追问）——命中则视为合法
_KNOWLEDGE_BLESSED_TMPL = (
    r"{name}[^。！？\n]{{0,16}}?"
    r"(?:不知道|不懂|不明白|没听说|没提过|一头雾水|莫名其妙|不知所以|怔住|愣住|皱眉|困惑)"
)
# 关键词切分：blinded_to 是人写的自由文本，用顿号/逗号/分号/换行切成多个候选
_KNOWLEDGE_SPLIT_RE = re.compile(r"[、，,；;/\n]+|(?:以及|还有|和)")

# 关键词长度下限：单字关键词（「命」「局」）在正文里撞车率极高，一律不查
_KNOWLEDGE_KW_MIN = 2
# 关键词长度上限：超长句不是「信息点」，无法作为可校验的边界
_KNOWLEDGE_KW_MAX = 12


# ---- 4) POV 越界关键词（副本章与间章出现 → 报警） ----
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
    """检查1：唐迟倒计时锚——本章标入戏Day=N时，正文不应出现唐迟视角下"第N+1天"之后的未来日号（不限制副本原剧本事件的并行出现）。"""
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
        return (f"唐迟入戏日号锚违反：本章是入戏第{day}天（T-{7-day}），正文出现了未来入戏日号 {sorted(set(violations))}——"
                "唐迟入戏日号必须按入戏第1→7天顺序推进，不能提前出现未来入戏日的事件"
                "（副本原剧本自身的时间表述如「二十年后」「那天夜里」「第三天凌晨」等不受此限）")
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
    """检查4：唐迟 POV 泄漏——副本章与间章都必须锁定唐迟限知视角。

    早前这里对间章直接 return None（认为「间章允许 NPC 视角/全知」），
    但全书 POV 铁律是唐迟限知，间章只是场景换到蓝星，叙述者没换——
    读者仍然只能通过唐迟的眼睛看这个世界。放开间章等于给模型开了
    「间章可以切全知旁白」的口子，它会真的这么写。
    """
    w = desk.get(chapter)
    if w is None:
        return None
    text = _chapter_text(desk, chapter)
    if not text:
        return None
    for pat, msg in _POV_LEAK_PATTERNS:
        if pat.search(text):
            kind = "间章" if bool(getattr(w, "is_interlude", False)) else "本章"
            return (f"POV 泄漏报警：{msg}。{kind}必须严格锁定唐迟限知视角——"
                    "间章只是场景换到蓝星，叙述者仍然是唐迟，不能借『间章』切全知旁白。")
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
    # 只在 T-0 章节（入戏第7天 / 场收束）才审计种子
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
        return (f"终局种子提醒：本场（第{field_no}场）是 T-0 收束场（入戏第7天），应对终局「{name}」层埋 1 颗种子"
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


# ---- 7) 伏笔兑现兜底（基于章前方向卡 foreshadow_advance 子串检测，软提醒） ----

def _brief_foreshadows(work) -> list[str]:
    """从 work.brief.selected.foreshadow_advance 提取伏笔关键短语列表。"""
    if work is None:
        return []
    brief = getattr(work, "brief", None)
    if not isinstance(brief, dict):
        return []
    sel = brief.get("selected")
    if not isinstance(sel, dict):
        return []
    raw = sel.get("foreshadow_advance") or sel.get("foreshadow") or []
    out: list[str] = []
    if isinstance(raw, str):
        # 支持顿号/逗号分隔的字符串
        for piece in re.split(r"[、，,；;]+", raw):
            piece = piece.strip()
            if piece:
                out.append(piece)
    elif isinstance(raw, list):
        for x in raw:
            s = str(x).strip() if x is not None else ""
            if s:
                out.append(s)
    return out


def check_foreshadow_payoff(desk, chapter: int, book_store=None, field_store=None) -> str | None:
    """检查7：伏笔兑现兜底——章前方向卡列出的 foreshadow_advance 子串命中 <50% → 提醒。

    这是兜底软提醒（book_review_checks 本身不阻断交稿，review_chapter 的硬阈值独立）：
    子串法误判率不低（语义改写/同义词替换都会漏判），所以只在字数≥600 的成稿
    且命中比 <50% 时才提醒，避免半成品误报。
    """
    w = desk.get(chapter)
    if w is None:
        return None
    foreshadows = _brief_foreshadows(w)
    if not foreshadows:
        return None
    text = _chapter_text(desk, chapter)
    if not text or len(text) < 600:
        return None
    hit: list[str] = []
    miss: list[str] = []
    for kw in foreshadows:
        # 关键短语长度≥2才做子串匹配，避免单字误判
        if len(kw) >= 2 and kw in text:
            hit.append(kw)
        else:
            miss.append(kw)
    if not miss:
        return None
    total = len(foreshadows)
    ratio = len(hit) / total if total else 1.0
    if ratio >= 0.5:
        return None
    return (f"伏笔兑现提醒：章前方向卡列出了 {total} 项本章需推进/埋设的伏笔，"
            f"子串命中 {len(hit)} 项（{int(ratio*100)}%），低于 50% 阈值。"
            f"未命中：{'、'.join(miss[:5])}"
            + ("…" if len(miss) > 5 else "")
            + "。请检查是否遗漏了伏笔推进；若已通过同义改写/侧面暗示完成兑现，"
              "可在前端手动标记忽略此提醒。")


def _knowledge_keywords(raw: str) -> list[str]:
    """把 blinded_to 自由文本切成可校验的关键词列表。

    只保留长度在 [2, 12] 的词：单字撞车率极高（「命」「局」「他」），
    超长句根本不是可校验的信息点。
    """
    out: list[str] = []
    for piece in _KNOWLEDGE_SPLIT_RE.split(raw or ""):
        kw = piece.strip().strip("「」\"'（）()【】[]")
        if _KNOWLEDGE_KW_MIN <= len(kw) <= _KNOWLEDGE_KW_MAX:
            out.append(kw)
    return out


def check_knowledge_boundary(desk, chapter: int, book_store=None,
                             field_store=None,
                             entity_store: "EntityStore | None" = None) -> str | None:
    """检查8：角色知情边界——被显式声明为「不知情」的角色不得在本章表现出知道。

    只查**明确声明**的边界（character.state_attrs["blinded_to"]），空值不检查——
    不自动推断「他应该知道却不知道」，那需要世界模型，成本与误判率都不可控。

    判定要求三个条件同时成立，宁可漏报不可误报：
    1. 本章是成稿（≥ KNOWLEDGE_MIN_CHARS 字），半成品一律放过；
    2. 关键词 K 出现在正文中；
    3. 角色名附近出现认知动词（知道/看穿/早就…）且**没有**困惑类反向信号。

    为什么是软提醒：启发式匹配误判率天然高（关键词可能只是巧合同名、
    「早就」也可能修饰别的事）。按 check_foreshadow_payoff 的先例，
    只在成稿且命中明确时报，不阻断交稿。
    """
    if entity_store is None:
        return None
    w = desk.get(chapter)
    if w is None:
        return None
    text = _chapter_text(desk, chapter)
    if not text or len(text) < KNOWLEDGE_MIN_CHARS:
        return None

    violations: list[str] = []
    for e in entity_store.list(type="character"):
        raw = (e.state_attrs or {}).get("blinded_to") or ""
        if not str(raw).strip():
            continue                      # 没声明 = 不检查
        for kw in _knowledge_keywords(str(raw)):
            if kw not in text:
                continue
            name = re.escape(e.name)
            leak = re.search(_KNOWLEDGE_LEAK_TMPL.format(name=name), text)
            if not leak:
                continue
            # 同一句里出现困惑类信号 → 合法的「不知道」，放过
            sent_start = max(text.rfind("。", 0, leak.start()),
                             text.rfind("\n", 0, leak.start())) + 1
            sentence = text[sent_start:leak.end() + 12]
            if re.search(_KNOWLEDGE_BLESSED_TMPL.format(name=name), sentence):
                continue
            violations.append(f"{e.name}×「{kw}」")

    if not violations:
        return None
    detail = "、".join(violations[:4]) + ("…" if len(violations) > 4 else "")
    return (f"角色知情边界报警（软提醒）：{detail} —— 该信息在实体库里被显式登记为"
            "此角色「不知情」，但本章里他表现出知道/看穿了它。"
            "无限流设定的张力全靠信息不对称撑着：NPC 不能说出他不该知道的事。"
            "若这是本章刚揭晓的转折（角色刚得知），请在实体库更新他的知情状态后再审；"
            "若是刻意误导（他其实早知道），删掉该角色的 blinded_to 声明即可。")


def book_review_checks(desk, chapter: int,
                       book_store=None, field_store=None,
                       entity_store=None) -> list[str]:
    """运行全部八条书规检查，返回报警列表（空=全部通过）。"""
    warnings: list[str] = []
    for fn in (check_seven_day_anchor, check_interlude_spine,
               check_obsession_cost_delay, check_tang_pov,
               check_endgame_seeds, check_power_escalation,
               check_foreshadow_payoff):
        try:
            msg = fn(desk, chapter, book_store=book_store, field_store=field_store)
        except Exception:
            msg = None
        if msg:
            warnings.append(msg)
    try:
        msg = check_knowledge_boundary(desk, chapter, book_store=book_store,
                                       field_store=field_store,
                                       entity_store=entity_store)
    except Exception:
        msg = None
    if msg:
        warnings.append(msg)
    return warnings
