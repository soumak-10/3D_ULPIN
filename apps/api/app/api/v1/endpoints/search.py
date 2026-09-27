"""Property search endpoints.

``GET /search`` with a ``q`` — everything else is optional. The route is a GET
with query parameters rather than a POST with a body so a search is a URL: it can
be bookmarked, pasted into a ticket, and linked from an email, which is most of
what makes a public register usable.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession, Pagination
from app.models.enums import OccupancyStatus, UnitType, VerificationOutcome
from app.schemas.search import SearchFilters, SearchResponse, SuggestResponse
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search"])


@router.get(
    "",
    response_model=SearchResponse,
    summary="Search by ULPIN, owner, tenant or building",
    description=(
        "One box, four kinds of answer. Leave `mode=auto` and the shape of the "
        "query decides: anything code-shaped goes to the identifier index, "
        "anything else is matched against owners, tenants and buildings at once.\n\n"
        "Matching is trigram, so a half-remembered fragment works — `Sunrise` "
        "finds *Sunrise Residency Tower B*, `Banerje` finds *Banerjee*. Each "
        "result carries `matched_on` saying which field hit, so a wrong guess is "
        "visible rather than merely disappointing.\n\n"
        "`mode_used` in the response reports what the detector chose; force it "
        "with `mode` if that was wrong."
    ),
)
async def search(
    session: DbSession,
    actor: CurrentUser,
    pagination: Pagination,
    q: Annotated[str, Query(min_length=1, max_length=120, description="What the user typed")],
    mode: Annotated[str, Query(pattern="^(auto|ulpin|owner|tenant|building)$")] = "auto",
    unit_type: Annotated[UnitType | None, Query()] = None,
    occupancy_status: Annotated[OccupancyStatus | None, Query()] = None,
    verification_outcome: Annotated[VerificationOutcome | None, Query()] = None,
    building_id: Annotated[uuid.UUID | None, Query()] = None,
    city: Annotated[str | None, Query(max_length=80)] = None,
    state: Annotated[str | None, Query(max_length=80)] = None,
    floor_number_min: Annotated[int | None, Query(ge=-10, le=200)] = None,
    floor_number_max: Annotated[int | None, Query(ge=-10, le=200)] = None,
    has_open_alerts: Annotated[bool | None, Query()] = None,
) -> SearchResponse:
    filters = SearchFilters(
        unit_type=unit_type,
        occupancy_status=occupancy_status,
        verification_outcome=verification_outcome,
        building_id=building_id,
        city=city,
        state=state,
        floor_number_min=floor_number_min,
        floor_number_max=floor_number_max,
        has_open_alerts=has_open_alerts,
    )
    return await SearchService(session).search(
        q,
        mode=mode,
        filters=filters,
        page=pagination.page,
        page_size=pagination.page_size,
        actor=actor,
    )


@router.get(
    "/suggest",
    response_model=SuggestResponse,
    summary="Typeahead for the search box",
    description=(
        "Short mixed list — a few identifiers, buildings, owners and tenants. "
        "Returns nothing under two characters, because a dropdown that fires on "
        "the first keystroke is a load test."
    ),
)
async def suggest(
    session: DbSession,
    actor: CurrentUser,
    q: Annotated[str, Query(max_length=80)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SuggestResponse:
    return await SearchService(session).suggest(q, actor=actor, limit=limit)
