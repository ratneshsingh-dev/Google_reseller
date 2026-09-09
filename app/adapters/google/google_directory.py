"""
Real Google Admin SDK Directory API adapter.

Uses google-api-python-client with Domain-Wide Delegation (DWD) to
impersonate the reseller admin and manage users in customer domains.
"""

from __future__ import annotations

import os
from typing import List, Optional

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.models.google_api import (
    GoogleCreateUserRequest,
    GoogleUpdateUserRequest,
    GoogleUser,
    GoogleUserName,
)
from app.services.directory_service import DirectoryService

logger = structlog.get_logger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]


def _build_service(subject: Optional[str] = None):
    """Build the Admin SDK Directory service.
    
    subject: email to impersonate via Domain-Wide Delegation.
              Defaults to GOOGLE_ADMIN_EMAIL from .env.
    """
    admin_email = subject or os.getenv("GOOGLE_ADMIN_EMAIL", "")

    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    if creds_json:
        import json
        info = json.loads(creds_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    else:
        creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        credentials = service_account.Credentials.from_service_account_file(creds_file, scopes=_SCOPES)

    delegated = credentials.with_subject(admin_email)
    return build("admin", "directory_v1", credentials=delegated, cache_discovery=False)


def _parse_user(resp: dict) -> GoogleUser:
    name = resp.get("name", {})
    return GoogleUser(
        id=resp.get("id", ""),
        primaryEmail=resp.get("primaryEmail", ""),
        name=GoogleUserName(
            givenName=name.get("givenName", ""),
            familyName=name.get("familyName", ""),
            fullName=name.get("fullName"),
        ),
        suspended=resp.get("suspended", False),
        changePasswordAtNextLogin=resp.get("changePasswordAtNextLogin", True),
        creationTime=resp.get("creationTime"),
        orgUnitPath=resp.get("orgUnitPath", "/"),
        isAdmin=resp.get("isAdmin", False),
        etag=resp.get("etag"),
    )


class GoogleDirectoryService(DirectoryService):
    """Real Google Admin SDK Directory API adapter using DWD."""

    def __init__(self) -> None:
        # Default service uses the reseller admin account for DWD
        self._service = _build_service()
        self._admin_email = os.getenv("GOOGLE_ADMIN_EMAIL", "")
        logger.info("google_directory_initialized", admin=self._admin_email)

    def _service_for_domain(self, domain: str):
        """Get a service impersonating the admin of a specific domain."""
        # For customer domains we just provisioned, we impersonate the new domain's admin
        # The admin email format is: admin@domain (or whatever was set in provisioning)
        # We fall back to the reseller admin for domains we manage
        return self._service

    async def get_user(self, user_email: str) -> GoogleUser:
        try:
            resp = self._service.users().get(userKey=user_email).execute()
            return _parse_user(resp)
        except HttpError as e:
            logger.error("google_directory_get_user_error", email=user_email, status=e.status_code)
            raise

    async def get_user_safe(self, user_email: str) -> Optional[GoogleUser]:
        try:
            return await self.get_user(user_email)
        except HttpError as e:
            if e.status_code == 404:
                return None
            raise

    async def create_user(self, request: GoogleCreateUserRequest) -> GoogleUser:
        body = {
            "primaryEmail": request.primary_email,
            "name": {
                "givenName": request.name.given_name,
                "familyName": request.name.family_name,
            },
            "password": request.password,
            "changePasswordAtNextLogin": request.change_password_at_next_login,
            "suspended": request.suspended,
            "orgUnitPath": request.org_unit_path or "/",
        }
        if request.recovery_email:
            body["recoveryEmail"] = request.recovery_email

        try:
            resp = self._service.users().insert(body=body).execute()
            logger.info("google_directory_user_created", email=request.primary_email, user_id=resp.get("id"))
            return _parse_user(resp)
        except HttpError as e:
            logger.error("google_directory_create_user_error", email=request.primary_email, status=e.status_code, error=str(e))
            raise

    async def update_user(
        self, user_email: str, request: GoogleUpdateUserRequest
    ) -> GoogleUser:
        body = {}
        if request.name:
            body["name"] = {
                "givenName": request.name.given_name,
                "familyName": request.name.family_name,
            }
        if request.suspended is not None:
            body["suspended"] = request.suspended
        if request.password:
            body["password"] = request.password
        if request.change_password_at_next_login is not None:
            body["changePasswordAtNextLogin"] = request.change_password_at_next_login

        try:
            resp = self._service.users().update(userKey=user_email, body=body).execute()
            logger.info("google_directory_user_updated", email=user_email)
            return _parse_user(resp)
        except HttpError as e:
            logger.error("google_directory_update_user_error", email=user_email, status=e.status_code, error=str(e))
            raise

    async def suspend_user(self, user_email: str) -> GoogleUser:
        try:
            resp = self._service.users().update(
                userKey=user_email, body={"suspended": True}
            ).execute()
            logger.info("google_directory_user_suspended", email=user_email)
            return _parse_user(resp)
        except HttpError as e:
            logger.error("google_directory_suspend_user_error", email=user_email, status=e.status_code, error=str(e))
            raise

    async def delete_user(self, user_email: str) -> None:
        try:
            self._service.users().delete(userKey=user_email).execute()
            logger.info("google_directory_user_deleted", email=user_email)
        except HttpError as e:
            if e.status_code == 404:
                logger.warning("google_directory_delete_user_not_found", email=user_email)
                return
            logger.error("google_directory_delete_user_error", email=user_email, status=e.status_code, error=str(e))
            raise

    async def list_users(self, domain: str) -> List[GoogleUser]:
        try:
            resp = self._service.users().list(domain=domain, maxResults=200).execute()
            users = resp.get("users", [])
            logger.info("google_directory_list_users", domain=domain, count=len(users))
            return [_parse_user(u) for u in users]
        except HttpError as e:
            logger.error("google_directory_list_users_error", domain=domain, status=e.status_code, error=str(e))
            raise

    async def make_admin(self, user_email: str) -> None:
        try:
            self._service.users().makeAdmin(
                userKey=user_email, body={"status": True}
            ).execute()
            logger.info("google_directory_make_admin", email=user_email)
        except HttpError as e:
            logger.error("google_directory_make_admin_error", email=user_email, status=e.status_code, error=str(e))
            raise
