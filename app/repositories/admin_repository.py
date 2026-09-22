"""
Admin user repository — manages the list of admin emails in Firestore.

Admins stored here can access the Admin Panel without a server redeployment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import structlog

from app.repositories.firestore_client import FirestoreStore

logger = structlog.get_logger(__name__)

_COLLECTION = "admin_users"


class AdminUserDocument:
    def __init__(
        self,
        email: str,
        name: str = "",
        added_by: str = "",
        added_at: Optional[datetime] = None,
        is_active: bool = True,
    ):
        self.email = email.lower().strip()
        self.name = name
        self.added_by = added_by
        self.added_at = added_at or datetime.now(timezone.utc)
        self.is_active = is_active

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "name": self.name,
            "added_by": self.added_by,
            "added_at": self.added_at.isoformat(),
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AdminUserDocument":
        return cls(
            email=d.get("email", ""),
            name=d.get("name", ""),
            added_by=d.get("added_by", ""),
            is_active=d.get("is_active", True),
        )


class AdminRepository:
    def __init__(self, store: FirestoreStore):
        self._store = store

    def get_all_active(self) -> List[AdminUserDocument]:
        """Return all active admin emails from Firestore."""
        try:
            docs = self._store.list_all(_COLLECTION)
            return [
                AdminUserDocument.from_dict(d)
                for d in docs
                if d.get("is_active", True)
            ]
        except Exception as e:
            logger.warning("admin_repo_list_failed", error=str(e))
            return []

    def get_active_emails(self) -> List[str]:
        """Return just the active admin email addresses (lowercased)."""
        return [a.email for a in self.get_all_active()]

    def add(self, email: str, name: str = "", added_by: str = "") -> AdminUserDocument:
        """Add a new admin email. If already exists, re-activates it."""
        email = email.lower().strip()
        doc = AdminUserDocument(email=email, name=name, added_by=added_by)
        self._store.set(_COLLECTION, email, doc.to_dict())
        logger.info("admin_added", email=email, by=added_by)
        return doc

    def remove(self, email: str, removed_by: str = "") -> bool:
        """Soft-delete an admin (marks is_active=False)."""
        email = email.lower().strip()
        try:
            self._store.update(_COLLECTION, email, {"is_active": False})
            logger.info("admin_removed", email=email, by=removed_by)
            return True
        except Exception as e:
            logger.warning("admin_remove_failed", email=email, error=str(e))
            return False

    def exists(self, email: str) -> bool:
        """Check if an email is an active admin."""
        email = email.lower().strip()
        try:
            doc = self._store.get(_COLLECTION, email)
            return doc is not None and doc.get("is_active", True)
        except Exception:
            return False
