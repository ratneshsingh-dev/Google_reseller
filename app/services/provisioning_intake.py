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

import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.core import job_dispatcher
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.database import JobStatus, ProvisioningJobDocument
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import AuditLogDocument, QuotaSummary, ResellerDocument
from app.repositories.audit_repository import AuditRepository
from app.repositories.coordination_repository import (
    IdempotencyRepository,
    LockRepository,
    PlanMixRepository,
    fingerprint,
)
from app.repositories.firestore_client import BaseStore
from app.repositories.job_repository import JobRepository
from app.repositories.batching import ContentionError
from app.repositories.reseller_repository import QuotaExceededError, ResellerRepository

logger = get_logger(__name__)

PROVISION_LOCK_SECONDS = 15 * 60
BATCH_COLLECTION = "provisioning_batches"
_TERMINAL = {JobStatus.COMPLETED.value, JobStatus.PARTIAL_FAILURE.value, JobStatus.FAILED.value}


COMMITMENT_PLAN = "ANNUAL_MONTHLY_PAY"
DEFAULT_PLAN = "FLEXIBLE"


class IntakeError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def _busy() -> IntakeError:
    return IntakeError(503, {
        "error": "Service busy",
        "detail": "Too many requests are being processed for your account at this moment. "
                  "Nothing was created; please retry in a few seconds.",
        "retry_after_seconds": 5,
    })


def plan_for_slot(slot: int, every: int) -> str:
    """Slot numbers start at 1. Every `every`-th slot (4, 8, 12, ...) is the commitment plan."""
    return COMMITMENT_PLAN if every > 0 and slot % every == 0 else DEFAULT_PLAN


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
        plan_mix_repo: Optional[PlanMixRepository] = None,
    ) -> None:
        self._plan_mix = plan_mix_repo or PlanMixRepository(store)
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

    def check_plan_rules(self, request: ProvisioningRequest) -> None:
        """Rules that depend on the plan mix. Raises IntakeError(422) when a request breaks them."""
        settings = get_settings()
        if settings.plan_mix_enabled:
            # The widest cap is checked here; a request above the commitment cap is made FLEXIBLE
            # (see _forced_flexible), so only more than the flexible cap is refused outright.
            limit = settings.plan_mix_flexible_max_licences
            if request.license_count > limit:
                raise IntakeError(422, {
                    "error": "Licence cap per domain exceeded",
                    "detail": (f"A domain can have at most {limit} licences on the Flexible plan "
                               f"and {settings.plan_mix_max_licences} on the Annual (commitment) plan; "
                               f"requested {request.license_count}."),
                })
        elif not request.plan:
            raise IntakeError(422, "plan is required: one of TRIAL, FLEXIBLE, ANNUAL_MONTHLY_PAY, ANNUAL_YEARLY_PAY.")

    @staticmethod
    def _forced_flexible(request: ProvisioningRequest) -> bool:
        """With the mix on, a domain asking for more licences than a commitment domain may hold is
        always FLEXIBLE and does not use up a plan slot (so the 1-in-4 pattern is unaffected)."""
        return request.license_count > get_settings().plan_mix_max_licences

    def accept(
        self,
        reseller: ResellerDocument,
        request: ProvisioningRequest,
        idempotency_key: Optional[str] = None,
        batch_id: Optional[str] = None,
        ip: str = "",
        assigned_plan: Optional[str] = None,
    ) -> Dict[str, Any]:
        """`assigned_plan` is set by accept_bulk, which hands out its plan slots in one block;
        for a single request the next slot of the partner's running counter is taken here."""
        job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
        domain = request.primary_domain
        seats = request.license_count
        self.check_plan_rules(request)

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
        except ContentionError as exc:
            # Nothing was reserved, so release everything we took and let the client retry.
            self._locks.release(domain, token)
            self._forget(reseller, idempotency_key, job_id)
            logger.error("quota_update_contention", reseller_id=reseller.reseller_id, domain=domain)
            raise _busy() from exc

        try:
            settings = get_settings()
            if settings.plan_mix_enabled:
                # Taken only now, after the request has passed every check, so refused requests
                # never use up a slot and any 100 accepted requests contain exactly 25 commitments.
                if self._forced_flexible(request):
                    plan = DEFAULT_PLAN        # too many licences for a commitment domain: no slot used
                else:
                    plan = assigned_plan or plan_for_slot(
                        self._plan_mix.reserve(reseller.reseller_id, 1) + 1, settings.plan_mix_annual_every)
                request = request.model_copy(update={"plan": plan})
            self._jobs.create_job(ProvisioningJobDocument(
                job_id=job_id,
                company_name=request.company_name,
                primary_domain=domain,
                status=JobStatus.PENDING.value,
                reseller_id=reseller.reseller_id,
                plan=request.plan,
                licences_reserved=seats,
                batch_id=batch_id,
                request_payload=request.model_dump(mode="json"),
                lock_token=token,
                settled=False,
            ))
            job_dispatcher.dispatch(job_id, lambda: self.run_job(job_id))
        except Exception as exc:
            self._resellers.increment_licences_used(reseller.reseller_id, -seats)
            self._locks.release(domain, token)
            self._forget(reseller, idempotency_key, job_id)
            if isinstance(exc, ContentionError):
                raise _busy() from exc
            raise

        self._record(reseller.reseller_id, "PROVISION", job_id, seats, ip, {"domain": domain, "batch_id": batch_id})
        logger.info("provision_accepted", job_id=job_id, domain=domain, reseller_id=reseller.reseller_id, seats=seats)
        return {
            "job_id": job_id,
            "status": JobStatus.PENDING.value,
            "primary_domain": domain,
            "plan": request.plan,
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

    # ------------------------------------------------------------------
    # Running a job (same code for the in-memory pool and Cloud Tasks)
    # ------------------------------------------------------------------

    async def run_job(self, job_id: str) -> str:
        """Run (or resume) a job from its stored record. Safe to call more than once:
        a finished job is a no-op, a job another live worker holds returns "busy", and a
        job whose worker died is taken over. Returns done | busy | missing | completed | failed.
        """
        settings = get_settings()
        owner = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"
        lease = settings.job_heartbeat_seconds * 3
        state = self._jobs.claim(job_id, owner, lease_seconds=lease)
        if state != "claimed":
            return state

        job = self._jobs.get_job(job_id)
        request = ProvisioningRequest(**job.request_payload)
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(job, stop, settings.job_heartbeat_seconds),
                                daemon=True, name=f"heartbeat-{job_id}")
        beat.start()
        charged, outcome = 0, "failed"
        try:
            charged = await self._service_factory().provision_company(request, job_id, reseller_id=job.reseller_id)
            outcome = "completed"
        except Exception as exc:
            logger.error("job_failed", job_id=job_id, domain=job.primary_domain, reseller_id=job.reseller_id,
                         attempt=job.attempts + 1, error=str(exc))
            current = self._jobs.get_job(job_id)
            charged = current.licences_added if current else 0
            if current and current.status not in _TERMINAL:
                self._jobs.update_job_status(job_id, JobStatus.FAILED.value, error_message=str(exc))
        finally:
            stop.set()
        self.settle(job_id, charged)
        return outcome

    def _heartbeat(self, job: ProvisioningJobDocument, stop: threading.Event, every: int) -> None:
        """Prove the worker is alive and keep the domain lock from expiring while it runs."""
        while not stop.wait(every):
            try:
                self._jobs.heartbeat(job.job_id)
                if job.lock_token:
                    self._locks.renew(job.primary_domain, job.lock_token, PROVISION_LOCK_SECONDS)
            except Exception as exc:
                logger.warning("heartbeat_failed", job_id=job.job_id, error=str(exc))

    def settle(self, job_id: str, charged: int) -> None:
        """Charge what Google actually added, refund the rest of the reservation and release the
        domain lock. Guarded by the job's settled flag, so it happens exactly once."""
        job = self._jobs.mark_settled(job_id, licences_added=charged)
        if job is None:
            return
        refund = max(0, job.licences_reserved - (charged or 0))
        try:
            if refund:
                self._resellers.increment_licences_used(job.reseller_id, -refund)
        except Exception as exc:
            logger.error("quota_refund_failed", job_id=job_id, refund=refund, error=str(exc))
        finally:
            if job.lock_token:
                self._locks.release(job.primary_domain, job.lock_token)
        logger.info("job_settled", job_id=job_id, reserved=job.licences_reserved, charged=charged, refunded=refund)

    # ------------------------------------------------------------------
    # Recovery: jobs whose worker died and will not be resumed
    # ------------------------------------------------------------------

    def reconcile_stale_jobs(self, stale_after_seconds: Optional[int] = None) -> List[str]:
        """Fail jobs with no heartbeat for too long, recording the step they stopped at, and
        settle their quota. Returns the job ids it failed."""
        stale_after = stale_after_seconds or get_settings().job_stale_after_seconds
        now = time.time()
        failed = []
        for job in self._jobs.list_unsettled():
            last_seen = job.heartbeat_at or job.created_at.timestamp()
            if now - last_seen < stale_after:
                continue
            step = job.status
            if job.status not in _TERMINAL:
                self._jobs.update_job_status(
                    job_id=job.job_id,
                    status=JobStatus.FAILED.value,
                    error_message=(
                        f"Interrupted: processing stopped during {step} and did not resume. "
                        f"Check {job.primary_domain} and send the request again if needed."
                    ),
                )
            logger.error("job_interrupted", job_id=job.job_id, domain=job.primary_domain, step=step,
                         reseller_id=job.reseller_id, minutes_silent=int((now - last_seen) // 60))
            self.settle(job.job_id, job.licences_added)
            failed.append(job.job_id)
        return failed

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

        # Phase 1: validate every item. Invalid items are reported and never use a plan slot.
        parsed: Dict[int, ProvisioningRequest] = {}
        rejected: Dict[int, Dict[str, Any]] = {}
        for index, raw in enumerate(items):
            domain = raw.get("primary_domain") if isinstance(raw, dict) else None
            try:
                request = ProvisioningRequest(**raw)
                self.check_plan_rules(request)
                parsed[index] = request
            except (ValidationError, TypeError) as exc:
                rejected[index] = {"index": index, "primary_domain": domain, "status_code": 422,
                                   "error": _validation_message(exc)}
            except IntakeError as exc:
                rejected[index] = {"index": index, "primary_domain": domain,
                                   "status_code": exc.status_code, "error": exc.detail}

        # Phase 2: one block of consecutive plan slots for all valid items, in list order, so
        # 100 valid items always contain exactly 25 commitment-plan domains.
        settings = get_settings()
        plans: Dict[int, str] = {}
        # Items that are forced to FLEXIBLE (too many licences for a commitment domain) take no slot.
        slotted = sorted(i for i, r in parsed.items() if not self._forced_flexible(r))
        if settings.plan_mix_enabled and slotted:
            try:
                first = self._plan_mix.reserve(reseller.reseller_id, len(slotted))
            except ContentionError as exc:
                self._forget(reseller, idempotency_key, batch_id, scope=f"{reseller.reseller_id}:bulk")
                raise _busy() from exc
            for position, index in enumerate(slotted, start=1):
                plans[index] = plan_for_slot(first + position, settings.plan_mix_annual_every)

        def one(index: int) -> Dict[str, Any]:
            if index in rejected:
                return rejected[index]
            request = parsed[index]
            try:
                result = self.accept(reseller, request, batch_id=batch_id, ip=ip, assigned_plan=plans.get(index))
                return {"index": index, "primary_domain": request.primary_domain, "status_code": 202,
                        "job_id": result["job_id"], "status": result["status"], "plan": result["plan"]}
            except IntakeError as exc:
                return {"index": index, "primary_domain": request.primary_domain,
                        "status_code": exc.status_code, "error": exc.detail}
            except Exception as exc:
                logger.error("bulk_item_failed", index=index, error=str(exc))
                return {"index": index, "primary_domain": request.primary_domain,
                        "status_code": 500, "error": "Could not accept this request; try it again."}

        with ThreadPoolExecutor(max_workers=min(20, len(items)), thread_name_prefix="intake") as pool:
            results = list(pool.map(one, range(len(items))))

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

    def _forget(self, reseller: ResellerDocument, key: Optional[str], job_id: str, scope: Optional[str] = None) -> None:
        if key:
            self._idempotency.forget(scope or reseller.reseller_id, key, job_id)

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
