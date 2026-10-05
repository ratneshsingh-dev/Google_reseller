"""Durable jobs: crash recovery, exactly-once quota settlement, leases, the reconciler,
the Cloud Tasks backend and its worker endpoint. Offline (mock Google, in-memory store)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.core import job_dispatcher, job_executor
from app.core.config import get_settings
from app.core.exceptions import PermanentError
from app.core.jwt_service import create_access_token
from app.dependencies import (
    get_job_repo,
    get_lock_repo,
    get_provisioning_intake,
    get_reseller_repo,
    get_reseller_service,
)
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument
from app.services.provisioning_service import ProvisioningService


def _reseller(cap: int = 50) -> ResellerDocument:
    doc = ResellerDocument(reseller_id="RSL-D1", company_name="D1", contact_email="d1@partner.test",
                           contact_name="D1", max_licence_cap=cap, client_secret_hash="unused")
    get_reseller_repo().create(doc)
    return doc


def _request(domain: str, seats: int = 3) -> ProvisioningRequest:
    return ProvisioningRequest(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", plan="FLEXIBLE", sku_id="1010020027", license_count=seats,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )


def _used() -> int:
    return get_reseller_repo().get_by_id("RSL-D1").licences_used


def _age_job(job_id: str, seconds: int) -> None:
    """Pretend the job's worker went silent `seconds` ago."""
    past = time.time() - seconds
    get_job_repo().update_fields(job_id, heartbeat_at=past)


def _subscriptions(domain: str):
    mock = get_reseller_service()
    return [s for subs in mock._subscriptions.values() for s in subs.values() if s.customer_domain == domain]


@pytest.fixture
def queued(client, monkeypatch):
    """Accept jobs without running them, like a queue that has not delivered yet."""
    monkeypatch.setattr(job_dispatcher, "dispatch", lambda job_id, run_locally: None)
    return get_provisioning_intake()


class TestExactlyOnceSettlement:
    def test_settling_twice_never_refunds_twice(self, client):
        intake = get_provisioning_intake()
        reseller = _reseller()
        job_id = intake.accept(reseller, _request("once.test", 3))["job_id"]
        assert job_executor.wait_until_idle(30)
        assert _used() == 3
        intake.settle(job_id, 0)
        intake.settle(job_id, 0)
        assert _used() == 3


class TestLease:
    def test_only_one_worker_holds_a_job(self, queued):
        job_id = queued.accept(_reseller(), _request("lease.test"))["job_id"]
        jobs = get_job_repo()
        assert jobs.claim(job_id, "worker-a", lease_seconds=60) == "claimed"
        assert jobs.claim(job_id, "worker-b", lease_seconds=60) == "busy"

    def test_a_dead_workers_lease_is_taken_over(self, queued):
        job_id = queued.accept(_reseller(), _request("takeover.test"))["job_id"]
        jobs = get_job_repo()
        jobs.claim(job_id, "worker-a", lease_seconds=60)
        _age_job(job_id, 120)
        assert jobs.claim(job_id, "worker-b", lease_seconds=60) == "claimed"
        assert jobs.get_job(job_id).attempts == 2


class TestCrashAndResume:
    def test_job_resumes_after_the_server_dies_mid_way(self, queued, monkeypatch):
        """Server dies after Google added the licences and created the admin, before the
        job finished. Redelivery must finish it, charge exactly once and create nothing twice."""
        job_id = queued.accept(_reseller(), _request("crash.test", 4))["job_id"]
        original = ProvisioningService._send_notifications
        calls = {"n": 0}

        async def die_first_time(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt("simulated server death")
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(ProvisioningService, "_send_notifications", die_first_time)

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(queued.run_job(job_id))
        stuck = get_job_repo().get_job(job_id)
        assert stuck.settled is False and stuck.status not in ("COMPLETED", "FAILED")
        assert _used() == 4

        _age_job(job_id, 120)
        assert asyncio.run(queued.run_job(job_id)) == "completed"

        job = get_job_repo().get_job(job_id)
        assert job.status == "COMPLETED" and job.settled and job.attempts == 2
        assert job.licences_added == 4
        assert _used() == 4
        subs = _subscriptions("crash.test")
        assert len(subs) == 1 and subs[0].seats.number_of_seats == 4

    def test_redelivering_a_finished_job_does_nothing(self, queued):
        job_id = queued.accept(_reseller(), _request("done.test", 2))["job_id"]
        assert asyncio.run(queued.run_job(job_id)) == "completed"
        assert asyncio.run(queued.run_job(job_id)) == "done"
        assert _used() == 2


class TestReconciler:
    def test_abandoned_job_is_failed_refunded_and_unlocked(self, queued):
        reseller = _reseller(cap=10)
        job_id = queued.accept(reseller, _request("lost.test", 5))["job_id"]
        assert _used() == 5
        _age_job(job_id, 7200)

        assert queued.reconcile_stale_jobs(stale_after_seconds=60) == [job_id]
        job = get_job_repo().get_job(job_id)
        assert job.status == "FAILED" and "Interrupted" in job.error_message and job.settled
        assert _used() == 0
        assert get_lock_repo().holder("lost.test") is None

        assert queued.reconcile_stale_jobs(stale_after_seconds=60) == []
        assert _used() == 0

    def test_live_jobs_are_left_alone(self, queued):
        job_id = queued.accept(_reseller(), _request("alive.test", 2))["job_id"]
        get_job_repo().claim(job_id, "worker-a", lease_seconds=60)
        assert queued.reconcile_stale_jobs(stale_after_seconds=600) == []
        assert get_job_repo().get_job(job_id).settled is False


class TestFailedJobsList:
    def test_partner_can_list_failed_jobs_with_reasons(self, client, monkeypatch):
        _reseller()
        headers = {"Authorization": "Bearer " + create_access_token("RSL-D1", "RESELLER_FULL", 1)}

        async def refuse(*args, **kwargs):
            raise PermanentError("Google refused the subscription")

        intake = get_provisioning_intake()
        intake.accept(get_reseller_repo().get_by_id("RSL-D1"), _request("ok.test", 1))
        assert job_executor.wait_until_idle(30)
        monkeypatch.setattr(get_reseller_service(), "create_subscription", refuse)
        intake.accept(get_reseller_repo().get_by_id("RSL-D1"), _request("bad.test", 1))
        assert job_executor.wait_until_idle(30)

        body = client.get("/api/v1/reseller/provision?status=FAILED", headers=headers).json()
        assert body["total"] == 1
        assert body["jobs"][0]["primary_domain"] == "bad.test"
        assert "refused" in body["jobs"][0]["error_message"]
        assert client.get("/api/v1/reseller/provision", headers=headers).json()["total"] == 2


class TestCloudTasksBackend:
    @pytest.fixture
    def cloudtasks(self, client, monkeypatch):
        monkeypatch.setenv("JOB_BACKEND", "cloudtasks")
        monkeypatch.setenv("CLOUD_TASKS_QUEUE", "provisioning-test")
        monkeypatch.setenv("TASKS_INVOKER_SA", "invoker@example.iam.gserviceaccount.com")
        monkeypatch.setenv("WORKER_BASE_URL", "https://worker.example.run.app")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        get_settings.cache_clear()
        monkeypatch.setattr(job_dispatcher, "_auth_headers", lambda: {"Authorization": "Bearer x"})
        sent = []

        class Resp:
            def __init__(self, code):
                self.status_code, self.text = code, ""

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

        def fake_post(url, headers, json, timeout):
            sent.append((url, json))
            return Resp(409 if len(sent) > 1 and json["task"]["name"] == sent[0][1]["task"]["name"] else 200)

        monkeypatch.setattr(job_dispatcher.requests, "post", fake_post)
        yield sent
        get_settings.cache_clear()

    def test_accepting_a_job_queues_a_signed_task(self, cloudtasks):
        job_id = get_provisioning_intake().accept(_reseller(), _request("queued.test"))["job_id"]
        url, body = cloudtasks[0]
        task = body["task"]
        assert url.endswith("/locations/us-central1/queues/provisioning-test/tasks")
        assert task["name"].endswith(f"/tasks/{job_id}")
        assert task["httpRequest"]["url"] == "https://worker.example.run.app/internal/tasks/provision-job"
        assert task["httpRequest"]["oidcToken"]["serviceAccountEmail"] == "invoker@example.iam.gserviceaccount.com"
        import base64
        assert json.loads(base64.b64decode(task["httpRequest"]["body"])) == {"job_id": job_id}
        assert job_executor.pending_count() == 0

    def test_queueing_the_same_job_twice_is_harmless(self, cloudtasks):
        job_dispatcher.enqueue_cloud_task("JOB-SAME")
        job_dispatcher.enqueue_cloud_task("JOB-SAME")
        assert len(cloudtasks) == 2

    def test_missing_configuration_rolls_back_the_reservation(self, cloudtasks, monkeypatch):
        monkeypatch.setenv("CLOUD_TASKS_QUEUE", "")
        get_settings.cache_clear()
        with pytest.raises(RuntimeError):
            get_provisioning_intake().accept(_reseller(), _request("noqueue.test", 3))
        assert _used() == 0
        assert get_lock_repo().holder("noqueue.test") is None


class TestWorkerEndpoint:
    def test_rejects_calls_without_a_valid_task_token(self, client):
        assert client.post("/internal/tasks/provision-job", json={"job_id": "JOB-X"}).status_code == 403
        r = client.post("/internal/tasks/provision-job", json={"job_id": "JOB-X"},
                        headers={"Authorization": "Bearer not-a-google-token"})
        assert r.status_code == 403

    def test_runs_the_job_and_is_idempotent(self, queued, monkeypatch):
        monkeypatch.setattr(job_dispatcher, "verify_task_request", lambda authorization: None)
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        job_id = queued.accept(_reseller(), _request("worker.test", 2))["job_id"]

        first = client.post("/internal/tasks/provision-job", json={"job_id": job_id})
        assert first.status_code == 200 and first.json()["outcome"] == "completed"
        again = client.post("/internal/tasks/provision-job", json={"job_id": job_id})
        assert again.status_code == 200 and again.json()["outcome"] == "done"
        assert _used() == 2

    def test_busy_job_asks_cloud_tasks_to_retry_later(self, queued, monkeypatch):
        monkeypatch.setattr(job_dispatcher, "verify_task_request", lambda authorization: None)
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        job_id = queued.accept(_reseller(), _request("busy.test", 1))["job_id"]
        get_job_repo().claim(job_id, "another-server", lease_seconds=600)
        assert client.post("/internal/tasks/provision-job", json={"job_id": job_id}).status_code == 503


class TestTimeouts:
    def test_google_calls_have_a_timeout(self):
        from google.auth.credentials import AnonymousCredentials
        from app.adapters.google.http_client import authorized_http
        assert authorized_http(AnonymousCredentials()).http.timeout == get_settings().google_call_timeout_seconds
