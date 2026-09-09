"""
Real Google Workspace Reseller API adapter.

Uses google-api-python-client (googleapiclient.discovery) with service
account credentials scoped to apps.order.
"""

from __future__ import annotations

import os
from typing import List, Optional

import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.models.google_api import (
    GoogleChangePlanRequest,
    GoogleChangSeatsRequest,
    GoogleCreateCustomerRequest,
    GoogleCreateSubscriptionRequest,
    GoogleCustomer,
    GooglePostalAddress,
    GoogleSubscription,
    GoogleSubscriptionPlan,
    GoogleSubscriptionSeats,
)
from app.services.reseller_service import ResellerService

logger = structlog.get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/apps.order"]


def _build_service():
    creds_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    admin_email = os.getenv("GOOGLE_ADMIN_EMAIL", "")
    credentials = service_account.Credentials.from_service_account_file(
        creds_file, scopes=_SCOPES
    )
    # Impersonate the Workspace admin via Domain-Wide Delegation
    # The raw service account is not authorized as a reseller — the admin is
    delegated = credentials.with_subject(admin_email)
    return build("reseller", "v1", credentials=delegated, cache_discovery=False)


def _parse_customer(resp: dict) -> GoogleCustomer:
    addr = resp.get("postalAddress") or {}
    return GoogleCustomer(
        customerId=resp.get("customerId", ""),
        customerDomain=resp.get("customerDomain", ""),
        customerType=resp.get("customerType", "domain"),
        alternateEmail=resp.get("alternateEmail", ""),
        customerDomainVerified=resp.get("customerDomainVerified", True),
        postalAddress=GooglePostalAddress(
            contactName=addr.get("contactName", ""),
            organizationName=addr.get("organizationName", ""),
            addressLine1=addr.get("addressLine1", ""),
            locality=addr.get("locality", ""),
            region=addr.get("region", ""),
            postalCode=addr.get("postalCode", ""),
            countryCode=addr.get("countryCode", ""),
        ) if addr else None,
    )


def _parse_subscription(resp: dict) -> GoogleSubscription:
    plan = resp.get("plan", {})
    seats = resp.get("seats", {})
    return GoogleSubscription(
        customerId=resp.get("customerId", ""),
        subscriptionId=resp.get("subscriptionId", ""),
        skuId=resp.get("skuId", ""),
        status=resp.get("status", "ACTIVE"),
        billingMethod=resp.get("billingMethod", "ONLINE"),
        customerDomain=resp.get("customerDomain", ""),
        skuName=resp.get("skuName", ""),
        creationTime=str(resp.get("creationTime", "")),
        plan=GoogleSubscriptionPlan(
            planName=plan.get("planName", "TRIAL"),
            isCommitmentPlan=plan.get("isCommitmentPlan", False),
        ),
        seats=GoogleSubscriptionSeats(
            numberOfSeats=seats.get("numberOfSeats", 0),
            licensedNumberOfSeats=seats.get("licensedNumberOfSeats", 0),
        ),
    )


class GoogleResellerService(ResellerService):
    """Real Google Workspace Reseller API adapter."""

    def __init__(self) -> None:
        self._service = _build_service()
        admin_email = os.getenv("GOOGLE_ADMIN_EMAIL", "")
        logger.info("google_reseller_initialized", delegated_as=admin_email)

    def _execute_with_retry(self, request_fn, max_retries: int = 3):
        """Execute a Google API request with retry on SSL/connection errors.
        
        httplib2 on Windows can drop connections with 'EOF occurred in violation
        of protocol'. We rebuild the service to get a fresh connection on retry.
        """
        import time
        last_error = None
        for attempt in range(max_retries):
            try:
                return request_fn(self._service)
            except HttpError:
                raise  # Don't retry HTTP-level errors, only connection errors
            except Exception as e:
                err_str = str(e)
                is_ssl = any(x in err_str for x in [
                    'EOF occurred in violation of protocol',
                    'SSLError', 'SSLEOFError',
                    'Connection reset', 'BrokenPipe',
                    'RemoteDisconnected',
                ])
                if is_ssl and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("google_api_ssl_retry",
                                   attempt=attempt + 1,
                                   wait_secs=wait,
                                   error=err_str[:150])
                    time.sleep(wait)
                    self._service = _build_service()  # fresh connection
                    last_error = e
                else:
                    raise
        raise last_error

    async def get_customer(self, customer_id: str) -> GoogleCustomer:
        try:
            resp = self._execute_with_retry(
                lambda svc: svc.customers().get(customerId=customer_id).execute()
            )
            logger.info("google_reseller_get_customer", customer_id=customer_id)
            return _parse_customer(resp)
        except HttpError as e:
            logger.error("google_reseller_get_customer_error", customer_id=customer_id, status=e.status_code, error=str(e))
            raise

    async def get_customer_by_domain(self, domain: str) -> Optional[GoogleCustomer]:
        """Look up a customer by primary domain.
        
        The Google Reseller API accepts the primary domain as the customerId,
        so we can call customers.get(customerId=domain) directly.
        Returns None if the domain is not yet registered.
        """
        try:
            resp = self._execute_with_retry(
                lambda svc: svc.customers().get(customerId=domain).execute()
            )
            logger.info("google_reseller_domain_found", domain=domain, customer_id=resp.get("customerId"))
            return _parse_customer(resp)
        except HttpError as e:
            if e.status_code in (404, 412):
                logger.info("google_reseller_domain_not_found", domain=domain)
                return None
            logger.error("google_reseller_get_by_domain_error", domain=domain, status=e.status_code, error=str(e))
            raise

    async def create_customer(
        self, request: GoogleCreateCustomerRequest
    ) -> GoogleCustomer:
        addr = request.postal_address
        body = {
            "customerDomain": request.customer_domain,
            "customerType": request.customer_type,
            "alternateEmail": request.alternate_email,
            "postalAddress": {
                "contactName": addr.contact_name,
                "organizationName": addr.organization_name,
                "addressLine1": addr.address_line1,
                "locality": addr.locality,
                "region": addr.region,
                "postalCode": addr.postal_code,
                "countryCode": addr.country_code,
            },
        }
        try:
            resp = self._execute_with_retry(
                lambda svc: svc.customers().insert(body=body).execute()
            )
            logger.info("google_reseller_customer_created",
                        customer_id=resp.get("customerId"),
                        domain=request.customer_domain)
            return _parse_customer(resp)
        except HttpError as e:
            logger.error("google_reseller_create_customer_error",
                         domain=request.customer_domain,
                         status=e.status_code,
                         error=str(e))
            raise

    async def get_subscription(
        self, customer_id: str, subscription_id: str
    ) -> GoogleSubscription:
        try:
            resp = (
                self._service.subscriptions()
                .get(customerId=customer_id, subscriptionId=subscription_id)
                .execute()
            )
            return _parse_subscription(resp)
        except HttpError as e:
            logger.error("google_reseller_get_subscription_error", customer_id=customer_id, error=str(e))
            raise

    async def create_subscription(
        self, customer_id: str, request: GoogleCreateSubscriptionRequest
    ) -> GoogleSubscription:
        plan_name = request.plan.plan_name.upper()
        num_seats = request.seats.number_of_seats

        # TRIAL uses maximumNumberOfSeats, FLEXIBLE/ANNUAL use numberOfSeats
        # Do NOT include 'kind' in seats — it causes a 400 badRequest
        if plan_name == "TRIAL":
            seats_body = {"maximumNumberOfSeats": num_seats}
        else:
            seats_body = {"numberOfSeats": num_seats}

        # customerId must be in the body (path param alone is not enough)
        body = {
            "customerId": customer_id,
            "skuId": request.sku_id,
            "plan": {"planName": plan_name},
            "seats": seats_body,
        }
        try:
            resp = self._execute_with_retry(
                lambda svc: svc.subscriptions().insert(
                    customerId=customer_id, body=body
                ).execute()
            )
            logger.info("google_reseller_subscription_created",
                        customer_id=customer_id,
                        subscription_id=resp.get("subscriptionId"),
                        sku=request.sku_id,
                        plan=plan_name,
                        seats=num_seats)
            return _parse_subscription(resp)
        except HttpError as e:
            logger.error("google_reseller_create_subscription_error",
                         customer_id=customer_id,
                         plan=plan_name,
                         status=e.status_code,
                         error=str(e))
            raise

    async def change_seats(
        self,
        customer_id: str,
        subscription_id: str,
        request: GoogleChangSeatsRequest,
    ) -> GoogleSubscription:
        body = {"numberOfSeats": request.seats.number_of_seats,
                "maximumNumberOfSeats": request.seats.number_of_seats}
        try:
            resp = (
                self._service.subscriptions()
                .changeSeats(customerId=customer_id, subscriptionId=subscription_id, body=body)
                .execute()
            )
            return _parse_subscription(resp)
        except HttpError as e:
            logger.error("google_reseller_change_seats_error", customer_id=customer_id, error=str(e))
            raise

    async def change_plan(
        self,
        customer_id: str,
        subscription_id: str,
        request: GoogleChangePlanRequest,
    ) -> GoogleSubscription:
        body = {"planName": request.plan_name}
        try:
            resp = (
                self._service.subscriptions()
                .changePlan(customerId=customer_id, subscriptionId=subscription_id, body=body)
                .execute()
            )
            return _parse_subscription(resp)
        except HttpError as e:
            logger.error("google_reseller_change_plan_error", customer_id=customer_id, error=str(e))
            raise

    async def list_subscriptions(
        self, customer_id: str
    ) -> List[GoogleSubscription]:
        try:
            resp = (
                self._service.subscriptions()
                .list(customerId=customer_id)
                .execute()
            )
            return [_parse_subscription(s) for s in resp.get("subscriptions", [])]
        except HttpError as e:
            logger.error("google_reseller_list_subscriptions_error", customer_id=customer_id, error=str(e))
            raise
