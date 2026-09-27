"""Python mirrors of the PostgreSQL enum types declared in 01_types.sql.

These are declared with ``create_type=False`` at the column level, because the
types already exist in the database — SQLAlchemy must reference them, never
attempt to create them.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    PROPERTY_OFFICER = "PROPERTY_OFFICER"
    OWNER = "OWNER"
    TENANT = "TENANT"
    AUDITOR = "AUDITOR"
    SERVICE = "SERVICE"


class UserStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    LOCKED = "LOCKED"
    DEACTIVATED = "DEACTIVATED"


class BuildingStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_VERIFICATION = "UNDER_VERIFICATION"
    VERIFIED = "VERIFIED"
    REGISTERED = "REGISTERED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class BuildingUse(str, enum.Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    MIXED_USE = "MIXED_USE"
    INSTITUTIONAL = "INSTITUTIONAL"
    PUBLIC = "PUBLIC"


class ConstructionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    UNDER_CONSTRUCTION = "UNDER_CONSTRUCTION"
    COMPLETED = "COMPLETED"
    OCCUPIED = "OCCUPIED"
    DEMOLISHED = "DEMOLISHED"


class FloorType(str, enum.Enum):
    BASEMENT = "BASEMENT"
    GROUND = "GROUND"
    MEZZANINE = "MEZZANINE"
    UPPER = "UPPER"
    TERRACE = "TERRACE"
    AIR_RIGHTS = "AIR_RIGHTS"
    STILT = "STILT"
    PODIUM = "PODIUM"


class UnitType(str, enum.Enum):
    """The three property types the registration form exposes, plus the
    ancillary kinds a real tower contains."""

    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    PARKING = "PARKING"
    STORAGE = "STORAGE"
    UTILITY = "UTILITY"
    COMMON_AREA = "COMMON_AREA"
    MIXED = "MIXED"


# Exposed in the public "Property Type" dropdown. The rest are assigned by
# officers during plan digitisation and are not citizen-selectable.
PUBLIC_UNIT_TYPES: frozenset[UnitType] = frozenset(
    {UnitType.RESIDENTIAL, UnitType.COMMERCIAL, UnitType.INDUSTRIAL}
)


class UnitStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    UNDER_DISPUTE = "UNDER_DISPUTE"
    MERGED = "MERGED"
    SUBDIVIDED = "SUBDIVIDED"
    DEMOLISHED = "DEMOLISHED"


class OccupancyStatus(str, enum.Enum):
    OCCUPIED = "OCCUPIED"
    VACANT = "VACANT"
    UNDER_RENOVATION = "UNDER_RENOVATION"
    SEALED = "SEALED"


class OwnerType(str, enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    JOINT = "JOINT"
    COMPANY = "COMPANY"
    TRUST = "TRUST"
    SOCIETY = "SOCIETY"
    GOVERNMENT = "GOVERNMENT"
    HUF = "HUF"


class OwnershipMode(str, enum.Enum):
    SOLE = "SOLE"
    JOINT_TENANCY = "JOINT_TENANCY"
    TENANCY_IN_COMMON = "TENANCY_IN_COMMON"
    LEASEHOLD = "LEASEHOLD"
    COOPERATIVE = "COOPERATIVE"


class TenancyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"
    PENDING = "PENDING"


class UlpinType(str, enum.Enum):
    PARCEL_2D = "PARCEL_2D"
    BUILDING = "BUILDING"
    FLOOR = "FLOOR"
    UNIT_3D = "UNIT_3D"


class UlpinStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PROVISIONAL = "PROVISIONAL"
    VERIFIED = "VERIFIED"
    ISSUED = "ISSUED"
    SUSPENDED = "SUSPENDED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


class VerificationType(str, enum.Enum):
    DOCUMENT = "DOCUMENT"
    FIELD_SURVEY = "FIELD_SURVEY"
    GEOMETRY = "GEOMETRY"
    IDENTITY = "IDENTITY"
    OWNERSHIP = "OWNERSHIP"
    AUTOMATED = "AUTOMATED"


class VerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    WAIVED = "WAIVED"


class AlertSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    RESOLVED = "RESOLVED"


class VerificationOutcome(str, enum.Enum):
    """The four answers the verification engine is allowed to give.

    Deliberately distinct from ``VerificationStatus``, which tracks the progress
    of one *check*. This is the verdict on the property as a whole, and it is
    what the citizen-facing page prints.
    """

    VERIFIED = "VERIFIED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    INVALID_CLAIM = "INVALID_CLAIM"
    UNAUTHORIZED_OCCUPANCY = "UNAUTHORIZED_OCCUPANCY"


class FraudRuleCode(str, enum.Enum):
    """The rule that fired, not the shape of the problem.

    Storing the rule code rather than a free-text reason is what lets an alert
    be re-evaluated later: when a rule is corrected, every alert it raised can
    be found and re-run.
    """

    MULTIPLE_OWNERS = "MULTIPLE_OWNERS"
    DUPLICATE_ULPIN = "DUPLICATE_ULPIN"
    OWNERSHIP_MISMATCH = "OWNERSHIP_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    UNAUTHORIZED_OCCUPANCY = "UNAUTHORIZED_OCCUPANCY"


class AuditAction(str, enum.Enum):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    VERIFY = "VERIFY"
    ISSUE = "ISSUE"
    EXPORT = "EXPORT"
