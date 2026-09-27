"""Verification endpoints.

``POST /verify`` is the whole specification in one route: give it a ULPIN, get
back one of four verdicts with the per-step reasoning attached. The rest of the
file exists so that verdict can be audited afterwards — history for one property,
a filterable list across all of them, and an officer's override.

The verdict is always returned with its checks. A citizen shown "Invalid Claim"
and nothing else has been accused without being told what of.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission, VerifiedUser
from app.core.exceptions import NotFoundException
from app.core.permissions import Permission
from app.models.enums import VerificationOutcome, VerificationStatus
from app.models.user import User
from app.repositories.verification_repository import VerificationRepository
from app.schemas.verification import (
    VerificationDecision,
    VerificationListResponse,
    VerificationRecordResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.services.verification_service import VerificationService

router = APIRouter(prefix="/verification", tags=["Verification"])

CanVerify = Annotated[User, Depends(RequirePermission(Permission.VERIFICATION_CREATE))]
CanDecide = Annotated[User, Depends(RequirePermission(Permission.VERIFICATION_DECIDE))]
CanRead = Annotated[User, Depends(RequirePermission(Permission.VERIFICATION_READ))]


@router.post(
    "/verify",
    response_model=VerifyResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify a property by ULPIN",
    description=(
        "Runs the four-step engine — locate the property, read its 3D geometry, "
        "validate ownership, validate tenancy — and returns one of "
        "`VERIFIED`, `PENDING_VERIFICATION`, `INVALID_CLAIM` or "
        "`UNAUTHORIZED_OCCUPANCY`.\n\n"
        "Supply `claimed_owner_name` or `claimed_owner_id` to check a specific "
        "assertion against the register; omit them and ownership is only checked "
        "for internal consistency.\n\n"
        "**`PENDING_VERIFICATION` is not an adverse finding.** It means a record "
        "the engine needs has not been digitised yet — most often the 3D volume. "
        "Only a contradiction in the register produces `INVALID_CLAIM`.\n\n"
        "Set `persist=false` to run the engine without writing a "
        "`verification_records` row, e.g. for a preview on the public search page."
    ),
)
async def verify_property(
    payload: VerifyRequest,
    session: DbSession,
    actor: CanVerify,
) -> VerifyResponse:
    return await VerificationService(session).verify(payload, actor=actor)


@router.get(
    "/history/{code}",
    response_model=VerificationListResponse,
    summary="Every verification run against one property",
    description=(
        "Accepts either code spelling. Newest first — a property whose verdict "
        "changed is more interesting than one that never did, and the change is "
        "visible by reading two adjacent rows."
    ),
)
async def verification_history(
    code: str,
    session: DbSession,
    actor: CanRead,
    pagination: Pagination,
) -> VerificationListResponse:
    items, total = await VerificationService(session).history(
        code, actor=actor, page=pagination.page, page_size=pagination.page_size
    )
    return VerificationListResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get(
    "",
    response_model=VerificationListResponse,
    summary="Search verification records",
)
async def list_verifications(
    session: DbSession,
    actor: CanRead,
    pagination: Pagination,
    outcome: Annotated[VerificationOutcome | None, Query()] = None,
    verification_status: Annotated[VerificationStatus | None, Query()] = None,
    building_id: Annotated[uuid.UUID | None, Query()] = None,
    unit_id: Annotated[uuid.UUID | None, Query()] = None,
) -> VerificationListResponse:
    items, total = await VerificationService(session).list_records(
        actor=actor,
        page=pagination.page,
        page_size=pagination.page_size,
        outcome=outcome,
        verification_status=verification_status,
        building_id=building_id,
        unit_id=unit_id,
    )
    return VerificationListResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.post(
    "/{verification_id}/decide",
    response_model=VerificationRecordResponse,
    summary="Record an officer's decision over the engine's finding",
    description=(
        "The engine is advisory. An officer who has stood in the flat can close a "
        "`PENDING_VERIFICATION` the engine could not resolve, or overturn an "
        "`INVALID_CLAIM` that came from a digitisation error.\n\n"
        "Both findings are kept: the engine's verdict stays in `findings.outcome` "
        "and the override is recorded alongside it under "
        "`findings.manual_decision`. Where they disagree, the disagreement is the "
        "most useful thing in the record — it is how rule precision gets measured "
        "rather than assumed."
    ),
)
async def decide_verification(
    verification_id: uuid.UUID,
    payload: VerificationDecision,
    session: DbSession,
    actor: CanDecide,
    _verified: VerifiedUser,
) -> VerificationRecordResponse:
    record = await VerificationService(session).decide(verification_id, payload, actor=actor)
    return VerificationRecordResponse.model_validate(record)


@router.get(
    "/{verification_id}",
    response_model=VerificationRecordResponse,
    summary="One verification record",
)
async def get_verification(
    verification_id: uuid.UUID,
    session: DbSession,
    actor: CanRead,
) -> VerificationRecordResponse:
    record = await VerificationRepository(session).get(verification_id)
    if record is None:
        raise NotFoundException("Verification record", verification_id)
    return VerificationRecordResponse.model_validate(record)
