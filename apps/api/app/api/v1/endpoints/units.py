"""Unit endpoints: the vertical property unit itself, its title and its lettings.

Reads are scope-checked against the caller's interest in the row — an owner sees
their own flat, an officer sees everything in their jurisdiction — so these
routes are open to any authenticated principal and refused inside the service.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission, VerifiedUser
from app.core.permissions import Permission
from app.models.enums import OccupancyStatus, UnitStatus, UnitType
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.property import (
    OccupancyUpdate,
    OwnershipAssign,
    OwnershipResponse,
    TenantCreate,
    TenantListResponse,
    TenantResponse,
    UnitDetailResponse,
    UnitListResponse,
    UnitResponse,
    UnitUpdate,
)
from app.services.property_service import PropertyService

router = APIRouter(prefix="/units", tags=["Units"])

CanUpdateUnit = Annotated[User, Depends(RequirePermission(Permission.UNIT_UPDATE))]
CanDeleteUnit = Annotated[User, Depends(RequirePermission(Permission.UNIT_DELETE))]
CanManageOwnership = Annotated[User, Depends(RequirePermission(Permission.OWNERSHIP_MANAGE))]


# ===========================================================================
# Units
# ===========================================================================
@router.get("", response_model=UnitListResponse, summary="Search units")
async def search_units(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    building_id: Annotated[uuid.UUID | None, Query()] = None,
    floor_number: Annotated[int | None, Query(ge=-20, le=250)] = None,
    property_type: Annotated[UnitType | None, Query()] = None,
    occupancy_status: Annotated[OccupancyStatus | None, Query()] = None,
    unit_status: Annotated[UnitStatus | None, Query()] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=40)] = None,
) -> UnitListResponse:
    rows, total = await PropertyService(session).list_units(
        actor=actor,
        page=pagination.page,
        page_size=pagination.page_size,
        building_id=building_id,
        floor_number=floor_number,
        unit_type=property_type,
        occupancy_status=occupancy_status,
        unit_status=unit_status,
        owner_id=owner_id,
        q=q,
    )
    return UnitListResponse(
        items=[UnitResponse.model_validate(u) for u in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get(
    "/{unit_id}",
    response_model=UnitDetailResponse,
    summary="One unit with its floor, owners and current tenancy",
)
async def get_unit(
    unit_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> UnitDetailResponse:
    unit = await PropertyService(session).get_unit(unit_id, actor=actor)

    detail = UnitDetailResponse.model_validate(unit)
    detail.floor_number = unit.floor.floor_number if unit.floor else None
    detail.storey_code = unit.floor.storey_code if unit.floor else None
    detail.building_name = unit.building.building_name if unit.building else None
    detail.owners = [
        OwnershipResponse.model_validate(o).model_copy(
            update={"owner_name": o.owner.display_name if o.owner else None}
        )
        for o in unit.ownerships
        if o.ended_on is None
    ]
    current = next((t for t in unit.tenancies if t.is_current), None)
    detail.current_tenancy = TenantResponse.model_validate(current) if current else None
    return detail


@router.patch("/{unit_id}", response_model=UnitResponse, summary="Amend a unit")
async def update_unit(
    unit_id: uuid.UUID,
    payload: UnitUpdate,
    session: DbSession,
    actor: CanUpdateUnit,
) -> UnitResponse:
    unit = await PropertyService(session).update_unit(unit_id, payload, actor=actor)
    return UnitResponse.model_validate(unit)


@router.post(
    "/{unit_id}/occupancy",
    response_model=UnitResponse,
    summary="Mark a unit Occupied or Vacant",
    description=(
        "Marking a unit vacant while an active tenancy runs against it is a 409 — "
        "end the tenancy first, so the two records cannot disagree."
    ),
)
async def set_occupancy(
    unit_id: uuid.UUID,
    payload: OccupancyUpdate,
    session: DbSession,
    actor: CanUpdateUnit,
) -> UnitResponse:
    unit = await PropertyService(session).set_occupancy(
        unit_id, payload.occupancy_status, actor=actor
    )
    return UnitResponse.model_validate(unit)


@router.delete("/{unit_id}", response_model=MessageResponse, summary="Soft-delete a draft unit")
async def delete_unit(
    unit_id: uuid.UUID,
    session: DbSession,
    actor: CanDeleteUnit,
) -> MessageResponse:
    await PropertyService(session).delete_unit(unit_id, actor=actor)
    return MessageResponse(message="Unit deleted")


# ===========================================================================
# Ownership
# ===========================================================================
@router.get(
    "/{unit_id}/ownership",
    response_model=list[OwnershipResponse],
    summary="Current title over a unit",
)
async def list_ownership(
    unit_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> list[OwnershipResponse]:
    rows = await PropertyService(session).list_ownership(unit_id, actor=actor)
    return [
        OwnershipResponse.model_validate(o).model_copy(
            update={"owner_name": o.owner.display_name if o.owner else None}
        )
        for o in rows
    ]


@router.post(
    "/{unit_id}/ownership",
    response_model=list[OwnershipResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Assign or transfer title",
    description=(
        "Shares must total exactly 1. Send `replace_existing=true` to transfer: "
        "the outgoing shares are ended and the incoming ones recorded in the same "
        "transaction, which is why the sum rule is deferred to COMMIT."
    ),
)
async def assign_ownership(
    unit_id: uuid.UUID,
    payload: OwnershipAssign,
    session: DbSession,
    actor: CanManageOwnership,
    _verified: VerifiedUser,
) -> list[OwnershipResponse]:
    rows = await PropertyService(session).assign_ownership(unit_id, payload, actor=actor)
    return [OwnershipResponse.model_validate(o) for o in rows]


# ===========================================================================
# Tenancies attached to a unit
# ===========================================================================
@router.get(
    "/{unit_id}/tenancies",
    response_model=TenantListResponse,
    summary="Tenancy history for a unit, most recent first",
)
async def list_tenancies(
    unit_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
) -> TenantListResponse:
    rows, total = await PropertyService(session).list_tenancies(
        unit_id, actor=actor, page=pagination.page, page_size=pagination.page_size
    )
    return TenantListResponse(
        items=[TenantResponse.model_validate(t) for t in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post(
    "/{unit_id}/tenancies",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a tenancy",
    description=(
        "An owner may let their own unit; an officer may record any tenancy in "
        "their jurisdiction. Overlapping active leases on one unit are refused "
        "both here and by an exclusion constraint in the database."
    ),
)
async def create_tenancy(
    unit_id: uuid.UUID,
    payload: TenantCreate,
    session: DbSession,
    actor: CurrentUser,
    _verified: VerifiedUser,
) -> TenantResponse:
    tenant = await PropertyService(session).create_tenancy(unit_id, payload, actor=actor)
    return TenantResponse.model_validate(tenant)
