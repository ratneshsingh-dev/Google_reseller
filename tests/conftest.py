"""
Shared pytest fixtures for the provisioning system tests.

Provides:
- Fresh in-memory storage for each test
- Pre-configured mock services
- FastAPI test client
- Helper functions to build test requests
"""

from __future__ import annotations

import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# Force test configuration BEFORE any app imports
os.environ["USE_FIRESTORE"] = "false"
os.environ["SERVICE_ADAPTER"] = "mock"
os.environ["MOCK_FAILURE_MODE"] = "false"
os.environ["MOCK_FAILURE_RATE"] = "0.0"
os.environ["LOG_LEVEL"] = "WARNING"


@pytest.fixture(autouse=True)
def reset_state():
    """Reset all singletons and in-memory state before each test."""
    from app.dependencies import reset_dependencies
    from app.repositories.firestore_client import reset_store
    from app.core.config import get_settings

    # Clear caches
    get_settings.cache_clear()
    reset_store()
    reset_dependencies()
    yield
    get_settings.cache_clear()
    reset_store()
    reset_dependencies()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """FastAPI test client with fresh state."""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_request() -> dict:
    """A valid provisioning request payload."""
    return {
        "company_name": "Test Corp",
        "primary_domain": "test-corp.com",
        "alternate_email": "admin@test-alt.com",
        "contact_name": "John Doe",
        "postal_address": {
            "address_line1": "123 Test Street",
            "locality": "Bengaluru",
            "region": "KA",
            "postal_code": "560001",
            "country_code": "IN",
        },
        "plan": "FLEXIBLE",
        "sku_id": "SKU-BUSINESS-STANDARD",
        "license_count": 3,
        "initiated_by_email": "developer@test-alt.com",
        "econz_notification_email": "econz@example.net",
        "employees": [
            {
                "first_name": "Rahul",
                "last_name": "Sharma",
                "personal_email": "rahul@gmail.com",
            },
            {
                "first_name": "Priya",
                "last_name": "Patel",
                "personal_email": "priya@gmail.com",
            },
            {
                "first_name": "Amit",
                "last_name": "Kumar",
                "personal_email": "amit@gmail.com",
            },
        ],
    }


@pytest.fixture
def sample_request_small() -> dict:
    """A minimal provisioning request with one employee."""
    return {
        "company_name": "Small Corp",
        "primary_domain": "small-corp.com",
        "alternate_email": "admin@small-alt.com",
        "contact_name": "Jane Doe",
        "postal_address": {
            "address_line1": "1 Small Lane",
            "locality": "Mumbai",
            "region": "MH",
            "postal_code": "400001",
            "country_code": "IN",
        },
        "plan": "FLEXIBLE",
        "sku_id": "SKU-BUSINESS-STARTER",
        "license_count": 1,
        "initiated_by_email": "jane@small-alt.com",
        "econz_notification_email": "econz@example.net",
        "employees": [
            {
                "first_name": "Jane",
                "last_name": "Doe",
                "personal_email": "jane.doe@gmail.com",
            },
        ],
    }


def wait_for_job_completion(client: TestClient, job_id: str, max_wait: float = 5.0) -> dict:
    """Poll job status until it reaches a terminal state."""
    import time
    terminal_states = {"COMPLETED", "PARTIAL_FAILURE", "FAILED"}
    start = time.time()
    while time.time() - start < max_wait:
        resp = client.get(f"/api/v1/provision/{job_id}")
        data = resp.json()
        if data["status"] in terminal_states:
            return data
        time.sleep(0.1)
    return data
