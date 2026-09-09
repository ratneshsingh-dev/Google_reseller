"""
Database document models for Firestore collections.

These represent the canonical shape of documents stored in Firestore
(or in-memory storage during local development).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Enums
# ===========================================================================


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    CUSTOMER_CREATING = "CUSTOMER_CREATING"
    CUSTOMER_CREATED = "CUSTOMER_CREATED"
    SUBSCRIPTION_CREATING = "SUBSCRIPTION_CREATING"
    SUBSCRIPTION_CREATED = "SUBSCRIPTION_CREATED"
    USER_PROVISIONING = "USER_PROVISIONING"
    EMAIL_SENDING = "EMAIL_SENDING"
    COMPLETED = "COMPLETED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


class EmployeeStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROVISIONED = "PROVISIONED"
    EXISTING = "EXISTING"
    FAILED = "FAILED"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class StepStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# ===========================================================================
# Document Models
# ===========================================================================


class CompanyDocument(BaseModel):
    """Stored in the 'companies' collection."""

    company_id: str
    company_name: str
    primary_domain: str
    alternate_email: str = ""
    contact_name: str = ""
    google_customer_id: Optional[str] = None
    status: str = "ACTIVE"
    reseller_id: Optional[str] = None  # Which reseller provisioned this company
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class SubscriptionDocument(BaseModel):
    """Stored in the 'subscriptions' collection."""

    subscription_id: str
    company_id: str
    google_subscription_id: Optional[str] = None
    sku_id: str
    plan: str
    seats: int
    status: str = "ACTIVE"
    billing_method: str = "ONLINE"
    created_at: datetime = Field(default_factory=_utcnow)


class EmployeeDocument(BaseModel):
    """Stored in the 'employees' collection."""

    employee_id: str
    company_id: str
    first_name: str
    last_name: str
    personal_email: Optional[str] = None
    corporate_email: str
    google_user_id: Optional[str] = None
    status: str = EmployeeStatus.PENDING.value
    password_hash: Optional[str] = None  # SHA-256 hash, never plaintext
    temporary_password: Optional[str] = None  # Plain-text for API response
    created_at: datetime = Field(default_factory=_utcnow)


class ProvisioningJobDocument(BaseModel):
    """Stored in the 'provisioning_jobs' collection."""

    job_id: str
    idempotency_key: Optional[str] = None
    company_name: str = ""
    primary_domain: str = ""
    status: str = JobStatus.PENDING.value
    reseller_id: Optional[str] = None  # Which reseller created this job
    google_customer_id: Optional[str] = None
    google_subscription_id: Optional[str] = None
    plan: Optional[str] = None
    sku_id: Optional[str] = None
    licensed_seats: Optional[int] = None
    users_created: int = 0
    users_failed: int = 0
    users_existing: int = 0
    email_status: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ProvisioningStepDocument(BaseModel):
    """Stored in the 'provisioning_steps' collection."""

    step_id: str
    job_id: str
    step_name: str
    status: str = StepStatus.PENDING.value
    details: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class NotificationDocument(BaseModel):
    """Stored in the 'notifications' collection."""

    notification_id: str
    job_id: str
    recipient: str
    subject: str
    body: str
    status: str = NotificationStatus.PENDING.value
    sent_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
