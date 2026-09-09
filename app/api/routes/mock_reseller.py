"""
Mock Reseller API HTTP endpoints for debugging/testing.

These mirror the real Google Reseller API structure so you can
inspect mock state directly via HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.exceptions import CustomerAlreadyExistsError, CustomerNotFoundError, SubscriptionAlreadyExistsError, SubscriptionNotFoundError
from app.dependencies import get_reseller_service
from app.models.google_api import (
    GoogleChangePlanRequest,
    GoogleChangSeatsRequest,
    GoogleCreateCustomerRequest,
    GoogleCreateSubscriptionRequest,
    GoogleCustomer,
    GoogleSubscription,
)

router = APIRouter(prefix="/mock/reseller/v1", tags=["Mock Reseller API"])


@router.post("/customers", response_model=GoogleCustomer)
async def create_customer(request: GoogleCreateCustomerRequest):
    try:
        return await get_reseller_service().create_customer(request)
    except CustomerAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/customers/{customer_id}", response_model=GoogleCustomer)
async def get_customer(customer_id: str):
    try:
        return await get_reseller_service().get_customer(customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/customers/{customer_id}/subscriptions",
    response_model=GoogleSubscription,
)
async def create_subscription(
    customer_id: str, request: GoogleCreateSubscriptionRequest
):
    try:
        return await get_reseller_service().create_subscription(
            customer_id, request
        )
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SubscriptionAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get(
    "/customers/{customer_id}/subscriptions/{subscription_id}",
    response_model=GoogleSubscription,
)
async def get_subscription(customer_id: str, subscription_id: str):
    try:
        return await get_reseller_service().get_subscription(
            customer_id, subscription_id
        )
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/customers/{customer_id}/subscriptions/{subscription_id}/changeSeats",
    response_model=GoogleSubscription,
)
async def change_seats(
    customer_id: str,
    subscription_id: str,
    request: GoogleChangSeatsRequest,
):
    try:
        return await get_reseller_service().change_seats(
            customer_id, subscription_id, request
        )
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/customers/{customer_id}/subscriptions/{subscription_id}/changePlan",
    response_model=GoogleSubscription,
)
async def change_plan(
    customer_id: str,
    subscription_id: str,
    request: GoogleChangePlanRequest,
):
    try:
        return await get_reseller_service().change_plan(
            customer_id, subscription_id, request
        )
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/customers/{customer_id}/subscriptions",
    response_model=list[GoogleSubscription],
)
async def list_subscriptions(customer_id: str):
    return await get_reseller_service().list_subscriptions(customer_id)
