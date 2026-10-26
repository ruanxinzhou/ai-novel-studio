"""按卷的原剧本 + 实体档案生成角色记忆碎片（AI 补全 + 作者确认）。

## 为什么需要这一层

唐迟是**魂穿**进一具陌生的身体的。写作时模型必须知道两件事：

1. 这具身体在被附身之前经历过什么（`host_character` 的身体状态/原剧本位置/
   第七日死状/隐藏死因…，以及原剧本故事里这人做过什么）；
2. 这些记忆**唐迟现在想起了几个**——第 1 章他不该知道第 7 天才会发生的事。

项目里原本没有承载这件事的字段：`details` 能放长文本但实体快照不注入它，
`state_attrs` 注入了但只读 `knows` / `blinded_to` 两个键。所以记忆无处安放。

这里做一件事：**把角色档案 + 本卷原剧本交给模型，让它推导出 5-8 条记忆碎片，
每条标一个「入戏第几天想起」。**

## 三条铁律

1. **只从已有档案和原剧本推导，不臆造设定**。
   模型看到的输入就是这个角色已有的全部描述 + 原剧本梗概/通稿节选，
   输出必须能追溯到输入里的某一条，不能凭空加「他小时候养过一条狗」这种
   输入里没有的信息——那是编设定，不是补档案。

2. **不覆盖作者内容**。生成的是**草稿**，走预览弹窗，作者逐条勾选再决定采用。
   端点本身不写库（只返回草稿），写入仍走既有的 PUT 接口。

3. **解锁日必须诚实**。`recovers_at` 大的碎片要指向原剧本后半段的底牌，
   小的指向开篇就成立的事实（身体状态、旧伤、人际关系）。
   把底牌排到第 1 天等于剧透——这是这个功能最危险的失败模式。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

log = logging.getLogger("entities.memory_gen")

# ============================================================
# 草稿校验与清洗
# ============================================================

#: 记忆碎片条数下限/上限（低于 3 条撑不起「逐渐想起」，高于 9 条注入太重）
MEMORY_MIN = 5
MEMORY_MAX = 8

#: 单条记忆长度区间（字）。太短没信息，太长会挤爆实体行的注入预算。
MEMORY_TEXT_MIN = 30
MEMORY_TEXT_MAX = 200

#: 解锁日范围（唐迟入戏第 1..7 天）
_DAY_MIN, _DAY_MAX = 1, 7

#: 模型爱加的编号/符号前缀
_ITEM_PREFIX_RE = re.compile(r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）]|[一二三四五六七八九十]+[、.])\s*")
_TAIL_NOTE_RE = re.compile(r"[（(][^）)]{8,}[）)]\s*$")


def _strip_noise(x: str) -> str:
    s = _ITEM_PREFIX_RE.sub("", str(x).strip()).strip()
    s = _TAIL_NOTE_RE.sub("", s).strip()
    return s


def _clamp_day(value, default: int = 1) -> int:
    try:
        day = int(value)
    except (TypeError, ValueError):
        return default
    return max(_DAY_MIN, min(_DAY_MAX, day))


def _clean_text(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    # 模型常在值里重复字段名（「记忆内容：…」），注入时会显得啰嗦
    for prefix in ("记忆内容：", "记忆：", "内容："):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    # 超长按句边界截，别拦腰砍断一个句子
    if len(s) > MEMORY_TEXT_MAX:
        cut = s[:MEMORY_TEXT_MAX]
        for ch in ("。", "！", "？", "；"):
            idx = cut.rfind(ch)
            if idx > MEMORY_TEXT_MAX // 2:
                return cut[: idx + 1].strip()
        return cut.strip()
    return s


def normalize_draft(raw: Any) -> list[dict]:
    """把 LLM 返回洗成规范的记忆碎片列表。

    容错点：
    - 模型可能吐 str（单个碎片）或 list[dict] 或 list[str]，统一成 list[dict]
    - recovers_at 缺失/越界 → 收敛到 1..7（越界说明模型没按 schema，兜底而不是丢弃）
    - 空 text / 过短 text → 丢掉（30 字以下的记忆没有信息量）
    - 按 text 去重（模型偶尔复述同一条）
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("memories") or raw.get("items") or raw.get("fragments") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            text, day, source = item, 1, ""
        elif isinstance(item, dict):
            text = _clean_text(item.get("text") or item.get("memory")
                               or item.get("content") or item.get("value"))
            day = _clamp_day(item.get("recovers_at") or item.get("day")
                             or item.get("unlock_day"), default=1)
            source = str(item.get("source") or "").strip()
        else:
            continue
        text = _clean_text(_strip_noise(text))
        if len(text) < MEMORY_TEXT_MIN:
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": text, "recovers_at": day, "source": source})

    # 按解锁日升序：预览弹窗里作者一眼能看到「先想起什么、后想起什么」
    out.sort(key=lambda m: m["recovers_at"])
    return out[:MEMORY_MAX]


def diff_against_current(draft: list[dict], current: list[dict] | None) -> dict:
    """对比草稿与现有碎片，标出「哪些会被改动」。

    前端用它决定默认勾选：作者手写过的记忆默认不勾，
    避免一次「生成」把精心调过的解锁日冲掉。
    """
    cur = current if isinstance(current, list) else []
    cur_keys = {re.sub(r"\s+", "", str(m.get("text") or "")) for m in cur if isinstance(m, dict)}
    new_items: list[str] = []
    same_items: list[str] = []
    for m in draft:
        key = re.sub(r"\s+", "", m.get("text") or "")
        (same_items if key in cur_keys else new_items).append(m.get("text") or "")
    return {
        "new": new_items,
        "kept": same_items,
        "current_count": len(cur),
    }


# ============================================================
# Prompt
# ============================================================

_GEN_SYSTEM = """你是一名小说角色塑造专家，正在为《第七日的亡者》补全角色的**记忆碎片**。

## 背景设定

主角唐迟会「魂穿」进一具陌生的身体——一个在原剧本里注定七天之后死掉的倒霉蛋。
他不是完整地继承这具身体的记忆，而是**随着剧情推进逐渐想起一些碎片**。

你要产出的是：**这具身体在被唐迟附身之前经历过什么，可以拆成哪些他日后
会陆续想起来的记忆碎片。**

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。

{
  "memories": [
    {
      "text": "……一条记忆，30~200 字，具体到一个场景/一段经历/一种身体感受……",
      "recovers_at": 3,
      "source": "……溯源：来自哪条档案或原剧本哪一幕……"
    }
  ]
}

## 关键约束（违反任何一条都算失败）

1. **一切必须能追溯到输入**。每条记忆都要能从角色档案或原剧本内容里推出来。
   输入里没有的信息不要编——不要写「他小时候养过一条狗」这种输入里没有的东西。
   那是编设定，不是补档案。

2. **必须是「记忆」，不是「设定」**。
   ✗「他是个私生子」——这是背景设定，唐迟醒来就该知道，没有戏剧价值
   ✓「手指下意识摸向左手无名指，那里有戴了多年戒指留下的白痕，但他想不起为什么」——这是记忆

2b. **用第三人称写，不要用「我」**。
   碎片会以「唐迟脑中的残响」或「某人本人记得的事」出现在正文里。
   写成第一人称会让模型把这段当成唐迟的亲身经历，魂穿最重要的
   「这记忆不属于我」的错位感就没了。
   ✗「我站在陶露的纸扎铺前，开口便说自己是来求购旧术资料的河道测绘员」
   ✓「这双手曾站在陶露的纸扎铺前，开口说的是来求购旧术资料的河道测绘员」

3. **要具体到能写成画面**。记忆碎片是正文里「唐迟忽然想起一段」的素材，
   所以必须是一个可以看见的场景、一种残留的身体感觉、一个他解释不了的动作。
   抽象概括（「他有一段不堪的过去」）没有用。

4. **解锁日必须诚实，这是最重要的一条**。
   - `recovers_at: 1` —— **醒来就能摸到的**：身体状态、旧伤、贴身物件、
     人际关系、还没愈合的伤。必须是「这具身体现在的状态」，不是「他去过哪」。
     ✗「陆骁在阮衡诊所接待桌前，看见过阮衡袖口下的暗红绳」——这是场景回放，
        醒来的人不可能同时想起某天在别人桌上看见的东西
     ✓「这双手的指缝里嵌着洗不净的黑色淤痕，贴身内袋里的黑胆珠持续发烫」
        ——这是身体现在就能感觉到的
   - `recovers_at: 2~3` —— 中段逐渐浮上来的：某段关系的破裂、做过的某个交易、
     得罪过谁、被谁盯上、去过某处留下的印象。
   - `recovers_at: 5~7` —— 压到最后的底牌：他做过的那件最致命的事、
     自己的真实死因、那个他一直不敢面对的真相。
   **把底牌排到第 1 天是本任务最严重的失败**——那会让唐迟一睁眼就看穿整个副本，
   先知感、悬疑张力、七日倒计时的全部意义都归零。

   **第 1 天尤其要写「身体状态」而不是「场景回放」**：
   唐迟醒来的第一件事是确认「我这是谁、这具身体怎么了」。
   碎片要支撑这个确认，所以第 1 天写淤痕、旧伤、贴身物件、发烫的珠子、
   还没散的酒气——而不是他昨天在谁桌上看见了什么。

5. **条数控制在 {min} 到 {max} 条**。宁缺毋滥，但少于 {min} 条撑不起「逐渐想起」的层次。

## 输出字段说明

- text：一条记忆，30~200 字。第一人称或第三人称都可以，但要让正文能自然化用。
- recovers_at：唐迟入戏第几天想起，1 到 7 的整数。
- source：这条记忆是从哪推出来的（用于作者核对，不必写进正文）。
"""


def build_messages(entity_name: str, entity: dict,
                   origin_novel: dict | None = None) -> list[dict]:
    """把角色档案 + 原剧本拍平成模型输入。"""
    e = entity if isinstance(entity, dict) else {}
    lines: list[str] = [f"角色名：{entity_name}"]
    if e.get("summary"):
        lines.append(f"简介：{e['summary']}")
    if e.get("details"):
        lines.append(f"档案原文：\n{e['details']}")
    if e.get("appearances"):
        ap = e["appearances"]
        lines.append(f"出场章节：{ap if isinstance(ap, str) else '、'.join(str(x) for x in ap)}")
    attrs = e.get("state_attrs") or {}
    if isinstance(attrs, dict) and attrs:
        attr_lines = "；".join(f"{k}={v}" for k, v in attrs.items() if v)
        if attr_lines:
            lines.append(f"状态属性：{attr_lines}")
    if e.get("memories"):
        old = [m.get("text") for m in e["memories"] if isinstance(m, dict)]
        if old:
            lines.append("已有记忆碎片（不要重复这些内容）：\n"
                         + "\n".join(f"- {t}" for t in old if t))

    # 原剧本：梗概全文 + 通稿节选。节选 6000 字够模型看清这个角色的处境了，
    # 再多只会挤占它的注意力，且通稿后半段的底牌本来也不该进第 1 章。
    on = origin_novel if isinstance(origin_novel, dict) else {}
    if on.get("one_liner"):
        lines.append(f"本卷原剧本一句话：{on['one_liner']}")
    if on.get("summary"):
        lines.append(f"本卷原剧本·小说化梗概（唐迟魂穿之前发生的事）：\n{on['summary']}")
    days = on.get("days") or []
    if isinstance(days, list) and days:
        day_lines = "\n".join(f"- 第{d.get('day')}天：{d.get('summary')}"
                              for d in days if isinstance(d, dict))
        if day_lines:
            lines.append(f"原剧本逐日推进：\n{day_lines}")
    full_text = str(on.get("full_text") or "")
    if full_text:
        lines.append(f"原剧本通稿（节选前 {min(6000, len(full_text))} 字）：\n{full_text[:6000]}")

    system = _GEN_SYSTEM.replace("{min}", str(MEMORY_MIN)).replace("{max}", str(MEMORY_MAX))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(lines) + (
            "\n\n请按 JSON schema 输出该角色的记忆碎片，严格遵守 5 条关键约束。"
        )},
    ]


# ============================================================
# 生成入口
# ============================================================

def generate_draft(
    provider: Any,
    entity_name: str,
    entity: dict,
    origin_novel: dict | None = None,
    *,
    regen: Callable[[], str] | None = None,
) -> dict:
    """生成记忆碎片草稿并返回规范化结果。

    provider 走 main.py 的 _chore_provider("voiceprint") 适配器（同一套模型配置）。
    regen 是重试时的备选调用（与 complete_structured 的用法一致）。

    失败时不抛异常阻断作者操作，返回 {"_error": "..."}，
    前端据此提示「生成失败，手动填写即可」。
    """
    from ..generation import complete_structured

    messages = build_messages(entity_name, entity, origin_novel)

    def _call() -> str:
        if regen is not None:
            return regen()
        return provider.chat(messages).text

    try:
        comp = complete_structured(_call, max_regenerations=2)
    except Exception as e:  # 生成失败不该让作者卡住——手动填也是路
        log.warning("记忆碎片生成失败（%s）：%s", entity_name, e)
        return {"_error": f"生成失败：{e}"}

    data = comp.data if isinstance(comp.data, dict) else {}
    if not data:
        return {"_error": "模型没有返回可用内容"}

    items = normalize_draft(data)
    if not items:
        return {"_error": "模型返回的内容全部无效"}
    return {"memories": items}