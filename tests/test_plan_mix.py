"""Plan mix: with PLAN_MIX_ENABLED the server decides each domain's plan (every 4th accepted
domain per partner is ANNUAL_MONTHLY_PAY, the rest FLEXIBLE), ignoring the client's plan,
with at most 5 licences per domain. Offline: mock Google, in-memory store."""

from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import job_dispatcher, job_executor
from app.core.config import get_settings
from app.core.jwt_service import create_access_token
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

ANNUAL = "ANNUAL_MONTHLY_PAY"
FLEX = "FLEXIBLE"


def _reseller(reseller_id: str = "RSL-M1", cap: int = 5000) -> ResellerDocument:
    doc = ResellerDocument(reseller_id=reseller_id, company_name=reseller_id,
                           contact_email=f"{reseller_id.lower()}@partner.test", contact_name=reseller_id,
                           max_licence_cap=cap, client_secret_hash="unused")
    get_reseller_repo().create(doc)
    return doc


def _payload(domain: str, seats: int = 5, plan=None) -> dict:
    body = dict(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", sku_id="1010020027", license_count=seats,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )
    if plan:
        body["plan"] = plan
    return body


def _request(domain: str, seats: int = 5, plan=None) -> ProvisioningRequest:
    return ProvisioningRequest(**_payload(domain, seats, plan))


@pytest.fixture
def mix_on(client, monkeypatch):
    monkeypatch.setenv("PLAN_MIX_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def queued(monkeypatch):
    """Accept without running jobs, so large counts stay fast."""
    monkeypatch.setattr(job_dispatcher, "dispatch", lambda job_id, run_locally: None)
    return get_provisioning_intake()


def _plans(results) -> list:
    return [r["plan"] for r in results]


class TestMixOff:
    def test_plan_is_required_and_honoured(self, client, queued):
        reseller = _reseller()
        with pytest.raises(IntakeError) as exc:
            queued.accept(reseller, _request("noplan.test"))
        assert exc.value.status_code == 422 and "plan is required" in str(exc.value.detail)
        assert queued.accept(reseller, _request("trial.test", plan="TRIAL"))["plan"] == "TRIAL"

    def test_no_licence_cap_applies(self, client, queued):
        assert queued.accept(_reseller(), _request("big.test", seats=50, plan="FLEXIBLE"))["plan"] == "FLEXIBLE"


class TestEveryFourthIsAnnual:
    def test_client_plan_is_ignored(self, mix_on, queued):
        reseller = _reseller()
        results = [queued.accept(reseller, _request(f"d{i}.test", plan="TRIAL" if i % 2 else "ANNUAL_YEARLY_PAY"))
                   for i in range(1, 9)]
        assert _plans(results) == [FLEX, FLEX, FLEX, ANNUAL, FLEX, FLEX, FLEX, ANNUAL]

    def test_plan_may_be_left_out(self, mix_on, queued):
        assert queued.accept(_reseller(), _request("omit.test"))["plan"] == FLEX

    def test_exactly_25_of_100_one_after_another(self, mix_on, queued):
        reseller = _reseller()
        results = [queued.accept(reseller, _request(f"seq{i}.test")) for i in range(100)]
        assert Counter(_plans(results)) == {ANNUAL: 25, FLEX: 75}

    def test_exactly_25_of_100_parallel_single_calls(self, mix_on, queued):
        reseller = _reseller()
        with ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(lambda i: queued.accept(reseller, _request(f"par{i}.test")), range(100)))
        assert Counter(_plans(results)) == {ANNUAL: 25, FLEX: 75}

    def test_partners_have_separate_counters(self, mix_on, queued):
        a, b = _reseller("RSL-A"), _reseller("RSL-B")
        for i in range(3):
            queued.accept(a, _request(f"a{i}.test"))
        assert queued.accept(b, _request("b0.test"))["plan"] == FLEX
        assert queued.accept(a, _request("a3.test"))["plan"] == ANNUAL

    def test_a_refused_request_does_not_use_a_slot(self, mix_on, queued):
        reseller = _reseller()
        plans = []
        for i in (1, 2, 3):
            plans.append(queued.accept(reseller, _request(f"s{i}.test"))["plan"])
        with pytest.raises(IntakeError) as busy:
            queued.accept(reseller, _request("s1.test"))
        assert busy.value.status_code == 409
        plans.append(queued.accept(reseller, _request("s4.test"))["plan"])
        assert plans == [FLEX, FLEX, FLEX, ANNUAL]

    def test_an_idempotent_replay_does_not_use_a_slot(self, mix_on, queued):
        reseller = _reseller()
        first = queued.accept(reseller, _request("i1.test"), idempotency_key="k1")
        again = queued.accept(reseller, _request("i1.test"), idempotency_key="k1")
        assert again["job_id"] == first["job_id"]
        plans = [queued.accept(reseller, _request(f"i{n}.test"))["plan"] for n in (2, 3, 4)]
        assert plans == [FLEX, FLEX, ANNUAL]


class TestBulk:
    def _bulk(self, intake, reseller, n, **kw):
        return intake.accept_bulk(reseller, [_payload(f"b{kw.get('tag', '')}{i}.test") for i in range(n)])

    def test_100_in_one_bulk_call_gives_exactly_25_annual(self, mix_on, queued):
        result = self._bulk(queued, _reseller(), 100)
        assert result["accepted"] == 100
        assert Counter(r["plan"] for r in result["results"]) == {ANNUAL: 25, FLEX: 75}

    def test_still_exactly_25_when_the_counter_is_mid_cycle(self, mix_on, queued):
        reseller = _reseller()
        for i in range(3):
            queued.accept(reseller, _request(f"warm{i}.test"))
        result = self._bulk(queued, reseller, 100)
        assert Counter(r["plan"] for r in result["results"]) == {ANNUAL: 25, FLEX: 75}

    def test_invalid_items_do_not_use_slots(self, mix_on, queued):
        reseller = _reseller()
        items = [_payload(f"v{i}.test") for i in range(8)]
        items[1]["license_count"] = 6          # over the cap
        items[5]["primary_domain"] = "not a domain"
        result = queued.accept_bulk(reseller, items)
        assert result["accepted"] == 6 and result["rejected"] == 2
        by_index = {r["index"]: r for r in result["results"]}
        assert by_index[1]["status_code"] == 422 and by_index[5]["status_code"] == 422
        accepted_plans = [by_index[i]["plan"] for i in (0, 2, 3, 4, 6, 7)]
        assert accepted_plans == [FLEX, FLEX, FLEX, ANNUAL, FLEX, FLEX]

    def test_bulk_and_single_calls_share_one_counter(self, mix_on, queued):
        reseller = _reseller()
        queued.accept_bulk(reseller, [_payload(f"x{i}.test") for i in range(6)])
        assert queued.accept(reseller, _request("next.test"))["plan"] == FLEX      # slot 7
        assert queued.accept(reseller, _request("next2.test"))["plan"] == ANNUAL   # slot 8


class TestLicenceCap:
    def test_more_than_5_licences_is_refused(self, mix_on, queued):
        with pytest.raises(IntakeError) as exc:
            queued.accept(_reseller(), _request("six.test", seats=6))
        assert exc.value.status_code == 422 and "at most 5" in exc.value.detail["detail"]

    def test_5_licences_is_allowed(self, mix_on, queued):
        assert queued.accept(_reseller(), _request("five.test", seats=5))["status"] == "PENDING"

    def test_increasing_an_existing_domain_past_5_is_refused(self, mix_on, client):
        reseller = _reseller()
        get_provisioning_intake().accept(reseller, _request("grow.test", seats=3))
        assert job_executor.wait_until_idle(30)
        service = get_domain_service()
        assert asyncio.run(service.change_licences("grow.test", get_reseller_repo().get_by_id("RSL-M1"), 5))["licences"] == 5
        with pytest.raises(DomainActionError) as exc:
            asyncio.run(service.change_licences("grow.test", get_reseller_repo().get_by_id("RSL-M1"), 6))
        assert exc.value.status_code == 400


class TestEndToEnd:
    def test_jobs_create_the_assigned_plan_with_5_licences(self, mix_on, client):
        reseller = _reseller()
        intake = get_provisioning_intake()
        results = [intake.accept(reseller, _request(f"e2e{i}.test", plan="TRIAL")) for i in range(4)]
        assert job_executor.wait_until_idle(60)

        jobs = get_job_repo()
        assert [jobs.get_job(r["job_id"]).status for r in results] == ["COMPLETED"] * 4
        mock = get_reseller_service()
        at_google = {s.customer_domain: (s.plan.plan_name, s.seats.number_of_seats)
                     for subs in mock._subscriptions.values() for s in subs.values()}
        assert [at_google[f"e2e{i}.test"] for i in range(4)] == [(FLEX, 5)] * 3 + [(ANNUAL, 5)]
        assert get_reseller_repo().get_by_id("RSL-M1").licences_used == 20

    def test_http_response_shows_the_plan_and_the_plan_field_is_optional(self, mix_on, client):
        _reseller()
        headers = {"Authorization": "Bearer " + create_access_token("RSL-M1", "RESELLER_FULL", 1)}
        r = client.post("/api/v1/reseller/provision", json=_payload("http.test"), headers=headers)
        assert r.status_code == 202 and r.json()["plan"] == FLEX
        assert job_executor.wait_until_idle(30)
        status = client.get(f"/api/v1/reseller/provision/{r.json()['job_id']}", headers=headers).json()
        assert status["plan"] == FLEX and status["status"] == "COMPLETED"

    def test_an_annual_domain_cannot_have_licences_reduced(self, mix_on, client):
        reseller = _reseller()
        intake = get_provisioning_intake()
        [intake.accept(reseller, _request(f"red{i}.test", seats=5)) for i in range(4)]
        assert job_executor.wait_until_idle(60)
        service = get_domain_service()
        with pytest.raises(DomainActionError) as exc:
            asyncio.run(service.change_licences("red3.test", get_reseller_repo().get_by_id("RSL-M1"), 3))
        assert exc.value.status_code == 400 and "FLEXIBLE" in str(exc.value.detail)
        assert asyncio.run(service.change_licences("red0.test", get_reseller_repo().get_by_id("RSL-M1"), 3))["licences"] == 3


class TestGoogleRequestBody:
    """What is actually sent to Google for each plan."""

    @pytest.mark.parametrize("plan,expected", [
        (ANNUAL, {"numberOfSeats": 5}),
        (FLEX, {"maximumNumberOfSeats": 5}),
    ])
    def test_seats_field_per_plan(self, plan, expected):
        from app.adapters.google.google_reseller import GoogleResellerService
        from app.models.google_api import (GoogleCreateSubscriptionRequest, GoogleSubscriptionPlan,
                                           GoogleSubscriptionSeats)
        svc = GoogleResellerService.__new__(GoogleResellerService)
        sent = []

        class Insert:
            def __init__(self, body):
                self.body = body

            def execute(self):
                return {"customerId": "C1", "subscriptionId": "S1", "skuId": "1010020027",
                        "plan": {"planName": plan}, "seats": self.body["seats"]}

        class Subs:
            def insert(self, customerId, body):
                sent.append(body)
                return Insert(body)

        class Svc:
            def subscriptions(self):
                return Subs()

        svc._execute_with_retry = lambda fn: fn(Svc())
        request = GoogleCreateSubscriptionRequest(
            skuId="1010020027", plan=GoogleSubscriptionPlan(planName=plan),
            seats=GoogleSubscriptionSeats(numberOfSeats=5, licensedNumberOfSeats=5))
        asyncio.run(svc.create_subscription("C1", request))
        assert sent[0]["seats"] == expected and sent[0]["plan"] == {"planName": plan}
