"""按实体档案生成角色声音档案（AI 补全 + 作者确认）。

## 为什么需要这一层

角色声音档案（习惯动作/口癖/口音/语气）是**写活角色的前提**，
但手写 8 个字段的成本很高，尤其是配角——出场两三章就没了，
作者不会为一个路人停下来想「他说话用什么词」。

结果是实体库里一片「待补：动作缺 3 个、缺口癖」，
这些角色到了正文里就只能千人一面。

所以这里做一件事：**把实体库里已有的档案（身份/年龄/外貌/性格/动机/秘密/
第七日死状/信息差…）交给模型，让它据此推导出这个角色的说话方式。**

## 三条铁律

1. **只从已有档案推导，不臆造设定**。
   模型看到的输入就是这个角色在实体库里的全部描述；
   输出必须能追溯到输入里的某一条，不能凭空加「他喜欢喝红茶」这种
   输入里没有的信息——那是编设定，不是补档案。

2. **不覆盖作者内容**。生成的是**草稿**，走预览弹窗，作者逐字段看过再决定采用。
   端点本身不写库（只返回草稿），写入仍走既有的 PUT /voiceprints/{eid}。

3. **声音要与身份一致**。刑警不能说话文绉绉，毒舌的人不会用软语，
   这是 `REFERENCE_VOICE_LINES` 里两个主角的对照写法：
   唐迟克制（情绪转成动作），江喻外放（情绪直接冲出来）。
   同一场景里两个人的通道必须不同——这是「对白有区分度」的实现方式。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

log = logging.getLogger("entities.voiceprint_gen")

# ============================================================
# 项目内置角色声线参考（生成时作为范例注入）
# ============================================================

#: 已确立声线的角色。生成其他角色时给模型看这份对照，
#: 目的是让模型明白「声线差异」指的是什么维度，
#: 而不是让所有角色都长成这两人的样子。
REFERENCE_VOICE_LINES: dict[str, dict] = {
    "唐迟": {
        "核心": "克制型观察者",
        "主通道": "不说——情绪转成动作和记录",
        "泄压": "摸口袋找烟、报时间、报数字",
        "语气": "短句多、陈述句多，不解释自己",
        "禁忌": "不说「我害怕」「我想你」，不用形容词抒情",
    },
    "江喻": {
        "核心": "外放的反差型",
        "主通道": "说——情绪直接冲出来",
        "泄压": "俏皮话、毒舌、自嘲式玩笑",
        "语气": "疑问句多、跳脱，偶尔忽然认真",
        "禁忌": "不用沉默表达关心（那是唐迟的路数，不能混）",
    },
}


def _reference_text() -> str:
    lines: list[str] = []
    for nm, cfg in REFERENCE_VOICE_LINES.items():
        lines.append(f"【{nm}】{cfg['核心']}")
        lines.append(f"  主通道：{cfg['主通道']}")
        lines.append(f"  泄压：{cfg['泄压']}")
        lines.append(f"  语气：{cfg['语气']}")
        lines.append(f"  禁忌：{cfg['禁忌']}")
    return "\n".join(lines)


# ============================================================
# 草稿校验与清洗
# ============================================================

#: 8 个字段 → 期望形态（list 或 str）
FIELD_SHAPE: dict[str, type] = {
    "habits": list,
    "catchphrases": list,
    "dialect": list,
    "register": str,
    "psychology": str,
    "silence": str,
    "speech_pattern": str,
    "taboos": list,
}

#: 各字段的硬性约束：habits 要 3-5 个具体小动作，catchphrases 只要 1-2 个
_FIELD_LIMITS: dict[str, int] = {
    "habits": 5,
    "catchphrases": 2,
    "dialect": 4,
    "taboos": 4,
}

_BAD_HABIT = ("性格", "内向", "外向", "沉默寡言", "开朗", "温柔", "冷漠",
             "固执", "聪明", "善良", "诡异", "神秘")


#: 模型爱在列表项前面加的编号/符号
_ITEM_PREFIX_RE = re.compile(r"^\s*(?:[-*•·]|\(?\d{1,2}[.、)）]|[一二三四五六七八九十]+[、.])\s*")
#: 模型爱在列表项后面加的破折号注释（「哈——（拉长音，对着空处…）」）
_TAIL_NOTE_RE = re.compile(r"[（(][^）)]{6,}[）)]\s*$")


def _strip_list_noise(x: str) -> str:
    """去掉列表项里的编号前缀、尾部注释与同项内多条。

    三种噪声都会污染注入文本：
    - 编号「1. xxx」——编辑器里每行本来就分开，编号是冗余
    - 尾部注释「哈——（拉长音，对着空处）」——口癖要的是那个词本身，
      注释是给作者看的说明，注入时只会让模型模仿括号
    - 一项里塞多条（「绝不X、绝不Y、绝不Z」写成一行）——那是三条不是一条
    """
    s = _ITEM_PREFIX_RE.sub("", str(x).strip()).strip()
    s = _TAIL_NOTE_RE.sub("", s).strip()
    if len(re.findall(r"绝不|不使用|不会说|不用", s)) >= 2 and "、" in s:
        parts = [p.strip() for p in re.split(r"[、]", s) if p.strip()]
        if len(parts) >= 2:
            return "、".join(dict.fromkeys(parts))
    return s.strip("。．.、")


def _clean_list(raw: Any, cap: int) -> list[str]:
    """把模型吐的 str/list 统一成去空去重的短句列表。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = re.split(r"[\n；;]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = []
        for v in raw:
            if isinstance(v, dict):
                v = str(v.get("habit") or v.get("text") or v.get("value") or "")
            items.extend(re.split(r"[\n；;]+", str(v)))
    else:
        items = [str(raw)]
    out: list[str] = []
    for x in items:
        x = _strip_list_noise(x)
        if x and x not in out:
            out.append(x)
    return out[:cap]


def _clean_text(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    # 模型常在值里重复字段名（「语气基调：短句多」），注入时会显得啰嗦
    for prefix in ("语气基调：", "心理基调：", "沉默习惯：",
                   "说话方式：", "语言禁忌：", "口音："):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s.rstrip("。").strip()


def normalize_draft(raw: Any) -> dict:
    """把 LLM 返回洗成规范的 voiceprint dict，剔除模型编的设定。

    剔除规则：habits 里出现「性格/内向/沉默寡言」这类**抽象人格词**的一律丢掉。
    因为它们不是可执行动作——「性格孤僻」没法写进正文，
    而「习惯把话咽回去」可以。留着它等于把 AI 味写进档案。
    """
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key, kind in FIELD_SHAPE.items():
        cap = _FIELD_LIMITS.get(key, 4)
        if kind is list:
            items = _clean_list(data.get(key), cap)
            if key == "habits":
                items = [x for x in items
                         if not any(bad in x for bad in _BAD_HABIT)]
            out[key] = items
        else:
            out[key] = _clean_text(data.get(key))
    return out


def diff_against_current(draft: dict, current: dict | None) -> dict:
    """对比草稿与现有档案，标出「哪些字段会被改动」。

    前端用它来决定默认勾选哪些字段——作者手写过的字段默认不勾，
    避免一次「生成」把精心调过的语气基调冲掉。
    """
    cur = current if isinstance(current, dict) else {}
    changed: list[str] = []
    unchanged: list[str] = []
    for key in FIELD_SHAPE:
        new = draft.get(key)
        old = cur.get(key)
        same = (json.dumps(new, ensure_ascii=False, sort_keys=True)
                == json.dumps(old, ensure_ascii=False, sort_keys=True))
        (unchanged if same else changed).append(key)
    return {"changed": changed, "unchanged": unchanged}


# ============================================================
# Prompt
# ============================================================

_GEN_SYSTEM = """你是一名小说角色塑造专家，正在为《第七日的亡者》补全角色的「声音档案」。

## 你要产出什么

给定一个角色在实体库里的全部描述（身份、年龄、外貌、性格、动机、秘密、
身体状态、死状、信息差等），推导出这个角色的**说话方式**——
也就是让读者不看名字也能认出「这句是他说的」。

严格以 JSON 返回，不要输出 JSON 之外的任何文字，不要写 Markdown 代码块包裹。

{
  "habits": ["……3~5 条动作倾向（方向，不是具体动作）……"],
  "catchphrases": ["……1~2 条口头习惯（类别或极有辨识度的词）……"],
  "dialect": ["……用语习惯：口音/语序/特殊称呼/用词来源，可空数组……"],
  "register": "……一句话，整体说话气质……",
  "psychology": "……一句话，他怎么想事情，这决定他说什么……",
  "silence": "……一句话，他不说话的时候在干什么……",
  "speech_pattern": "……一句话，句式习惯……",
  "taboos": ["……他绝不会说的话/绝不会用的词……"]
}

## 关键约束（违反任何一条都算失败）

1. **一切必须能追溯到输入**。每个习惯动作、口癖都要能从角色描述里推出来。
   输入里没有的信息不要编——不要写「他喜欢喝红茶」这种输入里没有的东西。
   那是编设定，不是补档案。

2. **habits 写的是「动作倾向」，不是「照着演的具体动作」——而且必须覆盖多个身体通道**。
   档案会被原样注入写正文的提示词。如果这里写死「拇指反复搓食指指腹」，
   写手就只会把这句话原样塞进正文，读者看到的是**硬要说一下**的机器味。
   你要写的是**方向**，让写手按方向去发明每次不同的具体动作。

   **更关键：不要让所有倾向都长在手上。**正文层有「两句对白之间插一个小动作」的硬要求，
   如果档案只给了「手部有小动作」一个方向，写手所有小动作只能往手部挤——
   一章下来全是「他手指…他拇指…他掌心…」，就是复读。
   所以 3-5 条倾向要**分布在不同通道**上：

   | 通道 | 写法示例 |
   |---|---|
   | 手部 | 手部常有下意识的小动作 / 手边总要摆着点什么可把玩的东西 |
   | 视线 | 视线会先扫一遍环境：出口、人数、能退到哪 |
   | 呼吸 | 呼吸节奏会变：紧张时压着气说话 |
   | 姿势 | 姿势总带一点随时能动的紧绷：重心偏在脚后跟 |
   | 声线 | 说话时声线压得比平时低半度 |
   | 脚步 | 走动时脚步比看上去轻 |
   | 距离感 | 跟人说话时总隔着半步，不让人靠太近 |
   | 物件 | 说话前先把手边的东西挪开，像在清桌面 |

   ✗ 5 条全是手部：「手指」「拇指」「掌心」「指节」「手腕」——**这是复读配方**
   ✗ 写死具体动作：「拇指反复搓食指指腹，那是长期敲键盘磨出来的」
   ✗ 抽象人格词：「性格孤僻」「沉默寡言」（没法写进正文）
   判断标准：**读完你的描述，写手能不能为它发明出五种不同的具体动作？**
   能，说明你写的是倾向；不能，说明你写死了动作。
   再问一句：**这 3-5 条是不是分布在不同通道上？**全在手上 → 重写。

3. **catchphrases 是「口头习惯的类别」，不是逐字台词清单**。
   档案里写了具体的那几个词，写手会反复原样搬用，读起来像口头禅复读机。
   要么写**类别**（他爱用短促的判断词收尾 / 紧张时会甩一句网络梗），
   要么只留 1-2 个**极有辨识度**、值得反复出现的词。
   ✓「热的。」「行。」「不烫。」「知道了。」 ✗ 完整句子（那是 register 不是口癖）
   ✗ 一次给七八个词（那不是口癖，是词库，写手会挨个轮流用）

4. **语气要与身份一致**。刑警不说文绉绉，毒舌的人不用软语，
   码头工人不说「这让我感到不安」。身份错位的语气是最容易被读者察觉的破绽。

5. **区分度 > 完整度**。宁可少写一个字段，也不要写得平庸。
   一个写得精准的「沉默习惯」比五个通用的「紧张时会搓手」有用得多。

## 声线差异的判断维度

参照下面两个角色的对照，理解「声线差异」指的是什么：

{reference}

注意：**这不是模板**。你要做的是找到这个角色自己的「主通道」
（他平时怎么表达情绪：说的还是做的）和「泄压通道」
（情绪压不住时从哪漏出来）。两个角色的通道必须不同。

## 输出字段说明

- habits：3~5 条**动作倾向**。每条是**方向**，不是具体动作——写手要按方向自己发明每次不同的具体表现。
  例：「手部常有下意识的小动作」「手边总要摆着点什么可把玩的东西」「习惯把身边的事按时间或数量记下来」。
  与身份/经历/身体状态相关，但**不要写死成某一个具体动作**。
- catchphrases：1~2 条**口头习惯**。要么写类别（「爱用短促的判断词收尾」「紧张时甩一句整活的抽象语录」），
  要么只留一个极有辨识度、值得反复出现的词。不要给一串词让它轮流用。
- dialect：可空数组。用语习惯——口音特征、语序习惯、对方言的称呼方式、**词从哪来**
  （例：「地球的词偶尔滑出来，顿半拍，自己换算成蓝星听得懂的说法」）。没有特别口音就给空数组。
- register：整体说话气质。比如「懒散、短句多、懒得解释」。
- psychology：他怎么想事情。比如「习惯把感受换算成成本和数字」。**这条最影响他说什么**。
- silence：他沉默的时候在干什么。这是外放型角色最容易漏的字段——
  江喻那样的人一旦沉默，那个沉默本身就是信号，要写出来。
- speech_pattern：句式习惯。比如「爱用省略主语的短句，不反问，只陈述」「喜欢抬杠，被怼了也要接一句」。
- taboos：他绝不会说的话/词。比如「不说『我爱你』」「不用书面语」。
"""


def build_messages(entity_name: str, entity: dict) -> list[dict]:
    """把实体档案拍平成模型输入。"""
    e = entity if isinstance(entity, dict) else {}
    lines: list[str] = [f"角色名：{entity_name}"]
    if e.get("summary"):
        lines.append(f"简介：{e['summary']}")
    if e.get("details"):
        lines.append(f"档案原文：\n{e['details']}")
    if e.get("appearances"):
        ap = e["appearances"]
        lines.append(f"出场章节：{ap if isinstance(ap, str) else '、'.join(str(x) for x in ap)}")
    if e.get("status"):
        lines.append(f"状态：{e['status']}")
    # state_attrs 里常有身体状态/死状/信息差，一并给模型
    attrs = e.get("state_attrs") or {}
    if isinstance(attrs, dict) and attrs:
        attr_lines = "；".join(f"{k}={v}" for k, v in attrs.items() if v)
        if attr_lines:
            lines.append(f"状态属性：{attr_lines}")

    return [
        {"role": "system", "content": _GEN_SYSTEM.replace("{reference}", _reference_text())},
        {"role": "user", "content": "\n".join(lines) + (
            "\n\n请按 JSON schema 输出该角色的声音档案，严格遵守 5 条关键约束。"
        )},
    ]


# ============================================================
# 生成入口
# ============================================================

def generate_draft(
    provider: Any,
    entity_name: str,
    entity: dict,
    *,
    regen: Callable[[], str] | None = None,
) -> dict:
    """生成草稿并返回规范化结果。

    provider 走 main.py 的 _chore_provider("voiceprint") 适配器。
    regen 是重试时的备选调用（与 complete_structured 的用法一致）。

    失败时不抛异常阻断作者操作，返回 {"_error": "..."}，
    前端据此提示「生成失败，手动填写即可」。
    """
    from ..generation import complete_structured

    messages = build_messages(entity_name, entity)

    def _call() -> str:
        if regen is not None:
            return regen()
        return provider.chat(messages).text

    try:
        comp = complete_structured(_call, max_regenerations=2)
    except Exception as e:  # 生成失败不该让作者卡住——手动填也是路
        log.warning("声音档案生成失败（%s）：%s", entity_name, e)
        return {"_error": f"生成失败：{e}"}

    data = comp.data if isinstance(comp.data, dict) else {}
    if not data:
        return {"_error": "模型没有返回可用内容"}

    draft = normalize_draft(data)
    if not any(
        draft.get(k) for k in FIELD_SHAPE
    ):
        return {"_error": "模型返回的内容全部无效"}
    return draft