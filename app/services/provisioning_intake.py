"""
Accepts provisioning requests safely under concurrency and runs them in parallel.

For every request (single or bulk) the order is:
  1. Idempotency-Key  -> a retried request returns the original job
  2. Domain lock      -> only one operation per domain at a time (409 otherwise)
  3. Quota reservation-> licences are taken atomically; the cap can never be exceeded
  4. Job created and handed to the worker pool; the API returns immediately

When the job finishes the partner is charged only what Google actually added; the
rest of the reservation is refunded (all of it if the job failed) and the lock is released.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.core import job_executor
from app.core.logging import get_logger
from app.models.database import JobStatus, ProvisioningJobDocument
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import AuditLogDocument, QuotaSummary, ResellerDocument
from app.repositories.audit_repository import AuditRepository
from app.repositories.coordination_repository import (
    IdempotencyRepository,
    LockRepository,
    fingerprint,
)
from app.repositories.firestore_client import BaseStore
from app.repositories.job_repository import JobRepository
from app.repositories.reseller_repository import QuotaExceededError, ResellerRepository

logger = get_logger(__name__)

PROVISION_LOCK_SECONDS = 15 * 60
BATCH_COLLECTION = "provisioning_batches"
_TERMINAL = {JobStatus.COMPLETED.value, JobStatus.PARTIAL_FAILURE.value, JobStatus.FAILED.value}


class IntakeError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class ProvisioningIntake:
    def __init__(
        self,
        store: BaseStore,
        job_repo: JobRepository,
        reseller_repo: ResellerRepository,
        lock_repo: LockRepository,
        idempotency_repo: IdempotencyRepository,
        audit_repo: AuditRepository,
        provisioning_service_factory,
        max_items: int = 100,
    ) -> None:
        self._store = store
        self._jobs = job_repo
        self._resellers = reseller_repo
        self._locks = lock_repo
        self._idempotency = idempotency_repo
        self._audit = audit_repo
        self._service_factory = provisioning_service_factory
        self._max_items = max_items

    # ------------------------------------------------------------------
    # Single request
    # ------------------------------------------------------------------

    def accept(
        self,
        reseller: ResellerDocument,
        request: ProvisioningRequest,
        idempotency_key: Optional[str] = None,
        batch_id: Optional[str] = None,
        ip: str = "",
    ) -> Dict[str, Any]:
        job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
        domain = request.primary_domain
        seats = request.license_count

        if idempotency_key:
            fp = fingerprint(request.model_dump(mode="json"))
            claimed, record = self._idempotency.claim(reseller.reseller_id, idempotency_key, fp, job_id)
            if not claimed:
                if record.get("fingerprint") != fp:
                    raise IntakeError(409, "This Idempotency-Key was already used for a different request.")
                return self._replay(reseller, record["result_id"], domain)

        token = self._locks.acquire(domain, "provision", PROVISION_LOCK_SECONDS)
        if not token:
            self._forget(reseller, idempotency_key, job_id)
            raise IntakeError(409, {
                "error": "Domain busy",
                "detail": f"{domain} already has a provisioning or change in progress. "
                          "Wait for it to finish, then try again.",
            })

        try:
            after = self._resellers.adjust_licences_used(reseller.reseller_id, seats, enforce_cap=True)
        except QuotaExceededError as exc:
            self._locks.release(domain, token)
            self._forget(reseller, idempotency_key, job_id)
            raise IntakeError(400, exc.as_detail())

        try:
            self._jobs.create_job(ProvisioningJobDocument(
                job_id=job_id,
                company_name=request.company_name,
                primary_domain=domain,
                status=JobStatus.PENDING.value,
                reseller_id=reseller.reseller_id,
                licences_reserved=seats,
                batch_id=batch_id,
            ))
            job_executor.submit(
                lambda: self._run(job_id, request, reseller.reseller_id, token, seats),
                name=job_id,
            )
        except Exception:
            self._resellers.increment_licences_used(reseller.reseller_id, -seats)
            self._locks.release(domain, token)
            self._forget(reseller, idempotency_key, job_id)
            raise

        self._record(reseller.reseller_id, "PROVISION", job_id, seats, ip, {"domain": domain, "batch_id": batch_id})
        logger.info("provision_accepted", job_id=job_id, domain=domain, reseller_id=reseller.reseller_id, seats=seats)
        return {
            "job_id": job_id,
            "status": JobStatus.PENDING.value,
            "primary_domain": domain,
            "quota_summary": QuotaSummary(
                licences_assigned_now=seats,
                total_licences_used=after.licences_used,
                max_licence_cap=after.max_licence_cap,
                licences_remaining=after.licences_remaining,
                message=(
                    f"Reserved {seats} licence(s). {after.licences_remaining} of "
                    f"{after.max_licence_cap} remaining."
                ),
            ).model_dump(),
        }

    async def _run(self, job_id: str, request: ProvisioningRequest, reseller_id: str, token: str, reserved: int) -> None:
        """Runs on a worker thread. Always settles the quota and releases the lock."""
        added = 0
        try:
            added = await self._service_factory().provision_company(request, job_id, reseller_id=reseller_id)
        except Exception as exc:
            logger.error("job_failed", job_id=job_id, error=str(exc))
            job = self._jobs.get_job(job_id)
            added = job.licences_added if job else 0
            if job and job.status not in _TERMINAL:
                self._jobs.update_job_status(job_id, JobStatus.FAILED.value, error_message=str(exc))
        finally:
            refund = max(0, reserved - (added or 0))
            try:
                if refund:
                    self._resellers.increment_licences_used(reseller_id, -refund)
            except Exception as exc:
                logger.error("quota_refund_failed", job_id=job_id, refund=refund, error=str(exc))
            finally:
                self._locks.release(request.primary_domain, token)
            logger.info("job_settled", job_id=job_id, reserved=reserved, charged=added, refunded=refund)

    # ------------------------------------------------------------------
    # Bulk
    # ------------------------------------------------------------------

    def accept_bulk(
        self,
        reseller: ResellerDocument,
        items: List[Dict[str, Any]],
        idempotency_key: Optional[str] = None,
        ip: str = "",
    ) -> Dict[str, Any]:
        if not items:
            raise IntakeError(422, "Send at least one provisioning request in 'requests'.")
        if len(items) > self._max_items:
            raise IntakeError(422, f"At most {self._max_items} requests per bulk call; received {len(items)}.")

        batch_id = f"BATCH-{uuid.uuid4().hex[:10].upper()}"
        if idempotency_key:
            fp = fingerprint(items)
            claimed, record = self._idempotency.claim(f"{reseller.reseller_id}:bulk", idempotency_key, fp, batch_id)
            if not claimed:
                if record.get("fingerprint") != fp:
                    raise IntakeError(409, "This Idempotency-Key was already used for a different request.")
                return self.batch_status(reseller, record["result_id"], replay=True)

        def one(index: int, raw: Dict[str, Any]) -> Dict[str, Any]:
            domain = raw.get("primary_domain") if isinstance(raw, dict) else None
            try:
                request = ProvisioningRequest(**raw)
            except (ValidationError, TypeError) as exc:
                return {"index": index, "primary_domain": domain, "status_code": 422,
                        "error": _validation_message(exc)}
            try:
                result = self.accept(reseller, request, batch_id=batch_id, ip=ip)
                return {"index": index, "primary_domain": request.primary_domain, "status_code": 202,
                        "job_id": result["job_id"], "status": result["status"]}
            except IntakeError as exc:
                return {"index": index, "primary_domain": request.primary_domain,
                        "status_code": exc.status_code, "error": exc.detail}
            except Exception as exc:
                logger.error("bulk_item_failed", index=index, error=str(exc))
                return {"index": index, "primary_domain": request.primary_domain,
                        "status_code": 500, "error": "Could not accept this request; try it again."}

        with ThreadPoolExecutor(max_workers=min(20, len(items)), thread_name_prefix="intake") as pool:
            results = list(pool.map(lambda pair: one(*pair), enumerate(items)))

        accepted = [r for r in results if r["status_code"] == 202]
        self._store.set(BATCH_COLLECTION, batch_id, {
            "batch_id": batch_id,
            "reseller_id": reseller.reseller_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": results,
        })
        logger.info("bulk_accepted", batch_id=batch_id, accepted=len(accepted), rejected=len(results) - len(accepted))
        return {
            "batch_id": batch_id,
            "accepted": len(accepted),
            "rejected": len(results) - len(accepted),
            "results": results,
        }

    def batch_status(self, reseller: ResellerDocument, batch_id: str, replay: bool = False) -> Dict[str, Any]:
        batch = self._store.get(BATCH_COLLECTION, batch_id)
        if not batch or batch.get("reseller_id") != reseller.reseller_id:
            raise IntakeError(404, f"Batch {batch_id} not found.")

        items = batch.get("items", [])
        job_ids = [i["job_id"] for i in items if i.get("job_id")]
        with ThreadPoolExecutor(max_workers=10, thread_name_prefix="batch") as pool:
            jobs = {jid: job for jid, job in zip(job_ids, pool.map(self._jobs.get_job, job_ids))}

        counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "rejected": 0}
        out = []
        for item in items:
            job = jobs.get(item.get("job_id"))
            if not job:
                counts["rejected"] += 1
                out.append(item)
                continue
            if job.status == JobStatus.PENDING.value:
                counts["pending"] += 1
            elif job.status in (JobStatus.COMPLETED.value, JobStatus.PARTIAL_FAILURE.value):
                counts["completed"] += 1
            elif job.status == JobStatus.FAILED.value:
                counts["failed"] += 1
            else:
                counts["in_progress"] += 1
            out.append({
                "index": item["index"],
                "primary_domain": job.primary_domain,
                "job_id": job.job_id,
                "status": job.status,
                "licences_added": job.licences_added,
                "error_message": job.error_message,
            })

        running = counts["pending"] + counts["in_progress"]
        overall = "IN_PROGRESS" if running else (
            "COMPLETED" if counts["failed"] == 0 and counts["rejected"] == 0 else "COMPLETED_WITH_ERRORS"
        )
        response = {
            "batch_id": batch_id,
            "status": overall,
            "total": len(items),
            "counts": counts,
            "results": out,
        }
        if replay:
            response["idempotent_replay"] = True
        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _replay(self, reseller: ResellerDocument, job_id: str, domain: str) -> Dict[str, Any]:
        job = self._jobs.get_job(job_id)
        current = self._resellers.get_by_id(reseller.reseller_id) or reseller
        return {
            "job_id": job_id,
            "status": job.status if job else JobStatus.PENDING.value,
            "primary_domain": domain,
            "idempotent_replay": True,
            "quota_summary": QuotaSummary(
                licences_assigned_now=0,
                total_licences_used=current.licences_used,
                max_licence_cap=current.max_licence_cap,
                licences_remaining=current.licences_remaining,
                message="Same Idempotency-Key as an earlier request: returning the original job.",
            ).model_dump(),
        }

    def _forget(self, reseller: ResellerDocument, key: Optional[str], job_id: str) -> None:
        if key:
            self._idempotency.forget(reseller.reseller_id, key, job_id)

    def _record(self, reseller_id: str, action: str, job_id: str, seats: int, ip: str, details: dict) -> None:
        try:
            self._audit.create(AuditLogDocument(
                log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                reseller_id=reseller_id,
                action=action,
                resource_type="job",
                resource_id=job_id,
                licences_requested=seats,
                ip_address=ip,
                details=details,
            ))
        except Exception:
            logger.warning("intake_audit_write_failed", job_id=job_id)


def _validation_message(exc: Exception) -> Any:
    if isinstance(exc, ValidationError):
        return [
            {"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
    return str(exc)
