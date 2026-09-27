"""Property aggregate: buildings, floors, units, owners, tenancies, ULPINs.

Mirrors ``database/schemas/02_tables.sql``. Two structural points carry over
from the DDL and matter when reading this file:

* ``Unit.building_id`` is denormalised for query speed, but a **composite**
  foreign key to ``floors(floor_id, building_id)`` makes a unit that claims one
  building while sitting on another building's floor unrepresentable.
* Geometry columns are declared with GeoAlchemy2 so the driver round-trips WKB
  rather than strings. Measurement never happens in EPSG:4326 — ``ST_Area`` on
  degrees returns square degrees. The DB functions ``fn_area_sqm`` and
  ``fn_volume_cum`` force the metric transform, and the service calls those.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    BuildingStatus,
    BuildingUse,
    ConstructionStatus,
    FloorType,
    OccupancyStatus,
    OwnershipMode,
    OwnerType,
    TenancyStatus,
    UnitStatus,
    UnitType,
    VerificationOutcome,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User

SCHEMA = "ulpin"


def _pg_enum(enum_cls: type, name: str) -> PGEnum:
    return PGEnum(
        enum_cls,
        name=name,
        schema=SCHEMA,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


# ===========================================================================
# Owners
# ===========================================================================
class Owner(Base):
    """A legal person who can hold title. Distinct from ``users``: most owners
    in a bulk-digitised register have no login, and one login may map to one
    owner record."""

    __tablename__ = "owners"
    __table_args__ = ({"schema": SCHEMA},)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )

    owner_type: Mapped[OwnerType] = mapped_column(
        _pg_enum(OwnerType, "owner_type"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    father_or_spouse_name: Mapped[str | None] = mapped_column(String(200))
    date_of_birth: Mapped[date | None] = mapped_column(Date)

    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(20))

    # The statutory number is never stored in the clear. `national_id_hash` is
    # an HMAC under the server pepper; `last4` exists only so an officer can
    # confirm they have the right person without the number being recoverable.
    national_id_type: Mapped[str | None] = mapped_column(String(20))
    national_id_hash: Mapped[bytes | None] = mapped_column()
    national_id_last4: Mapped[str | None] = mapped_column(String(4))

    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    country: Mapped[str] = mapped_column(String(2), server_default=text("'IN'"), nullable=False)
    address_location: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )

    organisation_name: Mapped[str | None] = mapped_column(String(255))
    registration_number: Mapped[str | None] = mapped_column(String(50))

    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    risk_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User | None"] = relationship(
        back_populates="owner_profile",
        primaryjoin="foreign(Owner.user_id) == User.user_id",
        viewonly=True,
    )
    ownerships: Mapped[list["UnitOwnership"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return self.organisation_name or self.full_name


# ===========================================================================
# Buildings
# ===========================================================================
class Building(Base):
    __tablename__ = "buildings"

    building_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )

    # The 14-character 2D ULPIN of the land parcel. Every 3D identifier in this
    # tower is derived from it, unmodified, so the vertical scheme stays
    # backward compatible with the existing cadastre.
    parcel_ulpin: Mapped[str] = mapped_column(CHAR(14), nullable=False)
    block_code: Mapped[str] = mapped_column(CHAR(2), nullable=False, server_default=text("'A1'"))

    building_name: Mapped[str] = mapped_column(String(200), nullable=False)
    building_number: Mapped[str | None] = mapped_column(String(50))

    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255))
    locality: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    district: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(10))
    jurisdiction_code: Mapped[str] = mapped_column(String(20), nullable=False)

    # -- Geometry ------------------------------------------------------------
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    location: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False)
    )
    footprint: Mapped[str | None] = mapped_column(
        Geometry("POLYGONZ", srid=4326, dimension=3, spatial_index=False)
    )
    envelope_solid: Mapped[str | None] = mapped_column(
        Geometry("POLYHEDRALSURFACEZ", srid=4326, dimension=3, spatial_index=False)
    )

    ground_elevation_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    building_height_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    vertical_datum: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'EGM2008'")
    )
    metric_srid: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("7755"))
    plot_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    built_up_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # -- Composition ---------------------------------------------------------
    total_floors: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    basement_floors: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # -- Short-code allocation -----------------------------------------------
    # The building's slice of the human-facing identifier, e.g. "WB-KOL-B001".
    # ``building_sequence`` is allocated per state+city from ulpin_sequences, so
    # it is meaningless without the region prefix and is stored beside it rather
    # than re-parsed out of the string on every read.
    short_code: Mapped[str | None] = mapped_column(String(12))
    building_sequence: Mapped[int | None] = mapped_column(Integer)

    building_use: Mapped[BuildingUse] = mapped_column(
        _pg_enum(BuildingUse, "building_use"),
        nullable=False,
        server_default=text("'RESIDENTIAL'"),
    )
    construction_status: Mapped[ConstructionStatus] = mapped_column(
        _pg_enum(ConstructionStatus, "construction_status"),
        nullable=False,
        server_default=text("'COMPLETED'"),
    )
    status: Mapped[BuildingStatus] = mapped_column(
        _pg_enum(BuildingStatus, "building_status"),
        nullable=False,
        server_default=text("'DRAFT'"),
    )

    # -- Statutory -----------------------------------------------------------
    sanction_number: Mapped[str | None] = mapped_column(String(80))
    sanction_date: Mapped[date | None] = mapped_column(Date)
    occupancy_certificate_number: Mapped[str | None] = mapped_column(String(80))
    occupancy_certificate_date: Mapped[date | None] = mapped_column(Date)
    year_built: Mapped[int | None] = mapped_column(SmallInteger)

    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    floors: Mapped[list["Floor"]] = relationship(
        back_populates="building",
        cascade="all, delete-orphan",
        order_by="Floor.floor_number",
    )
    units: Mapped[list["Unit"]] = relationship(
        back_populates="building",
        # Units reach buildings only through the composite FK to floors, so
        # there is no units -> buildings foreign key for SQLAlchemy to infer a
        # join from. Both sides of this relationship must therefore say so
        # explicitly; without it the first query that touches it raises
        # NoForeignKeysError at runtime, long after import time.
        primaryjoin="Building.building_id == Unit.building_id",
        foreign_keys="Unit.building_id",
        cascade="all, delete-orphan",
        overlaps="floors",
    )

    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="buildings_lat"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="buildings_lon"),
        CheckConstraint("total_floors BETWEEN 1 AND 250", name="buildings_floors"),
        CheckConstraint("basement_floors BETWEEN 0 AND 20", name="buildings_basements"),
        UniqueConstraint("parcel_ulpin", "block_code", name="uq_buildings_parcel_block"),
        {"schema": SCHEMA},
    )

    @property
    def is_editable(self) -> bool:
        """Registered buildings carry issued identifiers; changes go through
        supersession, not mutation."""
        return self.status in (
            BuildingStatus.DRAFT,
            BuildingStatus.SUBMITTED,
            BuildingStatus.REJECTED,
        )


# ===========================================================================
# Floors
# ===========================================================================
class Floor(Base):
    __tablename__ = "floors"

    floor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    building_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.buildings.building_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Signed: -2 is the second basement, 0 the ground floor, 7 the seventh.
    floor_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    floor_type: Mapped[FloorType] = mapped_column(
        _pg_enum(FloorType, "floor_type"), nullable=False, server_default=text("'UPPER'")
    )
    # Derived from (floor_number, floor_type) by fn_storey_code and checked by a
    # table constraint, so it can never drift from the number it encodes.
    storey_code: Mapped[str] = mapped_column(CHAR(4), nullable=False)
    floor_label: Mapped[str | None] = mapped_column(String(50))

    elevation_base_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    elevation_top_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    floor_height_m: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    floor_plate: Mapped[str | None] = mapped_column(
        Geometry("POLYGONZ", srid=4326, dimension=3, spatial_index=False)
    )
    gross_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    common_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    building: Mapped[Building] = relationship(back_populates="floors")
    units: Mapped[list["Unit"]] = relationship(
        back_populates="floor",
        cascade="all, delete-orphan",
        order_by="Unit.unit_ordinal",
        overlaps="units",
    )

    __table_args__ = (
        UniqueConstraint("building_id", "floor_number", name="uq_floors_building_number"),
        # Composite target for units' two-column foreign key.
        UniqueConstraint("floor_id", "building_id", name="uq_floors_identity"),
        CheckConstraint("floor_number BETWEEN -20 AND 250", name="floors_number_range"),
        CheckConstraint(
            "elevation_top_m IS NULL OR elevation_base_m IS NULL "
            "OR elevation_top_m > elevation_base_m",
            name="floors_elevation_order",
        ),
        {"schema": SCHEMA},
    )


# ===========================================================================
# Units — the vertical property unit
# ===========================================================================
class Unit(Base):
    __tablename__ = "units"

    unit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    floor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    building_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    unit_number: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_code: Mapped[str | None] = mapped_column(CHAR(5))
    unit_ordinal: Mapped[int | None] = mapped_column(Integer)
    unit_type: Mapped[UnitType] = mapped_column(
        _pg_enum(UnitType, "unit_type"), nullable=False
    )
    unit_status: Mapped[UnitStatus] = mapped_column(
        _pg_enum(UnitStatus, "unit_status"), nullable=False, server_default=text("'DRAFT'")
    )
    occupancy_status: Mapped[OccupancyStatus] = mapped_column(
        _pg_enum(OccupancyStatus, "occupancy_status"),
        nullable=False,
        server_default=text("'VACANT'"),
    )

    carpet_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    built_up_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    super_built_up_area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    volume_cum: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    ceiling_height_m: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    bedrooms: Mapped[int | None] = mapped_column(SmallInteger)
    bathrooms: Mapped[int | None] = mapped_column(SmallInteger)
    balconies: Mapped[int | None] = mapped_column(SmallInteger)
    parking_slots: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )

    floor_plate: Mapped[str | None] = mapped_column(
        Geometry("POLYGONZ", srid=4326, dimension=3, spatial_index=False)
    )
    volume_solid: Mapped[str | None] = mapped_column(
        Geometry("POLYHEDRALSURFACEZ", srid=4326, dimension=3, spatial_index=False)
    )
    centroid_3d: Mapped[str | None] = mapped_column(
        Geometry("POINTZ", srid=4326, dimension=3, spatial_index=False)
    )
    is_solid_valid: Mapped[bool | None] = mapped_column(Boolean)

    # -- Local building frame ------------------------------------------------
    # The same box as volume_solid, in metres relative to the building origin.
    # Kept alongside the geometry rather than projected out of it on read:
    # EPSG:4326 stores degrees, and a 0.3 m balcony does not survive the round
    # trip through seven decimal places intact. These are the numbers the viewer
    # positions blocks with and the property panel quotes to a citizen.
    #
    # Written by ThreeDULPINGenerator, never by hand — the scalars and the solid
    # must describe one box, so they are assigned in the same operation.
    x_coordinate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    y_coordinate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    z_coordinate: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    width_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    length_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    height_m: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    # Denormalised from floors.floor_number and kept in step by a BEFORE trigger
    # (trg_units_sync_floor_number). The vertical axis of nearly every query in
    # the system; joining floors just to filter one storey was the common case.
    floor_number: Mapped[int | None] = mapped_column(SmallInteger)

    # -- Verification state --------------------------------------------------
    # The current verdict on this unit, denormalised from verification_records.
    # The dashboard's Verified/Pending split counts *properties*, so a unit
    # verified four times must count once — grouping the records would count it
    # four times. The records remain the evidence trail; this is the answer.
    verification_outcome: Mapped[VerificationOutcome | None] = mapped_column(
        _pg_enum(VerificationOutcome, "verification_outcome"),
        server_default=text("'PENDING_VERIFICATION'"),
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    floor: Mapped[Floor] = relationship(back_populates="units", overlaps="units")
    building: Mapped[Building] = relationship(
        back_populates="units",
        primaryjoin="Unit.building_id == Building.building_id",
        foreign_keys="Unit.building_id",
        overlaps="floor,units",
    )
    ownerships: Mapped[list["UnitOwnership"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )
    tenancies: Mapped[list["Tenant"]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The two-column reference is the point: it forces building_id to agree
        # with the floor's own building, so the denormalised column cannot drift.
        ForeignKeyConstraint(
            ["floor_id", "building_id"],
            [f"{SCHEMA}.floors.floor_id", f"{SCHEMA}.floors.building_id"],
            ondelete="CASCADE",
            name="fk_units_floor_building",
        ),
        UniqueConstraint("floor_id", "unit_number", name="uq_units_floor_number"),
        CheckConstraint(
            "carpet_area_sqm IS NULL OR built_up_area_sqm IS NULL "
            "OR carpet_area_sqm <= built_up_area_sqm",
            name="units_area_order",
        ),
        Index("idx_units_building", "building_id"),
        Index("idx_units_floor", "floor_id"),
        {"schema": SCHEMA},
    )

    @property
    def is_occupied(self) -> bool:
        return self.occupancy_status is OccupancyStatus.OCCUPIED


# ===========================================================================
# Ownership (many-to-many with a share)
# ===========================================================================
class UnitOwnership(Base):
    """Title held by one owner over one unit, for a period, as a fraction.

    Shares are ``numeric``, never float: a third of a flat is 1/3 exactly in the
    register, and floating point cannot represent the three-way split summing
    back to 1.
    """

    __tablename__ = "unit_ownerships"

    ownership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.units.unit_id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.owners.owner_id", ondelete="RESTRICT"),
        nullable=False,
    )

    share_fraction: Mapped[Decimal] = mapped_column(
        Numeric(12, 9), nullable=False, server_default=text("1")
    )
    ownership_mode: Mapped[OwnershipMode] = mapped_column(
        _pg_enum(OwnershipMode, "ownership_mode"),
        nullable=False,
        server_default=text("'SOLE'"),
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    acquired_on: Mapped[date] = mapped_column(Date, nullable=False)
    ended_on: Mapped[date | None] = mapped_column(Date)
    acquisition_mode: Mapped[str | None] = mapped_column(String(50))
    deed_number: Mapped[str | None] = mapped_column(String(80))
    deed_date: Mapped[date | None] = mapped_column(Date)
    consideration_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    unit: Mapped[Unit] = relationship(back_populates="ownerships")
    owner: Mapped[Owner] = relationship(back_populates="ownerships")

    __table_args__ = (
        CheckConstraint("share_fraction > 0 AND share_fraction <= 1", name="ownership_share_range"),
        CheckConstraint("ended_on IS NULL OR ended_on >= acquired_on", name="ownership_dates"),
        {"schema": SCHEMA},
    )

    @property
    def is_active(self) -> bool:
        return self.ended_on is None


# ===========================================================================
# Tenancies
# ===========================================================================
class Tenant(Base):
    """One row per tenancy agreement, not per person.

    A GiST exclusion constraint in the DDL prevents two ACTIVE tenancies with
    overlapping date ranges on the same unit — the database refuses the
    double-let rather than trusting the application to notice.
    """

    __tablename__ = "tenants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.units.unit_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey(f"{SCHEMA}.users.user_id", ondelete="SET NULL")
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(20))
    national_id_type: Mapped[str | None] = mapped_column(String(20))
    national_id_hash: Mapped[bytes | None] = mapped_column()
    national_id_last4: Mapped[str | None] = mapped_column(String(4))

    lease_start: Mapped[date] = mapped_column(Date, nullable=False)
    lease_end: Mapped[date | None] = mapped_column(Date)
    monthly_rent: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    security_deposit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    agreement_number: Mapped[str | None] = mapped_column(String(80))
    is_registered_agreement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    occupant_count: Mapped[int | None] = mapped_column(SmallInteger)

    status: Mapped[TenancyStatus] = mapped_column(
        _pg_enum(TenancyStatus, "tenancy_status"),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    unit: Mapped[Unit] = relationship(back_populates="tenancies")
    user: Mapped["User | None"] = relationship(
        back_populates="tenant_profiles",
        primaryjoin="foreign(Tenant.user_id) == User.user_id",
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint("lease_end IS NULL OR lease_end >= lease_start", name="tenants_dates"),
        {"schema": SCHEMA},
    )

    @property
    def is_current(self) -> bool:
        return self.status is TenancyStatus.ACTIVE
