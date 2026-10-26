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

import logging as _logging
_log = _logging.getLogger("api.simulate")


def _fallback_investigators(cast_cards: list[dict], seed: int = 0) -> list[dict]:
    """LLM 车卡失败时的兜底：用一组通用职业（避开 NPC 已用职业）的路人调查员。"""
    from ..fields.coc_run import make_player_card
    # 收集 NPC 已用职业关键词，尽量避开
    used_occs = set()
    for c in cast_cards or []:
        occ = str(c.get("occupation") or "")
        for kw in ("外卖", "骑手", "便利店", "夜班", "经理", "修锁", "物业", "维修", "记者", "警察", "医生", "学生", "教师"):
            if kw in occ:
                used_occs.add(kw)
    pools = [
        ("货运司机", {"驾驶": 70, "侦查": 40, "斗殴": 45, "聆听": 40}),
        ("夜班护士", {"急救": 65, "医学": 55, "聆听": 50, "心理学": 40}),
        ("短视频博主", {"侦查": 55, "话术": 60, "电子学": 50, "闪避": 40}),
        ("风水先生", {"神秘学": 60, "历史": 50, "侦查": 40, "话术": 45}),
        ("代驾司机", {"驾驶": 75, "聆听": 50, "侦查": 40, "斗殴": 40}),
        ("保险理赔员", {"话术": 60, "图书馆": 55, "侦查": 50, "心理学": 45}),
        ("退休教师", {"图书馆": 70, "历史": 60, "话术": 50, "聆听": 45}),
        ("流浪歌手", {"表演": 70, "话术": 55, "聆听": 50, "闪避": 45}),
    ]
    picks = []
    i = 0
    fallback_names = ["沈昼", "江窈", "贺鸣", "苏苓", "罗铮", "孟岑", "裴砚", "周絮"]
    for name, (occ, sk) in zip(fallback_names, pools):
        if i >= 3:
            break
        # 粗粒度避开：如果职业关键词和 NPC 撞车就跳过
        if any(k in occ for k in used_occs):
            continue
        picks.append(make_player_card(f"{name}", occ, san_max=60 + (i * 3) % 15,
                                       hp_max=10 + (i % 4), skills=sk))
        i += 1
    if len(picks) < 2:
        picks = [
            make_player_card("沈昼", "货运司机", san_max=62, hp_max=13,
                             skills={"驾驶": 70, "侦查": 40, "斗殴": 45, "聆听": 40}),
            make_player_card("江窈", "夜班护士", san_max=58, hp_max=10,
                             skills={"急救": 65, "医学": 55, "聆听": 50, "心理学": 40}),
        ]
    return picks


def _start_sim(pid: str, vol_no: int, body: _StartIn) -> dict:
    from ..jobs.store import job_store, run_in_background
    log = _log

    d = _must_deps()
    vs, v = _get_volume_or_404(pid, vol_no)
    if not v.adapted_module:
        raise HTTPException(status_code=422, detail="请先完成 PDF 改编")

    # 用 helper 从 adapted_module（可能是 dict 也可能是旧字符串）取出 premise 与 NPC 卡司
    from ..book.volume import adapted_premise_of, adapted_cast_of, adapted_meta_of
    premise_text = adapted_premise_of(v.adapted_module)
    cast_cards = adapted_cast_of(v.adapted_module)
    meta = adapted_meta_of(v.adapted_module)
    if not premise_text:
        raise HTTPException(status_code=422, detail="副本核文本为空，无法跑团")

    book_store = d.book_for(pid)
    field_store = d.fields_for(pid)
    field_run_store = d.field_run_for(pid)
    coc_store = d.coc_run_for(pid)
    field_no = int(v.field_no or vol_no)

    # adapt_parsed：把副本核 premise + NPC 卡司完整塞给 KP；NPC 卡司是 KP 必须严格使用的命名人物表
    adapt_parsed = {
        "vid": f"vol{vol_no}",
        "title": v.title or f"卷{vol_no}",
        "world_premise": premise_text,
        "hook": premise_text[:80],
        "entry_mood": meta.get("flavor") or "不安",
        "cast": cast_cards,  # 剧本 NPC/KPC 卡司（命名人物），KP 必须从这里取
    }

    sid = _sim_session_id(pid, vol_no)
    NS = 3
    job = job_store.create(session_id=sid, kind="coc_batch", meta={
        "phase": "simulate",
        "total_sessions": NS,
        "volume_no": vol_no,
    })
    _session_labels = [f"平行宇宙 #{i+1}" for i in range(NS)]
    # 三场差异化种子
    import random as _r
    _brng = _r.Random()
    variant_configs = []
    # **不再预设 flavor 基调**（方案 B）。旧实现给三场硬写
    # good/bad/distinct，等于提前钦定「这场必须幸运/必须惨」，
    # 既与剧本无关，又限制了 KP 的发挥空间。差异化改为由
    # LLM 基于副本核生成的支线设定（llm_seed）提供。
    for i in range(NS):
        ss = _brng.randint(1, 2**30)
        variant_configs.append({
            "flavor": None,
            "label": f"平行宇宙 #{i+1}",
            "desc": "同一副本核下的一条独立支线，差异化由 LLM 生成的支线设定提供",
            "rng_seed": ss,
        })

    progress_lock = threading.Lock()
    session_state = {i: {"day": 1, "turn": 0, "done": False, "finalizing": False} for i in range(3)}
    # delta 合并缓冲：{session: {"text": 未落库的尾巴}}。
    # 逐 token append 会让 live_log 迅速吃满配额，三场并发时只有第一场
    # 留下记录，后两场看起来像「从没开场」。攒够一段再落库即可。
    _DELTA_CHUNK_CHARS = 200
    _delta_buffers: dict[int, dict] = {}

    def _flush_delta_buffers() -> None:
        """把各场未落库的 delta 尾巴冲刷进 live_log。

        必须在批次结束前调用，否则每场最后一段输出会永久丢失。
        """
        with progress_lock:
            for sess, buf in list(_delta_buffers.items()):
                tail = buf.get("text") or ""
                if tail.strip():
                    job.append_live("delta", tail, session=sess)
                buf["text"] = ""

    def _on_event(kind: str, payload: dict):
        with progress_lock:
            try:
                # ---- LLM 流式输出实时透出（第四步）----
                # 作者等的是「模型此刻在干什么」。thinking 是模型的思考流，
                # delta 是正在吐的字——两者都直接落进 job.live_log，
                # SSE 每秒推一次，前端就能实时看到，不用干等。
                if kind == "thinking":
                    txt = str(payload.get("text") or "")
                    if txt.strip():
                        job.append_live("thinking", txt.rstrip()[-160:],
                                        session=int(payload.get("session", 1) or 1))
                    return
                if kind == "delta":
                    txt = str(payload.get("text") or "")
                    if txt.strip():
                        # **合并写入**：模型逐 token 吐字，逐条 append 会让
                        # live_log 瞬间膨胀到上限（单场 27+ 次只是小 prompt，
                        # 真实一拍能到几百次），三场并发时把配额吃光。
                        # 同一场的连续 delta 拼到同一行，超过阈值再封口。
                        sess = int(payload.get("session", 1) or 1)
                        _buf = _delta_buffers.setdefault(sess, {"text": ""})
                        _buf["text"] += txt
                        if len(_buf["text"]) >= _DELTA_CHUNK_CHARS:
                            job.append_live("delta", _buf["text"], session=sess)
                            _buf["text"] = ""
                    return
                # 每拍裁决结果：作者最想看的一行（动作 → 判定 → 后果）
                if kind == "coc_turn_done" and payload.get("role") == "beat":
                    act = str(payload.get("action") or "")
                    chk = str(payload.get("check") or "")
                    if act or chk:
                        job.append_live(
                            "verdict",
                            f"{payload.get('beat', '?')}拍 · {act}"
                            + (f"　[{chk}]" if chk else ""),
                            session=int(payload.get("session", 1) or 1),
                        )
                    return

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
        from ..pipeline.sushi_generator import generate_investigators
        from ..pipeline.coc_simulator import run_coc_parallel_batch, generate_branch_seeds
        from ..fields.coc_run import _normalize_player_cards
        import copy as _copy

        # 每场单独生成一组 PL 调查员卡：基于副本核 premise + NPC 卡司 + 本场 flavor，
        # 生成 2-4 张原创调查员（普通人/合理卷入/职业错开/不与 NPC 重名），
        # 让三场平行跑团真的拥有不同的人物组合（而不是同一组卡反复跑）。
        update(progress=2, message="正在为本场 3 盘分别生成 PL 调查员卡…")
        player_sets: list[list[dict]] = []
        player_sets_meta: list[dict] = []
        for i in range(NS):
            try:
                # 单独 provider 实例，避免并发时共享会话
                prov = chore_provider("sushi")
                gen = generate_investigators(
                    prov,
                    premise=premise_text,
                    cast=cast_cards,
                    flavor_hint=variant_configs[i].get("label") + " " + variant_configs[i].get("desc", ""),
                    session_index=i,
                    n_sessions=NS,
                )
                cards = _normalize_player_cards(gen.investigators)
                player_sets.append(cards)
                player_sets_meta.append({
                    "investigators": cards,
                    "team_composition_note": gen.team_composition_note,
                })
                update(progress=2 + (i + 1) * 2, message=f"第 {i+1}/3 盘 PL 卡生成完毕（{len(cards)} 位调查员）")
            except Exception as e:
                log.warning("generate_investigators #%d failed: %s；回退到按副本职业派生卡", i, e)
                # 回退：基于 premise 自动派生一组路人调查员（职业不与 NPC 重名）
                fallback = _fallback_investigators(cast_cards, i)
                player_sets.append(fallback)
                player_sets_meta.append({"investigators": fallback, "team_composition_note": "（回退卡）"})

        # 方案 B：开场前用一次 LLM 调用，基于副本核生成三条支线的差异化设定。
        #
        # 为什么在这里而不是每场内部：三条线必须**互相可见地不同**，如果各场
        # 独立生成，模型很可能给出三个雷同方案。集中生成一次才能保证分化。
        #
        # 失败不阻塞：generate_branch_seeds 内部兜底返回空素材 seed，
        # 三场会退化成「同源 + 靠骰子分化」，仍可用。
        update(progress=8, message="正在为三条支线生成差异化设定…")
        try:
            branch_seeds = generate_branch_seeds(
                _coc_runner_factory(),
                adapt_parsed,
                n_branches=NS,
                player_sets_meta=player_sets_meta,
            )
        except Exception as e:
            log.warning("generate_branch_seeds 失败：%s；退回无素材种子", e)
            branch_seeds = []
        for i, bs in enumerate(branch_seeds):
            variant_configs[i]["llm_seed"] = bs
            # 三栏标签直接用 LLM 给的线名，作者一眼能分清三条线
            if bs.get("ending_label") and bs.get("source") == "llm":
                variant_configs[i]["label"] = bs["ending_label"]
            elif bs.get("core_twist"):
                variant_configs[i]["label"] = f"支线 {i+1}"
        log.info("支线种子来源：%s",
                 [b.get("source") for b in branch_seeds] or ["none"])
        update(progress=8, message="三条支线设定已就绪，开始跑团…")

        # 把本场 PL 组合注入 adapt_parsed（每场 copy 一份，避免并行相互污染）
        runner_factory = d.coc_runner_factory
        variants = run_coc_parallel_batch(
            runner_factory,
            book_store=book_store, field_store=field_store,
            field_no=field_no, coc_store=coc_store, field_run_store=field_run_store,
            adapt_parsed=adapt_parsed,
            player_sets=player_sets,
            player_sets_meta=player_sets_meta,
            variant_configs=variant_configs,
            max_pairs_per_session=max_pairs,
            max_turns_per_session=max_turns,
            on_event=_on_event,
            user_directive=user_dir,
            max_workers=3,
        )
        # 三场跑完，先把各场delta 尾巴冲进 live_log——
        # 否则每场最后一段输出永远留在缓冲里，作者看不到收尾。
        _flush_delta_buffers()
        for i, v in enumerate(variants):
            # 优先用 LLM 跑出来的 ending_label，fallback 到 variant_config 的占位 label。
            #
            # **不要把 fallback 写成三元表达式**：
            # `a or b if c else d` 的实际结构是 `(a or b) if c else d`，
            # 三元优先级高于 or，写在一起极易读错并埋下坑。这里显式分行。
            label = v.get("ending_label") or ""
            if not label:
                if i < len(_session_labels):
                    label = _session_labels[i]
                else:
                    label = f"平行宇宙 #{i + 1}"
            v["label"] = label
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
    # 原剧本归一化：选定这一刻就把三份形态收成一份权威结构，
    # 让下游（正文注入/记忆碎片生成/前端展示）只读 origin_novel 一个字段。
    # 旧卷没有这一列，读时 origin_novel_of() 会从 selected_variant 惰性重建。
    from ..book.volume import build_origin_novel
    v.origin_novel = build_origin_novel(picked, mode="pick_one", full_log=full_log)
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
    from ..book.volume import build_origin_novel
    v.origin_novel = build_origin_novel(variant, mode="custom", full_log=full_log)
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
    v.origin_novel = None      # 由旧改编稿跑出来的原剧本，跟着一起失效
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

    # 默认段落标签（兼容旧前端固定 6 段）；新前端允许作者自定义增删/重命名段落，
    # 会在 sel 里带 label 字段，下面会优先用它覆盖这里的标签。
    SECTION_LABELS = {
        "opening": "开场",
        "hook": "钩子 / 异兆初现",
        "clue_gather": "线索汇集",
        "first_death": "第一起死亡 / 第一个牺牲",
        "mid_reveal": "中段反转",
        "climax": "高潮 / 真相揭露",
        "escape": "逃出生天 / 代价结算",
        "ending": "结局",
        "npc_fates": "NPC 命运总览",
        "aftermath": "余波 / 尾声",
    }
    for sel in selections:
        if not isinstance(sel, dict):
            continue
        sec = sel.get("section", "")
        fi = int(sel.get("from_index", 0) or 0)
        note = sel.get("note", "") or ""
        # 作者自定义段落名优先；没有则查预设字典；都没有就直接用 sec key
        sec_label = sel.get("label") or SECTION_LABELS.get(sec, sec) or sec or "段落"
        if fi < 0 or fi >= len(variants):
            continue
        vv = variants[fi]
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
    # 拼贴的正文在 full_log（人工拼的段落），但 selected_variant 是基线那一版。
    # 归一化时把 merged_log 当 full_log 传下去，好让 summary 兜底能取到梗概段。
    from ..book.volume import build_origin_novel
    v.origin_novel = build_origin_novel(variants[base_idx], mode="custom",
                                        full_log=merged_log)
    v.stage_completed["simulate"] = True
    if isinstance(v.stage_completed, dict) and "_view" in v.stage_completed:
        v.stage_completed.pop("_view", None)
    vs.save(v)
    return {"ok": True, "volume": v.as_dict()}
