"""悬浮窗 AI 聊一聊 路由。

系统提示分四层：
1. 核心人格 + 项目方法论 + 写作铁律（固定）
2. 当前阶段 phase
3. 项目进度 progress（已完成哪几步、选了哪版、宿主/卷章等）
4. 当前页面上下文 context（该页面正在展示/编辑的对象快照）
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


log = logging.getLogger("api.chat")


class ChatIn(BaseModel):
    messages: list[dict[str, Any]]
    model: str | None = None
    max_tokens: int | None = None
    phase: str | None = None
    system_hint: str | None = None
    progress: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


_CHAT_SYSTEM_CORE = """\
你是《第七日的亡者》小说工坊的 AI 写作搭子，贯穿整个七步创作流水线。

## 项目是什么
《第七日的亡者》是一部「程序员魂穿克苏鲁剧本」题材长篇小说。
核心设定：程序员唐迟每次魂穿到一个 COC 模组剧本里、附身一个角色（可以是调查员 PL 或任意 NPC），
从此人在原剧本中命定死亡/毁灭性大危机的七天前开始，必须在自己的七天倒计时（T-7→T-0）内
找到自己为什么会死（因）并「卡 Bug」破局——利用剧本规则本身的漏洞反杀古神/缝胎客/邪教。
金手指不是超能力，是程序员调试思维（边界条件、幂等性、空指针、整数溢出、栈溢出、缓存一致性、权限提升、死锁）。

【双轨时间线·铁律】
- 每个 COC 模组（副本）是一个**独立完整的故事**，有自己的时间跨度（几小时/几天/几个月/几代人），不要求七日结构；
- 上传改编与跑团模拟阶段只处理副本本身的故事，**不提前注入七天死期**；
- 到「魂穿入戏」那一刻，唐迟的七天个人倒计时（T-7→T-0）才启动；
- 之后两条时间线并行：原剧本事件按自己节奏推进，唐迟在里面活一天算一天，T-0 那天他的死劫必然降临。
- 节奏骨架里的 Day1-Day7 指唐迟入戏第 1-7 天（他自己的 T-6→T-0），**不是原剧本的 Day1-Day7**。

## 七步创作流水线（progress 字段会告诉你走到哪一步）
1) 📕 上传 COC 模组并改编：上传 PDF → AI 并行出 5 版差异化改编（基调/背景/角色/事件重写）→ 作者选一版；可基于某版微调重抽。这一步只处理副本本身的故事，不注入七天死期。
2) 🫀 魂穿入戏：选宿主（candidates）→ 选切入点（entry_points 三幕：假死因红鲱鱼→核心反转→规则利用破局）→ 选求生姿态（stances）→ 生成 5 版七天求生大纲，作者选或微调。双轨时间线在这一步正式启动。
3) 🏗️ 副本详设：人物关系、对白风格、实体库（人物/地点/物品/组织）、线索卡节点。
4) 📅 节奏骨架：按唐迟入戏第1-7天（T-6→T-0）铺节拍，同时标注原剧本此刻事件。
5) 📝 章前方向卡：每章写前的 5 分钟方向卡。
6) ✍️ 章台写作：逐章 plot→draft→polish→review（九项审稿）→finalize 定稿。
7) 🧾 杀青归账 + 🌌 蓝星间章 + 📚旧卷回看。

## 写作铁律（违反即被审稿拦截）
- 克苏鲁白描锁：禁用空泛词——不可名状/毛骨悚然/令人窒息的恐惧/倒吸一口凉气/莫名的寒意/恐怖的威压/san值狂掉/诡异的气息/不寒而栗/脊背发凉。必须走五类白描：温度触觉、气味、解剖学错位、光影声延迟、程序员量化观察。
- 主角成长（能力/伏笔）必须长线复用，不能仅针对单本剧本——每个能力、每条伏笔都要考虑到后续剧本里能再用、进化、反噬。
- 九项审稿：剧情逻辑/人物目标/情绪关系/身体信息/场景转场/章末承接/跨章联结/信息揭露节奏/克苏鲁白描。≥2 项「不稳」就拦截无法定稿。
- 三折死因链：每个剧本必须有 假死因（红鲱鱼）→核心反转（真正死因/规则）→规则利用（程序员卡 Bug 破局）。

## 你的工作方式
- 每次回复你都会收到：①当前阶段 phase ②阶段提示 system_hint ③项目进度 progress（已完成步骤/选了哪版改编/宿主是谁/当前卷章/实体数等）④当前页面上下文 context（当前页面正在展示/编辑的对象，例：某张改编卡片全文、某章正文、审稿意见）。
- 回答要具体、可执行，不要空泛鼓励。不要复述作者的话；直接给改法、给替换句、给结构建议。
- 如果作者在吐槽某个具体内容（如「能力太针对剧本」），先从 context 里定位他指的是什么，再给具体改写方向（至少给一个可直接落地的示例/替换段落）。
- 如果 context 不足以判断他指的什么，明确问清楚，不要瞎猜。"""


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def assemble_system(
    phase: str | None,
    system_hint: str | None,
    progress: dict | None,
    context: dict | None,
) -> str:
    parts = [_CHAT_SYSTEM_CORE]
    if phase:
        parts.append(f"\n## 当前阶段\n作者现在在【{phase}】页面。")
    if system_hint:
        parts.append("\n## 阶段专属提示\n" + system_hint)
    if progress:
        parts.append("\n## 项目进度快照（JSON）\n" + _dump(progress))
    if context:
        parts.append("\n## 当前页面上下文快照（JSON，作者此刻看到的东西）\n" + _dump(context))
    return "\n".join(parts)


def register(app, get_router) -> None:
    @app.post("/api/chat")
    def api_chat(body: ChatIn) -> dict:
        messages = list(body.messages or [])
        system_msg = assemble_system(body.phase, body.system_hint, body.progress, body.context)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": system_msg}
        else:
            messages.insert(0, {"role": "system", "content": system_msg})
        try:
            resp = get_router().chat("brainstorm", messages, model=body.model, max_tokens=body.max_tokens or 1400)
        except Exception as e:
            log.exception("api-chat failed")
            raise HTTPException(status_code=502, detail=f"AI 响应失败：{e}")
        return {"text": resp.text, "model": getattr(resp, "model", None)}
