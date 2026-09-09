"""
Company API routes.

GET /api/v1/companies                   — List all companies
GET /api/v1/companies/{company_id}      — Company detail
GET /api/v1/companies/{company_id}/users — Company employees
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dependencies import (
    get_company_repo,
    get_employee_repo,
    get_job_repo,
    get_subscription_repo,
)
from app.models.responses import (
    CompanyDetailResponse,
    CompanyResponse,
    EmployeeResponse,
)

router = APIRouter(prefix="/api/v1/companies", tags=["Companies"])


@router.get(
    "",
    response_model=list[CompanyDetailResponse],
    summary="List all provisioned companies with details",
)
async def list_companies():
    company_repo = get_company_repo()
    subscription_repo = get_subscription_repo()
    job_repo = get_job_repo()

    companies = company_repo.list_all()
    subs = subscription_repo.list_all()
    jobs = job_repo.list_all_jobs()

    subs_by_company = {s.company_id: s for s in subs}
    
    # Get latest job per domain for email status
    jobs_by_domain = {}
    for j in jobs:
        existing = jobs_by_domain.get(j.primary_domain)
        if not existing or (j.created_at and existing.created_at and j.created_at > existing.created_at):
            jobs_by_domain[j.primary_domain] = j

    # Sort companies by created_at descending (newest first)
    companies.sort(key=lambda c: c.created_at or "", reverse=True)
    
    result = []
    for c in companies:
        sub = subs_by_company.get(c.company_id)
        job = jobs_by_domain.get(c.primary_domain)
        result.append(
            CompanyDetailResponse(
                company_id=c.company_id,
                company_name=c.company_name,
                primary_domain=c.primary_domain,
                google_customer_id=c.google_customer_id,
                status=c.status,
                created_at=c.created_at,
                alternate_email=c.alternate_email,
                subscription_id=sub.subscription_id if sub else None,
                google_subscription_id=sub.google_subscription_id if sub else None,
                plan=sub.plan if sub else None,
                sku_id=sub.sku_id if sub else None,
                seats=sub.seats if sub else None,
                subscription_status=sub.status if sub else None,
                email_status=job.email_status if job else None,
            )
        )
    return result


@router.get(
    "/{company_id}",
    response_model=CompanyDetailResponse,
    summary="Get company detail with subscription info",
)
async def get_company(company_id: str):
    company_repo = get_company_repo()
    subscription_repo = get_subscription_repo()

    company = company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=404, detail=f"Company {company_id} not found"
        )

    # Get subscription
    subs = subscription_repo.get_by_company_id(company_id)
    sub = subs[0] if subs else None

    # Get email status from latest job
    job_repo = get_job_repo()
    jobs = [j for j in job_repo.list_all_jobs() if j.primary_domain == company.primary_domain]
    latest_job = sorted(jobs, key=lambda x: x.created_at or "", reverse=True)[0] if jobs else None

    return CompanyDetailResponse(
        company_id=company.company_id,
        company_name=company.company_name,
        primary_domain=company.primary_domain,
        google_customer_id=company.google_customer_id,
        status=company.status,
        created_at=company.created_at,
        alternate_email=company.alternate_email,
        subscription_id=sub.subscription_id if sub else None,
        google_subscription_id=sub.google_subscription_id if sub else None,
        plan=sub.plan if sub else None,
        sku_id=sub.sku_id if sub else None,
        seats=sub.seats if sub else None,
        subscription_status=sub.status if sub else None,
        email_status=latest_job.email_status if latest_job else None,
    )


@router.get(
    "/{company_id}/users",
    response_model=list[EmployeeResponse],
    summary="List employees for a company",
)
async def list_company_users(company_id: str):
    company_repo = get_company_repo()
    employee_repo = get_employee_repo()

    company = company_repo.get_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=404, detail=f"Company {company_id} not found"
        )

    employees = employee_repo.get_by_company_id(company_id)
    return [
        EmployeeResponse(
            employee_id=e.employee_id,
            company_id=e.company_id,
            first_name=e.first_name,
            last_name=e.last_name,
            personal_email=e.personal_email,
            corporate_email=e.corporate_email,
            google_user_id=e.google_user_id,
            status=e.status,
            created_at=e.created_at,
        )
        for e in employees
    ]
