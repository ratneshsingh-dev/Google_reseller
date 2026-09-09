"""
Repository for the 'employees' collection.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.database import EmployeeDocument
from app.repositories.firestore_client import BaseStore


class EmployeeRepository:
    """CRUD operations for employee documents."""

    COLLECTION = "employees"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: EmployeeDocument) -> EmployeeDocument:
        self._store.set(
            self.COLLECTION, doc.employee_id, doc.model_dump(mode="json")
        )
        return doc

    def get_by_id(self, employee_id: str) -> Optional[EmployeeDocument]:
        data = self._store.get(self.COLLECTION, employee_id)
        if data:
            return EmployeeDocument(**data)
        return None

    def get_by_email(self, corporate_email: str) -> Optional[EmployeeDocument]:
        results = self._store.query(
            self.COLLECTION, "corporate_email", "==", corporate_email.lower()
        )
        if results:
            return EmployeeDocument(**results[0])
        return None

    def get_by_company_id(self, company_id: str) -> List[EmployeeDocument]:
        results = self._store.query(
            self.COLLECTION, "company_id", "==", company_id
        )
        return [EmployeeDocument(**r) for r in results]

    def update_status(self, employee_id: str, status: str, **kwargs) -> None:
        data = {"status": status}
        data.update(kwargs)
        self._store.update(self.COLLECTION, employee_id, data)

    def list_all(self) -> List[EmployeeDocument]:
        results = self._store.list_all(self.COLLECTION)
        return [EmployeeDocument(**r) for r in results]
