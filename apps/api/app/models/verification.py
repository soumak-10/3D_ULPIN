"""Verification records: the evidence trail behind a verdict.

Two vocabularies live here and keeping them apart is the point:

``VerificationStatus``
    The state of *one check*. A document check PASSED. A site visit is PENDING.

``VerificationOutcome``
    The engine's verdict on *the property*. VERIFIED, PENDING_VERIFICATION,
    INVALID_CLAIM, UNAUTHORIZED_OCCUPANCY.

Collapsing them would make "the geometry check failed" and "this claim is
fraudulent" the same sentence, which is how a clerical error becomes an
accusation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
from app.models.enums import VerificationOutcome, VerificationStatus, VerificationType

if TYPE_CHECKING:  # pragma: no cover
    from app.models.ulpin import Ulpin

SCHEMA = "ulpin"


def _pg_enum(enum_cls: type, name: str) -> PGEnum:
    return PGEnum(
        enum_cls,
        name=name,
        schema=SCHEMA,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class VerificationRecord(Base):
    """One verification attempt against one identifier.

    Records accumulate; they are never updated in place once closed. "Was this
    property verified in March?" has to stay answerable after a re-verification
    in September, because a transaction that relied on the March answer is still
    legally live.
    """

    __tablename__ = "verification_records"

    verification_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    ulpin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ulpins.ulpin_id", ondelete="CASCADE"),
        nullable=False,
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

    verification_type: Mapped[VerificationType] = mapped_column(
        _pg_enum(VerificationType, "verification_type"), nullable=False
    )
    status: Mapped[VerificationStatus] = mapped_column(
        _pg_enum(VerificationStatus, "verification_status"),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    outcome: Mapped[VerificationOutcome | None] = mapped_column(
        _pg_enum(VerificationOutcome, "verification_outcome")
    )

    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    method: Mapped[str | None] = mapped_column(String(64))
    reference_no: Mapped[str | None] = mapped_column(String(64))
    remarks: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    evidence_uri: Mapped[str | None] = mapped_column(Text)
    evidence_sha256: Mapped[bytes | None] = mapped_column()

    captured_location: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )
    captured_accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    checks_passed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    checks_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # The per-check detail behind the verdict: which rule ran, what it compared,
    # what it concluded. This is what an officer reads when a citizen disputes
    # the result, so it is structured rather than prose.
    findings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ulpin: Mapped["Ulpin"] = relationship(lazy="selectin", viewonly=True)

    __table_args__ = (
        CheckConstraint("checks_passed >= 0 AND checks_failed >= 0", name="verif_counts"),
        Index("idx_verif_ulpin", "ulpin_id", "requested_at"),
        Index("idx_verif_unit", "unit_id"),
        Index("idx_verif_status", "status"),
        {"schema": SCHEMA},
    )

    @property
    def is_closed(self) -> bool:
        return self.status in (
            VerificationStatus.PASSED,
            VerificationStatus.FAILED,
            VerificationStatus.WAIVED,
        )

    @property
    def total_checks(self) -> int:
        return self.checks_passed + self.checks_failed
