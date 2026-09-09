"""
Pydantic response models for the provisioning REST API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ProvisioningJobResponse(BaseModel):
    """Returned immediately when a provisioning request is accepted."""

    job_id: str
    status: str = "PENDING"


class ProvisioningStepResponse(BaseModel):
    """Status of a single provisioning step."""

    step_name: str
    status: str
    details: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class EmployeeStatusResponse(BaseModel):
    """Status of a provisioned employee."""

    employee_id: str
    first_name: str
    last_name: str
    corporate_email: str
    personal_email: Optional[str] = None
    google_user_id: Optional[str] = None
    status: str  # PROVISIONED, EXISTING, FAILED
    temporary_password: Optional[str] = None


class ProvisioningStatusResponse(BaseModel):
    """Full provisioning job status returned by GET /api/v1/provision/{job_id}."""

    job_id: str
    status: str
    company_name: Optional[str] = None
    primary_domain: Optional[str] = None
    google_customer_id: Optional[str] = None
    google_subscription_id: Optional[str] = None
    plan: Optional[str] = None
    sku_id: Optional[str] = None
    licensed_seats: Optional[int] = None
    users_created: int = 0
    users_failed: int = 0
    users_existing: int = 0
    steps: List[ProvisioningStepResponse] = Field(default_factory=list)
    employees: List[EmployeeStatusResponse] = Field(default_factory=list)
    email_status: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CompanyResponse(BaseModel):
    """Company summary."""

    company_id: str
    company_name: str
    primary_domain: str
    google_customer_id: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None


class CompanyDetailResponse(CompanyResponse):
    """Company with subscription details."""

    alternate_email: Optional[str] = None
    subscription_id: Optional[str] = None
    google_subscription_id: Optional[str] = None
    plan: Optional[str] = None
    sku_id: Optional[str] = None
    seats: Optional[int] = None
    subscription_status: Optional[str] = None
    email_status: Optional[str] = None


class EmployeeResponse(BaseModel):
    """Employee detail."""

    employee_id: str
    company_id: str
    first_name: str
    last_name: str
    personal_email: Optional[str] = None
    corporate_email: str
    google_user_id: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    status_code: int = 400


class CsvUploadResponse(BaseModel):
    """Response for CSV provisioning."""

    total_rows: int
    jobs: List[ProvisioningJobResponse]
    errors: List[dict[str, Any]] = Field(default_factory=list)


class SingleCompanyCsvResponse(BaseModel):
    """Response when provisioning a single company via CSV."""

    job_id: str
    status: str = "PENDING"
    company_name: str
    primary_domain: str
    employee_count: int
    employees: List[dict[str, Any]] = Field(default_factory=list)
    message: str = "Provisioning job started successfully"

