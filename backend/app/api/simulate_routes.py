"""工作台 simulate 阶段路由：3 场并行 COC 跑团 → SSE 进度 → 单选/拼贴合成为主记录。

挂载方式：main.py 末尾调用
    from .simulate_routes import register_simulate_routes
    register_simulate_routes(app, require_project=..., volumes_for=..., project_dir=...,
                             book_for=..., fields_for=..., coc_run_for=..., field_run_for=...,
                             chore_provider=..., coc_runner_factory=_coc_runner_factory)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


# ---- 依赖注入容器 ----

@dataclass
class _Deps:
    require_project: Callable
    volumes_for: Callable[[str], Any]
    project_dir: Callable[[str], Any]
    book_for: Callable[[str], Any]
    fields_for: Callable[[str], Any]
    coc_run_for: Callable[[str], Any]
    field_run_for: Callable[[str], Any]
    chore_provider: Callable[[str], Any]
    coc_runner_factory: Callable[[], Any]  # 每线程一个全新 PipelineRunner，见 jobs_routes 模式


_deps: _Deps | None = None


def register_simulate_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    project_dir: Callable[[str], Any],
    book_for: Callable[[str], Any],
    fields_for: Callable[[str], Any],
    coc_run_for: Callable[[str], Any],
    field_run_for: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
    coc_runner_factory: Callable[[], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        project_dir=project_dir,
        book_for=book_for,
        fields_for=fields_for,
        coc_run_for=coc_run_for,
        field_run_for=field_run_for,
        chore_provider=chore_provider,
        coc_runner_factory=coc_runner_factory,
    )

    router = APIRouter()
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/start",
        _start_sim, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/jobs",
        _list_jobs, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/stream",
        _stream_jobs, methods=["GET"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/pick-one",
        _pick_one, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/collage",
        _collage, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/save-custom",
        _save_custom_variant, methods=["POST"],
    )
    router.add_api_route(
        "/api/projects/{pid}/workbench/volumes/{vol_no}/simulate/back-to-adapt",
        _back_to_adapt, methods=["POST"],
    )
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("simulate_routes 未调用 register_simulate_routes 初始化")
    return _deps


# ---- 请求体 ----

class _StartIn(BaseModel):
    user_directive: str = ""
    max_pairs_per_session: int = 6
    max_turns_per_session: int = 20
    use_mock: bool = False  # 显式要求 mock（测试/快速演示）；默认走真 LLM


class _PickOneIn(BaseModel):
    job_id: str
    pick_index: int


class _CollageSelection(BaseModel):
    section: str
    from_index: int
    note: str = ""


class _CollageIn(BaseModel):
    job_id: str
    collage: dict  # {base_index, selections:[...], user_note}


class _SaveCustomVariantIn(BaseModel):
    variant: dict  # 自定义 variant 对象，含 full_log/one_liner/label 等字段


# ---- 帮助函数 ----

def _get_volume_or_404(pid: str, vol_no: int):
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return vs, v


def _sim_session_id(pid: str, vol_no: int) -> str:
    return f"sim:{pid}:{vol_no}"


# ---- 路由处理 ----

def _list_jobs(pid: str, vol_no: int) -> dict:
    from ..jobs.store import job_store
    _get_volume_or_404(pid, vol_no)
    sid = _sim_session_id(pid, vol_no)
    jobs = job_store.list_session(sid)
    return {"jobs": [j.as_dict() for j in jobs]}


def _stream_jobs(pid: str, vol_no: int):
    from ..jobs.store import job_store, jobs_to_sse_event_list
    _get_volume_or_404(pid, vol_no)
    sid = _sim_session_id(pid, vol_no)

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


# ---- 启动 3 场并行 COC ----

def _start_sim(pid: str, vol_no: int, body: _StartIn) -> dict:
    from ..jobs.store import job_store, run_in_background
    import logging
    log = logging.getLogger("api.simulate")

    d = _must_deps()
    vs, v = _get_volume_or_404(pid, vol_no)
    if not v.adapted_module:
        raise HTTPException(status_code=422, detail="请先完成 PDF 改编")

    # 构造伪 source
    src = {
        "adapted_premise": v.adapted_module or "",
        "title": v.title or f"卷{vol_no}",
        "player_cards": (v.simulated_run.get("player_cards", []) if v.simulated_run else []),
    }

    player_cards = list(src.get("player_cards") or [])
    if len(player_cards) < 2:
        from ..fields.coc_run import make_player_card
        defaults = [
            make_player_card("林觉", "小报记者", san_max=65,
                             skills={"侦查": 60, "聆听": 55, "图书馆": 70, "话术": 50}),
            make_player_card("赵魁", "退伍兵", san_max=60, hp_max=14,
                             skills={"侦查": 45, "聆听": 40, "斗殴": 70, "急救": 50, "闪避": 55}),
        ]
        player_cards = (player_cards + defaults)[:4]

    # 跑团仿真先于魂穿：PL 均为外乡调查员（模组 NPC 故事里的人），
    # 本阶段不注入唐迟，也不允许把某张 PL 卡替换成唐迟。
    # 三场 PL 完全相同、模组/NPC/开场钩子完全相同；只通过独立 rng_seed + KP 自由发挥产生差异。
    from ..pipeline.coc_simulator import run_coc_parallel_batch
    import copy as _copy, random as _r
    _brng = _r.Random()
    NS = 3
    player_sets = [_copy.deepcopy(player_cards) for _ in range(NS)]
    variant_configs = []
    for i in range(NS):
        ss = _brng.randint(1, 2**30)
        variant_configs.append({"flavor": None, "label": f"平行宇宙 #{i+1}",
            "desc": f"同模组·同PL·骰子种子 {ss}；KP 自由长", "rng_seed": ss})

    book_store = d.book_for(pid)
    field_store = d.fields_for(pid)
    field_run_store = d.field_run_for(pid)
    coc_store = d.coc_run_for(pid)
    field_no = int(v.field_no or vol_no)

    adapt_parsed = {
        "vid": f"vol{vol_no}",
        "title": v.title or f"卷{vol_no}",
        "world_premise": v.adapted_module or "",
        "hook": (v.adapted_module or "")[:80],
        "entry_mood": "潮湿、不安",
    }

    sid = _sim_session_id(pid, vol_no)
    job = job_store.create(session_id=sid, kind="coc_batch", meta={
        "phase": "simulate",
        "total_sessions": 3,
        "flavors": [vc.get("flavor") for vc in variant_configs],
        "volume_no": vol_no,
    })
    _session_labels = [vc.get("label", f"第{i+1}场") for i, vc in enumerate(variant_configs)]

    progress_lock = threading.Lock()
    session_state = {i: {"day": 1, "turn": 0, "done": False, "finalizing": False} for i in range(3)}

    def _on_event(kind: str, payload: dict):
        with progress_lock:
            try:
                sess_idx = int(payload.get("session", 1)) - 1
                if not (0 <= sess_idx < NS):
                    return
                i = payload.get("session")
                _lab = _session_labels[sess_idx]
                ss = session_state[sess_idx]
                if kind == "coc_session_start":
                    ss["done"] = False
                    ss["finalizing"] = False
                    msg = f"第 {i}/3 场（{_lab}）开场…"
                elif kind in ("coc_session_turn", "coc_turn_done", "coc_turn_start"):
                    day = int(payload.get("day", payload.get("current_day", ss.get("day", 1)) or 1))
                    turn = int(payload.get("turn", payload.get("turn_no", ss.get("turn", 0)) or 0))
                    role = payload.get("role", "")
                    ss["day"] = day
                    ss["turn"] = turn
                    role_cn = "守密人推进" if role == "keeper" else ("调查员行动" if role == "investigator" else "推演中")
                    msg = f"第 {i}/3 场（{_lab}）阶段{day} · {role_cn} · 回合{turn}"
                elif kind == "coc_session_finalize":
                    ss["finalizing"] = True
                    msg = f"第 {i}/3 场（{_lab}）跑团结束，整理小说化梗概…"
                elif kind == "coc_session_done":
                    ss["done"] = True
                    ss["finalizing"] = False
                    msg = f"第 {i}/3 场（{_lab}）完成"
                elif kind == "coc_session_error":
                    msg = f"第 {i}/3 场出错：{str(payload.get('error',''))[:60]}"
                else:
                    msg = f"第 {i}/3 场（{_lab}）{kind}"
                done_cnt = sum(1 for vv in session_state.values() if vv["done"])
                # 进度按「已完成场 × 33% + 当前进行中场的回合进度」估算
                pct = done_cnt * 33.0
                for vv in session_state.values():
                    if not vv["done"]:
                        if vv["finalizing"]:
                            pct += 30
                        else:
                            pct += min(28, 4 + vv.get("turn", 0) * 2.2)
                        break
                pct = min(99, pct)
                job_store.update(job.job_id, progress=pct, message=msg)
            except Exception:
                pass

    # 捕获到闭包
    max_pairs = int(body.max_pairs_per_session or 6)
    max_turns = int(body.max_turns_per_session or 20)
    user_dir = body.user_directive or None
    use_mock = bool(body.use_mock)
    chore_provider = d.chore_provider

    # 进度计算：三场并行，每场 max_pairs*2 回合（KP+PL），我们按回合数估算进度
    _per_session_turns = max(1, max_pairs * 2 + 2)  # +2 for finalize start/done
    _total_turn_estimate = _per_session_turns * 3

    def _work(j, update):
        # ---- mock 路径：仅当前端显式 use_mock=true 时启用，默认走真 LLM ----
        if use_mock:
            update(progress=5, message="演示模式：跳过 LLM 直接生成 mock 结果…")
            time.sleep(1)
            variants = []
            import random as _mr
            _mock_bias = ["bad", "bittersweet", "tradeoff", "narrow_escape", "costly"]
            for i, lab in enumerate(_session_labels):
                bias = _mock_bias[i % len(_mock_bias)]
                v = {
                    "label": lab,
                    "ending_label": f"{lab}（演示）",
                    "index": i,
                    "one_liner": "【演示结果】mock 跑团，未调用真实 LLM；请关闭「快速演示」后重跑以获得真实剧情。",
                    "flavor": "mock",
                    "ending_bias": "mock",
                    "full_log": (
                        f"===== 跑团完整日志（演示）=====\n"
                        f"（此为演示占位内容，非真实 KP 产出。）\n\n"
                        f"===== 小说化梗概（演示）=====\n"
                        f"请关闭「高级选项 → 快速演示」后重新开跑，获得真实跑团日志与小说化梗概。"
                    ),
                }
                variants.append(v)
                job_store.append_sub_result(job.job_id, v)
                update(progress=30 * (i + 1), message=f"{lab} mock 完成")
                time.sleep(0.2)
            update(progress=100, message="3 场演示完成（mock）", result={"variants": variants})
            return {"variants": variants}

        # ---- 真实路径 ----
        # 每线程一个全新 PipelineRunner（coc_runner_factory 已由 main.py 注入）
        runner_factory = d.coc_runner_factory
        variants = run_coc_parallel_batch(
            runner_factory,
            book_store=book_store, field_store=field_store,
            field_no=field_no, coc_store=coc_store, field_run_store=field_run_store,
            adapt_parsed=adapt_parsed,
            player_sets=player_sets,
            variant_configs=variant_configs,
            max_pairs_per_session=max_pairs,
            max_turns_per_session=max_turns,
            on_event=_on_event,
            user_directive=user_dir,
            max_workers=3,
        )
        for i, v in enumerate(variants):
            # 优先用 LLM 跑出来的 ending_label，fallback 到 variant_config 的占位 label
            v["label"] = v.get("ending_label") or _session_labels[i] if i < len(_session_labels) else (v.get("ending_label") or f"第{i+1}场")
            v["flavor"] = v.get("ending_bias") or v.get("flavor")
            v["index"] = i
            job_store.append_sub_result(job.job_id, v)
        update(progress=100, message="3 场全部完成", result={"variants": variants})
        return {"variants": variants}

    run_in_background(job_store, job.job_id, _work)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}


# ---- 单选一场作为主记录 ----

def _pick_one(pid: str, vol_no: int, body: _PickOneIn) -> dict:
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _sim_session_id(pid, vol_no)

    j = job_store.get(body.job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if j.session_id != sid:
        raise HTTPException(status_code=404, detail="任务不属于该卷")
    if j.kind != "coc_batch":
        raise HTTPException(status_code=422, detail=f"任务类型 {j.kind} 不支持选版")
    if j.status != "done":
        raise HTTPException(status_code=422, detail=f"任务状态为 {j.status}，不能挑选")

    variants = []
    if isinstance(j.result, dict):
        variants = j.result.get("variants") or j.sub_results or []
    else:
        variants = j.sub_results or []
    idx = max(0, min(int(body.pick_index or 0), max(len(variants) - 1, 0)))
    picked = variants[idx] if idx < len(variants) else None
    if not picked:
        raise HTTPException(status_code=422, detail="选版结果为空")

    full_log = picked.get("full_log") or ""
    if not full_log:
        full_log = json.dumps(picked, ensure_ascii=False)

    main_record = {
        "mode": "pick_one",
        "picked_index": idx,
        "label": picked.get("label", ""),
        "summary": picked.get("one_liner", ""),
        "full_log": full_log,
        "selected_variant": picked,
        "collage": None,
    }
    v.simulated_run = main_record
    v.stage_completed["simulate"] = True
    # 选盘完成 → 清除阶段视图覆盖，恢复按真实进度判定（下一阶段是 embody）
    if isinstance(v.stage_completed, dict) and "_view" in v.stage_completed:
        v.stage_completed.pop("_view", None)
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


def _save_custom_variant(pid: str, vol_no: int, body: _SaveCustomVariantIn) -> dict:
    """POST 直接保存作者自定义的跑团 variant，跳过 job 选版。"""
    vs, v = _get_volume_or_404(pid, vol_no)
    variant = body.variant or {}
    full_log = variant.get("full_log") or variant.get("content") or ""
    if not full_log:
        raise HTTPException(status_code=422, detail="variant.full_log 不能为空")
    main_record = {
        "mode": "custom",
        "label": variant.get("label") or "自定义",
        "summary": variant.get("one_liner") or (full_log[:80].replace("\n", " ")),
        "full_log": full_log,
        "selected_variant": variant,
        "collage": None,
    }
    v.simulated_run = main_record
    v.stage_completed["simulate"] = True
    if isinstance(v.stage_completed, dict) and "_view" in v.stage_completed:
        v.stage_completed.pop("_view", None)
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


def _back_to_adapt(pid: str, vol_no: int) -> dict:
    """从 simulate 阶段回退到 adapt（剧本改编/选版）。

    语义：用户想回到「剧本改编」页面重新挑一版改编稿、或重新发起改编。
    为了不破坏已经产出的改编结果与后续阶段的进度标记，这里采用 _view 覆盖
    机制（与 back-to-simulate 一致）：
    - 不清空 v.adapted_module（用户可以在 adapt 页看到已有版本做对比）；
    - 不重置 stage_completed（adapt/simulate/embody/... 标记保持不变）；
    - 清空 v.simulated_run 与后续魂穿/详设产物（因为换改编稿之后跑团结果必然失效）；
    - 设置 sc["_view"] = "adapt"，让 /phase 直接返回 adapt 视图；
    - 标记该卷下 running/pending 的 coc_batch 任务为 cancelled；
    - 当用户在 adapt 页重新选版 / 保存自定义改编时，adapt_routes 会 pop _view，
      正常进入 simulate 阶段。
    """
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _sim_session_id(pid, vol_no)
    # 终止正在进行的跑团任务（避免后台继续写状态）
    try:
        for j in job_store.list_session(sid):
            if j.kind == "coc_batch" and j.status in ("pending", "running"):
                job_store.update(
                    j.job_id,
                    status="cancelled",
                    error="用户返回上一步（重选剧本），任务已取消",
                )
    except Exception:
        pass
    # 清空跑团结果以及跑团之后的魂穿产物（换改编稿后这些都失效）
    v.simulated_run = None
    sc = dict(v.stage_completed or {})
    # 用 _view 覆盖让前端回到 adapt 页，不硬重置各阶段完成标记
    sc["_view"] = "adapt"
    v.stage_completed = sc
    # 顺带清掉魂穿/详设等后续阶段的 in-memory 产物（若有字段，保持一致性）
    for attr in ("host_character", "entry_point", "info_gap", "stance", "tangchi_story_outline", "deviation_points"):
        if hasattr(v, attr):
            try:
                setattr(v, attr, None)
            except Exception:
                pass
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}


# ---- 拼贴合成 ----

def _collage(pid: str, vol_no: int, body: _CollageIn) -> dict:
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _sim_session_id(pid, vol_no)

    j = job_store.get(body.job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if j.session_id != sid:
        raise HTTPException(status_code=404, detail="任务不属于该卷")
    if j.kind != "coc_batch":
        raise HTTPException(status_code=422, detail=f"任务类型 {j.kind} 不支持拼贴")
    if j.status != "done":
        raise HTTPException(status_code=422, detail=f"任务状态为 {j.status}，不能拼贴")

    variants = []
    if isinstance(j.result, dict):
        variants = j.result.get("variants") or j.sub_results or []
    else:
        variants = j.sub_results or []

    collage = body.collage or {}
    base_idx = int(collage.get("base_index", 0) or 0)
    selections = collage.get("selections") or []
    user_note = collage.get("user_note", "") or ""

    if base_idx < 0 or base_idx >= len(variants):
        raise HTTPException(status_code=422, detail=f"基线索引越界：{base_idx}")
    base = variants[base_idx]

    def _variant_snippet(vv: dict) -> str:
        if vv.get("full_log"):
            return str(vv["full_log"])[:800]
        if vv.get("log_tail"):
            return str(vv.get("one_liner", "")) + "\n" + str(vv.get("log_tail", ""))[:700]
        return json.dumps(vv, ensure_ascii=False)[:800]

    lines: list[str] = []
    lines.append("==== 拼贴主跑团记录 ====")
    lines.append(f"基线：{base.get('label', f'第{base_idx+1}场')}")
    lines.append(f"基调：{base.get('one_liner', '')}")
    lines.append("")

    SECTION_LABELS = {
        "opening": "开场",
        "climax": "高潮",
        "death_1": "第一个死亡",
        "death_2": "第二个死亡",
        "ending": "结局",
        "npc_fates": "NPC 命运",
    }
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        sec = sel.get("section", "")
        fi = int(sel.get("from_index", 0) or 0)
        note = sel.get("note", "") or ""
        if fi < 0 or fi >= len(variants):
            continue
        vv = variants[fi]
        sec_label = SECTION_LABELS.get(sec, sec)
        lines.append(f"----- {sec_label}（取自 {vv.get('label', f'第{fi+1}场')}）-----")
        if note:
            lines.append(f"备注：{note}")
        lines.append(_variant_snippet(vv))
        lines.append("")

    if user_note:
        lines.append("----- 作者备注 -----")
        lines.append(user_note)
        lines.append("")

    merged_log = "\n".join(lines)

    main_record = {
        "mode": "collage",
        "base_index": base_idx,
        "selections": selections,
        "user_note": user_note,
        "summary": f"拼贴（基线{base_idx+1}，取{len(selections)}段）",
        "full_log": merged_log,
        "selected_variant": variants[base_idx],
        "collage": collage,
    }
    v.simulated_run = main_record
    v.stage_completed["simulate"] = True
    if isinstance(v.stage_completed, dict) and "_view" in v.stage_completed:
        v.stage_completed.pop("_view", None)
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}
