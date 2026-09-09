"""
Admin API Routes — Full CRUD for Reseller Partner Management.

All routes require admin authentication via Google OAuth session cookie.

POST   /api/v1/admin/resellers                       — Create reseller
GET    /api/v1/admin/resellers                        — List all resellers
GET    /api/v1/admin/resellers/{id}                   — Reseller detail
PATCH  /api/v1/admin/resellers/{id}                   — Update cap/role/status
DELETE /api/v1/admin/resellers/{id}                   — Deactivate reseller
POST   /api/v1/admin/resellers/{id}/regenerate-secret — Rotate credentials
GET    /api/v1/admin/resellers/{id}/audit-log          — Audit history
GET    /api/v1/admin/dashboard/summary                 — Global stats
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth_middleware import require_admin
from app.core.jwt_service import generate_client_secret, hash_secret
from app.core.logging import get_logger
from app.models.reseller_models import (
    AuditLogDocument,
    AuditLogEntryResponse,
    CreateResellerRequest,
    DashboardSummaryResponse,
    ResellerCreatedResponse,
    ResellerDocument,
    ResellerResponse,
    ResellerStatus,
    UpdateResellerRequest,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.firestore_client import get_store
from app.repositories.reseller_repository import ResellerRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin — Reseller Management"])


def _to_response(doc: ResellerDocument) -> ResellerResponse:
    """Convert a ResellerDocument to a ResellerResponse."""
    return ResellerResponse(
        reseller_id=doc.reseller_id,
        company_name=doc.company_name,
        contact_email=doc.contact_email,
        contact_name=doc.contact_name,
        role=doc.role.value,
        status=doc.status.value,
        max_licence_cap=doc.max_licence_cap,
        licences_used=doc.licences_used,
        licences_remaining=doc.licences_remaining,
        utilization_percent=doc.utilization_percent,
        created_at=doc.created_at,
        last_api_call_at=doc.last_api_call_at,
        created_by=doc.created_by,
    )


# ---------------------------------------------------------------------------
# Create Reseller
# ---------------------------------------------------------------------------


@router.post(
    "/resellers",
    response_model=ResellerCreatedResponse,
    status_code=201,
    summary="Create a new reseller partner account",
)
async def create_reseller(
    body: CreateResellerRequest,
    request: Request,
    admin=Depends(require_admin),
) -> ResellerCreatedResponse:
    """Create a new reseller partner.

    Returns the **client_id** and **client_secret** — the secret is shown **only once**.
    Store it securely and share it with the reseller partner.
    """
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())

    # ---- Guard: block duplicate active email ----
    existing = reseller_repo.get_by_contact_email(body.contact_email.lower())
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A channel partner with email '{body.contact_email}' already exists "
                f"(ID: {existing.reseller_id}, Status: {existing.status.value}). "
                "Deactivate or delete the existing account first, or use a different email."
            ),
        )

    reseller_id = f"RSL-{uuid.uuid4().hex[:8].upper()}"
    plain_secret = generate_client_secret()
    secret_hash = hash_secret(plain_secret)
    admin_email = admin.get("email", "") if isinstance(admin, dict) else str(admin)

    doc = ResellerDocument(
        reseller_id=reseller_id,
        company_name=body.company_name,
        contact_email=body.contact_email.lower(),
        contact_name=body.contact_name,
        role=body.role,
        status=ResellerStatus.ACTIVE,
        max_licence_cap=body.max_licence_cap,
        licences_used=0,
        client_secret_hash=secret_hash,
        token_version=1,
        access_methods=body.access_methods,
        created_by=admin_email,
    )
    reseller_repo.create(doc)

    # Log audit
    audit_repo.create(
        AuditLogDocument(
            log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
            reseller_id=reseller_id,
            action="RESELLER_CREATED",
            status="SUCCESS",
            details={"created_by": admin_email, "max_licence_cap": body.max_licence_cap},
        )
    )

    logger.info(
        "reseller_created",
        reseller_id=reseller_id,
        company=body.company_name,
        cap=body.max_licence_cap,
        by=admin_email,
    )

    return ResellerCreatedResponse(
        reseller_id=reseller_id,
        company_name=doc.company_name,
        contact_email=doc.contact_email,
        contact_name=doc.contact_name,
        role=doc.role.value,
        status=doc.status.value,
        max_licence_cap=doc.max_licence_cap,
        licences_used=0,
        licences_remaining=doc.max_licence_cap,
        utilization_percent=0.0,
        access_methods=doc.access_methods,
        created_at=doc.created_at,
        last_api_call_at=None,
        created_by=admin_email,
        client_id=reseller_id,
        client_secret=plain_secret,
    )


# ---------------------------------------------------------------------------
# List All Resellers
# ---------------------------------------------------------------------------


@router.get(
    "/resellers",
    response_model=List[ResellerResponse],
    summary="List all reseller partners with usage stats",
)
async def list_resellers(admin=Depends(require_admin)) -> List[ResellerResponse]:
    """List all reseller accounts with their licence usage stats."""
    reseller_repo = ResellerRepository(get_store())
    resellers = reseller_repo.list_all()
    # Sort by created_at descending
    resellers.sort(key=lambda r: r.created_at or "", reverse=True)
    return [_to_response(r) for r in resellers]


# ---------------------------------------------------------------------------
# Get Reseller Detail
# ---------------------------------------------------------------------------


@router.get(
    "/resellers/{reseller_id}",
    response_model=ResellerResponse,
    summary="Get reseller detail",
)
async def get_reseller(
    reseller_id: str,
    admin=Depends(require_admin),
) -> ResellerResponse:
    """Get full details for a specific reseller including quota breakdown."""
    reseller_repo = ResellerRepository(get_store())
    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")
    return _to_response(reseller)


# ---------------------------------------------------------------------------
# Update Reseller (cap / role / status)
# ---------------------------------------------------------------------------


@router.patch(
    "/resellers/{reseller_id}",
    response_model=ResellerResponse,
    summary="Update reseller licence cap, role, or status",
)
async def update_reseller(
    reseller_id: str,
    body: UpdateResellerRequest,
    admin=Depends(require_admin),
) -> ResellerResponse:
    """Update a reseller's max_licence_cap, role, or status.

    - Set **status=SUSPENDED** to block all API calls immediately.
    - Set **status=ACTIVE** to re-enable.
    - Increase/decrease **max_licence_cap** to control provisioning quota.
    """
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())
    admin_email = admin.get("email", "") if isinstance(admin, dict) else str(admin)

    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")

    updates = {}
    if body.max_licence_cap is not None:
        updates["max_licence_cap"] = body.max_licence_cap
    if body.role is not None:
        updates["role"] = body.role.value
    if body.status is not None:
        updates["status"] = body.status.value

    if updates:
        reseller_repo.update(reseller_id, updates)

    audit_repo.create(
        AuditLogDocument(
            log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
            reseller_id=reseller_id,
            action="RESELLER_UPDATED",
            status="SUCCESS",
            details={"updated_by": admin_email, "changes": updates},
        )
    )

    logger.info("reseller_updated", reseller_id=reseller_id, updates=updates, by=admin_email)
    updated = reseller_repo.get_by_id(reseller_id)
    return _to_response(updated)


# ---------------------------------------------------------------------------
# Deactivate Reseller
# ---------------------------------------------------------------------------


@router.delete(
    "/resellers/{reseller_id}",
    summary="Deactivate a reseller partner",
)
async def deactivate_reseller(
    reseller_id: str,
    admin=Depends(require_admin),
) -> dict:
    """Soft-deactivate a reseller. Their tokens will be rejected immediately."""
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())
    admin_email = admin.get("email", "") if isinstance(admin, dict) else str(admin)

    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")

    reseller_repo.deactivate(reseller_id)
    # Also increment token_version to revoke existing tokens
    reseller_repo.increment_token_version(reseller_id)

    audit_repo.create(
        AuditLogDocument(
            log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
            reseller_id=reseller_id,
            action="RESELLER_DEACTIVATED",
            status="SUCCESS",
            details={"by": admin_email},
        )
    )

    logger.info("reseller_deactivated", reseller_id=reseller_id, by=admin_email)
    return {"message": f"Reseller {reseller_id} has been deactivated."}


# ---------------------------------------------------------------------------
# Regenerate Client Secret
# ---------------------------------------------------------------------------


@router.post(
    "/resellers/{reseller_id}/regenerate-secret",
    summary="Rotate reseller credentials (invalidates all active tokens)",
)
async def regenerate_secret(
    reseller_id: str,
    admin=Depends(require_admin),
) -> dict:
    """Generate a new client_secret for a reseller.

    - All currently active JWT tokens for this reseller are **immediately invalidated**.
    - The reseller must call /auth/token again with the new secret.
    - The new secret is shown **only once** — save it before closing this response.
    """
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())
    admin_email = admin.get("email", "") if isinstance(admin, dict) else str(admin)

    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")

    # Generate new secret and increment token_version (revokes all active JWTs)
    new_plain_secret = generate_client_secret()
    new_hash = hash_secret(new_plain_secret)
    new_version = reseller_repo.increment_token_version(reseller_id)
    reseller_repo.update(reseller_id, {"client_secret_hash": new_hash})

    audit_repo.create(
        AuditLogDocument(
            log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
            reseller_id=reseller_id,
            action="SECRET_REGENERATED",
            status="SUCCESS",
            details={"by": admin_email, "new_token_version": new_version},
        )
    )

    logger.info("reseller_secret_rotated", reseller_id=reseller_id, by=admin_email)
    return {
        "reseller_id": reseller_id,
        "client_id": reseller_id,
        "client_secret": new_plain_secret,
        "message": "Secret rotated. All previous tokens are now invalid. Share the new client_secret with the reseller.",
    }


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


@router.get(
    "/resellers/{reseller_id}/audit-log",
    response_model=List[AuditLogEntryResponse],
    summary="Get provisioning audit history for a reseller",
)
async def get_audit_log(
    reseller_id: str,
    admin=Depends(require_admin),
) -> List[AuditLogEntryResponse]:
    """Get all API actions performed by a reseller, newest first."""
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())

    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")

    logs = audit_repo.get_by_reseller(reseller_id)
    return [
        AuditLogEntryResponse(
            log_id=l.log_id,
            reseller_id=l.reseller_id,
            action=l.action,
            resource_type=l.resource_type,
            resource_id=l.resource_id,
            licences_requested=l.licences_requested,
            status=l.status,
            ip_address=l.ip_address,
            timestamp=l.timestamp,
            details=l.details,
        )
        for l in logs
    ]


# ---------------------------------------------------------------------------
# Dashboard Summary
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummaryResponse,
    summary="Global reseller dashboard stats",
)
async def dashboard_summary(admin=Depends(require_admin)) -> DashboardSummaryResponse:
    """Get aggregate statistics across all reseller partners."""
    reseller_repo = ResellerRepository(get_store())
    resellers = reseller_repo.list_all()

    total = len(resellers)
    active = sum(1 for r in resellers if r.status == ResellerStatus.ACTIVE)
    suspended = sum(1 for r in resellers if r.status.value in ("SUSPENDED", "DEACTIVATED"))
    total_used = sum(r.licences_used for r in resellers)
    total_cap = sum(r.max_licence_cap for r in resellers)
    utilization = round((total_used / total_cap) * 100, 2) if total_cap > 0 else 0.0

    resellers.sort(key=lambda r: r.created_at or "", reverse=True)

    return DashboardSummaryResponse(
        total_resellers=total,
        active_resellers=active,
        suspended_resellers=suspended,
        total_licences_provisioned=total_used,
        total_licence_cap=total_cap,
        overall_utilization_percent=utilization,
        resellers=[_to_response(r) for r in resellers],
    )
