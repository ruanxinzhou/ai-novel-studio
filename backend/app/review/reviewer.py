"""逐项目标审稿（M2，清单源自 Chinese-WebNovel-Skill consistency_review 模块）。

九项一致性逐项给出 稳/不稳 + 问题；任意两项不稳 → blocked（不交稿）。
第七项「跨章联结」对照上一章结尾审查欲望接力；无前章材料（第一章/无上下文）判稳。
第八项「信息揭露节奏」审查 NPC 对白是否出现 info dump。
第九项「克苏鲁白描」：空泛克苏鲁套话（不可名状/毛骨悚然/令人窒息/san值狂掉…）
        必须替换为温度/气味/解剖学局部错位/光影延迟/程序员量化观察等白描细节。
        ——该项由词法预扫描命中后作为"强制不稳"写入结果，同时要求 LLM 在上下文
        里也独立判断；词法命中列表会直接注入审稿上下文作为定点修复清单。
结构化输出走 complete_structured + 词法修复 JSON；审稿指令存 PromptStore
"review" 层，可在工坊编辑。

审稿是独立任务类型：不使用写作 SYSTEM_CONTRACT，使用 REVIEW_SYSTEM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.generation import complete_structured
from app.pipeline.prompts import PromptStore
from app.providers.base import Provider

# ---- 克苏鲁空泛套话词法白名单锁（白描锁） ----
# 命中任一 → 第九项「克苏鲁白描」强制判不稳，要求定点替换为感官白描。
_CLICHE_HORROR_PAT = re.compile(
    r"(不可名状|毛骨悚然|令人窒息的恐惧|倒吸一口凉气|莫名的寒意|恐怖的威压|san值狂掉|诡异的气息|不寒而栗|脊背发凉)",
    re.IGNORECASE,
)
# 每个命中取前后 18 字作为上下文片段，便于定点定位。
_CLICHÉ_CONTEXT_RADIUS = 18


def find_cliche_horror_hits(prose: str) -> list[dict]:
    """扫描正文中空泛克苏鲁套话，返回 [{word, context, index}]。

    空列表表示通过。该项与 LLM 第九项审查并列——词法命中是硬证据，不可被模型放水。
    """
    if not isinstance(prose, str) or not prose:
        return []
    hits: list[dict] = []
    for m in _CLICHE_HORROR_PAT.finditer(prose):
        word = m.group(0)
        start = max(0, m.start() - _CLICHÉ_CONTEXT_RADIUS)
        end = min(len(prose), m.end() + _CLICHÉ_CONTEXT_RADIUS)
        ctx = prose[start:end].replace("\n", " ").strip()
        hits.append({"word": word, "context": f"…{ctx}…", "index": m.start()})
    return hits


_REPLACEMENT_GUIDE = (
    "请将命中的空泛套话定点替换为以下五种白描之一：\n"
    "  1) 温度/触觉（海水的咸湿贴着后颈、指节冰凉得像刚摸过停尸柜）；\n"
    "  2) 气味（铁锈+烂海带+消毒水混合的甜味）；\n"
    "  3) 解剖学局部错位（指缝长出半透明藤壶、视野延迟 0.3 秒、下颌关节在嘴里多了一颗）；\n"
    "  4) 光影/声学延迟（脚步声比影子晚半拍、灯管按心跳频率闪烁）；\n"
    "  5) 程序员量化观察（响应时间 2.3 秒、错误码 0x5E、死锁条件成立、边界值溢出）。\n"
    "严禁使用「不可名状/毛骨悚然/san值狂掉」等抽象情绪形容词来渲染恐惧。"
)


REVIEW_SYSTEM = """你是中文网文交稿前的终审编辑。你只做一致性审查，不改写正文。
对给定章节，按九项清单逐项审查，每项给出"稳"或"不稳"，不稳时写明问题与证据句。
只输出一个 JSON 对象，格式：
{"items": [{"name": "剧情逻辑", "status": "稳", "problem": ""}, ...共九项],
 "summary": "一句话总评"}
九项 name 依次为：剧情逻辑、人物目标、情绪关系、身体信息、场景转场、章末承接、跨章联结、信息揭露节奏、克苏鲁白描。
跨章联结的审法：对照【上一章结尾】——本章开篇是否接住其变化、主角欲望是否比之前更具体或更急、章末是否留有下一章去向。
若未提供【上一章结尾】（如第一章），该项判"稳"并在 problem 里注明"无上一章可比"。
信息揭露节奏：NPC 对白是否出现大段世界观/真相倾倒（info dump），单段对白超过 150 字且连续解释设定视为不稳。
克苏鲁白描：恐怖渲染不得使用"不可名状/毛骨悚然/令人窒息的恐惧/诡异的气息/莫名的寒意/恐怖的威压/san值狂掉/脊背发凉/不寒而栗/倒吸一口凉气"等空泛情绪套话；
  必须用温度/气味/解剖学局部错位/光影延迟/程序员量化观察等具体白描替代。如果【白描锁命中清单】非空，该项必须判"不稳"，并逐条指出要替换的句子。
不要输出 JSON 以外的任何文字。"""

ITEM_NAMES = ["剧情逻辑", "人物目标", "情绪关系", "身体信息", "场景转场",
              "章末承接", "跨章联结", "信息揭露节奏", "克苏鲁白描"]
BLOCK_THRESHOLD = 2  # 任意两项不稳 → 不交稿


@dataclass(frozen=True)
class ReviewItem:
    name: str
    status: str          # 稳 | 不稳
    problem: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "problem": self.problem}


@dataclass(frozen=True)
class ChapterReview:
    items: list[ReviewItem]
    verdict: str                  # pass | blocked
    summary: str = ""
    attempts: int = 0
    raw: dict = field(default_factory=dict)
    cliche_hits: list[dict] = field(default_factory=list)

    @property
    def unstable_items(self) -> list[ReviewItem]:
        return [i for i in self.items if i.status == "不稳"]

    def as_dict(self) -> dict:
        return {
            "items": [i.as_dict() for i in self.items],
            "verdict": self.verdict,
            "summary": self.summary,
            "unstable_count": len(self.unstable_items),
            "attempts": self.attempts,
            "cliche_hits": list(self.cliche_hits),
        }


def _normalize(data: object) -> list[ReviewItem] | None:
    """模型输出 → 九项 ReviewItem；缺项/坏名补不稳，防静默放水。"""
    if not isinstance(data, dict):
        return None
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return None
    by_name: dict[str, dict] = {}
    for it in raw_items:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            by_name[it["name"].strip()] = it
    items: list[ReviewItem] = []
    for name in ITEM_NAMES:
        it = by_name.get(name)
        if it is None:
            items.append(ReviewItem(name=name, status="不稳", problem="模型未输出该项，视为未通过"))
            continue
        status = "不稳" if it.get("status") != "稳" else "稳"
        problem = it.get("problem") if isinstance(it.get("problem"), str) else ""
        if status == "不稳" and not problem:
            problem = "未说明问题"
        items.append(ReviewItem(name=name, status=status, problem=problem))
    return items


def _merge_cliche_verdict(items: list[ReviewItem], hits: list[dict]) -> list[ReviewItem]:
    """词法白描锁命中 → 强制覆写"克苏鲁白描"为不稳，附命中清单。

    不论 LLM 怎么回，词法命中都是硬证据；但如果 LLM 已经给出更具体的修复建议，
    我们保留它并把命中词清单拼到 problem 前面，方便前端定点高亮。
    """
    if not hits:
        return items
    merged: list[ReviewItem] = []
    hit_lines = "；".join(f"「{h['word']}」@ {h['context']}" for h in hits[:6])
    forced_problem = (
        f"白描锁命中 {len(hits)} 处空泛克苏鲁套话：{hit_lines}。"
        f"{_REPLACEMENT_GUIDE}"
    )
    for it in items:
        if it.name != "克苏鲁白描":
            merged.append(it)
            continue
        # 如果 LLM 已经判不稳且给出了 problem，拼在后面；否则直接用硬证据
        if it.status == "不稳" and it.problem and it.problem != "未说明问题":
            problem = forced_problem + "\n（模型补充：" + it.problem + "）"
        else:
            problem = forced_problem
        merged.append(ReviewItem(name=it.name, status="不稳", problem=problem))
    return merged


def review_chapter(
    provider: Provider,
    store: PromptStore,
    outline: str,
    prose: str,
    model: str | None = None,
    prev_tail: str = "",
) -> ChapterReview:
    """审一章：outline 为本章大纲/剧情（x），prose 为正文。
    prev_tail 为上一章结尾（滚动锚点），给定时作为第七项「跨章联结」的对照材料。

    词法白描锁（find_cliche_horror_hits）优先于 LLM：命中即第九项不稳，
    并将命中清单注入审稿上下文，强制 LLM 按定点清单给出修复建议。
    """
    cliche_hits = find_cliche_horror_hits(prose or "")
    instruction = store.effective("review")
    parts = [instruction]
    if prev_tail.strip():
        parts.append(f"【上一章结尾】\n{prev_tail.strip()}")
    parts.append(f"【本章大纲】\n{outline or '（无大纲，按上下文自洽审查）'}")
    if cliche_hits:
        hit_list = "\n".join(
            f"  - 「{h['word']}」：{h['context']}" for h in cliche_hits
        )
        parts.append(
            "【白描锁命中清单（强制不稳项，第九项必须按此清单逐条指出修复方向）】\n"
            f"共命中 {len(cliche_hits)} 处空泛克苏鲁套话：\n{hit_list}\n\n"
            f"{_REPLACEMENT_GUIDE}"
        )
    else:
        parts.append(
            "【白描锁命中清单】\n词法扫描未命中空泛套话黑名单；"
            "但第九项「克苏鲁白描」仍需你独立判断是否存在其他抽象恐怖形容词，若有请判不稳并指出替换方向。"
        )
    parts.append(f"【待审正文】\n{prose}")
    user = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": user},
    ]

    def generate() -> str:
        return provider.chat(messages, model).text

    completion = complete_structured(generate)
    if completion.data is None:
        raise ValueError(f"审稿输出连续 {completion.attempts} 次无法解析为 JSON")
    items = _normalize(completion.data)
    if items is None:
        raise ValueError("审稿 JSON 缺少 items 数组")
    # 词法白描锁强制覆写第九项
    items = _merge_cliche_verdict(items, cliche_hits)
    summary = ""
    if isinstance(completion.data, dict) and isinstance(completion.data.get("summary"), str):
        summary = completion.data["summary"]
    unstable = sum(1 for i in items if i.status == "不稳")
    return ChapterReview(
        items=items,
        verdict="blocked" if unstable >= BLOCK_THRESHOLD else "pass",
        summary=summary,
        attempts=completion.attempts,
        raw=completion.data if isinstance(completion.data, dict) else {},
        cliche_hits=cliche_hits,
    )
