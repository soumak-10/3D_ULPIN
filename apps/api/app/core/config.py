"""Application configuration.

Every setting is environment-driven and validated at import time, so a
misconfigured deployment fails at startup rather than on the first request that
happens to touch the bad value.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import (
    AnyHttpUrl,
    BeforeValidator,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: Any) -> Any:
    """Accept either a JSON array or a comma-separated string for list settings."""
    if isinstance(value, str) and not value.startswith("["):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# `NoDecode` is load-bearing. Without it pydantic-settings JSON-decodes any
# list-annotated field *at the dotenv source*, before validators run, so
# `ALLOWED_HOSTS=localhost,127.0.0.1` raises SettingsError at import and the
# validator below never sees the string. Every .env.example here documents the
# comma-separated form, so dropping this marker breaks startup everywhere.
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # -- Application ---------------------------------------------------------
    PROJECT_NAME: str = "3D ULPIN Generation and Vertical Property Mapping System"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = False
    VERSION: str = "1.0.0"

    # -- Security ------------------------------------------------------------
    # Generated per-process if unset, which is fine locally and catastrophic in
    # production (every restart invalidates all tokens, and replicas disagree).
    # The model validator below refuses to let that happen outside `local`.
    SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(64)))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    RESET_TOKEN_EXPIRE_MINUTES: int = 30
    VERIFY_TOKEN_EXPIRE_HOURS: int = 48

    # -- One-time passcodes --------------------------------------------------
    # Six digits is 10^6 — weak on its own, which is why all four of the
    # following matter together. Raising the expiry or the attempt cap without
    # thinking about the product of the two is how OTP flows become guessable.
    OTP_LENGTH: int = 6
    OTP_EXPIRE_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    # Seconds a user must wait before another code is sent, and the ceiling per
    # rolling hour. Without the hourly cap the cooldown alone still allows 60
    # codes an hour into someone else's inbox.
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    OTP_MAX_SENDS_PER_HOUR: int = 5
    # Ticket minted once a reset OTP is verified, carried to /reset-password so
    # the passcode itself is spent immediately. Short by design: it is a bearer
    # credential for changing a password.
    RESET_TICKET_EXPIRE_MINUTES: int = 15
    # Blocks login until the address is confirmed. Off only for automated tests
    # and seeded demo data that never receives mail.
    REQUIRE_EMAIL_VERIFICATION: bool = True

    JWT_ISSUER: str = "ulpin-api"
    JWT_AUDIENCE: str = "ulpin-web"

    # Pepper applied before hashing statutory IDs. Rotating it invalidates every
    # stored national_id_hash, so treat it as permanent once data exists.
    ID_HASH_PEPPER: SecretStr = Field(default=SecretStr("dev-pepper-change-me"))

    # Argon2id parameters. Defaults target ~100 ms on a 2-core container.
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST: int = 65536  # KiB
    ARGON2_PARALLELISM: int = 4
    ARGON2_HASH_LEN: int = 32
    ARGON2_SALT_LEN: int = 16

    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15
    PASSWORD_MIN_LENGTH: int = 12

    # -- CORS ----------------------------------------------------------------
    BACKEND_CORS_ORIGINS: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])
    ALLOWED_HOSTS: CsvList = Field(default_factory=lambda: ["*"])

    # -- Database ------------------------------------------------------------
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "ulpin_app"
    POSTGRES_PASSWORD: SecretStr = SecretStr("postgres")
    POSTGRES_DB: str = "ulpin_db"
    POSTGRES_SCHEMA: str = "ulpin"

    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_ECHO: bool = False

    # -- Redis ---------------------------------------------------------------
    REDIS_URL: RedisDsn = Field(default="redis://localhost:6379/0")
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 10
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 120

    # -- Email ---------------------------------------------------------------
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_TLS: bool = True
    EMAIL_FROM: str = "no-reply@ulpin.gov.in"
    EMAIL_FROM_NAME: str = "ULPIN Registry"
    FRONTEND_URL: AnyHttpUrl = Field(default="http://localhost:3000")

    # -- Geospatial ----------------------------------------------------------
    STORAGE_SRID: int = 4326
    DEFAULT_METRIC_SRID: int = 7755
    DEFAULT_VERTICAL_DATUM: str = "EGM2008"
    GEOM_TOLERANCE_M: float = 0.01

    # -- Cookies -------------------------------------------------------------
    # The refresh token lives in an HttpOnly cookie; the access token never
    # touches storage the browser can read from JavaScript.
    COOKIE_NAME_REFRESH: str = "ulpin_rt"
    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def _check_algorithm(cls, v: str) -> str:
        allowed = {"HS256", "HS384", "HS512", "RS256", "ES256"}
        if v not in allowed:
            raise ValueError(f"JWT_ALGORITHM must be one of {sorted(allowed)}")
        return v

    @field_validator("PASSWORD_MIN_LENGTH")
    @classmethod
    def _check_password_length(cls, v: int) -> int:
        if v < 8:
            raise ValueError("PASSWORD_MIN_LENGTH below 8 is not defensible")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.ENVIRONMENT in ("staging", "production"):
            secret = self.SECRET_KEY.get_secret_value()
            if len(secret) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters outside local")
            if self.ID_HASH_PEPPER.get_secret_value() == "dev-pepper-change-me":
                raise ValueError("ID_HASH_PEPPER still holds its development default")
            if not self.COOKIE_SECURE:
                raise ValueError("COOKIE_SECURE cannot be disabled outside local")
            if "*" in self.ALLOWED_HOSTS:
                raise ValueError("ALLOWED_HOSTS must be explicit outside local")
            if self.DEBUG:
                raise ValueError("DEBUG must be off outside local")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD.get_secret_value(),
                host=self.POSTGRES_SERVER,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SYNC_DATABASE_URI(self) -> str:
        """Alembic and psycopg-based tooling need the synchronous driver."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD.get_secret_value(),
                host=self.POSTGRES_SERVER,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAIL_FROM)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
