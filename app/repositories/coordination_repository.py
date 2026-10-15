"""
Coordination records that make concurrent requests safe:

- LockRepository:        one operation per domain at a time (lease with expiry).
- IdempotencyRepository: a retried request with the same Idempotency-Key returns
                         the original result instead of doing the work twice.

Both rely on the store's "create only if absent" / compare-and-swap writes, so
exactly one of any number of simultaneous callers wins.
"""

from __future__ import annotations

import hashlib
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from app.repositories.firestore_client import BaseStore


class LockRepository:
    COLLECTION = "domain_locks"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def acquire(self, key: str, operation: str, ttl_seconds: int) -> Optional[str]:
        """Take the lock for `key`. Returns a token, or None if someone else holds a live lock.

        A lock whose lease has expired (its holder crashed) is taken over.
        """
        doc_id = _safe_id(key)
        token = uuid.uuid4().hex
        record = {
            "key": key,
            "token": token,
            "operation": operation,
            "expires_at": time.time() + ttl_seconds,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._store.set_if_version(self.COLLECTION, doc_id, record, None):
            return token
        current, version = self._store.get_versioned(self.COLLECTION, doc_id)
        if current is None:
            return token if self._store.set_if_version(self.COLLECTION, doc_id, record, None) else None
        if float(current.get("expires_at", 0)) < time.time():
            return token if self._store.set_if_version(self.COLLECTION, doc_id, record, version) else None
        return None

    def holder(self, key: str) -> Optional[str]:
        current, _ = self._store.get_versioned(self.COLLECTION, _safe_id(key))
        if current and float(current.get("expires_at", 0)) >= time.time():
            return current.get("operation")
        return None

    def renew(self, key: str, token: str, ttl_seconds: int) -> bool:
        """Extend a lock we still hold (called from the job heartbeat)."""
        doc_id = _safe_id(key)
        current, version = self._store.get_versioned(self.COLLECTION, doc_id)
        if not current or current.get("token") != token or version is None:
            return False
        current["expires_at"] = time.time() + ttl_seconds
        return self._store.set_if_version(self.COLLECTION, doc_id, current, version)

    def release(self, key: str, token: str) -> None:
        """Release only if we still hold it (never remove a lock someone else took over)."""
        doc_id = _safe_id(key)
        current, version = self._store.get_versioned(self.COLLECTION, doc_id)
        if current and current.get("token") == token and version is not None:
            self._store.delete_if_version(self.COLLECTION, doc_id, version)


class IdempotencyRepository:
    COLLECTION = "idempotency_keys"

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def claim(self, scope: str, key: str, fingerprint: str, result_id: str) -> Tuple[bool, dict]:
        """Record `key` -> `result_id`. Returns (True, record) if this call claimed the key,
        or (False, existing_record) if the key was already used."""
        doc_id = _safe_id(f"{scope}:{key}")
        record = {
            "scope": scope,
            "fingerprint": fingerprint,
            "result_id": result_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._store.set_if_version(self.COLLECTION, doc_id, record, None):
            return True, record
        existing, _ = self._store.get_versioned(self.COLLECTION, doc_id)
        return False, existing or record

    def forget(self, scope: str, key: str, result_id: str) -> None:
        """Undo a claim when the request was rejected, so the client may retry with the same key."""
        doc_id = _safe_id(f"{scope}:{key}")
        current, version = self._store.get_versioned(self.COLLECTION, doc_id)
        if current and current.get("result_id") == result_id and version is not None:
            self._store.delete_if_version(self.COLLECTION, doc_id, version)


class PlanMixRepository:
    """Per-partner running counter that decides which domains get the commitment plan.

    Slots are handed out atomically (compare-and-swap), so any number of parallel
    requests, single or bulk, receive distinct consecutive slots and any run of N
    consecutive slots contains exactly N/every commitment slots.
    """

    COLLECTION = "plan_mix_counters"
    _ATTEMPTS = 30

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    def reserve(self, reseller_id: str, count: int) -> int:
        """Claim `count` consecutive slots. Returns the counter value BEFORE them, so the
        caller owns slots first+1 .. first+count."""
        doc_id = _safe_id(reseller_id)
        for attempt in range(self._ATTEMPTS):
            data, version = self._store.get_versioned(self.COLLECTION, doc_id)
            first = int(data["count"]) if data else 0
            record = {
                "reseller_id": reseller_id,
                "count": first + count,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if self._store.set_if_version(self.COLLECTION, doc_id, record, version):
                return first
            time.sleep(random.uniform(0.005, 0.05) * (attempt + 1))
        raise RuntimeError(f"Could not reserve plan slots for {reseller_id}: too much contention")


def fingerprint(payload: object) -> str:
    import json
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _safe_id(value: str) -> str:
    # Firestore document IDs cannot contain "/", so hash anything that is not a plain domain.
    lowered = value.strip().lower()
    if all(c.isalnum() or c in ".-_" for c in lowered) and 0 < len(lowered) <= 200:
        return lowered
    return hashlib.sha256(lowered.encode()).hexdigest()
