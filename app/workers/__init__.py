"""
Background worker for provisioning jobs.

Uses FastAPI BackgroundTasks for the prototype.
Structured so it can be replaced with Celery/Cloud Tasks by changing
only the dispatch mechanism in the API route.
"""

from __future__ import annotations

from typing import Optional

from app.core.logging import get_logger
from app.models.database import JobStatus
from app.models.requests import ProvisioningRequest
from app.repositories.job_repository import JobRepository
from app.services.provisioning_service import ProvisioningService

logger = get_logger(__name__)


async def run_provisioning_job(
    provisioning_service: ProvisioningService,
    job_repo: JobRepository,
    job_id: str,
    request: ProvisioningRequest,
    reseller_id: Optional[str] = None,
) -> None:
    """Execute a provisioning job in the background.

    This function is designed to be called by:
      - FastAPI BackgroundTasks (current prototype)
      - Celery task (future)
      - Cloud Tasks handler (future)

    The dispatch mechanism changes; this function does not.
    reseller_id — optional, set when dispatched from the Reseller API.
    """
    logger.info("worker_started", job_id=job_id, reseller_id=reseller_id)

    try:
        await provisioning_service.provision_company(request, job_id, reseller_id=reseller_id)
        logger.info("worker_completed", job_id=job_id)
    except Exception as exc:
        logger.error(
            "worker_failed",
            job_id=job_id,
            error=str(exc),
            exc_info=True,
        )
        # Ensure job is marked as FAILED even if provisioning_service didn't
        try:
            job = job_repo.get_job(job_id)
            if job and job.status not in (
                JobStatus.COMPLETED.value,
                JobStatus.PARTIAL_FAILURE.value,
                JobStatus.FAILED.value,
            ):
                job_repo.update_job_status(
                    job_id, JobStatus.FAILED.value, error_message=str(exc)
                )
        except Exception:
            pass  # Best-effort status update
