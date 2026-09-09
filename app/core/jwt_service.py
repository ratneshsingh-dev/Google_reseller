"""
JWT Service — create and decode tokens for the Reseller API.

Uses PyJWT with HMAC-SHA256 signing.
Secret key comes from JWT_SECRET_KEY in .env.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_CLIENT_SECRET_ALPHABET = string.ascii_letters + string.digits
_CLIENT_SECRET_LENGTH = 32


# ---------------------------------------------------------------------------
# Client secret generation & hashing
# ---------------------------------------------------------------------------


def generate_client_secret() -> str:
    """Generate a random client_secret with 'sec_' prefix.

    Example: sec_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
    This is shown to the admin ONCE at reseller creation time.
    """
    random_part = "".join(
        secrets.choice(_CLIENT_SECRET_ALPHABET)
        for _ in range(_CLIENT_SECRET_LENGTH)
    )
    return f"sec_{random_part}"


def hash_secret(secret: str) -> str:
    """Return SHA-256 hex digest of a client secret.

    Only hashes are stored in Firestore — never the plaintext.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(plain_secret: str, hashed_secret: str) -> bool:
    """Compare a plaintext secret against its stored hash."""
    return hash_secret(plain_secret) == hashed_secret


# ---------------------------------------------------------------------------
# JWT creation & decoding
# ---------------------------------------------------------------------------


def create_access_token(
    reseller_id: str,
    role: str,
    token_version: int,
) -> str:
    """Create a signed JWT access token valid for configured hours.

    Payload fields:
        sub           — reseller_id (= client_id)
        role          — reseller role string
        type          — "access"
        token_version — used for instant revocation check
        iat           — issued-at timestamp
        exp           — expiry timestamp
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=settings.jwt_access_token_expire_hours)

    payload: Dict[str, Any] = {
        "sub": reseller_id,
        "role": role,
        "type": "access",
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")
    logger.info(
        "jwt_token_created",
        reseller_id=reseller_id,
        role=role,
        expires_at=expire.isoformat(),
    )
    return token


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and verify a JWT token.

    Raises:
        jwt.ExpiredSignatureError — token has expired
        jwt.InvalidTokenError     — token is malformed or signature invalid
    """
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])


def get_token_expires_in_seconds() -> int:
    """Return the access token lifetime in seconds."""
    settings = get_settings()
    return settings.jwt_access_token_expire_hours * 3600
