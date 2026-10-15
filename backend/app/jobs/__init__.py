"""后台任务模块（jobs）。

快速入口::

    from app.jobs import job_store, run_in_background, jobs_to_sse_event_list, job_to_sse_event
    from app.jobs.store import Job, JobStore, JOB_KINDS, JOB_STATUSES
"""
from __future__ import annotations

from .store import (
    JOB_KINDS,
    JOB_STATUSES,
    Job,
    JobStore,
    job_store,
    job_to_sse_event,
    jobs_to_sse_event_list,
    run_in_background,
)

__all__ = [
    "JOB_KINDS",
    "JOB_STATUSES",
    "Job",
    "JobStore",
    "job_store",
    "job_to_sse_event",
    "jobs_to_sse_event_list",
    "run_in_background",
]
