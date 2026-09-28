"""Domain management: suspend, activate, delete, change licences (mock Google, in-memory store)."""

from __future__ import annotations

import asyncio

import pytest

from app.core.jwt_service import create_access_token, create_admin_view_token
from app.dependencies import get_job_repo, get_provisioning_service, get_reseller_repo
from app.models.database import ProvisioningJobDocument
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
    job_id = f"JOB-{domain[:8].upper()}"
    get_job_repo().create_job(ProvisioningJobDocument(
        job_id=job_id, company_name=domain, primary_domain=domain, status="PENDING", reseller_id=reseller_id))
    asyncio.run(get_provisioning_service().provision_company(request, job_id, reseller_id=reseller_id))


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

    def test_delete_releases_licences(self, client, partner):
        r = client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        assert r.status_code == 200, r.text
        assert r.json()["licences_released"] == 5
        assert _used("RSL-P1") == 3

    def test_deleted_domain_cannot_be_used(self, client, partner):
        client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        for call in (
            lambda: client.post(f"{BASE}/flex.test/suspend", headers=partner),
            lambda: client.patch(f"{BASE}/flex.test/licences", json={"license_count": 6}, headers=partner),
            lambda: client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner),
        ):
            assert call().status_code == 409

    def test_reprovisioning_a_deleted_domain_works(self, client, partner):
        client.delete(f"{BASE}/flex.test?confirm=flex.test", headers=partner)
        _provision("flex.test", "FLEXIBLE", 2, "RSL-P1")
        r = client.post(f"{BASE}/flex.test/suspend", headers=partner)
        assert r.status_code == 200


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
