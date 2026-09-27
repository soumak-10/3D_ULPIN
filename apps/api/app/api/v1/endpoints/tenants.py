"""Tenancy endpoints.

Creating a tenancy is a unit-scoped act and lives on the units router
(``POST /units/{unit_id}/tenancies``). What is left here operates on an existing
lease by its own identifier.

Ending a tenancy is modelled as ``POST /{id}/end`` rather than ``DELETE``: the
record is kept. A terminated lease is evidence of who occupied a unit and when,
and the register is not allowed to forget that.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission
from app.core.permissions import Permission
from app.models.user import User
from app.schemas.property import TenantListResponse, TenantResponse, TenantUpdate
from app.services.property_service import PropertyService

router = APIRouter(prefix="/tenancies", tags=["Tenancies"])

CanUpdateTenancy = Annotated[User, Depends(RequirePermission(Permission.TENANT_UPDATE))]


# Declared before "/{tenant_id}" so the literal wins the route match.
@router.get(
    "/me",
    response_model=TenantListResponse,
    summary="The caller's own tenancies, most recent lease first",
)
async def list_my_tenancies(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
) -> TenantListResponse:
    rows, total = await PropertyService(session).my_tenancies(
        actor=actor, page=pagination.page, page_size=pagination.page_size
    )
    return TenantListResponse(
        items=[TenantResponse.model_validate(t) for t in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{tenant_id}", response_model=TenantResponse, summary="One tenancy")
async def get_tenancy(
    tenant_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> TenantResponse:
    tenant = await PropertyService(session).get_tenancy(tenant_id, actor=actor)
    return TenantResponse.model_validate(tenant)


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Amend a tenancy",
    description=(
        "Extending `lease_end` is re-checked against every other active lease on "
        "the unit, so an extension cannot silently overlap the next tenant. "
        "Moving the status to TERMINATED or EXPIRED releases the unit to Vacant "
        "unless another active lease still covers today."
    ),
)
async def update_tenancy(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    session: DbSession,
    actor: CanUpdateTenancy,
) -> TenantResponse:
    tenant = await PropertyService(session).update_tenancy(tenant_id, payload, actor=actor)
    return TenantResponse.model_validate(tenant)


@router.post(
    "/{tenant_id}/end",
    response_model=TenantResponse,
    status_code=status.HTTP_200_OK,
    summary="Terminate a tenancy as of today",
    description=(
        "Sets status TERMINATED and `lease_end` to today, then vacates the unit "
        "if nothing else occupies it. The record itself is retained."
    ),
)
async def end_tenancy(
    tenant_id: uuid.UUID,
    session: DbSession,
    actor: CanUpdateTenancy,
) -> TenantResponse:
    tenant = await PropertyService(session).end_tenancy(tenant_id, actor=actor)
    return TenantResponse.model_validate(tenant)
