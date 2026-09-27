"""3D ULPIN endpoints: generate a volume for an identifier, then read it back.

Route order is load-bearing. ``/{unit_id}`` is declared last because FastAPI
matches in declaration order, and a path parameter placed first would swallow
``/building/...`` and ``/view/...`` — the request would arrive at the wrong
handler and fail on a UUID parse rather than 404, which is a confusing way to
learn about it.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import ValidationException
from app.models.enums import FloorType, UnitType
from app.models.property import Building, Unit
from app.schemas.property import FloorCreate, UnitBase, UnitBulkCreate
from app.schemas.three_d_ulpin import (
    Coordinates3D,
    Dimensions3D,
    ThreeDBuildingResponse,
    ThreeDGenerateRequest,
    ThreeDGenerateResponse,
    ThreeDUnitResponse,
)
from app.schemas.ulpin import UlpinGenerateRequest
from app.services.property_service import PropertyService
from app.services.three_d_ulpin_service import (
    DEFAULT_FLOOR_HEIGHT_M,
    ThreeDULPINGenerator,
)
from app.services.ulpin_service import UlpinService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/3d-ulpin", tags=["3D ULPIN"])


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def _to_response(
    unit: Unit,
    *,
    building: Building | None = None,
    ulpin_short: str | None = None,
    ulpin_full: str | None = None,
    geometry: dict | None = None,
    owner_name: str | None = None,
    tenant_name: str | None = None,
) -> ThreeDUnitResponse:
    """Flatten a unit and its context into one renderable record.

    Coordinates and dimensions are emitted as objects only when the unit has
    actually been placed. A half-populated ``{x: null, y: null}`` would force
    every consumer to null-check three levels down, and the viewer needs exactly
    one question answered: can I draw this or not.
    """
    located = unit.x_coordinate is not None and unit.width_m is not None
    return ThreeDUnitResponse(
        unit_id=unit.unit_id,
        unit_number=unit.unit_number,
        floor_number=unit.floor_number,
        building_id=unit.building_id,
        building_name=building.building_name if building else None,
        building_code=building.short_code if building else None,
        ulpin=ulpin_short or ulpin_full,
        short_code=ulpin_short,
        property_type=unit.unit_type,
        unit_status=unit.unit_status,
        occupancy_status=unit.occupancy_status,
        verification_status=unit.verification_outcome,
        carpet_area_sqm=unit.carpet_area_sqm,
        coordinates=(
            Coordinates3D(
                x=unit.x_coordinate, y=unit.y_coordinate, z=unit.z_coordinate
            )
            if located
            else None
        ),
        dimensions=(
            Dimensions3D(
                width=unit.width_m,
                length=unit.length_m,
                height=unit.height_m,
                volume_cum=unit.volume_cum,
            )
            if located
            else None
        ),
        geometry_3d=geometry,
        owner_name=owner_name,
        tenant_name=tenant_name,
    )


async def _assemble(
    gen: ThreeDULPINGenerator,
    rows: list[Unit],
    *,
    building: Building | None,
    include_geometry: bool,
) -> list[ThreeDUnitResponse]:
    """Build responses for many units with a fixed number of queries.

    Three lookups for the whole set rather than three per unit: the viewer asks
    for every unit in a tower at once, and the per-unit version of this was the
    reason clicking a flat cost a round trip.
    """
    unit_ids = [u.unit_id for u in rows]
    codes = await gen.ulpin_codes(unit_ids)
    parties = await gen.parties(unit_ids)
    geoms = await gen.solids_geojson(unit_ids) if include_geometry else {}

    out: list[ThreeDUnitResponse] = []
    for unit in rows:
        short, full = codes.get(unit.unit_id, (None, None))
        owner, tenant = parties.get(unit.unit_id, (None, None))
        out.append(
            _to_response(
                unit,
                building=building,
                ulpin_short=short,
                ulpin_full=full,
                geometry=geoms.get(unit.unit_id),
                owner_name=owner,
                tenant_name=tenant,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Generate
# --------------------------------------------------------------------------- #


@router.post(
    "/generate",
    response_model=ThreeDGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate 3D ULPINs: place units in space and mint their identifiers",
)
async def generate(
    payload: ThreeDGenerateRequest,
    session: DbSession,
    actor: CurrentUser,
) -> ThreeDGenerateResponse:
    """Turn a registration into located, identified volumes.

    The full path, in one transaction: create the storeys, create the units on
    them, mint a ULPIN per unit through ``UlpinService`` (the only component
    allowed to allocate one), then compute each unit's coordinates and write its
    solid. Either the whole building lands or none of it does — a building with
    four of six flats placed is worse than a building with none, because the
    viewer renders it as complete.
    """
    gen = ThreeDULPINGenerator(session)
    gen.require_generate(actor)
    properties = PropertyService(session)

    floors_created = 0
    units_created = 0

    if payload.unit_id is not None:
        unit, floor, building = await gen.unit_with_context(payload.unit_id, actor=actor)
        placement = await gen.locate_unit(
            unit,
            building=building,
            floor=floor,
            width=payload.width,
            length=payload.length,
            height=payload.height,
            overwrite=payload.overwrite,
        )
        placed = [unit] if placement is not None else []
    else:
        building = await gen._load_building(payload.building_id, actor=actor)

        if payload.floors is not None and payload.units_per_floor is not None:
            floors_created, units_created = await _scaffold(
                properties,
                building=building,
                storeys=payload.floors,
                per_floor=payload.units_per_floor,
                property_type=payload.property_type or UnitType.RESIDENTIAL,
                actor=actor,
            )

        placed_pairs = await gen.locate_building(
            building.building_id, actor=actor, overwrite=payload.overwrite
        )
        placed = [u for u, _ in placed_pairs]

    # Mint after placing. The identifier is the public fact and the geometry is
    # what makes it meaningful, so a code is only handed out once the volume it
    # denotes exists.
    minted = 0
    if placed:
        ulpin_result = await UlpinService(session).generate(
            UlpinGenerateRequest(building_id=building.building_id, issue=payload.issue),
            actor=actor,
        )
        minted = len(getattr(ulpin_result, "generated", []) or [])

    await session.commit()
    for unit in placed:
        await session.refresh(unit)

    units = await _assemble(gen, placed, building=building, include_geometry=False)
    logger.info(
        "3d ulpin generate building_id=%s floors=%s units=%s placed=%s minted=%s",
        building.building_id,
        floors_created,
        units_created,
        len(placed),
        minted,
    )
    return ThreeDGenerateResponse(
        building_id=building.building_id,
        building_name=building.building_name,
        floors_created=floors_created,
        units_created=units_created,
        units_placed=len(placed),
        ulpins_minted=minted,
        units=units,
    )


async def _scaffold(
    properties: PropertyService,
    *,
    building: Building,
    storeys: int,
    per_floor: int,
    property_type: UnitType,
    actor,
) -> tuple[int, int]:
    """Create storeys 1..N with ``per_floor`` units each, skipping what exists.

    Unit numbers follow the convention the ULPIN format assumes — storey 3, unit
    1 is ``301`` — so the identifier reads ``…-F03-U301`` without a translation
    table.

    Storeys are numbered from 1 upward, ground being 0. ``PropertyService``
    rejects a storey number at or above the building's declared level count, so a
    building registered as "3 floors" is widened to accommodate storeys 1-3 plus
    its ground floor. The alternative — refusing the request — would mean the
    caller had to know this module's numbering convention before using it.
    """
    if building.total_floors <= storeys:
        logger.info(
            "widening building_id=%s total_floors %s -> %s to fit storeys 1..%s",
            building.building_id,
            building.total_floors,
            storeys + 1,
            storeys,
        )
        building.total_floors = storeys + 1
        await properties.session.flush()

    floors_created = 0
    units_created = 0
    height = DEFAULT_FLOOR_HEIGHT_M

    for storey in range(1, storeys + 1):
        floor = await properties.floors.get_by_number(building.building_id, storey)
        if floor is None:
            floor = await properties.add_floor(
                building.building_id,
                FloorCreate(
                    floor_number=storey,
                    floor_type=FloorType.UPPER,
                    floor_label=f"Floor {storey}",
                    # The ladder the specification asks for: storey 1 at 0 m,
                    # storey 2 at 3 m, storey 3 at 6 m. Recorded on the floor so
                    # the generator reads a surveyed elevation, not an assumption.
                    elevation_base_m=(storey - 1) * height,
                    elevation_top_m=storey * height,
                ),
                actor=actor,
            )
            floors_created += 1

        # Re-registering a unit number that already exists would be rejected as a
        # duplicate, so existing numbers are filtered out here instead: re-running
        # the demo against a half-built building must be safe.
        fresh = [
            UnitBase(unit_number=number, property_type=property_type)
            for number in (f"{storey}{slot:02d}" for slot in range(1, per_floor + 1))
            if await properties.units.get_by_number(floor.floor_id, number) is None
        ]
        if fresh:
            created = await properties.create_units_bulk(
                building.building_id,
                UnitBulkCreate(floor_number=storey, units=fresh),
                actor=actor,
            )
            units_created += len(created)

    return floors_created, units_created


# --------------------------------------------------------------------------- #
# Reads — specific paths before the catch-all parameter
# --------------------------------------------------------------------------- #


@router.get(
    "/building/{building_id}",
    response_model=ThreeDBuildingResponse,
    summary="Every unit of a building as 3D volumes, ready to render",
)
async def building_view(
    session: DbSession,
    actor: CurrentUser,
    building_id: Annotated[uuid.UUID, Path()],
) -> ThreeDBuildingResponse:
    gen = ThreeDULPINGenerator(session)
    building, pairs = await gen.building_units(building_id, actor=actor)
    units = [u for u, _ in pairs]

    assembled = await _assemble(gen, units, building=building, include_geometry=False)
    return ThreeDBuildingResponse(
        building_id=building.building_id,
        building_name=building.building_name,
        building_code=building.short_code,
        state=building.state,
        city=building.city,
        latitude=building.latitude,
        longitude=building.longitude,
        total_floors=building.total_floors,
        unit_count=len(units),
        located_count=sum(1 for u in assembled if u.coordinates is not None),
        floor_numbers=sorted({f.floor_number for _, f in pairs}),
        units=assembled,
    )


@router.get(
    "/view/{ulpin}",
    response_model=ThreeDUnitResponse,
    summary="Resolve a ULPIN string to its 3D unit and full property metadata",
)
async def view_by_ulpin(
    session: DbSession,
    actor: CurrentUser,
    ulpin: Annotated[str, Path(max_length=40, description="e.g. WB-KOL-B001-F03-U301")],
) -> ThreeDUnitResponse:
    """The citizen-facing lookup: paste the code from a deed, get the volume.

    Resolution is delegated to ``UlpinService.resolve``, which already accepts the
    short code, the long code and a building code, and applies the read scope.
    Re-implementing the parse here would mean two definitions of what a valid
    ULPIN looks like.
    """
    record = await UlpinService(session).resolve(ulpin, actor=actor)
    if record.unit_id is None:
        raise ValidationException(
            f"{ulpin} identifies a building, not a unit; it has no single volume"
        )

    unit, _floor, building = await ThreeDULPINGenerator(session).unit_with_context(
        record.unit_id, actor=actor
    )
    gen = ThreeDULPINGenerator(session)
    # Geometry included here and nowhere else: this is the one route whose caller
    # asked about a single unit and may well want its GeoJSON. Sending the solid
    # for all 32 units of a tower would be ~40x the payload for no viewer gain,
    # since the scene draws from the scalars.
    geoms = await gen.solids_geojson([unit.unit_id])
    parties = await gen.parties([unit.unit_id])
    owner, tenant = parties.get(unit.unit_id, (None, None))

    return _to_response(
        unit,
        building=building,
        ulpin_short=record.short_code,
        ulpin_full=record.ulpin_code,
        geometry=geoms.get(unit.unit_id),
        owner_name=owner,
        tenant_name=tenant,
    )


@router.get(
    "/{unit_id}",
    response_model=ThreeDUnitResponse,
    summary="One unit's 3D placement, dimensions and metadata",
)
async def unit_view(
    session: DbSession,
    actor: CurrentUser,
    unit_id: Annotated[uuid.UUID, Path()],
) -> ThreeDUnitResponse:
    gen = ThreeDULPINGenerator(session)
    unit, _floor, building = await gen.unit_with_context(unit_id, actor=actor)
    codes = await gen.ulpin_codes([unit_id])
    parties = await gen.parties([unit_id])
    geoms = await gen.solids_geojson([unit_id])
    short, full = codes.get(unit_id, (None, None))
    owner, tenant = parties.get(unit_id, (None, None))
    return _to_response(
        unit,
        building=building,
        ulpin_short=short,
        ulpin_full=full,
        geometry=geoms.get(unit_id),
        owner_name=owner,
        tenant_name=tenant,
    )
