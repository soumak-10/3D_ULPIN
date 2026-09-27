"""Owner endpoints.

Owner records hold the most sensitive data in the system — names, addresses and
national identity numbers. Three rules follow from that and are enforced here:

* the full national ID is never returned, only its last four digits;
* a principal may always read and correct their *own* owner record, whatever
  their role, which is why the read routes take ``CurrentUser`` and defer the
  decision to the service;
* the search route is officer-only, because a free-text search over owners is
  exactly the capability an attacker wants.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission, VerifiedUser
from app.core.exceptions import NotFoundException
from app.core.permissions import Permission
from app.models.user import User
from app.schemas.property import (
    OwnerCreate,
    OwnerListResponse,
    OwnerResponse,
    OwnerUpdate,
)
from app.services.property_service import PropertyService

router = APIRouter(prefix="/owners", tags=["Owners"])

CanCreateOwner = Annotated[User, Depends(RequirePermission(Permission.OWNER_CREATE))]
CanSearchOwners = Annotated[User, Depends(RequirePermission(Permission.OWNER_READ))]


@router.post(
    "",
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an owner record",
    description=(
        "The national ID is hashed under the service pepper before it is stored "
        "and is not recoverable from the API. An identity already on file does "
        "not block the create — one person may own many units — but it is logged "
        "for the fraud module."
    ),
)
async def create_owner(
    payload: OwnerCreate,
    session: DbSession,
    actor: CanCreateOwner,
    _verified: VerifiedUser,
) -> OwnerResponse:
    owner = await PropertyService(session).create_owner(payload, actor=actor)
    return OwnerResponse.model_validate(owner)


@router.get(
    "",
    response_model=OwnerListResponse,
    summary="Search owners by name, organisation, email or phone",
)
async def search_owners(
    session: DbSession,
    actor: CanSearchOwners,
    pagination: Pagination,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> OwnerListResponse:
    rows, total = await PropertyService(session).search_owners(
        actor=actor, page=pagination.page, page_size=pagination.page_size, q=q
    )
    return OwnerListResponse(
        items=[OwnerResponse.model_validate(o) for o in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


# Declared before "/{owner_id}" so the literal wins the route match.
@router.get(
    "/me",
    response_model=OwnerResponse,
    summary="The owner record attached to the caller's login",
)
async def get_my_owner_record(
    session: DbSession,
    actor: CurrentUser,
) -> OwnerResponse:
    owner = await PropertyService(session).owners.get_by_user(actor.user_id)
    if owner is None:
        raise NotFoundException("Owner record for this login", actor.user_id)
    return OwnerResponse.model_validate(owner)


@router.get("/{owner_id}", response_model=OwnerResponse, summary="One owner")
async def get_owner(
    owner_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> OwnerResponse:
    owner = await PropertyService(session).get_owner(owner_id, actor=actor)
    return OwnerResponse.model_validate(owner)


@router.patch(
    "/{owner_id}",
    response_model=OwnerResponse,
    summary="Correct an owner's contact details",
    description=(
        "Owners may edit their own record. Editing anyone else's needs "
        "`owner:update`. Identity fields are not editable here at all — a "
        "correction to a national ID goes through verification."
    ),
)
async def update_owner(
    owner_id: uuid.UUID,
    payload: OwnerUpdate,
    session: DbSession,
    actor: CurrentUser,
) -> OwnerResponse:
    owner = await PropertyService(session).update_owner(owner_id, payload, actor=actor)
    return OwnerResponse.model_validate(owner)
