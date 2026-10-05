"""
Repository for the 'provisioning_jobs' and 'provisioning_steps' collections.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import List, Optional

from app.models.database import ProvisioningJobDocument, ProvisioningStepDocument
from app.repositories.firestore_client import BaseStore

_CAS_ATTEMPTS = 30


class JobRepository:
    """CRUD operations for provisioning job and step documents."""

    JOBS_COLLECTION = "provisioning_jobs"
    STEPS_COLLECTION = "provisioning_steps"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    # --- Jobs ---

    def create_job(self, doc: ProvisioningJobDocument) -> ProvisioningJobDocument:
        self._store.set(
            self.JOBS_COLLECTION, doc.job_id, doc.model_dump(mode="json")
        )
        return doc

    def get_job(self, job_id: str) -> Optional[ProvisioningJobDocument]:
        data = self._store.get(self.JOBS_COLLECTION, job_id)
        if data:
            return ProvisioningJobDocument(**data)
        return None

    def get_by_idempotency_key(
        self, key: str
    ) -> Optional[ProvisioningJobDocument]:
        results = self._store.query(
            self.JOBS_COLLECTION, "idempotency_key", "==", key
        )
        if results:
            return ProvisioningJobDocument(**results[0])
        return None

    def update_job_status(self, job_id: str, status: str, **kwargs) -> None:
        data = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        data.update(kwargs)
        self._store.update(self.JOBS_COLLECTION, job_id, data)

    def update_fields(self, job_id: str, **fields) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store.update(self.JOBS_COLLECTION, job_id, fields)

    def list_all_jobs(self) -> List[ProvisioningJobDocument]:
        results = self._store.list_all(self.JOBS_COLLECTION)
        return [ProvisioningJobDocument(**r) for r in results]

    def list_by_reseller(self, reseller_id: str) -> List[ProvisioningJobDocument]:
        results = self._store.query(self.JOBS_COLLECTION, "reseller_id", "==", reseller_id)
        return [ProvisioningJobDocument(**r) for r in results]

    def list_unsettled(self) -> List[ProvisioningJobDocument]:
        """Jobs still holding a quota reservation (running, queued, or abandoned by a crash)."""
        results = self._store.query(self.JOBS_COLLECTION, "settled", "==", False)
        return [ProvisioningJobDocument(**r) for r in results]

    # --- Lease: exactly one worker runs a job at a time; a crashed worker's lease expires ---

    def claim(self, job_id: str, owner: str, lease_seconds: int) -> str:
        """Try to take the job. Returns "claimed", "busy" (another live worker has it),
        "done" (already finished) or "missing"."""
        for _ in range(_CAS_ATTEMPTS):
            data, version = self._store.get_versioned(self.JOBS_COLLECTION, job_id)
            if data is None:
                return "missing"
            job = ProvisioningJobDocument(**data)
            if job.settled:
                return "done"
            now = time.time()
            if job.lease_owner and job.lease_owner != owner and (job.heartbeat_at or 0) > now - lease_seconds:
                return "busy"
            data.update({
                "lease_owner": owner,
                "heartbeat_at": now,
                "attempts": job.attempts + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            if self._store.set_if_version(self.JOBS_COLLECTION, job_id, data, version):
                return "claimed"
            time.sleep(random.uniform(0.01, 0.05))
        return "busy"

    def heartbeat(self, job_id: str) -> None:
        self._store.update(self.JOBS_COLLECTION, job_id, {"heartbeat_at": time.time()})

    def mark_settled(self, job_id: str, **fields) -> Optional[ProvisioningJobDocument]:
        """Flip settled False -> True exactly once. Returns the job if THIS call settled it,
        None if it was already settled (so quota is never refunded twice)."""
        for _ in range(_CAS_ATTEMPTS):
            data, version = self._store.get_versioned(self.JOBS_COLLECTION, job_id)
            if data is None or data.get("settled", True):
                return None
            data.update(fields)
            data.update({"settled": True, "lease_owner": None,
                         "updated_at": datetime.now(timezone.utc).isoformat()})
            if self._store.set_if_version(self.JOBS_COLLECTION, job_id, data, version):
                return ProvisioningJobDocument(**data)
            time.sleep(random.uniform(0.01, 0.05))
        raise RuntimeError(f"Could not settle job {job_id}: too much contention")

    # --- Steps ---

    def add_step(self, doc: ProvisioningStepDocument) -> ProvisioningStepDocument:
        self._store.set(
            self.STEPS_COLLECTION, doc.step_id, doc.model_dump(mode="json")
        )
        return doc

    def update_step(self, step_id: str, **kwargs) -> None:
        self._store.update(self.STEPS_COLLECTION, step_id, kwargs)

    def get_steps_for_job(self, job_id: str) -> List[ProvisioningStepDocument]:
        results = self._store.query(
            self.STEPS_COLLECTION, "job_id", "==", job_id
        )
        return [ProvisioningStepDocument(**r) for r in results]
