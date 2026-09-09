"""
Mock implementation of the Google Workspace Reseller API.

Stores data in-memory with deterministic ID generation.
Supports configurable failure simulation via MOCK_FAILURE_RATE.
"""

from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.exceptions import (
    CustomerAlreadyExistsError,
    CustomerNotFoundError,
    RetryableError,
    SubscriptionAlreadyExistsError,
    SubscriptionNotFoundError,
)
from app.core.logging import get_logger
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

logger = get_logger(__name__)

# SKU name mapping
SKU_NAMES = {
    "SKU-BUSINESS-STARTER": "Google Workspace Business Starter",
    "SKU-BUSINESS-STANDARD": "Google Workspace Business Standard",
    "SKU-BUSINESS-PLUS": "Google Workspace Business Plus",
    "SKU-ENTERPRISE-STANDARD": "Google Workspace Enterprise Standard",
    "SKU-ENTERPRISE-PLUS": "Google Workspace Enterprise Plus",
}


class MockResellerService(ResellerService):
    """In-memory mock of the Google Workspace Reseller API."""

    def __init__(self) -> None:
        self._customers: Dict[str, GoogleCustomer] = {}
        self._domain_index: Dict[str, str] = {}  # domain -> customer_id
        self._subscriptions: Dict[str, Dict[str, GoogleSubscription]] = {}
        self._customer_counter = 0
        self._subscription_counter = 0
        self._lock = threading.Lock()

    def _next_customer_id(self) -> str:
        with self._lock:
            self._customer_counter += 1
            return f"CUST-{self._customer_counter:06d}"

    def _next_subscription_id(self) -> str:
        with self._lock:
            self._subscription_counter += 1
            return f"SUB-{self._subscription_counter:06d}"

    def _maybe_fail(self) -> None:
        """Simulate transient failures based on MOCK_FAILURE_RATE."""
        settings = get_settings()
        if settings.mock_failure_mode and random.random() < settings.mock_failure_rate:
            logger.warning("mock_simulated_failure", service="reseller")
            raise RetryableError("Simulated transient Reseller API failure")

    async def get_customer(self, customer_id: str) -> GoogleCustomer:
        self._maybe_fail()
        await asyncio.sleep(0.01)  # Simulate network latency

        customer = self._customers.get(customer_id)
        if not customer:
            raise CustomerNotFoundError(
                f"Customer {customer_id} not found",
                details={"customer_id": customer_id},
            )
        return customer

    async def get_customer_by_domain(self, domain: str) -> Optional[GoogleCustomer]:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        customer_id = self._domain_index.get(domain.lower())
        if customer_id:
            return self._customers.get(customer_id)
        return None

    async def create_customer(
        self, request: GoogleCreateCustomerRequest
    ) -> GoogleCustomer:
        self._maybe_fail()
        await asyncio.sleep(0.02)

        domain = request.customer_domain.lower()

        # Check for duplicate
        if domain in self._domain_index:
            existing_id = self._domain_index[domain]
            raise CustomerAlreadyExistsError(
                f"Customer with domain {domain} already exists",
                customer_id=existing_id,
            )

        customer_id = self._next_customer_id()

        customer = GoogleCustomer(
            kind="reseller#customer",
            customerId=customer_id,
            customerDomain=domain,
            customerType=request.customer_type,
            postalAddress=request.postal_address,
            alternateEmail=request.alternate_email,
            customerDomainVerified=True,
        )

        self._customers[customer_id] = customer
        self._domain_index[domain] = customer_id

        logger.info(
            "mock_customer_created",
            customer_id=customer_id,
            domain=domain,
        )
        return customer

    async def get_subscription(
        self, customer_id: str, subscription_id: str
    ) -> GoogleSubscription:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        subs = self._subscriptions.get(customer_id, {})
        sub = subs.get(subscription_id)
        if not sub:
            raise SubscriptionNotFoundError(
                f"Subscription {subscription_id} not found for customer {customer_id}"
            )
        return sub

    async def create_subscription(
        self, customer_id: str, request: GoogleCreateSubscriptionRequest
    ) -> GoogleSubscription:
        self._maybe_fail()
        await asyncio.sleep(0.02)

        # Verify customer exists
        if customer_id not in self._customers:
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        # Check for duplicate subscription (same customer + SKU)
        existing_subs = self._subscriptions.get(customer_id, {})
        for sub in existing_subs.values():
            if sub.sku_id == request.sku_id:
                raise SubscriptionAlreadyExistsError(
                    f"Subscription for SKU {request.sku_id} already exists",
                    subscription_id=sub.subscription_id,
                )

        subscription_id = self._next_subscription_id()
        customer = self._customers[customer_id]

        is_commitment = request.plan.plan_name in (
            "ANNUAL_MONTHLY_PAY",
            "ANNUAL_YEARLY_PAY",
        )

        subscription = GoogleSubscription(
            kind="reseller#subscription",
            customerId=customer_id,
            subscriptionId=subscription_id,
            skuId=request.sku_id,
            plan=GoogleSubscriptionPlan(
                planName=request.plan.plan_name,
                isCommitmentPlan=is_commitment,
            ),
            seats=GoogleSubscriptionSeats(
                numberOfSeats=request.seats.number_of_seats,
                licensedNumberOfSeats=request.seats.number_of_seats,
            ),
            status="ACTIVE",
            billingMethod="MOCK",
            customerDomain=customer.customer_domain,
            skuName=SKU_NAMES.get(request.sku_id, request.sku_id),
            creationTime=datetime.now(timezone.utc).isoformat(),
        )

        if customer_id not in self._subscriptions:
            self._subscriptions[customer_id] = {}
        self._subscriptions[customer_id][subscription_id] = subscription

        logger.info(
            "mock_subscription_created",
            customer_id=customer_id,
            subscription_id=subscription_id,
            sku_id=request.sku_id,
            seats=request.seats.number_of_seats,
        )
        return subscription

    async def change_seats(
        self, customer_id: str, subscription_id: str, request: GoogleChangSeatsRequest
    ) -> GoogleSubscription:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        sub = await self.get_subscription(customer_id, subscription_id)

        updated = sub.model_copy(
            update={
                "seats": GoogleSubscriptionSeats(
                    numberOfSeats=request.seats.number_of_seats,
                    licensedNumberOfSeats=request.seats.number_of_seats,
                )
            }
        )
        self._subscriptions[customer_id][subscription_id] = updated

        logger.info(
            "mock_seats_changed",
            subscription_id=subscription_id,
            new_seats=request.seats.number_of_seats,
        )
        return updated

    async def change_plan(
        self, customer_id: str, subscription_id: str, request: GoogleChangePlanRequest
    ) -> GoogleSubscription:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        sub = await self.get_subscription(customer_id, subscription_id)

        is_commitment = request.plan_name in (
            "ANNUAL_MONTHLY_PAY",
            "ANNUAL_YEARLY_PAY",
        )

        updated = sub.model_copy(
            update={
                "plan": GoogleSubscriptionPlan(
                    planName=request.plan_name,
                    isCommitmentPlan=is_commitment,
                )
            }
        )
        self._subscriptions[customer_id][subscription_id] = updated

        logger.info(
            "mock_plan_changed",
            subscription_id=subscription_id,
            new_plan=request.plan_name,
        )
        return updated

    async def list_subscriptions(
        self, customer_id: str
    ) -> List[GoogleSubscription]:
        self._maybe_fail()
        await asyncio.sleep(0.01)

        return list(self._subscriptions.get(customer_id, {}).values())
