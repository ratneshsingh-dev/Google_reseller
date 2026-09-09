"""
Real Gmail API email adapter using Domain-Wide Delegation (DWD).

Uses the service account credentials + GOOGLE_ADMIN_EMAIL from .env to
impersonate the admin and send emails via the Gmail API.
"""

from __future__ import annotations

import base64
import os
from email.mime.text import MIMEText
from typing import Any, Dict

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.services.email_service import EmailService

_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailEmailService(EmailService):
    """Real Gmail API email service using DWD."""

    def __init__(self) -> None:
        credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        self._admin_email = os.getenv("GOOGLE_ADMIN_EMAIL", "")

        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=_SCOPES,
        )
        # Impersonate the admin email via Domain-Wide Delegation
        delegated_credentials = credentials.with_subject(self._admin_email)
        self._service = build(
            "gmail", "v1", credentials=delegated_credentials, cache_discovery=False
        )

    async def send_confirmation_email(
        self, recipient: str, subject: str, body: str
    ) -> Dict[str, Any]:
        """Send an email to the recipient using Gmail API."""
        message = MIMEText(body)
        message["to"] = recipient
        message["from"] = self._admin_email
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        result = (
            self._service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )

        return {"status": "SENT", "message_id": result["id"]}
