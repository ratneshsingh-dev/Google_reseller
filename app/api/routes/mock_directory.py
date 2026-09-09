"""
Mock Admin Directory API HTTP endpoints for debugging/testing.

These mirror the real Google Admin SDK Directory API structure.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.exceptions import DuplicateEmployeeError, UserNotFoundError
from app.dependencies import get_directory_service
from app.models.google_api import (
    GoogleCreateUserRequest,
    GoogleUpdateUserRequest,
    GoogleUser,
)

router = APIRouter(prefix="/mock/admin/directory/v1", tags=["Mock Directory API"])


@router.post("/users", response_model=GoogleUser)
async def create_user(request: GoogleCreateUserRequest):
    try:
        return await get_directory_service().create_user(request)
    except DuplicateEmployeeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/users", response_model=list[GoogleUser])
async def list_users(domain: Optional[str] = Query(None)):
    if not domain:
        raise HTTPException(
            status_code=400, detail="domain query parameter is required"
        )
    return await get_directory_service().list_users(domain)


@router.get("/users/{user_email}", response_model=GoogleUser)
async def get_user(user_email: str):
    try:
        return await get_directory_service().get_user(user_email)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/users/{user_email}", response_model=GoogleUser)
async def update_user(user_email: str, request: GoogleUpdateUserRequest):
    try:
        return await get_directory_service().update_user(user_email, request)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/users/{user_email}/suspend", response_model=GoogleUser)
async def suspend_user(user_email: str):
    try:
        return await get_directory_service().suspend_user(user_email)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/users/{user_email}", status_code=204)
async def delete_user(user_email: str):
    try:
        await get_directory_service().delete_user(user_email)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
