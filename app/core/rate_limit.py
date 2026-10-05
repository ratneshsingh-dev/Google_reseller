"""
Rate limiting configuration for the API using slowapi.

Limits are counted per partner when the request carries a valid partner token,
otherwise per client IP. On Cloud Run every request arrives from Google's front
end, so counting by the socket address would make all partners share one limit;
we use the first X-Forwarded-For address instead.
"""

from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: F401
from slowapi.util import get_remote_address
from starlette.requests import Request


def _rate_limit_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            from app.core.jwt_service import decode_token

            reseller_id = decode_token(auth[7:].strip()).get("sub")
            if reseller_id:
                return f"reseller:{reseller_id}"
        except Exception:
            pass
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key, default_limits=["120/minute"])
