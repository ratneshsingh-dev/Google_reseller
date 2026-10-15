"""Group commit on hot documents + the "busy" safety net. Offline, with simulated network delay
so contention behaves like it does against Firestore."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import job_executor
from app.core.jwt_service import create_access_token
from app.dependencies import (
    get_job_repo,
    get_lock_repo,
    get_provisioning_intake,
    get_reseller_repo,
)
from app.models.requests import ProvisioningRequest
from app.models.reseller_models import ResellerDocument
from app.repositories import batching
from app.repositories.batching import ContentionError, update_document
from app.repositories.coordination_repository import PlanMixRepository
from app.repositories.firestore_client import InMemoryStore
from app.repositories.reseller_repository import QuotaExceededError
from app.services.provisioning_intake import IntakeError


class SlowStore(InMemoryStore):
    """In-memory store that takes `delay` seconds per read/write and counts writes."""

    def __init__(self, delay: float = 0.03) -> None:
        super().__init__()
        self.delay = delay
        self.writes = 0
        self.write_log = []
        self._count_lock = threading.Lock()

    def get_versioned(self, collection, doc_id):
        time.sleep(self.delay)
        return super().get_versioned(collection, doc_id)

    def set_if_version(self, collection, doc_id, data, version):
        time.sleep(self.delay)
        ok = super().set_if_version(collection, doc_id, data, version)
        with self._count_lock:
            self.writes += 1
            self.write_log.append((collection, doc_id, ok))
        return ok


def _seed(store, used=0, cap=1000):
    store.set("hot", "doc", {"used": used, "cap": cap})


def _add(store, n=1, cap_check=False):
    def op(data):
        if cap_check and data["used"] + n > data["cap"]:
            raise QuotaExceededError(n, data["used"], data["cap"])
        data["used"] += n
        return data["used"]
    return update_document(store, "hot", "doc", op)


def _parallel(fn, count):
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(lambda i: fn(i), range(count)))


class TestGroupCommit:
    def test_30_parallel_updates_share_a_handful_of_writes(self):
        store = SlowStore(delay=0.03)
        _seed(store)
        started = time.time()
        results = _parallel(lambda i: _add(store), 30)
        elapsed = time.time() - started
        assert store.get("hot", "doc")["used"] == 30
        assert sorted(results) == list(range(1, 31)), "each caller must see its own post-update value"
        assert store.writes <= 6, f"30 updates used {store.writes} writes"
        assert elapsed < 1.5, f"took {elapsed:.2f}s"

    def test_without_it_the_same_load_needs_many_more_writes(self):
        """Baseline, so the test above means something: one compare-and-swap per caller."""
        store = SlowStore(delay=0.03)
        _seed(store)

        def plain(_):
            for attempt in range(200):
                data, version = store.get_versioned("hot", "doc")
                data["used"] += 1
                if store.set_if_version("hot", "doc", data, version):
                    return
                time.sleep(0.005 * (attempt + 1) % 0.05)

        _parallel(plain, 30)
        assert store.get("hot", "doc")["used"] == 30
        assert store.writes > 40

    def test_cap_is_never_exceeded_inside_a_batch(self):
        store = SlowStore(delay=0.02)
        _seed(store, cap=10)

        def attempt(_):
            try:
                return _add(store, cap_check=True)
            except QuotaExceededError:
                return "full"

        results = _parallel(attempt, 25)
        assert store.get("hot", "doc")["used"] == 10
        assert [r for r in results if r != "full"].__len__() == 10 and results.count("full") == 15

    def test_one_rejected_update_does_not_affect_the_others(self):
        store = SlowStore(delay=0.02)
        _seed(store)

        def op_for(i):
            def op(data):
                if i % 5 == 0:
                    raise ValueError(f"caller {i} refused")
                data["used"] += 1
                return data["used"]
            return op

        def run(i):
            try:
                return update_document(store, "hot", "doc", op_for(i))
            except ValueError:
                return "refused"

        results = _parallel(run, 20)
        assert results.count("refused") == 4
        assert store.get("hot", "doc")["used"] == 16

    def test_other_servers_writing_at_the_same_time_cause_retries_not_lost_updates(self):
        store = SlowStore(delay=0.01)
        _seed(store)
        stop = threading.Event()

        def other_server():
            while not stop.is_set():          # a second server changing the record now and then
                store.update("hot", "doc", {"touched": time.time()})
                time.sleep(0.04)

        rival = threading.Thread(target=other_server, daemon=True)
        rival.start()
        try:
            _parallel(lambda i: _add(store), 40)
        finally:
            stop.set()
            rival.join()
        assert store.get("hot", "doc")["used"] == 40

    def test_different_documents_do_not_wait_for_each_other(self):
        store = SlowStore(delay=0.02)
        store.set("hot", "a", {"used": 0})
        store.set("hot", "b", {"used": 0})

        def add_to(doc):
            return update_document(store, "hot", doc, lambda d: d.update(used=d["used"] + 1) or d["used"])

        _parallel(lambda i: add_to("a" if i % 2 else "b"), 20)
        assert store.get("hot", "a")["used"] == 10 and store.get("hot", "b")["used"] == 10

    def test_a_missing_document_is_created_when_a_default_is_given_otherwise_keyerror(self):
        store = SlowStore(delay=0)
        with pytest.raises(KeyError):
            update_document(store, "hot", "nope", lambda d: d)
        value = update_document(store, "hot", "new", lambda d: d.update(n=d["n"] + 1) or d["n"], missing={"n": 0})
        assert value == 1 and store.get("hot", "new")["n"] == 1

    def test_plan_slots_are_unique_and_consecutive_under_load(self):
        store = SlowStore(delay=0.02)
        repo = PlanMixRepository(store)
        firsts = _parallel(lambda i: repo.reserve("RSL-X", 1), 40)
        assert sorted(firsts) == list(range(40))
        assert repo.reserve("RSL-X", 5) == 40


class TestFailuresNeverStrandAnyone:
    def test_when_every_save_loses_all_callers_get_contention_error_and_the_queue_recovers(self, monkeypatch):
        store = SlowStore(delay=0)
        _seed(store)
        monkeypatch.setattr(batching, "MAX_ATTEMPTS", 3)
        monkeypatch.setattr(batching.time, "sleep", lambda s: None)
        real = store.set_if_version
        monkeypatch.setattr(store, "set_if_version", lambda *a, **k: False)

        def attempt(_):
            try:
                return _add(store)
            except ContentionError:
                return "busy"

        assert _parallel(attempt, 10) == ["busy"] * 10
        monkeypatch.setattr(store, "set_if_version", real)
        assert _add(store) == 1, "the document must be usable again afterwards"

    def test_a_store_error_reaches_every_waiting_caller_and_the_queue_recovers(self, monkeypatch):
        store = SlowStore(delay=0.01)
        _seed(store)
        real = store.get_versioned
        state = {"fail": True}

        def flaky(*a, **k):
            if state["fail"]:
                time.sleep(0.05)
                raise ConnectionError("firestore unreachable")
            return real(*a, **k)

        monkeypatch.setattr(store, "get_versioned", flaky)

        def attempt(_):
            try:
                return _add(store)
            except ConnectionError:
                return "error"

        assert _parallel(attempt, 8) == ["error"] * 8
        state["fail"] = False
        assert _add(store) == 1


def _reseller(cap=1000) -> ResellerDocument:
    doc = ResellerDocument(reseller_id="RSL-B1", company_name="B1", contact_email="b1@partner.test",
                           contact_name="B1", max_licence_cap=cap, client_secret_hash="unused")
    get_reseller_repo().create(doc)
    return doc


def _request(domain: str, seats: int = 1) -> ProvisioningRequest:
    return ProvisioningRequest(
        company_name=domain, primary_domain=domain, alternate_email="contact@partner.test",
        contact_name="Contact", plan="TRIAL", sku_id="1010020027", license_count=seats,
        postal_address=dict(address_line1="1 Road", locality="Kochi", region="KL",
                            postal_code="682001", country_code="IN"),
        initiated_by_email="ops@partner.test", econz_notification_email="ops@partner.test",
        admin_first_name="Ada", admin_last_name="Admin",
    )


@pytest.fixture
def busy_quota(client, monkeypatch):
    """The quota record cannot be saved (every attempt loses to other writers)."""
    def contended(*args, **kwargs):
        raise ContentionError("too much contention")
    monkeypatch.setattr(type(get_reseller_repo()), "adjust_licences_used", contended)


class TestBusySafetyNet:
    def test_busy_quota_releases_the_lock_and_idempotency_key_and_says_503(self, busy_quota, monkeypatch):
        intake = get_provisioning_intake()
        with pytest.raises(IntakeError) as exc:
            intake.accept(_reseller(), _request("busy.test"), idempotency_key="k-busy")
        assert exc.value.status_code == 503 and "retry" in exc.value.detail["detail"]
        assert get_lock_repo().holder("busy.test") is None, "the domain must not stay locked"

        monkeypatch.undo()                      # the contention clears
        result = intake.accept(get_reseller_repo().get_by_id("RSL-B1"), _request("busy.test"), idempotency_key="k-busy")
        assert result["status"] == "PENDING", "retrying with the same key and domain must work straight away"
        assert job_executor.wait_until_idle(30)
        assert get_reseller_repo().get_by_id("RSL-B1").licences_used == 1

    def test_http_response_is_503_with_retry_after(self, busy_quota):
        _reseller()
        headers = {"Authorization": "Bearer " + create_access_token("RSL-B1", "RESELLER_FULL", 1)}
        from app.main import app
        from fastapi.testclient import TestClient
        r = TestClient(app).post("/api/v1/reseller/provision", json=_request("http503.test").model_dump(mode="json"),
                                 headers=headers)
        assert r.status_code == 503 and r.headers["retry-after"] == "5"
        assert r.json()["detail"]["error"] == "Service busy"

    def test_busy_plan_counter_refunds_quota_and_releases_the_lock(self, client, monkeypatch):
        monkeypatch.setenv("PLAN_MIX_ENABLED", "true")
        from app.core.config import get_settings
        get_settings.cache_clear()

        def contended(*a, **k):
            raise ContentionError("busy")

        monkeypatch.setattr(PlanMixRepository, "reserve", contended)
        intake = get_provisioning_intake()
        with pytest.raises(IntakeError) as exc:
            intake.accept(_reseller(), _request("planbusy.test", seats=3))
        assert exc.value.status_code == 503
        assert get_reseller_repo().get_by_id("RSL-B1").licences_used == 0
        assert get_lock_repo().holder("planbusy.test") is None
        get_settings.cache_clear()

    def test_busy_bulk_plan_counter_returns_503_and_frees_the_key(self, client, monkeypatch):
        monkeypatch.setenv("PLAN_MIX_ENABLED", "true")
        from app.core.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setattr(PlanMixRepository, "reserve",
                            lambda *a, **k: (_ for _ in ()).throw(ContentionError("busy")))
        intake = get_provisioning_intake()
        items = [_request(f"bb{i}.test").model_dump(mode="json") for i in range(3)]
        with pytest.raises(IntakeError) as exc:
            intake.accept_bulk(_reseller(), items, idempotency_key="bulk-k")
        assert exc.value.status_code == 503
        monkeypatch.undo()
        monkeypatch.setenv("PLAN_MIX_ENABLED", "true")
        get_settings.cache_clear()
        assert intake.accept_bulk(get_reseller_repo().get_by_id("RSL-B1"), items, idempotency_key="bulk-k")["accepted"] == 3
        get_settings.cache_clear()


class TestAcceptThirtyInParallel:
    def test_30_parallel_accepts_use_few_quota_writes_and_charge_exactly_30(self, client, monkeypatch):
        """The case that was slow on dev: 30 simultaneous requests for one partner."""
        from app.repositories import firestore_client
        store = SlowStore(delay=0.03)
        monkeypatch.setattr(firestore_client, "_store_instance", store)
        from app.dependencies import reset_dependencies
        reset_dependencies()
        monkeypatch.setattr(job_executor, "submit", lambda make, name="job": None)
        from app.core import job_dispatcher
        monkeypatch.setattr(job_dispatcher, "dispatch", lambda job_id, run_locally: None)

        reseller = ResellerDocument(reseller_id="RSL-P30", company_name="P30", contact_email="p@x.test",
                                    contact_name="P", max_licence_cap=100, client_secret_hash="x")
        get_reseller_repo().create(reseller)
        intake = get_provisioning_intake()
        store.writes = 0
        store.write_log.clear()

        started = time.time()
        results = _parallel(lambda i: intake.accept(reseller, _request(f"p30-{i}.test")), 30)
        elapsed = time.time() - started

        assert {r["status"] for r in results} == {"PENDING"}
        assert get_reseller_repo().get_by_id("RSL-P30").licences_used == 30
        quota_writes = [w for w in store.write_log if w[0] == "resellers"]
        assert len(quota_writes) <= 8, f"quota record written {len(quota_writes)} times for 30 requests"
        assert elapsed < 3.0, f"30 parallel accepts took {elapsed:.1f}s"
        assert sorted(r["quota_summary"]["total_licences_used"] for r in results) == list(range(1, 31))
