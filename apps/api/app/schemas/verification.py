"""Contracts for the verification engine.

The engine runs five steps and returns one of four verdicts. The per-step detail
is returned alongside the verdict, always — a citizen told "Invalid Claim" with
no further explanation has been accused of something and given nothing to
answer, which is how a clerical gap in a digitisation batch turns into a
grievance petition.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import VerificationOutcome, VerificationStatus, VerificationType

# The five steps, in execution order. Named so the frontend can render a
# checklist that matches the spec without hardcoding the strings twice.
CheckName = Literal[
    "FETCH_PROPERTY",
    "FETCH_GEOMETRY",
    "VALIDATE_OWNERSHIP",
    "VALIDATE_TENANT",
]

CHECK_SEQUENCE: tuple[str, ...] = (
    "FETCH_PROPERTY",
    "FETCH_GEOMETRY",
    "LOCATE_IN_SPACE",
    "VALIDATE_OWNERSHIP",
    "VALIDATE_TENANT",
)

CHECK_LABELS: dict[str, str] = {
    "FETCH_PROPERTY": "Property record located",
    "FETCH_GEOMETRY": "3D geometry present and valid",
    # The vertical dimension's own check: a claim is about a volume, so the claim
    # must be assessed against the volume and not only against the row.
    "LOCATE_IN_SPACE": "Spatial location consistent with the register",
    "VALIDATE_OWNERSHIP": "Ownership record consistent",
    "VALIDATE_TENANT": "Tenancy record consistent",
}


class CheckResult(BaseModel):
    """One step's outcome.

    ``passed=False`` is not the same as ``blocking=True``. A unit with no
    geometry yet fails FETCH_GEOMETRY, but that is a digitisation backlog, not a
    defective title — it holds the verdict at PENDING_VERIFICATION rather than
    pushing it to INVALID_CLAIM.
    """

    check: str
    label: str
    passed: bool
    blocking: bool = Field(
        default=False,
        description="Whether failing this check can produce an adverse verdict on its own",
    )
    detail: str
    evidence: dict = Field(default_factory=dict)


class VerifyRequest(BaseModel):
    """Input is a ULPIN, per the specification. Everything else is optional context."""

    model_config = ConfigDict(str_strip_whitespace=True)

    ulpin: Annotated[str, StringConstraints(min_length=3, max_length=40)] = Field(
        description="Either the short code (WB-KOL-B001-F03-U301) or the long form"
    )
    claimed_owner_name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Name the claimant asserts is on the title. Supplied, it is compared "
            "against the register and a mismatch is an INVALID_CLAIM. Omitted, "
            "ownership is only checked for internal consistency."
        ),
    )
    claimed_owner_id: uuid.UUID | None = None
    claimed_tenant_name: str | None = Field(default=None, max_length=200)
    claimed_tenant_id: uuid.UUID | None = None
    persist: bool = Field(
        default=True,
        description="Write a verification_records row. False runs the engine read-only.",
    )
    verification_type: VerificationType = VerificationType.DOCUMENT


class VerifyResponse(BaseModel):
    """The verdict, plus everything needed to explain it."""

    ulpin: str
    resolved_code: str | None = None
    outcome: VerificationOutcome
    headline: str = Field(description="One sentence fit to show a citizen")
    confidence: Decimal = Field(ge=0, le=1)

    checks: list[CheckResult] = Field(default_factory=list)
    checks_passed: int = 0
    checks_failed: int = 0

    unit_id: uuid.UUID | None = None
    building_id: uuid.UUID | None = None
    floor_number: int | None = None
    unit_number: str | None = None
    building_name: str | None = None

    owner_names: list[str] = Field(default_factory=list)
    tenant_name: str | None = None
    occupancy_status: str | None = None

    verification_id: uuid.UUID | None = Field(
        default=None, description="Null when persist=false"
    )
    verified_at: datetime
    expires_at: datetime | None = None

    # Raised while verifying, not fetched. An engine that finds a contradiction
    # should say so at the moment it finds it rather than waiting for a sweep.
    alerts_raised: list[uuid.UUID] = Field(default_factory=list)


class VerificationRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verification_id: uuid.UUID
    ulpin_id: uuid.UUID
    unit_id: uuid.UUID | None = None
    building_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None

    verification_type: VerificationType
    status: VerificationStatus
    outcome: VerificationOutcome | None = None

    requested_at: datetime
    verified_at: datetime | None = None
    expires_at: datetime | None = None

    method: str | None = None
    reference_no: str | None = None
    remarks: str | None = None
    rejection_reason: str | None = None

    confidence: Decimal | None = None
    checks_passed: int
    checks_failed: int
    findings: dict = Field(default_factory=dict)

    # Denormalised for the history table.
    ulpin_code: str | None = None
    short_code: str | None = None


class VerificationListResponse(BaseModel):
    items: list[VerificationRecordResponse]
    total: int
    page: int
    page_size: int


class VerificationDecision(BaseModel):
    """An officer's manual override of an engine verdict.

    The engine is advisory. A field officer who has stood in the flat can close a
    PENDING_VERIFICATION that the engine could not resolve, and the record keeps
    both the engine's finding and the human's decision so the disagreement stays
    visible.
    """

    status: VerificationStatus = Field(description="PASSED, FAILED or WAIVED")
    outcome: VerificationOutcome
    remarks: Annotated[str, StringConstraints(min_length=5, max_length=1000)]
    reference_no: str | None = Field(default=None, max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
