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
    """POST 标记某阶段已完成。"""
    if body.stage not in _ALLOWED_STAGES:
        raise HTTPException(status_code=422, detail=f"未知阶段：{body.stage}")
    vs, v = _get_volume_or_404(pid, vol_no)
    v.stage_completed[body.stage] = True
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
        flavor_list = ["民俗阴冷向", "规则冷峻向", "诡异日常向", "科幻/未知向", "心理诡计向",
                       "古风玄幻向", "同人改设向", "黑色幽默向"]

        def _run_one(i: int) -> tuple[int, Any]:
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n_val} 版改编中…", progress=5 + (i / n_val) * 85)
            flavor = flavor_list[i % len(flavor_list)]
            this_hint = (hint + f"\n【本次定向：请往「{flavor}」方向偏，但不要生硬，自然融入；和同组其他版拉开差异。】").strip()
            try:
                prov = d.chore_provider("sushi")
                r = adapt_pdf_source(prov, ps, pdf_text, user_hint=this_hint)
                return i, {
                    "index": i,
                    "title": f"改编第{i+1}版·{flavor}",
                    "adapted_premise": r.adapted_premise,
                    "player_cards": list(r.player_cards),
                    "renamed_cast": list(r.renamed_cast),
                    "changed_elements": list(r.changed_elements),
                    "risk_flags": list(r.risk_flags),
                    "flavor": flavor,
                    "one_liner": r.adapted_premise[:80].replace("\n", " "),
                }
            except Exception as e:
                log.warning("adapt pdf5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e), "flavor": flavor}

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

    v.adapted_module = picked.get("adapted_premise", "")
    v.stage_completed["adapt"] = True
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


def _save_custom_adapt(pid: str, vol_no: int, body: _SaveCustomAdaptIn) -> dict:
    """POST 直接保存作者自定义的改编版本（跳过 job 选版流程）。"""
    vs, v = _get_volume_or_404(pid, vol_no)
    adapt = body.adaptation or {}
    premise = adapt.get("adapted_premise") or adapt.get("content") or ""
    if not premise:
        raise HTTPException(status_code=422, detail="adaptation.adapted_premise 不能为空")
    v.adapted_module = premise
    v.stage_completed["adapt"] = True
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}
