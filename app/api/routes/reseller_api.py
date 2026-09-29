"""
Reseller-Facing API Routes — Protected by JWT Bearer Token.

All routes require: Authorization: Bearer <token>
Get a token via: POST /api/v1/reseller/auth/token (or /google-login / /email-login)

ROUTES
------
POST  /api/v1/reseller/provision
      Called by: cp-manual.js (Manual form), cp-csv.js (CSV tab), partner own API
      Runs the full provisioning workflow as a background job.
      Enforces licence cap before accepting.
      Returns: { job_id, status, quota }
      Debug: check provisioning_service.py if the job fails.

GET   /api/v1/reseller/provision/{job_id}
      Called by: cp-manual.js (pollManualJob), cp-csv.js (pollJob)
      Returns job progress with all step statuses.
      Debug: if job stuck at PENDING, check server logs for the failed step.

GET   /api/v1/reseller/quota
      Called by: cp-quota.js (loadQuota), cp-core.js (showApp)
      Returns licences_used, max_licence_cap, access_methods.
      Debug: if numbers wrong, check reseller document in Firestore.

GET   /api/v1/reseller/companies
      Called by: cp-companies.js (loadCompanies)
      Returns only THIS resellers companies (filtered by reseller_id).
      Debug: if empty, check company document has reseller_id set correctly.

GET   /api/v1/reseller/companies/{company_id}
      Single company detail (own only - 404 if belongs to another reseller).

POST   /api/v1/reseller/domains/{domain}/suspend     Suspend the domain's subscription
POST   /api/v1/reseller/domains/{domain}/activate    Reactivate a suspended subscription
PATCH  /api/v1/reseller/domains/{domain}/licences    Set total licences (reduce: FLEXIBLE only)
DELETE /api/v1/reseller/domains/{domain}?confirm=... Transfer to Google, release quota


COMMON ERRORS
-------------
  401  Token missing, expired, or revoked - re-login
  403  Role too low, or licence cap exceeded
  404  Job or company not found, or belongs to different reseller
  422  Missing/invalid request field - check payload in network tab
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from typing import Any, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from app.core import job_executor
from app.core.auth_middleware import (
    require_reseller_token,
    require_role,
)
from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.dependencies import (
    get_audit_repo,
    get_company_repo,
    get_domain_service,
    get_employee_repo,
    get_job_repo,
    get_provisioning_intake,
    get_reseller_repo,
    get_subscription_repo,
)
from app.models.requests import BulkProvisioningRequest, ProvisioningRequest
from app.models.reseller_models import (
    AuditLogDocument,
    ChangeLicencesRequest,
    QuotaResponse,
    ResellerDocument,
    ResellerRole,
)
from app.services.domain_service import DomainActionError
from app.services.provisioning_intake import IntakeError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/reseller", tags=["Reseller API"])


def _log_audit(
    audit_repo,
    reseller_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    licences_requested: int = 0,
    status: str = "SUCCESS",
    ip: str = "",
    details: dict = None,
) -> None:
    """Helper to write an audit log entry (best-effort, never raises)."""
    try:
        audit_repo.create(
            AuditLogDocument(
                log_id=f"LOG-{uuid.uuid4().hex[:8].upper()}",
                reseller_id=reseller_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                licences_requested=licences_requested,
                status=status,
                ip_address=ip,
                details=details or {},
            )
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# POST /provision — Create a licence / provision a company
# ---------------------------------------------------------------------------


async def _accept(make_result) -> dict:
    """Run a blocking intake call on a worker thread and translate refusals to HTTP."""
    try:
        return await asyncio.to_thread(make_result)
    except IntakeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/provision",
    status_code=202,
    summary="Provision a company (creates Google Workspace customer + subscription)",
)
@limiter.limit("120/minute")
async def reseller_provision(
    request_body: ProvisioningRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Queue provisioning for one company and return its **job_id** immediately.

    - Licences are reserved atomically, so parallel requests can never exceed your cap.
    - Only one operation per domain at a time: a second request for the same domain gets 409.
    - Send an **Idempotency-Key** header to make retries safe: the same key returns the same job.
    - Poll GET /provision/{job_id} for progress.
    """
    intake = get_provisioning_intake()
    return await _accept(lambda: intake.accept(
        reseller, request_body, idempotency_key=idempotency_key, ip=_client_ip(request)
    ))


# ---------------------------------------------------------------------------
# POST /provision/bulk — many companies in one call, processed in parallel
# ---------------------------------------------------------------------------


@router.post(
    "/provision/bulk",
    status_code=202,
    summary="Provision up to 100 companies in one call (processed in parallel)",
)
@limiter.limit("10/minute")
async def reseller_provision_bulk(
    body: BulkProvisioningRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Every item is validated and accepted on its own: one bad item does not reject the rest.

    Returns a **batch_id** and one result per item (a job_id, or the reason it was refused).
    Track everything with GET /provision/batch/{batch_id}.
    """
    intake = get_provisioning_intake()
    return await _accept(lambda: intake.accept_bulk(
        reseller, body.requests, idempotency_key=idempotency_key, ip=_client_ip(request)
    ))


@router.get(
    "/provision/batch/{batch_id}",
    summary="Progress of every job in a bulk request",
)
@limiter.limit("600/minute")
async def reseller_batch_status(
    batch_id: str,
    request: Request,
    reseller: ResellerDocument = Depends(require_reseller_token),
) -> dict:
    intake = get_provisioning_intake()
    return await _accept(lambda: intake.batch_status(reseller, batch_id))


# ---------------------------------------------------------------------------
# POST /provision/csv — CSV bulk provisioning
# ---------------------------------------------------------------------------


@router.post(
    "/provision/csv",
    status_code=202,
    summary="Provision a company via CSV upload",
)
@limiter.limit("60/minute")
async def reseller_provision_csv(
    request: Request,
    file: UploadFile = File(...),
    license_count: Optional[int] = Form(None),
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Provision a company from the first row of a CSV file (same rules as POST /provision)."""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .csv file.")

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV contains no data rows.")

    row = {k.strip().lower(): v.strip() for k, v in rows[0].items() if k and v is not None}

    # Determine licence count
    final_licence_count = (
        license_count
        or (int(row["license_count"]) if row.get("license_count", "").isdigit() else None)
        or 5
    )

    # Build provisioning request from CSV
    final_domain = row.get("primary_domain") or row.get("domain", "")
    if not final_domain:
        raise HTTPException(status_code=400, detail="primary_domain is required in CSV.")

    try:
        req = ProvisioningRequest(
            company_name=row.get("company_name", "Corporate Workspace"),
            primary_domain=final_domain,
            alternate_email=row.get("alternate_email") or row.get("admin_email") or f"admin@{final_domain}",
            contact_name=row.get("contact_name") or row.get("admin_name") or "Workspace Admin",
            postal_address={
                "address_line1": row.get("address_line1") or "Corporate Plaza",
                "locality": row.get("locality") or row.get("city") or "Bengaluru",
                "region": row.get("region") or row.get("state") or "KA",
                "postal_code": row.get("postal_code") or row.get("zip") or "560001",
                "country_code": row.get("country_code") or "IN",
            },
            plan=row.get("plan") or "FLEXIBLE",
            sku_id=row.get("sku_id") or "Google-Apps-For-Business",
            license_count=final_licence_count,
            initiated_by_email=row.get("initiated_by_email") or f"admin@{final_domain}",
            econz_notification_email=row.get("econz_notification_email") or f"admin@{final_domain}",
            admin_first_name=row.get("admin_first_name") or row.get("first_name") or "Admin",
            admin_last_name=row.get("admin_last_name") or row.get("last_name") or "User",
            admin_recovery_email=row.get("admin_recovery_email") or row.get("personal_email"),
            admin_username=row.get("admin_username"),
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    intake = get_provisioning_intake()
    result = await _accept(lambda: intake.accept(reseller, req, ip=_client_ip(request)))
    result["company_name"] = req.company_name
    return result


# ---------------------------------------------------------------------------
# GET /provision/{job_id} — Job status (own jobs only)
# ---------------------------------------------------------------------------


@router.get(
    "/provision/{job_id}",
    summary="Get provisioning job status",
)
@limiter.limit("600/minute")
async def reseller_get_job_status(
    job_id: str,
    request: Request,
    reseller: ResellerDocument = Depends(require_reseller_token),
) -> dict:
    """Check the status of a provisioning job.

    You can only view jobs that were created by your reseller account.
    """
    ip = request.client.host if request.client else ""
    job_repo = get_job_repo()
    audit_repo = get_audit_repo()

    job = job_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")

    # Enforce ownership — resellers can only see their own jobs
    if job.reseller_id != reseller.reseller_id:
        raise HTTPException(status_code=403, detail="You do not have access to this job.")

    # Get steps
    steps = job_repo.get_steps_for_job(job_id)
    step_responses = [
        {
            "step_name": s.step_name,
            "status": s.status,
            "details": s.details,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
        for s in steps
    ]

    # Get employees
    employee_repo = get_employee_repo()
    company_repo = get_company_repo()
    employees_response = []
    company = company_repo.get_by_domain(job.primary_domain)
    if company:
        employees = employee_repo.get_by_company_id(company.company_id)
        employees_response = [
            {
                "employee_id": e.employee_id,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "corporate_email": e.corporate_email,
                "status": e.status,
                "temporary_password": e.temporary_password,
            }
            for e in employees
        ]

    _log_audit(
        audit_repo,
        reseller_id=reseller.reseller_id,
        action="VIEW_STATUS",
        resource_type="job",
        resource_id=job_id,
        ip=ip,
    )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "company_name": job.company_name,
        "primary_domain": job.primary_domain,
        "google_customer_id": job.google_customer_id,
        "google_subscription_id": job.google_subscription_id,
        "plan": job.plan,
        "licensed_seats": job.licensed_seats,
        "users_created": job.users_created,
        "users_failed": job.users_failed,
        "email_status": job.email_status,
        "error_message": job.error_message,
        "steps": step_responses,
        "employees": employees_response,
    }


# ---------------------------------------------------------------------------
# GET /quota — View own licence quota
# ---------------------------------------------------------------------------


@router.get(
    "/quota",
    response_model=QuotaResponse,
    summary="View your licence quota and usage",
)
async def reseller_get_quota(
    reseller: ResellerDocument = Depends(require_reseller_token),
) -> QuotaResponse:
    """Check your current licence usage and remaining quota."""
    # Refresh from DB to get latest counts
    reseller_repo = get_reseller_repo()
    fresh = reseller_repo.get_by_id(reseller.reseller_id)
    r = fresh or reseller
    return QuotaResponse(
        reseller_id=r.reseller_id,
        company_name=r.company_name,
        max_licence_cap=r.max_licence_cap,
        licences_used=r.licences_used,
        licences_remaining=r.licences_remaining,
        utilization_percent=r.utilization_percent,
        access_methods=r.access_methods,
        contact_email=r.contact_email,
    )


# ---------------------------------------------------------------------------
# GET /companies — List own provisioned companies
# ---------------------------------------------------------------------------


@router.get(
    "/companies",
    summary="List companies provisioned by your account",
)
async def reseller_list_companies(
    reseller: ResellerDocument = Depends(require_reseller_token),
) -> List[dict]:
    """List all companies you have provisioned, with subscription details."""
    company_repo = get_company_repo()
    subscription_repo = get_subscription_repo()
    job_repo = get_job_repo()

    all_companies = company_repo.list_all()
    # Filter to this reseller's companies only
    my_companies = [c for c in all_companies if c.reseller_id == reseller.reseller_id]
    my_companies.sort(key=lambda c: c.created_at or "", reverse=True)

    subs_map = {s.company_id: s for s in subscription_repo.list_all()}
    jobs_by_domain: dict = {}
    for j in job_repo.list_all_jobs():
        existing = jobs_by_domain.get(j.primary_domain)
        if not existing or (j.created_at and existing.created_at and j.created_at > existing.created_at):
            jobs_by_domain[j.primary_domain] = j

    result = []
    for c in my_companies:
        sub = subs_map.get(c.company_id)
        job = jobs_by_domain.get(c.primary_domain)
        result.append({
            "company_id": c.company_id,
            "company_name": c.company_name,
            "primary_domain": c.primary_domain,
            "google_customer_id": c.google_customer_id,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "licensed_seats": sub.seats if sub else None,
            "plan": sub.plan if sub else None,
            "sku_id": sub.sku_id if sub else None,
            "subscription_status": sub.status if sub else None,
            "email_status": job.email_status if job else None,
        })
    return result


# ---------------------------------------------------------------------------
# GET /companies/{company_id} — Company detail (own only)
# ---------------------------------------------------------------------------


@router.get(
    "/companies/{company_id}",
    summary="Get company detail",
)
async def reseller_get_company(
    company_id: str,
    reseller: ResellerDocument = Depends(require_reseller_token),
) -> dict:
    """Get details for a specific company you provisioned."""
    company_repo = get_company_repo()
    subscription_repo = get_subscription_repo()
    employee_repo = get_employee_repo()

    company = company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail=f"Company {company_id} not found.")

    # Enforce ownership
    if company.reseller_id != reseller.reseller_id:
        raise HTTPException(status_code=403, detail="You do not have access to this company.")

    subs = subscription_repo.get_by_company_id(company_id)
    sub = subs[0] if subs else None
    employees = employee_repo.get_by_company_id(company_id)

    return {
        "company_id": company.company_id,
        "company_name": company.company_name,
        "primary_domain": company.primary_domain,
        "alternate_email": company.alternate_email,
        "google_customer_id": company.google_customer_id,
        "status": company.status,
        "created_at": company.created_at.isoformat() if company.created_at else None,
        "subscription": {
            "subscription_id": sub.subscription_id if sub else None,
            "plan": sub.plan if sub else None,
            "sku_id": sub.sku_id if sub else None,
            "seats": sub.seats if sub else None,
            "status": sub.status if sub else None,
        } if sub else None,
        "employees": [
            {
                "employee_id": e.employee_id,
                "first_name": e.first_name,
                "last_name": e.last_name,
                "corporate_email": e.corporate_email,
                "status": e.status,
            }
            for e in employees
        ],
    }


# ---------------------------------------------------------------------------
# Domain management — suspend / activate / delete / change licences
# (business rules live in app/services/domain_service.py)
# ---------------------------------------------------------------------------


async def _run_domain_action(coro) -> dict:
    """Run on a worker thread: Google/Firestore calls block, and must not stall other requests."""
    try:
        return await job_executor.run_in_worker(lambda: coro)
    except DomainActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/domains/{domain}/suspend", summary="Suspend a domain's Google Workspace subscription")
@limiter.limit("60/minute")
async def suspend_domain(
    domain: str,
    request: Request,
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Users lose access; data and licences are kept until you activate or delete."""
    return await _run_domain_action(get_domain_service().suspend(domain, reseller, _client_ip(request)))


@router.post("/domains/{domain}/activate", summary="Reactivate a suspended domain")
@limiter.limit("60/minute")
async def activate_domain(
    domain: str,
    request: Request,
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    return await _run_domain_action(get_domain_service().activate(domain, reseller, _client_ip(request)))


@router.patch("/domains/{domain}/licences", summary="Change the total licences on a domain")
@limiter.limit("60/minute")
async def change_domain_licences(
    domain: str,
    body: ChangeLicencesRequest,
    request: Request,
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Set a new TOTAL. Increases work on any plan; decreases only on the FLEXIBLE plan."""
    return await _run_domain_action(
        get_domain_service().change_licences(domain, reseller, body.license_count, _client_ip(request))
    )


@router.delete("/domains/{domain}", summary="Remove a domain from your account (transfers it to Google)")
@limiter.limit("60/minute")
async def delete_domain(
    domain: str,
    request: Request,
    confirm: Optional[str] = Query(None, description="Repeat the domain name to confirm"),
    reseller: ResellerDocument = Depends(require_role(ResellerRole.RESELLER_FULL)),
) -> dict:
    """Transfers the domain's subscriptions to Google (Google no longer allows resellers to cancel
    Workspace subscriptions). The customer is billed directly by Google from then on, you stop being
    billed, and the licences return to your quota. Cannot be undone."""
    return await _run_domain_action(get_domain_service().delete(domain, reseller, confirm, _client_ip(request)))
