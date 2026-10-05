"""
Periodic safety net: finds provisioning jobs whose worker died (no heartbeat for
JOB_STALE_AFTER_SECONDS), marks them FAILED with the step they stopped at, and
refunds their unused quota exactly once.

Runs on every server, but a shared lease ensures only one server does the work per
interval. With Cloud Tasks most crashed jobs are resumed automatically; this catches
the rest (retries exhausted, queue problems, or the in-memory backend).
"""

from __future__ import annotations

import threading

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

RECONCILER_LOCK = "__job_reconciler__"


def run_once() -> list:
    from app.dependencies import get_lock_repo, get_provisioning_intake

    interval = get_settings().reconcile_interval_seconds
    token = get_lock_repo().acquire(RECONCILER_LOCK, "reconcile", ttl_seconds=max(30, interval - 10))
    if not token:
        return []
    failed = get_provisioning_intake().reconcile_stale_jobs()
    if failed:
        logger.warning("reconciler_interrupted_jobs", count=len(failed), job_ids=failed)
    return failed


def start_reconciler() -> threading.Event:
    """Start the background loop; set the returned event to stop it."""
    settings = get_settings()
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(settings.reconcile_interval_seconds):
            try:
                run_once()
            except Exception as exc:
                logger.error("reconciler_error", error=str(exc))

    if settings.reconcile_interval_seconds > 0:
        threading.Thread(target=loop, daemon=True, name="job-reconciler").start()
    return stop
