"""
Outbound webhooks — notify a partner's server when provisioning finishes.

Each delivery is a JSON POST signed with the partner's webhook secret:
    X-Webhook-Signature: sha256=HMAC_SHA256(secret, f"{timestamp}.{body}")
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urlparse

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

RETRY_DELAYS_SECONDS = (0, 5, 30)
REQUEST_TIMEOUT_SECONDS = 10


class WebhookUrlError(ValueError):
    """The URL is not an acceptable webhook destination."""


def generate_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def validate_webhook_url(url: str) -> str:
    """Accept only public HTTPS URLs, so partners cannot point us at internal services."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise WebhookUrlError("Webhook URL must start with https://")
    if not parsed.hostname:
        raise WebhookUrlError("Webhook URL has no host name.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise WebhookUrlError(f"Host '{parsed.hostname}' could not be resolved.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise WebhookUrlError("Webhook URL must point to a public internet address.")
    return url.strip()


def sign_payload(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


async def deliver(url: str, secret: str, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST one event, retrying on failure. Returns the outcome; never raises."""
    delivery_id = f"WH-{uuid.uuid4().hex[:12].upper()}"
    body = json.dumps(
        {
            "id": delivery_id,
            "event": event,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        },
        separators=(",", ":"),
    ).encode()

    last_error = ""
    for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(validate_webhook_url, url)
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Workspace-Provisioning-Webhook/1.0",
                "X-Webhook-Id": delivery_id,
                "X-Webhook-Event": event,
                "X-Webhook-Timestamp": timestamp,
                "X-Webhook-Signature": sign_payload(secret, timestamp, body),
            }
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
                resp = await client.post(url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                logger.info("webhook_delivered", delivery_id=delivery_id, webhook_event=event, attempt=attempt)
                return {"delivered": True, "delivery_id": delivery_id, "attempts": attempt, "status_code": resp.status_code}
            last_error = f"HTTP {resp.status_code}"
        except WebhookUrlError as exc:
            last_error = str(exc)
            break
        except Exception as exc:
            last_error = str(exc)[:200]
        logger.warning("webhook_attempt_failed", delivery_id=delivery_id, webhook_event=event, attempt=attempt, error=last_error)

    return {"delivered": False, "delivery_id": delivery_id, "attempts": attempt, "error": last_error}


async def notify_reseller(reseller_id: str | None, event: str, data: Dict[str, Any]) -> None:
    """Send an event to the reseller's registered webhook, if any, and audit the result."""
    if not reseller_id:
        return
    try:
        from app.models.reseller_models import AuditLogDocument
        from app.repositories.audit_repository import AuditRepository
        from app.repositories.firestore_client import get_store
        from app.repositories.reseller_repository import ResellerRepository

        reseller = ResellerRepository(get_store()).get_by_id(reseller_id)
        if not reseller or not reseller.webhook_url or not reseller.webhook_secret:
            return

        result = await deliver(reseller.webhook_url, reseller.webhook_secret, event, data)
        AuditRepository(get_store()).create(
            AuditLogDocument(
                log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                reseller_id=reseller_id,
                action="WEBHOOK_DELIVERY",
                resource_type="job",
                resource_id=str(data.get("job_id", "")),
                status="SUCCESS" if result["delivered"] else "FAILED",
                details={"event": event, **result},
            )
        )
    except Exception as exc:
        logger.error("webhook_notify_failed", reseller_id=reseller_id, webhook_event=event, error=str(exc))
