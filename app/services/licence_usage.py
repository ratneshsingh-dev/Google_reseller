"""Day-by-day licence usage for the admin dashboard (read-only).

Built from records that already exist, nothing new is written:
  - provisioning jobs   -> licences added to NEW domains, and failed jobs
  - audit log           -> later licence changes (+/-) and domains transferred to Google
Days are in India time (IST, UTC+5:30) so a day matches what the team sees on the clock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
_DONE = {"COMPLETED", "PARTIAL_FAILURE"}

_FIELDS = ("domains_created", "licences_new", "licences_added_later", "licences_removed",
           "licences_released", "failed_jobs")


def _day(moment: Optional[datetime]) -> Optional[date]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).date()


def _empty(day: date) -> Dict[str, Any]:
    return {"date": day.isoformat(), **{f: 0 for f in _FIELDS}}


def licence_usage(
    jobs: Iterable[Any],
    audit: Iterable[Any],
    days: int = 30,
    reseller_id: Optional[str] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Daily rows (newest first, every day in the range present) plus totals and a per-partner split."""
    days = max(1, min(int(days), 365))
    today = today or datetime.now(IST).date()
    start = today - timedelta(days=days - 1)
    rows: Dict[date, Dict[str, Any]] = {start + timedelta(days=i): _empty(start + timedelta(days=i)) for i in range(days)}
    partners: Dict[str, Dict[str, int]] = {}

    def bucket(rid: str) -> Dict[str, int]:
        return partners.setdefault(rid or "(none)", {f: 0 for f in _FIELDS})

    def add(day: Optional[date], rid: str, field: str, amount: int) -> None:
        if day not in rows or not amount:
            return
        rows[day][field] += amount
        bucket(rid)[field] += amount

    for job in jobs:
        if reseller_id and job.reseller_id != reseller_id:
            continue
        day = _day(job.created_at)
        rid = job.reseller_id or ""
        if job.status in _DONE:
            add(day, rid, "domains_created", 1)
            add(day, rid, "licences_new", job.licences_added or job.licensed_seats or 0)
        elif job.status == "FAILED":
            add(day, rid, "failed_jobs", 1)

    for entry in audit:
        if reseller_id and entry.reseller_id != reseller_id:
            continue
        if entry.status != "SUCCESS":
            continue
        day = _day(entry.timestamp)
        delta = entry.licences_requested or 0
        if entry.action == "LICENCES_CHANGED":
            add(day, entry.reseller_id, "licences_added_later" if delta > 0 else "licences_removed", abs(delta))
        elif entry.action == "DOMAIN_TRANSFER":
            add(day, entry.reseller_id, "licences_released", abs(delta))

    def net(r: Dict[str, int]) -> int:
        return r["licences_new"] + r["licences_added_later"] - r["licences_removed"] - r["licences_released"]

    out: List[Dict[str, Any]] = []
    totals = {f: 0 for f in _FIELDS}
    for day in sorted(rows, reverse=True):
        row = rows[day]
        row["net_change"] = net(row)
        for f in _FIELDS:
            totals[f] += row[f]
        out.append(row)
    totals["net_change"] = net(totals)

    return {
        "from": start.isoformat(),
        "to": today.isoformat(),
        "days": days,
        "timezone": "IST (UTC+5:30)",
        "reseller_id": reseller_id,
        "totals": totals,
        "daily": out,
        "by_partner": [{"reseller_id": rid, **b, "net_change": net(b)} for rid, b in
                       sorted(partners.items(), key=lambda kv: -net(kv[1]))],
    }
