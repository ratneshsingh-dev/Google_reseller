"""
Pydantic models for the Reseller Partner RBAC system.

Collections:
  - 'resellers'  → ResellerDocument
  - 'audit_log'  → AuditLogDocument
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Enums
# ===========================================================================


class ResellerRole(str, enum.Enum):
    RESELLER_FULL = "RESELLER_FULL"          # Can provision + view own data
    RESELLER_READONLY = "RESELLER_READONLY"  # Can only view own data
    ADMIN = "ADMIN"                           # Full system access


class ResellerStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


# ===========================================================================
# Firestore Document Models
# ===========================================================================


class ResellerDocument(BaseModel):
    """Stored in the 'resellers' Firestore collection.

    reseller_id also serves as the client_id for JWT authentication.
    """

    reseller_id: str                         # e.g., "RSL-A1B2C3D4" (= client_id)
    company_name: str
    contact_email: str
    contact_name: str
    role: ResellerRole = ResellerRole.RESELLER_FULL
    status: ResellerStatus = ResellerStatus.ACTIVE
    max_licence_cap: int                     # Admin-set maximum licences
    licences_used: int = 0                   # Running counter
    client_secret_hash: str                  # SHA-256 of the client_secret
    token_version: int = 1                   # Increment to revoke all active JWTs
    access_methods: List[str] = Field(default_factory=lambda: ["API"])  # CSV, MANUAL, API
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_api_call_at: Optional[datetime] = None
    created_by: str = ""                     # Admin email who created this

    @property
    def licences_remaining(self) -> int:
        return max(0, self.max_licence_cap - self.licences_used)

    @property
    def utilization_percent(self) -> float:
        if self.max_licence_cap == 0:
            return 0.0
        return round((self.licences_used / self.max_licence_cap) * 100, 2)


class AuditLogDocument(BaseModel):
    """Stored in the 'audit_log' Firestore collection."""

    log_id: str
    reseller_id: str
    action: str                              # PROVISION, VIEW_STATUS, AUTH_LOGIN, etc.
    resource_type: str = ""                  # company, job, subscription
    resource_id: str = ""
    licences_requested: int = 0
    status: str = "SUCCESS"                  # SUCCESS, DENIED_OVER_CAP, FAILED
    ip_address: str = ""
    timestamp: datetime = Field(default_factory=_utcnow)
    details: Dict[str, Any] = Field(default_factory=dict)


# ===========================================================================
# Request Models
# ===========================================================================


class TokenRequest(BaseModel):
    """Reseller submits this to /auth/token to get a JWT."""
    client_id: str
    client_secret: str


class CreateResellerRequest(BaseModel):
    """Admin creates a new channel partner."""
    company_name: str = Field(..., min_length=1, max_length=200)
    contact_email: str = Field(..., max_length=254)
    contact_name: str = Field(..., min_length=1, max_length=200)
    role: ResellerRole = ResellerRole.RESELLER_FULL
    max_licence_cap: int = Field(..., ge=1, description="Maximum licences this partner can provision")
    access_methods: List[str] = Field(default_factory=lambda: ["API"], description="CSV, MANUAL, API")


class EmailLoginRequest(BaseModel):
    """Channel partner logs into the portal with email + client_secret."""
    email: str
    client_secret: str


class UpdateResellerRequest(BaseModel):
    """Admin updates a reseller's cap, role, or status."""
    max_licence_cap: Optional[int] = Field(None, ge=1)
    role: Optional[ResellerRole] = None
    status: Optional[ResellerStatus] = None


# ===========================================================================
# Response Models
# ===========================================================================


class TokenResponse(BaseModel):
    """Returned by POST /reseller/auth/token."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int           # Seconds until expiry (86400 = 24hr)
    reseller_id: str
    role: str


class QuotaSummary(BaseModel):
    """Embedded in every provisioning response."""
    licences_assigned_now: int
    total_licences_used: int
    max_licence_cap: int
    licences_remaining: int
    message: str


class QuotaResponse(BaseModel):
    """Returned by GET /reseller/quota."""
    reseller_id: str
    company_name: str
    max_licence_cap: int
    licences_used: int
    licences_remaining: int
    utilization_percent: float
    access_methods: List[str] = Field(default_factory=lambda: ["API"])
    contact_email: str = ""


class ResellerResponse(BaseModel):
    """Channel partner summary returned by admin list/detail endpoints."""
    reseller_id: str
    company_name: str
    contact_email: str
    contact_name: str
    role: str
    status: str
    max_licence_cap: int
    licences_used: int
    licences_remaining: int
    utilization_percent: float
    access_methods: List[str] = Field(default_factory=lambda: ["API"])
    created_at: Optional[datetime] = None
    last_api_call_at: Optional[datetime] = None
    created_by: str = ""


class ResellerCreatedResponse(ResellerResponse):
    """Returned ONLY at partner creation — includes full credentials.

    After this, client_secret is never shown again.
    """
    client_id: str       # = reseller_id
    client_secret: str   # Plain-text, shown once only


class DashboardSummaryResponse(BaseModel):
    """Returned by GET /admin/dashboard/summary."""
    total_resellers: int
    active_resellers: int
    suspended_resellers: int
    total_licences_provisioned: int
    total_licence_cap: int
    overall_utilization_percent: float
    resellers: List[ResellerResponse]


class AuditLogEntryResponse(BaseModel):
    """Single audit log entry."""
    log_id: str
    reseller_id: str
    action: str
    resource_type: str
    resource_id: str
    licences_requested: int
    status: str
    ip_address: str
    timestamp: Optional[datetime] = None
    details: Dict[str, Any] = Field(default_factory=dict)
