"""
Pydantic request models for the provisioning REST API.

These models validate incoming JSON and CSV payloads before they reach
the provisioning orchestrator.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.security import validate_domain, validate_email


class PostalAddressRequest(BaseModel):
    """Postal address for a company."""

    address_line1: str = Field(..., min_length=1, max_length=500)
    locality: str = Field(..., min_length=1, max_length=200)
    region: str = Field(..., min_length=1, max_length=100)
    postal_code: str = Field(..., min_length=1, max_length=20)
    country_code: str = Field(..., min_length=2, max_length=2)


class EmployeeRequest(BaseModel):
    """Single employee to provision (kept for backward compatibility)."""

    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    personal_email: Optional[str] = Field(default=None, max_length=254)

    @field_validator("personal_email")
    @classmethod
    def validate_personal_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v != "" and not validate_email(v):
            raise ValueError(f"Invalid personal email format: {v}")
        return v


class ProvisioningRequest(BaseModel):
    """Enterprise-style provisioning request.

    Creates the customer, subscription, and ONE admin account.
    The admin then creates the remaining user accounts via
    the Google Workspace Admin Console.
    """

    company_name: str = Field(..., min_length=1, max_length=500)
    primary_domain: str = Field(..., min_length=3, max_length=253)
    alternate_email: str = Field(..., max_length=254)
    contact_name: str = Field(..., min_length=1, max_length=200)
    postal_address: PostalAddressRequest
    plan: str = Field(..., pattern=r"^(FLEXIBLE|ANNUAL_MONTHLY_PAY|ANNUAL_YEARLY_PAY|TRIAL)$")
    sku_id: str = Field(..., min_length=1, max_length=100)
    license_count: int = Field(..., ge=1, description="Number of licences. For reseller API, upper bound is enforced by your quota.")
    initiated_by_email: str = Field(..., max_length=254)
    econz_notification_email: str = Field(..., max_length=254)

    # Admin account details (enterprise flow)
    admin_first_name: str = Field(..., min_length=1, max_length=100)
    admin_last_name: str = Field(..., min_length=1, max_length=100)
    admin_recovery_email: Optional[str] = Field(default=None, max_length=254)

    # Legacy field — kept for backward compat but optional now
    employees: Optional[List[EmployeeRequest]] = Field(default=None)

    @field_validator("primary_domain")
    @classmethod
    def validate_primary_domain(cls, v: str) -> str:
        if not validate_domain(v):
            raise ValueError(f"Invalid domain format: {v}")
        return v.lower()

    @field_validator("alternate_email", "initiated_by_email", "econz_notification_email")
    @classmethod
    def validate_email_fields(cls, v: str) -> str:
        if not validate_email(v):
            raise ValueError(f"Invalid email format: {v}")
        return v.lower()

    @field_validator("admin_recovery_email")
    @classmethod
    def validate_admin_recovery_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v != "" and not validate_email(v):
            raise ValueError(f"Invalid admin recovery email format: {v}")
        return v

    @property
    def admin_as_employee(self) -> EmployeeRequest:
        """Convert the admin details into an EmployeeRequest for provisioning."""
        return EmployeeRequest(
            first_name=self.admin_first_name,
            last_name=self.admin_last_name,
            personal_email=self.admin_recovery_email,
        )
