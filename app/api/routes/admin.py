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

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.auth_middleware import require_admin
from app.core.jwt_service import (
    ADMIN_VIEW_TOKEN_MAX_AGE_SECONDS,
    create_admin_view_token,
    generate_client_secret,
    hash_secret,
)
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
from app.repositories.admin_repository import AdminRepository

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
# View a partner's dashboard (read-only)
# ---------------------------------------------------------------------------


@router.post(
    "/resellers/{reseller_id}/view-token",
    summary="Get a read-only token to open this partner's dashboard",
)
async def create_view_token(
    reseller_id: str,
    admin=Depends(require_admin),
) -> dict:
    reseller_repo = ResellerRepository(get_store())
    audit_repo = AuditRepository(get_store())
    admin_email = getattr(admin, "email", "") or (admin.get("email", "") if isinstance(admin, dict) else "")

    reseller = reseller_repo.get_by_id(reseller_id)
    if not reseller:
        raise HTTPException(status_code=404, detail=f"Reseller {reseller_id} not found.")

    token = create_admin_view_token(reseller_id, admin_email, reseller.token_version)

    try:
        audit_repo.create(
            AuditLogDocument(
                log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                reseller_id=reseller_id,
                action="ADMIN_VIEW_DASHBOARD",
                status="SUCCESS",
                details={"by": admin_email},
            )
        )
    except Exception:
        pass

    logger.info("admin_view_token_issued", reseller_id=reseller_id, by=admin_email)
    return {
        "access_token": token,
        "reseller_id": reseller_id,
        "contact_email": reseller.contact_email,
        "expires_in": ADMIN_VIEW_TOKEN_MAX_AGE_SECONDS,
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
def dashboard_summary(admin=Depends(require_admin)) -> DashboardSummaryResponse:
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

    from app.core.config import get_settings
    from app.dependencies import get_company_repo, get_subscription_repo
    from app.services.plan_stats import plan_breakdown

    settings = get_settings()
    overall_plans, plans_by_reseller = plan_breakdown(
        get_company_repo().list_all(), get_subscription_repo().list_all()
    )
    every = settings.plan_mix_annual_every

    return DashboardSummaryResponse(
        total_resellers=total,
        active_resellers=active,
        suspended_resellers=suspended,
        total_licences_provisioned=total_used,
        total_licence_cap=total_cap,
        overall_utilization_percent=utilization,
        resellers=[_to_response(r) for r in resellers],
        plan_breakdown=overall_plans,
        plan_breakdown_by_reseller=plans_by_reseller,
        plan_mix_enabled=settings.plan_mix_enabled,
        plan_mix_target_percent=round(100 / every, 1) if settings.plan_mix_enabled and every > 0 else 0.0,
    )


@router.get("/licence-usage", summary="Day-by-day licence usage (read-only)")
def licence_usage_report(
    days: int = Query(30, ge=1, le=365),
    reseller_id: Optional[str] = Query(None),
    admin=Depends(require_admin),
) -> dict:
    """Licences added to new domains, added/removed later, and released by transfers, per day (IST)."""
    from app.dependencies import get_job_repo
    from app.services.licence_usage import licence_usage

    return licence_usage(
        get_job_repo().list_all_jobs(), AuditRepository(get_store()).list_all(),
        days=days, reseller_id=reseller_id,
    )


def _reason_kind(reason: Optional[str]) -> str:
    text = (reason or "").lower()
    if "broken pipe" in text or "connection" in text or "timed out" in text:
        return "dropped_connection"
    if "403" in text or "not authorized" in text:
        return "google_403"
    return "other"


def _category(kind: str, attempts: int) -> str:
    """Where the dashboard puts a domain.
    fixable - the failure was on our side (dropped connection etc.): the Fix button should work.
    try     - Google refused (403) and nobody has tried since: worth one click, may need manual.
    manual  - Google refused again after a Fix attempt: fix it by hand in the Google Admin console."""
    if kind == "google_403":
        return "manual" if attempts >= 1 else "try"
    return "fixable"


_legacy_reason_cache: dict = {}


def _legacy_reason(job) -> str:
    """Why an older job (finished before failures were recorded) has no admin, worked out from what
    the old flow stored: no employee row for the admin = it stopped before creating anything (a
    dropped connection); only a FAILED row = creating the user was refused; a PROVISIONED row = the
    user was created and then making it admin was refused. Immutable for an old job, so cached."""
    cached = _legacy_reason_cache.get(job.job_id)
    if cached:
        return cached
    from app.dependencies import get_company_repo, get_employee_repo
    from app.services.provisioning_service import ProvisioningService

    try:
        email = ProvisioningService.job_admin_email(job).lower()
        company = get_company_repo().get_by_domain(job.primary_domain)
        rows = [e for e in get_employee_repo().get_by_company_id(company.company_id)
                if e.corporate_email.lower() == email] if company else []
    except Exception:
        return "Admin was not created (older job, reason not recorded)"
    statuses = {e.status for e in rows}
    if "PROVISIONED" in statuses:
        reason = "Google refused making the user an admin (403) - older job"
    elif "FAILED" in statuses:
        reason = "Google refused creating the user (403) - older job"
    else:
        reason = "Dropped connection before the admin step (Broken pipe) - older job"
    _legacy_reason_cache[job.job_id] = reason
    return reason


def _admin_failure_row(j, reason: Optional[str] = None) -> dict:
    from app.services.provisioning_service import ProvisioningService

    try:
        admin_email = ProvisioningService.job_admin_email(j)
    except Exception:
        admin_email = None
    reason = reason or j.admin_failure_reason or ""
    attempts = j.admin_repair_attempts or 0
    kind = _reason_kind(reason)
    return {
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "domain": j.primary_domain,
        "company_name": j.company_name,
        "job_id": j.job_id,
        "reseller_id": j.reseller_id,
        "google_customer_id": j.google_customer_id,
        "status": j.status,
        "admin_email": admin_email,
        "reason": reason,
        "reason_kind": kind,
        "category": _category(kind, attempts),
        "legacy": j.admin_created is None,
        "repair_attempts": attempts,
        "last_repair_at": j.admin_last_repair_at.isoformat() if j.admin_last_repair_at else None,
    }


def _failure_rows(jobs) -> list:
    """Rows for the list. The reason of an older (unflagged) job is worked out in parallel the
    first time and then served from the cache."""
    from concurrent.futures import ThreadPoolExecutor

    old = [j for j in jobs if j.admin_created is None and not j.admin_failure_reason]
    reasons = {}
    if old:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for job, reason in zip(old, pool.map(_legacy_reason, old)):
                reasons[job.job_id] = reason
    return [_admin_failure_row(j, reasons.get(j.job_id)) for j in jobs]


_repairs_running: set = set()
_repairs_lock = threading.Lock()


def _failed_admin_job(job_id: str):
    """The job, if it is on the failure list; 404 / 409 otherwise."""
    from app.dependencies import get_job_repo
    from app.repositories.job_repository import JobRepository

    job = get_job_repo().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not JobRepository.is_admin_failure(job):
        raise HTTPException(status_code=409, detail="This domain is not on the failed-admin list")
    if not job.request_payload:
        raise HTTPException(status_code=422, detail="The original request is not stored for this job")
    return job


def _audit_admin_action(job, action: str, status: str, by: str, **details) -> None:
    AuditRepository(get_store()).create(
        AuditLogDocument(
            log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
            reseller_id=job.reseller_id or "",
            action=action,
            resource_type="job",
            resource_id=job.job_id,
            status=status,
            details={"by": by, "domain": job.primary_domain, **details},
        )
    )


@router.post("/admin-failures/{job_id}/fix", summary="Create the admin user / make it admin for one domain")
def fix_admin(job_id: str, admin=Depends(require_admin)) -> dict:
    """The dashboard's "Fix admin" button. Safe to repeat: it looks at Google first and does only
    the missing step (create user, make admin), then confirms the result."""
    from app.dependencies import get_provisioning_service

    by = admin.get("email", "") if isinstance(admin, dict) else str(admin)
    job = _failed_admin_job(job_id)
    with _repairs_lock:
        if job_id in _repairs_running:
            raise HTTPException(status_code=409, detail="This domain is already being fixed")
        _repairs_running.add(job_id)
    try:
        result = asyncio.run(get_provisioning_service().repair_admin(job))
    except Exception as exc:
        _audit_admin_action(job, "ADMIN_REPAIR", "FAILED", by, error=str(exc)[:300])
        return {"fixed": False, "job_id": job_id, "domain": job.primary_domain,
                "reason": str(exc)[:500], "reason_kind": _reason_kind(str(exc))}
    finally:
        with _repairs_lock:
            _repairs_running.discard(job_id)
    _audit_admin_action(job, "ADMIN_REPAIR", "SUCCESS", by, outcome=result["outcome"],
                        admin_email=result["admin_email"])
    return {"fixed": True, "job_id": job_id, "domain": job.primary_domain, **result}


@router.get("/admin-failures/{job_id}/check", summary="Look at the domain's admin in Google (read-only)")
def check_admin(job_id: str, subscription: bool = Query(True), admin=Depends(require_admin)) -> dict:
    from app.dependencies import get_provisioning_service

    from app.dependencies import get_job_repo

    job = get_job_repo().get_job(job_id)
    if job is None or not job.request_payload:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        state = asyncio.run(get_provisioning_service().check_admin(job, with_subscription=subscription))
    except Exception as exc:
        return {"job_id": job_id, "domain": job.primary_domain, "error": str(exc)[:300]}
    return {"job_id": job_id, "domain": job.primary_domain, **state}


@router.post("/admin-failures/{job_id}/mark-handled", summary="Take a domain off the list (fixed by hand)")
def mark_admin_handled(job_id: str, admin=Depends(require_admin)) -> dict:
    from app.dependencies import get_job_repo

    by = admin.get("email", "") if isinstance(admin, dict) else str(admin)
    job = _failed_admin_job(job_id)
    get_job_repo().update_fields(
        job_id, admin_created=True, admin_failure_reason=f"Fixed manually (marked by {by})")
    try:
        from app.services.provisioning_service import ProvisioningService
        admin_email = ProvisioningService.job_admin_email(job)
    except Exception:
        admin_email = None
    _audit_admin_action(job, "ADMIN_MARKED_HANDLED", "SUCCESS", by, admin_email=admin_email)
    return {"job_id": job_id, "domain": job.primary_domain, "handled": True}


_FIX_METHODS = {
    "CREATED_AND_ADMIN": "Fix button: created the user and made it admin",
    "MADE_ADMIN": "Fix button: made the existing user admin",
    "ALREADY_ADMIN": "Fix button: user was already an admin",
}


@router.get("/admin-failures/fixed", summary="Domains whose admin was fixed from the dashboard (history)")
def fixed_admins(
    reseller_id: Optional[str] = Query(None),
    format: str = Query("json", pattern="^(json|csv)$"),
    admin=Depends(require_admin),
):
    """Every domain fixed with the Fix button or taken off the list with Mark handled, newest
    first, built from the audit log (so it includes fixes made before this tab existed)."""
    import csv
    import io

    from fastapi.responses import Response

    from app.core.security import generate_temporary_password
    from app.dependencies import get_job_repo

    audit = AuditRepository(get_store())
    entries = audit.list_by_action("ADMIN_REPAIR") + audit.list_by_action("ADMIN_MARKED_HANDLED")
    latest: dict = {}
    for e in sorted(entries, key=lambda x: x.timestamp):     # oldest first, so the last success wins
        if e.status == "SUCCESS":
            latest[e.resource_id] = e
    rows = []
    for job_id, e in latest.items():
        if reseller_id and e.reseller_id != reseller_id:
            continue
        d = e.details or {}
        admin_email, domain = d.get("admin_email"), d.get("domain")
        if not admin_email:                                   # entries written before the email was recorded
            job = get_job_repo().get_job(job_id)
            if job and job.request_payload:
                from app.services.provisioning_service import ProvisioningService
                try:
                    admin_email = ProvisioningService.job_admin_email(job)
                except Exception:
                    admin_email = None
        rows.append({
            "fixed_at": e.timestamp.isoformat() if e.timestamp else None,
            "fixed_by": d.get("by"),
            "domain": domain,
            "job_id": job_id,
            "reseller_id": e.reseller_id,
            "admin_email": admin_email,
            "method": ("Marked handled: fixed by hand in the Google Admin console"
                       if e.action == "ADMIN_MARKED_HANDLED"
                       else _FIX_METHODS.get(d.get("outcome"), "Fix button")),
            "temporary_password": None if e.action == "ADMIN_MARKED_HANDLED" else generate_temporary_password(),
        })
    rows.sort(key=lambda r: r["fixed_at"] or "", reverse=True)
    if format == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["fixed_at", "fixed_by", "domain", "job_id", "reseller_id",
                                                 "admin_email", "method", "temporary_password"])
        writer.writeheader()
        writer.writerows(rows)
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="fixed_admins.csv"'})
    return {"count": len(rows), "domains": rows}


@router.get("/admin-failures", summary="Domains created without a working admin user (read-only)")
def admin_failures(
    reseller_id: Optional[str] = Query(None),
    format: str = Query("json", pattern="^(json|csv)$"),
    admin=Depends(require_admin),
):
    """Jobs that finished (domain + subscription exist) but whose admin user could not be
    created or made admin, so someone has to finish them by hand. Newest first."""
    import csv
    import io

    from fastapi.responses import Response

    from app.dependencies import get_job_repo

    jobs = get_job_repo().list_admin_failures()
    if reseller_id:
        jobs = [j for j in jobs if j.reseller_id == reseller_id]
    rows = _failure_rows(jobs)
    if format == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()) if rows else ["domain"])
        writer.writeheader()
        writer.writerows(rows)
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="admin_failures.csv"'})
    return {"count": len(rows), "domains": rows}


# ---------------------------------------------------------------------------
# Admin User Management Routes
# ---------------------------------------------------------------------------


@router.get("/admins", summary="List all admin users")
async def list_admins(admin=Depends(require_admin)):
    """List all admin users who have access to this panel."""
    from app.core.config import get_settings
    settings = get_settings()
    admin_repo = AdminRepository(get_store())

    # Combine env var admins + Firestore admins
    env_admins = [{
        "email": e,
        "name": e.split("@")[0],
        "source": "config",
        "added_by": "system",
        "is_active": True,
    } for e in settings.admin_email_list]

    firestore_admins = [{
        "email": a.email,
        "name": a.name,
        "source": "firestore",
        "added_by": a.added_by,
        "is_active": a.is_active,
    } for a in admin_repo.get_all_active()]

    # Merge — avoid duplicates
    env_emails = {a["email"] for a in env_admins}
    merged = env_admins + [a for a in firestore_admins if a["email"] not in env_emails]
    return {"admins": merged, "total": len(merged)}


@router.post("/admins", summary="Add a new admin user", status_code=201)
async def add_admin(request: Request, admin=Depends(require_admin)):
    """Add a new admin email. Only superadmins (env var list) can do this."""
    from app.core.config import get_settings
    settings = get_settings()

    caller_email = admin.get("email", "") if isinstance(admin, dict) else getattr(admin, "email", "")

    # Only superadmins (env var list) can grant admin access
    if caller_email.lower() not in settings.admin_email_list:
        raise HTTPException(
            status_code=403,
            detail="Only superadmins can add new admin users."
        )

    body = await request.json()
    email = body.get("email", "").strip().lower()
    name = body.get("name", "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Valid email is required.")

    admin_repo = AdminRepository(get_store())
    doc = admin_repo.add(email=email, name=name, added_by=caller_email)

    logger.info("admin_user_added", email=email, by=caller_email)
    return {
        "message": f"{email} has been added as an admin.",
        "email": doc.email,
        "name": doc.name,
        "added_by": doc.added_by,
    }


@router.delete("/admins/{email}", summary="Remove an admin user")
async def remove_admin(email: str, admin=Depends(require_admin)):
    """Remove admin access for an email. Only superadmins can do this."""
    from app.core.config import get_settings
    settings = get_settings()

    caller_email = admin.get("email", "") if isinstance(admin, dict) else getattr(admin, "email", "")

    # Only superadmins (env var list) can remove admin access
    if caller_email.lower() not in settings.admin_email_list:
        raise HTTPException(
            status_code=403,
            detail="Only superadmins can remove admin users."
        )

    email = email.strip().lower()

    # Prevent removing superadmins (env var hardcoded)
    if email in settings.admin_email_list:
        raise HTTPException(
            status_code=400,
            detail=f"{email} is a superadmin and cannot be removed."
        )

    admin_repo = AdminRepository(get_store())
    success = admin_repo.remove(email=email, removed_by=caller_email)

    if not success:
        raise HTTPException(status_code=404, detail=f"{email} was not found in the admin list.")

    logger.info("admin_user_removed", email=email, by=caller_email)
    return {"message": f"{email} has been removed from admin access."}
