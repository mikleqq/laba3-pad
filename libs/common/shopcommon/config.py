"""Configuration management.

Every service has its own ``Settings`` class derived from
:class:`BaseServiceSettings`. Values are read from environment variables
(and an optional ``.env`` file), so the same image can be configured
differently per environment without code changes.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "service"
    log_level: str = "INFO"

    # Persistence: each service points to its own database.
    database_url: str = "sqlite+aiosqlite:///:memory:"

    # Messaging: with a PostgreSQL DATABASE_URL events go through the bus.* tables
    # of the same database; otherwise (tests, SQLite) an in-process bus is used.
    event_poll_interval_seconds: float = 0.5

    # Security
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    internal_token: str = "dev-internal-token-change-me"

    # Outgoing HTTP calls to other services
    http_timeout_seconds: float = 5.0

    # Browser origins allowed to call the API (the demo web UI).
    cors_origins: list[str] = ["*"]

    # Startup: how long to wait for DB / broker to become available
    startup_retries: int = 30
    startup_retry_delay_seconds: float = 2.0
