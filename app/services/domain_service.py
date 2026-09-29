"""
Domain management for channel partners: suspend, activate, delete and
change the licence count of a domain they provisioned.

Google is always changed first; our records and the partner's quota are only
updated after Google confirms, so the two never drift apart.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
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
from app.repositories.coordination_repository import LockRepository
from app.repositories.reseller_repository import QuotaExceededError, ResellerRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.reseller_service import ResellerService

logger = get_logger(__name__)

GOOGLE_MAX_SEATS_PER_CALL = 100
DOMAIN_ACTION_LOCK_SECONDS = 3 * 60
REDUCIBLE_PLANS = {"FLEXIBLE"}
RESELLER_SUSPENSION = "RESELLER_INITIATED"
TRANSFERRED = "TRANSFERRED_TO_GOOGLE"

_SUSPENSION_HELP = {
    "PENDING_TOS_ACCEPTANCE": (
        "The customer's admin must sign in at https://admin.google.com and accept the Google Workspace "
        "Terms of Service; the subscription becomes active automatically after that."
    ),
    "TRIAL_ENDED": "The free trial has ended; move the subscription to a paid plan to continue.",
    "RENEWAL_WITH_TYPE_CANCEL": "The subscription was set to cancel at renewal and has lapsed.",
    "OTHER": "Google suspended it for another reason; contact Google partner support.",
}


def _explain(reason: str) -> str:
    return _SUSPENSION_HELP.get(reason, f"Google suspension reason: {reason}.")


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
        lock_repo: LockRepository,
    ) -> None:
        self._google = reseller_service
        self._companies = company_repo
        self._subscriptions = subscription_repo
        self._resellers = reseller_repo
        self._audit = audit_repo
        self._locks = lock_repo

    # ------------------------------------------------------------------
    # Public actions (each holds the domain lock, so actions on one domain never overlap)
    # ------------------------------------------------------------------

    async def suspend(self, domain: str, reseller: ResellerDocument, ip: str = "") -> dict:
        async with self._domain_lock(domain, "suspend"):
            return await self._set_status(domain, reseller, ip, suspend=True)

    async def activate(self, domain: str, reseller: ResellerDocument, ip: str = "") -> dict:
        async with self._domain_lock(domain, "activate"):
            return await self._set_status(domain, reseller, ip, suspend=False)

    async def change_licences(
        self, domain: str, reseller: ResellerDocument, target: int, ip: str = ""
    ) -> dict:
        async with self._domain_lock(domain, "change_licences"):
            return await self._change_licences(domain, reseller, target, ip)

    async def delete(
        self, domain: str, reseller: ResellerDocument, confirm: Optional[str], ip: str = ""
    ) -> dict:
        if (confirm or "").strip().lower() != domain.strip().lower():
            raise DomainActionError(
                400,
                "This transfers the domain to Google: the customer moves to direct billing with Google, "
                "you stop being billed, and it cannot be undone. "
                f"To confirm, repeat the domain: DELETE /api/v1/reseller/domains/{domain}?confirm={domain}",
            )
        async with self._domain_lock(domain, "delete"):
            return await self._delete(domain, reseller, ip)

    @asynccontextmanager
    async def _domain_lock(self, domain: str, operation: str):
        key = domain.strip().lower()
        token = self._locks.acquire(key, operation, DOMAIN_ACTION_LOCK_SECONDS)
        if not token:
            busy = self._locks.holder(key) or "another operation"
            raise DomainActionError(409, {
                "error": "Domain busy",
                "detail": f"{domain} is busy ({busy} in progress). Wait for it to finish, then try again.",
            })
        try:
            yield
        finally:
            self._locks.release(key, token)

    async def _change_licences(
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
        if delta > 0:
            # Reserve before calling Google so parallel requests can never overshoot the cap.
            try:
                self._resellers.adjust_licences_used(reseller.reseller_id, delta, enforce_cap=True)
            except QuotaExceededError as exc:
                raise DomainActionError(400, exc.as_detail())

        try:
            await self._google_call(self._resize, company.google_customer_id, sub.subscription_id,
                                    current, target, plan)
        except DomainActionError:
            if delta > 0:
                self._resellers.increment_licences_used(reseller.reseller_id, -delta)
            raise

        if delta < 0:
            self._resellers.increment_licences_used(reseller.reseller_id, delta)
        if local:
            self._subscriptions.update(local.subscription_id, {"seats": target})
        self._record(reseller.reseller_id, "LICENCES_CHANGED", company.primary_domain, ip,
                     {"from": current, "to": target, "plan": plan}, licences=delta)
        logger.info("domain_licences_changed", domain=company.primary_domain, previous=current, new=target)

        sign = "+" if delta > 0 else ""
        return self._licence_result(company, plan, current, target, reseller,
                                    f"{company.primary_domain} now has {target} licences ({sign}{delta}).")

    async def _delete(self, domain: str, reseller: ResellerDocument, ip: str = "") -> dict:
        """Transfer the domain's subscriptions to Google and return their licences to the quota.

        Google no longer lets resellers cancel Workspace subscriptions; transfer_to_direct is the
        supported way to end the reseller relationship. All subscriptions go in one call because
        Google requires a customer's subscriptions to be transferred together.
        """
        company = self._owned_company(domain, reseller)
        google_subs = await self._google_call(self._google.list_subscriptions, company.google_customer_id)
        if not google_subs:
            raise DomainActionError(409, f"{company.primary_domain} has no subscriptions at Google to transfer.")

        subscription_ids = [s.subscription_id for s in google_subs]
        released = sum(s.seats.number_of_seats for s in google_subs)
        try:
            await self._google_call(self._google.transfer_to_google, company.google_customer_id, subscription_ids)
        except DomainActionError as exc:
            self._record(reseller.reseller_id, "DOMAIN_TRANSFER", company.primary_domain, ip,
                         {"subscriptions": subscription_ids, "error": exc.detail}, status="FAILED")
            raise

        self._resellers.increment_licences_used(reseller.reseller_id, -released)
        self._companies.update_status(company.company_id, TRANSFERRED)
        for local in self._subscriptions.get_by_company_id(company.company_id):
            self._subscriptions.update(local.subscription_id, {"status": TRANSFERRED})
        self._record(reseller.reseller_id, "DOMAIN_TRANSFER", company.primary_domain, ip,
                     {"subscriptions": subscription_ids, "licences_released": released}, licences=-released)
        logger.info("domain_transferred_to_google", domain=company.primary_domain,
                    subscriptions=subscription_ids, released=released)

        return {
            "domain": company.primary_domain,
            "status": TRANSFERRED,
            "transferred_subscriptions": subscription_ids,
            "licences_released": released,
            "quota": self._quota(reseller.reseller_id, reseller),
            "message": (
                f"{company.primary_domain} was transferred to Google: the customer is now billed directly by "
                f"Google and you are no longer billed. {released} licence(s) returned to your quota."
            ),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _set_status(self, domain: str, reseller: ResellerDocument, ip: str, suspend: bool) -> dict:
        """Add or remove OUR suspension. Google may hold its own suspensions (e.g. Terms of Service
        not yet accepted) that only the customer or Google can lift, so we act on the reasons, not the status."""
        company = self._owned_company(domain, reseller)
        local, sub = await self._subscription_for(company)
        ours = RESELLER_SUSPENSION in sub.suspension_reasons

        if suspend:
            if not ours:
                sub = await self._google_call(self._google.suspend_subscription,
                                              company.google_customer_id, sub.subscription_id)
        else:
            if not ours and sub.status == "SUSPENDED":
                raise DomainActionError(409, {
                    "error": "Cannot activate",
                    "detail": (
                        f"{company.primary_domain} was suspended by Google, not by you. "
                        + " ".join(_explain(r) for r in sub.suspension_reasons)
                    ).strip(),
                    "status": sub.status,
                    "suspension_reasons": sub.suspension_reasons,
                })
            if ours:
                sub = await self._google_call(self._google.activate_subscription,
                                              company.google_customer_id, sub.subscription_id)

        google_reasons = [r for r in sub.suspension_reasons if r != RESELLER_SUSPENSION]
        self._companies.update_status(company.company_id, sub.status)
        if local:
            self._subscriptions.update(local.subscription_id, {"status": sub.status})
        self._record(reseller.reseller_id, "DOMAIN_SUSPEND" if suspend else "DOMAIN_ACTIVATE",
                     company.primary_domain, ip,
                     {"google_subscription_id": sub.subscription_id, "suspension_reasons": sub.suspension_reasons})
        logger.info("domain_status_changed", domain=company.primary_domain, status=sub.status,
                    reasons=sub.suspension_reasons)

        if suspend:
            message = f"{company.primary_domain} is now suspended by you."
        elif google_reasons:
            message = (f"Your suspension was removed, but {company.primary_domain} is still suspended by Google. "
                       + " ".join(_explain(r) for r in google_reasons))
        else:
            message = f"{company.primary_domain} is now active."

        return {
            "domain": company.primary_domain,
            "status": sub.status,
            "suspension_reasons": sub.suspension_reasons,
            "google_customer_id": company.google_customer_id,
            "google_subscription_id": sub.subscription_id,
            "message": message,
        }

    def _owned_company(self, domain: str, reseller: ResellerDocument) -> CompanyDocument:
        company = self._companies.get_by_domain(domain.strip().lower())
        # Same answer for "missing" and "belongs to another partner", so other partners' domains stay hidden.
        if not company or company.reseller_id != reseller.reseller_id:
            raise DomainActionError(404, f"Domain {domain} was not found in your account.")
        if company.status in (TRANSFERRED, "DELETED"):
            raise DomainActionError(
                409, f"Domain {domain} has already been transferred to Google and is no longer managed by you."
            )
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
