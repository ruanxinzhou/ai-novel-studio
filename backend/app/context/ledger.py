"""项目状态账本：把「已写状态」实时推导成可注入材料。

续写大纲时作者只给新方向，机器必须先知道前面实际写成了什么样：
- 已写剧情（FINALIZED）：章台逐章 plot 梗概——实际发生了什么
- 正文近况（FINALIZED）：最近已写正文的结尾——文风与进度衔接
- 实体账本（REQUIRED）：角色/地点/道具/情节线清单 + 未回收伏笔债务

全部从项目数据实时推导、不落快照——账实永不分离。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

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

# ============================================================
# 原剧本注入预算（origin material）
# ============================================================
#
# 通稿实测 13532 字 ≈ 40 KB UTF-8，占 200K 总预算的 20%。每章都整段塞进来
# 会和「实体快照 + 任务书 + 写作模块 + 描写记忆」抢同一份预算，且中段是
# 机制细节，逐日推进那 7 条 150 字摘要已经覆盖。
#
# 所以只给 **头 + 尾**：开头是世界建立与第一个异常（唐迟最该预先知道的），
# 结尾是零号归位、守巷人接班（底牌）。想给全文就把这两个常量调大；
# 调 0 就退化成「只给梗概 + 逐日推进」的最省模式。
ORIGIN_NARRATIVE_HEAD = 1_500
ORIGIN_NARRATIVE_TAIL = 1_500

#: 逐日推进取几天窗口（本章日 ± 前后各 1 天，共 3 天）
ORIGIN_DAY_WINDOW = 1

#: 跨章连贯窗口：前 N 章 + 后 N 章
CONTINUITY_BEFORE = 2
CONTINUITY_AFTER = 2
#: 前 N 章每章 plot 全文上限（不截成 160 字摘要——那是 _plot_digest 的职责）
CONTINUITY_PLOT_CHARS = 1_500

#: 接缝三律。独立成常量是因为它有两种用法：跟在数据后面，
#: 以及数据全缺时单独出现（卷末最后几章没有「后 2 章」，恰恰最需要「不越」提醒）。
_SEAM_RULES = (
    "■ 接缝三律（这一段真正的价值在这里，光给数据模型不一定照做）\n"
    "1. 接住：前两章已经发生的事实在本章仍然成立——人还在哪、伤还在不在、"
    "谁已经知道了什么、谁还不知道。上一章结尾的钩子要在本章前段有回应。\n"
    "2. 不越：后两章的方向只能作为唐迟的预感/计划/被误导的来源，"
    "不能把后面两章的事件写进本章——提前泄底等于后面两章作废。\n"
    "3. 留口：本章结尾要能被下一章的开头接上，所以本章收尾应落在「变化」上，"
    "并给下一章留一个可执行的未决动作。"
)


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
        # spec 3：与开章任务书同一口径——按「最近一次真正推进」排，
        # 被反复提起却不往前推的伏笔必须浮到最前，不能沉在账底。
        planted.sort(key=lambda e: (_foreshadow_anchor(e) or 10**9, e.name))
        lines = []
        for e in planted[:FORESHADOW_LISTED]:
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            if e.appearances:
                seg += f"（第{min(e.appearances)}章埋"
                if e.last_advanced_ch:
                    seg += f"，第{e.last_advanced_ch}章推进过"
                else:
                    seg += "，从未推进"
                seg += "）"
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


def _voiceprint_material(store: EntityStore, chapter: int | None) -> Material | None:
    """角色声音档案（习惯动作/口癖/口音/语气/心理/沉默习惯）。

    这是「角色怎么说话」的唯一来源，REQUIRED 家族——通用去 AI 味规则和书级文风卡
    都不得覆盖它，也不得自己给角色编动作。档案为空的角色不硬造，只在末尾提示缺配置。
    """
    lines: list[str] = []
    missing: list[str] = []

    # 主角（不在 entities 里，单独取）
    pv = store.protagonist.voiceprint
    pname = store.protagonist.current_body_name or "主角"
    line = pv.render(pname)
    if line:
        lines.append(line)
    if not pv.has_minimum():
        missing.append(f"{pname}（动作 {len(pv.habits)}/3、口癖 {len(pv.catchphrases)}/1）")

    for e in store.characters_in_chapter(chapter):
        # 主角可能也建了同名 character 实体（历史数据），跳过避免重复注入
        if pname and e.name == pname:
            continue
        line = e.voiceprint.render(e.name)
        if line:
            lines.append(line)
        if not e.voiceprint.has_minimum():
            missing.append(f"{e.name}（动作 {len(e.voiceprint.habits)}/3、口癖 {len(e.voiceprint.catchphrases)}/1）")

    if not lines:
        return None

    head = (
        "本书在场角色的固定写法特征（角色声音档案，来自实体库，REQUIRED）。\n"
        "【**档案写的是「倾向」，不是「逐字抄写」的动作清单**】这一条比档案内容本身更重要——\n"
        "· 档案里的描述是**方向**：例如「手部有小动作」「把玩什么东西」「地球的词偶尔滑出来，顿半拍，自己换算成蓝星听得懂的说法」。\n"
        "  你要**按这个方向自己发明**具体动作和具体措辞，而不是把档案里的字面描写搬进正文。\n"
        "· **绝对禁止**把档案里的描述原样写进正文（不要让「手部有小动作」这几个字出现在正文里，"
        "更不要让正文变成「他手部有小动作，把玩什么东西」这种把设定念出来的句子）。\n"
        "· 正确做法：读到「手部有小动作」，你写「他把笔帽在指间转了半圈」；"
        "读到「地球的词偶尔滑出来」，你写他自己也愣一下、把那个词在嘴里过一遍再换个说法。\n"
        "· 档案是**辨识度的来源**：不同角色的倾向不能重合，"
        "也不要把所有角色都写成同一套动作；但**同一倾向在不同场面的具体表现应该各不相同**，"
        "不能每次都是同一个动作。\n"
        "【**通道分散——这条决定描写会不会复读**】\n"
        "· 档案里的动作倾向是**多条通道**（手部 / 视线 / 呼吸 / 姿势 / 声线 / 脚步 / 物件 / 距离感），"
        "不是一条。写小动作时**轮换使用不同通道**，不要所有小动作都长在手上。\n"
        "· **同一段场景里，连续两次小动作不要用同一通道**——"
        "「他手指敲了下桌面，他拇指又蹭过笔杆」是复读；"
        "「他手指敲了下桌面，视线往门口扫了一眼」才是分散。\n"
        "· 一个角色**全章最多集中用 2-3 次同一通道**（手部尤其）。"
        "第 3 次出现同一类身体描写时，换成别的通道（用视线/呼吸/姿势/脚步/物件承担）。\n"
        "· **道具动作尤其要节制**：如果这个角色整场都在把玩同一个东西，"
        "读者只会记住道具、记不住情绪——那个动作最多出现 1-2 次，其余交给其他通道。\n"
        "用法：两句对白之间插入该角色符合其倾向的一个小动作（≤7 字），"
        "**这个动作要与其前后几次的动作不同通道**；"
        "情绪外化优先走他的动作倾向而不是通用生理反应；口头习惯少量点缀，不要每句都用。\n"
        "档案为空的角色：先按「目标-阻碍-选择-代价」写功能性的行为，不要硬套别人的动作库。"
    )
    if missing:
        head += f"\n【尚未配齐声音档案的角色】{'、'.join(missing)}（可在实体库编辑补全）"
    return Material(source_id="state:voiceprint", family=MaterialFamily.REQUIRED,
                    content=head + "\n\n" + "\n".join(lines),
                    title="角色声音档案（习惯动作/口癖/口音）")


def build_state_materials(project_store, entity_store: EntityStore,
                          desk: DeskStore, pid: str,
                          book_store=None, field_store=None,
                          chapter: int | None = None) -> list[Material]:
    """项目已写状态 → 三类材料（有什么带什么，没有的不硬造）。
    book_store/field_store 传 None 时不注入书卡（旧项目/通用项目兼容）。"""
    mats: list[Material] = []
    for m in (_plot_digest(desk), _draft_recent(project_store, desk, pid),
              _entity_ledger(entity_store),
              _voiceprint_material(entity_store, chapter)):
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


def _foreshadow_anchor(e) -> int:
    """伏笔的「静默起点」章号（spec 3）。

    账龄按 appearances 的最小值算有个致命漏洞：一条每章都被提到一句的伏笔，
    min(appearances) 永远是埋设那章，账龄永远 0，于是它永远排在到期榜最后——
    恰恰是最该死的那条。所以改成「最近一次真正推进」的章号；
    从没推进过的（last_advanced_ch=0）退回埋设章，保证它排在最前面。
    """
    if getattr(e, "last_advanced_ch", 0):
        return int(e.last_advanced_ch)
    return min(e.appearances) if e.appearances else 0


KNOWLEDGE_ATTRS = ("knows", "blinded_to")


def _knowledge_seg(e) -> str:
    """角色行尾追加知情边界（spec 3），如：
    「（已知：唐迟是第七个住客；不知情：猫眼里的东西）」

    为什么进任务书而不只做审稿检查：审稿是**事后**报警，写这一章时模型
    根本不知道这条边界；注入才是让模型在写的时候就守住边界。
    没有声明的角色不加这段——不自动推断「他应该知道什么」，
    凭空推断会把正确的信息也判成越界。
    """
    attrs = getattr(e, "state_attrs", None) or {}
    knows = str(attrs.get("knows") or "").strip()
    blind = str(attrs.get("blinded_to") or "").strip()
    if not knows and not blind:
        return ""
    parts: list[str] = []
    if knows:
        parts.append(f"已知：{_clean(knows, 60)}")
    if blind:
        parts.append(f"不知情：{_clean(blind, 60)}")
    return "（" + "；".join(parts) + "）"


def _memory_seg(e, ch_day: int | None, ch_no: int | None = None) -> str:
    """角色行尾追加记忆碎片（按天解锁），如：
    「（忆起：手指无名指上那道白痕；想起自己三天前在废染坊偷了胆珠）」

    **为什么贴在角色行尾而不做成一段集中列表**：集中列表模型会当成背景资料，
    贴在角色名后面它才会当成「这个角色现在记得什么」。
    和 _knowledge_seg 同一个位置、同一套渲染形态。

    闸门在 MemoryFragment.unlocked_at：已写出的（revealed_at>0）以实际章号为准，
    不回收；没写出的按计划解锁日。没配碎片的角色不加这段——不自动推断
    「他应该记得什么」，凭空推断会把作者设计的解锁节奏打乱。
    """
    return _render_memories(getattr(e, "memories", None) or [],
                            ch_day, ch_no)


def _render_memories(frags, ch_day: int | None, ch_no: int | None = None,
                     *, label: str = "忆起：") -> str:
    """把一组碎片渲染成「（忆起：…）」片段，按天闸门过滤。"""
    unlocked = [m for m in frags
                if getattr(m, "text", "") and m.unlocked_at(ch_day, ch_no)]
    if not unlocked:
        return ""
    # 同一天解锁的多条合并，最多列 4 条——再多是背景噪音不是记忆
    shown = unlocked[:4]
    parts = [m.render() for m in shown]
    seg = label + "；".join(parts)
    if len(unlocked) > len(shown):
        seg += f"（另有 {len(unlocked) - len(shown)} 条尚未想起）"
    return f"（{seg}）"


def _protagonist_name(entity_store: EntityStore) -> str:
    """主角在实体快照里的显示名。

    主角不在 entities 的常规链路里，但历史上会建一个同名 character 实体
    （唐迟）。这里取 protagonist.current_body_name，拿不到就退回「唐迟」，
    用于判断「哪一行是主角的行」——宿主的残忆要挂在那行上。
    """
    try:
        proto = getattr(entity_store, "protagonist", None)
        name = getattr(proto, "current_body_name", "") or ""
        return name or "唐迟"
    except Exception:
        return "唐迟"


#: 宿主碎片里的第一人称代词 → 第三人称残响。
#: 「我」作主语（句首、逗号后、动词后）都要换——只换句首会漏掉
#: 「黑胆珠被我贴身藏在内袋」这种宾语位置，读起来还是唐迟自述。
#: 「我们」指原主的同伙群体，对唐迟同样是陌生人的记忆，整体换人。
_HOST_PRONOUN_RE = re.compile(r"我(?!们)")


def _to_third_person(m) -> "object":
    """把一条第一人称的宿主碎片转成第三人称残响（不改原文，只改人称）。

    为什么必须转：碎片是按宿主视角写的（「我站在陶露的纸扎铺前…」），
    但读它的是唐迟——他不知道那是谁干的，只知道**这双手**做过什么。
    保留第一人称会让模型把这段写成唐迟的自述（他亲身去过纸扎铺），
    魂穿最重要的一层「这记忆不属于我」的错位感就没了。

    转换只做代词替换，不重写句子——重写会毁掉作者精心调过的文案。
    用「这具身体」而不是「他」：唐迟此刻并不知道这具身体是谁，
    他只知道这不是他的记忆。模型自己会把这个残响接到宿主身上。
    """
    text = getattr(m, "text", "") or ""
    if not text:
        return m
    # 物主形式先处理：「我的」→「这具身体的」，避免被后面的主语替换
    # 拆成「这具身体的身体」这类坏句子。
    shifted = text.replace("我的", "这具身体的").replace("我们", "他们")
    shifted = _HOST_PRONOUN_RE.sub("这具身体", shifted)
    # 修掉叠词：这具身体的身体 → 这具身体
    shifted = shifted.replace("这具身体的身体", "这具身体")
    # 剔除混入的主角视角：模型偶尔会写「唐迟一碰到它，便能确认…」。
    # 这是宿主的记忆，宿主不可能知道自己叫唐迟——这句会让模型把整段
    # 当成唐迟的亲身经历，正好毁掉「记忆不属于我」这层。
    #
    # 按**整句**删而不是删半句：留下「便能确认这不是偶然捡来的饰物。」
    # 这种半截尾巴，句子悬空读不通，模型会自行脑补补全——那比留着
    # 一句主角视角更糟。所以凡是含「唐迟」的句子整句去掉。
    parts = [s for s in re.split(r"(?<=[。！？；])", shifted) if "唐迟" not in s]
    shifted = "".join(parts)
    try:
        return replace(m, text=shifted)
    except Exception:
        # dataclass 不可变或字段名不同 → 退化成一个轻量替身对象
        return SimpleNamespace(
            text=shifted,
            recovers_at=getattr(m, "recovers_at", 1),
            revealed_at=getattr(m, "revealed_at", 0),
            note=getattr(m, "note", ""),
            source=getattr(m, "source", ""),
            id=getattr(m, "id", ""),
            render=lambda: shifted + (f"（第{getattr(m, 'revealed_at', 0)}章已写出）"
                                      if getattr(m, "revealed_at", 0) else ""),
            unlocked_at=lambda ch_day, ch_no=None: m.unlocked_at(ch_day, ch_no),
        )


def _host_name(vol) -> str:
    """本卷的魂穿宿主名（空串 = 本卷没有宿主，如蓝星主线卷）。"""
    try:
        hc = getattr(vol, "host_character", None) or {}
        if isinstance(hc, dict):
            return str(hc.get("name") or "")
        return ""
    except Exception:
        return ""


def _host_memory_seg(entity_store: EntityStore, vol, ch_day: int | None,
                     ch_no: int | None = None) -> str:
    """宿主（唐迟魂穿进去的那具身体）的记忆 → 渲染成**主角自己的**模糊记忆。

    ## 为什么宿主记忆要挂到主角头上，而不是留在宿主的角色行

    魂穿设定里，唐迟穿进的是**别人的身体**。这具身体在被附身之前经历过的
    事，**不是陆骁的记忆，是唐迟现在能摸到的残响**——他会在后颈发凉时
    忽然想起「这双手好像在废染坊偷过什么东西」，但想不起完整的因果，
    只剩一个画面和一点生理性的恶心。

    之前把碎片挂在宿主实体的角色行上（「陆骁：（忆起：…）」），有两个问题：
    1. **视角错位**。正文是唐迟 POV，模型看到「陆骁忆起某事」会当成
       另一个人物的记忆，写出来是「陆骁当时……」的转述，不是唐迟脑中的
       碎片感——魂穿最重要的一层体验（记忆的陌生感、不属于我的感觉）
       整个丢掉了。
    2. **宿主常常不在场**。陆骁在第 3 章是「同名尸体」级别的存在，
       甚至角色行本身都可能被截断在快照前 20 名之外，
       于是「唐迟该想起的东西」跟着宿主一起消失。

    所以这里从卷的 host_character 找到宿主实体，把它的碎片取出来，
    渲染成主角行尾的「忆起」。碎片还是那份碎片（同一套按天闸门），
    只是**视角归位**到唐迟。

    ## 为什么保留宿主行上的碎片

    宿主行保留自己的碎片，因为还有另一种合法用法：作者想写
    「陆骁本人想起什么」（比如他在别的章里短暂夺回意识）。
    两种视角各有其用，不互相替代。
    """
    if entity_store is None or vol is None:
        return ""
    try:
        hc = getattr(vol, "host_character", None) or {}
        host_name = hc.get("name") if isinstance(hc, dict) else ""
        if not host_name:
            return ""
        host = entity_store.find("character", host_name)
        if host is None:
            return ""
        frags = getattr(host, "memories", None) or []
        if not frags:
            return ""
        # 视角归位：碎片是按宿主的第一人称写的（「我站在陶露的纸扎铺前…」），
        # 但读它的是唐迟——他不知道那是谁干的，只知道这双手做过什么。
        # 保留第一人称会诱导模型写成唐迟的自述（「我站在纸扎铺前…」），
        # 那就变成了唐迟亲身经历，与「陌生身体的残响」正好相反。
        # 所以转成第三人称：主角看到的是画面，不是别人的自述。
        shifted = [_to_third_person(m) for m in frags]
        seg = _render_memories(shifted, ch_day, ch_no, label="残忆：")
        if not seg:
            return ""
        return seg
    except Exception:
        return ""


# ============================================================
# 原剧本注入材料（origin material）
# ============================================================

def build_origin_material(volume, ch_day: int | None = None) -> Material | None:
    """原剧本（唐迟魂穿之前的完整故事）→ REQUIRED 材料。

    数据源是 Volume.origin_novel（归一化后的原剧本，见 book/volume.py
    build_origin_novel）。为什么必须有这一层：桌台原本读的是
    `simulated_run.get("runs")`，而真实数据里没有这个键——副本章的竞技场
    材料里从来没有任何原剧本内容，写正文时模型完全不知道唐迟穿进来之前
    发生过什么。

    装配四块：
      ■ 小说化梗概（全文，7 幕骨架）
      ■ 通稿原文节选（头 + 尾，头尾是唐迟最该预先知道的两块）
      ■ 原剧本逐日推进（本章日 ± 1 天，含未来 2 天作方向）
      ■ 使用纪律（今天正在发生什么 / 后两天只能当预感不能写出来）

    没有原剧本的卷返回 None——蓝星章、间章，以及还没跑团的卷。
    """
    if volume is None:
        return None
    try:
        from app.book.volume import origin_novel_of
        on = origin_novel_of(volume)
    except Exception:
        return None
    if not on or not (on.get("full_text") or on.get("summary") or on.get("days")):
        return None

    vno = getattr(volume, "volume_no", "?")
    parts: list[str] = ["【原剧本·唐迟魂穿之前的完整故事】"]

    one_liner = str(on.get("one_liner") or "").strip()
    if one_liner:
        parts.append(f"一句话：{one_liner}")

    summary = str(on.get("summary") or "").strip()
    if summary:
        parts.append(f"■ 小说化梗概（原剧本的骨架，唐迟已经历过一遍）\n{summary}")

    full_text = str(on.get("full_text") or "").strip()
    if full_text:
        head_n, tail_n = ORIGIN_NARRATIVE_HEAD, ORIGIN_NARRATIVE_TAIL
        if head_n > 0 and len(full_text) > head_n + tail_n:
            seg = (full_text[:head_n].rstrip()
                   + "\n\n……（原剧本中段已略，机制细节见上方逐日推进）……\n\n"
                   + full_text[-tail_n:].lstrip())
        else:
            seg = full_text
        parts.append(
            f"■ 通稿原文节选（{len(full_text)} 字全文取头尾两段）\n{seg}"
        )

    days = on.get("days") or []
    if isinstance(days, list) and days:
        w = ORIGIN_DAY_WINDOW
        lo = (ch_day - w) if ch_day else None
        hi = (ch_day + w) if ch_day else None
        lines: list[str] = []
        for row in days:
            if not isinstance(row, dict):
                continue
            try:
                d = int(row.get("day") or 0)
            except (TypeError, ValueError):
                continue
            if lo is not None and not (lo <= d <= hi):
                continue
            text = str(row.get("summary") or "").strip()
            if not text:
                continue
            if ch_day and d == ch_day:
                lines.append(f"- 第{d}天（← 本章对应日）：{text}")
            elif ch_day and d > ch_day:
                lines.append(f"- 第{d}天（原剧本之后会发生）：{text}")
            else:
                lines.append(f"- 第{d}天：{text}")
        if lines:
            label = f"第{ch_day}天 ± {w} 天" if ch_day else "全部"
            parts.append(f"■ 原剧本逐日推进（{label}）\n" + "\n".join(lines))

    # 使用纪律：光给「后两天会发生什么」不够，模型会直接把后两天写进本章。
    discipline: list[str] = [
        "■ 怎么用这份材料（这是唐迟的先知感来源，不是本章的剧情提纲）",
    ]
    if ch_day:
        discipline.append(
            f"原剧本里【第 {ch_day} 天】正在发生的事，是本章的时空背景——"
            "唐迟是带着这些记忆进来的，所以他会知道一些别人不知道的事、"
            "会去追一些别人看不懂的线索。")
        if hi and hi > ch_day:
            discipline.append(
                f"原剧本里【第 {ch_day + 1} ~ {hi} 天】会发生的事，只能作为唐迟的"
                "预感/计划/被误导的来源：**绝不能在本章把它们原样写出来**"
                "（提前泄底会让后面几章作废）。要写也只能写成他隐约预感、打算阻止、"
                "或者被人误导往反方向走。")
    discipline.append(
        "记忆解锁是有节奏的：唐迟不是一开始就全知道。哪些记忆他已经想起、"
        "哪些还没想起，以任务书里角色行的「忆起：」为准。")
    parts.append("\n".join(discipline))

    return Material(
        source_id=f"volume:v{vno}:origin",
        family=MaterialFamily.REQUIRED,
        content="\n\n".join(parts),
        title=f"原剧本（第{vno}卷·魂穿之前的完整故事）",
    )


# ============================================================
# 跨章连贯材料（前 2 后 2）
# ============================================================

def _continuity_window_material(desk: DeskStore, volume, chapter: int,
                                 ch_day: int | None = None,
                                 outline_map: dict | None = None) -> Material | None:
    """前 N 章（已发生，接住）+ 后 N 章（即将发生，只当方向）。

    **为什么要有这一段**：原来只有两处「往后看」——
    previous_tail（上章结尾 500 字）和 _opening_commission（上章结尾 400 字），
    都是上一章的**文字**。缺两样：

    1. 前两章的**剧情全貌**：160 字的 plot 摘要撑不住「人还在哪、伤还在不在、
       谁已经知道了什么」这种状态接续。
    2. 后面的**方向**：模型完全不知道下一章要往哪走，容易把本章写成孤立单元，
       或者干脆提前把后面的爆点写掉。

    「后 N 章」优先取 rhythm_blueprint.chapter_plan（含起承转合四段与伏笔种子，
    实测覆盖第 3-20 章）；蓝星章/间章没有 blueprint，退化为取大纲层的后两章标题。
    数据全缺时返回 None，不编造占位。
    """
    before_parts: list[str] = []
    after_parts: list[str] = []

    # ---- 前 N 章：取实际写过的最近 N 章（跳章时自动往前顺延）----
    written_nos = [no for no in sorted(desk.all_nos(), reverse=True)
                   if no < chapter and _stage_text(desk.get(no), "plot")]
    picked_before = written_nos[:CONTINUITY_BEFORE]
    if picked_before:
        # 跳章提示：最老的那一章和它紧邻的前一章之间断了（缺 plot 或整章没写）。
        # 只看 chapter-1 是否写过是不够的——中间隔了 5 章也一样是跳章。
        oldest = min(picked_before)
        prev_no = oldest - 1
        prev_written = (prev_no >= 1
                        and _stage_text(desk.get(prev_no), "plot") != "")
        if not prev_written:
            gap_note = f"（第{prev_no}章尚未写，此处取的是往前顺延的实际写过的章）"
        else:
            gap_note = ""
        for no in reversed(picked_before):     # 升序：先发生的在前
            w = desk.get(no)
            plot = _clean(_stage_text(w, "plot"), CONTINUITY_PLOT_CHARS)
            title = f" {w.title}" if w and w.title else ""
            tail = ""
            for st in ("polish", "draft"):
                t = _stage_text(w, st)
                if t:
                    tail = f"（正文结尾：{truncate_tail_at_sentence(t, 200)}）"
                    break
            before_parts.append(f"- 第{no}章{title}"
                                f"{gap_note if no == oldest else ''}"
                                f"\n  {plot}{tail}")

    # ---- 后 N 章：优先 rhythm_blueprint（含起承转合 + 伏笔种子）----
    bp = getattr(volume, "rhythm_blueprint", None) if volume is not None else None
    plan = bp.get("chapter_plan") if isinstance(bp, dict) else None
    if isinstance(plan, list) and plan:
        for n in range(chapter + 1, chapter + CONTINUITY_AFTER + 1):
            spec = None
            for p in plan:
                if isinstance(p, dict):
                    try:
                        if int(p.get("chapter") or -1) == n:
                            spec = p
                            break
                    except (TypeError, ValueError):
                        continue
            if not spec:
                continue
            title = spec.get("title") or ""
            day = spec.get("day")
            beat = spec.get("beat_type") or ""
            focus = _clean(spec.get("focus") or "", 120)
            lines = [f"- 第{n}章《{title}》"
                     + (f"（Day {day}" + (f"｜{beat}" if beat else "") + "）" if day is not None else "") + ""]
            if focus:
                lines.append(f"  方向：{focus}")
            pb = spec.get("plot_beats") if isinstance(spec.get("plot_beats"), dict) else None
            if pb:
                for k, label in (("qi", "起"), ("cheng", "承"),
                                 ("zhuan", "转"), ("he", "合")):
                    val = str(pb.get(k) or "").strip()
                    if val:
                        lines.append(f"  {label}：{_clean(val, 200)}")
            seeds = spec.get("foreshadow_seeds") or []
            if isinstance(seeds, list) and seeds:
                joined = "；".join(str(x) for x in seeds[:4])
                lines.append(f"  必埋伏笔：{_clean(joined, 200)}")
            after_parts.append("\n".join(lines))
    elif isinstance(outline_map, dict):
        # 蓝星章/间章没有 blueprint，退化为大纲层的后两章标题（大纲层一定有）
        for n in range(chapter + 1, chapter + CONTINUITY_AFTER + 1):
            body = outline_map.get(n)
            if not body:
                continue
            head = body.strip().splitlines()[0][:40] if body.strip() else ""
            first_sentence = _first_sentence_of(body, 120)
            after_parts.append(f"- 第{n}章{('《' + head + '》') if head else ''}\n  {first_sentence}")

    if not before_parts and not after_parts:
        # 全书第 1 章、或计划范围之外且没有任何已写章节时返回 None。
        # 不硬塞「纪律」：没有前后文可接的时候，纪律本身无处落地，
        # 只会让开书第一章凭空多一段说明文字。
        # 注意卷末最后几章不属于这种情况——它有 before_parts（前面写过的章），
        # 只是没有 after_parts，那时纪律照样会跟在数据后面出现。
        return None

    parts: list[str] = []
    if before_parts:
        parts.append("■ 前 {} 章（已发生——本章必须接住，不能当它们没存在）".format(
            len(before_parts)) + "\n" + "\n".join(before_parts))
    if after_parts:
        parts.append("■ 后 {} 章（即将发生——只能当方向，不能提前写出来）".format(
            len(after_parts)) + "\n" + "\n".join(after_parts))
    parts.append(_SEAM_RULES)

    return Material(
        source_id=f"state:continuity-ch{chapter}",
        family=MaterialFamily.REQUIRED,
        content="\n\n".join(parts),
        title=f"跨章接缝（前{len(before_parts)}后{len(after_parts)}）",
    )


def _first_sentence_of(text: str, limit: int) -> str:
    """取正文首句（按中文句末标点断），用于大纲层的简要预览。"""
    s = _clean(text, limit * 2)
    for ch in ("。", "！", "？", "\n"):
        idx = s.find(ch)
        if 0 <= idx < limit:
            return s[: idx + 1].strip()
    return s[:limit].strip()

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
                            volume_store=None, current_volume=None,
                            project_id: str = "") -> Material | None:
    """开章任务书：写第 N 章前的 REQUIRED 自检材料，七段结构。

    ①开篇委托（上一章结尾，开篇接住变化）②本章故事（进度与跳章提醒）
    ③本书本场（书铁律+双轨时间线节拍+讣告卡——book/field 有传才注入）
    ④本章人物与设定（截至上一章的实体快照，含已退场/未出场标注 + 角色知情边界）
    ⑤怎么写更顺（节奏位置 + 三线近况 + 到期伏笔按静默章数排急缓 + 趋势红线）
    ⑥收在哪里（章末收束要求）。
    另：⑦趋势红线由「怎么写更顺」段末尾承载（跨章质量退化提醒，spec 1）。
    六段全空（开书第一章且无设定）返回 None。
    book_store/field_store 传 None 时退化为五段通用任务书（旧项目兼容）。
    project_id 传空字符串时不做趋势判定（无项目上下文）。
    """
    sections: list[str] = []

    # 排除本章：写完本章剧情后重跑同阶段，任务书不变才能命中幂等收据
    written = [no for no in _written_nos(desk) if no != chapter]
    prior_written = [no for no in written if no < chapter]

    # ① 开篇委托
    opening = _opening_commission(desk, chapter)
    if opening is not None:
        sections.append(opening)

    # ①-b 跨章接缝窗口（前 2 后 2）。放在开篇委托之后：本章要接住的东西
    # 先给上一章的原文结尾（微观语气），再给前两章的剧情全貌（宏观状态），
    # 顺序反过来模型会先读大段规划再读结尾语气，接缝感反而弱。
    #
    # vol 在下面才解析，这里先算一次 ch_day（③-b 段落里还会再校正一次
    # brief.story_day_override，那次是最终权威）。
    _w0 = desk.get(chapter)
    _ch_day_early = getattr(_w0, "day", None) if _w0 is not None else None
    _br0 = getattr(_w0, "brief", None) if _w0 is not None else None
    if isinstance(_br0, dict):
        _ov0 = _br0.get("story_day_override")
        _sel0 = _br0.get("selected") if isinstance(_br0.get("selected"), dict) else None
        if isinstance(_ov0, int) and 0 <= _ov0 <= 7 and _ov0 > 0:
            _ch_day_early = _ov0
        elif _ch_day_early is None and isinstance(_sel0, dict) and _sel0.get("day"):
            _ch_day_early = _sel0.get("day")

    # vol 的解析提前到这里，好让 continuity 能用到 blueprint
    vol = None
    if current_volume is not None:
        vol = current_volume
    elif volume_store is not None:
        try:
            vol_no = None
            if _w0 is not None and getattr(_w0, "field_no", None):
                vol_no = int(_w0.field_no)
            if vol_no is None:
                vols = volume_store.all_list() if hasattr(volume_store, "all_list") else []
                dungeon_vols = [v for v in vols if getattr(v, "volume_kind", "dungeon") == "dungeon"]
                if dungeon_vols:
                    vol_no = max(dungeon_vols, key=lambda v: v.volume_no).volume_no
            if vol_no is not None and hasattr(volume_store, "get"):
                vol = volume_store.get(vol_no)
        except Exception:
            vol = None

    try:
        _cont = _continuity_window_material(desk, vol, chapter, _ch_day_early)
        if _cont is not None:
            sections.append(
                "【跨章接缝·前 2 后 2】（本章不是孤立的一章：前面已经发生了什么、"
                "后面要往哪走、这三条接缝纪律——写之前必须先对齐）\n"
                + _cont.content
            )
    except Exception as e:
        logger.debug("continuity window skipped: %s", e)

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
    # ch_day 这里算最终权威值（含 brief.story_day_override）；
    # vol 已在 ①-b 解析过，这里只在它还是 None 时才重新解析（避免重复劳动，
    # 也避免两次解析给出不同结果——那会让 continuity 和 volume 段对不上卷）。
    ch_day = _ch_day_early
    w = _w0
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
    if vol is None:
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

    # ③-c 原剧本（唐迟魂穿之前的完整故事）。和 ③-b 同一批注入而不是另开材料：
    # 两者都是「唐迟此刻面对的世界真相」，拆开会让模型当成两份参考资料互相印证，
    # 而它们本是一件事的两面（原剧本的走向 = 唐迟的先知感来源）。
    if vol is not None:
        try:
            origin_m = build_origin_material(vol, ch_day)
            if origin_m is not None:
                sections.append(
                    "【原剧本·唐迟魂穿之前】"
                    "（这是他穿进来之前这个世界已经发生过的事——他带着这些记忆，"
                    "所以他知道别人不知道的事、会去追别人看不懂的线索。"
                    "但记住：解锁是有节奏的，他不是一开始就全知道）\n"
                    + origin_m.content
                )
        except Exception as _e:
            logger.debug("origin material skipped: %s", _e)

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
            # 知情边界（spec 3）：写到这个角色时他不能凭空知道 blinded_to 里的事。
            # 只带显式声明过的，空值不写——不自动推断「他应该知道什么」。
            if etype == "character":
                seg += _knowledge_seg(e)
                # 记忆碎片：按本章所在的 Day 闸门解锁，与知情边界并排挂在角色行尾。
                #
                # 宿主例外：宿主（唐迟魂穿进去的那具身体）的碎片不挂它自己的行，
                # 而是挂到主角行（见下）。同一批碎片在两行都出现会让模型分不清
                # 「这是陆骁的记忆」还是「这是唐迟的记忆」——恰好是这里最不能含糊的
                # 一点。宿主碎片仍留在实体里（作者可在实体编辑器看到和调整）。
                if e.name != _host_name(vol):
                    seg += _memory_seg(e, ch_day, chapter)
                if e.name == _protagonist_name(entity_store):
                    # 主角自己的记忆（唐迟前世的/蓝星的）——存 protagonist，
                    # 不在 entities 里，之前**从来没有注入过**。
                    seg += _render_memories(
                        (getattr(getattr(entity_store, "protagonist", None),
                                 "memories", None) or []),
                        ch_day, chapter, label="忆起：")
                    # 魂穿宿主的记忆 → 挂到主角唐迟头上（视角归位）。
                    # 写在唐迟行里，模型才会写成「他忽然想起…」，而不是
                    # 转述「陆骁当时…」。
                    seg += _host_memory_seg(entity_store, vol, ch_day, chapter)
            lines.append(f"- {seg}")
        if len(ents) > CHECKLIST_SECTION_LIMITS[etype]:
            lines.append(f"（其余 {len(ents) - len(shown)} 个略）")
        sections.append(f"【{TYPE_LABELS[etype]}快照】（截至上一章的既有设定，不得重设或矛盾）\n"
                        + "\n".join(lines))

    # ④-b 宿主的残忆：挂不到主角行时的兜底。
    #
    # ④ 那一段是把宿主的碎片挂在「主角那一行」上。但主角实体可能不在
    # 角色快照里（没建同名实体、或被前 20 名的截断挤掉了）——那
    # 「唐迟该想起的东西」就跟着消失了，而唐迟恰恰是每一章都在场的。
    # 所以这里单独兜底：主角行没接住就自己开一段，绝不因为行不在就丢内容。
    if vol is not None:
        hero_name = _protagonist_name(entity_store)
        hero_line_has = any(
            sec.startswith("【角色快照】") and
            any(ln.startswith(f"- {hero_name}") and "残忆：" in ln
                for ln in sec.splitlines())
            for sec in sections)
        if not hero_line_has:
            host_seg = _host_memory_seg(entity_store, vol, ch_day, chapter)
            if host_seg:
                sections.append(
                    f"【{hero_name}此刻能摸到的残忆】（魂穿进的这具身体在被附身之前"
                    "经历过的事——不是这具身体主人的记忆，是主角自己脑中的碎片："
                    "只有画面和生理性的不适，没有完整因果。写成他忽然想起的片段，"
                    "不要转述成「某人当时……」）\n"
                    f"- {hero_name}{host_seg}"
                )

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
        # spec 3：账龄按「最近一次真正推进」算，不是按首次出现算。
        # 一条每章都被提到一句但从未推进的伏笔，账龄 0 → 排最前 → 本章被催，
        # 这正是我们要的行为；旧排序会让它永远沉底。
        anchored = [(e, _foreshadow_anchor(e)) for e in planted]
        anchored.sort(key=lambda t: (t[1], t[0].name))
        due_n = sum(1 for _, anchor in anchored
                    if chapter - anchor >= FORESHADOW_DUE_AFTER)
        hard_n = sum(1 for _, anchor in anchored
                     if chapter - anchor >= FORESHADOW_HARD_AFTER)
        head = (f"【到期伏笔】（未回收共 {len(planted)} 条，按「距上次推进的章数」排序——"
                f"静默 ≥{FORESHADOW_DUE_AFTER} 章的 {due_n} 条是本章硬债务，"
                "必须安排推进或兑现其一")
        if hard_n:
            head += f"；其中静默 ≥{FORESHADOW_HARD_AFTER} 章的 {hard_n} 条已拖到极限，最静的优先处理"
        head += "；「从未推进」的排在最前——被反复提起却不往前推，等于白埋）"
        lines = []
        for e, anchor in anchored[:CHECKLIST_FORESHADOW_LISTED]:
            age = chapter - anchor
            seg = e.name
            if e.summary:
                seg += f"：{_clean(e.summary, _SUMMARY_CHARS)}"
            seg += f"（第{min(e.appearances)}章埋，已静默 {age} 章"
            if e.last_advanced_ch:
                seg += f"，上次推进在第{e.last_advanced_ch}章（累计 {e.advance_count} 次）"
            else:
                seg += "，从未推进"
            if age >= FORESHADOW_DUE_AFTER:
                seg += "，本章必须处理"
            seg += "）"
            lines.append(f"- {seg}")
        if len(planted) > CHECKLIST_FORESHADOW_LISTED:
            lines.append(f"（其余 {len(planted) - CHECKLIST_FORESHADOW_LISTED} 条略）")
        smooth_parts.append(head + "\n" + "\n".join(lines))

    # 趋势红线（spec 1）：跨章质量退化提醒，闭环的最后一环。
    # 账本只是记录，**注入才是闭环**——让退化在下一章被看见。
    # 三条防误伤约束见 ledger.py 模块头：样本不足不判定、只报单调退化、
    # 注入的是提醒不是 P0（绝不进 hard_gate）。
    if project_id:
        try:
            from ..generation.ledger import compute_trends, render_trend_block
            trend_txt = render_trend_block(
                compute_trends(project_id, upto_chapter=chapter))
            if trend_txt:
                smooth_parts.append(trend_txt)
        except Exception as e:
            logger.debug("trend block skipped: %s", e)

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
