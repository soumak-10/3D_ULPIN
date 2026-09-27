"""Request and response contracts for the authentication endpoints.

Validation here is the first gate, not the only one: the service layer re-checks
anything security-relevant, and the database holds the final constraint. A
schema that rejects a bad password is a courtesy to the client; the policy is
enforced again in ``auth_service``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.config import settings
from app.core.security import validate_password_strength
from app.models.enums import UserRole, UserStatus
from app.models.user import OtpPurpose

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
FullName = Annotated[str, StringConstraints(min_length=2, max_length=200, strip_whitespace=True)]
Phone = Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,15}$")]
Jurisdiction = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{2}(-[A-Z0-9]{2,6}){0,3}$", to_upper=True)
]


class _PasswordPolicyMixin(BaseModel):
    """Applies the shared password policy to whichever field holds it."""

    @field_validator("password", check_fields=False)
    @classmethod
    def _check_strength(cls, v: str) -> str:
        problems = validate_password_strength(v)
        if problems:
            raise ValueError("; ".join(problems))
        return v


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
class RegisterRequest(_PasswordPolicyMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: Password
    confirm_password: Password
    full_name: FullName
    phone: Phone | None = None

    # Self-registration is limited to the two citizen-facing roles. ADMIN,
    # PROPERTY_OFFICER, AUDITOR and SERVICE accounts are provisioned by an
    # administrator — letting the public pick them would be a privilege
    # escalation by form field.
    role: UserRole = UserRole.OWNER
    jurisdiction_code: Jurisdiction | None = None

    @field_validator("role")
    @classmethod
    def _only_public_roles(cls, v: UserRole) -> UserRole:
        if v not in (UserRole.OWNER, UserRole.TENANT):
            raise ValueError("Only OWNER and TENANT accounts may be self-registered")
        return v

    @model_validator(mode="after")
    def _passwords_match(self) -> Self:
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self

    @model_validator(mode="after")
    def _password_excludes_email(self) -> Self:
        local = str(self.email).split("@")[0].lower()
        if len(local) >= 4 and local in self.password.lower():
            raise ValueError("Password must not contain your email address")
        return self


class AdminCreateUserRequest(BaseModel):
    """Administrative provisioning: any role, and a jurisdiction is mandatory
    for the roles whose authority is geographic."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    full_name: FullName
    phone: Phone | None = None
    role: UserRole
    jurisdiction_code: Jurisdiction | None = None
    send_invite: bool = True
    password: Password | None = Field(
        default=None,
        description="Omit to send an invitation link instead of setting a password.",
    )

    @model_validator(mode="after")
    def _jurisdiction_required_for_scoped_roles(self) -> Self:
        if self.role in (UserRole.PROPERTY_OFFICER, UserRole.OWNER, UserRole.TENANT):
            if not self.jurisdiction_code:
                raise ValueError(f"{self.role.value} accounts require a jurisdiction_code")
        return self


# ---------------------------------------------------------------------------
# Login and tokens
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(
        default=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        description="Access token lifetime in seconds.",
    )
    # Present only when the client is not a browser. Browser sessions receive the
    # refresh token as an HttpOnly cookie, which JavaScript cannot exfiltrate.
    refresh_token: str | None = None
    user: "UserResponse | None" = None


class RefreshRequest(BaseModel):
    """Optional body. Browsers send nothing and rely on the cookie."""

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    all_devices: bool = Field(
        default=False,
        description="Revoke every session for this account, not just the current one.",
    )


# ---------------------------------------------------------------------------
# Password reset and email verification
# ---------------------------------------------------------------------------
class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(_PasswordPolicyMixin):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]
    password: Password
    confirm_password: Password

    @model_validator(mode="after")
    def _passwords_match(self) -> Self:
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class ChangePasswordRequest(_PasswordPolicyMixin):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    password: Password
    confirm_password: Password

    @model_validator(mode="after")
    def _passwords_match(self) -> Self:
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self

    @model_validator(mode="after")
    def _must_differ(self) -> Self:
        if self.password == self.current_password:
            raise ValueError("New password must differ from the current one")
        return self


class VerifyEmailRequest(BaseModel):
    token: Annotated[str, StringConstraints(min_length=20, max_length=200)]


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# ---------------------------------------------------------------------------
# One-time passcodes
# ---------------------------------------------------------------------------
# Length comes from settings so the constraint and the generator cannot drift.
OtpCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=settings.OTP_LENGTH,
        max_length=settings.OTP_LENGTH,
        pattern=rf"^[0-9]{{{settings.OTP_LENGTH}}}$",
    ),
]


class _OtpNormaliserMixin(BaseModel):
    """Accepts what people actually paste.

    Mail clients and password managers add spaces, and users copy '123 456' or
    '123-456' straight out of the message. Rejecting those teaches the user their
    correct code is wrong, so strip the separators before validating rather than
    after.
    """

    @field_validator("code", mode="before", check_fields=False)
    @classmethod
    def _strip_separators(cls, v: object) -> object:
        if isinstance(v, str):
            return "".join(ch for ch in v if not ch.isspace() and ch != "-")
        return v


class VerifyOtpRequest(_OtpNormaliserMixin):
    """``POST /auth/verify-otp`` — confirms an email address after registration."""

    email: EmailStr
    code: OtpCode


class ResendOtpRequest(BaseModel):
    """``POST /auth/resend-otp``.

    The purpose is explicit so a resend cannot silently re-issue the wrong
    challenge for an account that has both live.
    """

    email: EmailStr
    purpose: OtpPurpose = OtpPurpose.EMAIL_VERIFY


class VerifyResetOtpRequest(_OtpNormaliserMixin):
    """``POST /auth/verify-reset-otp`` — spends the reset code and returns the
    ticket that ``/auth/reset-password`` accepts."""

    email: EmailStr
    code: OtpCode


class ResetTicketResponse(BaseModel):
    """Proof that a reset code was verified.

    The code is spent the moment it is checked; this short-lived opaque ticket
    carries the authorisation forward to the password form. Handing back a ticket
    rather than accepting a new password in the same call keeps the reset
    endpoint — and its password policy — exactly as it was.
    """

    reset_token: str
    expires_in: int = Field(
        default=settings.RESET_TICKET_EXPIRE_MINUTES * 60,
        description="Ticket lifetime in seconds.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class UserResponse(BaseModel):
    """The public projection of a user.

    Note what is absent: ``password_hash``, every token hash, the MFA secret and
    the failed-attempt counters. Serialising the ORM object directly would leak
    all of them, which is why this model enumerates fields explicitly.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: str | None = None
    role: UserRole
    status: UserStatus
    jurisdiction_code: str | None = None
    email_verified: bool
    mfa_enabled: bool
    last_login_at: datetime | None = None
    created_at: datetime


class MeResponse(UserResponse):
    """``/auth/me`` additionally returns the resolved permission set so the
    frontend can hide controls the server would refuse anyway."""

    permissions: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    ip_address: str | None = None
    device_label: str | None = None
    user_agent: str | None = None
    current: bool = False


class MessageResponse(BaseModel):
    message: str
    code: str | None = None


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    page: int
    page_size: int


TokenResponse.model_rebuild()
