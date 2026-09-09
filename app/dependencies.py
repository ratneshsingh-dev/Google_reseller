"""
FastAPI dependency injection.

Controls which adapter implementations are used based on SERVICE_ADAPTER env var.
All services are constructed through these dependency functions, ensuring
the provisioning orchestrator never directly imports concrete adapters.
"""

from __future__ import annotations

from functools import lru_cache

from app.adapters.mock.mock_directory import MockDirectoryService
from app.adapters.mock.mock_email import MockEmailService
from app.adapters.mock.mock_reseller import MockResellerService
from app.core.config import get_settings
from app.repositories.audit_repository import AuditRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.firestore_client import get_store
from app.repositories.job_repository import JobRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.reseller_repository import ResellerRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.directory_service import DirectoryService
from app.services.email_service import EmailService
from app.services.provisioning_service import ProvisioningService
from app.services.reseller_service import ResellerService


# ---------------------------------------------------------------------------
# Singleton service instances
# ---------------------------------------------------------------------------

_reseller_service: ResellerService | None = None
_directory_service: DirectoryService | None = None
_email_service: EmailService | None = None


def get_reseller_service() -> ResellerService:
    """Get the reseller service (mock or Google based on config)."""
    global _reseller_service
    if _reseller_service is None:
        settings = get_settings()
        if settings.service_adapter == "google":
            from app.adapters.google.google_reseller import GoogleResellerService
            _reseller_service = GoogleResellerService()
        else:
            _reseller_service = MockResellerService()
    return _reseller_service


def get_directory_service() -> DirectoryService:
    """Get the directory service (mock or Google based on config)."""
    global _directory_service
    if _directory_service is None:
        settings = get_settings()
        if settings.service_adapter == "google":
            from app.adapters.google.google_directory import GoogleDirectoryService
            _directory_service = GoogleDirectoryService()
        else:
            _directory_service = MockDirectoryService()
    return _directory_service


def get_email_service() -> EmailService:
    """Get the email service (mock or Gmail based on config)."""
    global _email_service
    if _email_service is None:
        settings = get_settings()
        if settings.service_adapter == "google":
            from app.adapters.google.gmail_email import GmailEmailService
            _email_service = GmailEmailService()
        else:
            _email_service = MockEmailService(get_notification_repo())
    return _email_service


# ---------------------------------------------------------------------------
# Repository dependencies
# ---------------------------------------------------------------------------


@lru_cache()
def get_company_repo() -> CompanyRepository:
    return CompanyRepository(get_store())


@lru_cache()
def get_subscription_repo() -> SubscriptionRepository:
    return SubscriptionRepository(get_store())


@lru_cache()
def get_employee_repo() -> EmployeeRepository:
    return EmployeeRepository(get_store())


@lru_cache()
def get_job_repo() -> JobRepository:
    return JobRepository(get_store())


@lru_cache()
def get_notification_repo() -> NotificationRepository:
    return NotificationRepository(get_store())


@lru_cache()
def get_reseller_repo() -> ResellerRepository:
    return ResellerRepository(get_store())


@lru_cache()
def get_audit_repo() -> AuditRepository:
    return AuditRepository(get_store())


# ---------------------------------------------------------------------------
# Provisioning service (orchestrator)
# ---------------------------------------------------------------------------


def get_provisioning_service() -> ProvisioningService:
    """Build the provisioning service with all dependencies injected."""
    return ProvisioningService(
        reseller_service=get_reseller_service(),
        directory_service=get_directory_service(),
        email_service=get_email_service(),
        company_repo=get_company_repo(),
        subscription_repo=get_subscription_repo(),
        employee_repo=get_employee_repo(),
        job_repo=get_job_repo(),
        notification_repo=get_notification_repo(),
    )


def reset_dependencies() -> None:
    """Reset all singletons — for testing only."""
    global _reseller_service, _directory_service, _email_service
    _reseller_service = None
    _directory_service = None
    _email_service = None
    get_company_repo.cache_clear()
    get_subscription_repo.cache_clear()
    get_employee_repo.cache_clear()
    get_job_repo.cache_clear()
    get_notification_repo.cache_clear()
    get_reseller_repo.cache_clear()
    get_audit_repo.cache_clear()
