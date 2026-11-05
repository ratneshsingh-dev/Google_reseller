"""Temporary-error retries (Google rate limits, server errors, dropped connections) and
the once-a-minute activity write. Offline: mock Google, in-memory store."""

from __future__ import annotations

import asyncio
import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.core import google_retry, job_executor
from app.core.jwt_service import create_access_token
from app.dependencies import (
    get_job_repo,
    get_provisioning_intake,
    get_reseller_repo,
    get_reseller_service,
)
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument


def http_error(status: int, reason: str = "") -> HttpError:
    body = {"error": {"code": status, "message": "x", "errors": [{"reason": reason, "message": "x"}] if reason else []}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(google_retry.asyncio, "sleep", instant)


class TestWhatIsTemporary:
    @pytest.mark.parametrize("exc", [
        http_error(403, "rateLimitExceeded"),
        http_error(403, "userRateLimitExceeded"),
        http_error(429),
        http_error(500), http_error(503),
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError(),
        TimeoutError(),
    ])
    def test_temporary(self, exc):
        assert google_retry.is_transient(exc)

    @pytest.mark.parametrize("exc", [
        http_error(403, "forbidden"),
        http_error(400, "invalid"),
        http_error(404, "notFound"),
        http_error(403, "dailyLimitExceeded"),
        ValueError("bad input"),
    ])
    def test_permanent(self, exc):
        assert not google_retry.is_transient(exc)

    def test_rate_limit_waits_long_enough_to_reach_the_next_quota_minute(self):
        rate = http_error(403, "rateLimitExceeded")
        delays = [google_retry.retry_delay(rate, i) for i in range(6)]
        assert delays[0] >= 4 and max(delays) <= 72
        assert sum(delays) >= 60


class TestCallWithRetry:
    def test_retries_a_rate_limit_then_succeeds(self):
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise http_error(403, "rateLimitExceeded")
            return "ok"

        assert asyncio.run(google_retry.call_with_retry(flaky)) == "ok"
        assert calls["n"] == 3

    def test_does_not_retry_a_permanent_error(self):
        calls = {"n": 0}

        async def refused():
            calls["n"] += 1
            raise http_error(403, "forbidden")

        with pytest.raises(HttpError):
            asyncio.run(google_retry.call_with_retry(refused))
        assert calls["n"] == 1

    def test_gives_up_after_the_last_attempt(self):
        async def always_limited():
            raise http_error(429)

        with pytest.raises(HttpError):
            asyncio.run(google_retry.call_with_retry(always_limited))


def _reseller(cap: int = 50) -> ResellerDocument:
    doc = ResellerDocument(reseller_id="RSL-R1", company_name="R1", contact_email="r1@partner.test",
                           contact_name="R1", max_licence_cap=cap, client_secret_hash="unused")
    get_reseller_repo().create(doc)
    return doc


def _request(domain: str) -> ProvisioningRequest:
    return ProvisioningRequest(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", plan="FLEXIBLE", sku_id="1010020027", license_count=2,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )


class TestJobsSurviveTemporaryErrors:
    @pytest.mark.parametrize("method,error", [
        ("create_subscription", http_error(403, "rateLimitExceeded")),
        ("create_customer", http_error(403, "rateLimitExceeded")),
        ("get_customer_by_domain", http_error(429)),
        ("list_subscriptions", BrokenPipeError(32, "Broken pipe")),
    ])
    def test_job_completes_after_one_temporary_failure(self, client, monkeypatch, method, error):
        mock = get_reseller_service()
        original = getattr(mock, method)
        state = {"failed": False}

        async def fail_once(*args, **kwargs):
            if not state["failed"]:
                state["failed"] = True
                raise error
            return await original(*args, **kwargs)

        monkeypatch.setattr(mock, method, fail_once)
        result = get_provisioning_intake().accept(_reseller(), _request(f"{method.replace('_', '')}.test"))
        assert job_executor.wait_until_idle(30)
        job = get_job_repo().get_job(result["job_id"])
        assert state["failed"] and job.status == "COMPLETED", job.error_message
        assert get_reseller_repo().get_by_id("RSL-R1").licences_used == 2


class TestMakeAdminRightAfterCreate:
    """Google can return 404 "Resource Not Found: userKey" from makeAdmin for a user created a
    second earlier (eventual consistency). That must be retried, not treated as final."""

    @pytest.fixture(autouse=True)
    def instant_waits(self, monkeypatch):
        import app.services.provisioning_service as ps

        async def instant(_seconds):
            return None
        monkeypatch.setattr(ps.asyncio, "sleep", instant)

    def _run_with_make_admin(self, monkeypatch, behaviour):
        from app.dependencies import get_directory_service
        directory = get_directory_service()
        original = directory.make_admin
        calls = {"n": 0}

        async def make_admin(email):
            calls["n"] += 1
            error = behaviour(calls["n"])
            if error:
                raise error
            return await original(email)

        monkeypatch.setattr(directory, "make_admin", make_admin)
        result = get_provisioning_intake().accept(_reseller(), _request("makeadmin.test"))
        assert job_executor.wait_until_idle(30)
        jobs = get_job_repo()
        job = jobs.get_job(result["job_id"])
        step = next(s for s in jobs.get_steps_for_job(job.job_id) if s.step_name == "CREATE_USERS")
        admin_status = step.details.rsplit("Status: ", 1)[-1].strip()
        return job, admin_status, calls["n"]

    def test_not_found_twice_then_admin_is_granted(self, client, monkeypatch):
        job, admin_status, calls = self._run_with_make_admin(
            monkeypatch, lambda n: http_error(404, "notFound") if n <= 2 else None)
        assert job.status == "COMPLETED" and admin_status == "PROVISIONED" and calls == 3

    def test_permanent_error_is_not_retried(self, client, monkeypatch):
        job, admin_status, calls = self._run_with_make_admin(
            monkeypatch, lambda n: http_error(403, "forbidden"))
        assert admin_status == "FAILED" and calls == 1

    def test_gives_up_after_the_last_wait(self, client, monkeypatch):
        job, admin_status, calls = self._run_with_make_admin(
            monkeypatch, lambda n: http_error(404, "notFound"))
        assert admin_status == "FAILED" and calls == 4


class TestActivityWrite:
    def test_last_api_call_is_written_at_most_once_a_minute(self, client, monkeypatch):
        _reseller()
        headers = {"Authorization": "Bearer " + create_access_token("RSL-R1", "RESELLER_FULL", 1)}
        repo_cls = type(get_reseller_repo())
        writes = []
        original_update = repo_cls.update

        def counting_update(self, reseller_id, data):
            if "last_api_call_at" in data:
                writes.append(reseller_id)
            return original_update(self, reseller_id, data)

        monkeypatch.setattr(repo_cls, "update", counting_update)
        for _ in range(10):
            assert client.get("/api/v1/reseller/quota", headers=headers).status_code == 200
        assert len(writes) == 1


class TestAdminStepIsReliableAndHonest:
    """The admin user is the last step of a job. A dropped connection on its first Directory
    call used to skip the whole step silently; now it is retried, and when the admin still
    cannot be created the job says so (admin_created=False) instead of looking fully done."""

    @pytest.fixture(autouse=True)
    def instant_waits(self, monkeypatch):
        import app.services.provisioning_service as ps

        async def instant(_seconds):
            return None
        monkeypatch.setattr(ps.asyncio, "sleep", instant)

    def _run(self, domain):
        result = get_provisioning_intake().accept(_reseller(), _request(domain))
        assert job_executor.wait_until_idle(30)
        return get_job_repo().get_job(result["job_id"])

    def test_dropped_connection_on_the_first_lookup_is_retried(self, client, monkeypatch):
        from app.dependencies import get_directory_service
        directory = get_directory_service()
        original = directory.get_user_safe
        state = {"n": 0}

        async def flaky(email):
            state["n"] += 1
            if state["n"] == 1:
                raise BrokenPipeError(32, "Broken pipe")
            return await original(email)

        monkeypatch.setattr(directory, "get_user_safe", flaky)
        job = self._run("pipelookup.test")
        assert state["n"] == 2
        assert job.status == "COMPLETED" and job.users_created == 1
        assert job.admin_created is True and not job.admin_failure_reason

    def test_a_good_job_is_marked_admin_created(self, client):
        job = self._run("goodadmin.test")
        assert job.admin_created is True and job.users_created == 1

    def test_permanent_refusal_is_recorded_and_listed(self, client, monkeypatch):
        from app.dependencies import get_directory_service
        directory = get_directory_service()

        async def refuse(email):
            raise http_error(403, "forbidden")

        monkeypatch.setattr(directory, "make_admin", refuse)
        job = self._run("refused.test")
        assert job.status == "COMPLETED"            # domain and subscription do exist
        assert job.admin_created is False
        assert "403" in (job.admin_failure_reason or "")
        listed = [j.job_id for j in get_job_repo().list_admin_failures()]
        assert job.job_id in listed

    def test_good_jobs_are_not_in_the_failure_list(self, client):
        job = self._run("notlisted.test")
        assert job.job_id not in [j.job_id for j in get_job_repo().list_admin_failures()]

    def test_existing_user_who_is_not_admin_is_made_admin(self, client):
        """An earlier attempt created the user but failed to grant admin; running the job again
        must finish the job instead of treating the plain user as done."""
        from app.dependencies import get_directory_service
        from app.models.google_api import GoogleCreateUserRequest, GoogleUserName
        directory = get_directory_service()
        admin_email = _request("halfdone.test").admin_email
        asyncio.run(directory.create_user(GoogleCreateUserRequest(
            primaryEmail=admin_email, name=GoogleUserName(givenName="Ada", familyName="Admin"),
            password="Temp#12345678", changePasswordAtNextLogin=True, suspended=False)))
        assert asyncio.run(directory.get_user(admin_email)).is_admin is False
        job = self._run("halfdone.test")
        assert asyncio.run(directory.get_user(admin_email)).is_admin is True
        assert job.admin_created is True


class TestEmailRetriesOnlyDroppedConnections:
    def test_dropped_connection_is_retried(self):
        state = {"n": 0}

        async def send():
            state["n"] += 1
            if state["n"] < 3:
                raise BrokenPipeError(32, "Broken pipe")
            return "sent"

        assert asyncio.run(google_retry.call_with_network_retry(send)) == "sent"
        assert state["n"] == 3

    def test_rate_limit_fails_fast_without_waiting(self):
        state = {"n": 0}

        async def send():
            state["n"] += 1
            raise http_error(429, "rateLimitExceeded")

        with pytest.raises(HttpError):
            asyncio.run(google_retry.call_with_network_retry(send))
        assert state["n"] == 1


class TestAdminFailuresEndpoint:
    @pytest.fixture
    def admin_client(self, client):
        from app.core.auth_middleware import require_admin
        from app.main import app
        app.dependency_overrides[require_admin] = lambda: {"email": "boss@econz.test"}
        yield client
        app.dependency_overrides.pop(require_admin, None)

    def test_requires_admin(self, client):
        assert client.get("/api/v1/admin/admin-failures").status_code == 401

    def test_lists_failed_domains_as_json_and_csv(self, admin_client, monkeypatch):
        from app.dependencies import get_directory_service
        directory = get_directory_service()

        async def refuse(email):
            raise http_error(403, "forbidden")

        monkeypatch.setattr(directory, "make_admin", refuse)
        import app.services.provisioning_service as ps

        async def instant(_seconds):
            return None
        monkeypatch.setattr(ps.asyncio, "sleep", instant)
        get_provisioning_intake().accept(_reseller(), _request("listme.test"))
        assert job_executor.wait_until_idle(30)
        body = admin_client.get("/api/v1/admin/admin-failures").json()
        assert body["count"] == 1 and body["domains"][0]["domain"] == "listme.test"
        csv_resp = admin_client.get("/api/v1/admin/admin-failures?format=csv")
        assert csv_resp.status_code == 200 and "listme.test" in csv_resp.text
