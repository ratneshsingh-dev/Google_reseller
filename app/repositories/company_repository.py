"""
Repository for the 'companies' collection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.models.database import CompanyDocument
from app.repositories.firestore_client import BaseStore


class CompanyRepository:
    """CRUD operations for company documents."""

    COLLECTION = "companies"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: CompanyDocument) -> CompanyDocument:
        self._store.set(self.COLLECTION, doc.company_id, doc.model_dump(mode="json"))
        return doc

    def get_by_id(self, company_id: str) -> Optional[CompanyDocument]:
        data = self._store.get(self.COLLECTION, company_id)
        if data:
            return CompanyDocument(**data)
        return None

    def get_by_domain(self, domain: str) -> Optional[CompanyDocument]:
        results = self._store.query(
            self.COLLECTION, "primary_domain", "==", domain.lower()
        )
        if results:
            return CompanyDocument(**results[0])
        return None

    def update_status(self, company_id: str, status: str) -> None:
        self._store.update(
            self.COLLECTION,
            company_id,
            {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()},
        )

    def update(self, company_id: str, data: dict) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._store.update(self.COLLECTION, company_id, data)

    def list_all(self) -> List[CompanyDocument]:
        results = self._store.list_all(self.COLLECTION)
        return [CompanyDocument(**r) for r in results]
