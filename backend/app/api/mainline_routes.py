"""主线开篇卷（mainline）章纲生成端点：POST /volumes/{vol_no}/mainline-outline。

挂载方式：main.py 末尾调用
    from .mainline_routes import register_mainline_routes
    register_mainline_routes(app, require_project=..., volumes_for=..., project_dir=..., chore_provider=...)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

log = logging.getLogger("api.mainline")

_MAINLINE_OUTLINE_SYSTEM = """你是《第七日的亡者》蓝星开篇卷的章纲设计师。本卷共 2 章，day=0（尚未进入副本），主角叫唐迟——一个理性、冷静、善归纳、对规则边界敏感的普通程序员，但他的理性底色体现为"先观察再判断、按逻辑推演因果"，禁止在叙事/心理/章纲里出现任何程序员职业比喻或黑话（如 debug/Bug/栈/日志/变量/指针/越权/溢出/短路/重定向/死锁/卡bug 等）。

【硬约束】
- 第1章（hook / 300字具体动作切入）：蓝星都市日常，从唐迟一个具体动作切入（加班/地铁/外卖/快递/便利店/加班夜宵等生活化场景）；1500字内完成主角亮相（理性/观察力/普通人质感）；3000字铺日常+不对劲（收到一份讣告/死讯/自己的名字出现在讣告栏上/快递到付一个空骨灰盒等合理钩子）；章末讣告送达。严禁穿越。每章目标4500字。
- 第2章（sell）：讣告甩不掉（手机/报纸/同事口耳相传多重应验）+第一桩应验事件发生+自救路径全堵+穿越瞬间（具体感官锚点：冷/湿/咸/锈味/有人喊他一个陌生名字），章末睁眼陌生脸喊宿主名。严禁编程黑话。每章目标4500字。
- 两章节奏：第1章慢热立人，第2章加速坠落。
- 不许出现任何副本/NPC/克苏鲁神明/魂穿宿主姓名——这些在副本卷才出现。
- 钩子必须有后劲：读者看完必须想知道"是谁给他发了讣告""穿越后他变成了谁"。

严格以 JSON 返回，不要输出 JSON 之外的任何文字。返回格式：
{
  "outlines": [
    {"chapter": 1, "title": "……5~10字短标题……", "day": 0, "summary": "……100~150字本章纲要（含开篇动作/主角亮相/不对劲铺陈/章末讣告钩子）……", "hooks": "……20~40字章末钩子……"},
    {"chapter": 2, "title": "……5~10字短标题……", "day": 0, "summary": "……100~150字本章纲要（讣告应验+自救失败+穿越瞬间感官锚点+章末睁眼陌生脸）……", "hooks": "……20~40字章末钩子……"}
  ]
}
"""


# ---- 依赖注入 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    project_dir: Callable[[str], Path]
    chore_provider: Callable


_deps: _Deps | None = None


def register_mainline_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    project_dir: Callable[[str], Path],
    chore_provider: Callable,
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        project_dir=project_dir,
        chore_provider=chore_provider,
    )

    router = APIRouter(tags=["mainline"])
    router.add_api_route(
        "/api/projects/{pid}/volumes/{vol_no}/mainline-outline",
        _mainline_outline, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("mainline_routes 未调用 register_mainline_routes 初始化")
    return _deps


# ---- 请求体 ----

class _OutlineIn(BaseModel):
    directive: str = ""
    chapter_count: int = 2


# ---- Mock 兜底 ----

def _mock_outlines() -> dict:
    return {
        "outlines": [
            {
                "chapter": 1,
                "title": "蓝星讣告",
                "day": 0,
                "summary": (
                    "深夜十点，唐迟加完班走出写字楼，在 24 小时便利店买了一份加热便当和一瓶矿泉水。"
                    "地铁上他习惯观察身边乘客的小动作，回家拆快递时发现一封没有寄件人地址的黑色信封，"
                    "里面是一份打印工整的讣告——死者姓名那一栏印着他自己的名字，死亡日期是三天后。"
                    "他起初以为是恶作剧，但手机里、小区公告栏、甚至第二天早报的讣闻栏都出现了相同的名字。"
                ),
                "hooks": "唐迟拨通讣告上预留的治丧电话，听筒里传来他自己昨晚说梦话的声音。",
            },
            {
                "chapter": 2,
                "title": "穿越坠落",
                "day": 0,
                "summary": (
                    "讣告如附骨之疽：同事口耳相传他已经走了，手机相册里凭空多出他的遗照，外卖送来的筷子是"
                    "丧宴用的白色一次性筷子。他尝试报警、找朋友求证、甚至躲进酒店，每一次自救都被更具体的"
                    "应验堵死。第三天深夜，他在自家浴缸里被一股冰冷湿咸的力量拽入水下，铁锈味灌满鼻腔，"
                    "恍惚间有人在耳边喊一个陌生名字。他猛地睁眼，头顶是摇晃的煤油灯，一张陌生的脸凑过来，"
                    "用他听不懂的方言焦急地喊他——宿主的名字。"
                ),
                "hooks": "陌生面孔凑到他面前喊出一个从未听过的名字，而他下意识地应了一声。",
            },
        ]
    }


# ---- book 上下文组装 ----

_DEFAULT_BOOK_CONTEXT = (
    "主角唐迟：理性、冷静、善归纳、对规则边界敏感的普通蓝星打工人；"
    "思维方式是先观察再判断、按逻辑推演因果；日常加班、坐地铁、吃便利店便当的普通人质感；"
    "严禁任何程序员职业比喻或黑话。"
)


def _collect_book_context(project_dir: Path) -> str:
    book_dir = project_dir / "book"
    if not book_dir.exists() or not book_dir.is_dir():
        return _DEFAULT_BOOK_CONTEXT
    parts: list[str] = []
    for p in sorted(book_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 只拼文本类字段
        text_parts: list[str] = []
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, str) and v.strip():
                    text_parts.append(f"{k}: {v.strip()[:200]}")
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.strip():
                            text_parts.append(f"- {item.strip()[:200]}")
        if text_parts:
            parts.append(f"===== {p.stem} =====\n" + "\n".join(text_parts))
    if not parts:
        return _DEFAULT_BOOK_CONTEXT
    return "\n\n".join(parts)


# ---- handler ----

def _mainline_outline(
    pid: str,
    vol_no: int,
    body: _OutlineIn = Body(...),
) -> dict:
    """POST /volumes/{vol_no}/mainline-outline —— 生成蓝星开篇卷章纲（固定 2 章）。"""
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    vol = vs.get(vol_no)
    if vol is None:
        raise HTTPException(404, f"卷 {vol_no} 不存在")
    if vol.volume_kind != "mainline":
        raise HTTPException(400, f"卷 {vol_no} 不是主线卷（mainline），无法生成主线章纲")

    book_ctx = _collect_book_context(d.project_dir(pid))

    parts: list[str] = []
    parts.append("===== 主角 / 世界观基础设定（book 卡）=====")
    parts.append(book_ctx)
    directive = (body.directive or "").strip()
    if directive:
        parts.append("\n===== 作者指令 =====")
        parts.append(directive)
    parts.append("\n===== 硬要求 =====")
    parts.append(
        "本卷是蓝星开篇卷，共 2 章，day=0（未进入副本）。\n"
        "第1章：蓝星都市日常切入 + 主角亮相 + 不对劲铺陈 + 章末讣告送达（严禁穿越）。\n"
        "第2章：讣告多重应验 + 自救失败 + 穿越瞬间感官锚点 + 章末睁眼陌生脸喊宿主名。\n"
        "每章目标 4500 字；禁止编程黑话；不许出现副本/NPC/克苏鲁神明/宿主姓名。\n"
        "请严格按 system 给定 JSON schema 返回 outlines 数组，不要输出任何其他文字。"
    )
    context = "\n".join(parts)

    data: dict | None = None
    try:
        from ..generation import complete_structured

        def _call() -> str:
            return d.chore_provider("mainline-outline").chat([
                {"role": "system", "content": _MAINLINE_OUTLINE_SYSTEM},
                {"role": "user", "content": context},
            ]).text

        comp = complete_structured(_call, max_regenerations=2)
        data0 = comp.data if isinstance(comp.data, dict) else {}
        if isinstance(data0, dict):
            outs = data0.get("outlines")
            if isinstance(outs, list) and len(outs) >= 2:
                data = data0
    except Exception as e:
        log.warning("mainline-outline LLM 失败，回退 mock：%s", e)

    if data is None:
        data = _mock_outlines()

    # 规整输出：只取前 chapter_count 章，chapter 字段重排 1..N
    outs0 = data.get("outlines") if isinstance(data, dict) else None
    if not isinstance(outs0, list) or len(outs0) < 2:
        outs0 = _mock_outlines()["outlines"]
    outlines = []
    for i, item in enumerate(outs0[: max(1, int(body.chapter_count or 2))], start=1):
        if not isinstance(item, dict):
            continue
        outlines.append({
            "chapter": i,
            "title": str(item.get("title") or "").strip() or ("蓝星讣告" if i == 1 else "穿越坠落"),
            "day": 0,
            "summary": str(item.get("summary") or "").strip(),
            "hooks": str(item.get("hooks") or "").strip(),
        })
    if len(outlines) < 2:
        outlines = _mock_outlines()["outlines"]

    return {"ok": True, "outlines": outlines, "is_mock": data is _mock_outlines() or data.get("is_mock") is True}
