"""Search orchestration.

Thin on purpose — the interesting work is the query shapes in the repository.
What lives here is the part that has to be decided rather than computed: which
field the user probably meant, and how to say so in the response.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import Permission, has_permission
from app.models.user import User
from app.repositories.search_repository import SearchRepository
from app.schemas.search import (
    SearchFilters,
    SearchResponse,
    SearchResult,
    SuggestItem,
    SuggestResponse,
)


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SearchRepository(session)

    async def search(
        self,
        q: str,
        *,
        mode: str,
        filters: SearchFilters,
        page: int,
        page_size: int,
        actor: User,
    ) -> SearchResponse:
        if not has_permission(actor.role, Permission.UNIT_READ):
            raise PermissionDeniedException("This action requires unit:read")

        started = time.perf_counter()
        query = q.strip()
        resolved = self.repo.detect_mode(query) if mode == "auto" else mode

        rows, total = await self.repo.search(
            query,
            mode=resolved,
            page=page,
            page_size=page_size,
            filters=filters.model_dump(exclude_none=True),
        )

        items = [self._to_result(r, query, resolved) for r in rows]
        return SearchResponse(
            query=query,
            mode_used=resolved,  # type: ignore[arg-type]
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            took_ms=int((time.perf_counter() - started) * 1000),
        )

    async def suggest(self, q: str, *, actor: User, limit: int = 8) -> SuggestResponse:
        if len(q.strip()) < 2:
            return SuggestResponse(items=[])
        rows = await self.repo.suggest(q.strip(), limit=limit)
        return SuggestResponse(items=[SuggestItem(**r) for r in rows])

    # -----------------------------------------------------------------------
    @staticmethod
    def _to_result(row: dict, query: str, mode: str) -> SearchResult:
        owners = [o for o in (row.get("owner_names") or []) if o]
        tenant = row.get("tenant_name")

        # Say why the row matched. Computed here rather than in SQL because it is
        # presentation, and because the cheap string test is right often enough
        # that paying for a per-row SQL expression would be waste.
        needle = query.lower()
        if any(needle in (o or "").lower() for o in owners):
            matched_on = f"Owner: {next(o for o in owners if needle in o.lower())}"
        elif tenant and needle in tenant.lower():
            matched_on = f"Tenant: {tenant}"
        elif row.get("building_name") and needle in row["building_name"].lower():
            matched_on = f"Building: {row['building_name']}"
        elif row.get("short_code"):
            matched_on = f"ULPIN: {row['short_code']}"
        else:
            matched_on = None

        address = ", ".join(
            p for p in (row.get("address_line1"), row.get("city"), row.get("state")) if p
        )

        return SearchResult(
            unit_id=row["unit_id"],
            unit_number=row["unit_number"],
            unit_type=row.get("unit_type"),
            unit_status=(
                row["unit_status"].value
                if row.get("unit_status") is not None and hasattr(row["unit_status"], "value")
                else row.get("unit_status")
            ),
            occupancy_status=row.get("occupancy_status"),
            carpet_area_sqm=(
                float(row["carpet_area_sqm"]) if row.get("carpet_area_sqm") is not None else None
            ),
            floor_id=row.get("floor_id"),
            floor_number=row.get("floor_number"),
            floor_label=row.get("floor_label"),
            building_id=row.get("building_id"),
            building_name=row.get("building_name"),
            address=address or None,
            city=row.get("city"),
            state=row.get("state"),
            latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
            longitude=float(row["longitude"]) if row.get("longitude") is not None else None,
            ulpin_id=row.get("ulpin_id"),
            short_code=row.get("short_code"),
            ulpin_code=row.get("ulpin_code"),
            verification_outcome=row.get("verification_outcome"),
            last_verified_at=row.get("last_verified_at"),
            open_alert_count=int(row.get("open_alert_count") or 0),
            owner_names=owners,
            tenant_name=tenant,
            matched_on=matched_on,
            rank=float(row["rank"]) if row.get("rank") is not None else None,
        )
