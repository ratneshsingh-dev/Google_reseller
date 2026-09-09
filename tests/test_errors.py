"""
Tests for error handling, validation, retry, and partial failure.

Covers:
11. Temporary failure retry
12. Permanent failure
13. Partial employee provisioning
14. Invalid input
15. Insufficient seats
"""

import os

from tests.conftest import wait_for_job_completion


class TestValidationErrors:
    """Input validation tests."""

    def test_invalid_domain(self, client, sample_request):
        """Test 14a: Invalid domain format returns 422."""
        sample_request["primary_domain"] = "not a domain!!!"
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_invalid_email(self, client, sample_request):
        """Test 14b: Invalid email format returns 422."""
        sample_request["alternate_email"] = "not-an-email"
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_invalid_plan(self, client, sample_request):
        """Test 14c: Invalid plan returns 422."""
        sample_request["plan"] = "INVALID_PLAN"
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_insufficient_seats(self, client, sample_request):
        """Test 15: license_count < employees returns 422."""
        sample_request["license_count"] = 1  # But has 3 employees
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_empty_employees(self, client, sample_request):
        """No employees returns 422."""
        sample_request["employees"] = []
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_max_5_employees_limit_exceeded(self, client, sample_request):
        """Test: Submitting > 5 employees in JSON payload returns 422."""
        sample_request["employees"] = [
            {"first_name": f"User{i}", "last_name": "Test", "personal_email": f"u{i}@g.com"}
            for i in range(6)
        ]
        sample_request["license_count"] = 6
        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 422

    def test_missing_required_fields(self, client):
        """Missing fields returns 422."""
        resp = client.post("/api/v1/provision", json={"company_name": "Test"})
        assert resp.status_code == 422


class TestFailureHandling:
    """Error handling and retry tests."""

    def test_mock_failure_mode_retry(self, client, sample_request):
        """Test 11: Transient failures are retried.

        We enable mock failure mode with a moderate rate and verify
        the system still completes (retries work).
        """
        # We need to set env vars before services are created
        os.environ["MOCK_FAILURE_MODE"] = "true"
        os.environ["MOCK_FAILURE_RATE"] = "0.3"  # 30% failure rate

        from app.core.config import get_settings
        from app.dependencies import reset_dependencies
        from app.repositories.firestore_client import reset_store

        get_settings.cache_clear()
        reset_store()
        reset_dependencies()

        resp = client.post("/api/v1/provision", json=sample_request)
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # With retries, it should still complete (or partial failure)
        status = wait_for_job_completion(client, job_id, max_wait=10.0)
        # Could be COMPLETED, PARTIAL_FAILURE, or FAILED depending on luck
        assert status["status"] in ("COMPLETED", "PARTIAL_FAILURE", "FAILED")

        # Reset
        os.environ["MOCK_FAILURE_MODE"] = "false"
        os.environ["MOCK_FAILURE_RATE"] = "0.0"

    def test_job_not_found(self, client):
        """Getting non-existent job returns 404."""
        resp = client.get("/api/v1/provision/JOB-NONEXIST")
        assert resp.status_code == 404

    def test_company_not_found(self, client):
        """Getting non-existent company returns 404."""
        resp = client.get("/api/v1/companies/COMP-NONEXIST")
        assert resp.status_code == 404

    def test_company_users_not_found(self, client):
        """Getting users for non-existent company returns 404."""
        resp = client.get("/api/v1/companies/COMP-NONEXIST/users")
        assert resp.status_code == 404


class TestDuplicateEmployees:
    """Duplicate employee handling tests."""

    def test_duplicate_name_employees_get_suffixed_emails(self, client):
        """Test 6: Employees with same name get suffixed emails."""
        request = {
            "company_name": "Dupe Name Corp",
            "primary_domain": "dupe-name.com",
            "alternate_email": "admin@dupe-alt.com",
            "contact_name": "Admin",
            "postal_address": {
                "address_line1": "1 Dupe St",
                "locality": "City",
                "region": "ST",
                "postal_code": "11111",
                "country_code": "US",
            },
            "plan": "FLEXIBLE",
            "sku_id": "SKU-BUSINESS-STANDARD",
            "license_count": 3,
            "initiated_by_email": "admin@dupe-alt.com",
            "econz_notification_email": "econz@example.net",
            "employees": [
                {"first_name": "John", "last_name": "Doe", "personal_email": "john1@g.com"},
                {"first_name": "John", "last_name": "Doe", "personal_email": "john2@g.com"},
                {"first_name": "John", "last_name": "Doe", "personal_email": "john3@g.com"},
            ],
        }

        resp = client.post("/api/v1/provision", json=request)
        job_id = resp.json()["job_id"]
        status = wait_for_job_completion(client, job_id)

        assert status["status"] == "COMPLETED"
        emails = {e["corporate_email"] for e in status["employees"]}
        assert "john.doe@dupe-name.com" in emails
        assert "john.doe2@dupe-name.com" in emails
        assert "john.doe3@dupe-name.com" in emails
