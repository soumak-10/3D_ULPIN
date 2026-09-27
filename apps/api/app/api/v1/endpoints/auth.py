"""Authentication endpoints.

Cookie policy: browsers receive the refresh token as an ``HttpOnly`` cookie and
never see it in a response body. Non-browser clients (mobile, service
integrations) opt out with ``X-Client-Type: native`` and get it in the payload
instead. That keeps the token out of reach of injected JavaScript for the client
that is actually exposed to XSS.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.deps import (
    ClientIP,
    CurrentUser,
    DbSession,
    Pagination,
    RequireRole,
)
from app.core.config import settings
from app.core.permissions import Role, permissions_for
from app.models.enums import UserRole, UserStatus
from app.schemas.auth import (
    AdminCreateUserRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    ResendOtpRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    ResetTicketResponse,
    SessionResponse,
    TokenResponse,
    UserListResponse,
    UserResponse,
    VerifyEmailRequest,
    VerifyOtpRequest,
    VerifyResetOtpRequest,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])

UserAgent = Annotated[str | None, Header(alias="User-Agent")]
ClientType = Annotated[str | None, Header(alias="X-Client-Type")]


def _is_native(client_type: str | None) -> bool:
    return (client_type or "").lower() in {"native", "mobile", "service"}


def _set_refresh_cookie(response: Response, raw_token: str, *, max_age: int) -> None:
    response.set_cookie(
        key=settings.COOKIE_NAME_REFRESH,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        path=f"{settings.API_V1_PREFIX}/auth",  # never sent to unrelated endpoints
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.COOKIE_NAME_REFRESH,
        domain=settings.COOKIE_DOMAIN,
        path=f"{settings.API_V1_PREFIX}/auth",
    )


def _token_payload(
    response: Response,
    *,
    access: str,
    refresh: str,
    user,  # noqa: ANN001 — ORM model, serialised below
    native: bool,
    remember: bool = True,
) -> TokenResponse:
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400 if remember else 86400
    if not native:
        _set_refresh_cookie(response, refresh, max_age=max_age)
    return TokenResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=refresh if native else None,
        user=UserResponse.model_validate(user),
    )


# ===========================================================================
# Register
# ===========================================================================
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={409: {"description": "Email already registered"}},
)
async def register(
    payload: RegisterRequest,
    session: DbSession,
    ip: ClientIP,
) -> UserResponse:
    """Self-service registration, limited to OWNER and TENANT roles.

    The account starts PENDING and cannot sign in until the emailed six-digit
    code is confirmed at ``/auth/verify-otp``.
    """
    user, _code = await AuthService(session).register(payload, ip=ip)
    return UserResponse.model_validate(user)


# ===========================================================================
# Login / refresh / logout
# ===========================================================================
@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a token pair",
    responses={
        401: {"description": "Invalid credentials"},
        403: {
            "description": (
                "Account suspended or deactivated, or the email address has not "
                "been verified (code `email_not_verified`)"
            )
        },
        423: {"description": "Account locked after repeated failures"},
    },
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
    ip: ClientIP,
    user_agent: UserAgent = None,
    client_type: ClientType = None,
) -> TokenResponse:
    user, access, refresh = await AuthService(session).authenticate(
        payload, ip=ip, user_agent=user_agent
    )
    return _token_payload(
        response,
        access=access,
        refresh=refresh,
        user=user,
        native=_is_native(client_type),
        remember=payload.remember_me,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate the refresh token and mint a new access token",
    responses={401: {"description": "Token invalid, expired, or replayed"}},
)
async def refresh_tokens(
    request: Request,
    response: Response,
    session: DbSession,
    ip: ClientIP,
    payload: RefreshRequest | None = None,
    user_agent: UserAgent = None,
    client_type: ClientType = None,
) -> TokenResponse:
    """Presenting a token that was already rotated revokes every session for the
    account — that pattern only occurs when a token has been stolen."""
    raw = (payload.refresh_token if payload else None) or request.cookies.get(
        settings.COOKIE_NAME_REFRESH
    )
    if not raw:
        from app.core.exceptions import InvalidTokenException

        raise InvalidTokenException("No refresh token was supplied")

    try:
        user, access, new_refresh = await AuthService(session).refresh(
            raw, ip=ip, user_agent=user_agent
        )
    except Exception:
        # Whatever the failure, the cookie now holds a token the server will
        # never accept again. Leaving it would loop the client forever.
        _clear_refresh_cookie(response)
        raise

    return _token_payload(
        response,
        access=access,
        refresh=new_refresh,
        user=user,
        native=_is_native(client_type),
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke the current session",
)
async def logout(
    request: Request,
    response: Response,
    session: DbSession,
    payload: LogoutRequest | None = None,
) -> MessageResponse:
    """Idempotent: logging out without a valid session is still a success."""
    raw = request.cookies.get(settings.COOKIE_NAME_REFRESH)
    all_devices = bool(payload and payload.all_devices)

    revoked = await AuthService(session).logout(raw, all_devices=all_devices)
    _clear_refresh_cookie(response)

    return MessageResponse(
        message=(
            f"Signed out of {revoked} session(s)" if revoked else "Signed out"
        ),
        code="logged_out",
    )


# ===========================================================================
# Password reset
# ===========================================================================
@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request a password reset code",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    session: DbSession,
    ip: ClientIP,
) -> MessageResponse:
    """Always reports success.

    Distinguishing a known address from an unknown one here would let anyone
    test whether a given citizen holds an account. For the same reason a
    throttled request also returns 200 — a 429 would answer the question a 404
    was not allowed to.
    """
    await AuthService(session).request_password_reset_otp(str(payload.email), ip=ip)
    return MessageResponse(
        message=(
            "If an account exists for that address, a six-digit code is on its "
            "way. Check your spam folder if it does not arrive within a few "
            "minutes."
        ),
        code="reset_requested",
    )


@router.post(
    "/verify-reset-otp",
    response_model=ResetTicketResponse,
    summary="Exchange a password reset code for a reset ticket",
    responses={401: {"description": "Code incorrect, expired, or already used"}},
)
async def verify_reset_otp(
    payload: VerifyResetOtpRequest, session: DbSession
) -> ResetTicketResponse:
    """Spends the code and returns a short-lived ticket.

    The password itself is submitted separately to ``/auth/reset-password``, so
    the code is already invalid by the time the new password is chosen.
    """
    ticket = await AuthService(session).verify_reset_otp(str(payload.email), payload.code)
    return ResetTicketResponse(reset_token=ticket)


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password using a reset token",
    responses={401: {"description": "Reset link invalid or expired"}},
)
async def reset_password(
    payload: ResetPasswordRequest,
    response: Response,
    session: DbSession,
) -> MessageResponse:
    await AuthService(session).reset_password(payload.token, payload.password)
    _clear_refresh_cookie(response)
    return MessageResponse(
        message="Password updated. All other sessions have been signed out.",
        code="password_reset",
    )


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Change your own password",
)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    session: DbSession,
    user: CurrentUser,
) -> MessageResponse:
    await AuthService(session).change_password(
        user, payload.current_password, payload.password
    )
    _clear_refresh_cookie(response)
    return MessageResponse(
        message="Password changed. Please sign in again.",
        code="password_changed",
    )


# ===========================================================================
# Email verification
# ===========================================================================
@router.post(
    "/verify-otp",
    response_model=MessageResponse,
    summary="Confirm an email address with the emailed six-digit code",
    responses={401: {"description": "Code incorrect, expired, or already used"}},
)
async def verify_otp(payload: VerifyOtpRequest, session: DbSession) -> MessageResponse:
    """Activates the account. Submitting a code for an already-verified address
    succeeds rather than erroring — a second tab should not be a failure."""
    user = await AuthService(session).verify_email_otp(str(payload.email), payload.code)
    return MessageResponse(
        message=f"{user.email} confirmed. You can now sign in.",
        code="email_verified",
    )


@router.post(
    "/resend-otp",
    response_model=MessageResponse,
    summary="Send another one-time code",
    responses={429: {"description": "Cooldown or hourly cap reached"}},
)
async def resend_otp(payload: ResendOtpRequest, session: DbSession) -> MessageResponse:
    """Issues a fresh code, which invalidates the previous one.

    ``purpose`` selects the challenge; the default re-sends the registration
    code.
    """
    await AuthService(session).resend_otp(str(payload.email), payload.purpose)
    return MessageResponse(
        message="If that address needs a code, a new one has been sent.",
        code="otp_resent",
    )


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    summary="Confirm an email address with a link token",
)
async def verify_email(payload: VerifyEmailRequest, session: DbSession) -> MessageResponse:
    """Retained for the link-based flow used by administrative invitations. The
    public registration path uses ``/auth/verify-otp``."""
    user = await AuthService(session).verify_email(payload.token)
    return MessageResponse(
        message=f"{user.email} confirmed. Your account is now active.",
        code="email_verified",
    )


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="Send another verification link",
)
async def resend_verification(
    payload: ResendVerificationRequest, session: DbSession
) -> MessageResponse:
    await AuthService(session).resend_verification(str(payload.email))
    return MessageResponse(
        message="If that address needs confirming, a new link has been sent.",
        code="verification_resent",
    )


# ===========================================================================
# Current principal
# ===========================================================================
@router.get(
    "/me",
    response_model=MeResponse,
    summary="The authenticated user and their resolved permissions",
)
async def read_me(user: CurrentUser) -> MeResponse:
    return MeResponse(
        **UserResponse.model_validate(user).model_dump(),
        permissions=permissions_for(user.role.value),
    )


@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List active sessions for the current user",
)
async def list_sessions(
    request: Request,
    session: DbSession,
    user: CurrentUser,
) -> list[SessionResponse]:
    from app.core.security import hash_token

    raw = request.cookies.get(settings.COOKIE_NAME_REFRESH)
    current_hash = hash_token(raw) if raw else None

    rows = await AuthService(session).list_sessions(user.user_id, current_hash=current_hash)
    return [
        SessionResponse.model_validate(token).model_copy(update={"current": is_current})
        for token, is_current in rows
    ]


@router.delete(
    "/sessions/{token_id}",
    response_model=MessageResponse,
    summary="Revoke one of your sessions",
)
async def revoke_session(
    token_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> MessageResponse:
    await AuthService(session).revoke_session(user, token_id)
    return MessageResponse(message="Session revoked", code="session_revoked")


# ===========================================================================
# Administration
# ===========================================================================
admin_router = APIRouter(
    prefix="/admin/users",
    tags=["User Administration"],
    dependencies=[Depends(RequireRole(Role.ADMIN, Role.PROPERTY_OFFICER))],
)


@admin_router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision an account with any role",
)
async def create_user(
    payload: AdminCreateUserRequest,
    session: DbSession,
    actor: CurrentUser,
) -> UserResponse:
    user, _invite = await AuthService(session).admin_create_user(payload, actor=actor)
    return UserResponse.model_validate(user)


@admin_router.get(
    "",
    response_model=UserListResponse,
    summary="Search and page through accounts",
)
async def list_users(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    role: UserRole | None = None,
    user_status: UserStatus | None = None,
    search: str | None = None,
) -> UserListResponse:
    from app.core.permissions import is_global_scope
    from app.repositories.user_repository import UserRepository

    # An officer sees only their own jurisdiction's accounts; an admin sees all.
    scope = None if is_global_scope(actor.role.value) else actor.jurisdiction_code

    items, total = await UserRepository(session).list_users(
        page=pagination.page,
        page_size=pagination.page_size,
        role=role,
        status=user_status,
        jurisdiction_prefix=scope,
        search=search,
    )
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in items],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@admin_router.post(
    "/{user_id}/revoke-sessions",
    response_model=MessageResponse,
    dependencies=[Depends(RequireRole(Role.ADMIN))],
    summary="Force-sign-out an account everywhere",
)
async def revoke_user_sessions(user_id: uuid.UUID, session: DbSession) -> MessageResponse:
    count = await AuthService(session).logout_user(user_id)
    return MessageResponse(message=f"Revoked {count} session(s)", code="sessions_revoked")
