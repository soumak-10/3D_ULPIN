"""Fraud alert endpoints.

``POST /scan`` runs the rules; everything else is the officer's queue. The queue
routes are deliberately richer than the scan route, because detection is the easy
half — a rule that fires into a list nobody triages has detected nothing.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, Pagination, RequirePermission
from app.core.permissions import Permission
from app.models.enums import AlertSeverity, AlertStatus, FraudRuleCode
from app.models.user import User
from app.schemas.fraud import (
    AlertAssign,
    AlertDecision,
    AlertListResponse,
    AlertResponse,
    FraudSummaryResponse,
    ScanRequest,
    ScanResponse,
)
from app.services.fraud_service import FraudService

router = APIRouter(prefix="/fraud", tags=["Fraud Detection"])

CanRead = Annotated[User, Depends(RequirePermission(Permission.FRAUD_READ))]
CanScan = Annotated[User, Depends(RequirePermission(Permission.FRAUD_CREATE))]
CanResolve = Annotated[User, Depends(RequirePermission(Permission.FRAUD_RESOLVE))]


@router.post(
    "/scan",
    response_model=ScanResponse,
    status_code=status.HTTP_200_OK,
    summary="Run the five fraud rules",
    description=(
        "Rules: multiple owners for the same property, duplicate ULPIN, ownership "
        "mismatch, tenant mismatch, unauthorized occupancy.\n\n"
        "Scope with `building_id` for the interactive case — an officer has just "
        "registered a tower and wants it checked. Unscoped runs the whole "
        "register and belongs on a schedule.\n\n"
        "**Safe to re-run.** Every finding carries a fingerprint of "
        "rule + subject, and an alert already open for that pair is updated in "
        "place rather than duplicated, so a nightly sweep over an unchanged "
        "register opens nothing. `dry_run=true` reports without writing."
    ),
)
async def scan(payload: ScanRequest, session: DbSession, actor: CanScan) -> ScanResponse:
    return await FraudService(session).scan(payload, actor=actor)


@router.get(
    "/summary",
    response_model=FraudSummaryResponse,
    summary="Queue header: open totals by severity, status and rule",
)
async def summary(session: DbSession, actor: CanRead) -> FraudSummaryResponse:
    return await FraudService(session).summary(actor=actor)


@router.get(
    "/trend",
    summary="Alerts opened per day, split by severity",
    description="Feeds the fraud trend chart on the admin dashboard.",
)
async def trend(
    session: DbSession,
    actor: CanRead,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[dict]:
    return await FraudService(session).trend(days, actor=actor)


@router.get(
    "/alerts",
    response_model=AlertListResponse,
    summary="The alert queue",
    description=(
        "Ordered by severity, then recency — a HIGH alert from Monday outranks a "
        "LOW one from this morning. Pass `open_only=true` for the working queue."
    ),
)
async def list_alerts(
    session: DbSession,
    actor: CanRead,
    pagination: Pagination,
    severity: Annotated[AlertSeverity | None, Query()] = None,
    alert_status: Annotated[AlertStatus | None, Query()] = None,
    rule_code: Annotated[FraudRuleCode | None, Query()] = None,
    building_id: Annotated[uuid.UUID | None, Query()] = None,
    unit_id: Annotated[uuid.UUID | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
) -> AlertListResponse:
    items, total = await FraudService(session).list_alerts(
        actor=actor,
        page=pagination.page,
        page_size=pagination.page_size,
        severity=severity,
        alert_status=alert_status,
        rule_code=rule_code,
        building_id=building_id,
        unit_id=unit_id,
        open_only=open_only,
    )
    return AlertListResponse(
        items=items, total=total, page=pagination.page, page_size=pagination.page_size
    )


@router.get("/alerts/{alert_id}", response_model=AlertResponse, summary="One alert")
async def get_alert(alert_id: uuid.UUID, session: DbSession, actor: CanRead) -> AlertResponse:
    return await FraudService(session).get_alert(alert_id, actor=actor)


@router.post(
    "/alerts/{alert_id}/assign",
    response_model=AlertResponse,
    summary="Assign an alert to an officer",
    description="Moves an OPEN alert to INVESTIGATING so it stops appearing unowned.",
)
async def assign_alert(
    alert_id: uuid.UUID,
    payload: AlertAssign,
    session: DbSession,
    actor: CanResolve,
) -> AlertResponse:
    return await FraudService(session).assign(alert_id, payload, actor=actor)


@router.post(
    "/alerts/{alert_id}/decide",
    response_model=AlertResponse,
    summary="Close an alert",
    description=(
        "`is_false_positive` is recorded separately from the status, because "
        "\"dismissed\" covers both \"this was noise\" and \"this was real and "
        "handled elsewhere\", and only the first should count against a rule's "
        "precision."
    ),
)
async def decide_alert(
    alert_id: uuid.UUID,
    payload: AlertDecision,
    session: DbSession,
    actor: CanResolve,
) -> AlertResponse:
    return await FraudService(session).decide(alert_id, payload, actor=actor)
