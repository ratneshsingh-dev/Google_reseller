"""
Repository for the 'subscriptions' collection.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.database import SubscriptionDocument
from app.repositories.firestore_client import BaseStore


class SubscriptionRepository:
    """CRUD operations for subscription documents."""

    COLLECTION = "subscriptions"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: SubscriptionDocument) -> SubscriptionDocument:
        self._store.set(
            self.COLLECTION, doc.subscription_id, doc.model_dump(mode="json")
        )
        return doc

    def get_by_id(self, subscription_id: str) -> Optional[SubscriptionDocument]:
        data = self._store.get(self.COLLECTION, subscription_id)
        if data:
            return SubscriptionDocument(**data)
        return None

    def get_by_company_id(self, company_id: str) -> List[SubscriptionDocument]:
        results = self._store.query(
            self.COLLECTION, "company_id", "==", company_id
        )
        return [SubscriptionDocument(**r) for r in results]

    def update(self, subscription_id: str, data: dict) -> None:
        self._store.update(self.COLLECTION, subscription_id, data)

    def list_all(self) -> List[SubscriptionDocument]:
        results = self._store.list_all(self.COLLECTION)
        return [SubscriptionDocument(**r) for r in results]
