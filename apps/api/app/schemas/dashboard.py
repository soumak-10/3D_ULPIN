"""Contracts for the admin dashboard.

Seven stat cards, four charts, three tables — assembled into one response rather
than eleven endpoints. A dashboard that fires eleven requests renders eleven
times, and the officer watches numbers appear one at a time for two seconds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AlertSeverity, AlertStatus, FraudRuleCode, VerificationOutcome


class StatCard(BaseModel):
    """One headline number.

    ``delta`` is against the same window a period earlier, and is null rather
    than zero when there is no prior period to compare — a fresh deployment
    showing "0% change" implies a comparison it has not made.
    """

    key: str
    label: str
    value: int
    delta: float | None = None
    delta_label: str | None = None
    hint: str | None = None


class SeriesPoint(BaseModel):
    label: str
    value: int
    key: str | None = None


class TrendPoint(BaseModel):
    """A day on a time axis. Extra series ride along as named keys."""

    date: str
    total: int = 0
    low: int = 0
    medium: int = 0
    high: int = 0
    critical: int = 0


class OccupancyPoint(BaseModel):
    date: str
    occupied: int = 0
    vacant: int = 0
    occupancy_rate: float = 0.0


class ActivityRow(BaseModel):
    log_id: uuid.UUID | None = None
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    ulpin_code: str | None = None
    actor_role: str | None = None
    actor_user_id: uuid.UUID | None = None
    success: bool = True
    created_at: datetime


class VerificationRow(BaseModel):
    verification_id: uuid.UUID
    short_code: str | None = None
    unit_number: str | None = None
    building_name: str | None = None
    outcome: VerificationOutcome | None = None
    verified_at: datetime | None = None
    requested_at: datetime


class AlertRow(BaseModel):
    alert_id: uuid.UUID
    rule_code: FraudRuleCode
    severity: AlertSeverity
    status: AlertStatus
    title: str
    unit_number: str | None = None
    building_name: str | None = None
    short_code: str | None = None
    detected_at: datetime


class DashboardResponse(BaseModel):
    generated_at: datetime

    # -- Statistics cards ---------------------------------------------------
    cards: list[StatCard] = Field(default_factory=list)

    # -- Charts -------------------------------------------------------------
    property_distribution: list[SeriesPoint] = Field(
        default_factory=list, description="Units by type: residential, commercial, industrial, …"
    )
    verification_status: list[SeriesPoint] = Field(
        default_factory=list, description="Units by verification outcome"
    )
    fraud_trend: list[TrendPoint] = Field(
        default_factory=list, description="Alerts opened per day, split by severity"
    )
    occupancy_trend: list[OccupancyPoint] = Field(
        default_factory=list, description="Occupied vs vacant over time"
    )

    # -- Tables -------------------------------------------------------------
    recent_activities: list[ActivityRow] = Field(default_factory=list)
    recent_verifications: list[VerificationRow] = Field(default_factory=list)
    fraud_alerts: list[AlertRow] = Field(default_factory=list)
