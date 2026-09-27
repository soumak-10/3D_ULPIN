"""Building registration endpoints.

Capability is checked here by ``RequirePermission``; row-level scope — is this
building inside the officer's jurisdiction — is checked inside the service,
which is the only layer holding the loaded row.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission, VerifiedUser
from app.core.permissions import Permission
from app.models.enums import BuildingStatus, BuildingUse, OccupancyStatus
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.property import (
    BuildingCreate,
    BuildingDetailResponse,
    BuildingListResponse,
    BuildingResponse,
    BuildingSearchParams,
    BuildingStatusUpdate,
    BuildingUpdate,
    FloorCreate,
    FloorResponse,
    FloorUpdate,
    UnitBulkCreate,
    UnitCreate,
    UnitListResponse,
    UnitResponse,
)
from app.services.property_service import PropertyService

router = APIRouter(prefix="/buildings", tags=["Buildings"])

# The guards return the authenticated principal, so a handler can take the
# permission check and the actor in one parameter instead of two.
CanCreateBuilding = Annotated[User, Depends(RequirePermission(Permission.BUILDING_CREATE))]
CanUpdateBuilding = Annotated[User, Depends(RequirePermission(Permission.BUILDING_UPDATE))]
CanDeleteBuilding = Annotated[User, Depends(RequirePermission(Permission.BUILDING_DELETE))]
CanCreateFloor = Annotated[User, Depends(RequirePermission(Permission.FLOOR_CREATE))]
CanUpdateFloor = Annotated[User, Depends(RequirePermission(Permission.FLOOR_UPDATE))]
CanCreateUnit = Annotated[User, Depends(RequirePermission(Permission.UNIT_CREATE))]


# ===========================================================================
# Buildings
# ===========================================================================
@router.post(
    "",
    response_model=BuildingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a building",
    description=(
        "Creates the building in DRAFT. With `auto_create_floors` (the default) "
        "one floor row is generated per storey, basements included, so units can "
        "be registered immediately."
    ),
)
async def register_building(
    payload: BuildingCreate,
    session: DbSession,
    actor: CanCreateBuilding,
    _verified: VerifiedUser,
) -> BuildingResponse:
    building = await PropertyService(session).create_building(payload, actor=actor)
    return BuildingResponse.model_validate(building)


@router.get("", response_model=BuildingListResponse, summary="Search buildings")
async def search_buildings(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    q: Annotated[str | None, Query(max_length=120, description="Name, address or ULPIN")] = None,
    city: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    building_status: Annotated[BuildingStatus | None, Query(alias="status")] = None,
    building_use: Annotated[BuildingUse | None, Query()] = None,
    parcel_ulpin: Annotated[str | None, Query(min_length=14, max_length=14)] = None,
    near_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    near_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_m: Annotated[
        int | None,
        Query(ge=10, le=50_000, description="Metres. Requires near_lat and near_lon."),
    ] = None,
) -> BuildingListResponse:
    # Built here rather than bound as a dependency so the model validators —
    # notably "radius requires a centre" — run inside the request and surface as
    # a 422 through the normal handler.
    params = BuildingSearchParams(
        q=q,
        city=city,
        state=state,
        status=building_status,
        building_use=building_use,
        parcel_ulpin=parcel_ulpin,
        near_lat=near_lat,
        near_lon=near_lon,
        radius_m=radius_m,
    )
    rows, total = await PropertyService(session).search_buildings(
        params, actor=actor, page=pagination.page, page_size=pagination.page_size
    )
    return BuildingListResponse(
        items=[BuildingResponse.model_validate(b) for b in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{building_id}",
    response_model=BuildingDetailResponse,
    summary="Building with its floors and occupancy summary",
)
async def get_building(
    building_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> BuildingDetailResponse:
    service = PropertyService(session)
    building = await service.get_building(building_id, actor=actor)
    counts = await service.buildings.unit_counts(building_id)

    detail = BuildingDetailResponse.model_validate(building)
    detail.floors = [FloorResponse.model_validate(f) for f in building.floors]
    detail.occupied_units = counts.get(OccupancyStatus.OCCUPIED.value, 0)
    detail.vacant_units = counts.get(OccupancyStatus.VACANT.value, 0)
    return detail


@router.patch(
    "/{building_id}",
    response_model=BuildingResponse,
    summary="Amend a building that is not yet registered",
)
async def update_building(
    building_id: uuid.UUID,
    payload: BuildingUpdate,
    session: DbSession,
    actor: CanUpdateBuilding,
) -> BuildingResponse:
    building = await PropertyService(session).update_building(
        building_id, payload, actor=actor
    )
    return BuildingResponse.model_validate(building)


@router.post(
    "/{building_id}/status",
    response_model=BuildingResponse,
    summary="Move a building through the registration workflow",
    description=(
        "DRAFT → SUBMITTED → UNDER_VERIFICATION → VERIFIED → REGISTERED, with "
        "REJECTED reachable from the middle states. Any other transition is a 409."
    ),
)
async def change_building_status(
    building_id: uuid.UUID,
    payload: BuildingStatusUpdate,
    session: DbSession,
    actor: CurrentUser,
) -> BuildingResponse:
    building = await PropertyService(session).set_building_status(
        building_id, payload.status, actor=actor
    )
    return BuildingResponse.model_validate(building)


@router.delete(
    "/{building_id}",
    response_model=MessageResponse,
    summary="Soft-delete an unregistered, empty building",
)
async def delete_building(
    building_id: uuid.UUID,
    session: DbSession,
    actor: CanDeleteBuilding,
) -> MessageResponse:
    await PropertyService(session).delete_building(building_id, actor=actor)
    return MessageResponse(message="Building deleted")


# ===========================================================================
# Floors
# ===========================================================================
@router.get(
    "/{building_id}/floors",
    response_model=list[FloorResponse],
    summary="Floors of a building, basements first",
)
async def list_floors(
    building_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> list[FloorResponse]:
    floors = await PropertyService(session).list_floors(building_id, actor=actor)
    return [FloorResponse.model_validate(f) for f in floors]


@router.post(
    "/{building_id}/floors",
    response_model=FloorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a single floor",
)
async def add_floor(
    building_id: uuid.UUID,
    payload: FloorCreate,
    session: DbSession,
    actor: CanCreateFloor,
) -> FloorResponse:
    floor = await PropertyService(session).add_floor(building_id, payload, actor=actor)
    return FloorResponse.model_validate(floor)


@router.patch(
    "/floors/{floor_id}",
    response_model=FloorResponse,
    summary="Amend a floor's label, type, elevations or areas",
)
async def update_floor(
    floor_id: uuid.UUID,
    payload: FloorUpdate,
    session: DbSession,
    actor: CanUpdateFloor,
) -> FloorResponse:
    floor = await PropertyService(session).update_floor(floor_id, payload, actor=actor)
    return FloorResponse.model_validate(floor)


# ===========================================================================
# Units nested under a building
# ===========================================================================
@router.post(
    "/{building_id}/units",
    response_model=UnitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register one unit",
)
async def register_unit(
    building_id: uuid.UUID,
    payload: UnitCreate,
    session: DbSession,
    actor: CanCreateUnit,
    _verified: VerifiedUser,
) -> UnitResponse:
    unit = await PropertyService(session).create_unit(building_id, payload, actor=actor)
    return UnitResponse.model_validate(unit)


@router.post(
    "/{building_id}/units/bulk",
    response_model=list[UnitResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a whole floor of units in one transaction",
    description=(
        "All or nothing. A batch that fails partway leaves no half-registered "
        "floor behind."
    ),
)
async def register_units_bulk(
    building_id: uuid.UUID,
    payload: UnitBulkCreate,
    session: DbSession,
    actor: CanCreateUnit,
    _verified: VerifiedUser,
) -> list[UnitResponse]:
    units = await PropertyService(session).create_units_bulk(building_id, payload, actor=actor)
    return [UnitResponse.model_validate(u) for u in units]


@router.get(
    "/{building_id}/units",
    response_model=UnitListResponse,
    summary="Units in a building, optionally filtered by floor or type",
)
async def list_building_units(
    building_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    floor_number: Annotated[int | None, Query(ge=-20, le=250)] = None,
    occupancy_status: Annotated[OccupancyStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=40, description="Unit number")] = None,
) -> UnitListResponse:
    rows, total = await PropertyService(session).list_units(
        actor=actor,
        page=pagination.page,
        page_size=pagination.page_size,
        building_id=building_id,
        floor_number=floor_number,
        occupancy_status=occupancy_status,
        q=q,
    )
    return UnitListResponse(
        items=[UnitResponse.model_validate(u) for u in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )
