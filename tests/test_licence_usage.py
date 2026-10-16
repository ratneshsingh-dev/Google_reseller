"""Admin licence-usage-by-date report (read-only). Offline."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.auth_middleware import require_admin
from app.dependencies import get_audit_repo, get_job_repo
from app.models.database import ProvisioningJobDocument
from app.models.reseller_models import AuditLogDocument
from app.services.licence_usage import IST, licence_usage

TODAY = date(2026, 10, 16)


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST).astimezone(timezone.utc)


def _job(jid, day, status="COMPLETED", added=1, rid="RSL-A"):
    return ProvisioningJobDocument(job_id=jid, primary_domain=f"{jid}.test", status=status, reseller_id=rid,
                                   licences_added=added, created_at=_at(day))


def _log(lid, day, action, delta, rid="RSL-A", status="SUCCESS"):
    return AuditLogDocument(log_id=lid, reseller_id=rid, action=action, licences_requested=delta,
                            status=status, timestamp=_at(day))


class TestReport:
    def test_counts_per_day_and_net(self):
        d1, d2 = TODAY, TODAY - timedelta(days=1)
        jobs = [_job("J1", d1, added=3), _job("J2", d1, added=1), _job("J3", d2, added=5),
                _job("J4", d1, status="FAILED", added=0)]
        audit = [_log("L1", d1, "LICENCES_CHANGED", 2), _log("L2", d1, "LICENCES_CHANGED", -1),
                 _log("L3", d2, "DOMAIN_TRANSFER", -5)]
        out = licence_usage(jobs, audit, days=7, today=TODAY)
        today_row, yesterday = out["daily"][0], out["daily"][1]
        assert today_row["date"] == "2026-10-16"
        assert (today_row["domains_created"], today_row["licences_new"], today_row["failed_jobs"]) == (2, 4, 1)
        assert (today_row["licences_added_later"], today_row["licences_removed"]) == (2, 1)
        assert today_row["net_change"] == 4 + 2 - 1
        assert (yesterday["licences_new"], yesterday["licences_released"], yesterday["net_change"]) == (5, 5, 0)
        assert out["totals"]["net_change"] == 5 and len(out["daily"]) == 7

    def test_every_day_in_range_is_present_and_old_records_are_left_out(self):
        out = licence_usage([_job("J1", TODAY - timedelta(days=40))], [], days=30, today=TODAY)
        assert len(out["daily"]) == 30 and out["totals"]["domains_created"] == 0

    def test_partner_filter_and_split(self):
        jobs = [_job("J1", TODAY, added=2, rid="RSL-A"), _job("J2", TODAY, added=7, rid="RSL-B")]
        assert licence_usage(jobs, [], 7, today=TODAY)["by_partner"][0]["reseller_id"] == "RSL-B"
        only_a = licence_usage(jobs, [], 7, reseller_id="RSL-A", today=TODAY)
        assert only_a["totals"]["licences_new"] == 2 and [p["reseller_id"] for p in only_a["by_partner"]] == ["RSL-A"]

    def test_failed_and_denied_entries_do_not_count_as_usage(self):
        jobs = [_job("J1", TODAY, status="PENDING", added=0)]
        audit = [_log("L1", TODAY, "LICENCES_CHANGED", 3, status="FAILED"),
                 _log("L2", TODAY, "DOMAIN_TRANSFER", -4, status="FAILED")]
        assert licence_usage(jobs, audit, 7, today=TODAY)["totals"]["net_change"] == 0

    def test_day_is_in_india_time(self):
        late = datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc)     # 01:30 IST on the 16th
        job = ProvisioningJobDocument(job_id="J1", status="COMPLETED", reseller_id="RSL-A", licences_added=1, created_at=late)
        assert licence_usage([job], [], 3, today=TODAY)["daily"][0]["domains_created"] == 1

    def test_days_are_clamped(self):
        assert len(licence_usage([], [], days=9999, today=TODAY)["daily"]) == 365
        assert len(licence_usage([], [], days=0, today=TODAY)["daily"]) == 1


@pytest.fixture
def admin_client(client):
    from app.main import app
    app.dependency_overrides[require_admin] = lambda: {"email": "boss@econz.test"}
    yield client
    app.dependency_overrides.pop(require_admin, None)


class TestEndpoint:
    def test_requires_admin(self, client):
        assert client.get("/api/v1/admin/licence-usage").status_code == 401

    def test_returns_the_report(self, admin_client):
        today = datetime.now(IST).date()
        get_job_repo().create_job(_job("JOB-T1", today, added=4))
        get_audit_repo().create(_log("LOG-T1", today, "LICENCES_CHANGED", 2))
        body = admin_client.get("/api/v1/admin/licence-usage?days=7").json()
        assert body["daily"][0]["licences_new"] == 4 and body["daily"][0]["licences_added_later"] == 2
        assert body["totals"]["net_change"] == 6

    def test_rejects_a_bad_range(self, admin_client):
        assert admin_client.get("/api/v1/admin/licence-usage?days=0").status_code == 422
        assert admin_client.get("/api/v1/admin/licence-usage?days=1000").status_code == 422

    def test_page_is_served_and_linked(self, client):
        assert client.get("/static/licence-usage.html").status_code == 200
        assert "licence-usage.html" in client.get("/static/reseller-admin.html").text
