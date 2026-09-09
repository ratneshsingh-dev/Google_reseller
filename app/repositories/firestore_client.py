"""
Storage backend abstraction.

Provides:
  - InMemoryStore for fast local testing
  - FirestoreStore for live Google Cloud Firestore (using REST v1 with Google Auth)
"""

from __future__ import annotations

import datetime
import threading
from typing import Any, Dict, List, Optional

import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account
import requests

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Firestore REST Serializers & Deserializers
# ---------------------------------------------------------------------------


def _python_to_firestore_value(val: Any) -> dict:
    """Convert Python value to Firestore REST Value JSON."""
    if val is None:
        return {"nullValue": None}
    if isinstance(val, bool):
        return {"booleanValue": val}
    if isinstance(val, int):
        return {"integerValue": str(val)}
    if isinstance(val, float):
        return {"doubleValue": val}
    if isinstance(val, str):
        return {"stringValue": val}
    if isinstance(val, (datetime.datetime, datetime.date)):
        if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
            val = datetime.datetime.combine(val, datetime.time.min)
        if val.tzinfo is None:
            val = val.replace(tzinfo=datetime.timezone.utc)
        return {"timestampValue": val.isoformat()}
    if isinstance(val, list):
        return {"arrayValue": {"values": [_python_to_firestore_value(x) for x in val]}}
    if isinstance(val, dict):
        return {
            "mapValue": {
                "fields": {k: _python_to_firestore_value(v) for k, v in val.items()}
            }
        }
    return {"stringValue": str(val)}


def _firestore_value_to_python(val_obj: dict) -> Any:
    """Convert Firestore REST Value JSON to Python value."""
    if "nullValue" in val_obj:
        return None
    if "booleanValue" in val_obj:
        return val_obj["booleanValue"]
    if "integerValue" in val_obj:
        return int(val_obj["integerValue"])
    if "doubleValue" in val_obj:
        return float(val_obj["doubleValue"])
    if "stringValue" in val_obj:
        return val_obj["stringValue"]
    if "timestampValue" in val_obj:
        return val_obj["timestampValue"]
    if "arrayValue" in val_obj:
        return [
            _firestore_value_to_python(x)
            for x in val_obj["arrayValue"].get("values", [])
        ]
    if "mapValue" in val_obj:
        return {
            k: _firestore_value_to_python(v)
            for k, v in val_obj["mapValue"].get("fields", {}).items()
        }
    return None


def _firestore_doc_to_dict(doc_json: dict) -> dict:
    """Convert a Firestore Document JSON object to a Python dict."""
    fields = doc_json.get("fields", {})
    return {k: _firestore_value_to_python(v) for k, v in fields.items()}


def _dict_to_firestore_doc(data: dict) -> dict:
    """Convert a Python dict to a Firestore Document JSON body."""
    return {"fields": {k: _python_to_firestore_value(v) for k, v in data.items()}}


# ---------------------------------------------------------------------------
# Storage Interface & Implementations
# ---------------------------------------------------------------------------


class BaseStore:
    """Abstract storage interface."""

    def get(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        raise NotImplementedError

    def update(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, collection: str, doc_id: str) -> None:
        raise NotImplementedError

    def query(
        self,
        collection: str,
        field: str,
        op: str,
        value: Any,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list_all(self, collection: str) -> List[Dict[str, Any]]:
        raise NotImplementedError


class InMemoryStore(BaseStore):
    """Thread-safe in-memory storage for local development/testing."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data.get(collection, {}).get(doc_id)

    def set(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        with self._lock:
            if collection not in self._data:
                self._data[collection] = {}
            self._data[collection][doc_id] = data.copy()

    def update(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        with self._lock:
            if collection not in self._data:
                self._data[collection] = {}
            existing = self._data[collection].get(doc_id, {})
            existing.update(data)
            self._data[collection][doc_id] = existing

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            if collection in self._data:
                self._data[collection].pop(doc_id, None)

    def query(
        self,
        collection: str,
        field: str,
        op: str,
        value: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            results = []
            for doc in self._data.get(collection, {}).values():
                doc_value = doc.get(field)
                if op == "==" and doc_value == value:
                    results.append(doc.copy())
                elif op == "!=" and doc_value != value:
                    results.append(doc.copy())
                elif op == ">" and doc_value is not None and doc_value > value:
                    results.append(doc.copy())
                elif op == "<" and doc_value is not None and doc_value < value:
                    results.append(doc.copy())
            return results

    def list_all(self, collection: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [doc.copy() for doc in self._data.get(collection, {}).values()]

    def clear(self) -> None:
        """Clear all data — useful for testing."""
        with self._lock:
            self._data.clear()


class FirestoreStore(BaseStore):
    """Live Google Cloud Firestore storage backend (REST v1)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._project = settings.google_cloud_project
        self._database = settings.firestore_database
        self._base_url = (
            f"https://firestore.googleapis.com/v1/projects/{self._project}"
            f"/databases/{self._database}/documents"
        )
        # Explicitly load credentials from GOOGLE_APPLICATION_CREDENTIALS
        # (bypasses system-level env var that may point to a different project)
        import os
        creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        try:
            self._credentials = service_account.Credentials.from_service_account_file(
                creds_file,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception:
            # Fallback to ADC if service account file not found/invalid
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        self._auth_req = google.auth.transport.requests.Request()
        self._lock = threading.Lock()
        logger.info(
            "firestore_initialized",
            project=self._project,
            database=self._database,
        )

    def _get_headers(self) -> dict:
        with self._lock:
            if not self._credentials.valid:
                self._credentials.refresh(self._auth_req)
            return {
                "Authorization": f"Bearer {self._credentials.token}",
                "Content-Type": "application/json",
            }

    def get(self, collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}/{collection}/{doc_id}"
        resp = requests.get(url, headers=self._get_headers())
        if resp.status_code == 200:
            return _firestore_doc_to_dict(resp.json())
        if resp.status_code == 404:
            return None
        if resp.status_code == 403:
            logger.error("firestore_permission_denied", collection=collection, doc_id=doc_id,
                        hint="Grant 'Cloud Datastore User' IAM role to the service account in GCP Console")
            return None
        logger.error("firestore_get_error", status=resp.status_code, text=resp.text)
        return None

    def set(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        url = f"{self._base_url}/{collection}/{doc_id}"
        body = _dict_to_firestore_doc(data)
        resp = requests.patch(url, headers=self._get_headers(), json=body)
        if resp.status_code not in (200, 201):
            logger.error("firestore_set_error", status=resp.status_code, text=resp.text)
            resp.raise_for_status()

    def update(self, collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        # Fetch existing, merge, and patch
        existing = self.get(collection, doc_id) or {}
        existing.update(data)
        try:
            self.set(collection, doc_id, existing)
        except Exception as exc:
            logger.error("firestore_update_error", collection=collection, doc_id=doc_id, error=str(exc))

    def delete(self, collection: str, doc_id: str) -> None:
        url = f"{self._base_url}/{collection}/{doc_id}"
        resp = requests.delete(url, headers=self._get_headers())
        if resp.status_code not in (200, 204, 404):
            logger.error("firestore_delete_error", status=resp.status_code, text=resp.text)

    def query(
        self,
        collection: str,
        field: str,
        op: str,
        value: Any,
    ) -> List[Dict[str, Any]]:
        # Run structured query via Firestore REST API
        url = f"{self._base_url}:runQuery"
        op_map = {
            "==": "EQUAL",
            "!=": "NOT_EQUAL",
            ">": "GREATER_THAN",
            "<": "LESS_THAN",
            ">=": "GREATER_THAN_OR_EQUAL",
            "<=": "LESS_THAN_OR_EQUAL",
        }
        structured_query = {
            "structuredQuery": {
                "from": [{"collectionId": collection}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": field},
                        "op": op_map.get(op, "EQUAL"),
                        "value": _python_to_firestore_value(value),
                    }
                },
            }
        }
        resp = requests.post(url, headers=self._get_headers(), json=structured_query)
        if resp.status_code != 200:
            logger.error("firestore_query_error", status=resp.status_code, text=resp.text)
            return []

        results = []
        for item in resp.json():
            doc = item.get("document")
            if doc:
                results.append(_firestore_doc_to_dict(doc))
        return results

    def list_all(self, collection: str) -> List[Dict[str, Any]]:
        url = f"{self._base_url}/{collection}"
        resp = requests.get(url, headers=self._get_headers())
        if resp.status_code == 200:
            docs = resp.json().get("documents", [])
            return [_firestore_doc_to_dict(d) for d in docs]
        if resp.status_code == 403:
            logger.error("firestore_list_permission_denied", collection=collection,
                        hint="Grant 'Cloud Datastore User' IAM role to the service account in GCP Console")
        return []


# ---------------------------------------------------------------------------
# Singleton Factory
# ---------------------------------------------------------------------------

_store_instance: Optional[BaseStore] = None
_store_lock = threading.Lock()


def get_store() -> BaseStore:
    """Get the singleton storage backend."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                settings = get_settings()
                if settings.use_firestore:
                    _store_instance = FirestoreStore()
                    logger.info("storage_backend", backend="firestore")
                else:
                    _store_instance = InMemoryStore()
                    logger.info("storage_backend", backend="in_memory")
    return _store_instance


def reset_store() -> None:
    """Reset store singleton — only for testing."""
    global _store_instance
    with _store_lock:
        _store_instance = None
