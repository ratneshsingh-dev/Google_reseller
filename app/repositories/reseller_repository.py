"""
Repository for the 'resellers' Firestore collection.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import List, Optional

from app.models.reseller_models import ResellerDocument
from app.repositories.firestore_client import BaseStore

_CAS_ATTEMPTS = 30


class QuotaExceededError(Exception):
    """Reserving these licences would take the partner over their cap."""

    def __init__(self, requested: int, used: int, cap: int) -> None:
        self.requested = requested
        self.used = used
        self.cap = cap
        self.remaining = max(0, cap - used)
        super().__init__(f"Cannot assign {requested} licence(s). You have {self.remaining} remaining out of {cap}.")

    def as_detail(self) -> dict:
        return {
            "error": "Licence cap exceeded",
            "detail": str(self),
            "quota": {
                "requested": self.requested,
                "licences_used": self.used,
                "max_licence_cap": self.cap,
                "licences_remaining": self.remaining,
            },
        }


class ResellerRepository:
    """CRUD operations for reseller partner documents."""

    COLLECTION = "resellers"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: ResellerDocument) -> ResellerDocument:
        """Persist a new reseller document."""
        self._store.set(
            self.COLLECTION, doc.reseller_id, doc.model_dump(mode="json")
        )
        return doc

    def get_by_id(self, reseller_id: str) -> Optional[ResellerDocument]:
        """Fetch a reseller by their ID (= client_id)."""
        data = self._store.get(self.COLLECTION, reseller_id)
        if data:
            return ResellerDocument(**data)
        return None

    def get_by_contact_email(self, email: str) -> Optional[ResellerDocument]:
        """Fetch an ACTIVE reseller by contact email. Skips deactivated/suspended records."""
        results = self._store.query(
            self.COLLECTION, "contact_email", "==", email.lower()
        )
        if results:
            # Prefer ACTIVE first, then any non-DEACTIVATED
            for r in results:
                if r.get("status") == "ACTIVE":
                    return ResellerDocument(**r)
            # fallback to first non-deactivated
            for r in results:
                if r.get("status") != "DEACTIVATED":
                    return ResellerDocument(**r)
        return None

    def list_all(self) -> List[ResellerDocument]:
        """List all resellers."""
        results = self._store.list_all(self.COLLECTION)
        return [ResellerDocument(**r) for r in results]

    def update(self, reseller_id: str, data: dict) -> None:
        """Update arbitrary fields on a reseller document."""
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store.update(self.COLLECTION, reseller_id, data)

    def increment_licences_used(self, reseller_id: str, count: int) -> None:
        """Adjust licences_used by count (negative releases). Atomic, no cap check."""
        self.adjust_licences_used(reseller_id, count, enforce_cap=False)

    def adjust_licences_used(self, reseller_id: str, delta: int, enforce_cap: bool) -> ResellerDocument:
        """Atomically change licences_used using compare-and-swap, retrying on conflict.

        With enforce_cap=True the change is refused (QuotaExceededError) if it would exceed
        max_licence_cap, so parallel requests can never push a partner over their cap.
        """
        for attempt in range(_CAS_ATTEMPTS):
            data, version = self._store.get_versioned(self.COLLECTION, reseller_id)
            if data is None:
                raise KeyError(f"Reseller {reseller_id} not found")
            reseller = ResellerDocument(**data)
            new_used = max(0, reseller.licences_used + delta)
            if enforce_cap and delta > 0 and new_used > reseller.max_licence_cap:
                raise QuotaExceededError(delta, reseller.licences_used, reseller.max_licence_cap)
            data["licences_used"] = new_used
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            if self._store.set_if_version(self.COLLECTION, reseller_id, data, version):
                return ResellerDocument(**data)
            time.sleep(random.uniform(0.005, 0.05) * (attempt + 1))
        raise RuntimeError(f"Could not update licence quota for {reseller_id}: too much contention")

    def increment_token_version(self, reseller_id: str) -> int:
        """Increment token_version to revoke all active JWTs for this reseller.

        Returns the new token_version.
        """
        reseller = self.get_by_id(reseller_id)
        if not reseller:
            return 1
        new_version = reseller.token_version + 1
        self.update(reseller_id, {"token_version": new_version})
        return new_version

    def deactivate(self, reseller_id: str) -> None:
        """Soft-delete: set status to DEACTIVATED."""
        self.update(reseller_id, {"status": "DEACTIVATED"})
