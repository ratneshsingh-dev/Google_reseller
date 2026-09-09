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
