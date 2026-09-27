"""Shared FastAPI dependencies: the authenticated principal and the guards.

The chain is deliberate:

``bearer token`` → decode → load user → check version → check status → principal

Every step can fail closed. In particular the ``ver`` claim is compared against
the stored ``token_version``: that is what makes "sign out everywhere" and
"password changed" take effect immediately, even though access tokens are
stateless and un-revocable on their own.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountInactiveException,
    AccountLockedException,
    AuthenticationException,
    EmailNotVerifiedException,
    InvalidTokenException,
    JurisdictionDeniedException,
    PermissionDeniedException,
)
from app.core.permissions import (
    Permission,
    Role,
    can_access_jurisdiction,
    has_permission,
)
from app.core.security import decode_access_token
from app.db.session import get_db, set_audit_context
from app.models.enums import UserStatus
from app.models.user import User
from app.repositories.user_repository import UserRepository

# auto_error=False so a missing header raises our own problem+json response
# rather than FastAPI's bare {"detail": "Not authenticated"}.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_db)]
BearerToken = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_current_user(
    request: Request,
    session: DbSession,
    credentials: BearerToken,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationException("Authentication credentials were not supplied")

    payload = decode_access_token(credentials.credentials)

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise InvalidTokenException("Token subject is not a valid identifier") from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        # The account was deleted after the token was minted.
        raise InvalidTokenException("This account no longer exists")

    if int(payload.get("ver", -1)) != int(user.token_version or 0):
        # Password changed, sessions revoked, or the token predates a forced
        # rotation. Stateless tokens cannot be blocklisted; the version counter
        # is what ends them.
        raise InvalidTokenException("This session has been superseded. Please sign in again.")

    if user.is_locked:
        raise AccountLockedException("This account is temporarily locked")

    if user.status in (UserStatus.SUSPENDED, UserStatus.DEACTIVATED):
        raise AccountInactiveException(f"This account is {user.status.value.lower()}")

    # Give the audit triggers an actor for every write this request performs.
    await set_audit_context(
        session,
        user_id=str(user.user_id),
        user_role=user.role.value,
        request_id=getattr(request.state, "request_id", None),
    )
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
    request: Request,
    session: DbSession,
    credentials: BearerToken,
) -> User | None:
    """For endpoints that serve both public and authenticated callers."""
    if credentials is None:
        return None
    try:
        return await get_current_user(request, session, credentials)
    except AuthenticationException:
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


async def get_verified_user(user: CurrentUser) -> User:
    """Requires a confirmed mailbox. Applied to anything that changes records."""
    if not user.email_verified:
        raise EmailNotVerifiedException(
            "Confirm your email address before performing this action"
        )
    return user


VerifiedUser = Annotated[User, Depends(get_verified_user)]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
# These are factories that return a closure, not callable classes. The
# distinction matters: FastAPI resolves a dependency's annotations with
# ``getattr(call, "__globals__", {})``, and a class or a class *instance* has no
# ``__globals__``. Combined with ``from __future__ import annotations`` at the
# top of this module, a callable-class dependency leaves ``CurrentUser`` as an
# unresolved string and OpenAPI generation fails. A closure is a function, so it
# carries this module's globals and resolves correctly.
def RequirePermission(  # noqa: N802 - reads as a type at the call site
    *permissions: Permission, require_all: bool = True
) -> Callable[[User], Awaitable[User]]:
    """Enforce one or more permissions from the RBAC matrix.

    Usage::

        CanCreateBuilding = Annotated[
            User, Depends(RequirePermission(Permission.BUILDING_CREATE))
        ]

    This checks *capability* only. Row-level scope — is this building inside the
    officer's jurisdiction — is checked in the service, because only the service
    knows which row is involved. Returns the principal, so a handler can take
    the check and the actor in a single parameter.
    """

    async def guard(user: CurrentUser) -> User:
        checks = [has_permission(user.role.value, p) for p in permissions]
        ok = all(checks) if require_all else any(checks)
        if not ok:
            missing = [
                p.value for p, granted in zip(permissions, checks, strict=True) if not granted
            ]
            raise PermissionDeniedException(
                f"Your role ({user.role.value}) lacks: {', '.join(missing)}"
            )
        return user

    joiner = " and " if require_all else " or "
    guard.__name__ = "require_" + "_".join(p.name.lower() for p in permissions)
    guard.__doc__ = f"Requires {joiner.join(p.value for p in permissions)}."
    return guard


def RequireRole(*roles: Role) -> Callable[[User], Awaitable[User]]:  # noqa: N802
    """Coarse guard for endpoints that are role-shaped rather than
    permission-shaped, such as the admin console."""
    allowed = {r.value for r in roles}

    async def guard(user: CurrentUser) -> User:
        if user.role.value not in allowed:
            raise PermissionDeniedException(
                f"This endpoint requires one of: {', '.join(sorted(allowed))}"
            )
        return user

    guard.__name__ = "require_role_" + "_".join(r.name.lower() for r in roles)
    guard.__doc__ = f"Requires one of: {', '.join(sorted(allowed))}."
    return guard


def require_jurisdiction(user: User, target_jurisdiction: str | None) -> None:
    """Assert the principal's authority reaches a record's jurisdiction.

    Call this from services once the target row is loaded. ADMIN, AUDITOR and
    SERVICE pass unconditionally; a PROPERTY_OFFICER scoped to ``KA-BLR`` passes
    for ``KA-BLR-001`` and fails for ``MH-MUM-004``.
    """
    if not can_access_jurisdiction(user.role.value, user.jurisdiction_code, target_jurisdiction):
        raise JurisdictionDeniedException(
            f"Your jurisdiction ({user.jurisdiction_code}) does not cover "
            f"{target_jurisdiction}"
        )


# ---------------------------------------------------------------------------
# Common query parameters
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PaginationParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


# A function, not ``Depends()`` on the class, for the same ``__globals__``
# reason as the guards above.
def pagination_params(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, description="Rows per page")] = 25,
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


Pagination = Annotated[PaginationParams, Depends(pagination_params)]


def client_ip(request: Request) -> str | None:
    """Caller IP, honouring the first hop of X-Forwarded-For.

    Only trustworthy behind a proxy that overwrites the header; the nginx config
    in ``infra/nginx`` does. Direct exposure would let a client forge it.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


ClientIP = Annotated[str | None, Depends(client_ip)]
