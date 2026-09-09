"""
Repository for the 'audit_log' Firestore collection.
"""

from __future__ import annotations

from typing import List

from app.models.reseller_models import AuditLogDocument
from app.repositories.firestore_client import BaseStore


class AuditRepository:
    """CRUD for reseller audit log entries."""

    COLLECTION = "audit_log"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: AuditLogDocument) -> AuditLogDocument:
        """Persist a new audit log entry."""
        self._store.set(
            self.COLLECTION, doc.log_id, doc.model_dump(mode="json")
        )
        return doc

    def get_by_reseller(self, reseller_id: str) -> List[AuditLogDocument]:
        """Fetch all audit entries for a specific reseller, newest first."""
        results = self._store.query(
            self.COLLECTION, "reseller_id", "==", reseller_id
        )
        docs = [AuditLogDocument(**r) for r in results]
        # Sort newest first
        docs.sort(key=lambda d: d.timestamp or "", reverse=True)
        return docs

    def list_all(self) -> List[AuditLogDocument]:
        """List all audit entries across all resellers."""
        results = self._store.list_all(self.COLLECTION)
        docs = [AuditLogDocument(**r) for r in results]
        docs.sort(key=lambda d: d.timestamp or "", reverse=True)
        return docs
