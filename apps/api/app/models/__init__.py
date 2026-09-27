"""Model registry.

Importing this package registers every mapper with the declarative ``Base``.
Alembic's autogenerate and SQLAlchemy's relationship resolution both need the
whole set loaded, so import from here rather than from individual modules when
order matters.
"""

from app.db.base import Base
from app.models.audit import AuditLog
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AuditAction,
    BuildingStatus,
    BuildingUse,
    ConstructionStatus,
    FloorType,
    FraudRuleCode,
    OccupancyStatus,
    OwnershipMode,
    OwnerType,
    TenancyStatus,
    UlpinStatus,
    UlpinType,
    UnitStatus,
    UnitType,
    UserRole,
    UserStatus,
    VerificationOutcome,
    VerificationStatus,
    VerificationType,
)
from app.models.fraud import OPEN_ALERT_STATUSES, FraudAlert
from app.models.property import (
    Building,
    Floor,
    Owner,
    Tenant,
    Unit,
    UnitOwnership,
)
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin, UlpinSequence
from app.models.user import RefreshToken, User
from app.models.verification import VerificationRecord

__all__ = [
    "Base",
    # Entities
    "User",
    "RefreshToken",
    "Owner",
    "Building",
    "Floor",
    "Unit",
    "UnitOwnership",
    "Tenant",
    "Ulpin",
    "UlpinSequence",
    "VerificationRecord",
    "FraudAlert",
    "AuditLog",
    "OPEN_ALERT_STATUSES",
    "LIVE_ULPIN_STATUSES",
    # Enums
    "UserRole",
    "UserStatus",
    "BuildingStatus",
    "BuildingUse",
    "ConstructionStatus",
    "FloorType",
    "UnitType",
    "UnitStatus",
    "OccupancyStatus",
    "OwnerType",
    "OwnershipMode",
    "TenancyStatus",
    "UlpinType",
    "UlpinStatus",
    "VerificationType",
    "VerificationStatus",
    "VerificationOutcome",
    "FraudRuleCode",
    "AlertSeverity",
    "AlertStatus",
    "AuditAction",
]
