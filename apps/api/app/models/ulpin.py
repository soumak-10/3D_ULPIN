"""ULPIN registry models: the issued identifier and its allocation counter.

An identifier is not an attribute of a property — it is a record in its own
right, with a lifecycle (draft → provisional → issued → superseded/retired),
an issuer, a timestamp and a lineage. Storing it on ``units.ulpin_code`` would
make all of that unrepresentable: you cannot supersede a varchar.

Two codes live on every row and both are unique:

``ulpin_code``   ``IN29BLR0001234-A1-F007-U0412-Y``
    Canonical. Carries the parent parcel unmodified so the existing 2D cadastre
    can resolve it by slicing the first 14 characters.

``short_code``   ``WB-KOL-B001-F03-U301``
    Public. What a citizen reads off a notice and types into the search box.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import UlpinStatus, UlpinType

if TYPE_CHECKING:  # pragma: no cover
    from app.models.property import Building, Floor, Unit

SCHEMA = "ulpin"

# Statuses in which a code is the current answer to "what identifies this unit?".
# Superseded and retired codes still resolve — a deed quotes the code that was
# current when it was signed — but must never be returned as the live answer.
# Defined once here so the repository filters and the ``is_live`` property cannot
# drift apart.
LIVE_ULPIN_STATUSES: tuple[UlpinStatus, ...] = (
    UlpinStatus.PROVISIONAL,
    UlpinStatus.VERIFIED,
    UlpinStatus.ISSUED,
)


def _pg_enum(enum_cls: type, name: str) -> PGEnum:
    return PGEnum(
        enum_cls,
        name=name,
        schema=SCHEMA,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Ulpin(Base):
    """One issued identifier.

    The subject columns are all nullable and exactly one is populated, matching
    ``ulpin_type``. A single table rather than three is the right shape because
    uniqueness, lineage and supersession are properties of *identifiers*, and
    splitting them by subject would mean maintaining that logic three times.
    """

    __tablename__ = "ulpins"

    ulpin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    ulpin_code: Mapped[str] = mapped_column(String(32), nullable=False)
    ulpin_type: Mapped[UlpinType] = mapped_column(
        _pg_enum(UlpinType, "ulpin_type"), nullable=False
    )
    status: Mapped[UlpinStatus] = mapped_column(
        _pg_enum(UlpinStatus, "ulpin_status"), nullable=False, server_default=text("'DRAFT'")
    )

    # -- Decomposed canonical segments ---------------------------------------
    # Stored rather than parsed on demand: "every unit on storey F007 of this
    # parcel" is a range scan over indexed columns, and string surgery in a
    # WHERE clause is not.
    parent_ulpin: Mapped[str] = mapped_column(CHAR(14), nullable=False)
    block_code: Mapped[str | None] = mapped_column(CHAR(2))
    storey_code: Mapped[str | None] = mapped_column(CHAR(4))
    unit_code: Mapped[str | None] = mapped_column(CHAR(5))
    check_char: Mapped[str | None] = mapped_column(CHAR(1))

    # -- Short code ----------------------------------------------------------
    short_code: Mapped[str | None] = mapped_column(String(24))
    building_short_code: Mapped[str | None] = mapped_column(String(12))
    state_code: Mapped[str | None] = mapped_column(CHAR(2))
    city_code: Mapped[str | None] = mapped_column(CHAR(3))

    # -- Subject (exactly one, per ck_ulpins_subject) -------------------------
    building_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.buildings.building_id", ondelete="CASCADE"),
    )
    floor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.floors.floor_id", ondelete="CASCADE")
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.units.unit_id", ondelete="CASCADE")
    )

    # -- Lineage -------------------------------------------------------------
    parent_ulpin_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ulpins.ulpin_id", ondelete="SET NULL")
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.ulpins.ulpin_id", ondelete="SET NULL")
    )

    jurisdiction_code: Mapped[str] = mapped_column(String(16), nullable=False)
    centroid: Mapped[str | None] = mapped_column(
        Geometry("POINTZ", srid=4326, dimension=3, spatial_index=False)
    )

    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retirement_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    unit: Mapped["Unit | None"] = relationship(
        primaryjoin="Ulpin.unit_id == Unit.unit_id", lazy="selectin", viewonly=True
    )
    building: Mapped["Building | None"] = relationship(
        primaryjoin="Ulpin.building_id == Building.building_id", lazy="selectin", viewonly=True
    )
    floor: Mapped["Floor | None"] = relationship(
        primaryjoin="Ulpin.floor_id == Floor.floor_id", lazy="selectin", viewonly=True
    )

    __table_args__ = (
        UniqueConstraint("ulpin_code", name="uq_ulpins_code"),
        Index("idx_ulpins_parent", "parent_ulpin"),
        Index("idx_ulpins_unit", "unit_id"),
        Index("idx_ulpins_building_subject", "building_id"),
        Index("idx_ulpins_status", "status"),
        {"schema": SCHEMA},
    )

    @property
    def is_live(self) -> bool:
        """An identifier that still names the current state of a property.

        Superseded and retired codes must keep resolving — a deed quotes the
        code that was current when it was signed — but they must never be
        returned as the answer to "what identifies this unit now".
        """
        return self.status in LIVE_ULPIN_STATUSES

    @property
    def display_code(self) -> str:
        """What the UI shows. The short form when there is one, because it is
        the form the citizen will be asked to quote back."""
        return self.short_code or self.ulpin_code


class UlpinSequence(Base):
    """Per-region building counter.

    A PostgreSQL ``SEQUENCE`` is the wrong tool: it is global where this counter
    is per-region, and it is deliberately non-transactional, so a registration
    that rolls back would burn a number and leave a permanent hole in the
    register. Officers notice holes and file tickets about them.

    A counter row taken with ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING``
    is atomic, serialises concurrent allocators on the row lock, and rolls back
    cleanly with the transaction that failed.
    """

    __tablename__ = "ulpin_sequences"

    state_code: Mapped[str] = mapped_column(CHAR(2), primary_key=True)
    city_code: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("last_value >= 0 AND last_value <= 9999", name="ulpin_sequences_value"),
        {"schema": SCHEMA},
    )
