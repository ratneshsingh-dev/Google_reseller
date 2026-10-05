"""
Hands provisioning jobs to a backend, selected by JOB_BACKEND:

- "memory"     : the in-process worker pool (app.core.job_executor). Fast, but a job
                 is lost if the server dies; the reconciler then fails it and refunds.
- "cloudtasks" : Google Cloud Tasks. The job is stored in Google's durable queue and
                 delivered to POST /internal/tasks/provision-job on any server, with
                 retries and backoff; a crashed server's job is re-delivered and resumed.

Both backends run the same code (ProvisioningIntake.run_job), which is safe to repeat.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Callable, Optional

import requests

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

WORKER_PATH = "/internal/tasks/provision-job"
TASKS_API = "https://cloudtasks.googleapis.com/v2"
DISPATCH_DEADLINE = "900s"

_creds = None
_creds_lock = threading.Lock()


def dispatch(job_id: str, run_locally: Callable[[], object]) -> None:
    """Queue the job on the configured backend. `run_locally` is used for the memory backend."""
    settings = get_settings()
    if settings.job_backend == "cloudtasks":
        enqueue_cloud_task(job_id)
    else:
        from app.core import job_executor
        job_executor.submit(run_locally, name=job_id)


def enqueue_cloud_task(job_id: str) -> None:
    settings = get_settings()
    for name, value in (("CLOUD_TASKS_QUEUE", settings.cloud_tasks_queue),
                        ("WORKER_BASE_URL", settings.worker_base_url),
                        ("TASKS_INVOKER_SA", settings.tasks_invoker_sa)):
        if not value:
            raise RuntimeError(f"JOB_BACKEND=cloudtasks needs {name} to be set")

    queue = (f"projects/{settings.google_cloud_project}/locations/"
             f"{settings.cloud_tasks_location}/queues/{settings.cloud_tasks_queue}")
    audience = settings.worker_base_url.rstrip("/")
    task = {
        # Naming the task after the job makes enqueueing idempotent: Cloud Tasks refuses a
        # second task with the same name, so one job can never be queued twice.
        "name": f"{queue}/tasks/{job_id}",
        "dispatchDeadline": DISPATCH_DEADLINE,
        "httpRequest": {
            "httpMethod": "POST",
            "url": audience + WORKER_PATH,
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(json.dumps({"job_id": job_id}).encode()).decode(),
            "oidcToken": {"serviceAccountEmail": settings.tasks_invoker_sa, "audience": audience},
        },
    }
    resp = requests.post(f"{TASKS_API}/{queue}/tasks", headers=_auth_headers(), json={"task": task}, timeout=30)
    if resp.status_code == 409:
        logger.info("cloud_task_already_queued", job_id=job_id)
        return
    if resp.status_code != 200:
        logger.error("cloud_task_enqueue_failed", job_id=job_id, status=resp.status_code, text=resp.text[:300])
        resp.raise_for_status()
    logger.info("cloud_task_enqueued", job_id=job_id, queue=settings.cloud_tasks_queue)


def verify_task_request(authorization: Optional[str]) -> None:
    """Accept only calls carrying a Google-signed OIDC token for our invoker service account."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    settings = get_settings()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise PermissionError("Missing task token")
    claims = id_token.verify_oauth2_token(
        authorization[7:].strip(), google_requests.Request(), audience=settings.worker_base_url.rstrip("/")
    )
    if claims.get("email") != settings.tasks_invoker_sa or not claims.get("email_verified"):
        raise PermissionError("Task token is not from the expected service account")


def _auth_headers() -> dict:
    global _creds
    import google.auth
    import google.auth.transport.requests
    from google.oauth2 import service_account

    with _creds_lock:
        if _creds is None:
            scopes = ["https://www.googleapis.com/auth/cloud-platform"]
            info = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
            if info:
                _creds = service_account.Credentials.from_service_account_info(json.loads(info), scopes=scopes)
            else:
                path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
                try:
                    _creds = service_account.Credentials.from_service_account_file(path, scopes=scopes)
                except Exception:
                    _creds, _ = google.auth.default(scopes=scopes)
        if not _creds.valid:
            _creds.refresh(google.auth.transport.requests.Request())
        return {"Authorization": f"Bearer {_creds.token}", "Content-Type": "application/json"}
