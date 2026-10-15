"""杀青自动归账：从 wrapup 卡文本里用 LLM 抽取结构化归账 diff。

入口函数：
  extract_settle_from_wrapup(wrapup_text, provider, ps) -> dict

diff 结构（与 EntityStore.apply_settle_diff 对齐）：
  {
    "crafts": [{"name","proficiency","from_field"}],
    "echoes": [{"name","craft_hint","obsession","fade_stage","from_field"}],
    "god_marks": [{"god","seal_form","risk","from_field"}],
    "brought_items": [{"name","contam","effect","from_field"}],
    "bonds_delta": [{"name","relation","depth","note"}],
    "kill_record_line": str,
    "contamination_delta": int 0-10,
    "field_count_inc": 1,
  }
"""
from __future__ import annotations

from app.generation import complete_structured
from app.providers.base import Provider
from app.pipeline.prompts import PromptStore


SETTLE_SYSTEM = """你是《第七日的亡者》的杀青账房先生。任务是阅读作者刚锁定的「杀青/残页卡」文本，从中严格抽取归账 diff（JSON），用于自动回写到主角唐迟的跨场档案。

==== 抽取铁律 ====
1. 只输出一个 JSON 对象，不要输出任何解释文字、不要 markdown 代码块包裹。
2. 字段必须齐全；文本里没有提到的项留空数组/0/空字符串，不要编造。
3. 手艺/残影/邪神标记/带回物品：只有当文本明确写了「学会/借到/带回/身上多了/刻了/留了印记」这类因果绑定才抽取；不要凭空脑补。
4. bonds_delta（蓝星羁绊新增/加深）：只有当文本出现蓝星人物互动（猫也算）、且羁绊有明确推进才抽取。name 写具体称谓（如「煤球/楼下阿姨/便利店小哥」）；relation 写关系（猫/邻居/同事/其他扮演者伪装）；depth 取「浅/中/深」；note 写推进方式。
5. kill_record_line：杀青账一句话，风格如「第X场：小镇纸人，借了剪纸手艺，欠了老阿婆一条命」。要能直接贴到账本上。
6. contamination_delta：本场污染增量 0-10 整数，普通爽副本 0-2，邪神直瞄 5-8，直视外神 9-10。
7. field_count_inc 固定为 1（完成一场）。
8. from_field 先留空字符串（调用方会回填当前 field_no）。

==== JSON Schema ====
{
  "crafts": [{"name": str, "proficiency": str, "from_field": str}],
  "echoes": [{"name": str, "craft_hint": str, "obsession": str, "fade_stage": str, "from_field": str}],
  "god_marks": [{"god": str, "seal_form": str, "risk": str, "from_field": str}],
  "brought_items": [{"name": str, "contam": int, "effect": str, "from_field": str}],
  "bonds_delta": [{"name": str, "relation": str, "depth": str, "note": str}],
  "kill_record_line": str,
  "contamination_delta": int,
  "field_count_inc": int
}

其中：
- proficiency 取值建议："初窥"/"小成"/"精通"（或文本里的描述）。
- fade_stage 取值建议："新鲜"/"淡去"/"快散"（描述残影消退程度）。
- contam 取值 0-10（单物件污染强度）。
- depth 取值 "浅"/"中"/"深"。

==== 注意 ====
- 如果文本完全没提到某一类（比如没有邪神标记），该数组给 []，不要硬凑。
- kill_record_line 如果文本里已经写了杀青账句子，优先用原文；否则按文本信息自己总结一句，20-40 字。
- contamination_delta 要综合：污染征兆强度、直视邪神时长、使用执念金手指次数、带回物污染度。"""


def _empty_diff() -> dict:
    return {
        "crafts": [],
        "echoes": [],
        "god_marks": [],
        "brought_items": [],
        "bonds_delta": [],
        "kill_record_line": "",
        "contamination_delta": 0,
        "field_count_inc": 1,
    }


def _normalize_diff(data: object, field_no: int | None = None) -> dict:
    """容错归一化 LLM 输出。"""
    out = _empty_diff()
    if not isinstance(data, dict):
        return out
    fn = str(field_no) if field_no is not None else ""

    def _lst(key: str) -> list[dict]:
        arr = data.get(key)
        return [x for x in arr if isinstance(x, dict)] if isinstance(arr, list) else []

    crafts = []
    for c in _lst("crafts"):
        crafts.append({
            "name": str(c.get("name", "") or "").strip(),
            "proficiency": str(c.get("proficiency", "初窥") or "初窥").strip(),
            "from_field": str(c.get("from_field") or fn),
        })
    out["crafts"] = [c for c in crafts if c["name"]]

    echoes = []
    for e in _lst("echoes"):
        echoes.append({
            "name": str(e.get("name", "") or "").strip(),
            "craft_hint": str(e.get("craft_hint", "") or "").strip(),
            "obsession": str(e.get("obsession", "") or "").strip(),
            "fade_stage": str(e.get("fade_stage", "新鲜") or "新鲜").strip(),
            "from_field": str(e.get("from_field") or fn),
        })
    out["echoes"] = [e for e in echoes if e["name"]]

    marks = []
    for m in _lst("god_marks"):
        marks.append({
            "god": str(m.get("god", "") or "").strip(),
            "seal_form": str(m.get("seal_form", "") or "").strip(),
            "risk": str(m.get("risk", "") or "").strip(),
            "from_field": str(m.get("from_field") or fn),
        })
    out["god_marks"] = [m for m in marks if m["god"] or m["seal_form"]]

    items = []
    for b in _lst("brought_items"):
        try:
            contam = int(b.get("contam", 0) or 0)
        except (TypeError, ValueError):
            contam = 0
        contam = max(0, min(10, contam))
        items.append({
            "name": str(b.get("name", "") or "").strip(),
            "contam": contam,
            "effect": str(b.get("effect", "") or "").strip(),
            "from_field": str(b.get("from_field") or fn),
        })
    out["brought_items"] = [b for b in items if b["name"]]

    bonds = []
    for bd in _lst("bonds_delta"):
        bonds.append({
            "name": str(bd.get("name", "") or "").strip(),
            "relation": str(bd.get("relation", "") or "").strip(),
            "depth": str(bd.get("depth", "浅") or "浅").strip(),
            "note": str(bd.get("note", "") or "").strip(),
        })
    out["bonds_delta"] = [b for b in bonds if b["name"]]

    out["kill_record_line"] = str(data.get("kill_record_line", "") or "").strip()
    try:
        cd = int(data.get("contamination_delta", 0) or 0)
    except (TypeError, ValueError):
        cd = 0
    out["contamination_delta"] = max(0, min(10, cd))
    try:
        out["field_count_inc"] = int(data.get("field_count_inc", 1) or 1)
    except (TypeError, ValueError):
        out["field_count_inc"] = 1
    return out


def extract_settle_from_wrapup(
    wrapup_text: str,
    provider: Provider,
    ps: PromptStore,
    *,
    field_no: int | None = None,
) -> dict:
    """让 LLM 从杀青卡文本抽取结构化归账 diff。

    provider: 用 chore_provider("settle") 传入即可。
    ps: PromptStore（为保持签名一致传入，当前 settle system prompt 使用模块内常量，不走工坊覆盖）。
    field_no: 可选，若传入会自动写入 diff 每项的 from_field。
    """
    text = (wrapup_text or "").strip()
    if not text:
        return _empty_diff()

    user_block = (
        "【杀青/残页卡文本】\n"
        f"{text}\n\n"
        "请严格按照 system 提示的 JSON Schema 输出归账 diff。只输出 JSON，不要任何解释。"
    )
    messages = [
        {"role": "system", "content": SETTLE_SYSTEM},
        {"role": "user", "content": user_block},
    ]

    def generate() -> str:
        return provider.chat(messages).text

    try:
        completion = complete_structured(generate, max_regenerations=2)
        return _normalize_diff(completion.data, field_no=field_no)
    except Exception:
        # LLM 彻底失败时仍返回空 diff，不阻断前端流程（前端弹空 diff 让用户手填即可）
        return _empty_diff()
