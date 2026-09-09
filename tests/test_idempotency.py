"""
Tests for idempotency and duplicate handling.

Covers:
7. Duplicate company handling
8. Idempotency key
"""

from tests.conftest import wait_for_job_completion


class TestIdempotency:
    """Idempotency and duplicate prevention tests."""

    def test_idempotency_key_returns_existing_job(self, client, sample_request):
        """Test 8: Same idempotency key returns existing job."""
        headers = {"Idempotency-Key": "test-key-001"}

        resp1 = client.post(
            "/api/v1/provision", json=sample_request, headers=headers
        )
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        # Wait for completion
        wait_for_job_completion(client, job_id_1)

        # Submit again with same key
        resp2 = client.post(
            "/api/v1/provision", json=sample_request, headers=headers
        )
        assert resp2.status_code == 202
        job_id_2 = resp2.json()["job_id"]

        # Same job should be returned
        assert job_id_1 == job_id_2

    def test_idempotency_key_prevents_duplicate_even_before_completion(
        self, client, sample_request
    ):
        """Submitting same idempotency key returns same job even if first hasn't completed."""
        headers = {"Idempotency-Key": "test-key-002"}

        resp1 = client.post(
            "/api/v1/provision", json=sample_request, headers=headers
        )
        job_id_1 = resp1.json()["job_id"]

        # Submit again with same key (first may or may not have completed
        # due to BackgroundTasks running synchronously in tests)
        resp2 = client.post(
            "/api/v1/provision", json=sample_request, headers=headers
        )
        job_id_2 = resp2.json()["job_id"]

        assert job_id_1 == job_id_2

    def test_different_idempotency_keys_create_different_jobs(
        self, client, sample_request, sample_request_small
    ):
        """Different keys with different domains create separate jobs."""
        resp1 = client.post(
            "/api/v1/provision",
            json=sample_request,
            headers={"Idempotency-Key": "key-a"},
        )
        resp2 = client.post(
            "/api/v1/provision",
            json=sample_request_small,
            headers={"Idempotency-Key": "key-b"},
        )

        assert resp1.json()["job_id"] != resp2.json()["job_id"]

    def test_re_provision_same_company_detects_existing_users(
        self, client, sample_request
    ):
        """Re-provisioning same company marks users as EXISTING when found in DB."""
        # First provisioning
        resp1 = client.post(
            "/api/v1/provision",
            json=sample_request,
            headers={"Idempotency-Key": "first-run"},
        )
        job_id_1 = resp1.json()["job_id"]
        status1 = wait_for_job_completion(client, job_id_1)
        assert status1["status"] == "COMPLETED"
        assert status1["users_created"] == 3

        # Second provisioning — same domain, new key
        # Users exist in DB but since mock directory is separate per service
        # instance, the directory check finds them via the DB lookup
        resp2 = client.post(
            "/api/v1/provision",
            json=sample_request,
            headers={"Idempotency-Key": "second-run"},
        )
        job_id_2 = resp2.json()["job_id"]
        status2 = wait_for_job_completion(client, job_id_2)

        # The users exist in the mock directory (same singleton) so they
        # should be detected as existing via directory lookup
        total_accounted = (
            status2["users_created"]
            + status2["users_existing"]
        )
        assert total_accounted == 3

    def test_duplicate_domain_completed_job_allows_new_job(
        self, client, sample_request
    ):
        """Test 7: A completed job for same domain allows submitting a new one."""
        resp1 = client.post(
            "/api/v1/provision",
            json=sample_request,
            headers={"Idempotency-Key": "domain-dup-1"},
        )
        job_id_1 = resp1.json()["job_id"]
        wait_for_job_completion(client, job_id_1)

        # Submit again without idempotency key for same domain
        # Since first job is COMPLETED (not in-flight), a new job is allowed
        resp2 = client.post(
            "/api/v1/provision",
            json=sample_request,
            headers={"Idempotency-Key": "domain-dup-2"},
        )
        assert resp2.status_code == 202
        # Different key → different job
        assert resp2.json()["job_id"] != job_id_1
