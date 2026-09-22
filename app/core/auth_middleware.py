"""
FastAPI dependency functions for authentication and RBAC.

Usage in routes:
    @router.post("/provision")
    async def provision(reseller: ResellerDocument = Depends(require_reseller_token)):
        ...

    @router.post("/resellers")
    async def create_reseller(admin = Depends(require_admin)):
        ...
"""

from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import (
    ForbiddenError,
    InvalidTokenError,
    LicenceCapExceededError,
    ResellerNotFoundError,
    ResellerSuspendedError,
    TokenExpiredError,
    UnauthorizedError,
)
from app.core.jwt_service import decode_token
from app.core.logging import get_logger
from app.models.reseller_models import (
    QuotaSummary,
    ResellerDocument,
    ResellerRole,
    ResellerStatus,
)

logger = get_logger(__name__)

# FastAPI's built-in Bearer token extractor
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Reseller JWT Authentication
# ---------------------------------------------------------------------------


async def require_reseller_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> ResellerDocument:
    """FastAPI dependency: authenticate reseller via JWT Bearer token.

    Steps:
      1. Extract Bearer token from Authorization header
      2. Decode + validate JWT (signature, expiry)
      3. Fetch reseller from Firestore by reseller_id
      4. Verify token_version matches (instant revocation check)
      5. Verify reseller status == ACTIVE
      6. Update last_api_call_at timestamp
      7. Return ResellerDocument for use in the route

    Raises:
        401 — token missing, invalid, or expired
        403 — reseller is suspended / deactivated
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication token. Include 'Authorization: Bearer <token>' header.",
        )

    token = credentials.credentials

    # Decode JWT
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired. Please re-authenticate via POST /api/v1/reseller/auth/token.",
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {exc}",
        )

    reseller_id = payload.get("sub")
    token_version = payload.get("token_version", 0)
    token_type = payload.get("type")

    if token_type != "access" or not reseller_id:
        raise HTTPException(status_code=401, detail="Invalid token payload.")

    # Fetch reseller from storage
    from app.repositories.reseller_repository import ResellerRepository
    from app.repositories.firestore_client import get_store

    repo = ResellerRepository(get_store())
    reseller = repo.get_by_id(reseller_id)

    if not reseller:
        raise HTTPException(status_code=401, detail="Reseller account not found.")

    # Check token_version for instant revocation support
    if reseller.token_version != token_version:
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked. Please re-authenticate.",
        )

    # Check reseller status
    if reseller.status == ResellerStatus.SUSPENDED:
        raise HTTPException(
            status_code=403,
            detail="Your reseller account has been suspended. Contact admin.",
        )
    if reseller.status == ResellerStatus.DEACTIVATED:
        raise HTTPException(
            status_code=403,
            detail="Your reseller account has been deactivated. Contact admin.",
        )

    # Update last_api_call_at (best-effort)
    try:
        from datetime import datetime, timezone
        repo.update(reseller_id, {"last_api_call_at": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass

    logger.info("reseller_authenticated", reseller_id=reseller_id, role=reseller.role)
    return reseller


# ---------------------------------------------------------------------------
# Admin Authentication (session cookie)
# ---------------------------------------------------------------------------


async def require_admin(request: Request) -> dict:
    """FastAPI dependency: authenticate admin via a signed session cookie.

    The `session_user` cookie holds a signed JWT (not a raw email) so it
    cannot be forged by simply setting a cookie value — the signature is
    verified here before the email inside it is trusted at all.

    Checks that the logged-in user's email is in:
      1. The admin_emails env var whitelist (always works), OR
      2. The Firestore admin_users collection (dynamic, no redeployment needed)

    Raises:
        401 — not logged in (no cookie, or cookie is invalid/expired)
        403 — logged in but not an admin
    """
    from app.api.routes.auth import _sessions, UserInfo
    from app.core.config import get_settings
    from app.core.jwt_service import decode_admin_session_token
    from app.dependencies import get_admin_repo

    token = request.cookies.get("session_user")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please login via the admin panel.",
        )

    try:
        session_email = decode_admin_session_token(token)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Session invalid or expired. Please login again.",
        )

    settings = get_settings()
    email_lower = session_email.lower()

    # Check 1: static env var whitelist
    is_admin = email_lower in settings.admin_email_list

    # Check 2: dynamic Firestore admin_users collection
    if not is_admin:
        try:
            admin_repo = get_admin_repo()
            is_admin = admin_repo.exists(email_lower)
        except Exception:
            pass  # If Firestore check fails, fall through to deny

    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. {session_email} is not an admin.",
        )

    # Re-hydrate in-memory session if missing (e.g. after server restart)
    if session_email not in _sessions:
        _sessions[session_email] = UserInfo(
            email=session_email,
            name=session_email.split("@")[0],
            picture="",
        )

    return _sessions[session_email]


# ---------------------------------------------------------------------------
# RBAC Role Check
# ---------------------------------------------------------------------------


def require_role(*allowed_roles: ResellerRole):
    """Return a FastAPI dependency that checks the reseller's role.

    Usage:
        @router.post("/provision")
        async def provision(
            reseller = Depends(require_reseller_token),
            _       = Depends(require_role(ResellerRole.RESELLER_FULL)),
        ): ...
    """

    async def _check_role(
        reseller: ResellerDocument = Depends(require_reseller_token),
    ) -> ResellerDocument:
        if reseller.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Your role ({reseller.role}) does not have permission for this action. "
                    f"Required: {', '.join(r.value for r in allowed_roles)}"
                ),
            )
        return reseller

    return _check_role


# ---------------------------------------------------------------------------
# Licence Cap Enforcement
# ---------------------------------------------------------------------------


def build_quota_summary(
    reseller: ResellerDocument,
    just_assigned: int,
) -> QuotaSummary:
    """Build a quota summary dict after licences have been assigned."""
    remaining = reseller.licences_remaining - just_assigned
    return QuotaSummary(
        licences_assigned_now=just_assigned,
        total_licences_used=reseller.licences_used + just_assigned,
        max_licence_cap=reseller.max_licence_cap,
        licences_remaining=max(0, remaining),
        message=(
            f"Successfully assigned {just_assigned} licence(s). "
            f"{max(0, remaining)} of {reseller.max_licence_cap} remaining."
        ),
    )


def check_licence_cap(reseller: ResellerDocument, requested: int) -> None:
    """Raise LicenceCapExceededError if the reseller would exceed their cap.

    Call this BEFORE provisioning to block the request.
    """
    remaining = reseller.licences_remaining
    if requested > remaining:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Licence cap exceeded",
                "detail": (
                    f"Cannot assign {requested} licence(s). "
                    f"You have {remaining} remaining out of {reseller.max_licence_cap}."
                ),
                "quota": {
                    "requested": requested,
                    "licences_used": reseller.licences_used,
                    "max_licence_cap": reseller.max_licence_cap,
                    "licences_remaining": remaining,
                },
            },
        )
