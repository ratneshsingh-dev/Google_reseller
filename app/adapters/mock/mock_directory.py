"""
Mock implementation of the Google Admin SDK Directory API.

Stores users in-memory with deterministic ID generation.
Supports configurable failure simulation via MOCK_FAILURE_RATE.
"""

from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.exceptions import (
    DuplicateEmployeeError,
    RetryableError,
    UserNotFoundError,
)
from app.core.logging import get_logger
from app.models.google_api import (
    GoogleCreateUserRequest,
    GoogleUpdateUserRequest,
    GoogleUser,
    GoogleUserName,
)
from app.services.directory_service import DirectoryService

logger = get_logger(__name__)


class MockDirectoryService(DirectoryService):
    """In-memory mock of the Google Admin SDK Directory API."""

    def __init__(self) -> None:
        self._users: Dict[str, GoogleUser] = {}  # keyed by primaryEmail
        self._user_counter = 0
        self._lock = threading.Lock()

    def _next_user_id(self) -> str:
        with self._lock:
            self._user_counter += 1
            return f"USER-{self._user_counter:06d}"

    def _maybe_fail(self) -> None:
        """Simulate transient failures based on MOCK_FAILURE_RATE."""
        settings = get_settings()
        if settings.mock_failure_mode and random.random() < settings.mock_failure_rate:
            logger.warning("mock_simulated_failure", service="directory")
            raise RetryableError("Simulated transient Directory API failure")

    async def get_user(self, user_email: str) -> GoogleUser:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        user = self._users.get(user_email.lower())
        if not user:
            raise UserNotFoundError(f"User {user_email} not found")
        return user

    async def get_user_safe(self, user_email: str) -> Optional[GoogleUser]:
        """Return user or None — does not raise on not-found."""
        try:
            return await self.get_user(user_email)
        except UserNotFoundError:
            return None

    async def create_user(self, request: GoogleCreateUserRequest) -> GoogleUser:
        self._maybe_fail()
        await asyncio.sleep(0.02)

        email = request.primary_email.lower()

        # Check for duplicate
        if email in self._users:
            raise DuplicateEmployeeError(
                f"User {email} already exists"
            )

        user_id = self._next_user_id()
        full_name = f"{request.name.given_name} {request.name.family_name}"

        user = GoogleUser(
            kind="admin#directory#user",
            id=user_id,
            primaryEmail=email,
            name=GoogleUserName(
                givenName=request.name.given_name,
                familyName=request.name.family_name,
                fullName=full_name,
            ),
            suspended=request.suspended,
            changePasswordAtNextLogin=request.change_password_at_next_login,
            creationTime=datetime.now(timezone.utc).isoformat(),
            orgUnitPath=request.org_unit_path or "/",
        )

        self._users[email] = user

        logger.info(
            "mock_user_created",
            user_id=user_id,
            email=email,
        )
        return user

    async def update_user(
        self, user_email: str, request: GoogleUpdateUserRequest
    ) -> GoogleUser:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        email = user_email.lower()
        existing = self._users.get(email)
        if not existing:
            raise UserNotFoundError(f"User {email} not found")

        updates = {}
        if request.name is not None:
            full_name = f"{request.name.given_name} {request.name.family_name}"
            updates["name"] = GoogleUserName(
                givenName=request.name.given_name,
                familyName=request.name.family_name,
                fullName=full_name,
            )
        if request.suspended is not None:
            updates["suspended"] = request.suspended

        updated = existing.model_copy(update=updates)
        self._users[email] = updated
        return updated

    async def suspend_user(self, user_email: str) -> GoogleUser:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        email = user_email.lower()
        existing = self._users.get(email)
        if not existing:
            raise UserNotFoundError(f"User {email} not found")

        updated = existing.model_copy(update={"suspended": True})
        self._users[email] = updated

        logger.info("mock_user_suspended", email=email)
        return updated

    async def delete_user(self, user_email: str) -> None:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        email = user_email.lower()
        if email not in self._users:
            raise UserNotFoundError(f"User {email} not found")

        del self._users[email]
        logger.info("mock_user_deleted", email=email)

    async def make_admin(self, user_email: str) -> None:
        self._maybe_fail()
        await asyncio.sleep(0.01)
        user = self._users.get(user_email.lower())
        if user:
            user.isAdmin = True
        else:
            raise UserNotFoundError(f"User {user_email} not found")

    async def list_users(self, domain: str) -> List[GoogleUser]:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        domain = domain.lower()
        return [
            user
            for user in self._users.values()
            if user.primary_email.endswith(f"@{domain}")
        ]
