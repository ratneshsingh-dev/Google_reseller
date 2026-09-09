"""
Custom exception hierarchy for the provisioning system.

All domain exceptions inherit from ProvisioningError so callers
can catch at any level of granularity.
"""

from __future__ import annotations


class ProvisioningError(Exception):
    """Base exception for all provisioning errors."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# --- Validation Errors ---


class ValidationError(ProvisioningError):
    """Input validation failed."""


class InvalidDomainError(ValidationError):
    """Domain format is invalid."""


class InvalidEmailError(ValidationError):
    """Email format is invalid."""


class InsufficientSeatsError(ValidationError):
    """License count is less than employee count."""


# --- Duplicate / Conflict Errors ---


class CustomerAlreadyExistsError(ProvisioningError):
    """Customer with this domain already exists in the mock/real API."""

    def __init__(self, message: str, customer_id: str | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.customer_id = customer_id


class SubscriptionAlreadyExistsError(ProvisioningError):
    """Subscription for this customer/SKU already exists."""

    def __init__(self, message: str, subscription_id: str | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.subscription_id = subscription_id


class DuplicateEmployeeError(ProvisioningError):
    """Employee with this corporate email already exists."""


class IdempotencyConflictError(ProvisioningError):
    """Request with this idempotency key already processed."""

    def __init__(self, message: str, existing_job_id: str | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.existing_job_id = existing_job_id


# --- API / Infrastructure Errors ---


class MockApiError(ProvisioningError):
    """Error from the mock API layer."""

    def __init__(self, message: str, status_code: int = 500, **kwargs):
        super().__init__(message, **kwargs)
        self.status_code = status_code


class RetryableError(MockApiError):
    """Transient error that can be retried (e.g., 503)."""

    def __init__(self, message: str = "Temporary service unavailable", **kwargs):
        super().__init__(message, status_code=503, **kwargs)


class PermanentError(MockApiError):
    """Permanent error that should not be retried."""

    def __init__(self, message: str = "Permanent failure", **kwargs):
        super().__init__(message, status_code=500, **kwargs)


# --- Not Found ---


class CustomerNotFoundError(ProvisioningError):
    """Customer not found."""


class SubscriptionNotFoundError(ProvisioningError):
    """Subscription not found."""


class UserNotFoundError(ProvisioningError):
    """User not found."""


class JobNotFoundError(ProvisioningError):
    """Provisioning job not found."""


# --- Reseller / RBAC / Auth Errors ---


class UnauthorizedError(ProvisioningError):
    """Request is not authenticated (missing or invalid token)."""


class ForbiddenError(ProvisioningError):
    """Authenticated but not permitted (wrong role)."""


class ResellerNotFoundError(ProvisioningError):
    """Reseller account not found."""


class ResellerSuspendedError(ProvisioningError):
    """Reseller account is suspended or deactivated."""


class InvalidTokenError(ProvisioningError):
    """JWT token is invalid or malformed."""


class TokenExpiredError(ProvisioningError):
    """JWT token has expired."""


class LicenceCapExceededError(ProvisioningError):
    """Reseller has exceeded their maximum licence cap."""

    def __init__(
        self,
        message: str,
        requested: int = 0,
        used: int = 0,
        cap: int = 0,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.requested = requested
        self.used = used
        self.cap = cap
        self.remaining = cap - used
