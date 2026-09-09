"""
Provisioning Orchestrator — the core business logic.

This service coordinates the full company onboarding workflow:
  1. Validate input
  2. Create/retrieve customer
  3. Create/retrieve subscription
  4. Reconcile seats if needed
  5. Generate corporate emails
  6. Create user accounts (with retry)
  7. Send confirmation emails
  8. Track job status through state machine

IMPORTANT: This service is idempotent. Re-running the same request
will skip already-completed steps and only retry failed operations.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.exceptions import (
    CustomerAlreadyExistsError,
    DuplicateEmployeeError,
    InsufficientSeatsError,
    RetryableError,
    SubscriptionAlreadyExistsError,
)
from app.core.logging import get_logger
from app.core.security import (
    generate_corporate_email,
    generate_temporary_password,
    hash_password,
    redact_password,
)
from app.models.database import (
    CompanyDocument,
    EmployeeDocument,
    EmployeeStatus,
    JobStatus,
    NotificationDocument,
    NotificationStatus,
    ProvisioningJobDocument,
    ProvisioningStepDocument,
    StepStatus,
    SubscriptionDocument,
)
from app.models.google_api import (
    GoogleCreateCustomerRequest,
    GoogleCreateSubscriptionRequest,
    GoogleCreateUserRequest,
    GooglePostalAddress,
    GoogleSubscriptionPlan,
    GoogleSubscriptionSeats,
    GoogleUserName,
)
from app.models.requests import ProvisioningRequest
from app.repositories.company_repository import CompanyRepository
from app.repositories.employee_repository import EmployeeRepository
from app.repositories.job_repository import JobRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.directory_service import DirectoryService
from app.services.email_service import EmailService
from app.services.reseller_service import ResellerService

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # seconds
GOOGLE_MAX_SEATS_PER_CALL = 100  # Google Reseller API seat limit per call


class ProvisioningService:
    """Orchestrates the full company provisioning workflow."""

    def __init__(
        self,
        reseller_service: ResellerService,
        directory_service: DirectoryService,
        email_service: EmailService,
        company_repo: CompanyRepository,
        subscription_repo: SubscriptionRepository,
        employee_repo: EmployeeRepository,
        job_repo: JobRepository,
        notification_repo: NotificationRepository,
    ) -> None:
        self._reseller = reseller_service
        self._directory = directory_service
        self._email = email_service
        self._company_repo = company_repo
        self._subscription_repo = subscription_repo
        self._employee_repo = employee_repo
        self._job_repo = job_repo
        self._notification_repo = notification_repo

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def provision_company(
        self, request: ProvisioningRequest, job_id: str, reseller_id: str | None = None
    ) -> None:
        """Execute the full provisioning workflow for a company.

        This method is designed to be called by the background worker.
        It updates the job document in Firestore as it progresses.
        reseller_id — optional, set when called from the Reseller API.
        """
        try:
            # --- Step 1: Validate ---
            await self._update_job(job_id, JobStatus.VALIDATING)
            step_id = await self._start_step(job_id, "VALIDATE_INPUT")

            if request.license_count < 1:
                raise InsufficientSeatsError(
                    f"license_count ({request.license_count}) must be >= 1"
                )
            await self._complete_step(step_id)

            # --- Step 2: Create/retrieve customer ---
            await self._update_job(job_id, JobStatus.CUSTOMER_CREATING)
            step_id = await self._start_step(job_id, "CREATE_CUSTOMER")

            customer = await self._ensure_customer(request)
            google_customer_id = customer.customer_id

            # Save company record
            company_id = f"COMP-{uuid.uuid4().hex[:8].upper()}"
            existing_company = self._company_repo.get_by_domain(
                request.primary_domain
            )
            if existing_company:
                company_id = existing_company.company_id
                self._company_repo.update(
                    company_id,
                    {"google_customer_id": google_customer_id},
                )
            else:
                self._company_repo.create(
                    CompanyDocument(
                        company_id=company_id,
                        company_name=request.company_name,
                        primary_domain=request.primary_domain,
                        alternate_email=request.alternate_email,
                        contact_name=request.contact_name,
                        google_customer_id=google_customer_id,
                        reseller_id=reseller_id,
                    )
                )

            await self._complete_step(
                step_id, f"Customer: {google_customer_id}"
            )
            await self._update_job(
                job_id,
                JobStatus.CUSTOMER_CREATED,
                google_customer_id=google_customer_id,
                company_name=request.company_name,
                primary_domain=request.primary_domain,
            )

            # --- Step 3: Create/retrieve subscription with seat batching ---
            await self._update_job(job_id, JobStatus.SUBSCRIPTION_CREATING)
            step_id = await self._start_step(job_id, "CREATE_SUBSCRIPTION")

            subscription = await self._ensure_subscription_with_batching(
                google_customer_id, request
            )
            google_sub_id = subscription.subscription_id

            # Save subscription record
            existing_subs = self._subscription_repo.get_by_company_id(company_id)
            if not existing_subs:
                self._subscription_repo.create(
                    SubscriptionDocument(
                        subscription_id=f"LSUB-{uuid.uuid4().hex[:8].upper()}",
                        company_id=company_id,
                        google_subscription_id=google_sub_id,
                        sku_id=request.sku_id,
                        plan=request.plan,
                        seats=request.license_count,
                        billing_method="MOCK",
                    )
                )
            else:
                self._subscription_repo.update(
                    existing_subs[0].subscription_id,
                    {
                        "google_subscription_id": google_sub_id,
                        "seats": request.license_count,
                        "plan": request.plan,
                    },
                )

            await self._complete_step(
                step_id, f"Subscription: {google_sub_id} | Seats: {request.license_count}"
            )
            await self._update_job(
                job_id,
                JobStatus.SUBSCRIPTION_CREATED,
                google_subscription_id=google_sub_id,
                plan=request.plan,
                sku_id=request.sku_id,
                licensed_seats=request.license_count,
            )

            # --- Step 4: Create admin user (optional — may fail for new reseller customers) ---
            await self._update_job(job_id, JobStatus.USER_PROVISIONING)
            step_id = await self._start_step(job_id, "CREATE_USERS")

            users_created = 0
            users_failed = 0
            users_existing = 0

            try:
                # Enterprise flow: only create 1 admin account
                admin_employee = request.admin_as_employee
                admin_result = await self._provision_single_user(
                    company_id, request.primary_domain, admin_employee, set(), is_admin=True
                )

                users_created = 1 if admin_result == EmployeeStatus.PROVISIONED else 0
                users_failed = 1 if admin_result == EmployeeStatus.FAILED else 0
                users_existing = 1 if admin_result == EmployeeStatus.EXISTING else 0

                await self._complete_step(
                    step_id,
                    f"Admin: {request.admin_first_name}.{request.admin_last_name}@{request.primary_domain} "
                    f"| Status: {admin_result.value}",
                )
            except Exception as user_exc:
                logger.warning(
                    "admin_user_creation_skipped",
                    job_id=job_id,
                    reason=str(user_exc),
                )
                await self._complete_step(
                    step_id,
                    f"Skipped — customer must complete setup first: {str(user_exc)[:100]}",
                )

            self._job_repo.update_job_status(
                job_id,
                JobStatus.USER_PROVISIONING.value,
                users_created=users_created,
                users_failed=users_failed,
                users_existing=users_existing,
            )

            # --- Step 5: Send confirmation emails (best-effort) ---
            await self._update_job(job_id, JobStatus.EMAIL_SENDING)
            step_id = await self._start_step(job_id, "SEND_EMAILS")

            try:
                email_status = await self._send_notifications(
                    job_id, company_id, request, google_customer_id, google_sub_id,
                    users_created, users_failed, users_existing,
                )
            except Exception as email_exc:
                logger.warning(
                    "email_sending_skipped",
                    job_id=job_id,
                    reason=str(email_exc),
                )
                email_status = "SKIPPED"

            await self._complete_step(step_id, f"Email status: {email_status}")

            # --- Determine final status ---
            # Customer + Subscription created = success, even if user/email steps were skipped
            final_status = JobStatus.COMPLETED

            await self._update_job(
                job_id,
                final_status,
                email_status=email_status,
            )

            logger.info(
                "provisioning_complete",
                job_id=job_id,
                status=final_status.value,
                users_created=users_created,
                users_failed=users_failed,
            )

        except Exception as exc:
            logger.error(
                "provisioning_failed",
                job_id=job_id,
                error=str(exc),
                exc_info=True,
            )
            self._job_repo.update_job_status(
                job_id,
                JobStatus.FAILED.value,
                error_message=str(exc),
            )
            raise

    # ------------------------------------------------------------------
    # Customer management
    # ------------------------------------------------------------------

    async def _ensure_customer(self, request: ProvisioningRequest):
        """Create or retrieve existing customer (idempotent)."""
        # Check if customer already exists by domain
        existing = await self._reseller.get_customer_by_domain(
            request.primary_domain
        )
        if existing:
            logger.info(
                "customer_already_exists",
                domain=request.primary_domain,
                customer_id=existing.customer_id,
            )
            return existing

        # Create new customer
        try:
            return await self._retry_operation(
                self._reseller.create_customer,
                GoogleCreateCustomerRequest(
                    customerDomain=request.primary_domain,
                    customerType="domain",
                    postalAddress=GooglePostalAddress(
                        contactName=request.contact_name,
                        organizationName=request.company_name,
                        addressLine1=request.postal_address.address_line1,
                        locality=request.postal_address.locality,
                        region=request.postal_address.region,
                        postalCode=request.postal_address.postal_code,
                        countryCode=request.postal_address.country_code,
                    ),
                    alternateEmail=request.alternate_email,
                ),
            )
        except CustomerAlreadyExistsError as exc:
            # Race condition: customer was created between our check and create
            if exc.customer_id:
                return await self._reseller.get_customer(exc.customer_id)
            # Try again to find by domain
            return await self._reseller.get_customer_by_domain(
                request.primary_domain
            )

    # ------------------------------------------------------------------
    # Subscription management (with Google API seat batching)
    # ------------------------------------------------------------------

    async def _ensure_subscription_with_batching(
        self, customer_id: str, request: ProvisioningRequest
    ):
        """Create or retrieve subscription, scaling seats in batches of 100.

        Google's Reseller API limits seats per call to ~100.
        We create the subscription with up to 100 seats, then use
        changeSeats() to scale up in increments until we reach the target.
        """
        from app.models.google_api import GoogleChangSeatsRequest

        total_needed = request.license_count

        # Check existing subscriptions first (idempotent)
        existing_subs = await self._reseller.list_subscriptions(customer_id)
        for sub in existing_subs:
            if sub.sku_id == request.sku_id:
                logger.info(
                    "subscription_already_exists",
                    subscription_id=sub.subscription_id,
                    sku_id=request.sku_id,
                )
                # Scale up if needed
                current = sub.seats.number_of_seats
                if current < total_needed:
                    sub = await self._scale_seats(
                        customer_id, sub.subscription_id, current, total_needed
                    )
                return sub

        # Create new subscription with first batch (max 100)
        initial_batch = min(total_needed, GOOGLE_MAX_SEATS_PER_CALL)
        try:
            subscription = await self._retry_operation(
                self._reseller.create_subscription,
                customer_id,
                GoogleCreateSubscriptionRequest(
                    skuId=request.sku_id,
                    plan=GoogleSubscriptionPlan(planName=request.plan),
                    seats=GoogleSubscriptionSeats(
                        numberOfSeats=initial_batch,
                        licensedNumberOfSeats=initial_batch,
                    ),
                    customerDomain=request.primary_domain,
                ),
            )
        except SubscriptionAlreadyExistsError as exc:
            if exc.subscription_id:
                subscription = await self._reseller.get_subscription(
                    customer_id, exc.subscription_id
                )
            else:
                subs = await self._reseller.list_subscriptions(customer_id)
                subscription = next((s for s in subs if s.sku_id == request.sku_id), None)
                if not subscription:
                    raise

        # Scale from initial_batch up to total_needed in batches
        current_seats = subscription.seats.number_of_seats
        if current_seats < total_needed:
            subscription = await self._scale_seats(
                customer_id, subscription.subscription_id, current_seats, total_needed
            )

        return subscription

    async def _scale_seats(
        self,
        customer_id: str,
        subscription_id: str,
        current_seats: int,
        target_seats: int,
    ):
        """Scale subscription seats up to target in increments of GOOGLE_MAX_SEATS_PER_CALL."""
        from app.models.google_api import GoogleChangSeatsRequest

        subscription = None
        while current_seats < target_seats:
            next_target = min(current_seats + GOOGLE_MAX_SEATS_PER_CALL, target_seats)
            logger.info(
                "scaling_seats",
                customer_id=customer_id,
                from_seats=current_seats,
                to_seats=next_target,
                final_target=target_seats,
            )
            subscription = await self._retry_operation(
                self._reseller.change_seats,
                customer_id,
                subscription_id,
                GoogleChangSeatsRequest(
                    seats=GoogleSubscriptionSeats(
                        numberOfSeats=next_target,
                        licensedNumberOfSeats=next_target,
                    )
                ),
            )
            current_seats = next_target

        return subscription

    async def _ensure_subscription(
        self, customer_id: str, request: ProvisioningRequest
    ):
        """Legacy: Create or retrieve existing subscription (idempotent, no batching)."""
        existing_subs = await self._reseller.list_subscriptions(customer_id)
        for sub in existing_subs:
            if sub.sku_id == request.sku_id:
                logger.info(
                    "subscription_already_exists",
                    subscription_id=sub.subscription_id,
                    sku_id=request.sku_id,
                )
                if sub.seats.number_of_seats < request.license_count:
                    from app.models.google_api import GoogleChangSeatsRequest
                    sub = await self._reseller.change_seats(
                        customer_id,
                        sub.subscription_id,
                        GoogleChangSeatsRequest(
                            seats=GoogleSubscriptionSeats(
                                numberOfSeats=request.license_count,
                                licensedNumberOfSeats=request.license_count,
                            )
                        ),
                    )
                return sub

        try:
            return await self._retry_operation(
                self._reseller.create_subscription,
                customer_id,
                GoogleCreateSubscriptionRequest(
                    skuId=request.sku_id,
                    plan=GoogleSubscriptionPlan(planName=request.plan),
                    seats=GoogleSubscriptionSeats(
                        numberOfSeats=request.license_count,
                        licensedNumberOfSeats=request.license_count,
                    ),
                    customerDomain=request.primary_domain,
                ),
            )
        except SubscriptionAlreadyExistsError as exc:
            if exc.subscription_id:
                return await self._reseller.get_subscription(
                    customer_id, exc.subscription_id
                )
            subs = await self._reseller.list_subscriptions(customer_id)
            for sub in subs:
                if sub.sku_id == request.sku_id:
                    return sub
            raise

    # ------------------------------------------------------------------
    # User provisioning
    # ------------------------------------------------------------------

    async def _provision_users(
        self,
        company_id: str,
        domain: str,
        employees: list,
    ) -> list[EmployeeStatus]:
        """Create user accounts for all employees."""
        # Gather existing corporate emails for dedup
        existing_employees = self._employee_repo.get_by_company_id(company_id)
        existing_emails: set[str] = {
            e.corporate_email.lower() for e in existing_employees
        }

        # Also check existing users in the directory
        try:
            existing_users = await self._directory.list_users(domain)
            for u in existing_users:
                existing_emails.add(u.primary_email.lower())
        except Exception:
            pass  # Non-critical — we'll catch dupes at creation time

        results: list[EmployeeStatus] = []

        for emp in employees:
            result = await self._provision_single_user(
                company_id, domain, emp, existing_emails, is_admin=False
            )
            results.append(result)

        return results

    async def _provision_single_user(
        self,
        company_id: str,
        domain: str,
        employee,
        existing_emails: set[str],
        is_admin: bool = False,
    ) -> EmployeeStatus:
        """Provision a single user with retry logic."""
        # Generate corporate email
        corporate_email = generate_corporate_email(
            employee.first_name, employee.last_name, domain, existing_emails
        )

        # Check if already provisioned in our DB
        existing = self._employee_repo.get_by_email(corporate_email)
        if existing and existing.status == EmployeeStatus.PROVISIONED.value:
            logger.info(
                "employee_already_provisioned",
                email=corporate_email,
            )
            return EmployeeStatus.EXISTING

        # Check if user exists in directory
        existing_user = await self._directory.get_user_safe(corporate_email)
        if existing_user:
            # User exists in directory — save locally as EXISTING
            employee_id = f"EMP-{uuid.uuid4().hex[:8].upper()}"
            self._employee_repo.create(
                EmployeeDocument(
                    employee_id=employee_id,
                    company_id=company_id,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    personal_email=employee.personal_email,
                    corporate_email=corporate_email,
                    google_user_id=existing_user.id,
                    status=EmployeeStatus.EXISTING.value,
                )
            )
            existing_emails.add(corporate_email)
            return EmployeeStatus.EXISTING

        # Generate temporary password
        temp_password = generate_temporary_password()
        password_hashed = hash_password(temp_password)

        # Create user with retry
        try:
            google_user = await self._retry_operation(
                self._directory.create_user,
                GoogleCreateUserRequest(
                    primaryEmail=corporate_email,
                    name=GoogleUserName(
                        givenName=employee.first_name,
                        familyName=employee.last_name,
                    ),
                    password=temp_password,
                    changePasswordAtNextLogin=True,
                    suspended=False,
                ),
            )

            employee_id = f"EMP-{uuid.uuid4().hex[:8].upper()}"
            self._employee_repo.create(
                EmployeeDocument(
                    employee_id=employee_id,
                    company_id=company_id,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    personal_email=employee.personal_email,
                    corporate_email=corporate_email,
                    google_user_id=google_user.id,
                    status=EmployeeStatus.PROVISIONED.value,
                    password_hash=password_hashed,
                    temporary_password=temp_password,
                )
            )

            existing_emails.add(corporate_email)

            if is_admin:
                await self._retry_operation(
                    self._directory.make_admin,
                    corporate_email
                )

            logger.info(
                "employee_provisioned",
                email=corporate_email,
                google_user_id=google_user.id,
                password_preview=redact_password(temp_password),
            )
            return EmployeeStatus.PROVISIONED

        except DuplicateEmployeeError:
            logger.info(
                "employee_duplicate_in_directory",
                email=corporate_email,
            )
            existing_emails.add(corporate_email)
            return EmployeeStatus.EXISTING

        except Exception as exc:
            logger.error(
                "employee_provisioning_failed",
                email=corporate_email,
                error=str(exc),
            )
            # Save as failed
            employee_id = f"EMP-{uuid.uuid4().hex[:8].upper()}"
            self._employee_repo.create(
                EmployeeDocument(
                    employee_id=employee_id,
                    company_id=company_id,
                    first_name=employee.first_name,
                    last_name=employee.last_name,
                    personal_email=employee.personal_email,
                    corporate_email=corporate_email,
                    status=EmployeeStatus.FAILED.value,
                )
            )
            existing_emails.add(corporate_email)
            return EmployeeStatus.FAILED

    # ------------------------------------------------------------------
    # Email notifications
    # ------------------------------------------------------------------

    async def _send_notifications(
        self,
        job_id: str,
        company_id: str,
        request: ProvisioningRequest,
        customer_id: str,
        subscription_id: str,
        users_created: int,
        users_failed: int,
        users_existing: int,
    ) -> str:
        """Send confirmation email to both recipients."""
        total_users = users_created + users_existing
        status_text = "COMPLETED"
        if users_failed > 0:
            status_text = (
                "PARTIAL_FAILURE" if users_created > 0 else "FAILED"
            )

        subject = (
            f"Google Workspace Provisioning {status_text} - "
            f"{request.company_name}"
        )

        # Fetch employees to find the admin (only present if users_created > 0)
        employees = self._employee_repo.get_by_company_id(company_id)
        admin_employee = next((e for e in employees if e.temporary_password), None)

        subscription_block = (
            f"--- Google Workspace Details ---\n"
            f"Company:         {request.company_name}\n"
            f"Domain:          {request.primary_domain}\n"
            f"Customer ID:     {customer_id}\n"
            f"Subscription ID: {subscription_id}\n"
            f"Plan:            {request.plan}\n"
            f"Total Licenses:  {request.license_count}\n"
        )

        if admin_employee and admin_employee.temporary_password:
            # Admin user was successfully created — include credentials
            admin_email_addr = admin_employee.corporate_email
            admin_password = admin_employee.temporary_password
            credentials_block = (
                f"\n--- Admin Credentials ---\n"
                f"Admin Username:     {admin_email_addr}\n"
                f"Temporary Password: {admin_password}\n"
                f"Login URL:         https://admin.google.com\n"
                f"\nChange your password on first login.\n"
            )
            extra_recipient = admin_email_addr
        else:
            # New domain — user creation skipped; give clear setup instructions
            intended_admin = (
                f"{request.admin_first_name.lower()}.{request.admin_last_name.lower()}"
                f"@{request.primary_domain}"
            )
            intended_password = "Welcome@12345!" # Default temporary password
            credentials_block = (
                f"\n--- Admin Credentials (Action Required) ---\n"
                f"Your Google Workspace domain has been provisioned successfully.\n"
                f"To activate your admin account:\n"
                f"\n"
                f"1. Check: {request.alternate_email}\n"
                f"   Google will send a setup email to this address.\n"
                f"\n"
                f"2. Go to: https://admin.google.com\n"
                f"   Intended Admin Email: {intended_admin}\n"
                f"   Intended Password:    {intended_password}\n"
                f"\n"
                f"3. Set your password and accept Google Terms of Service.\n"
                f"\n"
                f"4. Contact support once setup is complete.\n"
            )
            extra_recipient = None

        body = (
            f"Welcome to Google Workspace!\n\n"
            f"Your provisioning request for {request.company_name} has been completed.\n\n"
            f"{subscription_block}"
            f"{credentials_block}"
        )

        email_statuses = []
        recipients = [request.initiated_by_email]

        for recipient in recipients:
            try:
                result = await self._email.send_confirmation_email(
                    recipient, subject, body
                )
                email_statuses.append(result.get("status", "UNKNOWN"))

                # Save notification
                self._notification_repo.create(
                    NotificationDocument(
                        notification_id=f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
                        job_id=job_id,
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        status=NotificationStatus.SENT.value,
                        sent_at=datetime.now(timezone.utc),
                    )
                )
            except Exception as exc:
                logger.error(
                    "email_send_failed",
                    recipient=recipient,
                    error=str(exc),
                )
                email_statuses.append("FAILED")
                self._notification_repo.create(
                    NotificationDocument(
                        notification_id=f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
                        job_id=job_id,
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        status=NotificationStatus.FAILED.value,
                    )
                )

        if all(s == "SENT" for s in email_statuses):
            return "ALL_SENT"
        elif any(s == "SENT" for s in email_statuses):
            return "PARTIAL_SENT"
        return "ALL_FAILED"

    # ------------------------------------------------------------------
    # Job/step tracking helpers
    # ------------------------------------------------------------------

    async def _update_job(
        self, job_id: str, status: JobStatus, **kwargs
    ) -> None:
        self._job_repo.update_job_status(job_id, status.value, **kwargs)

    async def _start_step(self, job_id: str, step_name: str) -> str:
        step_id = f"STEP-{uuid.uuid4().hex[:8].upper()}"
        self._job_repo.add_step(
            ProvisioningStepDocument(
                step_id=step_id,
                job_id=job_id,
                step_name=step_name,
                status=StepStatus.IN_PROGRESS.value,
                started_at=datetime.now(timezone.utc),
            )
        )
        return step_id

    async def _complete_step(
        self, step_id: str, details: Optional[str] = None
    ) -> None:
        self._job_repo.update_step(
            step_id,
            status=StepStatus.COMPLETED.value,
            details=details,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _fail_step(
        self, step_id: str, details: Optional[str] = None
    ) -> None:
        self._job_repo.update_step(
            step_id,
            status=StepStatus.FAILED.value,
            details=details,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    async def _retry_operation(self, operation, *args, **kwargs):
        """Retry an async operation with exponential backoff on RetryableError."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return await operation(*args, **kwargs)
            except RetryableError as exc:
                last_error = exc
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "retrying_operation",
                    operation=operation.__name__,
                    attempt=attempt + 1,
                    max_retries=MAX_RETRIES,
                    delay=delay,
                )
                await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]
