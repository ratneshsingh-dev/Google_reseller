"""
Repository for the 'notifications' collection.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.database import NotificationDocument
from app.repositories.firestore_client import BaseStore


class NotificationRepository:
    """CRUD operations for notification documents."""

    COLLECTION = "notifications"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def create(self, doc: NotificationDocument) -> NotificationDocument:
        self._store.set(
            self.COLLECTION, doc.notification_id, doc.model_dump(mode="json")
        )
        return doc

    def get_by_id(
        self, notification_id: str
    ) -> Optional[NotificationDocument]:
        data = self._store.get(self.COLLECTION, notification_id)
        if data:
            return NotificationDocument(**data)
        return None

    def get_by_job_id(self, job_id: str) -> List[NotificationDocument]:
        results = self._store.query(
            self.COLLECTION, "job_id", "==", job_id
        )
        return [NotificationDocument(**r) for r in results]

    def update_status(self, notification_id: str, status: str, **kwargs) -> None:
        data = {"status": status}
        data.update(kwargs)
        self._store.update(self.COLLECTION, notification_id, data)
