"""Concurrency: parallel requests must stay correct (quota, one job per domain, idempotency)
and must actually run in parallel. Runs offline against the mock Google adapter."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.core import job_executor
from app.core.exceptions import PermanentError
from app.core.jwt_service import create_access_token
from app.core.rate_limit import _rate_limit_key
from app.dependencies import (
    get_domain_service,
    get_job_repo,
    get_provisioning_intake,
    get_reseller_repo,
    get_reseller_service,
)
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument
from app.services.domain_service import DomainActionError
from app.services.provisioning_intake import IntakeError


def _reseller(reseller_id: str = "RSL-C1", cap: int = 100) -> ResellerDocument:
    doc = ResellerDocument(
        reseller_id=reseller_id, company_name=reseller_id, contact_email=f"{reseller_id.lower()}@partner.test",
        contact_name=reseller_id, max_licence_cap=cap, client_secret_hash="unused",
    )
    get_reseller_repo().create(doc)
    return doc


def _payload(domain: str, seats: int = 1, plan: str = "FLEXIBLE") -> dict:
    return dict(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", plan=plan, sku_id="1010020027", license_count=seats,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )


def _request(domain: str, seats: int = 1) -> ProvisioningRequest:
    return ProvisioningRequest(**_payload(domain, seats))


def _used(reseller_id: str) -> int:
    return get_reseller_repo().get_by_id(reseller_id).licences_used


def _parallel(fn, args):
    with ThreadPoolExecutor(max_workers=len(args)) as pool:
        return list(pool.map(fn, args))


def _slow_google(monkeypatch, method: str, seconds: float):
    """Make one mock Google call take `seconds`, like the real API (~20 s per call)."""
    mock = get_reseller_service()
    original = getattr(mock, method)

    async def slow(*args, **kwargs):
        await asyncio.sleep(seconds)
        return await original(*args, **kwargs)

    monkeypatch.setattr(mock, method, slow)


@pytest.fixture
def intake(client):
    return get_provisioning_intake()


class TestQuotaUnderLoad:
    def test_parallel_requests_never_exceed_the_cap(self, intake):
        reseller = _reseller(cap=10)

        def attempt(i):
            try:
                intake.accept(reseller, _request(f"cap{i}.test"))
                return "accepted"
            except IntakeError as exc:
                return exc.status_code

        outcomes = _parallel(attempt, range(25))
        assert outcomes.count("accepted") == 10
        assert outcomes.count(400) == 15
        assert job_executor.wait_until_idle(30)
        assert _used("RSL-C1") == 10

    def test_concurrent_quota_updates_are_never_lost(self, client):
        _reseller(cap=10_000)
        repo = get_reseller_repo()
        _parallel(lambda _: repo.increment_licences_used("RSL-C1", 1), range(200))
        assert _used("RSL-C1") == 200

    def test_field_update_does_not_undo_a_quota_change(self, client):
        """The login timestamp write used to re-save the whole record and could erase quota changes."""
        _reseller(cap=10_000)
        repo = get_reseller_repo()

        def mixed(i):
            if i % 2:
                repo.increment_licences_used("RSL-C1", 1)
            else:
                repo.update("RSL-C1", {"last_api_call_at": "2026-09-29T00:00:00Z"})

        _parallel(mixed, range(200))
        assert _used("RSL-C1") == 100


class TestOneOperationPerDomain:
    def test_same_domain_sent_twice_at_once(self, intake, monkeypatch):
        _slow_google(monkeypatch, "create_customer", 0.5)
        reseller = _reseller()

        def attempt(_):
            try:
                return intake.accept(reseller, _request("twice.test", 3))["job_id"]
            except IntakeError as exc:
                return exc.status_code

        outcomes = _parallel(attempt, range(8))
        assert len([o for o in outcomes if isinstance(o, str)]) == 1
        assert outcomes.count(409) == 7
        assert job_executor.wait_until_idle(30)
        assert _used("RSL-C1") == 3

    def test_lock_is_released_after_the_job(self, intake):
        reseller = _reseller()
        intake.accept(reseller, _request("again.test", 2))
        assert job_executor.wait_until_idle(30)
        result = intake.accept(reseller, _request("again.test", 4))
        assert job_executor.wait_until_idle(30)
        assert get_job_repo().get_job(result["job_id"]).status == "COMPLETED"
        assert _used("RSL-C1") == 4

    def test_licence_changes_on_one_domain_do_not_overlap(self, intake, monkeypatch):
        reseller = _reseller()
        intake.accept(reseller, _request("serial.test", 5))
        assert job_executor.wait_until_idle(30)
        _slow_google(monkeypatch, "change_seats", 0.3)
        service = get_domain_service()

        def change(target):
            try:
                asyncio.run(service.change_licences("serial.test", get_reseller_repo().get_by_id("RSL-C1"), target))
                return "changed"
            except DomainActionError as exc:
                return exc.status_code

        outcomes = _parallel(change, [6, 7, 8, 9, 10, 11])
        assert "changed" in outcomes and 409 in outcomes
        mock = get_reseller_service()
        seats = [s.seats.number_of_seats for subs in mock._subscriptions.values() for s in subs.values()
                 if s.customer_domain == "serial.test"][0]
        assert _used("RSL-C1") == seats


class TestFailures:
    def test_failed_job_refunds_quota_and_frees_the_domain(self, intake, monkeypatch):
        reseller = _reseller(cap=10)

        async def refuse(*args, **kwargs):
            raise PermanentError("Google refused the subscription")

        monkeypatch.setattr(get_reseller_service(), "create_subscription", refuse)
        result = intake.accept(reseller, _request("fails.test", 6))
        assert _used("RSL-C1") == 6
        assert job_executor.wait_until_idle(30)
        assert get_job_repo().get_job(result["job_id"]).status == "FAILED"
        assert _used("RSL-C1") == 0

        monkeypatch.undo()
        retry = intake.accept(reseller, _request("fails.test", 6))
        assert job_executor.wait_until_idle(30)
        assert get_job_repo().get_job(retry["job_id"]).status == "COMPLETED"
        assert _used("RSL-C1") == 6


class TestIdempotency:
    def test_same_key_returns_the_same_job(self, intake):
        reseller = _reseller()
        first = intake.accept(reseller, _request("idem.test", 2), idempotency_key="order-1")
        again = intake.accept(reseller, _request("idem.test", 2), idempotency_key="order-1")
        assert again["job_id"] == first["job_id"] and again["idempotent_replay"] is True
        assert job_executor.wait_until_idle(30)
        assert _used("RSL-C1") == 2

    def test_same_key_sent_ten_times_at_once_creates_one_job(self, intake):
        reseller = _reseller()
        job_ids = _parallel(
            lambda _: intake.accept(reseller, _request("burst.test", 3), idempotency_key="order-2")["job_id"],
            range(10),
        )
        assert len(set(job_ids)) == 1
        assert job_executor.wait_until_idle(30)
        assert _used("RSL-C1") == 3

    def test_same_key_with_a_different_body_is_refused(self, intake):
        reseller = _reseller()
        intake.accept(reseller, _request("idem2.test", 2), idempotency_key="order-3")
        with pytest.raises(IntakeError) as exc:
            intake.accept(reseller, _request("idem2.test", 5), idempotency_key="order-3")
        assert exc.value.status_code == 409


class TestParallelSpeed:
    def test_fifty_jobs_take_about_as_long_as_two(self, intake, monkeypatch):
        """Each job waits 1 s on Google. Run one after another, 50 jobs would take 50+ s."""
        _slow_google(monkeypatch, "create_customer", 1.0)
        reseller = _reseller(cap=500)

        started = time.time()
        result = intake.accept_bulk(reseller, [_payload(f"speed{i}.test", 2) for i in range(50)])
        accepted_in = time.time() - started
        assert result["accepted"] == 50
        assert job_executor.wait_until_idle(60)
        elapsed = time.time() - started

        assert accepted_in < 5, f"accepting 50 took {accepted_in:.1f}s"
        assert elapsed < 10, f"50 parallel jobs took {elapsed:.1f}s"
        status = intake.batch_status(reseller, result["batch_id"])
        assert status["status"] == "COMPLETED" and status["counts"]["completed"] == 50
        assert _used("RSL-C1") == 100


class TestBulkApi:
    def test_bulk_endpoint_and_batch_status(self, client):
        _reseller(cap=50)
        headers = {"Authorization": "Bearer " + create_access_token("RSL-C1", "RESELLER_FULL", 1)}
        items = [_payload(f"bulk{i}.test", 2) for i in range(5)]
        items.append({**_payload("broken.test"), "plan": "WEEKLY"})
        items.append(_payload("bulk0.test", 2))

        r = client.post("/api/v1/reseller/provision/bulk", json={"requests": items}, headers=headers)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["accepted"] == 5 and body["rejected"] == 2
        codes = {item["primary_domain"]: item["status_code"] for item in body["results"] if item["index"] >= 5}
        assert codes == {"broken.test": 422, "bulk0.test": 409}

        assert job_executor.wait_until_idle(30)
        status = client.get(f"/api/v1/reseller/provision/batch/{body['batch_id']}", headers=headers).json()
        assert status["status"] == "COMPLETED_WITH_ERRORS"
        assert status["counts"]["completed"] == 5 and status["counts"]["rejected"] == 2
        assert _used("RSL-C1") == 10

    def test_bulk_over_the_limit_is_refused(self, client):
        _reseller(cap=500)
        headers = {"Authorization": "Bearer " + create_access_token("RSL-C1", "RESELLER_FULL", 1)}
        items = [_payload(f"many{i}.test") for i in range(101)]
        r = client.post("/api/v1/reseller/provision/bulk", json={"requests": items}, headers=headers)
        assert r.status_code == 422

    def test_other_partner_cannot_read_a_batch(self, client):
        _reseller("RSL-C1", cap=50)
        _reseller("RSL-C2", cap=50)
        mine = {"Authorization": "Bearer " + create_access_token("RSL-C1", "RESELLER_FULL", 1)}
        theirs = {"Authorization": "Bearer " + create_access_token("RSL-C2", "RESELLER_FULL", 1)}
        batch = client.post("/api/v1/reseller/provision/bulk",
                            json={"requests": [_payload("mine.test")]}, headers=mine).json()["batch_id"]
        assert client.get(f"/api/v1/reseller/provision/batch/{batch}", headers=theirs).status_code == 404


class TestRateLimitKey:
    def test_partners_are_counted_separately(self):
        def req(headers):
            return SimpleNamespace(headers=headers, client=SimpleNamespace(host="169.254.169.126"))

        a = req({"authorization": "Bearer " + create_access_token("RSL-A1", "RESELLER_FULL", 1)})
        b = req({"authorization": "Bearer " + create_access_token("RSL-B1", "RESELLER_FULL", 1)})
        assert _rate_limit_key(a) == "reseller:RSL-A1"
        assert _rate_limit_key(b) == "reseller:RSL-B1"
        assert _rate_limit_key(req({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})) == "ip:203.0.113.9"
