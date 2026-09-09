"""
Mock implementation of the Email Service.

Instead of sending real emails, stores them in the notifications repository
and prints a safe preview to logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.logging import get_logger
from app.models.database import NotificationDocument, NotificationStatus
from app.repositories.notification_repository import NotificationRepository
from app.services.email_service import EmailService

logger = get_logger(__name__)


class MockEmailService(EmailService):
    """Mock email service that stores emails in Firestore/in-memory."""

    def __init__(self, notification_repo: NotificationRepository) -> None:
        self._notification_repo = notification_repo

    async def send_confirmation_email(
        self, recipient: str, subject: str, body: str
    ) -> Dict[str, Any]:
        """Store email in notifications collection and log a safe preview."""

        notification_id = f"NOTIF-{uuid.uuid4().hex[:12].upper()}"

        notification = NotificationDocument(
            notification_id=notification_id,
            job_id="",  # Will be set by caller if available
            recipient=recipient,
            subject=subject,
            body=body,
            status=NotificationStatus.SENT.value,
            sent_at=datetime.now(timezone.utc),
        )

        self._notification_repo.create(notification)

        # Log a safe preview (no passwords)
        logger.info(
            "mock_email_sent",
            notification_id=notification_id,
            recipient=recipient,
            subject=subject,
            body_preview=body[:200] + "..." if len(body) > 200 else body,
        )

        print(f"\n{'='*60}")
        print(f"  MOCK EMAIL SENT")
        print(f"{'='*60}")
        print(f"  To:      {recipient}")
        print(f"  Subject: {subject}")
        print(f"  {'-'*56}")
        print(f"  {body}")
        print(f"{'='*60}\n")

        return {
            "status": "SENT",
            "message_id": notification_id,
            "recipient": recipient,
        }
