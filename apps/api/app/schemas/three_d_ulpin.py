"""Request and response models for 3D ULPIN generation.

Separate from ``schemas/property.py`` because these describe a different thing:
property.py answers "what is registered here", this answers "where in space is
it". A caller rendering a building needs the second and very little of the first,
and sending the whole property record per unit to draw 32 boxes is wasteful.

The wire names follow the specification — ``x_coordinate``, ``width``,
``geometry_3d`` — while storage keeps the repository's unit-suffixed convention
(``width_m``) and its existing solid column (``volume_solid``). The mapping is
declared here, in one place, rather than by adding duplicate columns to the
table.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from app.models.enums import OccupancyStatus, UnitStatus, UnitType, VerificationOutcome


class Coordinates3D(BaseModel):
    """A unit's origin in the building's own frame, metres.

    Local rather than geographic on purpose: ``X=10, Y=20, Z=6`` is a sentence a
    registrar can check against a floor plan, where 77.5946091° is not.
    """

    x: Decimal = Field(description="Metres east of the building origin")
    y: Decimal = Field(description="Metres north of the building origin")
    z: Decimal = Field(
        description="Metres above the building ground plane; storey N is at (N-1) * floor height"
    )


class Dimensions3D(BaseModel):
    width: Decimal = Field(description="Extent along local X, metres")
    length: Decimal = Field(description="Extent along local Y, metres")
    height: Decimal = Field(description="Floor-to-ceiling extent, metres")
    volume_cum: Decimal | None = Field(
        default=None, description="width x length x height, cubic metres"
    )


class ThreeDUnitResponse(BaseModel):
    """One unit as a clickable volume, with everything its panel displays.

    Owner and tenant are flattened to names here. The 3D scene needs a label, not
    a party record, and resolving them per unit from the client — as the viewer
    did — costs one request per click.
    """

    model_config = ConfigDict(from_attributes=True)

    unit_id: uuid.UUID
    unit_number: str
    floor_number: int | None
    building_id: uuid.UUID
    building_name: str | None = None
    building_code: str | None = Field(
        default=None, description="The B-segment of the ULPIN, e.g. B001"
    )

    ulpin: str | None = Field(default=None, description="STATE-CITY-BUILDING-FLOOR-UNIT")
    short_code: str | None = None

    property_type: UnitType
    unit_status: UnitStatus
    occupancy_status: OccupancyStatus
    verification_status: VerificationOutcome | None = None

    coordinates: Coordinates3D | None = Field(
        default=None, description="Null until the unit has been placed by the generator"
    )
    dimensions: Dimensions3D | None = None

    carpet_area_sqm: Decimal | None = None
    geometry_3d: dict | None = Field(
        default=None,
        description=(
            "The closed POLYHEDRALSURFACEZ solid as GeoJSON, EPSG:4326 with Z in "
            "metres. Stored in units.volume_solid."
        ),
    )

    owner_name: str | None = None
    tenant_name: str | None = None

    @property
    def is_located(self) -> bool:
        return self.coordinates is not None


class ThreeDBuildingResponse(BaseModel):
    """A whole building, ready to render."""

    building_id: uuid.UUID
    building_name: str
    building_code: str | None = None
    state: str | None = None
    city: str | None = None
    latitude: Decimal
    longitude: Decimal
    total_floors: int

    unit_count: int
    located_count: int = Field(description="Units that actually carry a 3D solid")
    floor_numbers: list[int] = Field(default_factory=list)
    units: list[ThreeDUnitResponse] = Field(default_factory=list)


class ThreeDGenerateRequest(BaseModel):
    """Place units in space, optionally scaffolding the storeys first.

    Three shapes, narrowest to widest:

    * ``unit_id`` — place one unit, e.g. after a re-survey changed its area.
    * ``building_id`` — place every unit already registered in the building.
    * ``building_id`` with ``floors`` and ``units_per_floor`` — create the
      storeys and units too, then place them. This is the registration path: one
      call turns "Building A, 3 floors, 2 units per floor" into six located,
      identified volumes.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    building_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None

    floors: int | None = Field(
        default=None, ge=1, le=250, description="Scaffold this many storeys, numbered from 1"
    )
    units_per_floor: int | None = Field(
        default=None, ge=1, le=100, description="Units to create on each scaffolded storey"
    )
    property_type: UnitType | None = Field(
        default=None, description="Type for scaffolded units; defaults to RESIDENTIAL"
    )

    width: Decimal | None = Field(default=None, gt=0, le=500)
    length: Decimal | None = Field(default=None, gt=0, le=500)
    height: Decimal | None = Field(default=None, gt=0, le=30)

    issue: bool = Field(
        default=False,
        description="Issue the minted identifiers immediately rather than leaving them provisional",
    )
    overwrite: bool = Field(
        default=True,
        description=(
            "Re-place units that already carry geometry. False makes the call a "
            "backfill of unplaced units only, which is the safe repeat."
        ),
    )

    @model_validator(mode="after")
    def _one_subject(self) -> Self:
        if (self.unit_id is None) == (self.building_id is None):
            raise ValueError("Provide exactly one of unit_id or building_id")
        if self.unit_id is not None and (self.floors or self.units_per_floor):
            raise ValueError("floors and units_per_floor apply to a building, not a single unit")
        # Half a scaffold specification is almost certainly a mistake, and
        # guessing the other half would silently create the wrong building.
        if (self.floors is None) != (self.units_per_floor is None):
            raise ValueError("floors and units_per_floor must be given together")
        if (self.width is None) != (self.length is None):
            raise ValueError("width and length must be given together")
        return self


class ThreeDGenerateResponse(BaseModel):
    """What the generator did, in terms the caller can verify."""

    building_id: uuid.UUID
    building_name: str
    floors_created: int = 0
    units_created: int = 0
    units_placed: int = 0
    ulpins_minted: int = 0
    skipped: list[str] = Field(
        default_factory=list, description="Units left alone, with the reason"
    )
    units: list[ThreeDUnitResponse] = Field(default_factory=list)


__all__ = [
    "Coordinates3D",
    "Dimensions3D",
    "ThreeDBuildingResponse",
    "ThreeDGenerateRequest",
    "ThreeDGenerateResponse",
    "ThreeDUnitResponse",
]
