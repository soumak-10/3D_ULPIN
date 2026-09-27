"""ULPIN endpoints: mint, validate, resolve, and the issued-identifier lifecycle.

``/ulpins/validate`` and ``/ulpins/resolve/{code}`` are deliberately cheap and
tolerant — they are what the citizen-facing search box and the QR scanner call,
and they must answer "that is not a valid code" differently from "that code is
not in the register".
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination, RequirePermission, VerifiedUser
from app.core.permissions import Permission
from app.models.enums import UlpinStatus, UlpinType
from app.models.user import User
from app.schemas.auth import MessageResponse
from app.schemas.ulpin import (
    UlpinGenerateRequest,
    UlpinGenerateResponse,
    UlpinIssueRequest,
    UlpinListResponse,
    UlpinResponse,
    UlpinRetireRequest,
    UlpinSequenceResponse,
    UlpinSupersedeRequest,
    UlpinValidateRequest,
    UlpinValidateResponse,
)
from app.services.ulpin_service import UlpinService

router = APIRouter(prefix="/ulpins", tags=["ULPIN"])

CanGenerate = Annotated[User, Depends(RequirePermission(Permission.ULPIN_GENERATE))]
CanIssue = Annotated[User, Depends(RequirePermission(Permission.ULPIN_ISSUE))]
CanSupersede = Annotated[User, Depends(RequirePermission(Permission.ULPIN_SUPERSEDE))]
CanRetire = Annotated[User, Depends(RequirePermission(Permission.ULPIN_RETIRE))]


# ===========================================================================
# Generation
# ===========================================================================
@router.post(
    "/generate",
    response_model=UlpinGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mint identifiers for a unit or a whole building",
    description=(
        "Format `STATE-CITY-BUILDING-FLOOR-UNIT`, e.g. `WB-KOL-B001-F03-U301`.\n\n"
        "Send `building_id` to code every unit that lacks an identifier — the "
        "normal path, since units are registered in bulk from a sanctioned plan. "
        "Send `unit_id` to code one.\n\n"
        "Idempotent: units that already carry a live identifier are reported under "
        "`skipped`, not re-coded and not treated as an error. Use `dry_run=true` "
        "to see the codes without consuming a sequence number."
    ),
)
async def generate_ulpins(
    payload: UlpinGenerateRequest,
    session: DbSession,
    actor: CanGenerate,
    _verified: VerifiedUser,
) -> UlpinGenerateResponse:
    return await UlpinService(session).generate(payload, actor=actor)


# ===========================================================================
# Validation and resolution
# ===========================================================================
@router.post(
    "/validate",
    response_model=UlpinValidateResponse,
    summary="Check a code's grammar and look it up",
    description=(
        "Accepts either spelling — the short `WB-KOL-B001-F03-U301` or the "
        "parcel-derived `IN29BLR0001234-A1-F007-U0412-Y` — and tolerates messy "
        "input such as lowercase or space separators.\n\n"
        "`is_well_formed` and `exists` are reported separately on purpose: a typo "
        "and a code that is genuinely not in the register need different answers."
    ),
)
async def validate_ulpin(
    payload: UlpinValidateRequest,
    session: DbSession,
    actor: CurrentUser,
) -> UlpinValidateResponse:
    return await UlpinService(session).validate(payload.code)


@router.get(
    "/resolve/{code}",
    response_model=UlpinResponse,
    summary="Resolve a code to its identifier record",
    description=(
        "Superseded and retired codes still resolve — a deed quotes the code that "
        "was current when it was signed — so check `status` before treating the "
        "answer as current."
    ),
)
async def resolve_ulpin(
    code: str,
    session: DbSession,
    actor: CurrentUser,
) -> UlpinResponse:
    service = UlpinService(session)
    row = await service.resolve(code, actor=actor)
    return await service.to_response(row)


@router.get("", response_model=UlpinListResponse, summary="Search issued identifiers")
async def list_ulpins(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    q: Annotated[str | None, Query(max_length=40, description="Fragment of either code")] = None,
    ulpin_status: Annotated[UlpinStatus | None, Query()] = None,
    ulpin_type: Annotated[UlpinType | None, Query()] = None,
    building_id: Annotated[uuid.UUID | None, Query()] = None,
    state: Annotated[str | None, Query(max_length=20)] = None,
    city: Annotated[str | None, Query(max_length=40)] = None,
) -> UlpinListResponse:
    items, total = await UlpinService(session).list_ulpins(
        actor=actor,
        page=pagination.page,
        page_size=pagination.page_size,
        q=q,
        status=ulpin_status,
        ulpin_type=ulpin_type,
        building_id=building_id,
        state=state,
        city=city,
    )
    return UlpinListResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get(
    "/sequences",
    response_model=list[UlpinSequenceResponse],
    summary="Per-region building counters",
    description=(
        "Where the `Bnnn` counter stands in each state+city. The first question "
        "after a failed bulk import is always whether it burned numbers; this "
        "answers it without a DBA."
    ),
)
async def list_sequences(
    session: DbSession,
    actor: CurrentUser,
) -> list[UlpinSequenceResponse]:
    rows = await UlpinService(session).sequences_report(actor=actor)
    return [UlpinSequenceResponse.model_validate(r) for r in rows]


@router.get("/{ulpin_id}", response_model=UlpinResponse, summary="One identifier by id")
async def get_ulpin(
    ulpin_id: uuid.UUID,
    session: DbSession,
    actor: CurrentUser,
) -> UlpinResponse:
    return await UlpinService(session).get_by_id(ulpin_id, actor=actor)


# ===========================================================================
# Lifecycle
# ===========================================================================
@router.post(
    "/{ulpin_id}/issue",
    response_model=UlpinResponse,
    summary="Issue a provisional identifier",
    description=(
        "Issuing is one-way. An issued identifier can be superseded but never "
        "edited or deleted, because documents quote it. Re-issuing an already "
        "issued code is a no-op rather than an error."
    ),
)
async def issue_ulpin(
    ulpin_id: uuid.UUID,
    payload: UlpinIssueRequest,
    session: DbSession,
    actor: CanIssue,
    _verified: VerifiedUser,
) -> UlpinResponse:
    service = UlpinService(session)
    row = await service.issue(ulpin_id, actor=actor, remarks=payload.remarks)
    return await service.to_response(row)


@router.post(
    "/{ulpin_id}/supersede",
    response_model=UlpinResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Replace an issued identifier, keeping the old one resolvable",
    description=(
        "For subdivision and amalgamation. Returns the **new** identifier; the "
        "superseded one keeps resolving with `status=SUPERSEDED` and a "
        "`supersedes_id` link from its replacement."
    ),
)
async def supersede_ulpin(
    ulpin_id: uuid.UUID,
    payload: UlpinSupersedeRequest,
    session: DbSession,
    actor: CanSupersede,
    _verified: VerifiedUser,
) -> UlpinResponse:
    service = UlpinService(session)
    _old, new = await service.supersede(
        ulpin_id,
        reason=payload.reason,
        successor_unit_id=payload.successor_unit_id,
        actor=actor,
    )
    return await service.to_response(new)


@router.post(
    "/{ulpin_id}/retire",
    response_model=MessageResponse,
    summary="Retire an identifier",
    description=(
        "For demolition and de-registration. The row is never deleted — retiring "
        "it stops it being a live answer while keeping historic lookups working."
    ),
)
async def retire_ulpin(
    ulpin_id: uuid.UUID,
    payload: UlpinRetireRequest,
    session: DbSession,
    actor: CanRetire,
    _verified: VerifiedUser,
) -> MessageResponse:
    row = await UlpinService(session).retire(ulpin_id, reason=payload.reason, actor=actor)
    return MessageResponse(message=f"Identifier {row.display_code} retired")
