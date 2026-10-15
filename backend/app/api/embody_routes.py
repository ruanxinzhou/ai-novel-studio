"""工作台 embody 阶段（唐迟魂穿）路由：
选身体（candidates）→ 选切入点（entry-points）→ 选立场（stances）
→ 5 版大纲并行生成（generate-outline, JobStore + SSE）→ 选版落盘（pick-outline）。

挂载方式：main.py 末尾调用
    from .embody_routes import register_embody_routes
    register_embody_routes(app, require_project=..., volumes_for=..., chore_provider=...)
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    chore_provider: Callable[[str], Any]


_deps: _Deps | None = None


def register_embody_routes(
    app,
    *,
    require_project: Callable,
    volumes_for: Callable[[str], Any],
    chore_provider: Callable[[str], Any],
) -> None:
    global _deps
    _deps = _Deps(
        require_project=require_project,
        volumes_for=volumes_for,
        chore_provider=chore_provider,
    )

    router = APIRouter()
    prefix = "/api/projects/{pid}/workbench/volumes/{vol_no}/embody"
    router.add_api_route(f"{prefix}/candidates", _list_candidates, methods=["GET"])
    router.add_api_route(f"{prefix}/entry-points", _gen_entry_points, methods=["POST"])
    router.add_api_route(f"{prefix}/stances", _gen_stances, methods=["POST"])
    router.add_api_route(f"{prefix}/generate-outline", _start_outline5, methods=["POST"])
    router.add_api_route(f"{prefix}/jobs", _list_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/stream", _stream_jobs, methods=["GET"])
    router.add_api_route(f"{prefix}/pick-outline", _pick_outline, methods=["POST"])
    app.include_router(router)


def _must_deps() -> _Deps:
    if _deps is None:
        raise RuntimeError("embody_routes 未调用 register_embody_routes 初始化")
    return _deps


# ---- 请求体 ----

class _CandidateRef(BaseModel):
    candidate_id: str | None = None
    candidate: dict | None = None


class _EntryPointsIn(_CandidateRef):
    pass


class _StancesIn(BaseModel):
    candidate: dict | None = None
    entry_point: dict | None = None


class _GenerateOutlineIn(BaseModel):
    candidate: dict | None = None
    entry_point: dict | None = None
    stance: dict | None = None
    user_directive: str = ""
    n: int = 5


class _PickOutlineIn(BaseModel):
    job_id: str = ""
    pick_index: int = -1
    candidate: dict | None = None
    entry_point: dict | None = None
    stance: dict | None = None
    custom_data: dict | None = None  # 自定义大纲数据，存在则跳过 job 校验


# ---- 帮助函数 ----

def _session_id(pid: str, vol_no: int) -> str:
    return f"embody:{pid}:{vol_no}"


def _get_volume_or_404(pid: str, vol_no: int):
    d = _must_deps()
    d.require_project(pid)
    vs = d.volumes_for(pid)
    v = vs.get(vol_no)
    if v is None:
        raise HTTPException(status_code=404, detail=f"卷 {vol_no} 不存在")
    return vs, v


def _is_mock(adapted_module: str | None, candidate: dict | None) -> bool:
    if adapted_module and adapted_module.startswith("测试副本核文本"):
        return True
    if candidate and isinstance(candidate.get("name"), str) and "林薇" in candidate["name"]:
        return True
    return False


def _outline_to_text(o: dict) -> str:
    """把一版大纲 JSON 转成可读纯文本（落盘/显示用）。"""
    lines = []
    title = o.get("title") or "（无标题）"
    lines.append(f"# {title}")
    if o.get("pacing"):
        lines.append(f"节奏：{o['pacing']}")
    if o.get("one_liner"):
        lines.append(f"一句话：{o['one_liner']}")
    if o.get("tangchi_entry"):
        lines.append(f"\n魂穿设定：{o['tangchi_entry']}")
    dbs = o.get("day_breakdown") or []
    if dbs:
        lines.append("\n## 七日流程")
        for d_item in dbs:
            if isinstance(d_item, dict):
                day = d_item.get("day", "?")
                s = d_item.get("summary", "")
                lines.append(f"- Day{day}：{s}")
    if o.get("climax"):
        lines.append(f"\n## 第七日高潮\n{o['climax']}")
    if o.get("blue_star_hook"):
        lines.append(f"\n## 杀青蓝星钩\n{o['blue_star_hook']}")
    fp = o.get("foreshadow_planted") or []
    if fp:
        lines.append("\n## 长线伏笔")
        for f_item in fp:
            lines.append(f"- {f_item}")
    return "\n".join(lines)


# ---- 1. 魂穿候选 ----

def _list_candidates(pid: str, vol_no: int):
    vs, v = _get_volume_or_404(pid, vol_no)
    adapted = v.adapted_module or ""
    if _is_mock(adapted, None):
        time.sleep(1.5)
        return {"candidates": _MOCK_CANDIDATES}

    # 真实路径（本任务阶段主要靠 mock 跑通，保留占位）
    # TODO: 基于 adapted_module 调 LLM 抽取 5-8 个将死/倒霉角色候选
    return {"candidates": _MOCK_CANDIDATES}


# ---- 2. 切入点 ----

def _gen_entry_points(pid: str, vol_no: int, body: _EntryPointsIn):
    vs, v = _get_volume_or_404(pid, vol_no)
    candidate = body.candidate or {}
    if _is_mock(v.adapted_module, candidate):
        time.sleep(0.8)
        return {"entry_points": _mock_entry_points_for(candidate)}

    return {"entry_points": _mock_entry_points_for(candidate)}


# ---- 3. 立场 ----

def _gen_stances(pid: str, vol_no: int, body: _StancesIn):
    vs, v = _get_volume_or_404(pid, vol_no)
    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    if _is_mock(v.adapted_module, candidate):
        time.sleep(0.8)
        return {"stances": _mock_stances_for(candidate, entry_point)}

    return {"stances": _mock_stances_for(candidate, entry_point)}


# ---- 4. 启动 5 版大纲生成 ----

_OUTLINE5_SYSTEM = (
    "你是《第七日的亡者》长篇小说专职大纲写手。基于给定的副本骨架+选定的跑团走向+唐迟替换角色，"
    "产出一版唐迟视角 Day1-Day7 大纲。大纲是成品可读的梗概，不是分点清单，要能让作者直接按它写章节细纲。\n\n"
    "大纲必须包含：\n"
    "①title：12 字内标题，定这一版的节奏/气质；\n"
    "②one_liner：一句话梗概（40 字内）；\n"
    "③pacing：节奏标签（慢热铺陈/反转密集/开场高能/先甜后刀/步步紧逼 等，可自造）；\n"
    "④tangchi_entry：唐迟在这版里替换谁、开场身份是什么、何时/如何发现身份问题（若是诡计副本）、第七日死法如何破/被破；\n"
    "⑤day_breakdown：[Day1..Day7] 每天 80-150 字梗概，写清当天关键行动/转折/死人/线索揭示/代价；\n"
    "⑥climax：Day7 高潮 100-200 字（怎么破局/死不死/谁活下来/代价是什么）；\n"
    "⑦blue_star_hook：杀青后带回什么/蓝星小钩子 50 字；\n"
    "⑧foreshadow_planted：本场埋下的长线伏笔列表（3-5 条，供卷规划参考）。\n\n"
    "输出严格 JSON：{title,one_liner,pacing,tangchi_entry,day_breakdown:[{day,summary}],climax,blue_star_hook,foreshadow_planted:[str]}。"
    "不要输出 JSON 之外的任何文字。\n\n"
    "===== 魂穿硬约束（必须遵守）=====\n"
    "唐迟魂穿的身体是一个**即将死亡/已死/被替换/SAN 归零/被诅咒缠身**的倒霉角色；他不是来爽的，是来捡烂摊子活过七天的。\n"
    "- 必须紧扣「魂穿对象是谁、穿入时点是什么、唐迟当时掌握多少信息、他以什么立场行动」这四个要素；\n"
    "- 切入点决定了唐迟开局知道什么/不知道什么（信息差）；立场决定他是救同伴/独善其身/主动查真相/反向利用污染；\n"
    "- 七日每一天都要体现「信息差被一点点揭开」「SAN 在掉」「同伴在死或在变」「身体原主留下的痕迹/债务/因果必须面对」；\n"
    "- 第七日高潮必须是对「魂穿身份 + 切入点 + 立场」三者组合的收束，不能和其他版本雷同。"
)


def _start_outline5(pid: str, vol_no: int, body: _GenerateOutlineIn):
    from ..jobs import job_store, run_in_background
    from ..generation import complete_structured
    log = logging.getLogger("api.embody")
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    stance = body.stance or {}
    user_dir = (body.user_directive or "").strip()
    n = max(2, min(8, int(body.n or 5)))

    job = job_store.create(sid, "embody_outline5", meta={
        "phase": "embody", "n": n,
        "candidate_name": candidate.get("name", ""),
    })

    # 构造 context
    adapted_text = v.adapted_module or ""
    sim = v.simulated_run or {}
    sel_variant = sim.get("selected_variant") or {}
    context_parts = [
        "===== 副本骨架（改编稿）=====",
        adapted_text[:4000],
    ]
    if sel_variant:
        context_parts.append("\n===== 选定跑团走向 =====\n")
        context_parts.append(f"标签：{sel_variant.get('label','')}")
        context_parts.append(f"一句话：{sel_variant.get('one_liner','')}")
        fl = sel_variant.get("full_log") or ""
        if fl:
            context_parts.append(f"\n跑团日志节选：\n{fl[:2500]}")
    context_parts.append("\n===== 魂穿设定（作者已选）=====")
    context_parts.append(f"魂穿身体：{candidate.get('name','')} —— {candidate.get('body_state','')} / {candidate.get('struggle_hook','')}")
    context_parts.append(f"切入点：{entry_point.get('name','')} —— {entry_point.get('description','')}（信息差：{entry_point.get('info_gap','')}）")
    context_parts.append(f"立场：{stance.get('name','')} —— {stance.get('description','')}")
    if user_dir:
        context_parts.append(f"\n===== 作者额外要求 =====\n{user_dir}")
    context_text = "\n".join(context_parts)

    pacing_seeds = [
        "步步紧逼·每夜都死人",
        "伪装求生·身边都是怪物",
        "记忆污染·分不清自己是谁",
        "逃不掉的轮回·死亡只是开始",
        "最后一天反转·幸存者即祭品",
    ]

    def _work(j, update):
        # mock 短路：测试副本核文本 或 候选名含「林薇」→ 不调 LLM，2 秒返回
        if _is_mock(v.adapted_module, candidate):
            update(progress=5, message="测试模式：mock 生成 5 版大纲…")
            time.sleep(2)
            outs = []
            for i in range(n):
                flavor = pacing_seeds[i % len(pacing_seeds)]
                o = _mock_outline(i, flavor, candidate, entry_point, stance)
                outs.append(o)
                job_store.append_sub_result(job.job_id, o)
                update(progress=10 + int((i + 1) / n * 85),
                       message=f"已生成 {i+1}/{n} 版大纲（mock）")
            update(progress=100, message=f"完成 {n} 版大纲（mock）",
                   result={"outlines": outs})
            return {"outlines": outs}

        # 真实 LLM 路径
        d2 = _must_deps()
        results = [None] * n

        def _run_one(i: int):
            if j.status == "cancelled":
                return i, None
            update(message=f"第 {i+1}/{n} 版大纲生成中…",
                   progress=5 + int((i / n) * 85))
            flavor = pacing_seeds[i % len(pacing_seeds)]
            messages = [
                {"role": "system", "content": _OUTLINE5_SYSTEM},
                {"role": "user", "content": context_text
                    + f"\n\n【本版节奏定向】请按「{flavor}」的节奏与克苏鲁基调来写这一版大纲，"
                      f"让这一版和其他版有明显差别。必须严格遵守魂穿四要素约束，"
                      f"唐迟是{flavor}这一版节奏下的求生者，不是全知主角。"},
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
                log.warning("embody outline5 #%d failed: %s", i, e)
                return i, {"index": i, "error": str(e),
                           "title": f"大纲第{i+1}版（失败）", "flavor": flavor}

        with ThreadPoolExecutor(max_workers=min(n, 3)) as ex:
            futures = {ex.submit(_run_one, i): i for i in range(n)}
            done_cnt = 0
            for fut in as_completed(futures):
                i, r = fut.result()
                results[i] = r
                if r and not r.get("error"):
                    job_store.append_sub_result(job.job_id, r)
                done_cnt += 1
                update(progress=5 + int((done_cnt / n) * 90),
                       message=f"已完成 {done_cnt}/{n} 版大纲")
        success = [r for r in results if r and not r.get("error")]
        if not success:
            raise RuntimeError("大纲全部失败")
        update(progress=100, message=f"完成 {len(success)}/{n} 版大纲",
               result={"outlines": success})

    run_in_background(job_store, job.job_id, _work)
    return {"ok": True, "job": job.as_dict(), "job_id": job.job_id}


# ---- 5. 列 jobs ----

def _list_jobs(pid: str, vol_no: int):
    from ..jobs.store import job_store
    _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)
    jobs = job_store.list_session(sid)
    return {"jobs": [j.as_dict() for j in jobs]}


# ---- 6. SSE 流 ----

def _stream_jobs(pid: str, vol_no: int):
    from ..jobs.store import job_store, jobs_to_sse_event_list
    _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

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


# ---- 7. 选版落盘 ----

def _pick_outline(pid: str, vol_no: int, body: _PickOutlineIn):
    from ..jobs.store import job_store
    vs, v = _get_volume_or_404(pid, vol_no)
    sid = _session_id(pid, vol_no)

    candidate = body.candidate or {}
    entry_point = body.entry_point or {}
    stance = body.stance or {}

    # 自定义数据分支：跳过 job 校验，直接用 custom_data 作为选中大纲
    if body.custom_data:
        picked = body.custom_data
    else:
        job = job_store.get(body.job_id)
        if job is None or job.session_id != sid:
            raise HTTPException(status_code=404, detail="job 不存在或不属于该卷")
        outlines = (job.result or {}).get("outlines") or []
        idx = int(body.pick_index)
        if idx < 0 or idx >= len(outlines):
            raise HTTPException(status_code=422, detail=f"pick_index 越界（共 {len(outlines)} 版）")
        picked = outlines[idx]

    # 计算信息差（info_gap）：从切入点里取
    info_gap = entry_point.get("info_gap") or entry_point.get("description") or ""

    outline_text = _outline_to_text(picked)

    v.host_character = candidate
    v.entry_point = entry_point
    v.info_gap = info_gap
    v.stance = stance
    v.tangchi_story_outline = {
        **picked,
        "job_id": body.job_id,
        "outline_text": outline_text,
    }
    v.stage_completed["embody"] = True
    vs.save(v)
    return {"ok": True, "volume": v.as_dict(), "picked": picked}


# ============================================================
# Mock 数据
# ============================================================

_MOCK_CANDIDATES = [
    {
        "id": "linwei",
        "name": "林薇",
        "role": "女大学生/PL队友",
        "age": 21,
        "body_state": "即将坠楼——身体已经翻过天台护栏，指尖只剩最后半寸抓着水泥沿",
        "struggle_hook": "她在 Day3 晚上查到了校医院地下室的秘密，被「它」推下了楼。唐迟就在她摔下去前一秒穿进来——只要手一松就死；但抓紧爬回去，那个「它」就在天台门口等她。",
        "san_loss": "已掉 35/99",
        "death_countdown": "10 秒",
        "tag": "倒计时型",
    },
    {
        "id": "han",
        "name": "韩医生",
        "role": "校医院校医/关键NPC",
        "age": 47,
        "body_state": "已被替换——真正的韩医生三天前就死在解剖室冰柜里，现在这具身体是「它」披着人皮在扮演他，他自己还不知道",
        "struggle_hook": "他/它已经习惯了用韩医生的脸给学生开安眠药、给调查的人打「镇静剂」。唐迟穿进来时，韩医生正在诊室写病历，左手无名指已经开始蜕皮露出下面的鳞片。",
        "san_loss": "原主 SAN 已归零",
        "death_countdown": "身份被揭穿即死",
        "tag": "身份诡计型",
    },
    {
        "id": "laochen",
        "name": "老陈",
        "role": "学校门卫/NPC",
        "age": 62,
        "body_state": "SAN 见底——七天里他看了三次不该看的东西，今晚是第七夜，他已经把尼龙绳挂上了值班室房梁",
        "struggle_hook": "老陈知道每晚 11:47 宿舍楼后门会多出来第十三阶台阶；知道七年前失踪的四个学生其实没有走；他想吊死自己逃掉，因为「它」许诺过：自缢的人不用当容器。唐迟穿入时他正踩着椅子把脖子往绳圈里套。",
        "san_loss": "剩 4/99",
        "death_countdown": "椅子已经在晃",
        "tag": "SAN 崩盘型",
    },
]


def _mock_entry_points_for(candidate: dict) -> list[dict]:
    name = candidate.get("name", "")
    if "林薇" in name:
        return [
            {
                "id": "lw_pre_fall",
                "name": "坠楼前 10 秒",
                "description": "唐迟穿入时林薇还抓着护栏，下面是六楼高度。他/她完全没读过林薇之前三天查到的线索，口袋里只有一张被汗浸湿的、写着「不要看镜子」的便利贴。",
                "info_gap": "对事件零认知，只知道自己马上要死；必须先活下来才能开始查。",
                "time_anchor": "Day3 夜 23:41",
            },
            {
                "id": "lw_morning_after",
                "name": "Day4 清晨醒来（诈尸）",
                "description": "林薇已经摔下去了，但唐迟醒在医院病床上——所有人都以为她只是骨折住院。只有唐迟知道：昨晚摔死的那个「林薇」，和现在躺在病床上的自己，不是同一个「回来」的东西。",
                "info_gap": "知道自己死过一次，但不知道是谁/什么把自己塞回这具身体；身体上多出了自己不知道的旧伤痕。",
                "time_anchor": "Day4 早 06:13",
            },
            {
                "id": "lw_final_night",
                "name": "Day7 最后一夜·钟楼顶",
                "description": "唐迟直接穿入林薇在第七夜被献祭的瞬间——仪式已经开始，其他队友大部分已经死了或疯了，他/她手里只有林薇三天前就藏好的一把手术刀，而面前那个东西正用林薇妈妈的脸在笑。",
                "info_gap": "一无所知地被丢进终局，必须在 20 分钟内从尸体/残页/疯话里反推七天发生了什么并破局。",
                "time_anchor": "Day7 夜 23:55",
            },
        ]
    if "韩" in name:
        return [
            {
                "id": "han_clinic_day2",
                "name": "Day2 白班·第一个病人",
                "description": "穿入时韩医生正在诊室坐诊，第一个病人就是个说「我肚子里有东西在动」的女生。唐迟还没搞清楚自己已经不是人了，左手的蜕皮在白大褂袖子里发痒。",
                "info_gap": "不知道自己是替换物，不知道医院地下室有什么；但周围同事似乎都「认识」他并对他有某种隐秘的期待。",
                "time_anchor": "Day2 下午 14:20",
            },
            {
                "id": "han_basement_door",
                "name": "Day5 夜班·站在地下室门口",
                "description": "唐迟穿着白大褂站在通往地下室的楼梯口，钥匙已经插进锁孔。身体原主（那个取代韩医生的东西）每晚都下来「喂食」，唐迟接盘了这具身体的全套肌肉记忆，却完全不知道门后面是什么。",
                "info_gap": "继承了韩医生的日常动作/钥匙/排班表，但没有任何关于「自己是什么」的记忆；门一旦打开就再也关不上。",
                "time_anchor": "Day5 夜 01:08",
            },
            {
                "id": "han_exposed",
                "name": "Day6 午后·被学生指认",
                "description": "一个调查到一半的 PL 学生当着唐迟的面说：「你不是韩医生。」唐迟必须在三秒内决定：是当场灭口（完成身体原主未完成的事），还是赌一把承认自己也是被卷进来的、与对方合作。",
                "info_gap": "知道自己不是韩医生但不知道该假装到什么程度；身份即将崩解，倒计时开始。",
                "time_anchor": "Day6 下午 16:47",
            },
        ]
    # 老陈
    return [
        {
        "id": "lc_on_stool",
        "name": "绳圈套脖·椅子在晃",
        "description": "唐迟穿入时老陈正踩在椅子上，脖子已经套进绳圈，只差把椅子踢开。他必须立刻自救，但自救动作必须慢——因为值班室窗外，那个「它」正在看着，等着他死。",
        "info_gap": "继承了老陈脑子里的 7 年值守记忆碎片，全是一闪而过的恐怖画面：多出来的台阶、不存在的 404 宿舍、井里的头发；但碎片顺序是乱的。",
        "time_anchor": "Day1 夜 22:58",
    },
    {
        "id": "lc_gate_midnight",
        "name": "Day3 午夜·开门放人",
        "description": "唐迟穿入老陈的时候，有人/有什么东西正在敲门卫室的窗户让他开门。老陈的手已经在拉门栓了，但唐迟隐约知道——老陈七年前开过一次门，放进来的就是它。这一次是同一「批」的，还是另一「批」？",
        "info_gap": "知道开门的规矩但不知道门外是谁；知道 11:47 的台阶禁忌但不知道为什么不能数到十三。",
        "time_anchor": "Day3 夜 00:02",
    },
    {
        "id": "lc_final_shift",
        "name": "Day7 换班·接班人没来",
        "description": "第七天老陈本应在凌晨交班，但接班人一直没来。唐迟必须死守门卫室到天亮，或者烧掉七年的门卫日志让「它」失去凭据——但烧掉日志意味着老陈七年前的罪证也没了。",
        "info_gap": "知道全部规则碎片，但不知道哪一条是真正能活命的；知道自己是容器候选之一，但不知道容器装的是什么。",
        "time_anchor": "Day7 凌晨 05:00",
    },
    ]


def _mock_stances_for(candidate: dict, entry_point: dict) -> list[dict]:
    return [
        {
            "id": "save_all",
            "name": "能救一个是一个·救场者",
            "description": "唐迟不接受宿命——他要用自己七天内拿到的信息，尽可能把队友/NPC 从死局里拽出来。代价：每救一个人，SAN 多掉一份；救到最后可能一个都救不下。",
            "cost": "SAN 代价重，同伴伤亡风险转嫁给自己",
            "tendency": "利他/燃向/悲壮",
        },
        {
            "id": "lone_wolf",
            "name": "自己先活下来·独狼",
            "description": "唐迟清楚这具身体的原主是个将死之人——他/她首要目标是保证自己七天后还能喘气。必要时可以牺牲 NPC、可以躲、可以装作没看见。但克苏鲁副本里独善其身的人，最后往往发现自己活下来的「自己」已经不是自己。",
            "cost": "内心崩塌，可能触发身份污染结局",
            "tendency": "利己/冷硬/黑暗",
        },
        {
            "id": "truth_seeker",
            "name": "死也要知道真相·追真相者",
            "description": "唐迟决定把整件事挖到底——它是什么？七年前发生了什么？自己为什么穿进这具身体？追真相会比逃跑 SAN 掉得更快，但只有看清真相才能在第七日找到真正的破局点。",
            "cost": "SAN 崩盘最快，但可能发现真正破局法",
            "tendency": "理性/癫狂/揭秘",
        },
    ]


def _mock_outline(idx: int, flavor: str, candidate: dict, entry_point: dict, stance: dict) -> dict:
    name = candidate.get("name", "?")
    cname = candidate.get("name", "")
    ep_name = entry_point.get("name", "")
    st_name = stance.get("name", "")
    titles = [
        f"{cname}的第十三次坠落",
        f"披着{cname}皮的人",
        "第七夜值班日志",
        "她摔下去之后",
        "门卫室的绳圈",
    ]
    oneliners = [
        f"唐迟穿进即将坠楼的{cname}，从{ep_name}开始，以「{st_name}」的立场在七日内一边活命一边追凶，最后发现——那个推她下楼的，就是七天后的自己。",
        f"穿成{cname}的唐迟在{ep_name}节点接盘了一具正在被替换的身体，他必须以「{st_name}」的姿态撑过七天，才能在照镜子时不看见另一个东西。",
        f"老陈门卫室的绳圈已经在晃，唐迟从{ep_name}开始七天守夜，以「{st_name}」的立场面对多出来的第十三阶台阶，最后发现台阶通向的是自己的记忆。",
        f"唐迟在{cname}已经摔死的次日清晨醒来，医院病床上的这具身体开始蜕皮，他以「{st_name}」的立场重返学校，追查到第七夜的钟楼献祭。",
        f"以「{st_name}」为信条，唐迟在{cname}的身体里从{ep_name}硬扛到 Day7，最后活下来的只有一个——但活下来的那个人，瞳孔是竖的。",
    ]
    pacings = [
        "步步紧逼·每夜都死人",
        "伪装求生·身边都是怪物",
        "记忆污染·分不清自己是谁",
        "逃不掉的轮回·死亡只是开始",
        "最后一天反转·幸存者即祭品",
    ]
    days = []
    for d in range(1, 8):
        days.append({
            "day": d,
            "summary": (
                f"Day{d}：以{cname}的身份活动，{('继续调查身边异常' if d < 4 else '线索开始收束' if d < 6 else '真相层层揭开')}。"
                f"当天发生{('第一起诡异事件' if d == 1 else '第一次有人失踪/死亡' if d == 3 else '第一次正面遭遇它' if d == 5 else '第七日终局')}，"
                f"唐迟 SAN 掉 {d*4}，身体原主的残留记忆闪现「{('不要照镜子' if d%2 else '别数台阶')}」。"
            ),
        })
    climax = (
        f"第七夜，唐迟以{st_name}的姿态站在{('天台/校医院地下室/门卫室/钟楼')[(idx)%4]}——"
        f"七天里死掉的同伴、替换过的 NPC、消失的记忆在这一刻同时汇聚。"
        f"他终于看清：{cname}的死不是意外，是整局仪式的钥匙；而自己在{ep_name}穿入的那一刻，就已经成为了它的下一个容器候选。"
        f"破局的代价是——（留白，由作者定）。"
    )
    blue_star = f"唐迟从副本里带出了一张七年前失踪名单的残角，背面写着自己的字迹：「别再当门卫。」"
    foreshadows = [
        f"{cname}身上出现的来历不明的伤痕——长线指向卷二/卷三替换物的传染机制",
        "十三阶台阶的隐喻——长线指向「蓝星/副本」的空间折叠",
        f"唐迟穿入瞬间看到的「它」的脸——长线指向 BOSS 真身",
        f"{st_name}立场留下的心理创伤——长线影响后续副本的 SAN 上限",
    ]
    return {
        "index": idx,
        "title": titles[idx % len(titles)],
        "one_liner": oneliners[idx % len(oneliners)],
        "pacing": flavor or pacings[idx % len(pacings)],
        "tangchi_entry": f"唐迟在{ep_name}魂穿{cname}，以「{st_name}」为行动立场；身体原主正处于{candidate.get('body_state','危机状态')}，开局即死局。",
        "day_breakdown": days,
        "climax": climax,
        "blue_star_hook": blue_star,
        "foreshadow_planted": foreshadows,
        "flavor": flavor,
    }
