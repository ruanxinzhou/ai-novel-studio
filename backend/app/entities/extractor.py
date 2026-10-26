"""实体抽取（M3b）：LLM 从正文/剧情抽取五类实体并回灌实体库。

抽取走 chore 通道（个人成本优先）；结构化输出复用 complete_structured
（词法 JSON 修复 + 重试），修复失败抛错交上层提示重试，不静默丢弃。
抽取指令存 PromptStore "entities" 层，可在工坊编辑（合同区不可覆盖）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

ENTITY_EXTRACT_SYSTEM = """你是小说设定管理员，负责维护实体库。从给定文本中抽取实体，只输出一个 JSON 对象：
{"entities": [{"type": "character|location|item|plotline|foreshadow", "name": "实体名", "summary": "一句话描述", "details": "补充设定，可为空字符串"}]}
抽取规则：
1. character=有名字或固定称谓的角色；location=具体地点/场所/组织；item=有名字的关键道具/法宝/信物；plotline=正在推进的情节线或核心冲突；foreshadow=文中埋下或回收的伏笔；
2. 只抽文本中实际出现或明确指向的，不要臆造；无名泛指（如"路人""随从"）不抽；
3. summary 一句话说清它是什么；foreshadow 必须在 details 写明「已埋」还是「已回收」及所指内容；
4. 不要输出 JSON 以外的任何文字。"""


@dataclass
class ExtractionResult:
    created: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    attempts: int = 0
    state_changes: list[dict] = field(default_factory=list)  # 本章产生的状态变动

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)


def extract_entities(
    provider: Provider,
    store,                      # EntityStore
    prompt_store: PromptStore,
    text: str,
    chapter: int | None = None,
    model: str | None = None,
) -> ExtractionResult:
    """从一段文本抽取实体并合并入库；chapter 提供时记入出现章节。

    **主来源不是这里**——副本卷创作链路里（节奏蓝图 finalize / 方向卡 select）
    已经把本章实体落库了。所以这里的定位是「状态更新 + 补漏」：
    · 库里已有且文本声明了新状态（如 alive=False）→ 写 StateChange
    · 库里已有但状态没变 → 只补appearances，不产生变更记录
    · 库里没有 → 建档（这是前置落库漏掉的）
    """
    text = text.strip()
    if not text:
        raise ValueError("待抽取文本不能为空")
    instruction = prompt_store.effective("entities")
    messages = [
        {"role": "system", "content": ENTITY_EXTRACT_SYSTEM},
        {"role": "user", "content": f"{instruction}\n\n【待抽取文本】\n{text}"},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"抽取输出连续 {completion.attempts} 次无法解析为 JSON")
    raw = completion.data.get("entities") if isinstance(completion.data, dict) else None
    found = raw if isinstance(raw, list) else []
    merged = store.merge_extracted(found, chapter, text=text)
    return ExtractionResult(
        created=merged["created"], updated=merged["updated"],
        dropped=merged["dropped"], attempts=completion.attempts,
        state_changes=merged.get("state_changes") or [],
    )


# ---- 伏笔兑现问询（P1② 定稿回账：账上欠了什么，本章还了没有） ----

FORESHADOW_RECONCILE_SYSTEM = """你是小说伏笔账房【对账模式】。对照【未回收伏笔清单】逐条核对【本章正文】，判断每一条在本章的状态，只输出一个 JSON 对象：
{"paid": ["本章兑现的伏笔名", ...], "advanced": ["本章推进但未兑现的伏笔名", ...]}
判断规则：
1. paid=本章正文正面回收了该伏笔：谜底揭晓、伏笔物件发挥关键作用、该悬念被解决——作者顺手为之也算兑现；
2. advanced=本章再次提及或部分推进，但尚未到兑现；
3. 拿不准是否兑现时列 advanced，确实正面处理了就大胆列 paid——报错作者可改回，漏报账就烂了；
4. 名字必须与清单完全一致，不要自创、缩写或改写；
5. 两者都不是的条目直接略过；不要输出 JSON 以外的任何文字。"""


@dataclass
class ForeshadowReport:
    paid: list[str] = field(default_factory=list)
    advanced: list[str] = field(default_factory=list)
    attempts: int = 0
    warning: str = ""

    def as_dict(self) -> dict:
        return {"paid": self.paid, "advanced": self.advanced,
                "attempts": self.attempts, "warning": self.warning}


def reconcile_foreshadows(
    provider: Provider,
    store,                      # EntityStore
    prompt_store: PromptStore,
    text: str,
    chapter: int | None = None,
    model: str | None = None,
) -> ForeshadowReport | None:
    """定稿回账：对照账上未回收伏笔问询本章兑现情况（chore 通道）。

    账上没有 planted 伏笔时返回 None（没有债就不问，防注意力摊薄）。
    paid → 状态改 paid 并记本章出场；
    advanced → 记本章出场**并更新推进追踪**（spec 3：last_advanced_ch/advance_count）。

    为什么要分开记：appearance 只说明「出现过」，一条每章都被提到一句的
    伏笔 appearance 会一直增长，但账龄按 min(appearances) 算永远是「刚埋」，
    于是它永远排在到期榜最后——恰恰是最该死的那条。
    """
    planted = store.list(type="foreshadow", status="planted")
    if not planted:
        return None
    lines = []
    for e in planted:
        seg = e.name
        if e.summary:
            seg += f"：{e.summary}"
        if e.appearances:
            seg += f"（第{min(e.appearances)}章埋）"
        if e.last_advanced_ch:
            seg += f"，上次推进在第 {e.last_advanced_ch} 章（累计 {e.advance_count} 次）"
        else:
            seg += "，从未推进过"
        lines.append(f"- {seg}")
    instruction = prompt_store.effective("foreshadow")
    messages = [
        {"role": "system", "content": FORESHADOW_RECONCILE_SYSTEM},
        {"role": "user", "content": instruction
            + "\n\n【未回收伏笔清单】\n" + "\n".join(lines)
            + "\n\n【本章正文】\n" + text.strip()},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"伏笔问询输出连续 {completion.attempts} 次无法解析为 JSON")
    data = completion.data if isinstance(completion.data, dict) else {}

    def _names(key: str) -> set[str]:
        raw = data.get(key)
        return {str(n).strip() for n in raw if isinstance(n, str) and n.strip()} \
            if isinstance(raw, list) else set()

    paid, advanced = _names("paid"), _names("advanced")
    report = ForeshadowReport(attempts=completion.attempts)
    for e in planted:
        if e.name in paid:
            store.update(e.id, status="paid")
            if chapter is not None:
                store.record_appearance(e.id, chapter)
            report.paid.append(e.name)
        elif e.name in advanced:
            if chapter is not None:
                store.record_appearance(e.id, chapter)
            # spec 3：推进是正交维度——status 仍是 planted，只更新推进追踪。
            store.note_foreshadow_advance(e.id, chapter)
            report.advanced.append(e.name)

    # 反向提醒（乌贼规则）：埋坑密而零回收，账在烂掉——宁可慢，不可假
    paid_total = len(store.list(type="foreshadow", status="paid"))
    planted_total = len(store.list(type="foreshadow", status="planted"))
    ch = chapter or 0
    if paid_total == 0 and planted_total >= 10 and ch >= 10:
        report.warning = (f"伏笔过密：已埋 {planted_total} 个坑、零回收"
                          f"（写到第 {ch} 章）——挖坑容易填坑难，建议尽快安排首个兑现")
    return report


# ============================================================
# 新伏笔检出（定稿回账的另一半）
# ============================================================
#
# **为什么必须补这一半**：reconcile_foreshadows 只能对**账上已有的**
# 伏笔问询「还了没有」。但作者在正文里现挖的坑——「他注意到对方袖口
# 沾着同样的淤泥」——如果没登记进实体库，就**永远不会出现在任何清单里**，
# 于是永远不会被问、不会被催、也不会被回收。
#
# 后果是账目系统性漏记：正文埋了 50 个坑，账上只有作者当初手写的那几个。
# 实测本项目 67 条伏笔、18 章正文，绝大多数来自卷级详设预埋，
# 正文里现挖的那部分基本没进账。
#
# 所以定稿时要问模型两个问题，而不只是一个：
#   1. 账上这些，本章还了没有？（已有 reconcile_foreshadows）
#   2. 本章正文里现挖了哪些账上没有的坑？（本函数）

FORESHADOW_DETECT_SYSTEM = """你是小说伏笔账房【检出模式】。阅读【本章正文】，找出作者**在本章新埋下的悬念**——
即：只在这一章出现、但明显没有交代清楚/没有当场解决的线索。

只输出一个 JSON 对象：
{"planted": [{"name": "伏笔名", "summary": "一句话说明埋的是什么", "evidence": "正文里对应的那句话（原文摘抄）"}]}

判断规则：
1. 只报**悬念**，不报设定、人物介绍、已解答的问题、当章解决的事。
   ✗「周朔是县局刑侦队长」——这是设定，不是悬念
   ✗「唐迟在 Day3 拆穿了血供契」——这是当章解决的事
   ✓「对方袖口沾着和唐迟一样的黑淤泥，他没问」——这是埋坑
2. evidence 必须是正文里的**原文摘抄**（可以直接复制那一小段），
   用来让作者核对你不是在编。
3. name 要短、能当标签用（8 字以内优先），不要用整句话当名字。
4. 拿不准算不算悬念的，就**不要报**。宁可漏报——作者自己知道埋了什么，
   账上多一条错的比少一条更麻烦。
5. 最多报 5 条，不要为了凑数硬找。
6. 不要输出 JSON 以外的任何文字。"""


@dataclass
class ForeshadowDetect:
    planted: list[dict] = field(default_factory=list)
    attempts: int = 0
    warning: str = ""

    def as_dict(self) -> dict:
        return {"planted": self.planted, "attempts": self.attempts,
                "warning": self.warning}


def detect_new_foreshadows(
    provider: Provider,
    store,                      # EntityStore
    prompt_store: PromptStore,
    text: str,
    chapter: int | None = None,
    model: str | None = None,
) -> ForeshadowDetect:
    """检出本章正文里现挖的、账上没有的伏笔。

    **只报不写**：命中的条目直接落进实体库（status=planted），
    因为作者在正文里已经写下了，账上没它就是账实分离——和 state_attrs
    的处理原则一致（正文是既成事实，账本跟上）。
    """
    existing = {e.name.strip() for e in store.list(type="foreshadow")}
    # 独立提示词层：与对账（foreshadow）分开。共用一层的话，作者改写
    # 那条指令时会把「对照清单核销」的要求塞进检出，模型收到互相矛盾的
    # 两份合同，产出的是两边都不满足的东西。
    instruction = prompt_store.effective("foreshadow_detect")
    messages = [
        {"role": "system", "content": FORESHADOW_DETECT_SYSTEM},
        {"role": "user", "content": instruction + "\n\n【本章正文】\n" + text.strip()},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    try:
        completion = complete_structured(generate)
    except Exception as e:
        # 检出失败不阻断定稿——账目漏记的后果远小于「定稿定不下去」
        return ForeshadowDetect(warning=f"新伏笔检出失败：{str(e)[:200]}")
    if completion.data is None:
        return ForeshadowDetect(attempts=completion.attempts,
                                warning="新伏笔检出输出无法解析为 JSON")

    raw = completion.data.get("planted") if isinstance(completion.data, dict) else None
    report = ForeshadowDetect(attempts=completion.attempts)
    if not isinstance(raw, list):
        return report

    seen = set(existing)
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        summary = str(row.get("summary") or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        if evidence:
            summary = f"{summary}（正文：{evidence}）" if summary else f"正文：{evidence}"
        store.add("foreshadow", name, summary=summary,
                  source="auto_detected", chapter=chapter)
        report.planted.append({"name": name, "summary": summary,
                               "evidence": evidence})
    return report
