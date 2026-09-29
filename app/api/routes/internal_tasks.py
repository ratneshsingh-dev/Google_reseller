"""
Internal endpoints called by Google Cloud Tasks (and Cloud Scheduler), never by partners.

Every call must carry a Google-signed OIDC token for the configured invoker service
account; anything else is refused with 403.

Response codes drive Cloud Tasks retries: 2xx = done (never retried),
503 = another worker is running this job right now, try again later.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.core import job_dispatcher, job_executor
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.dependencies import get_provisioning_intake

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/tasks", tags=["Internal (Cloud Tasks)"], include_in_schema=False)


class ProvisionTask(BaseModel):
    job_id: str


def _authorise(authorization: Optional[str]) -> None:
    try:
        job_dispatcher.verify_task_request(authorization)
    except Exception as exc:
        logger.warning("task_request_rejected", reason=str(exc)[:120])
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/provision-job")
@limiter.exempt
async def run_provision_job(
    task: ProvisionTask,
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    _authorise(authorization)
    intake = get_provisioning_intake()
    attempt = request.headers.get("X-CloudTasks-TaskRetryCount", "0")
    logger.info("task_received", job_id=task.job_id, retry=attempt)

    future = job_executor.submit(lambda: intake.run_job(task.job_id), name=task.job_id)
    outcome = await asyncio.wrap_future(future)
    if outcome == "busy":
        raise HTTPException(status_code=503, detail="Job is running on another worker; retry later.")
    return {"job_id": task.job_id, "outcome": outcome}


@router.post("/reconcile")
@limiter.exempt
async def reconcile(authorization: Optional[str] = Header(None)) -> dict:
    """Fail and settle jobs that stopped sending heartbeats (for Cloud Scheduler)."""
    _authorise(authorization)
    intake = get_provisioning_intake()
    failed = await asyncio.to_thread(intake.reconcile_stale_jobs)
    return {"interrupted_jobs": failed}
