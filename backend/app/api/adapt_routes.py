"""工作台 adapt 阶段路由：PDF 上传 → 5 版并行改编 → SSE 推送 → 选版落盘 → 阶段完成标记。

挂载方式：main.py 末尾调用
    from .adapt_routes import register_adapt_routes
    register_adapt_routes(app, require_project=..., volumes_for=..., project_dir=...,
                          chore_provider=..., prompt_store=...)
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    project_dir: Callable[[str], Any]
    chore_provider: Callable[[str], Any]
    prompt_store: Any


_deps: _Deps | None = None


def register_adapt_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    project_dir: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
    prompt_store: Any,
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        project_dir=project_dir,
        chore_provider=chore_provider,
        prompt_store=prompt_store,
    )

    router = APIRouter()
    # 2a. volume detail
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}",
        _get_volume, methods=["GET"],
    )
    # 2b. 启动 pdf5
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/pdf5",
        _start_pdf5, methods=["POST"],
    )
    # 2c. 列 jobs
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/jobs",
        _list_jobs, methods=["GET"],
    )
    # 2d. SSE 流
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/stream",
        _stream_jobs, methods=["GET"],
    )
    # 2e. 选版
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/pick",
        _pick_adapt, methods=["POST"],
    )
    # 2f. 保存自定义改编（不依赖 job，直接写入）
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/save-custom",
        _save_custom_adapt, methods=["POST"],
    )
    # 2f-bis. 撤销选版：清掉 adapted_module + adapt 完成标记，回到待选版状态（不动原 PDF 和 adaptations）
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/adapt/unpick",
        _unpick_adapt, methods=["POST"],
    )
    # 2g. 阶段完成标记
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/stage-complete",
        _stage_complete, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("adapt_routes 未调用 register_adapt_routes 初始化")
    return _deps


# ---- 请求体 ----

class _PickIn(BaseModel):
    job_id: str
    pick_index: int
    extra_edits: str = ""


class _SaveCustomAdaptIn(BaseModel):
    adaptation: dict  # 自定义 adaptation 对象，含 adapted_premise 等字段


class _StageCompleteIn(BaseModel):
    stage: str


_ALLOWED_STAGES = {"adapt", "simulate", "embody", "scaffold", "rhythm", "settle"}


# ---- 帮助函数 ----

def _get_volume_or_404(pid: str, vol_no: int):
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return vs, v


def _adapt_session_id(pid: str, vol_no: int) -> str:
    return f"adapt:{pid}:{vol_no}"


# ---- 路由处理 ----

def _get_volume(pid: str, vol_no: int) -> dict:
    """GET 卷详情。"""
    _, v = _get_volume_or_404(pid, vol_no)
    return {"volume": v.as_dict()}


def _list_jobs(pid: str, vol_no: int) -> dict:
    """GET 列出该卷所有 adapt jobs。"""
    from ..jobs.store import job_store
    _get_volume_or_404(pid, vol_no)
    sid = _adapt_session_id(pid, vol_no)
    jobs = job_store.list_session(sid)
    return {"jobs": [j.as_dict() for j in jobs]}


def _stream_jobs(pid: str, vol_no: int):
    """SSE：持续推送 adapt session 下所有 job，3 轮 idle 后结束。"""
    from ..jobs.store import job_store, jobs_to_sse_event_list
    _get_volume_or_404(pid, vol_no)
    sid = _adapt_session_id(pid, vol_no)

    def gen():
        idle_rounds = 0
        while True:
            jobs = job_store.list_session(sid)
            yield jobs_to_sse_event_list(jobs)
            any_active = any(j.status in ("pending", "running") for j in jobs)
            if not any_active:
                idle_rounds += 1
                if idle_rounds >= 3:
                    yield f"event: job_idle\ndata: {json.dumps({'ok': True}, ensure_ascii=False)}\n\n"
                    return
            else:
                idle_rounds = 0
            time.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _stage_complete(pid: str, vol_no: int, body: _StageCompleteIn) -> dict:
    """POST 标记某阶段已完成。

    **必须清掉 `_view` 覆盖**：`_view` 是「回看已完成阶段」的临时标记，
    而 `/phase` 路由在函数最开头就检查它并无条件返回——只要它还在，
    任何阶段推进都会被拦回原来那一页，表现为「点下一阶段原地刷新」。

    历史事故：卷 2 的 `_view='adapt'` 残留，`adapt=True` 却永远被拦回改编页。
    """
    if body.stage not in _ALLOWED_STAGES:
        raise HTTPException(status_code=422, detail=f"未知阶段：{body.stage}")
    vs, v = _get_volume_or_404(pid, vol_no)
    sc = dict(v.stage_completed or {})
    sc[body.stage] = True
    sc.pop("_view", None)
    v.stage_completed = sc
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


def _unpick_adapt(pid: str, vol_no: int) -> dict:
    """POST 撤销选版：清掉 adapted_module 与 adapt 阶段完成标记，回到待选版状态。
    保留原 PDF 文件和 pdf5 job 产物（adaptations），以便作者重抽或重新挑选。
    同时清理跑团及之后阶段所有产物。"""
    vs, v = _get_volume_or_404(pid, vol_no)
    v.adapted_module = None
    sc = dict(v.stage_completed or {})
    sc["adapt"] = False
    sc.pop("_view", None)
    for stage in ("simulate", "embody", "scaffold", "rhythm"):
        sc[stage] = False
    v.stage_completed = sc
    for attr in (
        "simulated_run", "host_character", "entry_point", "info_gap",
        "stance", "tangchi_story_outline", "deviation_points",
        "scaffold_cards", "death_nodes", "rhythm_blueprint", "density_map",
    ):
        if hasattr(v, attr):
            try:
                setattr(v, attr, None)
            except Exception:
                pass
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


# ============================================================
# PDF × 5 并行改编（不依赖 session）
# ============================================================

def _start_pdf5(
    pid: str,
    vol_no: int,
    file: UploadFile = File(...),
    user_hint: str = Form(""),
    n: int = Form(5),
) -> dict:
    """POST multipart：上传 PDF，启动 n 版并行改编后台任务。"""
    from ..jobs.store import job_store, run_in_background
    from ..pipeline.sushi_generator import extract_pdf_text, adapt_pdf_source

    log = logging.getLogger("api.adapt")
    vs, v = _get_volume_or_404(pid, vol_no)
    if v.volume_kind != "dungeon":
        raise HTTPException(status_code=422, detail="只有副本卷（dungeon）可以上传模组 PDF")

    fname = (file.filename or "").lower()
    if not fname.endswith(".pdf"):
        raise HTTPException(status_code=422, detail="仅支持 .pdf 文件")
    pdf_bytes = file.file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="PDF 过大（>20MB）")

    try:
        pdf_text, _ = extract_pdf_text(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{e}")
    if len(pdf_text.strip()) < 100:
        raise HTTPException(status_code=422, detail="提取出的文本太短（扫描版需 OCR 或文件损坏）")

    n_val = max(2, min(8, int(n or 5)))
    d = _must_deps()
    sid = _adapt_session_id(pid, vol_no)
    job = job_store.create(session_id=sid, kind="pdf_adapt5", meta={
        "filename": file.filename, "n": n_val, "volume_no": vol_no,
    })

    ps = d.prompt_store
    hint = user_hint or ""

    def _work(j, update):
        results = [None] * n_val
        errors: list[str] = []
        # 风格方向池：涵盖中外/古今/虚拟/现实多种方向，供 _run_one 每轮抽取；
        # 注意这里只是"提示方向"，真正落地以 prompt 里"自主选择"规则为准，不强制按档分。
        flavor_pool = [
            "中式乡土民俗阴冷（祠堂/旧俗/水乡/山村/渔港）",
            "现代都市规则冷峻（明文禁忌/制度恶意/职场/医院/学校/公寓）",
            "当代诡异日常（平常秩序里长出不对劲：邻居/便利店/通勤/外卖/快递）",
            "西方哥特维多利亚（古堡/家族/修道院/侦探/雾都/宗教审判）",
            "美式小镇/西部荒野（偏远小镇/公路/荒野/加油站/汽车旅馆/印第安传说）",
            "日式怪谈/校园（校园/公寓/邻居/艺伎/武士/昭和/平成都市传说）",
            "东方古风仙侠志怪（修仙宗门/古国王朝/山泽志怪/江湖/镖局/衙门/古寺）",
            "科幻深空/未来都市（空间站/外星殖民地/赛博都市/AI/仿生人/时间殖民）",
            "末日废土/灾变后（辐射区/避难所/废土聚落/幸存者部落/污染区）",
            "太空歌剧/异星文明（异星神殿/古老星舰/外星考古/星际教会/虚空航行）",
            "航海/极地/孤岛（鬼船/南极科考站/孤岛灯塔/深海潜艇/北极冰站）",
            "虚拟世界/游戏（VR MMO/SAO 类死亡游戏/NPC 觉醒/嵌套赛博空间/桌游成精）",
            "蒸汽朋克/柴油朋克（飞艇/齿轮都市/维多利亚工业/炼金工厂/殖民探险）",
            "魔法学院/西幻（魔法学校/巫师家族/精灵矮人古国/地下城/龙与神）",
            "宗教/修道院/审判（古修道院/十字军/异端审判/圣物/沙漠隐修会）",
            "黑色幽默/荒诞（明明很吓人但人物嘴贱/荒诞规则/喜剧式死亡，骨架仍要是克苏鲁）",
            "影视/文学/游戏同人克苏鲁化（把某个熟知的童话/童话反转/经典 IP 世界观重写成克苏鲁，但所有专名必须原创化）",
            "心理诡计/认知层（副本本身是某人的梦境/遗物/脑内世界/虚假记忆，身份错配/时间循环）",
            "童话反转/寓言（把童话/民间故事/寓言反向克苏鲁化，糖果屋/小红帽/睡美人/桃花源 等外壳，但专名必须换）",
            "梦境/潜意识层（梦域/清醒梦/集体潜意识/清醒入梦师/梦里的怪物）",
        ]

        def _run_one(i: int) -> tuple[int, Any]:
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n_val} 版改编中…", progress=5 + (i / n_val) * 85)
            # 从 flavor_pool 里按 i 轮转抽取，同时避开和原模组背景明显撞车的方向——
            # 这里不做硬过滤，让 prompt 里"行业去重/风格自主"规则兜底；只负责给一个"本次偏这方向"的锚点。
            flavor = flavor_pool[i % len(flavor_pool)]
            # 为每版再加一个"差异化维度"提示，强制拉开职业/地理/时代
            diff_axis = [
                "尽量把关键人物的职业与原模组错开，换到与其他版本不同的行业",
                "换一个与原模组完全不同的地理与时代背景，但保留核心钩子",
                "把核心反派/异常从「人形/组织」改成「非人存在/空间本身/规则本身」",
                "把叙事改成多视角/倒叙/嵌套/时间循环，保留同样的事件但打乱呈现顺序",
                "加入原创禁忌规则侧枝（至少 2 条原模组没有的小规则），让规则体系更厚",
            ][i % 5]
            this_hint = (
                hint
                + f"\n【本次风格定向·仅作方向提示，可以灵活偏离】请往「{flavor}」方向改编，但要自然融入，不要生硬套壳；同时：{diff_axis}。和同组其他版在题材/时代/地域/职业/文风上都要拉开差异，不要跟其他版本写同一个行业/同一座城市。"
            ).strip()
            try:
                prov = d.chore_provider("sushi")
                r = adapt_pdf_source(prov, ps, pdf_text, user_hint=this_hint)
                short_label = flavor.split("（", 1)[0]
                return i, {
                    "index": i,
                    "title": f"改编第{i+1}版·{short_label}",
                    "adapted_premise": r.adapted_premise,
                    "player_cards": list(r.player_cards),
                    "renamed_cast": list(r.renamed_cast),
                    "changed_elements": list(r.changed_elements),
                    "risk_flags": list(r.risk_flags),
                    "flavor": short_label,
                    "flavor_full": flavor,
                    "one_liner": r.adapted_premise[:80].replace("\n", " "),
                }
            except Exception as e:
                log.warning("adapt pdf5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e), "flavor": flavor.split("（",1)[0]}

        with ThreadPoolExecutor(max_workers=min(n_val, 3)) as ex:
            futures = {ex.submit(_run_one, i): i for i in range(n_val)}
            done_cnt = 0
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                if r and not r.get("error"):
                    job_store.append_sub_result(job.job_id, r)
                else:
                    errors.append(f"第{i+1}版失败：{r.get('error') if r else 'unknown'}")
                done_cnt += 1
                update(progress=5 + (done_cnt / n_val) * 90,
                       message=f"已完成 {done_cnt}/{n_val} 版")

        success = [r for r in results if r and not r.get("error")]
        if not success:
            raise RuntimeError("全部改编失败：" + "; ".join(errors))
        update(progress=100, message=f"完成 {len(success)}/{n_val} 版",
               result={"adaptations": success, "errors": errors})

    run_in_background(job_store, job.job_id, _work)
    return {"ok": True, "job_id": job.job_id, "job": job.as_dict()}


def _pick_adapt(pid: str, vol_no: int, body: _PickIn) -> dict:
    """POST 选定一版改编，写入 volume.adapted_module 并标记 adapt 阶段完成。"""
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)

    j = job_store.get(body.job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if j.kind != "pdf_adapt5":
        raise HTTPException(status_code=422, detail=f"任务类型 {j.kind} 不支持选版")
    if j.status != "done":
        raise HTTPException(status_code=422, detail=f"任务状态为 {j.status}，不能挑选")
    sid = _adapt_session_id(pid, vol_no)
    if j.session_id != sid:
        raise HTTPException(status_code=404, detail="任务不属于该卷")

    adaptations = []
    if isinstance(j.result, dict):
        adaptations = j.result.get("adaptations") or j.sub_results or []
    else:
        adaptations = j.sub_results or []
    idx = max(0, min(int(body.pick_index or 0), max(len(adaptations) - 1, 0)))
    picked = adaptations[idx] if idx < len(adaptations) else None
    if not picked:
        raise HTTPException(status_code=422, detail="选版结果为空")

    # 把整版 adaptation dict 存进去（含 premise/cast/renamed_cast/changed_elements/
    # risk_flags/flavor 等字段），后续跑团/魂穿/详设阶段都要用到 NPC 卡司。
    # 为了兼容后续消费点，统一把人物卡字段命名为 cast（剧本 NPC/KPC），同时保留
    # player_cards 作为别名，避免老消费点直接读 player_cards 时炸。
    picked = dict(picked or {})
    raw_cards = picked.get("player_cards") or []
    if not isinstance(raw_cards, list):
        raw_cards = []
    picked["cast"] = [c for c in raw_cards if isinstance(c, dict)]
    picked["player_cards"] = picked["cast"]  # 兼容老字段名
    # 确保 premise 字段存在
    if not picked.get("adapted_premise"):
        picked["adapted_premise"] = ""
    v.adapted_module = picked
    sc = dict(v.stage_completed or {})
    sc["adapt"] = True
    # 用户重新选版后，清掉 _view 覆盖，恢复正向阶段推进；同时把后续阶段的完成标记清掉，
    # 因为改编稿换了之后跑团/魂穿/详设产物都会失效，需要重新走。
    sc.pop("_view", None)
    for stage in ("simulate", "embody", "scaffold", "rhythm"):
        sc[stage] = False
    v.stage_completed = sc
    # 清掉跑团及之后的产物
    for attr in ("simulated_run", "host_character", "entry_point", "info_gap", "stance", "tangchi_story_outline", "deviation_points"):
        if hasattr(v, attr):
            try:
                setattr(v, attr, None)
            except Exception:
                pass
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


def _save_custom_adapt(pid: str, vol_no: int, body: _SaveCustomAdaptIn) -> dict:
    """POST 直接保存作者自定义的改编版本（跳过 job 选版流程）。"""
    vs, v = _get_volume_or_404(pid, vol_no)
    adapt_in = body.adaptation or {}
    premise = adapt_in.get("adapted_premise") or adapt_in.get("content") or ""
    if not premise:
        raise HTTPException(status_code=422, detail="adaptation.adapted_premise 不能为空")
    # 把作者提交的整份 adaptation dict 存进去（如果只传了 premise，也规整成 dict）
    if isinstance(adapt_in, dict):
        saved = dict(adapt_in)
    else:
        saved = {"adapted_premise": premise}
    saved["adapted_premise"] = premise
    raw_cards = saved.get("cast") or saved.get("player_cards") or []
    if not isinstance(raw_cards, list):
        raw_cards = []
    saved["cast"] = [c for c in raw_cards if isinstance(c, dict)]
    saved["player_cards"] = saved["cast"]  # 兼容老字段名
    v.adapted_module = saved
    sc = dict(v.stage_completed or {})
    sc["adapt"] = True
    sc.pop("_view", None)
    for stage in ("simulate", "embody", "scaffold", "rhythm"):
        sc[stage] = False
    v.stage_completed = sc
    for attr in ("simulated_run", "host_character", "entry_point", "info_gap", "stance", "tangchi_story_outline", "deviation_points"):
        if hasattr(v, attr):
            try:
                setattr(v, attr, None)
            except Exception:
                pass
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}
