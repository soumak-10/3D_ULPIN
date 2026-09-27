"""Domain exception hierarchy.

Every exception carries a stable machine-readable ``code`` so clients branch on a
value rather than on prose. The handlers in ``error_handlers.py`` render these as
RFC 9457 ``application/problem+json``.
"""

from __future__ import annotations

from typing import Any


class ULPINException(Exception):
    """Base for everything this application raises deliberately."""

    status_code: int = 500
    code: str = "internal_error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        errors: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.detail = detail or self.title
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.errors = errors or {}
        self.headers = headers or {}
        super().__init__(self.detail)

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"https://docs.ulpin.gov.in/errors/{self.code}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "code": self.code,
        }
        if instance:
            problem["instance"] = instance
        if self.errors:
            problem["errors"] = self.errors
        return problem


# ---------------------------------------------------------------------------
# 400 family
# ---------------------------------------------------------------------------
class BadRequestException(ULPINException):
    status_code = 400
    code = "bad_request"
    title = "Bad Request"


class ValidationException(ULPINException):
    status_code = 422
    code = "validation_error"
    title = "Validation Failed"


class WeakPasswordException(ValidationException):
    code = "weak_password"
    title = "Password Does Not Meet Policy"


# ---------------------------------------------------------------------------
# 401 / 403
# ---------------------------------------------------------------------------
class AuthenticationException(ULPINException):
    status_code = 401
    code = "unauthenticated"
    title = "Authentication Required"

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(detail, **kwargs)


class InvalidCredentialsException(AuthenticationException):
    code = "invalid_credentials"
    title = "Invalid Credentials"

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        # Never distinguishes "no such user" from "wrong password" — that
        # distinction is an account enumeration oracle.
        super().__init__(detail or "Incorrect email or password", **kwargs)


class InvalidTokenException(AuthenticationException):
    code = "invalid_token"
    title = "Invalid Token"


class TokenExpiredException(AuthenticationException):
    code = "token_expired"
    title = "Token Expired"


class TokenReuseException(AuthenticationException):
    code = "token_reuse_detected"
    title = "Token Reuse Detected"

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            detail or "This session has been revoked. Please sign in again.",
            **kwargs,
        )


class AccountLockedException(AuthenticationException):
    status_code = 423
    code = "account_locked"
    title = "Account Locked"


class AccountInactiveException(AuthenticationException):
    status_code = 403
    code = "account_inactive"
    title = "Account Not Active"


class EmailNotVerifiedException(AuthenticationException):
    status_code = 403
    code = "email_not_verified"
    title = "Email Address Not Verified"


class PermissionDeniedException(ULPINException):
    status_code = 403
    code = "permission_denied"
    title = "Permission Denied"


class JurisdictionDeniedException(PermissionDeniedException):
    code = "jurisdiction_denied"
    title = "Outside Your Jurisdiction"


# ---------------------------------------------------------------------------
# 404 / 409
# ---------------------------------------------------------------------------
class NotFoundException(ULPINException):
    status_code = 404
    code = "not_found"
    title = "Resource Not Found"

    def __init__(self, resource: str = "Resource", identifier: Any = None, **kwargs: Any) -> None:
        detail = f"{resource} not found" if identifier is None else f"{resource} '{identifier}' not found"
        super().__init__(detail, **kwargs)


class ConflictException(ULPINException):
    status_code = 409
    code = "conflict"
    title = "Conflict"


class DuplicateEmailException(ConflictException):
    code = "email_taken"
    title = "Email Already Registered"


class DuplicateResourceException(ConflictException):
    code = "duplicate_resource"
    title = "Resource Already Exists"


# ---------------------------------------------------------------------------
# 429
# ---------------------------------------------------------------------------
class RateLimitException(ULPINException):
    status_code = 429
    code = "rate_limited"
    title = "Too Many Requests"

    def __init__(self, detail: str | None = None, retry_after: int = 60, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {})["Retry-After"] = str(retry_after)
        super().__init__(detail or "Too many requests. Please slow down.", **kwargs)


# ---------------------------------------------------------------------------
# Domain-specific
# ---------------------------------------------------------------------------
class ULPINGenerationException(ULPINException):
    status_code = 422
    code = "ulpin_generation_failed"
    title = "Identifier Generation Failed"


class GeometryException(ULPINException):
    status_code = 422
    code = "invalid_geometry"
    title = "Invalid Geometry"


class ImmutableRecordException(ConflictException):
    code = "record_immutable"
    title = "Record Is Immutable"

    def __init__(self, detail: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            detail or "This record carries an issued identifier and cannot be modified. Supersede it instead.",
            **kwargs,
        )
