"""Contracts for the rule-based fraud detection system.

Five rules, three alert levels. The severity attached to a rule is fixed rather
than computed, because an alert level that moves is an alert level nobody trusts:
an officer triaging a queue needs "HIGH means drop everything" to mean the same
thing on Tuesday as it did on Monday.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.enums import AlertSeverity, AlertStatus, FraudRuleCode

# The five rules from the specification, with the level each raises at.
#
# Severity is a judgement about *consequence*, not about confidence:
#   - MULTIPLE_OWNERS: two people hold sole title to one flat. Somebody will be
#     dispossessed. HIGH.
#   - DUPLICATE_ULPIN: two identifiers for one volume. Every downstream system
#     that keys on the identifier is now wrong. HIGH.
#   - OWNERSHIP_MISMATCH: a claim contradicts the register. Could be either side
#     being wrong, so it is a dispute to investigate, not a finding. MEDIUM.
#   - TENANT_MISMATCH: double-letting. Costly, rarely irreversible. MEDIUM.
#   - UNAUTHORIZED_OCCUPANCY: occupation with no recorded right. Often a
#     digitisation gap rather than a squatter, so it opens LOW and is escalated
#     by a human who has looked.
RULE_SEVERITY: dict[FraudRuleCode, AlertSeverity] = {
    FraudRuleCode.MULTIPLE_OWNERS: AlertSeverity.HIGH,
    FraudRuleCode.DUPLICATE_ULPIN: AlertSeverity.HIGH,
    FraudRuleCode.OWNERSHIP_MISMATCH: AlertSeverity.MEDIUM,
    FraudRuleCode.TENANT_MISMATCH: AlertSeverity.MEDIUM,
    FraudRuleCode.UNAUTHORIZED_OCCUPANCY: AlertSeverity.LOW,
}

RULE_TITLES: dict[FraudRuleCode, str] = {
    FraudRuleCode.MULTIPLE_OWNERS: "Multiple owners recorded for the same property",
    FraudRuleCode.DUPLICATE_ULPIN: "Duplicate ULPIN",
    FraudRuleCode.OWNERSHIP_MISMATCH: "Ownership mismatch",
    FraudRuleCode.TENANT_MISMATCH: "Tenant mismatch",
    FraudRuleCode.UNAUTHORIZED_OCCUPANCY: "Unauthorized occupancy",
}

# What each rule is for, in the words an officer would use. Rendered in the
# dashboard beside the count, so the queue explains itself.
RULE_DESCRIPTIONS: dict[FraudRuleCode, str] = {
    FraudRuleCode.MULTIPLE_OWNERS: (
        "More than one live sole-ownership record, or shares that do not total 1, "
        "against a single unit."
    ),
    FraudRuleCode.DUPLICATE_ULPIN: (
        "One unit carrying more than one live identifier, or one identifier "
        "pointing at more than one unit."
    ),
    FraudRuleCode.OWNERSHIP_MISMATCH: (
        "A verification run found the claimed owner absent from the title."
    ),
    FraudRuleCode.TENANT_MISMATCH: (
        "Concurrent active tenancies, or a claimed tenancy the register does not hold."
    ),
    FraudRuleCode.UNAUTHORIZED_OCCUPANCY: (
        "A unit recorded as occupied with neither an active lease nor a live "
        "ownership behind it."
    ),
}


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_id: uuid.UUID
    rule_code: FraudRuleCode
    alert_type: str | None = None
    severity: AlertSeverity
    status: AlertStatus

    title: str
    description: str | None = None

    unit_id: uuid.UUID | None = None
    building_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    ulpin_id: uuid.UUID | None = None

    risk_score: Decimal | None = None
    confidence: Decimal | None = None
    measured_value: Decimal | None = None
    threshold_value: Decimal | None = None
    evidence: dict = Field(default_factory=dict)

    detected_at: datetime
    assigned_to: uuid.UUID | None = None
    resolved_at: datetime | None = None
    resolved_by: uuid.UUID | None = None
    resolution_notes: str | None = None
    is_false_positive: bool | None = None

    # Denormalised for the alert table — an officer scanning the queue needs the
    # property, not a UUID to go and look up.
    short_code: str | None = None
    unit_number: str | None = None
    building_name: str | None = None
    floor_number: int | None = None


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int
    page: int
    page_size: int


class ScanRequest(BaseModel):
    """Scope for a detection run.

    Scoped to a building for the normal case — an officer has just registered 80
    units and wants them checked. Unscoped runs the whole register, which is a
    nightly job rather than an interactive one.
    """

    building_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    rules: list[FraudRuleCode] | None = Field(
        default=None, description="Subset of rules to run. Omit for all five."
    )
    dry_run: bool = Field(
        default=False, description="Report findings without opening or updating alerts"
    )


class RuleFinding(BaseModel):
    rule_code: FraudRuleCode
    severity: AlertSeverity
    title: str
    description: str
    unit_id: uuid.UUID | None = None
    building_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    ulpin_id: uuid.UUID | None = None
    evidence: dict = Field(default_factory=dict)
    measured_value: Decimal | None = Field(
        default=None, description="The number the rule tripped on, where it has one"
    )
    fingerprint: str
    alert_id: uuid.UUID | None = Field(
        default=None, description="Null on a dry run, or when the rule only re-touched an open alert"
    )
    was_existing: bool = False


class ScanResponse(BaseModel):
    dry_run: bool
    rules_run: list[FraudRuleCode]
    units_examined: int
    findings: list[RuleFinding]
    opened: int
    updated: int

    @property
    def total(self) -> int:
        return len(self.findings)


class AlertDecision(BaseModel):
    """Closing an alert.

    ``is_false_positive`` is separate from ``status`` on purpose. "Dismissed"
    covers both "this was noise" and "this was real and has been dealt with
    elsewhere", and only the first should count against a rule's precision.
    """

    status: AlertStatus
    resolution_notes: Annotated[str, StringConstraints(min_length=5, max_length=2000)]
    is_false_positive: bool = False


class AlertAssign(BaseModel):
    assigned_to: uuid.UUID


class RuleSummary(BaseModel):
    rule_code: FraudRuleCode
    title: str
    description: str
    severity: AlertSeverity
    open_count: int = 0
    total_count: int = 0


class FraudSummaryResponse(BaseModel):
    """The dashboard header for the fraud module."""

    open_total: int
    by_severity: dict[str, int]
    by_status: dict[str, int]
    rules: list[RuleSummary]
    newest: list[AlertResponse] = Field(default_factory=list)
