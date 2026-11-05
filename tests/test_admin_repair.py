"""Failed-admin dashboard: list, check, fix admin, mark handled. Offline (mock Google, in-memory store)."""

from __future__ import annotations

import asyncio
import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.core import google_retry, job_executor
from app.core.auth_middleware import require_admin
from app.dependencies import (
    get_directory_service,
    get_employee_repo,
    get_job_repo,
    get_provisioning_intake,
    get_reseller_repo,
)
from app.main import app
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument

STATIC_PASSWORD = "Welcome@12345!"


def http_error(status: int, reason: str = "forbidden") -> HttpError:
    body = {"error": {"code": status, "message": "Not Authorized to access this resource/api",
                      "errors": [{"reason": reason, "message": "x"}]}}
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode())


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    import app.services.provisioning_service as ps

    async def instant(_seconds):
        return None
    monkeypatch.setattr(google_retry.asyncio, "sleep", instant)
    monkeypatch.setattr(ps.asyncio, "sleep", instant)


@pytest.fixture
def admin_client(client):
    app.dependency_overrides[require_admin] = lambda: {"email": "boss@econz.test"}
    yield client
    app.dependency_overrides.pop(require_admin, None)


def _reseller() -> ResellerDocument:
    doc = ResellerDocument(reseller_id="RSL-R1", company_name="R1", contact_email="r1@partner.test",
                           contact_name="R1", max_licence_cap=500, client_secret_hash="unused")
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


def _failed_job(monkeypatch, domain: str, refuse: str):
    """Run a job whose admin step is refused by Google, then restore Google. refuse = 'make_admin' or 'create_user'."""
    directory = get_directory_service()
    original = getattr(directory, refuse)

    async def refused(*args, **kwargs):
        raise http_error(403)

    monkeypatch.setattr(directory, refuse, refused)
    result = get_provisioning_intake().accept(_reseller(), _request(domain))
    assert job_executor.wait_until_idle(30)
    monkeypatch.setattr(directory, refuse, original)
    job = get_job_repo().get_job(result["job_id"])
    assert job.admin_created is False
    return job


class TestFixAdmin:
    def test_user_exists_but_not_admin_is_made_admin(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "madeadmin.test", "make_admin")
        email = ProvisioningRequest(**job.request_payload).admin_email
        directory = get_directory_service()
        assert asyncio.run(directory.get_user(email)).is_admin is False

        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is True and body["outcome"] == "MADE_ADMIN"
        assert body["temporary_password"] == STATIC_PASSWORD
        assert asyncio.run(directory.get_user(email)).is_admin is True
        after = get_job_repo().get_job(job.job_id)
        assert after.admin_created is True and not after.admin_failure_reason
        assert after.status == "COMPLETED" and after.admin_repair_attempts == 1

    def test_missing_user_is_created_with_the_static_password_then_made_admin(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "createdadmin.test", "create_user")
        email = ProvisioningRequest(**job.request_payload).admin_email
        directory = get_directory_service()
        assert asyncio.run(directory.get_user_safe(email)) is None

        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is True and body["outcome"] == "CREATED_AND_ADMIN"
        assert body["temporary_password"] == STATIC_PASSWORD
        assert asyncio.run(directory.get_user(email)).is_admin is True
        saved = [e for e in get_employee_repo().list_all() if e.corporate_email.lower() == email.lower()]
        assert any(e.temporary_password == STATIC_PASSWORD for e in saved)

    def test_google_still_refusing_keeps_it_on_the_list_with_the_reason(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "stillrefused.test", "make_admin")
        directory = get_directory_service()

        async def refuse(email):
            raise http_error(403)
        monkeypatch.setattr(directory, "make_admin", refuse)

        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is False and "403" in body["reason"] and body["reason_kind"] == "google_403"
        after = get_job_repo().get_job(job.job_id)
        assert after.admin_created is False and after.admin_repair_attempts == 1
        assert job.job_id in [j.job_id for j in get_job_repo().list_admin_failures()]

    def test_an_existing_users_password_is_never_reset(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "keeppassword.test", "make_admin")
        directory = get_directory_service()

        async def forbidden_update(*args, **kwargs):
            raise AssertionError("repair must not change an existing user's password")
        monkeypatch.setattr(directory, "update_user", forbidden_update)
        assert admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()["fixed"] is True

    def test_second_click_is_refused_because_it_is_no_longer_on_the_list(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "twice.test", "make_admin")
        assert admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()["fixed"] is True
        assert admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").status_code == 409

    def test_a_healthy_job_and_an_unknown_job_cannot_be_fixed(self, admin_client):
        result = get_provisioning_intake().accept(_reseller(), _request("healthy.test"))
        assert job_executor.wait_until_idle(30)
        assert admin_client.post(f"/api/v1/admin/admin-failures/{result['job_id']}/fix").status_code == 409
        assert admin_client.post("/api/v1/admin/admin-failures/JOB-NOPE/fix").status_code == 404

    def test_requires_admin(self, client):
        for method, path in [("post", "/api/v1/admin/admin-failures/JOB-X/fix"),
                             ("get", "/api/v1/admin/admin-failures/JOB-X/check"),
                             ("post", "/api/v1/admin/admin-failures/JOB-X/mark-handled")]:
            assert getattr(client, method)(path).status_code == 401


class TestCheckAndMarkHandled:
    def test_check_reports_what_google_has(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "checkme.test", "make_admin")
        body = admin_client.get(f"/api/v1/admin/admin-failures/{job.job_id}/check").json()
        assert body["user_exists"] is True and body["is_admin"] is False
        assert body["admin_email"] == ProvisioningRequest(**job.request_payload).admin_email

    def test_mark_handled_takes_it_off_the_list(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "byhand.test", "create_user")
        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/mark-handled").json()
        assert body["handled"] is True
        assert job.job_id not in [j.job_id for j in get_job_repo().list_admin_failures()]
        assert "Fixed manually" in get_job_repo().get_job(job.job_id).admin_failure_reason

    def test_list_rows_carry_what_the_dashboard_needs(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "rowdata.test", "make_admin")
        row = next(r for r in admin_client.get("/api/v1/admin/admin-failures").json()["domains"]
                   if r["job_id"] == job.job_id)
        assert row["domain"] == "rowdata.test" and row["reason_kind"] == "google_403"
        assert row["admin_email"] and row["repair_attempts"] == 0

    def test_dashboard_page_is_served_and_linked_from_the_admin_panel(self, client):
        assert client.get("/static/admin-failures.html").status_code == 200
        assert "admin-failures.html" in client.get("/static/reseller-admin.html").text



def _make_it_an_older_job(job_id: str, **extra):
    """Turn a finished job into one from before admin_created existed: no flag, no reason."""
    get_job_repo().update_fields(job_id, admin_created=None, admin_failure_reason=None,
                                 users_created=0, users_existing=0, **extra)


def _row(admin_client, job_id: str) -> dict:
    return next(r for r in admin_client.get("/api/v1/admin/admin-failures").json()["domains"]
                if r["job_id"] == job_id)


class TestOlderFailuresAppearByThemselves:
    """The failures from before this feature must be on the list right after deploying, with no
    migration step, and must be fixable like any other."""

    def test_an_older_failed_job_is_listed_and_a_good_job_is_not(self, admin_client, monkeypatch):
        old = _failed_job(monkeypatch, "oldfail.test", "make_admin")
        _make_it_an_older_job(old.job_id)
        good = get_provisioning_intake().accept(_reseller(), _request("oldgood.test"))["job_id"]
        assert job_executor.wait_until_idle(30)
        ids = [r["job_id"] for r in admin_client.get("/api/v1/admin/admin-failures").json()["domains"]]
        assert old.job_id in ids and good not in ids
        assert _row(admin_client, old.job_id)["legacy"] is True

    def test_the_reason_of_an_older_job_is_worked_out_from_what_was_stored(self, admin_client, monkeypatch):
        refused_admin = _failed_job(monkeypatch, "olda.test", "make_admin")    # user created, admin refused
        refused_create = _failed_job(monkeypatch, "oldb.test", "create_user")  # nothing created
        dropped = _failed_job(monkeypatch, "oldc.test", "make_admin")
        for j in (refused_admin, refused_create, dropped):
            _make_it_an_older_job(j.job_id)
        get_job_repo().update_fields(dropped.job_id, primary_domain="nocompany.test")  # no stored employee rows

        assert "making the user an admin" in _row(admin_client, refused_admin.job_id)["reason"]
        assert "creating the user" in _row(admin_client, refused_create.job_id)["reason"]
        assert "Dropped connection" in _row(admin_client, dropped.job_id)["reason"]
        assert _row(admin_client, dropped.job_id)["category"] == "fixable"
        assert _row(admin_client, refused_admin.job_id)["category"] == "try"

    def test_an_older_job_can_be_fixed_and_leaves_the_list(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "oldfix.test", "make_admin")
        _make_it_an_older_job(job.job_id)
        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is True
        assert job.job_id not in [r["job_id"] for r in admin_client.get("/api/v1/admin/admin-failures").json()["domains"]]
        assert get_job_repo().get_job(job.job_id).admin_created is True

    def test_an_older_job_can_be_marked_handled(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "oldhand.test", "create_user")
        _make_it_an_older_job(job.job_id)
        assert admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/mark-handled").json()["handled"] is True
        assert job.job_id not in [j.job_id for j in get_job_repo().list_admin_failures()]


class TestCategories:
    def test_a_google_403_is_try_first_and_becomes_manual_after_a_failed_fix(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "cat403.test", "make_admin")
        assert _row(admin_client, job.job_id)["category"] == "try"

        async def refuse(email):
            raise http_error(403)
        monkeypatch.setattr(get_directory_service(), "make_admin", refuse)
        assert admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()["fixed"] is False
        row = _row(admin_client, job.job_id)
        assert row["category"] == "manual" and row["repair_attempts"] == 1

    def test_a_dropped_connection_stays_fixable_even_after_a_failed_try(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "catpipe.test", "make_admin")
        get_job_repo().update_fields(job.job_id, admin_failure_reason="[Errno 32] Broken pipe",
                                     admin_repair_attempts=2)
        row = _row(admin_client, job.job_id)
        assert row["reason_kind"] == "dropped_connection" and row["category"] == "fixable"


class TestQuickCheckAfterFix:
    def test_check_without_subscription_skips_the_reseller_call(self, admin_client, monkeypatch):
        from app.dependencies import get_reseller_service
        job = _failed_job(monkeypatch, "quick.test", "make_admin")

        async def must_not_be_called(*args, **kwargs):
            raise AssertionError("quick check must not call the Reseller API")
        monkeypatch.setattr(get_reseller_service(), "list_subscriptions", must_not_be_called)
        body = admin_client.get(f"/api/v1/admin/admin-failures/{job.job_id}/check?subscription=false").json()
        assert body["user_exists"] is True and body["is_admin"] is False


class TestConfirmationWaitsForGoogle:
    """Google answers makeAdmin before the user reads back as an admin. A read-back taken at once
    gave a false "not fixed" even though the user became an admin a moment later."""

    def _slow_to_show_admin(self, monkeypatch, reads_before_admin: int) -> dict:
        directory = get_directory_service()
        original_get, original_make = directory.get_user_safe, directory.make_admin
        state = {"made": False, "reads_after_make": 0}

        async def make_admin(email):
            state["made"] = True
            return await original_make(email)

        async def get_user_safe(email):
            user = await original_get(email)
            if user and state["made"]:
                state["reads_after_make"] += 1
                if state["reads_after_make"] <= reads_before_admin:
                    return user.model_copy(update={"is_admin": False})
            return user

        monkeypatch.setattr(directory, "make_admin", make_admin)
        monkeypatch.setattr(directory, "get_user_safe", get_user_safe)
        return state

    def test_an_admin_that_shows_up_a_few_seconds_late_is_still_a_success(self, admin_client, monkeypatch):
        job = _failed_job(monkeypatch, "slowadmin.test", "make_admin")
        state = self._slow_to_show_admin(monkeypatch, reads_before_admin=2)
        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is True and state["reads_after_make"] == 3
        assert get_job_repo().get_job(job.job_id).admin_created is True

    def test_an_admin_that_never_shows_up_is_not_called_fixed(self, admin_client, monkeypatch):
        from app.services.provisioning_service import ADMIN_CONFIRM_WAITS
        job = _failed_job(monkeypatch, "neveradmin.test", "make_admin")
        state = self._slow_to_show_admin(monkeypatch, reads_before_admin=999)
        body = admin_client.post(f"/api/v1/admin/admin-failures/{job.job_id}/fix").json()
        assert body["fixed"] is False and "still not an admin" in body["reason"]
        assert state["reads_after_make"] == len(ADMIN_CONFIRM_WAITS)
        assert get_job_repo().get_job(job.job_id).admin_created is False
        assert job.job_id in [j.job_id for j in get_job_repo().list_admin_failures()]
