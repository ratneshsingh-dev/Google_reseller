"""Domain management: suspend, activate, delete, change licences (mock Google, in-memory store)."""

from __future__ import annotations

import pytest

from app.core import job_executor
from app.core.jwt_service import create_access_token, create_admin_view_token
from app.dependencies import get_provisioning_intake, get_reseller_repo
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument

BASE = "/api/v1/reseller/domains"


def _add_reseller(reseller_id: str, cap: int = 20) -> dict:
    get_reseller_repo().create(ResellerDocument(
        reseller_id=reseller_id, company_name=reseller_id, contact_email=f"{reseller_id.lower()}@partner.test",
        contact_name=reseller_id, max_licence_cap=cap, client_secret_hash="unused",
    ))
    return {"Authorization": "Bearer " + create_access_token(reseller_id, "RESELLER_FULL", 1)}


def _provision(domain: str, plan: str, seats: int, reseller_id: str) -> None:
    request = ProvisioningRequest(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", plan=plan, sku_id="1010020027", license_count=seats,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )
    reseller = get_reseller_repo().get_by_id(reseller_id)
    get_provisioning_intake().accept(reseller, request)
    assert job_executor.wait_until_idle(timeout=30)


def _used(reseller_id: str) -> int:
    return get_reseller_repo().get_by_id(reseller_id).licences_used


@pytest.fixture
def partner(client):
    headers = _add_reseller("RSL-P1")
    _provision("flex.test", "FLEXIBLE", 5, "RSL-P1")
    _provision("trial.test", "TRIAL", 3, "RSL-P1")
    assert _used("RSL-P1") == 8
    return headers


class TestLicences:
    def test_increase_charges_only_the_difference(self, client, partner):
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 8}, headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["previous_licences"] == 5 and r.json()["licences"] == 8 and r.json()["change"] == 3
        assert _used("RSL-P1") == 11

    def test_decrease_on_flexible_returns_licences(self, client, partner):
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 2}, headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["change"] == -3
        assert _used("RSL-P1") == 5

    def test_decrease_refused_on_trial(self, client, partner):
        r = client.patch(f"{BASE}/trial.test/licences", json={"license_count": 1}, headers=partner)
        assert r.status_code == 400
        assert "FLEXIBLE" in r.json()["detail"]
        assert _used("RSL-P1") == 8

    def test_same_count_is_a_no_op(self, client, partner):
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 5}, headers=partner)
        assert r.status_code == 200 and r.json()["change"] == 0
        assert _used("RSL-P1") == 8

    def test_increase_beyond_quota_refused(self, client, partner):
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 40}, headers=partner)
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "Licence cap exceeded"
        assert _used("RSL-P1") == 8

    def test_increase_above_100_is_batched(self, client):
        headers = _add_reseller("RSL-BIG", cap=500)
        _provision("big.test", "FLEXIBLE", 5, "RSL-BIG")
        r = client.patch(f"{BASE}/big.test/licences", json={"license_count": 250}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["licences"] == 250
        assert _used("RSL-BIG") == 250

    def test_zero_licences_rejected(self, client, partner):
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 0}, headers=partner)
        assert r.status_code == 422


class TestSuspendActivate:
    def test_suspend_then_activate(self, client, partner):
        r = client.post(f"{BASE}/flex.test/suspend", headers=partner)
        assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"
        assert r.json()["suspension_reasons"] == ["RESELLER_INITIATED"]
        r = client.post(f"{BASE}/flex.test/activate", headers=partner)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
        assert r.json()["suspension_reasons"] == []

    def test_activate_when_already_active_is_safe(self, client, partner):
        r = client.post(f"{BASE}/flex.test/activate", headers=partner)
        assert r.status_code == 200 and r.json()["status"] == "ACTIVE"

    def test_suspend_twice_is_safe(self, client, partner):
        client.post(f"{BASE}/flex.test/suspend", headers=partner)
        r = client.post(f"{BASE}/flex.test/suspend", headers=partner)
        assert r.status_code == 200 and r.json()["status"] == "SUSPENDED"

    def test_suspend_keeps_quota(self, client, partner):
        client.post(f"{BASE}/flex.test/suspend", headers=partner)
        assert _used("RSL-P1") == 8


def _google_suspends(domain: str, reason: str) -> None:
    """Simulate Google suspending the subscription itself (as it does for new paid plans)."""
    from app.dependencies import get_reseller_service
    mock = get_reseller_service()
    for subs in mock._subscriptions.values():
        for sid, sub in subs.items():
            if sub.customer_domain == domain:
                subs[sid] = sub.model_copy(update={"status": "SUSPENDED", "suspension_reasons": [reason]})


class TestGoogleSuspension:
    def test_cannot_activate_what_google_suspended(self, client, partner):
        _google_suspends("flex.test", "PENDING_TOS_ACCEPTANCE")
        r = client.post(f"{BASE}/flex.test/activate", headers=partner)
        assert r.status_code == 409
        body = r.json()["detail"]
        assert body["suspension_reasons"] == ["PENDING_TOS_ACCEPTANCE"]
        assert "Terms of Service" in body["detail"]

    def test_suspend_adds_our_suspension_on_top(self, client, partner):
        _google_suspends("flex.test", "PENDING_TOS_ACCEPTANCE")
        r = client.post(f"{BASE}/flex.test/suspend", headers=partner)
        assert r.status_code == 200
        assert set(r.json()["suspension_reasons"]) == {"PENDING_TOS_ACCEPTANCE", "RESELLER_INITIATED"}

    def test_activate_removes_ours_but_reports_google_hold(self, client, partner):
        _google_suspends("flex.test", "PENDING_TOS_ACCEPTANCE")
        client.post(f"{BASE}/flex.test/suspend", headers=partner)
        r = client.post(f"{BASE}/flex.test/activate", headers=partner)
        assert r.status_code == 200
        assert r.json()["status"] == "SUSPENDED"
        assert r.json()["suspension_reasons"] == ["PENDING_TOS_ACCEPTANCE"]
        assert "still suspended by Google" in r.json()["message"]

    def test_licences_can_change_while_google_holds_it(self, client, partner):
        _google_suspends("flex.test", "PENDING_TOS_ACCEPTANCE")
        r = client.patch(f"{BASE}/flex.test/licences", json={"license_count": 7}, headers=partner)
        assert r.status_code == 200 and r.json()["licences"] == 7


class TestDelete:
    def test_requires_confirmation(self, client, partner):
        assert client.delete(f"{BASE}/flex.test", headers=partner).status_code == 400
        assert client.delete(f"{BASE}/flex.test?confirm=other.test", headers=partner).status_code == 400
        assert _used("RSL-P1") == 8

    def test_delete_transfers_to_google_and_releases_licences(self, client, partner):
        r = client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "TRANSFERRED_TO_GOOGLE"
        assert body["licences_released"] == 5 and len(body["transferred_subscriptions"]) == 1
        assert _used("RSL-P1") == 3

    def test_deleted_domain_cannot_be_used(self, client, partner):
        client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        for call in (
            lambda: client.post(f"{BASE}/flex.test/suspend", headers=partner),
            lambda: client.patch(f"{BASE}/flex.test/licences", json={"license_count": 6}, headers=partner),
            lambda: client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner),
        ):
            assert call().status_code == 409

    def test_all_subscriptions_are_transferred_in_one_call(self, client, partner, monkeypatch):
        from app.dependencies import get_reseller_service
        mock = get_reseller_service()
        customer_id, subs = next((cid, s) for cid, s in mock._subscriptions.items()
                                 if any(x.customer_domain == "flex.test" for x in s.values()))
        first = next(iter(subs.values()))
        subs["SUB-EXTRA"] = first.model_copy(update={"subscription_id": "SUB-EXTRA", "sku_id": "OTHER-SKU"})
        calls = []
        original = mock.transfer_to_google

        async def record(cid, ids):
            calls.append(list(ids))
            return await original(cid, ids)

        monkeypatch.setattr(mock, "transfer_to_google", record)
        r = client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        assert r.status_code == 200, r.text
        assert len(calls) == 1 and len(calls[0]) == 2
        assert r.json()["licences_released"] == 10


class TestAccess:
    def test_other_partner_gets_not_found(self, client, partner):
        other = _add_reseller("RSL-P2")
        for call in (
            lambda: client.post(f"{BASE}/flex.test/suspend", headers=other),
            lambda: client.patch(f"{BASE}/flex.test/licences", json={"license_count": 1}, headers=other),
            lambda: client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=other),
        ):
            assert call().status_code == 404

    def test_unknown_domain(self, client, partner):
        assert client.post(f"{BASE}/nothing.test/suspend", headers=partner).status_code == 404

    def test_no_token(self, client, partner):
        assert client.post(f"{BASE}/flex.test/suspend").status_code == 401

    def test_admin_view_is_read_only(self, client, partner):
        view = {"Authorization": "Bearer " + create_admin_view_token("RSL-P1", "boss@econz.test", 1)}
        assert client.post(f"{BASE}/flex.test/suspend", headers=view).status_code == 403
        assert client.patch(f"{BASE}/flex.test/licences", json={"license_count": 9}, headers=view).status_code == 403
        assert client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=view).status_code == 403

    def test_readonly_role_cannot_act(self, client, partner):
        get_reseller_repo().update("RSL-P1", {"role": "RESELLER_READONLY"})
        ro = {"Authorization": "Bearer " + create_access_token("RSL-P1", "RESELLER_READONLY", 1)}
        assert client.post(f"{BASE}/flex.test/suspend", headers=ro).status_code == 403


class TestAddAdmin:
    """POST /domains/{domain}/admins: add an extra user and make it a super admin."""

    def _directory(self):
        from app.dependencies import get_directory_service
        return get_directory_service()

    def test_creates_user_and_makes_admin(self, client, partner):
        r = client.post(f"{BASE}/flex.test/admin",
                        json={"first_name": "Sam", "last_name": "Support", "username": "sam.support"},
                        headers=partner)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["admin_email"] == "sam.support@flex.test"
        assert body["outcome"] == "CREATED_AND_ADMIN" and body["is_admin"] is True
        assert body["temporary_password"] == "Welcome@12345!"
        import asyncio
        user = asyncio.run(self._directory().get_user_safe("sam.support@flex.test"))
        assert user is not None and user.is_admin

    def test_defaults_to_first_dot_last(self, client, partner):
        r = client.post(f"{BASE}/flex.test/admin", json={"first_name": "Sam", "last_name": "Support"}, headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["admin_email"] == "sam.support@flex.test"

    def test_existing_non_admin_is_promoted_without_password(self, client, partner):
        import asyncio
        from app.models.google_api import GoogleCreateUserRequest, GoogleUserName
        asyncio.run(self._directory().create_user(GoogleCreateUserRequest(
            primaryEmail="staff@flex.test", name=GoogleUserName(givenName="St", familyName="Aff"),
            password="Own-Pass-1!", changePasswordAtNextLogin=False, suspended=False)))
        r = client.post(f"{BASE}/flex.test/admin",
                        json={"first_name": "St", "last_name": "Aff", "username": "staff"}, headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["outcome"] == "MADE_ADMIN" and "temporary_password" not in r.json()

    def test_second_call_is_safe(self, client, partner):
        body = {"first_name": "Sam", "last_name": "Support", "username": "sam.support"}
        assert client.post(f"{BASE}/flex.test/admin", json=body, headers=partner).status_code == 200
        r = client.post(f"{BASE}/flex.test/admin", json=body, headers=partner)
        assert r.status_code == 200 and r.json()["outcome"] == "ALREADY_ADMIN"
        assert "temporary_password" not in r.json()

    def test_unknown_or_other_partners_domain_is_404(self, client, partner):
        assert client.post(f"{BASE}/nope.test/admin", json={"first_name": "A", "last_name": "B"},
                           headers=partner).status_code == 404
        other = _add_reseller("RSL-P2")
        assert client.post(f"{BASE}/flex.test/admin", json={"first_name": "A", "last_name": "B"},
                           headers=other).status_code == 404

    def test_bad_username_and_wrong_domain_rejected(self, client, partner):
        for name in ("bad name", "a..b", "x@other.com"):
            r = client.post(f"{BASE}/flex.test/admin",
                            json={"first_name": "A", "last_name": "B", "username": name}, headers=partner)
            assert r.status_code == 400, (name, r.text)

    def test_requires_login(self, client, partner):
        assert client.post(f"{BASE}/flex.test/admin", json={"first_name": "A", "last_name": "B"}).status_code in (401, 403)

    def test_does_not_use_licence_quota(self, client, partner):
        before = _used("RSL-P1")
        client.post(f"{BASE}/flex.test/admin", json={"first_name": "A", "last_name": "B"}, headers=partner)
        assert _used("RSL-P1") == before


class TestOriginalAdmin:
    """No body: fix the admin from the original provisioning request."""

    def _directory(self):
        from app.dependencies import get_directory_service
        return get_directory_service()

    def test_no_body_repairs_the_original_admin(self, client, partner):
        import asyncio
        d = self._directory()
        asyncio.run(d.delete_user("ada.admin@flex.test"))      # admin was never created
        r = client.post(f"{BASE}/flex.test/admin", headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["admin_email"] == "ada.admin@flex.test" and r.json()["outcome"] == "CREATED_AND_ADMIN"
        assert asyncio.run(d.get_user_safe("ada.admin@flex.test")).is_admin

    def test_no_body_promotes_user_that_is_not_admin(self, client, partner):
        import asyncio
        d = self._directory()
        asyncio.run(d.get_user_safe("ada.admin@flex.test")).is_admin = False
        r = client.post(f"{BASE}/flex.test/admin", headers=partner)
        assert r.status_code == 200 and r.json()["outcome"] == "MADE_ADMIN"
        assert "temporary_password" not in r.json()

    def test_fixing_original_admin_clears_failed_flag(self, client, partner):
        from app.dependencies import get_job_repo
        job = get_job_repo().list_by_domain("flex.test")[0]
        get_job_repo().update_fields(job.job_id, admin_created=False, users_created=0)
        assert client.post(f"{BASE}/flex.test/admin", headers=partner).status_code == 200
        fresh = get_job_repo().get_job(job.job_id)
        assert fresh.admin_created is True and fresh.users_created == 1


class TestBulkAdmin:
    BULK = BASE + "/admin/bulk"

    def test_bulk_fixes_many_and_reports_each(self, client):
        import asyncio
        from app.dependencies import get_directory_service
        headers = _add_reseller("RSL-BULK", cap=200)
        names = [f"bulk{i}.test" for i in range(12)]
        for n in names:
            _provision(n, "FLEXIBLE", 1, "RSL-BULK")
        d = get_directory_service()
        for n in names[:6]:
            asyncio.run(d.delete_user(f"ada.admin@{n}"))              # never created
        r = client.post(self.BULK, json={"domains": names + ["missing.test", names[0]]}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["requested"] == 13 and body["fixed"] == 12 and body["failed"] == 1
        by = {x["domain"]: x for x in body["results"]}
        assert by["missing.test"]["http_status"] == 404
        assert by[names[0]]["outcome"] == "CREATED_AND_ADMIN"
        assert by[names[11]]["outcome"] == "ALREADY_ADMIN"
        for n in names:
            assert asyncio.run(d.get_user_safe(f"ada.admin@{n}")).is_admin

    def test_bulk_rejects_too_many_or_empty(self, client, partner):
        assert client.post(self.BULK, json={"domains": []}, headers=partner).status_code == 422
        assert client.post(self.BULK, json={"domains": [f"d{i}.test" for i in range(51)]},
                           headers=partner).status_code == 422

    def test_bulk_requires_login(self, client, partner):
        assert client.post(self.BULK, json={"domains": ["flex.test"]}).status_code in (401, 403)

    def test_other_partners_domains_are_not_touched(self, client, partner):
        other = _add_reseller("RSL-P9")
        r = client.post(self.BULK, json={"domains": ["flex.test"]}, headers=other)
        assert r.json()["failed"] == 1 and r.json()["results"][0]["http_status"] == 404

    def test_parallel_single_calls_all_succeed(self, client):
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from app.dependencies import get_directory_service
        headers = _add_reseller("RSL-PAR", cap=200)
        names = [f"par{i}.test" for i in range(20)]
        for n in names:
            _provision(n, "FLEXIBLE", 1, "RSL-PAR")
        d = get_directory_service()
        for n in names:
            asyncio.run(d.delete_user(f"ada.admin@{n}"))
        with ThreadPoolExecutor(20) as pool:
            codes = list(pool.map(lambda n: client.post(f"{BASE}/{n}/admin", headers=headers).status_code, names))
        assert codes == [200] * 20, codes
