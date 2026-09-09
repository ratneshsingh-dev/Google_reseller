"""
Repository for the 'provisioning_jobs' and 'provisioning_steps' collections.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.models.database import ProvisioningJobDocument, ProvisioningStepDocument
from app.repositories.firestore_client import BaseStore


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

    def list_all_jobs(self) -> List[ProvisioningJobDocument]:
        results = self._store.list_all(self.JOBS_COLLECTION)
        return [ProvisioningJobDocument(**r) for r in results]

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
