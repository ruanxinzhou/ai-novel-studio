"""长耗时后台任务路由：PDF×5 / COC×3 / Outline×5 + SSE 进度推送。

这些路由和 main.py 共享 app 句柄，挂载方式：在 main.py 末尾调用
`register_job_routes(app, deps)` 即可，避免在 main.py 里塞太多长任务代码。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


# ---- 依赖注入容器（由 main.py 注册时传入）----

@dataclass
class _Deps:
    require_project: Callable
    sessions_for: Callable
    fields_for: Callable
    field_run_for: Callable
    coc_run_for: Callable
    book_for: Callable
    chore_provider: Callable       # (tag) -> Provider
    coc_runner: Callable[[], Any]   # () -> Provider，每次返回新实例（线程安全）
    prompt_store: Any
    json_dumps: Callable


_deps: _Deps | None = None


def register_job_routes(
    app,
    *,
    require_project: Callable,
    sessions_for: Callable,
    fields_for: Callable,
    field_run_for: Callable,
    coc_run_for: Callable,
    book_for: Callable,
    chore_provider: Callable,
    coc_runner_factory: Callable[[], Any],
    prompt_store: Any,
    json_dumps: Callable = json.dumps,
) -> None:
    """注册作业相关路由到传入的 FastAPI app 上（用装饰器挂载，正确支持 UploadFile）。"""
    global _deps
    _deps = _Deps(
        require_project=require_project,
        sessions_for=sessions_for,
        fields_for=fields_for,
        field_run_for=field_run_for,
        coc_run_for=coc_run_for,
        book_for=book_for,
        chore_provider=chore_provider,
        coc_runner=coc_runner_factory,
        prompt_store=prompt_store,
        json_dumps=json_dumps,
    )

    # 用装饰器把所有路由挂到 app 上
    app.get("/api/projects/{pid}/sessions/{sid}/jobs")(_list_jobs)
    app.get("/api/projects/{pid}/sessions/{sid}/jobs/stream")(_stream_jobs)
    # pdf5 需要 multipart 文件上传，直接在 main.py 里挂（避免本模块再 import File/Form 不生效的问题）
    app.post("/api/projects/{pid}/sessions/{sid}/jobs/coc")(_start_coc)
    app.post("/api/projects/{pid}/sessions/{sid}/jobs/outline5")(_start_outline5)
    app.post("/api/projects/{pid}/sessions/{sid}/jobs/{job_id}/cancel")(_cancel_job)
    app.post("/api/projects/{pid}/sessions/{sid}/jobs/{job_id}/pick")(_pick_job_result)


def register_pdf5_route(app) -> None:
    """pdf5 因为依赖 UploadFile+Form，单独暴露一个注册函数（调用方传 app 即可）。"""
    app.post("/api/projects/{pid}/sessions/{sid}/jobs/pdf5")(_start_pdf5)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("jobs_routes 未调用 register_job_routes 初始化")
    return _deps


# ---- 帮助函数 ----

def _sse_line(event: str, data: Any) -> str:
    d = _must_deps()
    return f"event: {event}\ndata: {d.json_dumps(data, ensure_ascii=False)}\n\n"


def _get_session(pid: str, sid: str):
    d = _must_deps()
    d.require_project(pid)
    ss = d.sessions_for(pid)
    s = ss.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ss, s


# ---- 路由 ----

def _list_jobs(pid: str, sid: str) -> dict:
    from ..jobs import job_store
    _get_session(pid, sid)
    jobs = job_store.list_session(sid)
    return {"ok": True, "jobs": [j.as_dict() for j in jobs]}


def _stream_jobs(pid: str, sid: str):
    """SSE：持续推送该 session 的 job 更新，直到最近一个 running/pending 任务结束或 10 分钟无事件。"""
    from ..jobs import job_store, job_to_sse_event
    _get_session(pid, sid)

    def gen():
        # 先推一次当前状态
        jobs = job_store.list_session(sid)
        yield _sse_line("job_init", [j.as_dict() for j in jobs])
        # 简单轮询（每秒一次）——避免引入 asyncio，且后台任务本身是线程模型
        idle_rounds = 0
        while True:
            time.sleep(1.0)
            jobs = job_store.list_session(sid)
            any_active = any(j.status in ("pending", "running") for j in jobs)
            changed = False
            for j in jobs:
                if j.started_at and not getattr(j, "_pushed", False):
                    yield job_to_sse_event(j)
                    j._pushed = True
                    changed = True
            if not any_active:
                idle_rounds += 1
                if idle_rounds >= 3:
                    yield _sse_line("job_idle", {"ok": True})
                    return
            else:
                idle_rounds = 0
            if not changed:
                # 心跳
                yield ": keepalive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


def _cancel_job(pid: str, sid: str, job_id: str) -> dict:
    from ..jobs import job_store
    _get_session(pid, sid)
    j = job_store.get(job_id)
    if j is None or j.session_id != sid:
        raise HTTPException(status_code=404, detail="任务不存在")
    job_store.update(job_id, status="cancelled", message="用户取消")
    return {"ok": True, "job": job_store.get(job_id).as_dict()}


class _PickIn(BaseModel):
    pick_index: int
    extra: dict | None = None


def _pick_job_result(pid: str, sid: str, job_id: str, body: _PickIn) -> dict:
    """作者选定某个 job 的某个结果（pdf5/ coc/ outline5），把它写入 session 对应 artifacts/outputs。"""
    from ..jobs import job_store
    ss, s = _get_session(pid, sid)
    j = job_store.get(job_id)
    if j is None or j.session_id != sid:
        raise HTTPException(status_code=404, detail="任务不存在")
    if j.status != "done":
        raise HTTPException(status_code=422, detail=f"任务状态为 {j.status}，不能挑选")
    sub = j.sub_results
    full = j.result
    if not isinstance(full, dict) and not isinstance(sub, list):
        raise HTTPException(status_code=422, detail="任务结果为空")

    idx = max(0, min(int(body.pick_index or 0), max(len(sub) - 1, 0)))

    # 按 kind 做不同的落盘
    if j.kind == "pdf_adapt5":
        picked = sub[idx] if idx < len(sub) else {}
        s.artifacts["selected_pdf_adapt"] = picked
        s.add_message("ai", f"已选定第 {idx+1} 版改编：{picked.get('title','')}\n{picked.get('adapted_premise','')[:120]}…",
                      kind="gate", meta={"phase": "kindle", "action": "pick_pdf_adapt", "index": idx})
    elif j.kind == "coc_batch":
        picked = sub[idx] if idx < len(sub) else {}
        s.artifacts["selected_coc_variant"] = picked
        s.artifacts["coc_variants"] = list(sub)
        s.add_message("ai", f"已选定第 {idx+1} 场跑团走向（{picked.get('label',picked.get('title',''))}）：{picked.get('one_liner','')}",
                      kind="gate", meta={"phase": "scaffold", "action": "pick_coc_variant", "index": idx})
    elif j.kind == "outline5":
        picked = sub[idx] if idx < len(sub) else {}
        s.artifacts["selected_outline"] = picked
        s.add_message("ai", f"已选定第 {idx+1} 版唐迟视角大纲：{picked.get('title','')}\n{picked.get('one_liner','')[:120]}…",
                      kind="gate", meta={"phase": "scaffold", "action": "pick_outline", "index": idx})
    else:
        raise HTTPException(status_code=422, detail=f"不支持的 job kind：{j.kind}")

    ss.save(s)
    return {"ok": True, "session": s.as_dict(), "picked": picked}


# ============================================================
# PDF × 5 并行改编
# ============================================================

class _Pdf5In(BaseModel):
    user_hint: str = ""
    n: int = 5


def _start_pdf5(pid: str, sid: str, body: _Pdf5In, file: UploadFile = File(...)) -> dict:
    from ..jobs import job_store, run_in_background
    from ..pipeline.sushi_generator import extract_pdf_text, adapt_pdf_source
    import logging
    log = logging.getLogger("api.jobs")
    ss, s = _get_session(pid, sid)
    if s.current_phase != "kindle":
        raise HTTPException(status_code=422, detail="PDF 5版改编只在副本点火阶段可用")

    fname = (file.filename or "").lower()
    if not fname.endswith(".pdf"):
        raise HTTPException(status_code=422, detail="仅支持 .pdf 文件")
    pdf_bytes = file.file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="PDF 过大（>20MB）")

    # 抽文本在主线程做（快），改编并行跑
    try:
        pdf_text, _ = extract_pdf_text(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF 解析失败：{e}")
    if len(pdf_text.strip()) < 100:
        raise HTTPException(status_code=422, detail="提取出的文本太短（扫描版需 OCR 或文件损坏）")

    n = max(2, min(8, int(body.n or 5)))
    d = _must_deps()
    job = job_store.create(sid, "pdf_adapt5", meta={
        "filename": file.filename, "n": n,
        "phase": "kindle",
    })

    ps = d.prompt_store
    user_hint = body.user_hint or ""

    def _work(j, update):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        d2 = _must_deps()
        results = [None] * n
        errors = []
        flavor_pool = [
            "中式乡土民俗阴冷", "现代都市规则冷峻", "当代诡异日常",
            "西方哥特维多利亚", "美式小镇/西部荒野", "日式怪谈/校园",
            "东方古风仙侠志怪", "科幻深空/未来都市", "末日废土/灾变后",
            "太空歌剧/异星文明", "航海/极地/孤岛", "虚拟世界/游戏",
            "蒸汽朋克/柴油朋克", "魔法学院/西幻", "宗教/修道院/审判",
            "黑色幽默/荒诞", "同人克苏鲁化", "心理诡计/认知层",
            "童话反转/寓言", "梦境/潜意识层",
        ]
        diff_axes = [
            "尽量把关键人物的职业与原模组错开，换到与其他版本不同的行业",
            "换一个与原模组完全不同的地理与时代背景，但保留核心钩子",
            "把核心反派/异常从「人形/组织」改成「非人存在/空间本身/规则本身」",
            "把叙事改成多视角/倒叙/嵌套/时间循环，保留同样的事件但打乱呈现顺序",
            "加入原创禁忌规则侧枝（至少 2 条原模组没有的小规则），让规则体系更厚",
        ]

        def _run_one(i: int) -> tuple[int, Any]:
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n} 版改编中…", progress=5 + (i / n) * 85)
            try:
                flavor = flavor_pool[i % len(flavor_pool)]
                diff = diff_axes[i % len(diff_axes)]
                hint = (user_hint + f"\n【本次风格定向·仅作方向提示，可以灵活偏离】请往「{flavor}」方向改编，但要自然融入，不要生硬套壳；同时：{diff}。和同组其他版在题材/时代/地域/职业/文风上都要拉开差异，不要跟其他版本写同一个行业/同一座城市。").strip()
                prov = d2.chore_provider("sushi")
                r = adapt_pdf_source(prov, ps, pdf_text, user_hint=hint)
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
                log.warning("pdf5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e), "flavor": flavor}

        with ThreadPoolExecutor(max_workers=min(n, 3)) as ex:
            futures = {ex.submit(_run_one, i): i for i in range(n)}
            done_cnt = 0
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                if r and not r.get("error"):
                    job_store.append_sub_result(job.job_id, r)
                else:
                    errors.append(f"第{i+1}版失败：{r.get('error') if r else 'unknown'}")
                done_cnt += 1
                update(progress=5 + (done_cnt / n) * 90,
                       message=f"已完成 {done_cnt}/{n} 版")

        success = [r for r in results if r and not r.get("error")]
        if not success:
            raise RuntimeError("全部改编失败：" + "; ".join(errors))
        update(progress=100, message=f"完成 {len(success)}/{n} 版",
               result={"adaptations": success, "errors": errors})

    run_in_background(job_store, job.job_id, _work)
    s.add_message("system", f"开始 PDF 5 版并行改编（job {job.job_id}）…", kind="gate")
    ss.save(s)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}


# ============================================================
# COC 3 场并行（2 同 1 异）
# ============================================================

class _CocIn(BaseModel):
    tangchi_replace_idx: int | None = None   # 唐迟替换 player_cards 第几张（None=不替换为唐迟，让所有 PL 都是原创人物，后续大纲阶段再决定唐迟替换谁）
    user_directive: str = ""
    source: str | None = None                # 指定用哪个改编稿；默认用 artifacts["selected_pdf_adapt"]
    max_pairs_per_session: int = 6
    max_turns_per_session: int = 20


def _start_coc(pid: str, sid: str, body: _CocIn) -> dict:
    from ..jobs import job_store, run_in_background
    import logging
    log = logging.getLogger("api.jobs")
    ss, s = _get_session(pid, sid)
    if s.current_phase != "scaffold":
        raise HTTPException(status_code=422, detail="COC 三场模拟只在跑团与大纲阶段可用")

    d = _must_deps()

    # 选定改编稿
    src = None
    if body.source:
        # 允许直接传 player_cards + adapted_premise
        try:
            src = json.loads(body.source)
        except Exception:
            pass
    if src is None:
        src = s.artifacts.get("selected_pdf_adapt") or s.artifacts.get("pdf_adapt")
    if not src or not src.get("adapted_premise"):
        raise HTTPException(status_code=422, detail="请先在点火阶段完成 PDF 改编并选定一版，或直接提供改编稿")

    player_cards = list(src.get("player_cards") or [])
    if len(player_cards) < 2:
        # 补默认卡
        from ..fields.coc_run import make_player_card
        defaults = [
            make_player_card("林觉", "小报记者", san_max=65, skills={"侦查":60,"聆听":55,"图书馆":70,"话术":50}),
            make_player_card("赵魁", "退伍兵", san_max=60, hp_max=14, skills={"侦查":45,"聆听":40,"斗殴":70,"急救":50,"闪避":55}),
        ]
        player_cards = (player_cards + defaults)[:4]

    from ..pipeline.coc_simulator import build_2same_1different_configs, run_coc_parallel_batch
    player_sets, variant_configs = build_2same_1different_configs(
        player_cards, tangchi_replace_idx=body.tangchi_replace_idx
    )

    book_store = d.book_for(pid)
    field_store = d.fields_for(pid)
    field_run_store = d.field_run_for(pid)
    coc_store = d.coc_run_for(pid)
    field_no = int(s.field_no or 1)

    adapt_parsed = {
        "vid": "custom",
        "title": src.get("title", "本场副本"),
        "world_premise": src.get("adapted_premise", ""),
        "hook": (src.get("changed_elements") or [""])[0] or src.get("adapted_premise", "")[:80],
        "entry_mood": "潮湿、不安",
    }

    job = job_store.create(sid, "coc_batch", meta={
        "phase": "scaffold",
        "total_sessions": 3,
        "flavors": [vc.get("flavor") for vc in variant_configs],
    })
    labels = ["全员殒落线", "一人独活线", "真相污染线"]

    progress_lock = threading.Lock()
    session_state = {i: {"day": 1, "turn": 0, "done": False} for i in range(3)}

    def _on_event(kind: str, payload: dict):
        with progress_lock:
            sess_idx = int(payload.get("session", 1)) - 1
            i = payload.get("session")
            if kind == "coc_session_start":
                msg = f"第 {i}/3 场（{labels[sess_idx]}）开场…"
            elif kind == "coc_session_turn":
                day = int(payload.get("day", payload.get("current_day", 1) or 1))
                turn = int(payload.get("turn", 0))
                session_state[sess_idx]["day"] = day
                session_state[sess_idx]["turn"] = turn
                msg = f"第 {i}/3 场（{labels[sess_idx]}）Day{day} 推演中…"
            elif kind == "coc_session_done":
                session_state[sess_idx]["done"] = True
                msg = f"第 {i}/3 场（{labels[sess_idx]}）完成"
            elif kind == "coc_session_error":
                msg = f"第 {i}/3 场出错：{payload.get('error','')[:40]}"
            else:
                msg = kind
            # 汇总三厂进度
            done_cnt = sum(1 for v in session_state.values() if v["done"])
            total_turns = sum(v["turn"] for v in session_state.values())
            # 粗略百分比：每场 0-30% 开场、30-90% 跑回合、90-100% 收尾
            base = done_cnt * 30
            in_flight = min(len(session_state) - done_cnt, 1)
            in_flight_pct = 0
            for v in session_state.values():
                if not v["done"]:
                    in_flight_pct = 20 + v["turn"] * 3  # 每回合+3%，最多60%
                    break
            pct = min(99, base + in_flight_pct)
            job_store.update(job.job_id, progress=pct, message=msg)

    def _work(j, update):
        d2 = _must_deps()
        # runner 工厂：线程内独立 provider
        def _factory():
            return d2.chore_provider("coc")
        variants = run_coc_parallel_batch(
            _factory,
            book_store=book_store, field_store=field_store,
            field_no=field_no, coc_store=coc_store, field_run_store=field_run_store,
            adapt_parsed=adapt_parsed,
            player_sets=player_sets,
            variant_configs=variant_configs,
            max_pairs_per_session=body.max_pairs_per_session,
            max_turns_per_session=body.max_turns_per_session,
            on_event=_on_event,
            user_directive=body.user_directive or None,
            max_workers=3,
        )
        # 打标签
        for i, v in enumerate(variants):
            v["label"] = labels[i] if i < len(labels) else f"第{i+1}场"
            v["index"] = i
            job_store.append_sub_result(job.job_id, v)
        job_store.update(job.job_id, progress=100, message="3 场全部完成",
                         result={"variants": variants})

    run_in_background(job_store, job.job_id, _work)
    s.add_message("system", f"开始并行跑 3 场 COC 模拟（2同1异，job {job.job_id}），请稍候…", kind="gate")
    ss.save(s)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}


# ============================================================
# 唐迟大纲 × 5 并行
# ============================================================

class _Outline5In(BaseModel):
    tangchi_role: str = ""          # 唐迟替换谁（一句话描述）
    must_have: list[str] = []       # 必现情节/伏笔
    user_directive: str = ""
    selected_coc_idx: int | None = None
    n: int = 5


_OUTLINE5_SYSTEM = (
    "你是《第七日的亡者》长篇小说专职大纲写手。基于给定的副本骨架+选定的跑团走向+唐迟替换角色，"
    "产出一版唐迟视角的七天求生追查死因破局大纲。大纲是成品可读的梗概，不是分点清单，要能让作者直接按它写章节细纲。\n\n"
    "【双轨时间线·铁律】\n"
    "- 副本原剧本是独立故事，有自己的时间跨度（几小时/几天/几个月/几代人均可），不要求七日结构；\n"
    "- day_breakdown 的 day=1..7 指唐迟入戏第1-7天（他自己的T-6→T-0倒计时），不是原剧本的Day1-Day7；\n"
    "- 两条时间线并行，每天要写清「原剧本此刻正在哪个事件」。\n\n"
    "大纲必须包含：\n"
    "①title：12 字内标题，定这一版的节奏/气质；\n"
    "②one_liner：一句话梗概（40 字内）；\n"
    "③pacing：节奏标签（慢热铺陈/反转密集/开场高能/先甜后刀/步步紧逼 等，可自造）；\n"
    "④original_module_timeline_note：原剧本时间跨度与入戏点说明（30-80字）；\n"
    "⑤tangchi_entry：唐迟在这版里替换谁、开场身份是什么、T-0死法如何破/被破、此刻原剧本在哪个事件；\n"
    "⑥day_breakdown：[入戏第1-7天] 每天 80-150 字梗概（含original_timeline_anchor字段说明原剧本此刻事件），写清当天关键行动/转折/死人/线索揭示/代价；\n"
    "⑦climax：T-0 高潮 100-200 字（怎么破局/死不死/谁活下来/代价是什么）；\n"
    "⑧blue_star_hook：杀青后带回什么/蓝星小钩子 50 字；\n"
    "⑨foreshadow_planted：本场埋下的长线伏笔列表（3-5 条，供卷规划参考）。\n\n"
    "输出严格 JSON：{title,one_liner,pacing,original_module_timeline_note,tangchi_entry,"
    "day_breakdown:[{day,summary,original_timeline_anchor}],climax,blue_star_hook,foreshadow_planted:[str]}。"
    "不要输出 JSON 之外的任何文字。"
)


def _start_outline5(pid: str, sid: str, body: _Outline5In) -> dict:
    from ..jobs import job_store, run_in_background
    from ..generation import complete_structured
    import logging
    log = logging.getLogger("api.jobs")
    ss, s = _get_session(pid, sid)
    if s.current_phase != "scaffold":
        raise HTTPException(status_code=422, detail="唐迟大纲 5 版只在跑团与大纲阶段可用")
    d = _must_deps()

    # 找选定改编稿 + 选定 coc variant
    adapt = s.artifacts.get("selected_pdf_adapt") or s.artifacts.get("pdf_adapt")
    if not adapt:
        raise HTTPException(status_code=422, detail="请先完成 PDF 改编")
    coc_variants = s.artifacts.get("coc_variants") or []
    sel_coc_idx = body.selected_coc_idx
    if sel_coc_idx is None:
        sel_coc_idx = 0
        if s.artifacts.get("selected_coc_variant"):
            for i, v in enumerate(coc_variants):
                if v.get("vid") == s.artifacts["selected_coc_variant"].get("vid"):
                    sel_coc_idx = i
                    break
    sel_coc = coc_variants[sel_coc_idx] if 0 <= sel_coc_idx < len(coc_variants) else {}

    n = max(2, min(8, int(body.n or 5)))
    job = job_store.create(sid, "outline5", meta={"phase": "scaffold", "n": n})

    # 构造共用 context
    context_parts = [
        "===== 副本骨架（选定改编稿）=====",
        adapt.get("adapted_premise", ""),
    ]
    if sel_coc:
        context_parts.append("\n===== 选定跑团走向梗概 =====\n")
        context_parts.append(f"标签：{sel_coc.get('label','')}")
        context_parts.append(f"一句话：{sel_coc.get('one_liner','')}")
        db = sel_coc.get("day_breakdown") or []
        for d_entry in db:
            if isinstance(d_entry, dict):
                context_parts.append(f"原剧本时点{d_entry.get('day','?')}：{d_entry.get('summary','')}")
        if sel_coc.get("final_summary"):
            context_parts.append(f"\n结局：{sel_coc['final_summary'][:600]}")
    if body.tangchi_role:
        context_parts.append(f"\n===== 唐迟替换角色（作者指定）=====\n{body.tangchi_role}")
    if body.must_have:
        context_parts.append("\n===== 必现情节/伏笔（作者指定，必须全部出现）=====\n" + "\n".join(f"- {x}" for x in body.must_have))
    if body.user_directive:
        context_parts.append(f"\n===== 作者额外要求 =====\n{body.user_directive}")
    context_text = "\n".join(context_parts)

    pacing_seeds = ["慢热铺陈·后劲刀", "反转密集·层层翻盘", "开场高能·步步紧逼",
                    "先甜后刀·情感重击", "诡计误导·身份错配", "生存紧逼·每天都死人",
                    "探索解谜·智斗破局", "黑色幽默·荒诞收束"]

    def _work(j, update):
        d2 = _must_deps()
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = [None] * n

        def _run_one(i: int):
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n} 版大纲生成中…", progress=5 + (i / n) * 85)
            flavor = pacing_seeds[i % len(pacing_seeds)]
            messages = [
                {"role": "system", "content": _OUTLINE5_SYSTEM},
                {"role": "user", "content": context_text + f"\n\n【本版节奏定向】请按「{flavor}」的节奏风格来写，让这一版和其他版有明显差别。注意：唐迟可以替换任意角色（PL/NPC/Boss/幕后），允许诡计副本/身份错配/虚假记忆；唐迟个人T-7→T-0倒计时锚必须清晰，同时写清原剧本时间线并行进展。"},
            ]
            try:
                def _call():
                    return d2.chore_provider("outline").chat(messages).text
                comp = complete_structured(_call, max_regenerations=2)
                data = comp.data if isinstance(comp.data, dict) else {}
                data.setdefault("title", f"大纲第{i+1}版·{flavor}")
                data.setdefault("pacing", flavor)
                data.setdefault("one_liner", "")
                data["index"] = i
                data["flavor"] = flavor
                return i, data
            except Exception as e:
                log.warning("outline5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e), "title": f"大纲第{i+1}版（失败）", "flavor": flavor}

        with ThreadPoolExecutor(max_workers=min(n, 3)) as ex:
            futures = {ex.submit(_run_one, i): i for i in range(n)}
            done_cnt = 0
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                if r and not r.get("error"):
                    job_store.append_sub_result(job.job_id, r)
                done_cnt += 1
                update(progress=5 + (done_cnt / n) * 90,
                       message=f"已完成 {done_cnt}/{n} 版大纲")
        success = [r for r in results if r and not r.get("error")]
        if not success:
            raise RuntimeError("大纲全部失败")
        update(progress=100, message=f"完成 {len(success)}/{n} 版大纲",
               result={"outlines": success})

    run_in_background(job_store, job.job_id, _work)
    s.add_message("system", f"开始并行生成 5 版唐迟视角大纲（job {job.job_id}）…", kind="gate")
    ss.save(s)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}
