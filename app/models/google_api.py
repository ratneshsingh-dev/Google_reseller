"""
Pydantic models matching Google Workspace Reseller API and Admin Directory API
request/response shapes.

These models serve as the contract between our service interfaces and the
mock/real adapters, ensuring that swapping MockResellerService for
GoogleResellerService requires minimal changes.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ===========================================================================
# Google Reseller API Models
# ===========================================================================


class GooglePostalAddress(BaseModel):
    """Matches Google Reseller API postalAddress object."""

    contact_name: str = Field(alias="contactName", default="")
    organization_name: str = Field(alias="organizationName", default="")
    address_line1: str = Field(alias="addressLine1", default="")
    locality: str = ""
    region: str = ""
    postal_code: str = Field(alias="postalCode", default="")
    country_code: str = Field(alias="countryCode", default="")

    model_config = {"populate_by_name": True}


class GoogleCreateCustomerRequest(BaseModel):
    """Request body for creating a customer via Reseller API."""

    customer_domain: str = Field(alias="customerDomain")
    customer_type: str = Field(alias="customerType", default="domain")
    postal_address: GooglePostalAddress = Field(alias="postalAddress")
    alternate_email: str = Field(alias="alternateEmail")

    model_config = {"populate_by_name": True}


class GoogleCustomer(BaseModel):
    """Response from Reseller API customer endpoints."""

    kind: str = "reseller#customer"
    customer_id: str = Field(alias="customerId")
    customer_domain: str = Field(alias="customerDomain")
    customer_type: str = Field(alias="customerType", default="domain")
    postal_address: Optional[GooglePostalAddress] = Field(
        alias="postalAddress", default=None
    )
    alternate_email: str = Field(alias="alternateEmail", default="")
    customer_domain_verified: bool = Field(
        alias="customerDomainVerified", default=True
    )

    model_config = {"populate_by_name": True}


# ===========================================================================
# Google Subscription Models
# ===========================================================================


class GoogleSubscriptionPlan(BaseModel):
    """Plan details within a subscription."""

    plan_name: str = Field(alias="planName")
    is_commitment_plan: bool = Field(alias="isCommitmentPlan", default=False)

    model_config = {"populate_by_name": True}


class GoogleSubscriptionSeats(BaseModel):
    """Seat details within a subscription."""

    number_of_seats: int = Field(alias="numberOfSeats", default=0)
    licensed_number_of_seats: int = Field(
        alias="licensedNumberOfSeats", default=0
    )

    model_config = {"populate_by_name": True}


class GoogleCreateSubscriptionRequest(BaseModel):
    """Request body for creating a subscription."""

    sku_id: str = Field(alias="skuId")
    plan: GoogleSubscriptionPlan
    seats: GoogleSubscriptionSeats
    customer_domain: str = Field(alias="customerDomain", default="")

    model_config = {"populate_by_name": True}


class GoogleChangSeatsRequest(BaseModel):
    """Request body for changing seats."""

    seats: GoogleSubscriptionSeats

    model_config = {"populate_by_name": True}


class GoogleChangePlanRequest(BaseModel):
    """Request body for changing plan."""

    plan_name: str = Field(alias="planName")

    model_config = {"populate_by_name": True}


class GoogleSubscription(BaseModel):
    """Response from Reseller API subscription endpoints."""

    kind: str = "reseller#subscription"
    customer_id: str = Field(alias="customerId")
    subscription_id: str = Field(alias="subscriptionId")
    sku_id: str = Field(alias="skuId")
    plan: GoogleSubscriptionPlan
    seats: GoogleSubscriptionSeats
    status: str = "ACTIVE"
    billing_method: str = Field(alias="billingMethod", default="ONLINE")
    customer_domain: str = Field(alias="customerDomain", default="")
    sku_name: str = Field(alias="skuName", default="")
    creation_time: Optional[str] = Field(alias="creationTime", default=None)

    model_config = {"populate_by_name": True}


# ===========================================================================
# Google Admin Directory API Models
# ===========================================================================


class GoogleUserName(BaseModel):
    """Name object within a Directory API user."""

    given_name: str = Field(alias="givenName")
    family_name: str = Field(alias="familyName")
    full_name: Optional[str] = Field(alias="fullName", default=None)

    model_config = {"populate_by_name": True}


class GoogleCreateUserRequest(BaseModel):
    """Request body for creating a user via Admin Directory API."""

    primary_email: str = Field(alias="primaryEmail")
    name: GoogleUserName
    password: str
    change_password_at_next_login: bool = Field(
        alias="changePasswordAtNextLogin", default=True
    )
    suspended: bool = False
    org_unit_path: Optional[str] = Field(alias="orgUnitPath", default="/")
    recovery_email: Optional[str] = Field(alias="recoveryEmail", default=None)

    model_config = {"populate_by_name": True}


class GoogleUpdateUserRequest(BaseModel):
    """Request body for updating a user."""

    name: Optional[GoogleUserName] = None
    suspended: Optional[bool] = None
    password: Optional[str] = None
    change_password_at_next_login: Optional[bool] = Field(
        alias="changePasswordAtNextLogin", default=None
    )

    model_config = {"populate_by_name": True}


class GoogleUser(BaseModel):
    """Response from Admin Directory API user endpoints."""

    kind: str = "admin#directory#user"
    id: str
    primary_email: str = Field(alias="primaryEmail")
    name: GoogleUserName
    suspended: bool = False
    change_password_at_next_login: bool = Field(
        alias="changePasswordAtNextLogin", default=True
    )
    creation_time: Optional[str] = Field(alias="creationTime", default=None)
    org_unit_path: str = Field(alias="orgUnitPath", default="/")
    is_admin: bool = Field(alias="isAdmin", default=False)
    etag: Optional[str] = None

    model_config = {"populate_by_name": True}
