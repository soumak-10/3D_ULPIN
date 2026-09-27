"""Role-based access control.

The permission matrix is data, not scattered ``if role ==`` checks. That keeps the
policy auditable in one place and testable without spinning up the API.

Two orthogonal dimensions are enforced:

1. **Capability** — does this role hold the permission at all?
2. **Scope** — may this principal act on *this* row? A PROPERTY_OFFICER holds
   ``building:update`` but only inside their own jurisdiction; an OWNER holds
   ``unit:read`` but only for units they hold title to.

Capability alone is never sufficient. Endpoints check the permission, services
check the scope.
"""

from __future__ import annotations

import enum
from typing import Final


class Role(str, enum.Enum):
    """Mirrors the ``user_role`` enum in 01_types.sql. Keep them in lockstep."""

    ADMIN = "ADMIN"
    PROPERTY_OFFICER = "PROPERTY_OFFICER"
    OWNER = "OWNER"
    TENANT = "TENANT"
    AUDITOR = "AUDITOR"
    SERVICE = "SERVICE"


class Permission(str, enum.Enum):
    # Users
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_ASSIGN_ROLE = "user:assign_role"

    # Buildings
    BUILDING_CREATE = "building:create"
    BUILDING_READ = "building:read"
    BUILDING_UPDATE = "building:update"
    BUILDING_DELETE = "building:delete"
    BUILDING_VERIFY = "building:verify"

    # Floors
    FLOOR_CREATE = "floor:create"
    FLOOR_READ = "floor:read"
    FLOOR_UPDATE = "floor:update"
    FLOOR_DELETE = "floor:delete"

    # Units
    UNIT_CREATE = "unit:create"
    UNIT_READ = "unit:read"
    UNIT_UPDATE = "unit:update"
    UNIT_DELETE = "unit:delete"

    # Owners and tenants
    OWNER_CREATE = "owner:create"
    OWNER_READ = "owner:read"
    OWNER_UPDATE = "owner:update"
    OWNER_DELETE = "owner:delete"
    OWNER_READ_PII = "owner:read_pii"

    TENANT_CREATE = "tenant:create"
    TENANT_READ = "tenant:read"
    TENANT_UPDATE = "tenant:update"
    TENANT_DELETE = "tenant:delete"

    OWNERSHIP_MANAGE = "ownership:manage"

    # Identifiers
    ULPIN_READ = "ulpin:read"
    ULPIN_GENERATE = "ulpin:generate"
    ULPIN_ISSUE = "ulpin:issue"
    ULPIN_SUPERSEDE = "ulpin:supersede"
    ULPIN_RETIRE = "ulpin:retire"

    # Workflow
    VERIFICATION_CREATE = "verification:create"
    VERIFICATION_READ = "verification:read"
    VERIFICATION_DECIDE = "verification:decide"

    FRAUD_READ = "fraud:read"
    FRAUD_CREATE = "fraud:create"
    FRAUD_RESOLVE = "fraud:resolve"

    # Platform
    AUDIT_READ = "audit:read"
    EXPORT_DATA = "export:data"
    ADMIN_SETTINGS = "admin:settings"


P = Permission

# Read-only set granted to anyone who can see a record at all.
_BASE_READ: Final[frozenset[Permission]] = frozenset(
    {P.BUILDING_READ, P.FLOOR_READ, P.UNIT_READ, P.ULPIN_READ}
)

_OFFICER: Final[frozenset[Permission]] = frozenset(
    {
        P.BUILDING_CREATE, P.BUILDING_READ, P.BUILDING_UPDATE, P.BUILDING_VERIFY,
        P.FLOOR_CREATE, P.FLOOR_READ, P.FLOOR_UPDATE, P.FLOOR_DELETE,
        P.UNIT_CREATE, P.UNIT_READ, P.UNIT_UPDATE, P.UNIT_DELETE,
        P.OWNER_CREATE, P.OWNER_READ, P.OWNER_UPDATE, P.OWNER_READ_PII,
        P.TENANT_CREATE, P.TENANT_READ, P.TENANT_UPDATE, P.TENANT_DELETE,
        P.OWNERSHIP_MANAGE,
        P.ULPIN_READ, P.ULPIN_GENERATE, P.ULPIN_ISSUE, P.ULPIN_SUPERSEDE,
        P.VERIFICATION_CREATE, P.VERIFICATION_READ, P.VERIFICATION_DECIDE,
        P.FRAUD_READ, P.FRAUD_CREATE,
        P.EXPORT_DATA,
        P.USER_READ,
    }
)

ROLE_PERMISSIONS: Final[dict[Role, frozenset[Permission]]] = {
    Role.ADMIN: frozenset(Permission),  # everything
    Role.PROPERTY_OFFICER: _OFFICER,
    Role.OWNER: _BASE_READ
    | frozenset(
        {
            P.OWNER_READ,
            P.TENANT_READ,
            P.TENANT_CREATE,
            P.TENANT_UPDATE,
            P.VERIFICATION_READ,
            P.VERIFICATION_CREATE,
            P.FRAUD_CREATE,  # an owner may report suspected fraud on their own unit
        }
    ),
    Role.TENANT: _BASE_READ | frozenset({P.TENANT_READ, P.FRAUD_CREATE}),
    Role.AUDITOR: _BASE_READ
    | frozenset(
        {
            P.USER_READ,
            P.OWNER_READ,
            P.TENANT_READ,
            P.VERIFICATION_READ,
            P.FRAUD_READ,
            P.AUDIT_READ,
            P.EXPORT_DATA,
        }
    ),
    Role.SERVICE: _BASE_READ | frozenset({P.OWNER_READ, P.EXPORT_DATA}),
}

# Roles whose reach is national rather than jurisdiction-scoped.
GLOBAL_SCOPE_ROLES: Final[frozenset[Role]] = frozenset({Role.ADMIN, Role.AUDITOR, Role.SERVICE})

# Roles restricted to rows they are personally attached to, regardless of
# jurisdiction. Scope checks for these consult ownership/tenancy, not geography.
SELF_SCOPE_ROLES: Final[frozenset[Role]] = frozenset({Role.OWNER, Role.TENANT})


def has_permission(role: Role | str, permission: Permission) -> bool:
    try:
        role_enum = Role(role)
    except ValueError:
        return False
    return permission in ROLE_PERMISSIONS.get(role_enum, frozenset())


def has_any_permission(role: Role | str, permissions: list[Permission]) -> bool:
    return any(has_permission(role, p) for p in permissions)


def has_all_permissions(role: Role | str, permissions: list[Permission]) -> bool:
    return all(has_permission(role, p) for p in permissions)


def permissions_for(role: Role | str) -> list[str]:
    """Flat list of permission strings, for the ``/auth/me`` payload."""
    try:
        role_enum = Role(role)
    except ValueError:
        return []
    return sorted(p.value for p in ROLE_PERMISSIONS.get(role_enum, frozenset()))


def is_global_scope(role: Role | str) -> bool:
    try:
        return Role(role) in GLOBAL_SCOPE_ROLES
    except ValueError:
        return False


def is_self_scope(role: Role | str) -> bool:
    try:
        return Role(role) in SELF_SCOPE_ROLES
    except ValueError:
        return False


def can_access_jurisdiction(
    role: Role | str,
    user_jurisdiction: str | None,
    target_jurisdiction: str | None,
) -> bool:
    """Jurisdiction containment check.

    Codes are hierarchical and dot-free, e.g. ``KA`` contains ``KA-BLR``
    contains ``KA-BLR-001``. A user scoped to a parent may act on its children.
    """
    if is_global_scope(role):
        return True
    if target_jurisdiction is None:
        return True
    if user_jurisdiction is None:
        return False
    return target_jurisdiction == user_jurisdiction or target_jurisdiction.startswith(
        f"{user_jurisdiction}-"
    )
