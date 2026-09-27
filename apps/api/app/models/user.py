"""``users`` and ``refresh_tokens`` — the authentication aggregate.

Mirrors the DDL in ``database/schemas/02_tables.sql``. Where the database
already enforces an invariant (lockout counters, token-version bumps on password
change), the model does not duplicate it: the trigger is authoritative because
it also holds for rows written by psql, migrations and workers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base
from app.models.enums import UserRole, UserStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.models.property import Owner, Tenant

user_role_enum = PGEnum(
    UserRole,
    name="user_role",
    schema="ulpin",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)
user_status_enum = PGEnum(
    UserStatus,
    name="user_status",
    schema="ulpin",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)


class OtpPurpose(StrEnum):
    """Which challenge a request is about.

    Not a column: the two purposes have their own column pairs, so nothing in the
    database needs to name them. This exists so ``/auth/resend-otp`` can say which
    code to re-send, and lives here rather than in ``app.models.enums`` because
    that module mirrors real PostgreSQL enum types.
    """

    EMAIL_VERIFY = "EMAIL_VERIFY"
    PASSWORD_RESET = "PASSWORD_RESET"


def _challenge_is_live(
    code_hash: str | None, expires_at: datetime | None, attempts: int
) -> bool:
    """Shared liveness test for the two OTP challenges."""
    if code_hash is None or expires_at is None:
        return False
    if expires_at <= datetime.now(UTC):
        return False
    return attempts < settings.OTP_MAX_ATTEMPTS


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # -- Identity ------------------------------------------------------------
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # -- Credentials ---------------------------------------------------------
    # Argon2id encoded hash — never a password, never reversible.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # -- Authorisation -------------------------------------------------------
    role: Mapped[UserRole] = mapped_column(user_role_enum, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        user_status_enum, nullable=False, server_default=text("'PENDING'")
    )
    jurisdiction_code: Mapped[str | None] = mapped_column(String(20))

    # Bumping this invalidates every access token already in the wild, without
    # a blocklist. The password-rotation trigger increments it automatically.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # -- Verification and recovery -------------------------------------------
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_token_hash: Mapped[str | None] = mapped_column(String(64))
    verify_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reset_token_hash: Mapped[str | None] = mapped_column(String(64))
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- One-time passcodes ---------------------------------------------------
    # Two independent challenges. One live code per purpose: issuing a code
    # overwrites the previous one, so a user who taps "resend" three times has
    # exactly one code that works — the newest. See
    # database/schemas/07_auth_otp.sql for why this is columns on the user row
    # rather than an otp_codes table.
    otp_code_hash: Mapped[str | None] = mapped_column(String(64))
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    reset_otp_hash: Mapped[str | None] = mapped_column(String(64))
    reset_otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_otp_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # Send throttling, shared across both purposes: they protect one mailbox. The
    # window anchor lets the per-hour count reset on its own rather than needing a
    # scheduled job to clear it.
    otp_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_send_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    otp_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- Brute-force defence -------------------------------------------------
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(INET)

    # -- MFA -----------------------------------------------------------------
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    mfa_secret_encrypted: Mapped[bytes | None] = mapped_column()

    # -- Housekeeping --------------------------------------------------------
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # -- Relationships -------------------------------------------------------
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    owner_profile: Mapped["Owner | None"] = relationship(
        back_populates="user",
        primaryjoin="User.user_id == foreign(Owner.user_id)",
        uselist=False,
        viewonly=True,
    )
    tenant_profiles: Mapped[list["Tenant"]] = relationship(
        back_populates="user",
        primaryjoin="User.user_id == foreign(Tenant.user_id)",
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'AUDITOR', 'SERVICE') OR jurisdiction_code IS NOT NULL",
            name="users_scope",
        ),
        Index(
            "uq_users_email_lower",
            func.lower(email),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": "ulpin"},
    )

    # -- Derived state -------------------------------------------------------
    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > datetime.now(UTC)

    @property
    def is_active(self) -> bool:
        return (
            self.status is UserStatus.ACTIVE
            and self.deleted_at is None
            and not self.is_locked
        )

    @property
    def can_authenticate(self) -> bool:
        """Login-eligible. PENDING is allowed through so the API can return a
        specific 'verify your email' error rather than a generic refusal."""
        return (
            self.deleted_at is None
            and self.status in (UserStatus.ACTIVE, UserStatus.PENDING)
            and not self.is_locked
        )

    def reset_token_is_valid(self) -> bool:
        return (
            self.reset_token_hash is not None
            and self.reset_token_expires_at is not None
            and self.reset_token_expires_at > datetime.now(UTC)
        )

    def verify_token_is_valid(self) -> bool:
        return (
            self.verify_token_hash is not None
            and self.verify_token_expires_at is not None
            and self.verify_token_expires_at > datetime.now(UTC)
        )

    def otp_is_live(self) -> bool:
        """True when a usable email-verification challenge exists.

        Exhausted attempts count as not live, so a burnt challenge cannot be
        retried by waiting — only by requesting a new code.
        """
        return _challenge_is_live(
            self.otp_code_hash, self.otp_expires_at, self.otp_attempts
        )

    def reset_otp_is_live(self) -> bool:
        """True when a usable password-reset challenge exists."""
        return _challenge_is_live(
            self.reset_otp_hash, self.reset_otp_expires_at, self.reset_otp_attempts
        )

    def clear_otp(self) -> None:
        """Spend the verification challenge. Called on success and on exhaustion
        alike — a code that has done its job and a code that has been guessed at
        five times are equally finished."""
        self.otp_code_hash = None
        self.otp_expires_at = None
        self.otp_attempts = 0

    def clear_reset_otp(self) -> None:
        """Spend the reset challenge."""
        self.reset_otp_hash = None
        self.reset_otp_expires_at = None
        self.reset_otp_attempts = 0


class RefreshToken(Base):
    """One row per issued refresh token.

    Only the SHA-256 digest is stored, so a database dump yields no usable
    sessions. ``replaced_by`` records the rotation chain: presenting a token that
    has already been rotated proves theft, and the whole chain is revoked.
    """

    __tablename__ = "refresh_tokens"

    token_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ulpin.users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(100))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("ulpin.refresh_tokens.token_id", ondelete="SET NULL"),
    )

    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip_address: Mapped[str | None] = mapped_column(INET)
    device_label: Mapped[str | None] = mapped_column(String(100))

    # Monotonic sequence for ordering sessions in the "signed-in devices" view
    # without relying on clock resolution.
    seq: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=True)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = ({"schema": "ulpin"},)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)

    @property
    def was_rotated(self) -> bool:
        return self.replaced_by is not None
