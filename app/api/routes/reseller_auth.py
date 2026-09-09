"""
Reseller / Channel Partner Authentication Routes — Public, no token required.

POST /api/v1/reseller/auth/token
    Authenticate with client_id + client_secret → get JWT access token (24hr)
POST /api/v1/reseller/auth/email-login
    Channel partner logs in via the portal using contact email + client_secret
POST /api/v1/reseller/auth/google-login
    Channel partner logs in via Google OAuth (ID token from Google Sign-In)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.core.jwt_service import (
    create_access_token,
    get_token_expires_in_seconds,
    verify_secret,
)
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.models.reseller_models import (
    AuditLogDocument,
    EmailLoginRequest,
    TokenRequest,
    TokenResponse,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.firestore_client import get_store
from app.repositories.reseller_repository import ResellerRepository
from app.core.config import get_settings

import uuid
import requests as http_requests


logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/reseller/auth", tags=["Reseller Auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Get a JWT access token using client credentials",
    description=(
        "Authenticate with your **client_id** and **client_secret** to receive a JWT access token. "
        "The token is valid for **24 hours**. When it expires, simply call this endpoint again "
        "with the same credentials — no refresh token needed."
    ),
)
@limiter.limit("5/minute")
async def get_token(request: Request, body: TokenRequest) -> TokenResponse:
    """Exchange client credentials for a JWT access token.

    - **client_id**: Your reseller ID (e.g., RSL-A1B2C3D4)
    - **client_secret**: The secret provided when your account was created

    Returns a Bearer token to use in the Authorization header of all API calls.
    """
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())
    ip = request.client.host if request.client else ""

    # Look up reseller by client_id (= reseller_id)
    reseller = reseller_repo.get_by_id(body.client_id)

    def _log_audit(status: str, detail: str = "") -> None:
        try:
            audit_repo.create(
                AuditLogDocument(
                    log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                    reseller_id=body.client_id,
                    action="AUTH_LOGIN",
                    status=status,
                    ip_address=ip,
                    details={"detail": detail},
                )
            )
        except Exception:
            pass

    if not reseller:
        _log_audit("FAILED", "Reseller not found")
        logger.warning("auth_failed_unknown_client", client_id=body.client_id, ip=ip)
        # Generic message to prevent user enumeration
        raise HTTPException(status_code=401, detail="Invalid client_id or client_secret.")

    # Verify secret
    if not verify_secret(body.client_secret, reseller.client_secret_hash):
        _log_audit("FAILED", "Invalid secret")
        logger.warning("auth_failed_bad_secret", reseller_id=reseller.reseller_id, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid client_id or client_secret.")

    # Check account status
    from app.models.reseller_models import ResellerStatus
    if reseller.status == ResellerStatus.SUSPENDED:
        _log_audit("DENIED", "Account suspended")
        raise HTTPException(
            status_code=403,
            detail="Your reseller account has been suspended. Contact admin.",
        )
    if reseller.status == ResellerStatus.DEACTIVATED:
        _log_audit("DENIED", "Account deactivated")
        raise HTTPException(
            status_code=403,
            detail="Your reseller account has been deactivated. Contact admin.",
        )

    # Create JWT token
    access_token = create_access_token(
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
        token_version=reseller.token_version,
    )

    _log_audit("SUCCESS")
    logger.info("auth_success", reseller_id=reseller.reseller_id, ip=ip)

    return TokenResponse(
        access_token=access_token,
        expires_in=get_token_expires_in_seconds(),
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
    )


@router.post(
    "/email-login",
    response_model=TokenResponse,
    summary="Channel Partner portal login via email + client_secret",
    description=(
        "Used by the **Channel Partner Portal**. "
        "Authenticate with your **contact email** and **client_secret** "
        "to receive a JWT access token for the portal."
    ),
)
@limiter.limit("5/minute")
async def email_login(request: Request, body: EmailLoginRequest) -> TokenResponse:
    """Login to the Channel Partner Portal using contact email + client_secret."""
    reseller_repo = ResellerRepository(get_store())
    ip = request.client.host if request.client else ""

    # Look up reseller by contact email
    reseller = reseller_repo.get_by_contact_email(body.email.lower().strip())

    if not reseller:
        logger.warning("email_login_failed_unknown", email=body.email, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid email or client_secret.")

    # Verify secret
    if not verify_secret(body.client_secret, reseller.client_secret_hash):
        logger.warning("email_login_failed_bad_secret", reseller_id=reseller.reseller_id, ip=ip)
        raise HTTPException(status_code=401, detail="Invalid email or client_secret.")

    # Check MANUAL-only partners — they don't have portal access
    if reseller.access_methods == ["MANUAL"]:
        raise HTTPException(
            status_code=403,
            detail="Your account is set to MANUAL access. Contact your admin.",
        )

    from app.models.reseller_models import ResellerStatus
    if reseller.status == ResellerStatus.SUSPENDED:
        raise HTTPException(status_code=403, detail="Your account has been suspended. Contact admin.")
    if reseller.status == ResellerStatus.DEACTIVATED:
        raise HTTPException(status_code=403, detail="Your account has been deactivated. Contact admin.")

    access_token = create_access_token(
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
        token_version=reseller.token_version,
    )

    logger.info("email_login_success", reseller_id=reseller.reseller_id, email=body.email, ip=ip)

    return TokenResponse(
        access_token=access_token,
        expires_in=get_token_expires_in_seconds(),
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
    )


class GoogleLoginRequest(BaseModel if False else object):
    pass


from pydantic import BaseModel as _BaseModel


class GooglePartnerLoginRequest(_BaseModel):
    """Google ID token from Google Sign-In button on the Channel Partner Portal."""
    id_token: str


@router.post(
    "/google-login",
    response_model=TokenResponse,
    summary="Channel Partner portal login via Google Sign-In",
    description=(
        "Used by the **Channel Partner Portal**. "
        "Submit the **Google ID token** from the Google Sign-In button. "
        "The backend verifies the token with Google, extracts the email, "
        "and checks if it matches a registered partner."
    ),
)
@limiter.limit("10/minute")
async def google_login(request: Request, body: GooglePartnerLoginRequest) -> TokenResponse:
    """Login to the Channel Partner Portal using Google OAuth."""
    ip = request.client.host if request.client else ""

    # Verify ID token with Google's tokeninfo endpoint
    try:
        resp = http_requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": body.id_token},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("google_login_invalid_token", ip=ip)
            raise HTTPException(status_code=401, detail="Invalid Google ID token.")
        token_info = resp.json()
    except http_requests.RequestException as exc:
        logger.error("google_login_network_error", error=str(exc))
        raise HTTPException(status_code=502, detail="Could not verify Google token. Try again.")

    email = token_info.get("email", "").lower().strip()
    if not email:
        raise HTTPException(status_code=401, detail="No email in Google token.")

    # Optional: check audience matches our client_id
    settings = get_settings()
    if settings.google_oauth_client_id:
        aud = token_info.get("aud", "")
        if aud != settings.google_oauth_client_id:
            logger.warning("google_login_wrong_audience", aud=aud, ip=ip)
            raise HTTPException(status_code=401, detail="Token audience mismatch.")

    # Look up partner by email
    reseller_repo = ResellerRepository(get_store())
    reseller = reseller_repo.get_by_contact_email(email)

    if not reseller:
        logger.warning("google_login_no_partner", email=email, ip=ip)
        raise HTTPException(
            status_code=403,
            detail=f"No channel partner account found for {email}. Contact your admin."
        )

    if reseller.access_methods == ["MANUAL"]:
        raise HTTPException(
            status_code=403,
            detail="Your account is MANUAL access only. Contact your admin."
        )

    from app.models.reseller_models import ResellerStatus
    if reseller.status == ResellerStatus.SUSPENDED:
        raise HTTPException(status_code=403, detail="Account suspended. Contact admin.")
    if reseller.status == ResellerStatus.DEACTIVATED:
        raise HTTPException(status_code=403, detail="Account deactivated. Contact admin.")

    access_token = create_access_token(
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
        token_version=reseller.token_version,
    )

    logger.info("google_login_success", reseller_id=reseller.reseller_id, email=email, ip=ip)

    return TokenResponse(
        access_token=access_token,
        expires_in=get_token_expires_in_seconds(),
        reseller_id=reseller.reseller_id,
        role=reseller.role.value,
    )
