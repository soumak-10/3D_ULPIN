"""Fraud alerts raised by the rule engine.

Design notes that are load-bearing:

*A rule fires on a sweep, not on a row.* Re-running the engine must not clone
every open alert, or the queue is unreadable within a day and officers stop
opening it. Each alert carries a ``fingerprint`` — rule code plus its subject —
with a partial unique index over open statuses, so a repeat detection updates
the existing alert instead of adding a duplicate.

*An alert is an allegation, not a finding.* Nothing here changes a property's
status. It raises a question for a human, and ``is_false_positive`` records the
answer, which is how rule precision gets measured rather than guessed at.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AlertSeverity, AlertStatus, FraudRuleCode

if TYPE_CHECKING:  # pragma: no cover
    from app.models.property import Building, Owner, Tenant, Unit
    from app.models.ulpin import Ulpin

SCHEMA = "ulpin"

# Statuses in which an alert is still somebody's problem. Matches the predicate
# on uq_fraud_open_fingerprint — if these drift apart, deduplication silently
# stops working, so they are defined once and imported by the service.
OPEN_ALERT_STATUSES: tuple[AlertStatus, ...] = (
    AlertStatus.OPEN,
    AlertStatus.INVESTIGATING,
)


def _pg_enum(enum_cls: type, name: str) -> PGEnum:
    return PGEnum(
        enum_cls,
        name=name,
        schema=SCHEMA,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    alert_type: Mapped[FraudRuleCode] = mapped_column(
        _pg_enum(FraudRuleCode, "fraud_alert_type"), nullable=False
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        _pg_enum(AlertSeverity, "alert_severity"),
        nullable=False,
        server_default=text("'MEDIUM'"),
    )
    status: Mapped[AlertStatus] = mapped_column(
        _pg_enum(AlertStatus, "alert_status"), nullable=False, server_default=text("'OPEN'")
    )

    # -- Subjects ------------------------------------------------------------
    # All nullable: a DUPLICATE_ULPIN alert names two identifiers and no unit,
    # while an UNAUTHORIZED_OCCUPANCY alert names a unit and a tenant.
    ulpin_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ulpins.ulpin_id", ondelete="CASCADE")
    )
    related_ulpin_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ulpins.ulpin_id", ondelete="SET NULL")
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.units.unit_id", ondelete="SET NULL")
    )
    building_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.buildings.building_id", ondelete="SET NULL")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.owners.owner_id", ondelete="SET NULL")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.tenants.tenant_id", ondelete="SET NULL")
    )

    rule_code: Mapped[str | None] = mapped_column(String(32))
    fingerprint: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    measured_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    threshold_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))

    # What the rule actually saw. An officer must be able to reconstruct the
    # decision without re-running the engine against changed data.
    evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    alert_location: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    detected_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    is_false_positive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ulpin: Mapped["Ulpin | None"] = relationship(
        primaryjoin="FraudAlert.ulpin_id == Ulpin.ulpin_id", lazy="selectin", viewonly=True
    )
    unit: Mapped["Unit | None"] = relationship(
        primaryjoin="FraudAlert.unit_id == Unit.unit_id", lazy="selectin", viewonly=True
    )
    building: Mapped["Building | None"] = relationship(
        primaryjoin="FraudAlert.building_id == Building.building_id",
        lazy="selectin",
        viewonly=True,
    )
    owner: Mapped["Owner | None"] = relationship(
        primaryjoin="FraudAlert.owner_id == Owner.owner_id", lazy="selectin", viewonly=True
    )
    tenant: Mapped["Tenant | None"] = relationship(
        primaryjoin="FraudAlert.tenant_id == Tenant.tenant_id", lazy="selectin", viewonly=True
    )

    __table_args__ = (
        CheckConstraint(
            "related_ulpin_id IS DISTINCT FROM ulpin_id", name="fraud_distinct_ulpins"
        ),
        CheckConstraint(
            "(assigned_to IS NULL) = (assigned_at IS NULL)", name="fraud_assigned_pair"
        ),
        Index("idx_fraud_status_detected", "status", "detected_at"),
        Index("idx_fraud_unit", "unit_id"),
        Index("idx_fraud_building", "building_id"),
        {"schema": SCHEMA},
    )

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_ALERT_STATUSES
