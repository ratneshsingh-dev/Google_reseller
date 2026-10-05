"""
Which Google API failures are temporary, and how long to wait before retrying them.

- Rate limits: Google reports per-minute quota as 403 (reason rateLimitExceeded /
  userRateLimitExceeded) or 429. Waits grow 5s -> 60s so a retry lands in a new quota minute.
- Google server errors: 500, 502, 503, 504.
- Network drops: broken pipe, connection reset/aborted, timeouts, TLS errors.

Everything else (invalid domain, permission denied, quota for the day, ...) is permanent.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import random
import socket
import ssl
from typing import Awaitable, Callable, Optional, Set, TypeVar

from app.core.exceptions import RetryableError
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

MAX_ATTEMPTS = 6
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
SERVER_ERRORS = {500, 502, 503, 504}


def _http_error_parts(exc: BaseException) -> tuple[Optional[int], Set[str]]:
    from googleapiclient.errors import HttpError

    if not isinstance(exc, HttpError):
        return None, set()
    reasons: Set[str] = set()
    for detail in getattr(exc, "error_details", None) or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reasons.add(detail["reason"])
    if not reasons:
        try:
            body = json.loads(exc.content.decode() if isinstance(exc.content, bytes) else exc.content)
            for err in body.get("error", {}).get("errors", []):
                if err.get("reason"):
                    reasons.add(err["reason"])
        except Exception:
            pass
    return exc.status_code, reasons


def is_rate_limited(exc: BaseException) -> bool:
    status, reasons = _http_error_parts(exc)
    return status == 429 or (status == 403 and bool(reasons & RATE_LIMIT_REASONS))


def is_network_error(exc: BaseException) -> bool:
    try:
        import httplib2
        httplib2_errors: tuple = (httplib2.HttpLib2Error,)
    except Exception:
        httplib2_errors = ()
    return isinstance(
        exc,
        (ConnectionError, TimeoutError, socket.timeout, ssl.SSLError, http.client.HTTPException) + httplib2_errors,
    )


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, RetryableError):
        return True
    if is_rate_limited(exc) or is_network_error(exc):
        return True
    status, _ = _http_error_parts(exc)
    return status in SERVER_ERRORS


def retry_delay(exc: BaseException, attempt: int) -> float:
    """Seconds to wait before retry number `attempt` (0-based), with jitter."""
    base = min(60.0, 5.0 * (2 ** attempt)) if is_rate_limited(exc) else min(30.0, 1.0 * (2 ** attempt))
    return base * random.uniform(0.8, 1.2)


async def call_with_retry(operation: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
    """Await operation(*args, **kwargs), retrying temporary failures with backoff."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await operation(*args, **kwargs)
        except Exception as exc:
            if not is_transient(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = retry_delay(exc, attempt)
            logger.warning(
                "google_call_retry",
                operation=getattr(operation, "__name__", str(operation)),
                attempt=attempt + 1,
                delay_seconds=round(delay, 1),
                rate_limited=is_rate_limited(exc),
                error=str(exc)[:200],
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")
