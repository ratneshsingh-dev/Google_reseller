"""
Authentication routes — Google OAuth Sign-In.

Verifies Google ID tokens from the frontend Sign-In button and manages
session cookies so the logged-in user's email is available for notifications.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import limiter

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

# ---------- Request / Response models ----------

class GoogleLoginRequest(BaseModel):
    """Payload sent by the frontend after Google Sign-In."""
    credential: str  # The Google ID token (JWT)


class UserInfo(BaseModel):
    """Minimal user profile returned after login."""
    email: str
    name: str
    picture: str = ""
    given_name: str = ""


# ---------- In-memory session store (simple cookie-based) ----------
# For production, replace with Redis / signed JWT sessions.
_sessions: dict[str, UserInfo] = {}


def _verify_google_token(token: str) -> dict:
    """Verify the Google ID token and return the payload.

    Uses the google-auth library to validate against Google's public certs.
    Falls back to manual JWT decode if google-auth is unavailable.
    """
    settings = get_settings()
    client_id = settings.google_oauth_client_id

    if not client_id:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_OAUTH_CLIENT_ID is not configured in .env",
        )

    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token

        request_adapter = google.auth.transport.requests.Request()
        payload = id_token.verify_oauth2_token(
            token, request_adapter, client_id
        )
        return payload
    except ValueError as exc:
        logger.error("google_token_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=401,
            detail=f"Invalid Google token: {exc}",
        )
    except Exception as exc:
        logger.error("google_token_unexpected_error", error=str(exc))
        raise HTTPException(
            status_code=401,
            detail=f"Token verification failed: {exc}",
        )


# ---------- Routes ----------

@router.post("/google", response_model=UserInfo)
@limiter.limit("5/minute")
async def google_login(request: Request, body: GoogleLoginRequest, response: Response):
    """Verify a Google ID token and create a session.

    Only admin-whitelisted emails (ADMIN_EMAILS in .env) are allowed.
    Channel partners must use the Channel Partner Portal instead.
    """
    payload = _verify_google_token(body.credential)

    email = payload.get("email", "").lower().strip()

    # Enforce admin whitelist — reject non-admin emails
    settings = get_settings()
    if email not in settings.admin_email_list:
        logger.warning("admin_login_rejected", email=email)
        raise HTTPException(
            status_code=403,
            detail=(
                f"Access denied. '{email}' is not an authorized admin. "
                "If you are a channel partner, please use the Channel Partner Portal."
            ),
        )

    user = UserInfo(
        email=email,
        name=payload.get("name", ""),
        picture=payload.get("picture", ""),
        given_name=payload.get("given_name", ""),
    )

    # Store session keyed by email
    _sessions[user.email] = user

    # Set a simple session cookie
    response.set_cookie(
        key="session_user",
        value=user.email,
        httponly=True,
        samesite="lax",
        max_age=86400,  # 24 hours
    )

    logger.info("user_logged_in", email=user.email, name=user.name)
    return user


@router.get("/me", response_model=UserInfo)
async def get_current_user(request: Request):
    """Return the currently logged-in user from the session cookie."""
    session_email = request.cookies.get("session_user")
    if not session_email or session_email not in _sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _sessions[session_email]


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Clear the session cookie and remove the session."""
    session_email = request.cookies.get("session_user")
    if session_email and session_email in _sessions:
        del _sessions[session_email]
        logger.info("user_logged_out", email=session_email)

    response.delete_cookie("session_user")
    return {"message": "Logged out successfully"}


@router.get("/client-id")
async def get_client_id():
    """Return the Google OAuth Client ID for the frontend."""
    settings = get_settings()
    return {"client_id": settings.google_oauth_client_id}


@router.get("/config")
async def get_auth_config():
    """Return auth config for the frontend (client ID, etc.)."""
    settings = get_settings()
    return {"google_client_id": settings.google_oauth_client_id}


@router.post("/dev-login")
async def dev_login(response: Response):
    """Bypass Google login for local development. Logs in as the first admin."""
    settings = get_settings()
    # Default to ratnesh.s@econz.net if available, or first admin
    admin_email = "ratnesh.s@econz.net"
    if settings.admin_email_list and admin_email not in settings.admin_email_list:
        admin_email = settings.admin_email_list[0]

    user = UserInfo(
        email=admin_email,
        name="Dev Admin",
        picture="",
        given_name="Dev",
    )

    _sessions[user.email] = user

    response.set_cookie(
        key="session_user",
        value=user.email,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )

    logger.info("dev_user_logged_in", email=user.email)
    return user

