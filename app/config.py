"""Application settings (pydantic-settings) + severity/channel config.

Ported from the Adaptive Gateway pattern: a single cached ``Settings`` object,
populated from environment variables (and ``.env`` for local/host runs). Inside
docker compose the variables arrive via ``env_file: .env`` as real env vars, so
the file itself need not be present in the image.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from channels.base import Severity


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    secret_key: str = "dev-insecure-change-me"
    docs_enabled: bool = True
    simulator_enabled: bool = True
    metrics_public: bool = True
    cors_allowed_origins: str = ""
    ingest_shared_secret: str = ""

    # Infrastructure connections (defaults match docker-compose service names).
    database_url: str = "postgresql+asyncpg://beacon:beacon@postgres:5432/beacon"
    redis_url: str = "redis://redis:6379"
    amqp_url: str = "amqp://beacon:beacon@rabbitmq:5672/"

    # Responder matching (Redis GEO).
    responder_search_radius_m: int = Field(default=5000, ge=1)
    responder_max_radius_m: int = Field(default=25000, ge=1)

    # USSD session state (Redis).
    ussd_session_ttl_seconds: int = 120

    # Auth (JWT access + refresh rotation).
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Event bus (RabbitMQ).
    event_exchange: str = "beacon.events"
    max_queue_priority: int = 10
    broker_timeout_seconds: float = Field(default=5, gt=0)
    outbox_poll_seconds: float = Field(default=0.5, gt=0)
    outbox_batch_size: int = Field(default=32, ge=1, le=1000)
    delivery_max_retries: int = Field(default=5, ge=0, le=20)
    delivery_retry_base_seconds: float = Field(default=1, gt=0)
    delivery_retry_max_seconds: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        if self.responder_max_radius_m < self.responder_search_radius_m:
            raise ValueError("Maximum responder radius must cover the starting radius")
        if self.environment.lower() == "production":
            if self.secret_key == "dev-insecure-change-me" or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value in production"
                )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def severity_priorities(self) -> dict[str, int]:
        """Severity -> RabbitMQ message priority (single source: the enum)."""
        return {s.value: s.numeric_priority() for s in Severity}


@lru_cache
def get_settings() -> Settings:
    return Settings()
