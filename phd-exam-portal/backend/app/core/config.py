"""Application settings.

Everything is environment driven so the same image runs in dev and prod.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---------------------------------------------------------------- general
    APP_NAME: str = "PhD Entrance Examination & Live Proctoring Portal"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    BASE_URL: str = "http://localhost:8080"

    # --------------------------------------------------------------- database
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "exam"
    POSTGRES_PASSWORD: str = "exam"
    POSTGRES_DB: str = "examportal"

    # Section 6 of the spec asks for pool_size=20. It quotes two different
    # max_overflow values (40 in section 1, 10 in section 6); the runtime value
    # is configurable and defaults to 40 so the pool can absorb the documented
    # peak while `DB_MAX_OVERFLOW=10` reproduces the stricter setting.
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_PRE_PING: bool = True
    DB_ECHO: bool = False

    # ------------------------------------------------------------------ redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    # ------------------------------------------------------------- jwt / auth
    SECRET_KEY: str = "CHANGE-ME-dev-only-secret-key-please-override"
    JWT_ALGORITHM: str = "HS256"
    CANDIDATE_TOKEN_MINUTES: int = 360
    ADMIN_TOKEN_MINUTES: int = 480

    # Two *separate* cookies so a candidate token can never satisfy an admin
    # guard and vice versa (strict role separation).
    CANDIDATE_COOKIE_NAME: str = "exam_candidate_token"
    ADMIN_COOKIE_NAME: str = "exam_admin_token"
    COOKIE_SECURE: bool = False  # set true behind TLS
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    COOKIE_DOMAIN: str | None = None

    # ------------------------------------------------------------ rate limits
    LOGIN_RATE_LIMIT: int = 10           # attempts
    LOGIN_RATE_WINDOW_SECONDS: int = 300  # per 5 minutes per ip+identity
    API_RATE_LIMIT: int = 600
    API_RATE_WINDOW_SECONDS: int = 60

    # --------------------------------------------------------------- seeding
    SEED_ADMIN_EMAIL: str = "admin@examportal.edu"
    SEED_ADMIN_PASSWORD: str = "Admin@123"
    SEED_CANDIDATE_PASSWORD: str = "dsirt@123"
    SEED_DATA_DIR: str = "seed_data"

    # ------------------------------------------------------------- proctoring
    STUN_URL: str = "stun:localhost:3478"
    TURN_URL: str = "turn:localhost:3478"
    TURN_USERNAME: str = "examportal"
    TURN_PASSWORD: str = "turnsecret"
    HEARTBEAT_TTL_SECONDS: int = 30
    SNAPSHOT_DIR: str = "/app/media/snapshots"
    SNAPSHOT_URL_PREFIX: str = "/media/snapshots"

    # -------------------------------------------------------------- exam rules
    DEFAULT_PHASE1_MINUTES: int = 60
    DEFAULT_PHASE2_MINUTES: int = 60
    MAX_VIOLATIONS_BEFORE_FLAG: int = 5

    @field_validator("SECRET_KEY")
    @classmethod
    def _warn_default_secret(cls, v: str) -> str:
        return v

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """Used by Alembic, which runs migrations synchronously."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def logo_url(self) -> str:
        """URL of the institution logo, resolved once at startup.

        Looks for a real logo first and falls back to a neutral placeholder,
        so the pages never request a missing file. Drop ``logo.png`` into
        ``app/static/img/`` and restart -- no template change needed.
        """
        from pathlib import Path

        base = Path(__file__).resolve().parents[1] / "static" / "img"
        for name in ("logo.png", "logo.svg", "logo.jpg", "logo.webp"):
            if (base / name).is_file():
                return f"/static/img/{name}"
        return "/static/img/logo-placeholder.svg"

    @property
    def ice_servers(self) -> list[dict]:
        return [
            {"urls": [self.STUN_URL]},
            {
                "urls": [self.TURN_URL],
                "username": self.TURN_USERNAME,
                "credential": self.TURN_PASSWORD,
            },
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
