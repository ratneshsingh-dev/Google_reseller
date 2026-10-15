"""Admin dashboard plan breakdown (Flexible vs Annual) and Firestore list pagination. Offline."""

from __future__ import annotations

import pytest

from app.core.auth_middleware import require_admin
from app.core.config import get_settings
from app.dependencies import get_company_repo, get_reseller_repo, get_subscription_repo
from app.models.database import CompanyDocument, SubscriptionDocument
from app.models.reseller_models import ResellerDocument
from app.repositories.firestore_client import FirestoreStore
from app.services.plan_stats import plan_breakdown


def _company(cid: str, reseller_id, status: str = "ACTIVE") -> CompanyDocument:
    return CompanyDocument(company_id=cid, company_name=cid, primary_domain=f"{cid.lower()}.test",
                           reseller_id=reseller_id, status=status)


def _sub(cid: str, plan: str, seats: int = 5) -> SubscriptionDocument:
    return SubscriptionDocument(subscription_id=f"S-{cid}", company_id=cid, sku_id="1010020027",
                                plan=plan, seats=seats)


class TestCounting:
    def test_counts_by_plan_overall_and_per_partner(self):
        companies = [_company("C1", "RSL-A"), _company("C2", "RSL-A"), _company("C3", "RSL-A"),
                     _company("C4", "RSL-A"), _company("C5", "RSL-B"), _company("C6", "RSL-B")]
        subs = [_sub("C1", "FLEXIBLE"), _sub("C2", "FLEXIBLE"), _sub("C3", "FLEXIBLE"),
                _sub("C4", "ANNUAL_MONTHLY_PAY"), _sub("C5", "TRIAL", 1), _sub("C6", "ANNUAL_YEARLY_PAY", 3)]
        overall, per = plan_breakdown(companies, subs)

        assert (overall.flexible, overall.annual_monthly_pay, overall.annual_yearly_pay, overall.trial) == (3, 1, 1, 1)
        assert overall.total == 6 and overall.annual_total == 2 and overall.annual_percent == 33.3
        assert overall.flexible_licences == 15 and overall.annual_licences == 8
        a, b = per["RSL-A"], per["RSL-B"]
        assert (a.flexible, a.annual_total, a.total, a.annual_percent) == (3, 1, 4, 25.0)
        assert (b.trial, b.annual_yearly_pay, b.total) == (1, 1, 2)

    def test_transferred_deleted_and_plan_less_domains_are_not_counted(self):
        companies = [_company("C1", "RSL-A"), _company("C2", "RSL-A", "TRANSFERRED_TO_GOOGLE"),
                     _company("C3", "RSL-A", "DELETED"), _company("C4", "RSL-A")]
        subs = [_sub("C1", "FLEXIBLE"), _sub("C2", "FLEXIBLE"), _sub("C3", "FLEXIBLE")]   # C4 has no subscription
        overall, _ = plan_breakdown(companies, subs)
        assert overall.total == 1 and overall.flexible == 1

    def test_suspended_domains_still_count(self):
        overall, _ = plan_breakdown([_company("C1", "RSL-A", "SUSPENDED")], [_sub("C1", "ANNUAL_MONTHLY_PAY")])
        assert overall.annual_monthly_pay == 1

    def test_empty(self):
        overall, per = plan_breakdown([], [])
        assert overall.total == 0 and overall.annual_percent == 0.0 and per == {}

    def test_domains_without_a_partner_are_counted_overall_only_under_none(self):
        overall, per = plan_breakdown([_company("C1", None)], [_sub("C1", "FLEXIBLE")])
        assert overall.flexible == 1 and "(none)" in per


@pytest.fixture
def admin_client(client):
    from app.main import app
    app.dependency_overrides[require_admin] = lambda: {"email": "boss@econz.test"}
    yield client
    app.dependency_overrides.pop(require_admin, None)


class TestEndpoint:
    def _seed(self):
        get_reseller_repo().create(ResellerDocument(
            reseller_id="RSL-A", company_name="Partner A", contact_email="a@x.test", contact_name="A",
            max_licence_cap=100, client_secret_hash="unused"))
        for i in range(1, 5):
            get_company_repo().create(_company(f"C{i}", "RSL-A"))
            get_subscription_repo().create(_sub(f"C{i}", "ANNUAL_MONTHLY_PAY" if i == 4 else "FLEXIBLE"))

    def test_summary_includes_the_breakdown(self, admin_client):
        self._seed()
        body = admin_client.get("/api/v1/admin/dashboard/summary").json()
        assert body["plan_breakdown"]["flexible"] == 3
        assert body["plan_breakdown"]["annual_monthly_pay"] == 1
        assert body["plan_breakdown"]["annual_percent"] == 25.0
        assert body["plan_breakdown_by_reseller"]["RSL-A"]["total"] == 4
        assert body["plan_mix_enabled"] is False and body["plan_mix_target_percent"] == 0.0

    def test_summary_shows_the_target_when_the_mix_is_on(self, admin_client, monkeypatch):
        monkeypatch.setenv("PLAN_MIX_ENABLED", "true")
        get_settings.cache_clear()
        body = admin_client.get("/api/v1/admin/dashboard/summary").json()
        assert body["plan_mix_enabled"] is True and body["plan_mix_target_percent"] == 25.0

    def test_summary_requires_admin(self, client):
        assert client.get("/api/v1/admin/dashboard/summary").status_code == 401


class TestListAllPagination:
    """Firestore returns one page at a time; list_all must follow nextPageToken (it used to stop
    after the first 100 documents, so counts and lists silently lost the rest)."""

    @staticmethod
    def _store(pages):
        store = FirestoreStore.__new__(FirestoreStore)
        store._base_url = "https://example.invalid/documents"
        store._get_headers = lambda: {}
        calls = []

        class Resp:
            def __init__(self, status, body):
                self.status_code, self._body, self.text = status, body, ""

            def json(self):
                return self._body

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

        class Http:
            def get(self, url, headers, params, timeout):
                calls.append(dict(params))
                return pages[len(calls) - 1]

        store._http = lambda: Http()
        return store, calls, Resp

    @staticmethod
    def _doc(n):
        return {"name": f"projects/p/databases/d/documents/c/{n}", "fields": {"n": {"integerValue": str(n)}}}

    def test_follows_every_page(self):
        store, calls, Resp = self._store([None, None, None])
        pages = [Resp(200, {"documents": [self._doc(i) for i in range(0, 300)], "nextPageToken": "t1"}),
                 Resp(200, {"documents": [self._doc(i) for i in range(300, 600)], "nextPageToken": "t2"}),
                 Resp(200, {"documents": [self._doc(i) for i in range(600, 650)]})]
        store, calls, _ = self._store(pages)
        docs = store.list_all("c")
        assert len(docs) == 650 and [d["n"] for d in docs][-1] == 649
        assert [c.get("pageToken") for c in calls] == [None, "t1", "t2"]
        assert all(c["pageSize"] == 300 for c in calls)

    def test_a_collection_that_fits_one_page_makes_one_call(self):
        _, _, Resp = self._store([])
        store, calls, _ = self._store([Resp(200, {"documents": [self._doc(1)]})])
        assert len(store.list_all("c")) == 1 and len(calls) == 1

    def test_an_empty_collection(self):
        _, _, Resp = self._store([])
        store, _, _ = self._store([Resp(200, {})])
        assert store.list_all("c") == []

    def test_an_error_half_way_is_not_hidden(self):
        _, _, Resp = self._store([])
        pages = [Resp(200, {"documents": [self._doc(1)], "nextPageToken": "t1"}), Resp(500, {})]
        store, _, _ = self._store(pages)
        with pytest.raises(RuntimeError):
            store.list_all("c")

    def test_first_page_permission_error_returns_empty_as_before(self):
        _, _, Resp = self._store([])
        store, _, _ = self._store([Resp(403, {})])
        assert store.list_all("c") == []


class TestDashboardFilesAreNotStale:
    """After a release browsers must pick up the new dashboard script/CSS (they used to keep
    old copies and showed a new page driven by old code)."""

    @pytest.mark.parametrize("path", [
        "/static/reseller-admin.html", "/static/js/reseller-admin.js", "/static/css/reseller-admin.css",
    ])
    def test_static_files_must_be_revalidated(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers.get("etag")

    def test_unchanged_file_costs_only_a_304(self, client):
        first = client.get("/static/js/reseller-admin.js")
        again = client.get("/static/js/reseller-admin.js", headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304
