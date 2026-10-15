"""How many customer domains are on each plan (for the admin dashboard)."""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from app.models.database import CompanyDocument, SubscriptionDocument
from app.models.reseller_models import PlanBreakdown

# Domains handed back to Google or deleted are no longer managed, so they are not counted.
_NOT_MANAGED = {"TRANSFERRED_TO_GOOGLE", "DELETED"}
_ANNUAL = {"ANNUAL_MONTHLY_PAY", "ANNUAL_YEARLY_PAY"}
UNASSIGNED = "(none)"


def _add(b: PlanBreakdown, plan: str, seats: int) -> None:
    plan = (plan or "").upper()
    if plan == "FLEXIBLE":
        b.flexible += 1
        b.flexible_licences += seats
    elif plan == "ANNUAL_MONTHLY_PAY":
        b.annual_monthly_pay += 1
    elif plan == "ANNUAL_YEARLY_PAY":
        b.annual_yearly_pay += 1
    elif plan == "TRIAL":
        b.trial += 1
    else:
        b.other += 1
    if plan in _ANNUAL:
        b.annual_licences += seats
    b.total += 1


def _finish(b: PlanBreakdown) -> PlanBreakdown:
    b.annual_total = b.annual_monthly_pay + b.annual_yearly_pay
    b.annual_percent = round(b.annual_total * 100 / b.total, 1) if b.total else 0.0
    return b


def plan_breakdown(
    companies: Iterable[CompanyDocument], subscriptions: Iterable[SubscriptionDocument]
) -> Tuple[PlanBreakdown, Dict[str, PlanBreakdown]]:
    """(overall, per partner). A domain counts once, by the plan of its subscription; a domain
    whose job never created a subscription has no plan yet and is skipped."""
    by_company: Dict[str, SubscriptionDocument] = {}
    for sub in subscriptions:
        by_company.setdefault(sub.company_id, sub)

    overall = PlanBreakdown()
    per_partner: Dict[str, PlanBreakdown] = {}
    for company in companies:
        if company.status in _NOT_MANAGED:
            continue
        sub = by_company.get(company.company_id)
        if sub is None:
            continue
        _add(overall, sub.plan, sub.seats)
        _add(per_partner.setdefault(company.reseller_id or UNASSIGNED, PlanBreakdown()), sub.plan, sub.seats)

    return _finish(overall), {rid: _finish(b) for rid, b in per_partner.items()}
