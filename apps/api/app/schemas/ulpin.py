"""Request and response contracts for 3D ULPIN generation.

Two identifiers travel together on every response and the distinction matters:

``short_code``
    ``WB-KOL-B001-F03-U301``. What a citizen reads out over the phone, what goes
    on the assessment notice, what the search box accepts. Human-facing.

``ulpin_code``
    ``IN29BLR0001234-A1-F007-U0412-Y``. Carries the 14-character parcel ULPIN
    unmodified, so an existing 2D cadastral system resolves it by slicing the
    first fourteen characters. Machine-facing.

Neither is derivable from the other — the short code's building number is a
per-region sequence, not a function of the parcel — so both are stored and both
are uniquely indexed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import UlpinStatus, UlpinType, VerificationOutcome
from app.ulpin.shortcode import BUILDING_SHORTCODE_PATTERN, SHORTCODE_PATTERN

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------
ShortCode = Annotated[
    str,
    StringConstraints(pattern=SHORTCODE_PATTERN.pattern, to_upper=True, strip_whitespace=True),
]
BuildingShortCode = Annotated[
    str,
    StringConstraints(
        pattern=BUILDING_SHORTCODE_PATTERN.pattern, to_upper=True, strip_whitespace=True
    ),
]
# Deliberately loose: the resolve endpoint accepts either spelling plus the
# lowercase, space-separated things people actually paste. Normalisation and the
# real grammar check happen in the service, where a failure can say *which*
# format was attempted and why it did not parse.
AnyCode = Annotated[str, StringConstraints(min_length=3, max_length=40, strip_whitespace=True)]


# ===========================================================================
# Generation
# ===========================================================================
class UlpinGenerateRequest(BaseModel):
    """Mint identifiers for one unit, or for every unit under a building.

    Exactly one subject. Sending a unit mints one code; sending a building mints
    codes for every unit that lacks one, which is the normal path — units are
    registered in bulk from a sanctioned plan and numbered afterwards.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    unit_id: uuid.UUID | None = Field(default=None, description="Mint for this unit only")
    building_id: uuid.UUID | None = Field(
        default=None, description="Mint for every un-coded unit in this building"
    )
    issue: bool = Field(
        default=False,
        description=(
            "Issue immediately rather than leaving the code PROVISIONAL. "
            "Requires ulpin:issue. An issued code is permanent — it can be "
            "superseded but never edited or deleted."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Compute the codes and return them without writing. Nothing is "
            "persisted and no sequence number is consumed."
        ),
    )

    @model_validator(mode="after")
    def exactly_one_subject(self) -> "UlpinGenerateRequest":
        if (self.unit_id is None) == (self.building_id is None):
            raise ValueError("Supply exactly one of unit_id or building_id")
        return self


class UlpinResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ulpin_id: uuid.UUID
    ulpin_code: str = Field(description="Machine-facing parcel-derived identifier")
    short_code: str | None = Field(
        default=None, description="Human-facing STATE-CITY-BUILDING-FLOOR-UNIT code"
    )
    building_short_code: str | None = None
    state_code: str | None = None
    city_code: str | None = None

    ulpin_type: UlpinType
    status: UlpinStatus

    building_id: uuid.UUID | None = None
    floor_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    jurisdiction_code: str | None = None

    issued_at: datetime | None = None
    superseded_at: datetime | None = None
    retired_at: datetime | None = None
    created_at: datetime

    # Denormalised for display. Populated by the service; a bare
    # model_validate() leaves them None rather than guessing.
    unit_number: str | None = None
    floor_number: int | None = None
    building_name: str | None = None
    verification_outcome: VerificationOutcome | None = None


class UlpinGenerateResponse(BaseModel):
    """What a mint run did.

    ``skipped`` is not an error. Re-running generation over a building that is
    already fully coded is the expected idempotent case, and the officer needs
    to see that it was a no-op rather than wonder whether it worked.
    """

    generated: list[UlpinResponse] = Field(default_factory=list)
    skipped: list["UlpinSkip"] = Field(default_factory=list)
    dry_run: bool = False

    @property
    def count(self) -> int:
        return len(self.generated)


class UlpinSkip(BaseModel):
    unit_id: uuid.UUID
    unit_number: str | None = None
    reason: str
    existing_short_code: str | None = None


class UlpinValidateRequest(BaseModel):
    code: AnyCode


class UlpinValidateResponse(BaseModel):
    """Grammar check, then existence check — reported separately.

    A well-formed code that is not in the register and a malformed string are
    different problems with different remedies, and collapsing them into one
    boolean is how a typo gets reported as "this property does not exist".
    """

    input: str
    normalised: str | None = None
    format: str | None = Field(
        default=None, description="SHORT_CODE, ULPIN_CODE, BUILDING_CODE, or null if unparseable"
    )
    is_well_formed: bool
    exists: bool
    error: str | None = None

    # Decomposition, when the code parsed as a short code.
    state_code: str | None = None
    city_code: str | None = None
    building_sequence: int | None = None
    floor_number: int | None = None
    floor_type: str | None = None
    unit_sequence: int | None = None

    ulpin: UlpinResponse | None = None


class UlpinIssueRequest(BaseModel):
    remarks: str | None = Field(default=None, max_length=500)


class UlpinSupersedeRequest(BaseModel):
    """Replace an issued code, keeping the old one resolvable.

    Used when a unit is subdivided or amalgamated. The superseded code keeps
    answering lookups — a deed quotes the code that was current when it was
    signed — but stops being the answer to "what identifies this unit now".
    """

    reason: Annotated[str, StringConstraints(min_length=5, max_length=500)]
    successor_unit_id: uuid.UUID | None = Field(
        default=None,
        description="Unit the replacement code should describe. Defaults to the same unit.",
    )


class UlpinRetireRequest(BaseModel):
    reason: Annotated[str, StringConstraints(min_length=5, max_length=500)]


class UlpinListResponse(BaseModel):
    items: list[UlpinResponse]
    total: int
    page: int
    page_size: int


class UlpinSequenceResponse(BaseModel):
    """Where the per-region building counter stands.

    Exposed because the first question after a failed bulk import is always
    "did it burn my numbers?", and the answer should not require a DBA.
    """

    model_config = ConfigDict(from_attributes=True)

    state_code: str
    city_code: str
    last_value: int
    updated_at: datetime | None = None


UlpinGenerateResponse.model_rebuild()
