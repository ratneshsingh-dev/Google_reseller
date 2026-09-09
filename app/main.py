"""
Google Workspace Automated Provisioning System — FastAPI Application.

Entrypoint: uvicorn app.main:app --reload
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv(override=True)
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, companies, mock_directory, mock_reseller, provisioning
from app.api.routes import admin as admin_routes
from app.api.routes import reseller_api, reseller_auth
from app.core.config import get_settings
from app.core.exceptions import (
    InsufficientSeatsError,
    InvalidDomainError,
    InvalidEmailError,
    JobNotFoundError,
    LicenceCapExceededError,
    ProvisioningError,
    ValidationError,
)
from app.core.logging import get_logger, setup_logging
from app.core.rate_limit import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown events."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "app_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        adapter=settings.service_adapter,
        firestore=settings.use_firestore,
    )
    yield
    logger.info("app_shutdown")


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Automated Google Workspace provisioning system with mock API support. "
            "Creates customers, subscriptions, and user accounts through a "
            "realistic simulation of the Google Workspace Reseller and Admin APIs."
        ),
        lifespan=lifespan,
    )

    # --- Rate Limiting ---
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    application.add_middleware(SlowAPIMiddleware)

    # --- CORS ---
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Exception handlers ---
    @application.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=400,
            content={"error": exc.message, "detail": str(exc.details)},
        )

    @application.exception_handler(InvalidDomainError)
    async def domain_error_handler(request: Request, exc: InvalidDomainError):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid domain", "detail": exc.message},
        )

    @application.exception_handler(InvalidEmailError)
    async def email_error_handler(request: Request, exc: InvalidEmailError):
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid email", "detail": exc.message},
        )

    @application.exception_handler(InsufficientSeatsError)
    async def seats_error_handler(
        request: Request, exc: InsufficientSeatsError
    ):
        return JSONResponse(
            status_code=400,
            content={"error": "Insufficient seats", "detail": exc.message},
        )

    @application.exception_handler(JobNotFoundError)
    async def job_not_found_handler(request: Request, exc: JobNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"error": "Job not found", "detail": exc.message},
        )

    @application.exception_handler(ProvisioningError)
    async def provisioning_error_handler(
        request: Request, exc: ProvisioningError
    ):
        return JSONResponse(
            status_code=500,
            content={"error": "Provisioning error", "detail": exc.message},
        )

    @application.exception_handler(LicenceCapExceededError)
    async def licence_cap_error_handler(request: Request, exc: LicenceCapExceededError):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Licence cap exceeded",
                "detail": exc.message,
                "quota": {
                    "requested": exc.requested,
                    "licences_used": exc.used,
                    "max_licence_cap": exc.cap,
                    "licences_remaining": exc.remaining,
                },
            },
        )

    # --- Routes ---
    application.include_router(auth.router)
    application.include_router(admin_routes.router)
    application.include_router(reseller_auth.router)
    application.include_router(reseller_api.router)
    application.include_router(provisioning.router)
    application.include_router(companies.router)
    application.include_router(mock_reseller.router)
    application.include_router(mock_directory.router)

    # --- Static Files & Frontend ---
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        application.mount(
            "/static", StaticFiles(directory=static_dir), name="static"
        )

    @application.get("/", include_in_schema=False)
    @application.get("/ui", include_in_schema=False)
    async def serve_frontend():
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return JSONResponse({"message": "Frontend UI available at /docs"})

    # --- Config ---
    @application.get("/api/v1/config/oauth-client-id", tags=["Config"])
    async def get_oauth_client_id():
        return {"client_id": settings.google_oauth_client_id}

    # --- Health check ---
    @application.get("/health", tags=["Health"])
    async def health_check():
        return {
            "status": "healthy",
            "version": settings.app_version,
            "adapter": settings.service_adapter,
        }

    return application


app = create_app()
