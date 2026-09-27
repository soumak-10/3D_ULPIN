"""Request and response contracts for the property registration module.

The public form asks for very little — name, address, lat/lon, total floors for
a building; floor number, unit number, property type for a unit. Everything else
is optional and defaulted, so the minimal form submits cleanly while an officer
digitising a sanctioned plan can supply the full record in the same endpoint.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

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

# ---------------------------------------------------------------------------
# Shared field types
# ---------------------------------------------------------------------------
Latitude = Annotated[Decimal, Field(ge=-90, le=90, decimal_places=7)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, decimal_places=7)]
ParcelUlpin = Annotated[
    str, StringConstraints(pattern=r"^[A-Z0-9]{14}$", to_upper=True, strip_whitespace=True)
]
Jurisdiction = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{2}(-[A-Z0-9]{2,6}){0,3}$", to_upper=True)
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
UnitNumber = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9\-/ ]{0,19}$", strip_whitespace=True
    ),
]

# India's mainland plus islands. A building at (0, 0) is a data-entry slip, not a
# property in the Gulf of Guinea, and it is worth catching at the boundary.
INDIA_BBOX = (68.0, 6.0, 97.5, 37.6)  # minx, miny, maxx, maxy

# The three types the citizen-facing dropdown offers.
PublicPropertyType = Literal["RESIDENTIAL", "COMMERCIAL", "INDUSTRIAL"]


# ===========================================================================
# Building
# ===========================================================================
class BuildingBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    building_name: ShortText = Field(description="Registered name of the building")
    address_line1: Annotated[str, StringConstraints(min_length=3, max_length=255)]
    address_line2: str | None = Field(default=None, max_length=255)
    locality: str | None = Field(default=None, max_length=120)
    city: Annotated[str, StringConstraints(min_length=2, max_length=100)]
    district: str | None = Field(default=None, max_length=100)
    state: Annotated[str, StringConstraints(min_length=2, max_length=100)]
    postal_code: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{5}$")] | None = None

    latitude: Latitude
    longitude: Longitude

    total_floors: int = Field(ge=1, le=250, description="Floors above ground, ground included")
    basement_floors: int = Field(default=0, ge=0, le=20)

    building_use: BuildingUse = BuildingUse.RESIDENTIAL
    construction_status: ConstructionStatus = ConstructionStatus.COMPLETED
    building_number: str | None = Field(default=None, max_length=50)
    year_built: int | None = Field(default=None, ge=1800, le=2100)
    plot_area_sqm: Decimal | None = Field(default=None, gt=0, le=10_000_000)
    built_up_area_sqm: Decimal | None = Field(default=None, gt=0, le=10_000_000)
    ground_elevation_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    building_height_m: Decimal | None = Field(default=None, gt=0, le=1000)

    sanction_number: str | None = Field(default=None, max_length=80)
    sanction_date: date | None = None
    occupancy_certificate_number: str | None = Field(default=None, max_length=80)
    occupancy_certificate_date: date | None = None

    @model_validator(mode="after")
    def _within_india(self) -> Self:
        minx, miny, maxx, maxy = INDIA_BBOX
        if not (minx <= float(self.longitude) <= maxx and miny <= float(self.latitude) <= maxy):
            raise ValueError(
                "Coordinates fall outside India. Check that latitude and "
                "longitude have not been transposed."
            )
        return self

    @model_validator(mode="after")
    def _areas_consistent(self) -> Self:
        if self.plot_area_sqm and self.built_up_area_sqm:
            # Built-up area legitimately exceeds the plot — that is what floors
            # are for — but not by more than the floor count allows.
            ceiling = self.plot_area_sqm * (self.total_floors + self.basement_floors + 1)
            if self.built_up_area_sqm > ceiling:
                raise ValueError(
                    "Built-up area exceeds what the plot and floor count can support"
                )
        return self

    @model_validator(mode="after")
    def _certificate_after_sanction(self) -> Self:
        if self.sanction_date and self.occupancy_certificate_date:
            if self.occupancy_certificate_date < self.sanction_date:
                raise ValueError("Occupancy certificate cannot predate the sanction")
        return self


class BuildingCreate(BuildingBase):
    parcel_ulpin: ParcelUlpin = Field(
        description="14-character 2D ULPIN of the land parcel this building stands on"
    )
    block_code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2}$", to_upper=True)] = "A1"
    jurisdiction_code: Jurisdiction
    metric_srid: int = Field(
        default=7755,
        description="Projected SRID used for all area and volume measurement",
    )
    vertical_datum: str = Field(default="EGM2008", max_length=20)

    # Optional convenience: create the floor rows in the same call. Officers
    # digitising a plan almost always want this; the API works without it.
    auto_create_floors: bool = Field(
        default=True,
        description="Generate floor rows for every storey, basements included",
    )

    @field_validator("metric_srid")
    @classmethod
    def _known_metric_srid(cls, v: int) -> int:
        allowed = {7755, 32642, 32643, 32644, 32645, 32646, 32647}
        if v not in allowed:
            raise ValueError(
                f"metric_srid must be one of {sorted(allowed)} — "
                "measuring in a geographic CRS yields square degrees"
            )
        return v


class BuildingUpdate(BaseModel):
    """Every field optional; only what is sent is changed.

    ``parcel_ulpin`` and ``block_code`` are absent by design — they are the
    identifier prefix for every ULPIN in the tower, and moving a building to a
    different parcel is a supersession, not an edit.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    building_name: ShortText | None = None
    address_line1: Annotated[str, StringConstraints(min_length=3, max_length=255)] | None = None
    address_line2: str | None = Field(default=None, max_length=255)
    locality: str | None = Field(default=None, max_length=120)
    city: Annotated[str, StringConstraints(min_length=2, max_length=100)] | None = None
    district: str | None = Field(default=None, max_length=100)
    state: Annotated[str, StringConstraints(min_length=2, max_length=100)] | None = None
    postal_code: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{5}$")] | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    total_floors: int | None = Field(default=None, ge=1, le=250)
    basement_floors: int | None = Field(default=None, ge=0, le=20)
    building_use: BuildingUse | None = None
    construction_status: ConstructionStatus | None = None
    building_number: str | None = Field(default=None, max_length=50)
    year_built: int | None = Field(default=None, ge=1800, le=2100)
    plot_area_sqm: Decimal | None = Field(default=None, gt=0)
    built_up_area_sqm: Decimal | None = Field(default=None, gt=0)
    ground_elevation_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    building_height_m: Decimal | None = Field(default=None, gt=0, le=1000)
    sanction_number: str | None = Field(default=None, max_length=80)
    sanction_date: date | None = None
    occupancy_certificate_number: str | None = Field(default=None, max_length=80)
    occupancy_certificate_date: date | None = None

    @model_validator(mode="after")
    def _coordinates_travel_together(self) -> Self:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send latitude and longitude together, or neither")
        return self


class BuildingStatusUpdate(BaseModel):
    status: BuildingStatus
    remarks: str | None = Field(default=None, max_length=500)


class BuildingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    building_id: uuid.UUID
    parcel_ulpin: str
    block_code: str
    building_name: str
    building_number: str | None = None

    address_line1: str
    address_line2: str | None = None
    locality: str | None = None
    city: str
    district: str | None = None
    state: str
    postal_code: str | None = None
    jurisdiction_code: str

    latitude: Decimal
    longitude: Decimal
    ground_elevation_m: Decimal | None = None
    building_height_m: Decimal | None = None
    plot_area_sqm: Decimal | None = None
    built_up_area_sqm: Decimal | None = None
    metric_srid: int
    vertical_datum: str

    total_floors: int
    basement_floors: int
    total_units: int

    building_use: BuildingUse
    construction_status: ConstructionStatus
    status: BuildingStatus

    sanction_number: str | None = None
    sanction_date: date | None = None
    occupancy_certificate_number: str | None = None
    occupancy_certificate_date: date | None = None
    year_built: int | None = None

    created_at: datetime
    updated_at: datetime


class BuildingDetailResponse(BuildingResponse):
    floors: list["FloorResponse"] = Field(default_factory=list)
    occupied_units: int = 0
    vacant_units: int = 0


# ===========================================================================
# Floor
# ===========================================================================
class FloorCreate(BaseModel):
    floor_number: int = Field(
        ge=-20,
        le=250,
        description="Signed: -1 is the first basement, 0 the ground floor",
    )
    floor_type: FloorType | None = Field(
        default=None,
        description="Inferred from floor_number when omitted",
    )
    floor_label: str | None = Field(default=None, max_length=50)
    elevation_base_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    elevation_top_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    gross_area_sqm: Decimal | None = Field(default=None, gt=0)
    common_area_sqm: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _elevations_ordered(self) -> Self:
        if self.elevation_base_m is not None and self.elevation_top_m is not None:
            if self.elevation_top_m <= self.elevation_base_m:
                raise ValueError("elevation_top_m must exceed elevation_base_m")
        return self


class FloorUpdate(BaseModel):
    floor_label: str | None = Field(default=None, max_length=50)
    floor_type: FloorType | None = None
    elevation_base_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    elevation_top_m: Decimal | None = Field(default=None, ge=-500, le=9000)
    gross_area_sqm: Decimal | None = Field(default=None, gt=0)
    common_area_sqm: Decimal | None = Field(default=None, ge=0)


class FloorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    floor_id: uuid.UUID
    building_id: uuid.UUID
    floor_number: int
    floor_type: FloorType
    storey_code: str
    floor_label: str | None = None
    elevation_base_m: Decimal | None = None
    elevation_top_m: Decimal | None = None
    floor_height_m: Decimal | None = None
    gross_area_sqm: Decimal | None = None
    common_area_sqm: Decimal | None = None
    unit_count: int
    created_at: datetime


# ===========================================================================
# Unit
# ===========================================================================
class UnitBase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    unit_number: UnitNumber = Field(description="As shown on the door, e.g. 401 or B-12")
    property_type: UnitType = Field(
        description="Residential, Commercial or Industrial for citizen-facing registration"
    )

    carpet_area_sqm: Decimal | None = Field(default=None, gt=0, le=100_000)
    built_up_area_sqm: Decimal | None = Field(default=None, gt=0, le=100_000)
    super_built_up_area_sqm: Decimal | None = Field(default=None, gt=0, le=100_000)
    ceiling_height_m: Decimal | None = Field(default=None, gt=0, le=30)

    bedrooms: int | None = Field(default=None, ge=0, le=50)
    bathrooms: int | None = Field(default=None, ge=0, le=50)
    balconies: int | None = Field(default=None, ge=0, le=20)
    parking_slots: int = Field(default=0, ge=0, le=50)

    occupancy_status: OccupancyStatus = OccupancyStatus.VACANT

    @model_validator(mode="after")
    def _area_hierarchy(self) -> Self:
        """Carpet ≤ built-up ≤ super built-up.

        A record that inverts these is either a transcription error or an
        inflated sale pitch; either way it must not enter the register.
        """
        c, b, s = self.carpet_area_sqm, self.built_up_area_sqm, self.super_built_up_area_sqm
        if c and b and c > b:
            raise ValueError("Carpet area cannot exceed built-up area")
        if b and s and b > s:
            raise ValueError("Built-up area cannot exceed super built-up area")
        if c and s and c > s:
            raise ValueError("Carpet area cannot exceed super built-up area")
        return self

    @model_validator(mode="after")
    def _residential_sanity(self) -> Self:
        if self.property_type is UnitType.RESIDENTIAL and self.bedrooms and self.carpet_area_sqm:
            # ~7 m² is the smallest habitable room in most municipal byelaws.
            if self.carpet_area_sqm < Decimal(self.bedrooms) * Decimal("7"):
                raise ValueError(
                    f"{self.bedrooms} bedroom(s) cannot fit in "
                    f"{self.carpet_area_sqm} m² of carpet area"
                )
        return self


class UnitCreate(UnitBase):
    floor_number: int = Field(
        ge=-20, le=250, description="Must match an existing floor of the building"
    )
    unit_status: UnitStatus = UnitStatus.DRAFT


class UnitBulkCreate(BaseModel):
    """Register a whole floor in one call.

    Digitising a plan means entering twelve near-identical flats. Doing that as
    twelve round trips is slow and leaves half-registered floors when one fails;
    this inserts them in a single transaction.
    """

    floor_number: int = Field(ge=-20, le=250)
    units: list[UnitBase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _numbers_unique(self) -> Self:
        seen = [u.unit_number.upper() for u in self.units]
        duplicates = {n for n in seen if seen.count(n) > 1}
        if duplicates:
            raise ValueError(f"Duplicate unit numbers in the batch: {', '.join(sorted(duplicates))}")
        return self


class UnitUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    unit_number: UnitNumber | None = None
    property_type: UnitType | None = None
    occupancy_status: OccupancyStatus | None = None
    unit_status: UnitStatus | None = None
    carpet_area_sqm: Decimal | None = Field(default=None, gt=0)
    built_up_area_sqm: Decimal | None = Field(default=None, gt=0)
    super_built_up_area_sqm: Decimal | None = Field(default=None, gt=0)
    ceiling_height_m: Decimal | None = Field(default=None, gt=0, le=30)
    bedrooms: int | None = Field(default=None, ge=0, le=50)
    bathrooms: int | None = Field(default=None, ge=0, le=50)
    balconies: int | None = Field(default=None, ge=0, le=20)
    parking_slots: int | None = Field(default=None, ge=0, le=50)


class UnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    unit_id: uuid.UUID
    building_id: uuid.UUID
    floor_id: uuid.UUID
    unit_number: str
    unit_code: str | None = None
    unit_ordinal: int | None = None
    unit_type: UnitType
    unit_status: UnitStatus
    occupancy_status: OccupancyStatus
    # The viewer colours each unit by this: green verified, yellow pending, red
    # invalid or unauthorised. It was mapped on the model and declared in the
    # TypeScript UnitResponse but missing here, so every unit arrived with the
    # field undefined and the whole building rendered in the neutral tone.
    verification_outcome: VerificationOutcome | None = None

    carpet_area_sqm: Decimal | None = None
    built_up_area_sqm: Decimal | None = None
    super_built_up_area_sqm: Decimal | None = None
    volume_cum: Decimal | None = None
    ceiling_height_m: Decimal | None = None

    # -- 3D placement, in the building's own frame --------------------------
    # Declared here as well as in the 3D module's own response, so the viewer can
    # position its blocks from the unit list it already fetches rather than
    # issuing a second request for the building. Null means unplaced, which the
    # scene draws with its fallback layout; see ThreeDUnitResponse for the same
    # numbers with the ULPIN and parties attached.
    x_coordinate: Decimal | None = None
    y_coordinate: Decimal | None = None
    z_coordinate: Decimal | None = None
    width_m: Decimal | None = None
    length_m: Decimal | None = None
    height_m: Decimal | None = None
    floor_number: int | None = None

    bedrooms: int | None = None
    bathrooms: int | None = None
    balconies: int | None = None
    parking_slots: int

    created_at: datetime
    updated_at: datetime


class UnitDetailResponse(UnitResponse):
    floor_number: int | None = None
    storey_code: str | None = None
    building_name: str | None = None
    ulpin_code: str | None = None
    owners: list["OwnershipResponse"] = Field(default_factory=list)
    current_tenancy: "TenantResponse | None" = None


class OccupancyUpdate(BaseModel):
    """Occupied/Vacant is the field the public register actually shows, so it is
    changed through its own endpoint rather than folded into a general update."""

    occupancy_status: OccupancyStatus
    remarks: str | None = Field(default=None, max_length=500)


# ===========================================================================
# Owners and ownership
# ===========================================================================
class OwnerCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    owner_type: OwnerType = OwnerType.INDIVIDUAL
    full_name: ShortText
    father_or_spouse_name: str | None = Field(default=None, max_length=200)
    date_of_birth: date | None = None
    email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,15}$")] | None = None

    # Accepted in the clear, hashed before it touches the database, and never
    # returned. The response carries only the last four digits.
    national_id_type: Literal["AADHAAR", "PAN", "PASSPORT", "VOTER_ID", "CIN", "GSTIN"] | None = None
    national_id: str | None = Field(default=None, min_length=4, max_length=30)

    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{5}$")] | None = None

    organisation_name: str | None = Field(default=None, max_length=255)
    registration_number: str | None = Field(default=None, max_length=50)
    link_user_id: uuid.UUID | None = Field(
        default=None, description="Attach this owner record to an existing login"
    )

    @model_validator(mode="after")
    def _organisation_needs_a_name(self) -> Self:
        corporate = {OwnerType.COMPANY, OwnerType.TRUST, OwnerType.SOCIETY, OwnerType.GOVERNMENT}
        if self.owner_type in corporate and not self.organisation_name:
            raise ValueError(f"{self.owner_type.value} owners require an organisation_name")
        return self

    @model_validator(mode="after")
    def _id_pair(self) -> Self:
        if bool(self.national_id) != bool(self.national_id_type):
            raise ValueError("Send national_id and national_id_type together, or neither")
        if self.national_id and self.national_id_type == "AADHAAR":
            digits = self.national_id.replace(" ", "")
            if not (digits.isdigit() and len(digits) == 12):
                raise ValueError("An Aadhaar number is 12 digits")
        if self.national_id and self.national_id_type == "PAN":
            import re

            if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", self.national_id.upper()):
                raise ValueError("PAN must match AAAAA9999A")
        return self


class OwnerUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: ShortText | None = None
    father_or_spouse_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,15}$")] | None = None
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{5}$")] | None = None
    organisation_name: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class OwnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    owner_id: uuid.UUID
    owner_type: OwnerType
    full_name: str
    organisation_name: str | None = None
    email: str | None = None
    phone: str | None = None
    national_id_type: str | None = None
    national_id_last4: str | None = None  # never the full number
    city: str | None = None
    state: str | None = None
    is_verified: bool
    created_at: datetime


class OwnershipAssign(BaseModel):
    """Attach one or more owners to a unit.

    Shares must total exactly 1. The database enforces this too, with a deferred
    constraint trigger, because a transfer legitimately passes through an
    invalid intermediate state inside a single transaction.
    """

    owners: list["OwnershipShare"] = Field(min_length=1, max_length=20)
    ownership_mode: OwnershipMode = OwnershipMode.SOLE
    acquired_on: date
    acquisition_mode: str | None = Field(default=None, max_length=50)
    deed_number: str | None = Field(default=None, max_length=80)
    deed_date: date | None = None
    consideration_amount: Decimal | None = Field(default=None, ge=0)
    replace_existing: bool = Field(
        default=False,
        description="End the current ownerships as of acquired_on before applying these",
    )

    @model_validator(mode="after")
    def _shares_total_one(self) -> Self:
        total = sum((o.share_fraction for o in self.owners), Decimal(0))
        if total != Decimal(1):
            raise ValueError(f"Shares must total exactly 1, got {total}")
        return self

    @model_validator(mode="after")
    def _one_primary(self) -> Self:
        primaries = [o for o in self.owners if o.is_primary]
        if len(primaries) > 1:
            raise ValueError("Only one owner may be marked primary")
        if not primaries and len(self.owners) > 1:
            raise ValueError("Mark one owner as primary for correspondence")
        return self

    @model_validator(mode="after")
    def _distinct_owners(self) -> Self:
        ids = [o.owner_id for o in self.owners]
        if len(set(ids)) != len(ids):
            raise ValueError("The same owner appears more than once")
        return self

    @model_validator(mode="after")
    def _mode_matches_count(self) -> Self:
        if self.ownership_mode is OwnershipMode.SOLE and len(self.owners) > 1:
            raise ValueError("SOLE ownership cannot have more than one owner")
        return self


class OwnershipShare(BaseModel):
    owner_id: uuid.UUID
    share_fraction: Decimal = Field(gt=0, le=1, decimal_places=9)
    is_primary: bool = False


class OwnershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ownership_id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: str | None = None
    share_fraction: Decimal
    ownership_mode: OwnershipMode
    is_primary: bool
    acquired_on: date
    ended_on: date | None = None
    deed_number: str | None = None


# ===========================================================================
# Tenancies
# ===========================================================================
class TenantCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: ShortText
    email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,15}$")] | None = None
    national_id_type: Literal["AADHAAR", "PAN", "PASSPORT", "VOTER_ID"] | None = None
    national_id: str | None = Field(default=None, min_length=4, max_length=30)

    lease_start: date
    lease_end: date | None = None
    monthly_rent: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    security_deposit: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    agreement_number: str | None = Field(default=None, max_length=80)
    is_registered_agreement: bool = False
    occupant_count: int | None = Field(default=None, ge=1, le=100)
    link_user_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _lease_dates(self) -> Self:
        if self.lease_end and self.lease_end < self.lease_start:
            raise ValueError("lease_end cannot precede lease_start")
        if self.lease_end and (self.lease_end - self.lease_start).days > 365 * 99:
            raise ValueError("A lease longer than 99 years is a conveyance, not a tenancy")
        return self

    @model_validator(mode="after")
    def _id_pair(self) -> Self:
        if bool(self.national_id) != bool(self.national_id_type):
            raise ValueError("Send national_id and national_id_type together, or neither")
        return self


class TenantUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: ShortText | None = None
    email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9]{10,15}$")] | None = None
    lease_end: date | None = None
    monthly_rent: Decimal | None = Field(default=None, ge=0)
    security_deposit: Decimal | None = Field(default=None, ge=0)
    agreement_number: str | None = Field(default=None, max_length=80)
    is_registered_agreement: bool | None = None
    occupant_count: int | None = Field(default=None, ge=1, le=100)
    status: TenancyStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: uuid.UUID
    unit_id: uuid.UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    national_id_type: str | None = None
    national_id_last4: str | None = None
    lease_start: date
    lease_end: date | None = None
    monthly_rent: Decimal | None = None
    security_deposit: Decimal | None = None
    agreement_number: str | None = None
    is_registered_agreement: bool
    occupant_count: int | None = None
    status: TenancyStatus
    created_at: datetime


# ===========================================================================
# Listing envelopes
# ===========================================================================
class PagedResponse(BaseModel):
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class BuildingListResponse(PagedResponse):
    items: list[BuildingResponse]


class UnitListResponse(PagedResponse):
    items: list[UnitResponse]


class OwnerListResponse(PagedResponse):
    items: list[OwnerResponse]


class TenantListResponse(PagedResponse):
    items: list[TenantResponse]


class BuildingSearchParams(BaseModel):
    """Map- and list-driven search. ``near_*`` runs a radius query through the
    spatial index; without a radius the point is ignored."""

    q: str | None = Field(default=None, max_length=120)
    city: str | None = None
    state: str | None = None
    status: BuildingStatus | None = None
    building_use: BuildingUse | None = None
    parcel_ulpin: str | None = None
    near_lat: Latitude | None = None
    near_lon: Longitude | None = None
    radius_m: int | None = Field(default=None, ge=10, le=50_000)

    @model_validator(mode="after")
    def _radius_needs_a_centre(self) -> Self:
        has_point = self.near_lat is not None and self.near_lon is not None
        if self.radius_m is not None and not has_point:
            raise ValueError("radius_m requires near_lat and near_lon")
        return self


BuildingDetailResponse.model_rebuild()
UnitDetailResponse.model_rebuild()
OwnershipAssign.model_rebuild()
