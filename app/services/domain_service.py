"""
Domain management for channel partners: suspend, activate, delete and
change the licence count of a domain they provisioned.

Google is always changed first; our records and the partner's quota are only
updated after Google confirms, so the two never drift apart.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Tuple

from app.core.logging import get_logger
from app.models.database import CompanyDocument, SubscriptionDocument
from app.models.google_api import (
    GoogleChangSeatsRequest,
    GoogleSubscription,
    GoogleSubscriptionSeats,
)
from app.models.reseller_models import AuditLogDocument, ResellerDocument
from app.repositories.audit_repository import AuditRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.reseller_repository import ResellerRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.reseller_service import ResellerService

logger = get_logger(__name__)

GOOGLE_MAX_SEATS_PER_CALL = 100
REDUCIBLE_PLANS = {"FLEXIBLE"}


class DomainActionError(Exception):
    """A domain action was refused. Carries the HTTP status and a partner-facing message."""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class DomainService:
    def __init__(
        self,
        reseller_service: ResellerService,
        company_repo: CompanyRepository,
        subscription_repo: SubscriptionRepository,
        reseller_repo: ResellerRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._google = reseller_service
        self._companies = company_repo
        self._subscriptions = subscription_repo
        self._resellers = reseller_repo
        self._audit = audit_repo

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    async def suspend(self, domain: str, reseller: ResellerDocument, ip: str = "") -> dict:
        return await self._set_status(domain, reseller, ip, suspend=True)

    async def activate(self, domain: str, reseller: ResellerDocument, ip: str = "") -> dict:
        return await self._set_status(domain, reseller, ip, suspend=False)

    async def change_licences(
        self, domain: str, reseller: ResellerDocument, target: int, ip: str = ""
    ) -> dict:
        """Set a new TOTAL. Increases work on any plan; decreases only on FLEXIBLE."""
        company = self._owned_company(domain, reseller)
        local, sub = await self._subscription_for(company)
        plan = sub.plan.plan_name.upper()
        current = sub.seats.number_of_seats
        delta = target - current

        if delta == 0:
            return self._licence_result(company, plan, current, target, reseller,
                                        "No change: the domain already has this many licences.")
        if delta < 0 and plan not in REDUCIBLE_PLANS:
            raise DomainActionError(
                400,
                f"Licences can only be reduced on the FLEXIBLE plan. "
                f"{company.primary_domain} is on {plan}.",
            )
        if delta > 0 and delta > reseller.licences_remaining:
            raise DomainActionError(400, {
                "error": "Licence cap exceeded",
                "detail": (
                    f"Cannot add {delta} licence(s). You have {reseller.licences_remaining} "
                    f"remaining out of {reseller.max_licence_cap}."
                ),
                "quota": {
                    "requested": delta,
                    "licences_used": reseller.licences_used,
                    "max_licence_cap": reseller.max_licence_cap,
                    "licences_remaining": reseller.licences_remaining,
                },
            })

        await self._google_call(self._resize, company.google_customer_id, sub.subscription_id,
                                current, target, plan)

        self._resellers.increment_licences_used(reseller.reseller_id, delta)
        if local:
            self._subscriptions.update(local.subscription_id, {"seats": target})
        self._record(reseller.reseller_id, "LICENCES_CHANGED", company.primary_domain, ip,
                     {"from": current, "to": target, "plan": plan}, licences=delta)
        logger.info("domain_licences_changed", domain=company.primary_domain, previous=current, new=target)

        sign = "+" if delta > 0 else ""
        return self._licence_result(company, plan, current, target, reseller,
                                    f"{company.primary_domain} now has {target} licences ({sign}{delta}).")

    async def delete(
        self, domain: str, reseller: ResellerDocument, confirm: Optional[str], ip: str = ""
    ) -> dict:
        """Cancel every subscription on the domain and return its licences to the quota."""
        if (confirm or "").strip().lower() != domain.strip().lower():
            raise DomainActionError(
                400,
                "Deleting cancels all subscriptions immediately and cannot be undone. "
                f"To confirm, repeat the domain: DELETE /api/v1/reseller/domains/{domain}?confirm={domain}",
            )

        company = self._owned_company(domain, reseller)
        google_subs = await self._google_call(self._google.list_subscriptions, company.google_customer_id)

        cancelled, released = [], 0
        failure: Optional[DomainActionError] = None
        for sub in google_subs:
            try:
                await self._google_call(self._google.delete_subscription,
                                        company.google_customer_id, sub.subscription_id)
            except DomainActionError as exc:
                failure = exc
                break
            cancelled.append(sub.subscription_id)
            released += sub.seats.number_of_seats

        if released:
            self._resellers.increment_licences_used(reseller.reseller_id, -released)

        if failure:
            self._record(reseller.reseller_id, "DOMAIN_DELETE", company.primary_domain, ip,
                         {"cancelled": cancelled, "licences_released": released, "error": failure.detail},
                         licences=-released, status="FAILED")
            raise DomainActionError(
                failure.status_code,
                f"{failure.detail} Cancelled before the error: {cancelled or 'none'}; "
                f"licences released: {released}.",
            )

        self._companies.update_status(company.company_id, "DELETED")
        for local in self._subscriptions.get_by_company_id(company.company_id):
            self._subscriptions.update(local.subscription_id, {"status": "CANCELLED"})
        self._record(reseller.reseller_id, "DOMAIN_DELETE", company.primary_domain, ip,
                     {"cancelled": cancelled, "licences_released": released}, licences=-released)
        logger.info("domain_deleted", domain=company.primary_domain, cancelled=cancelled, released=released)

        return {
            "domain": company.primary_domain,
            "status": "DELETED",
            "cancelled_subscriptions": cancelled,
            "licences_released": released,
            "quota": self._quota(reseller.reseller_id, reseller),
            "message": (
                f"All subscriptions for {company.primary_domain} were cancelled and "
                f"{released} licence(s) returned to your quota."
            ),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _set_status(self, domain: str, reseller: ResellerDocument, ip: str, suspend: bool) -> dict:
        company = self._owned_company(domain, reseller)
        local, sub = await self._subscription_for(company)
        target = "SUSPENDED" if suspend else "ACTIVE"

        if sub.status != target:
            action = self._google.suspend_subscription if suspend else self._google.activate_subscription
            sub = await self._google_call(action, company.google_customer_id, sub.subscription_id)

        self._companies.update_status(company.company_id, target)
        if local:
            self._subscriptions.update(local.subscription_id, {"status": target})
        self._record(reseller.reseller_id, "DOMAIN_SUSPEND" if suspend else "DOMAIN_ACTIVATE",
                     company.primary_domain, ip, {"google_subscription_id": sub.subscription_id})
        logger.info("domain_status_changed", domain=company.primary_domain, status=target)

        return {
            "domain": company.primary_domain,
            "status": target,
            "google_customer_id": company.google_customer_id,
            "google_subscription_id": sub.subscription_id,
            "message": f"{company.primary_domain} is now {target.lower()}.",
        }

    def _owned_company(self, domain: str, reseller: ResellerDocument) -> CompanyDocument:
        company = self._companies.get_by_domain(domain.strip().lower())
        # Same answer for "missing" and "belongs to another partner", so other partners' domains stay hidden.
        if not company or company.reseller_id != reseller.reseller_id:
            raise DomainActionError(404, f"Domain {domain} was not found in your account.")
        if company.status == "DELETED":
            raise DomainActionError(409, f"Domain {domain} has already been deleted.")
        if not company.google_customer_id:
            raise DomainActionError(409, f"Domain {domain} has no Google customer yet.")
        return company

    async def _subscription_for(
        self, company: CompanyDocument
    ) -> Tuple[Optional[SubscriptionDocument], GoogleSubscription]:
        """The live Google subscription for the domain, plus our record of it."""
        local_subs = self._subscriptions.get_by_company_id(company.company_id)
        local = local_subs[0] if local_subs else None
        google_subs = await self._google_call(self._google.list_subscriptions, company.google_customer_id)

        if local and local.google_subscription_id:
            match = next((s for s in google_subs if s.subscription_id == local.google_subscription_id), None)
            if match:
                return local, match
        if len(google_subs) == 1:
            return local, google_subs[0]
        if not google_subs:
            raise DomainActionError(409, f"{company.primary_domain} has no active subscription at Google.")
        raise DomainActionError(
            409, f"Could not identify a single subscription for {company.primary_domain}."
        )

    async def _resize(self, customer_id: str, subscription_id: str, current: int, target: int, plan: str):
        # Google accepts at most 100 extra seats per call; reductions go in one step.
        steps = [target] if target < current else list(
            range(current + GOOGLE_MAX_SEATS_PER_CALL, target, GOOGLE_MAX_SEATS_PER_CALL)
        ) + [target]
        for seats in steps:
            await self._google.change_seats(
                customer_id,
                subscription_id,
                GoogleChangSeatsRequest(seats=GoogleSubscriptionSeats(numberOfSeats=seats, licensedNumberOfSeats=seats)),
                plan_name=plan,
            )

    async def _google_call(self, fn, *args):
        """Run a Google call and turn any failure into a partner-facing error."""
        try:
            return await fn(*args)
        except DomainActionError:
            raise
        except Exception as exc:
            raise self._google_error(exc)

    @staticmethod
    def _google_error(exc: Exception) -> DomainActionError:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError):
            reason = exc._get_reason() or str(exc)
            if exc.status_code == 404:
                return DomainActionError(404, f"Google could not find this subscription: {reason}")
            if 400 <= exc.status_code < 500:
                return DomainActionError(400, f"Google rejected the request: {reason}")
        return DomainActionError(502, f"Google request failed: {str(exc)[:200]}")

    def _quota(self, reseller_id: str, fallback: ResellerDocument) -> dict:
        r = self._resellers.get_by_id(reseller_id) or fallback
        return {
            "licences_used": r.licences_used,
            "max_licence_cap": r.max_licence_cap,
            "licences_remaining": r.licences_remaining,
        }

    def _licence_result(self, company: CompanyDocument, plan: str, previous: int, target: int,
                        reseller: ResellerDocument, message: str) -> dict:
        return {
            "domain": company.primary_domain,
            "plan": plan,
            "previous_licences": previous,
            "licences": target,
            "change": target - previous,
            "quota": self._quota(reseller.reseller_id, reseller),
            "message": message,
        }

    def _record(self, reseller_id: str, action: str, domain: str, ip: str, details: dict,
                licences: int = 0, status: str = "SUCCESS") -> None:
        try:
            self._audit.create(AuditLogDocument(
                log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                reseller_id=reseller_id,
                action=action,
                resource_type="domain",
                resource_id=domain,
                licences_requested=licences,
                status=status,
                ip_address=ip,
                details=details,
            ))
        except Exception:
            logger.warning("domain_audit_write_failed", action=action, domain=domain)
