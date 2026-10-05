"""
Application configuration loaded from environment variables.

Uses pydantic-settings for type-safe config with .env file support.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- GCP ---
    google_cloud_project: str = "testing-ratnesh"
    firestore_database: str = "(default)"
    gcs_bucket: str = "workspace-provisioning-bucket"
    google_oauth_client_id: str = ""

    # --- Storage mode ---
    use_firestore: bool = False

    # --- Service adapter ---
    service_adapter: str = "mock"  # "mock" | "google"

    # --- Background jobs ---
    job_concurrency: int = 25          # provisioning jobs running at the same time per server
    bulk_max_items: int = 100          # domains accepted in one bulk request
    job_backend: str = "memory"        # "memory" (in-process pool) | "cloudtasks" (durable queue)
    job_stale_after_seconds: int = 1800  # no heartbeat for this long -> job is treated as dead
    job_heartbeat_seconds: int = 20
    reconcile_interval_seconds: int = 300
    google_call_timeout_seconds: int = 60

    # --- Cloud Tasks (used when job_backend == "cloudtasks") ---
    cloud_tasks_location: str = "us-central1"
    cloud_tasks_queue: str = ""
    tasks_invoker_sa: str = ""         # service account Cloud Tasks signs its OIDC token as
    worker_base_url: str = ""          # public URL of this service, e.g. https://....run.app

    # --- Mock failure simulation ---
    mock_failure_mode: bool = False
    mock_failure_rate: float = 0.0  # 0.0 to 1.0

    # --- CORS ---
    cors_origins: str = '["http://localhost:3000","http://localhost:8000"]'

    # --- Application ---
    app_name: str = "Google Workspace Provisioning System"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # --- JWT (Reseller API Auth) ---
    jwt_secret_key: str = "CHANGE-ME-TO-A-LONG-RANDOM-SECRET-IN-PRODUCTION"
    jwt_access_token_expire_hours: int = 24  # Token valid for 24 hours

    # --- Admin whitelist (emails that can access /api/v1/admin/*) ---
    admin_emails: str = '["ratnesh.s@econz.net", "Admin@supportnation.co.in"]'

    # --- Dev-only login bypass (POST /api/v1/auth/dev-login) ---
    # Must be explicitly enabled for local development. Never set this in
    # Cloud Run — it logs the caller in as an admin with no credentials.
    enable_dev_login: bool = False

    @property
    def cors_origin_list(self) -> List[str]:
        """Parse CORS origins from JSON string."""
        try:
            return json.loads(self.cors_origins)
        except (json.JSONDecodeError, TypeError):
            return ["http://localhost:3000", "http://localhost:8000"]

    @property
    def admin_email_list(self) -> List[str]:
        """Parse admin emails from JSON string."""
        try:
            return [e.lower() for e in json.loads(self.admin_emails)]
        except (json.JSONDecodeError, TypeError):
            return []


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
