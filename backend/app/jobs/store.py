"""后台任务框架（进程内单例 JobStore）。

为 coc_batch / pdf_adapt5 / outline5 / sushi 等耗时任务提供：
- Job 数据类（进度、消息、子结果、最终结果、错误、元信息）
- 进程内单例 JobStore，按 session_id 隔离
- 线程安全（threading.Lock）
- run_in_background：在 daemon 线程里跑 work_func，自动 try/except 更新状态
- jobs_to_sse_event_list：把 Job 列表序列化为 SSE 文本块（event: job_update）

用法::

    from app.jobs import job_store, run_in_background, jobs_to_sse_event_list

    job = job_store.create(session_id=sid, kind="coc_batch", meta={"total": 3})

    def work(job, update):
        for i in range(3):
            update(progress=(i+1)/3*100, message=f"第 {i+1}/3 场调查中...")
            # ... 干活 ...
            job_store.append_sub_result(job.job_id, {"variant": i, "text": "..."})
        return {"final": "ok"}

    run_in_background(job_store, job.job_id, work)

    # SSE 推送：
    jobs = job_store.list_session(sid)
    yield jobs_to_sse_event_list(jobs)
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

JOB_KINDS = ("coc_batch", "pdf_adapt5", "outline5", "embody_outline5", "sushi", "scaffold_prefill")
JOB_STATUSES = ("pending", "running", "done", "error", "cancelled")


def _short_uuid() -> str:
    """生成 uuid4 短串（12 位 hex，足够进程内唯一）。"""
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Job 数据类
# ---------------------------------------------------------------------------

@dataclass
class Job:
    job_id: str
    session_id: str
    kind: str
    status: str = "pending"
    progress: float = 0.0
    message: str = ""
    sub_results: list[Any] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    # ---- 序列化 ----

    def as_dict(self) -> dict:
        return asdict(self)


_UPDATEABLE_FIELDS = {
    "status", "progress", "message", "sub_results",
    "result", "error", "started_at", "finished_at", "meta",
}


# ---------------------------------------------------------------------------
# JobStore（进程内单例）
# ---------------------------------------------------------------------------

class JobStore:
    """进程内后台任务存储。

    - 以 session_id 做逻辑隔离，每个 session 可有多个 job。
    - 线程安全：所有读写均在 self._lock 下进行。
    - 纯内存，进程退出即丢失（适合前端轮询/SSE 实时进度场景）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # job_id -> Job
        self._jobs: dict[str, Job] = {}
        # session_id -> list[job_id] （保持插入顺序）
        self._session_index: dict[str, list[str]] = {}

    # ---- 基础 CRUD ----

    def create(
        self,
        session_id: str,
        kind: str,
        meta: dict[str, Any] | None = None,
    ) -> Job:
        """创建一个 pending 状态的 Job 并登记。"""
        if kind not in JOB_KINDS:
            raise ValueError(f"未知 job kind：{kind}，可选：{JOB_KINDS}")
        job = Job(
            job_id=_short_uuid(),
            session_id=session_id,
            kind=kind,
            status="pending",
            progress=0.0,
            message="排队中...",
            meta=dict(meta or {}),
            started_at=None,
            finished_at=None,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._session_index.setdefault(session_id, []).append(job.job_id)
        return job

    def get(self, job_id: str) -> Job | None:
        """按 job_id 获取 Job（返回的是内部引用，调用方请勿跨线程直接改字段）。"""
        with self._lock:
            return self._jobs.get(job_id)

    def list_session(self, session_id: str) -> list[Job]:
        """列出某 session 下所有 Job（按创建时间升序）。"""
        with self._lock:
            ids = self._session_index.get(session_id, [])
            return [self._jobs[jid] for jid in ids if jid in self._jobs]

    def update(self, job_id: str, **kwargs: Any) -> Job:
        """更新 Job 的任意可写字段（progress/message/status/result/error/...）。

        传入 None 的字段不会被覆盖，方便只更新部分字段。
        自动维护 started_at / finished_at：
        - status -> running 时若 started_at 为空则填入当前时间
        - status -> done/error/cancelled 时若 finished_at 为空则填入当前时间
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job 不存在：{job_id}")

            # 过滤非法字段
            unknown = set(kwargs.keys()) - _UPDATEABLE_FIELDS
            if unknown:
                raise ValueError(f"不可更新的字段：{sorted(unknown)}")

            # 应用更新（None 表示"不改"）
            for k, v in kwargs.items():
                if v is None:
                    continue
                setattr(job, k, v)

            # 自动时间戳
            if job.status == "running" and not job.started_at:
                job.started_at = _now_iso()
            if job.status in ("done", "error", "cancelled") and not job.finished_at:
                job.finished_at = _now_iso()

            return job

    def append_sub_result(self, job_id: str, item: Any) -> None:
        """线程安全地追加一个子任务结果到 sub_results。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job 不存在：{job_id}")
            job.sub_results.append(item)

    def cancel(self, job_id: str) -> Job:
        """把 Job 标记为 cancelled（若已结束则不动）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job 不存在：{job_id}")
            if job.status in ("done", "error", "cancelled"):
                return job
            job.status = "cancelled"
            job.message = job.message or "已取消"
            if not job.finished_at:
                job.finished_at = _now_iso()
            return job

    def clear_session(self, session_id: str) -> int:
        """清掉某 session 下的全部 job（返回删除数量）。主要用于 session 结束时回收。"""
        with self._lock:
            ids = self._session_index.pop(session_id, [])
            for jid in ids:
                self._jobs.pop(jid, None)
            return len(ids)


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

job_store = JobStore()


# ---------------------------------------------------------------------------
# 后台运行器：run_in_background
# ---------------------------------------------------------------------------

UpdateFn = Callable[..., Job]


def run_in_background(
    store: JobStore,
    job_id: str,
    work_func: Callable[[Job, UpdateFn], Any],
) -> threading.Thread:
    """在 daemon 线程里执行 work_func(job, update_fn)。

    - 进入时自动把 status 置为 running、记录 started_at。
    - work_func 正常返回：把返回值写入 result，status 置为 done，progress=100。
    - work_func 抛异常：捕获后把 error 置为 traceback 字符串，status 置为 error。
    - work_func 可在内部通过 update_fn(progress=..., message=...) 汇报进度，
      也可直接调用 store.append_sub_result(...) 追加子结果。

    返回启动后的 Thread 对象（已 start）。
    """

    def _runner() -> None:
        # 标记 running
        try:
            store.update(job_id, status="running", started_at=_now_iso())
        except KeyError:
            return

        job = store.get(job_id)
        if job is None:
            return

        # 给 work_func 用的便捷 update：绑定 job_id
        def _update(**kwargs: Any) -> Job:
            return store.update(job_id, **kwargs)

        try:
            result = work_func(job, _update)
            store.update(
                job_id,
                status="done",
                result=result,
                progress=100.0,
                finished_at=_now_iso(),
            )
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            try:
                store.update(
                    job_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}\n{tb}",
                    finished_at=_now_iso(),
                )
            except KeyError:
                pass

    t = threading.Thread(target=_runner, name=f"job-{job_id}", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# SSE 序列化
# ---------------------------------------------------------------------------

def jobs_to_sse_event_list(jobs: list[Job]) -> str:
    """把一组 Job 序列化为 SSE 文本块。

    每条 job 都以独立的 ``event: job_update`` 事件发送，data 为该 job 的 JSON 字典。
    末尾会再发一条 ``event: job_update_batch`` 事件，data 为完整 list（方便前端
    一次性做全量替换）。

    格式示例::

        event: job_update
        data: {"job_id": "...", "status": "running", ...}

        event: job_update_batch
        data: [{...}, {...}]

    """
    parts: list[str] = []
    for job in jobs:
        parts.append(f"event: job_update\ndata: {json.dumps(job.as_dict(), ensure_ascii=False)}\n\n")
    parts.append(
        "event: job_update_batch\n"
        f"data: {json.dumps([j.as_dict() for j in jobs], ensure_ascii=False)}\n\n"
    )
    return "".join(parts)


def job_to_sse_event(job: Job) -> str:
    """单个 Job 的 SSE 事件（增量推送用）。"""
    return f"event: job_update\ndata: {json.dumps(job.as_dict(), ensure_ascii=False)}\n\n"
