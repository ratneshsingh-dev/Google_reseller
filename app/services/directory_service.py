"""
Abstract interface for the Google Admin SDK Directory API.

Concrete implementations:
  - MockDirectoryService  (app.adapters.mock.mock_directory)
  - GoogleDirectoryService (app.adapters.google.google_directory)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from app.models.google_api import (
    GoogleCreateUserRequest,
    GoogleUpdateUserRequest,
    GoogleUser,
)


class DirectoryService(ABC):
    """Interface for Admin Directory API user operations."""

    @abstractmethod
    async def get_user(self, user_email: str) -> GoogleUser:
        """Retrieve a user by primary email."""
        ...

    @abstractmethod
    async def get_user_safe(self, user_email: str) -> Optional[GoogleUser]:
        """Retrieve a user by primary email. Returns None if not found."""
        ...

    @abstractmethod
    async def create_user(self, request: GoogleCreateUserRequest) -> GoogleUser:
        """Create a new user account."""
        ...

    @abstractmethod
    async def update_user(
        self, user_email: str, request: GoogleUpdateUserRequest
    ) -> GoogleUser:
        """Update an existing user."""
        ...

    @abstractmethod
    async def suspend_user(self, user_email: str) -> GoogleUser:
        """Suspend a user account."""
        ...

    @abstractmethod
    async def delete_user(self, user_email: str) -> None:
        """Delete a user account."""
        ...

    @abstractmethod
    async def list_users(self, domain: str) -> List[GoogleUser]:
        """List all users in a domain."""
        ...

    @abstractmethod
    async def make_admin(self, user_email: str) -> None:
        """Assign super admin privileges to a user."""
        ...
