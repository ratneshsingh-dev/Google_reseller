"""
Abstract interface for email notifications.

Concrete implementations:
  - MockEmailService  (app.adapters.mock.mock_email)
  - GmailEmailService (app.adapters.google.gmail_email)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class EmailService(ABC):
    """Interface for sending email notifications."""

    @abstractmethod
    async def send_confirmation_email(
        self, recipient: str, subject: str, body: str
    ) -> Dict[str, Any]:
        """Send a confirmation email.

        Returns:
            Dict with at least: {"status": "SENT"|"FAILED", "message_id": str}
        """
        ...
