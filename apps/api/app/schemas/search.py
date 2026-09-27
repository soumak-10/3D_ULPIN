"""Contracts for property search.

One box, four kinds of answer: a ULPIN, an owner's name, a tenant's name, a
building's name. The user does not tell us which they typed, so the API does not
ask — it detects, and says which it decided in the response, so a wrong guess is
visible rather than merely disappointing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import OccupancyStatus, UnitType, VerificationOutcome

SearchMode = Literal["auto", "ulpin", "owner", "tenant", "building"]


class SearchResult(BaseModel):
    """One unit, with everything the result card shows.

    Flattened rather than nested. The result list is the most-read screen in the
    system and a nested shape means the frontend walks four levels to render a
    row, or worse, issues a follow-up request per result.
    """

    model_config = ConfigDict(from_attributes=True)

    unit_id: uuid.UUID
    unit_number: str
    unit_type: UnitType | None = None
    unit_status: str | None = None
    occupancy_status: OccupancyStatus | None = None
    carpet_area_sqm: float | None = None

    floor_id: uuid.UUID | None = None
    floor_number: int | None = None
    floor_label: str | None = None

    building_id: uuid.UUID | None = None
    building_name: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    ulpin_id: uuid.UUID | None = None
    short_code: str | None = None
    ulpin_code: str | None = None

    verification_outcome: VerificationOutcome | None = None
    last_verified_at: datetime | None = None
    open_alert_count: int = 0

    owner_names: list[str] = Field(default_factory=list)
    tenant_name: str | None = None

    # Why this row matched — "owner: Rina Banerjee" under a result whose title is
    # a flat number is the difference between a list that reads as an answer and
    # one that reads as a guess.
    matched_on: str | None = None
    rank: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode_used: SearchMode
    items: list[SearchResult]
    total: int
    page: int
    page_size: int
    took_ms: int | None = None

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


class SearchFilters(BaseModel):
    """Everything the filter rail offers."""

    unit_type: UnitType | None = None
    occupancy_status: OccupancyStatus | None = None
    verification_outcome: VerificationOutcome | None = None
    building_id: uuid.UUID | None = None
    city: Annotated[str, StringConstraints(max_length=80)] | None = None
    state: Annotated[str, StringConstraints(max_length=80)] | None = None
    floor_number_min: int | None = None
    floor_number_max: int | None = None
    has_open_alerts: bool | None = None


class SuggestItem(BaseModel):
    label: str
    value: str
    kind: Literal["ulpin", "owner", "tenant", "building"]
    hint: str | None = None


class SuggestResponse(BaseModel):
    items: list[SuggestItem] = Field(default_factory=list)
