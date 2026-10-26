"""副本卷实体预落库（plan-time seeding）。

## 为什么要有这个模块

实体库原来只有一个入口：`finalize_chapter` 在正文定稿后调 `extract_entities` 从正文里
拆实体。那是**事后追认**——等到第 5 章定稿，第 1 章该出现的角色、道具、伏笔才进库，
于是：

1. 前 4 章写作时注入的实体账本是空的，模型拿不到「这个角色已经出场过」；
2. 伏笔永远处于「未埋」状态，因为埋设那一章还没写；
3. 角色卡（习惯动作/口癖/口音）没有时机配置——写正文时才发现这个角色没声音。

正确的时机是**副本卷创作链路里、章前方向卡生成之前**：
选副本 → 详设 6 卡 → 节奏蓝图（chapter_plan）→ 章前方向卡 → 章细纲 → 正文。
这条链路上每个阶段的产物都已经声明了本章会有哪些角色、地点、道具、伏笔，
所以实体库应该在这里就落库，让每一章写作时都能注入到。

## 落库分两级

| 级别 | 时机 | 来源 | 精度 |
|---|---|---|---|
| 卷级 seed | rhythm/finalize | 详设 6 卡 + chapter_plan 汇总 | 全卷出场过的都建，不写 details |
| 章级 sync | brief/select | 该章 brief.selected | 精确到本章，并回写伏笔埋设/回收状态 |

两级都**幂等**：重复调用只补空缺，不覆盖作者已改过的内容。
定稿后的 extract_entities 保留，但降级为「核对/补漏」，不再是主要来源。

## 设计约束

- 不臆造：只从已有产物字段里提取，字段没有就不建。
- 不覆盖：作者手改过的 summary/details/voiceprint 一律不动。
- 幂等：同一份产物重复落库不产生重复实体。
- 角色可配声音档案：角色建档时若能推断职业，就给出「待确认」的初始动作建议，
  但绝不自动写死——声音档案是作者的决定权。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.entities.store import PROTAGONIST_ID, EntityStore, Voiceprint

log = logging.getLogger("entities.vault_plan")


# ============================================================
# 结果类型
# ============================================================

@dataclass
class SeedResult:
    """一次落库的产出摘要（用于 API 返回和前端提示）。"""
    created: list[dict] = field(default_factory=list)     # 新建的实体摘要
    existed: list[str] = field(default_factory=list)      # 已存在、跳过的实体名
    foreshadow_planted: list[str] = field(default_factory=list)   # 本次标记为「已埋」的伏笔
    foreshadow_paid: list[str] = field(default_factory=list)       # 本次标记为「已回收」的伏笔
    voices_missing: list[str] = field(default_factory=list)       # 建了档但没配声音档案的角色

    @property
    def created_count(self) -> int:
        return len(self.created)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "created_count": self.created_count,
            "existed": self.existed,
            "foreshadow_planted": self.foreshadow_planted,
            "foreshadow_paid": self.foreshadow_paid,
            "voices_missing": self.voices_missing,
        }

    def merge(self, other: "SeedResult") -> "SeedResult":
        self.created.extend(other.created)
        self.existed.extend(other.existed)
        self.foreshadow_planted.extend(other.foreshadow_planted)
        self.foreshadow_paid.extend(other.foreshadow_paid)
        self.voices_missing.extend(other.voices_missing)
        return self


# ============================================================
# 文本清洗：从 LLM 产物里提取干净的实体名
# ============================================================

# 「宿管王姨（半透明）」→ 主名「宿管王姨」，括号内容进 summary
_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")
# 数量词/序数前缀：「3个宿管」「第一位死者」
_COUNT_PREFIX_RE = re.compile(r"^[0-9一二三四五六七八九十两几]+\s*(个|名|位|只|条|张|把|个位|位)?")
# 分隔符：顿号/逗号/斜杠/竖线
_SPLIT_RE = re.compile(r"[、,，;；/｜|]+")
# 明显不是实体名的词（抽象概念/机制名/纯指示词）
_STOP_NAMES = {
    "无", "空", "无", "主角", "读者", "视角", "无人", "无名", "有人",
    "自己", "对方", "众人", "所有人", "其他人",
}

# 人名误判清洗：中文里「林薇没说话」会被正则抓成「林薇没」。
# 这些字出现在候选名尾部时，多半是下一小句的开头（否定/副词/量词）。
_NAME_TAIL_JUNK = (
    "没", "不", "也", "又", "还", "就", "都", "却", "才", "很", "在", "是",
    "的", "了", "着", "过", "和", "与", "或", "而", "但", "并", "把", "被",
    "一", "两", "三", "四", "五", "六", "七", "八", "九", "十",
    "说", "道", "问", "答", "喊", "吼", "笑", "看", "想", "走", "来", "去",
)


def _clean_name(raw: Any) -> str:
    """把 LLM 产出的角色/地点/道具名清洗成可入库的名字。

    - 去掉数量前缀、括注、空白
    - 截断到合理长度（实体名不该是一整句话）
    - 过长的说明性文本直接判为非实体名
    """
    if not isinstance(raw, str):
        return ""
    s = raw.strip().strip("「」『』\"'“”")
    if not s:
        return ""
    # 截第一个子句——实体名不会跨标点
    for sep in ("。", "！", "？", "；", ";", "，", ",", "（", "("):
        if sep in s:
            s = s.split(sep)[0]
            break
    s = _PAREN_RE.sub("", s).strip()
    s = _COUNT_PREFIX_RE.sub("", s).strip()
    s = s.strip(" 　·-—、,，。.")
    if not s or len(s) < 1:
        return ""
    # 太长的是描述句不是名字
    if len(s) > 20:
        return ""
    if s in _STOP_NAMES:
        return ""
    # 剥掉尾部粘连的虚词（「林薇没」→「林薇」），至少剥到 2 字
    changed = True
    while changed and len(s) > 2:
        changed = False
        if s[-1] in _NAME_TAIL_JUNK:
            s = s[:-1]
            changed = True
    if len(s) < 2:
        return ""
    if s in _STOP_NAMES:
        return ""
    return s


def _extract_paren_note(raw: str) -> str:
    """取括注内容作为 summary 线索。"""
    m = _PAREN_RE.search(raw or "")
    return m.group(1).strip() if m else ""


def _split_names(raw: Any) -> list[tuple[str, str]]:
    """把 '甲、乙（异常）、丙' 拆成 [(name, note), ...]。"""
    out: list[tuple[str, str]] = []
    if isinstance(raw, str):
        parts = _SPLIT_RE.split(raw)
    elif isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        return out
    for p in parts:
        name = _clean_name(p)
        if name:
            out.append((name, _extract_paren_note(p)))
    # 去重保序
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for n, note in out:
        if n not in seen:
            seen.add(n)
            uniq.append((n, note))
    return uniq


def _first_sentence(text: Any, limit: int = 60) -> str:
    if not isinstance(text, str):
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    if not s:
        return ""
    m = re.match(r"^(.{1,%d}?句号|.{1,%d}?。|.{1,%d})" % (limit, limit, limit), s)
    out = (m.group(1) if m else s[:limit]).strip()
    return out


# ============================================================
# 人物档案提取：从已有的结构化数据拼 summary / details
# ============================================================
#
# **为什么需要这一层**：卷级落库原本只传「名字 + 一句括注」，
# 于是实体库里 107 个 volume_plan 角色全是空档案——
# 简介写的是「周朔（电话）」里的括注「电话」，细节档案全空。
#
# 但数据其实**早就在** adapted_module 里了：cast 有职业/年龄/外貌/性格/
# 动机/秘密，renamed_cast 有身份定位，host_character 有身体状态与死状。
# 这里只是把它们取出来，按固定格式拼成实体档案。
#
# 三条铁律：
# 1. **不臆造**：源数据里没有的字段就不写这一行，绝不用占位文案凑格式。
# 2. **不覆盖**：作者改过的档案一律不动。
# 3. **零 token**：纯字段搬运，不调模型。

# 档案行标题 → 对应的源字段名（按优先级依次找）
_PROFILE_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("身份", ("occupation", "role", "identity")),
    ("年龄", ("age",)),
    ("性别", ("gender", "sex")),
    ("外貌", ("appearance", "look", "appearance_desc")),
    ("性格", ("personality", "character", "temperament")),
    ("动机", ("motivation", "goal", "objective")),
    ("秘密", ("secret", "secrets", "hidden")),
    ("关键技能", ("key_skills", "skills")),
)

# 宿主角色额外字段（只有 host_character 有）
_HOST_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("身体状态", ("body_state",)),
    ("原剧本位置", ("original_story_position",)),
    ("第七日死状", ("seventh_day_death",)),
    ("隐藏死因", ("hidden_death_cause",)),
    ("挣扎钩子", ("struggle_hook",)),
    ("信息差", ("info_gap_hint",)),
)


def _fmt_skills(raw: Any) -> str:
    """把技能 dict 压成「侦查74/意志57/…」这种一行。"""
    if isinstance(raw, dict):
        items = list(raw.items())[:8]
        return "/".join(f"{k}{v}" for k, v in items if k)
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _field_text(card: dict, keys: tuple[str, ...], fmt=None) -> str:
    """从卡里取第一个非空的指定字段。"""
    for k in keys:
        v = card.get(k)
        if v is None:
            continue
        text = fmt(v) if fmt else str(v).strip()
        text = (text or "").strip()
        if text:
            return text
    return ""


def _build_profile(
    name: str,
    *,
    cast_card: dict | None = None,
    renamed: dict | None = None,
    player_card: dict | None = None,
    host: dict | None = None,
    note: str = "",
) -> tuple[str, str]:
    """由结构化数据拼出 (summary, details)。

    summary：一句话身份定位，如「周朔，雾汀县公安局刑侦中队长。」
    details：多行档案，每行「标题：内容」，源数据没有的字段直接不出现。

    多个来源按「信息量」合并：cast 字段最全，其次 renamed_cast 的 role，
    再是 player_cards 的 desc。同一字段取先命中且非空的。
    """
    sources: list[dict] = []
    for s in (cast_card, player_card, host, renamed):
        if isinstance(s, dict) and s:
            sources.append(s)

    def pick(keys: tuple[str, ...], fmt=None) -> str:
        for src in sources:
            text = _field_text(src, keys, fmt)
            if text:
                return text
        return ""

    # ---- summary：姓名 + 身份定位 ----
    title = pick(("occupation", "role", "identity"))
    summary = f"{name}，{title}。" if title else ""
    if not summary:
        # 退到 player_cards 的描述首句
        desc = ""
        for src in sources:
            desc = _first_sentence(str(src.get("desc") or src.get("summary") or ""), 50)
            if desc:
                break
        summary = desc
    if not summary and note:
        summary = note.strip()

    # ---- details：逐字段 ----
    lines: list[str] = []
    for label, keys in _PROFILE_FIELDS:
        text = pick(keys, _fmt_skills if label == "关键技能" else None)
        if text:
            lines.append(f"{label}：{text}")
    if isinstance(host, dict):
        for label, keys in _HOST_FIELDS:
            text = _field_text(host, keys)
            if text:
                lines.append(f"{label}：{text}")
    if renamed:
        # 字段名以 sushi_generator 的 schema 为准（original / new_name / role），
        # 旧写法 from 也兼容。
        old_name = str(renamed.get("original") or renamed.get("from") or "").strip()
        if old_name:
            lines.append(f"原名：{old_name}")
    if not lines and note:
        lines.append(f"备注：{note.strip()}")

    return summary.strip(), "\n".join(lines)


def _now_iso() -> str:
    """当前时间戳，格式与 EntityStore 内部一致。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_placeholder_summary(summary: str) -> bool:
    """判断现有 summary 是不是「碎片/占位」，是才允许覆盖。

    碎片特征：空、太短、或者只是某个括注（如「电话」）。
    作者认真写的简介再短也超过 8 个字，且不会只有一两个字的通名。
    """
    s = (summary or "").strip()
    if not s:
        return True
    if len(s) <= 6:
        return True
    return False


# ============================================================
# 伏笔名清洗：伏笔描述常是整句，需要抽出可引用的短名
# ============================================================

_FS_PREFIX_RE = re.compile(r"^伏笔点?\s*\d*\s*[:：]?\s*")


def _clean_foreshadow_name(raw: Any) -> str:
    """伏笔种子文本 → 短名。

    '伏笔点1：出现一枚刻着学号的铜扣，暂不解释' → '刻着学号的铜扣'

    关键：伏笔种子是**整句描述**（LLM 契约里就允许 15-40 字），
    逗号后面通常是「暂不解释」「待回收」这类**操作备注而不是名字的一部分**。
    所以这里不走 _split_names——按逗号拆会造出「暂不解释」这种垃圾实体。
    只按分号/换行拆（那才是多个伏笔并列），逗号只用来取第一个分句。
    """
    if not isinstance(raw, str):
        return ""
    s = _FS_PREFIX_RE.sub("", raw.strip()).strip()
    if not s:
        return ""
    # 分号/换行 = 多个伏笔并列；冒号后是正文
    if ";" in s or "；" in s or "\n" in s:
        s = re.split(r"[;；\n]", s)[0]
    # 去掉「回收上一卷伏笔：」这类前缀式说明
    if "：" in s or ":" in s:
        head, _, tail = s.partition("：" if "：" in s else ":")
        # 头部是说明（回收/推进/呼应/埋下）时取尾部，否则整段留用
        if re.match(r"^(回收|推进|呼应|埋|补|接|收|用)", head):
            s = tail.strip()
    # 只取第一个逗号分句（逗号后多为操作备注）
    for sep in ("，", ",", "。", "；", ";"):
        if sep in s:
            s = s.split(sep)[0]
            break
    s = _PAREN_RE.sub("", s).strip(" 　·-—")
    if not s or len(s) > 24:
        return ""
    # 纯操作备注不是伏笔名
    if s in ("暂不解释", "待回收", "不解释", "略", "无", "暂时保留"):
        return ""
    return s


def _split_foreshadow_names(raw: Any) -> list[str]:
    """伏笔列表 → 短名列表。列表元素本身是整句，按分号/换行再拆。"""
    out: list[str] = []
    if isinstance(raw, str):
        items = re.split(r"[;；\n]", raw)
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        return out
    for it in items:
        # 列表元素内部还可能有「甲；乙」并列
        for piece in re.split(r"[;；\n]", it):
            nm = _clean_foreshadow_name(piece)
            if nm and nm not in out:
                out.append(nm)
    return out


# ============================================================
# 核心：卷级落库
# ============================================================

def _seed_character(store: EntityStore, name: str, note: str,
                    chapter: int | None, source: str,
                    summary_hint: str = "",
                    details_hint: str = "") -> str:
    """建/取一个角色。返回 'created' | 'existed'。

    刻意**不自动写声音档案**：习惯动作/口癖是作者的决定权，
    自动编一套反而会污染「所有角色共用动作库」这个 AI 味源头。
    但会记进 SeedResult.voices_missing 提醒作者去配。

    档案补写规则（**不覆盖作者**）：
    - summary 是空/碎片（≤6 字）→ 用 summary_hint 覆盖
    - details 是空 → 用 details_hint 覆盖
    - 作者写过 → 一律不动
    """
    existing = store.find("character", name)
    if existing is not None:
        # 只补 appearances，不动作者改过的 summary/details
        if chapter is not None and chapter not in existing.appearances:
            existing.appearances.append(chapter)
            existing.appearances.sort()
        # 档案补写：只填空位，绝不覆盖作者写过的内容
        changed = False
        if _is_placeholder_summary(existing.summary) and summary_hint:
            existing.summary = summary_hint.strip()
            changed = True
        if not (existing.details or "").strip() and details_hint:
            existing.details = details_hint.strip()
            changed = True
        if changed:
            existing.updated_at = _now_iso()
        return "existed"
    summary = summary_hint or (f"{note}" if note else "")
    store.add("character", name, summary=summary, details=details_hint,
              source=source, chapter=chapter)
    return "created"


def _seed_simple(store: EntityStore, etype: str, name: str, note: str,
                 chapter: int | None, source: str,
                 summary_hint: str = "", state_attrs: dict | None = None) -> str:
    """建/取 location/item/plotline 这类实体。"""
    existing = store.find(etype, name)
    if existing is not None:
        if chapter is not None and chapter not in existing.appearances:
            existing.appearances.append(chapter)
            existing.appearances.sort()
        return "existed"
    summary = summary_hint or (f"{note}" if note else "")
    store.add(etype, name, summary=summary, source=source, chapter=chapter,
              state_attrs=state_attrs or {})
    return "created"


def _seed_foreshadow(store: EntityStore, name: str, chapter: int | None,
                     source: str, planted: bool = True,
                     summary: str = "") -> tuple[str, bool]:
    """建/取伏笔。返回 (created|existed, 是否本次新标为已埋)。"""
    name = _clean_foreshadow_name(name)
    if not name:
        return "", False
    existing = store.find("foreshadow", name)
    if existing is None:
        e = store.add("foreshadow", name,
                      summary=summary or ("" if not source else ""),
                      status="planted" if planted else "planted",
                      source=source, chapter=chapter)
        if chapter is not None:
            e.state_attrs.setdefault("plant_chapter", str(chapter))
        return "created", bool(planted)
    # 已存在：只在 planted 且当前未标记埋设章时补 plant_chapter，不覆盖已有
    if planted and chapter is not None and not existing.state_attrs.get("plant_chapter"):
        existing.state_attrs["plant_chapter"] = str(chapter)
        if chapter not in existing.appearances:
            existing.appearances.append(chapter)
            existing.appearances.sort()
    return "existed", False


def seed_from_volume_plan(
    store: EntityStore,
    vol,
    *,
    start_chapter_no: int | None = None,
    source: str = "volume_plan",
) -> SeedResult:
    """卷级预落库：扫描整卷节奏蓝图 + 详设，建档本章骨架里会出现的一切。

    数据来源（都是已锁定的产物，不臆造）：
    - rhythm_blueprint.chapter_plan[*].characters_on_stage / location / foreshadow_seeds
    - rhythm_blueprint.chapter_plan[*].foreshadow_payback
    - vol.death_nodes / climax_nodes / clue_nodes 里的角色、地点线索
    - vol.host_character（宿主）
    - vol.adapted_module.player_cards / renamed_cast（改编后的角色卡）

    幂等：重复调用只补 appearances 与伏笔埋设章。
    """
    res = SeedResult()
    bp = getattr(vol, "rhythm_blueprint", None) or {}
    plan = bp.get("chapter_plan") if isinstance(bp, dict) else []
    if not isinstance(plan, list):
        plan = []
    start_no = int(start_chapter_no or getattr(vol, "start_chapter_no", 1) or 1)

    def _abs_ch(rel_no: int) -> int | None:
        return start_no + max(rel_no - 1, 0) if start_no else None

    def _record(etype: str, name: str, note: str, chapter: int | None,
                summary_hint: str = "", details_hint: str = "") -> None:
        if etype == "character":
            r = _seed_character(store, name, note, chapter, source,
                                summary_hint, details_hint)
        else:
            r = _seed_simple(store, etype, name, note, chapter, source, summary_hint)
        if r == "created":
            e = store.find(etype, name)
            if e is not None:
                res.created.append(e.as_summary())
                if etype == "character":
                    res.voices_missing.append(name)
        else:
            res.existed.append(f"{etype}:{name}")

    # ============================================================
    # 第 1 步：先建「有档案的角色」
    #
    # **顺序很关键**：旧实现先扫 chapter_plan，characters_on_stage 里的
    # 「周朔（电话）」把 summary 写成了「电话」，后面再拿到完整 cast
    # 档案也补不进去了（已存在 → 直接返回）。
    # 现在反过来：先把 adapted_module 里的完整档案写进去，
    # chapter_plan 只负责补出场章号。
    # ============================================================
    adapted = getattr(vol, "adapted_module", None)
    if not isinstance(adapted, dict):
        adapted = {}
    host = getattr(vol, "host_character", None) or {}
    if not isinstance(host, dict):
        host = {}

    # 索引：名字 → 各类结构化卡
    cast_by_name: dict[str, dict] = {}
    for c in (adapted.get("cast") or []):
        if isinstance(c, dict):
            nm = _clean_name(c.get("name") or "")
            if nm:
                cast_by_name.setdefault(nm, c)

    player_by_name: dict[str, dict] = {}
    for c in (adapted.get("player_cards") or []):
        if isinstance(c, dict):
            nm = _clean_name(c.get("name") or "")
            if nm:
                player_by_name.setdefault(nm, c)

    renamed_by_name: dict[str, dict] = {}
    for item in (adapted.get("renamed_cast") or []):
        if isinstance(item, dict):
            # 字段名以 sushi_generator 的 schema 为准：original / new_name / role。
            # 旧写法 to/from 也兼容（早期 mock 数据用过）。
            nm = _clean_name(item.get("new_name") or item.get("to") or "")
            if nm:
                renamed_by_name.setdefault(nm, item)
        elif isinstance(item, str):
            nm = _clean_name(item)
            if nm:
                renamed_by_name.setdefault(nm, {})

    host_name = _clean_name(host.get("name") or "")

    # 1a) cast（改编后的角色卡，字段最全）
    for nm, card in cast_by_name.items():
        summary, details = _build_profile(
            nm, cast_card=card,
            renamed=renamed_by_name.get(nm),
            player_card=player_by_name.get(nm),
            host=host if nm == host_name else None,
        )
        _record("character", nm, "", None, summary, details)

    # 1b) 宿主角色（可能不在 cast 里）
    if host_name and host_name not in cast_by_name:
        summary, details = _build_profile(
            host_name, host=host, renamed=renamed_by_name.get(host_name),
        )
        _record("character", host_name, "", None, summary, details)

    # 1c) player_cards（调查员卡）
    for nm, card in player_by_name.items():
        if nm in cast_by_name:
            continue
        summary, details = _build_profile(
            nm, player_card=card, renamed=renamed_by_name.get(nm),
        )
        _record("character", nm, "", None, summary, details)

    # 1d) renamed_cast 里还没建过的人（只有旧名→新名映射，没有更多档案）
    for nm, item in renamed_by_name.items():
        if nm in cast_by_name or nm in player_by_name or nm == host_name:
            continue
        summary, details = _build_profile(nm, renamed=item)
        _record("character", nm, "", None, summary, details)

    # ============================================================
    # 第 2 步：chapter_plan —— 补出场章号 + 兜底建档
    # ============================================================
    for item in plan:
        if not isinstance(item, dict):
            continue
        try:
            rel = int(item.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if rel <= 0:
            continue
        ch = _abs_ch(rel)

        for name, note in _split_names(item.get("characters_on_stage")):
            # 已在第 1 步建过完整档案 → 这里只补章号
            # （_seed_character 内部会判断「已存在」只补 appearances）
            _record("character", name, note, ch)

        for name, note in _split_names(item.get("location")):
            _record("location", name, note, ch)

        for fs_name in _split_foreshadow_names(item.get("foreshadow_seeds") or []):
            st, planted = _seed_foreshadow(store, fs_name, ch, source, planted=True)
            if st == "created":
                e = store.find("foreshadow", _clean_foreshadow_name(fs_name))
                if e is not None:
                    res.created.append(e.as_summary())
                if planted:
                    res.foreshadow_planted.append(_clean_foreshadow_name(fs_name))
            elif st:
                res.existed.append(f"foreshadow:{fs_name}")

        # 回收的伏笔：标成 paid（如果它已经在库里）
        for fs_name in _split_foreshadow_names(item.get("foreshadow_payback") or []):
            e = store.find("foreshadow", fs_name)
            if e is not None and e.status == "planted":
                e.status = "paid"
                if ch is not None:
                    e.state_attrs["payoff_chapter"] = str(ch)
                res.foreshadow_paid.append(fs_name)

    # ---- 详设节点里的角色线索（characters / who 等字段） ----
    for attr in ("death_nodes", "climax_nodes", "clue_nodes"):
        for node in (getattr(vol, attr, None) or []):
            if not isinstance(node, dict):
                continue
            for key in ("characters", "character", "who", "involved", "actors", "npc"):
                for name, note in _split_names(node.get(key)):
                    _record("character", name, note, None)
            for key in ("location", "place", "where"):
                for name, note in _split_names(node.get(key)):
                    _record("location", name, note, None)
            for key in ("item", "items", "object", "prop", "token"):
                for name, note in _split_names(node.get(key)):
                    _record("item", name, note, None)

    if res.created or res.foreshadow_paid:
        store._flush()
    return res


# ============================================================
# 章级落库：方向卡锁定时同步本章
# ============================================================

def sync_from_brief(store: EntityStore, work, chapter: int | None = None) -> SeedResult:
    """章级同步：把 work.brief.selected 里声明的实体落库，并回写伏笔状态。

    方向卡里的 entities_on_stage / foreshadow_advance / foreshadow_seeds
    此前只是文本，这里让它们真正进实体库——这样本章写正文时能注入到。
    """
    res = SeedResult()
    brief = getattr(work, "brief", None)
    if not isinstance(brief, dict):
        return res
    sel = brief.get("selected")
    if not isinstance(sel, dict):
        return res
    ch = int(chapter or brief.get("chapter_no") or getattr(work, "day", 0) or 0) or None
    source = f"brief:ch{ch}"

    def _record(etype: str, name: str, note: str) -> None:
        if etype == "character":
            r = _seed_character(store, name, note, ch, source)
        else:
            r = _seed_simple(store, etype, name, note, ch, source)
        if r == "created":
            e = store.find(etype, name)
            if e is not None:
                res.created.append(e.as_summary())
                if etype == "character":
                    res.voices_missing.append(name)
        else:
            res.existed.append(f"{etype}:{name}")

    # 在场实体：brief 里是「角色/地点/物品」混在一起的名字
    for name, note in _split_names(sel.get("entities_on_stage")):
        etype = _guess_type(name, store)
        _record(etype, name, note)

    # 本章埋的新伏笔
    for fs_name in _split_foreshadow_names(sel.get("foreshadow_seeds") or []):
        st, planted = _seed_foreshadow(store, fs_name, ch, source, planted=True)
        if st == "created":
            e = store.find("foreshadow", _clean_foreshadow_name(fs_name))
            if e is not None:
                res.created.append(e.as_summary())
            if planted:
                res.foreshadow_planted.append(_clean_foreshadow_name(fs_name))
        elif st:
            res.existed.append(f"foreshadow:{fs_name}")

    # 本章推进/回收的伏笔：库里有就标 paid，没有就先建（作为待埋）
    for fs_name in _split_foreshadow_names(sel.get("foreshadow_advance") or []):
        e = store.find("foreshadow", fs_name)
        if e is not None:
            if e.status == "planted":
                e.status = "paid"
                if ch is not None:
                    e.state_attrs["payoff_chapter"] = str(ch)
                res.foreshadow_paid.append(fs_name)
        else:
            # 方向卡引用了一个库里的伏笔但没建——补建为已回收（说明是旧卷回收）
            e = store.add("foreshadow", fs_name, status="paid",
                          source=source, chapter=ch)
            res.created.append(e.as_summary())
            res.foreshadow_paid.append(fs_name)

    if res.created or res.foreshadow_paid or res.foreshadow_planted:
        store._flush()
    return res


def _guess_type(name: str, store: EntityStore) -> str:
    """brief 里 entities_on_stage 是混合名单，猜它属于哪类。

    库里已有同名实体就沿用其类型；否则按名做弱启发式。
    """
    for etype in ("character", "location", "item", "plotline", "foreshadow"):
        if store.find(etype, name) is not None:
            return etype
    # 弱启发式：带地点后缀/方位词的多半是地点
    if any(k in name for k in ("楼", "室", "屋", "门", "口", "街", "路", "场", "厅", "院", "区")):
        return "location"
    return "character"


# ============================================================
# 审查预警：发现陌生角色
# ============================================================

def find_unknown_characters(store: EntityStore, text: str,
                             known: Iterable[str] | None = None) -> list[str]:
    """从正文里找「库里没有的角色名」，供审查环节提醒作者建档。

    这是**启发式预警**，不是抽取：宁可多提醒，不可漏报。
    只认「引号前的说话人 + 常见称谓」和已知主角名，避免把普通名词当人名。
    """
    if not text:
        return []
    known_set = {k for k in (known or []) if k}
    # 库里所有角色名（含主角）
    for e in store.list(type="character"):
        known_set.add(e.name)
    pname = store.protagonist.current_body_name
    if pname:
        known_set.add(pname)

    found: list[str] = []
    # 「XX说」「XX道」「XX问」等说话人模式
    for m in re.finditer(r"([一-龥]{2,4})(?:说|道|问|答|喊|吼|笑|皱眉|点头|摇头|开口|低声)", text):
        name = _clean_name(m.group(1))
        if not name or name in known_set or name in found:
            continue
        found.append(name)
    return found


def seed_unknown_characters(store: EntityStore, names: Iterable[str],
                            chapter: int | None,
                            source: str = "review") -> SeedResult:
    """把审查发现的陌生角色建档（空档案，等作者配声音）。"""
    res = SeedResult()
    for name in names:
        name = _clean_name(name)
        if not name:
            continue
        r = _seed_character(store, name, "", chapter, source)
        if r == "created":
            e = store.find("character", name)
            if e is not None:
                res.created.append(e.as_summary())
                res.voices_missing.append(name)
        else:
            res.existed.append(f"character:{name}")
    return res