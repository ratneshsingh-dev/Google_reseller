"""
Abstract interface for the Google Workspace Reseller API.

Concrete implementations:
  - MockResellerService  (app.adapters.mock.mock_reseller)
  - GoogleResellerService (app.adapters.google.google_reseller)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from app.models.google_api import (
    GoogleChangePlanRequest,
    GoogleChangSeatsRequest,
    GoogleCreateCustomerRequest,
    GoogleCreateSubscriptionRequest,
    GoogleCustomer,
    GoogleSubscription,
)


class ResellerService(ABC):
    """Interface for Reseller API operations."""

    @abstractmethod
    async def get_customer(self, customer_id: str) -> GoogleCustomer:
        """Retrieve a customer by ID."""
        ...

    @abstractmethod
    async def get_customer_by_domain(self, domain: str) -> Optional[GoogleCustomer]:
        """Retrieve a customer by domain. Returns None if not found."""
        ...

    @abstractmethod
    async def create_customer(
        self, request: GoogleCreateCustomerRequest
    ) -> GoogleCustomer:
        """Create a new customer."""
        ...

    @abstractmethod
    async def get_subscription(
        self, customer_id: str, subscription_id: str
    ) -> GoogleSubscription:
        """Retrieve a subscription."""
        ...

    @abstractmethod
    async def create_subscription(
        self, customer_id: str, request: GoogleCreateSubscriptionRequest
    ) -> GoogleSubscription:
        """Create a new subscription for a customer."""
        ...

    @abstractmethod
    async def change_seats(
        self, customer_id: str, subscription_id: str, request: GoogleChangSeatsRequest
    ) -> GoogleSubscription:
        """Change seat count on a subscription."""
        ...

    @abstractmethod
    async def change_plan(
        self, customer_id: str, subscription_id: str, request: GoogleChangePlanRequest
    ) -> GoogleSubscription:
        """Change the plan on a subscription."""
        ...

    @abstractmethod
    async def list_subscriptions(
        self, customer_id: str
    ) -> List[GoogleSubscription]:
        """List all subscriptions for a customer."""
        ...
