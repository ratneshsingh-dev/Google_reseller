"""
Provisioning API routes.

POST /api/v1/provision       — JSON provisioning request
POST /api/v1/provision/csv   — CSV bulk provisioning
GET  /api/v1/provision/{id}  — Job status
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Form,
    Header,
    HTTPException,
    UploadFile,
    Request,
    File,
)

from app.core.rate_limit import limiter

from app.dependencies import get_job_repo, get_provisioning_service
from app.models.database import JobStatus, ProvisioningJobDocument
from app.models.requests import ProvisioningRequest
from app.models.responses import (
    CsvUploadResponse,
    EmployeeStatusResponse,
    ErrorResponse,
    ProvisioningJobResponse,
    ProvisioningStepResponse,
    ProvisioningStatusResponse,
    SingleCompanyCsvResponse,
)
from app.workers import run_provisioning_job

router = APIRouter(prefix="/api/v1", tags=["Provisioning"])


@router.post(
    "/provision",
    response_model=ProvisioningJobResponse,
    status_code=202,
    summary="Submit a company provisioning request",
    responses={409: {"model": ErrorResponse}},
)
@limiter.limit("20/minute")
async def provision_company(
    request: ProvisioningRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Accept a provisioning request, create a job, and process in background."""
    import asyncio

    job_repo = get_job_repo()

    # Check idempotency
    if idempotency_key:
        existing = job_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            return ProvisioningJobResponse(
                job_id=existing.job_id,
                status=existing.status,
            )

    # Check domain-level duplicate — only block on ACTIVE jobs (not stale PENDING)
    active_statuses = (
        JobStatus.VALIDATING.value,
        JobStatus.CUSTOMER_CREATING.value,
        JobStatus.CUSTOMER_CREATED.value,
        JobStatus.SUBSCRIPTION_CREATING.value,
        JobStatus.SUBSCRIPTION_CREATED.value,
        JobStatus.USER_PROVISIONING.value,
        JobStatus.EMAIL_SENDING.value,
    )
    existing_jobs = [
        j
        for j in job_repo.list_all_jobs()
        if j.primary_domain == request.primary_domain
        and j.status in active_statuses
    ]
    if existing_jobs:
        return ProvisioningJobResponse(
            job_id=existing_jobs[0].job_id,
            status=existing_jobs[0].status,
        )

    # Create job
    job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
    job = ProvisioningJobDocument(
        job_id=job_id,
        idempotency_key=idempotency_key,
        company_name=request.company_name,
        primary_domain=request.primary_domain,
        status=JobStatus.PENDING.value,
    )
    job_repo.create_job(job)

    # Dispatch via asyncio.create_task (survives after response on Cloud Run)
    provisioning_service = get_provisioning_service()
    asyncio.create_task(
        run_provisioning_job(
            provisioning_service,
            job_repo,
            job_id,
            request,
        )
    )

    return ProvisioningJobResponse(job_id=job_id, status="PENDING")


@router.post(
    "/provision/csv",
    response_model=SingleCompanyCsvResponse,
    status_code=202,
    summary="Provision a single company via CSV upload (enterprise admin-only flow)",
)
async def provision_csv(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="CSV file with company and admin data"),
    company_name: Optional[str] = Form(None),
    primary_domain: Optional[str] = Form(None),
    alternate_email: Optional[str] = Form(None),
    contact_name: Optional[str] = Form(None),
    plan: Optional[str] = Form(None),
    sku_id: Optional[str] = Form(None),
    license_count: Optional[int] = Form(None),
    admin_first_name: Optional[str] = Form(None),
    admin_last_name: Optional[str] = Form(None),
    admin_recovery_email: Optional[str] = Form(None),
    initiated_by_email: Optional[str] = Form(None),
    econz_notification_email: Optional[str] = Form(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Parse a CSV file with company + admin data and initiate provisioning."""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .csv file")

    content = await file.read()
    text = content.decode("utf-8-sig")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="CSV file is empty")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no valid headers")

    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV contains no data rows")

    # Use the first row for company + admin data
    row = {k.strip().lower(): v.strip() for k, v in rows[0].items() if k and v is not None}

    csv_data = {
        "company_name": row.get("company_name"),
        "primary_domain": row.get("primary_domain") or row.get("domain"),
        "alternate_email": row.get("alternate_email") or row.get("admin_email"),
        "contact_name": row.get("contact_name") or row.get("admin_name"),
        "address_line1": row.get("address_line1") or row.get("address") or "Corporate Plaza",
        "locality": row.get("locality") or row.get("city") or "Bengaluru",
        "region": row.get("region") or row.get("state") or "KA",
        "postal_code": row.get("postal_code") or row.get("zip") or "560001",
        "country_code": row.get("country_code") or row.get("country") or "IN",
        "plan": row.get("plan"),
        "sku_id": row.get("sku_id"),
        "license_count": int(row["license_count"]) if row.get("license_count") and row["license_count"].isdigit() else None,
        "admin_first_name": row.get("admin_first_name") or row.get("first_name"),
        "admin_last_name": row.get("admin_last_name") or row.get("last_name"),
        "admin_recovery_email": row.get("admin_recovery_email") or row.get("personal_email") or row.get("email"),
        "initiated_by_email": row.get("initiated_by_email"),
        "econz_notification_email": row.get("econz_notification_email"),
    }

    # Merge: form fields take precedence, then CSV
    final_domain = primary_domain or csv_data.get("primary_domain")
    if not final_domain:
        raise HTTPException(status_code=400, detail="Company primary domain is required.")

    final_admin_first = admin_first_name or csv_data.get("admin_first_name")
    final_admin_last = admin_last_name or csv_data.get("admin_last_name")
    if not final_admin_first or not final_admin_last:
        raise HTTPException(status_code=400, detail="Admin first name and last name are required.")

    final_alt_email = alternate_email or csv_data.get("alternate_email") or f"admin@{final_domain}"

    try:
        req = ProvisioningRequest(
            company_name=company_name or csv_data.get("company_name") or "Corporate Workspace",
            primary_domain=final_domain,
            alternate_email=final_alt_email,
            contact_name=contact_name or csv_data.get("contact_name") or "Workspace Admin",
            postal_address={
                "address_line1": csv_data.get("address_line1") or "Corporate Plaza",
                "locality": csv_data.get("locality") or "Bengaluru",
                "region": csv_data.get("region") or "KA",
                "postal_code": csv_data.get("postal_code") or "560001",
                "country_code": csv_data.get("country_code") or "IN",
            },
            plan=plan or csv_data.get("plan") or "FLEXIBLE",
            sku_id=sku_id or csv_data.get("sku_id") or "SKU-BUSINESS-STANDARD",
            license_count=license_count or csv_data.get("license_count") or 5,
            initiated_by_email=initiated_by_email or csv_data.get("initiated_by_email") or final_alt_email,
            econz_notification_email=econz_notification_email or csv_data.get("econz_notification_email") or "econz-notifications@example.net",
            admin_first_name=final_admin_first,
            admin_last_name=final_admin_last,
            admin_recovery_email=admin_recovery_email or csv_data.get("admin_recovery_email"),
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    job_repo = get_job_repo()
    provisioning_service = get_provisioning_service()

    job_id = f"JOB-{uuid.uuid4().hex[:8].upper()}"
    job = ProvisioningJobDocument(
        job_id=job_id,
        idempotency_key=idempotency_key,
        company_name=req.company_name,
        primary_domain=req.primary_domain,
        status=JobStatus.PENDING.value,
    )
    job_repo.create_job(job)

    background_tasks.add_task(
        run_provisioning_job,
        provisioning_service,
        job_repo,
        job_id,
        req,
    )

    return SingleCompanyCsvResponse(
        job_id=job_id,
        status="PENDING",
        company_name=req.company_name,
        primary_domain=req.primary_domain,
        employee_count=1,
        employees=[{"first_name": final_admin_first, "last_name": final_admin_last, "personal_email": req.admin_recovery_email}],
        message=f"Admin account provisioning queued for {req.company_name} ({final_admin_first} {final_admin_last}@{final_domain})",
    )


@router.get(
    "/provision/{job_id}",
    response_model=ProvisioningStatusResponse,
    summary="Get provisioning job status",
)
async def get_provision_status(job_id: str):
    """Return the full status of a provisioning job."""
    job_repo = get_job_repo()
    job = job_repo.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # Get steps
    steps = job_repo.get_steps_for_job(job_id)
    step_responses = [
        ProvisioningStepResponse(
            step_name=s.step_name,
            status=s.status,
            details=s.details,
            started_at=s.started_at,
            completed_at=s.completed_at,
        )
        for s in steps
    ]

    # Get employees
    from app.dependencies import get_employee_repo, get_company_repo

    employee_repo = get_employee_repo()
    company_repo = get_company_repo()

    employees_response = []
    company = company_repo.get_by_domain(job.primary_domain)
    if company:
        employees = employee_repo.get_by_company_id(company.company_id)
        employees_response = [
            EmployeeStatusResponse(
                employee_id=e.employee_id,
                first_name=e.first_name,
                last_name=e.last_name,
                corporate_email=e.corporate_email,
                personal_email=e.personal_email,
                google_user_id=e.google_user_id,
                status=e.status,
                temporary_password=e.temporary_password,
            )
            for e in employees
        ]

    return ProvisioningStatusResponse(
        job_id=job.job_id,
        status=job.status,
        company_name=job.company_name,
        primary_domain=job.primary_domain,
        google_customer_id=job.google_customer_id,
        google_subscription_id=job.google_subscription_id,
        plan=job.plan,
        sku_id=job.sku_id,
        licensed_seats=job.licensed_seats,
        users_created=job.users_created,
        users_failed=job.users_failed,
        users_existing=job.users_existing,
        steps=step_responses,
        employees=employees_response,
        email_status=job.email_status,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
