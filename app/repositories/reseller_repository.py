"""
Repository for the 'resellers' Firestore collection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.models.reseller_models import ResellerDocument
from app.repositories.firestore_client import BaseStore


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
        """Atomically (best-effort) increment the licences_used counter."""
        reseller = self.get_by_id(reseller_id)
        if reseller:
            new_count = reseller.licences_used + count
            self.update(reseller_id, {"licences_used": new_count})

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
