"""The rule-based fraud detection engine.

Five rules, each a single query, each producing findings with a stable
fingerprint. The fingerprint is what makes the engine safe to run on a schedule:
a nightly sweep over a register that has not changed opens no new alerts, it
re-touches the ones already open. Without that, an officer arrives on Monday to
forty copies of the same finding and stops reading the queue — which is the real
failure mode of automated detection, not missing a case.

Nothing here decides that fraud *occurred*. A rule finds a contradiction in the
register; a human decides what it means. The vocabulary throughout is "alert",
never "fraud found", because the same contradiction is produced by a data-entry
error and by a forgery, and the system cannot tell them apart.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.core.permissions import Permission, has_permission
from app.models.enums import AlertSeverity, AlertStatus, FraudRuleCode
from app.models.fraud import FraudAlert
from app.models.user import User
from app.repositories.fraud_repository import FraudRepository
from app.schemas.fraud import (
    RULE_DESCRIPTIONS,
    RULE_SEVERITY,
    RULE_TITLES,
    AlertAssign,
    AlertDecision,
    AlertResponse,
    FraudSummaryResponse,
    RuleFinding,
    RuleSummary,
    ScanRequest,
    ScanResponse,
)

ALL_RULES: tuple[FraudRuleCode, ...] = (
    FraudRuleCode.MULTIPLE_OWNERS,
    FraudRuleCode.DUPLICATE_ULPIN,
    FraudRuleCode.OWNERSHIP_MISMATCH,
    FraudRuleCode.TENANT_MISMATCH,
    FraudRuleCode.UNAUTHORIZED_OCCUPANCY,
)


def fingerprint(rule: FraudRuleCode, subject: str) -> str:
    """Stable identity for "this rule, about this thing".

    Hashed rather than concatenated so the column is fixed-width and indexable,
    and so a subject that is a list of UUIDs does not blow the column length.
    """
    return hashlib.sha256(f"{rule.value}:{subject}".encode()).hexdigest()[:40]


class FraudService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FraudRepository(session)

    # =======================================================================
    # Detection
    # =======================================================================
    async def scan(self, payload: ScanRequest, *, actor: User) -> ScanResponse:
        if not has_permission(actor.role, Permission.FRAUD_CREATE):
            raise PermissionDeniedException("This action requires fraud:create")

        rules = tuple(payload.rules) if payload.rules else ALL_RULES
        scope = {"building_id": payload.building_id, "unit_id": payload.unit_id}

        findings: list[RuleFinding] = []
        for rule in rules:
            findings.extend(await self._run_rule(rule, scope))

        opened = updated = 0
        if not payload.dry_run:
            for finding in findings:
                alert_id, existed = await self._raise(finding, actor=actor)
                finding.alert_id = alert_id
                finding.was_existing = existed
                if existed:
                    updated += 1
                else:
                    opened += 1
            await self.session.flush()

        return ScanResponse(
            dry_run=payload.dry_run,
            rules_run=list(rules),
            units_examined=await self.repo.count_units(**scope),
            findings=findings,
            opened=opened,
            updated=updated,
        )

    async def _run_rule(self, rule: FraudRuleCode, scope: dict) -> list[RuleFinding]:
        handler = {
            FraudRuleCode.MULTIPLE_OWNERS: self._rule_multiple_owners,
            FraudRuleCode.DUPLICATE_ULPIN: self._rule_duplicate_ulpin,
            FraudRuleCode.OWNERSHIP_MISMATCH: self._rule_ownership_mismatch,
            FraudRuleCode.TENANT_MISMATCH: self._rule_tenant_mismatch,
            FraudRuleCode.UNAUTHORIZED_OCCUPANCY: self._rule_unauthorized_occupancy,
        }[rule]
        return await handler(scope)

    # -- Rule 1 --------------------------------------------------------------
    async def _rule_multiple_owners(self, scope: dict) -> list[RuleFinding]:
        rule = FraudRuleCode.MULTIPLE_OWNERS
        out = []
        for row in await self.repo.rule_multiple_owners(**scope):
            share_total = float(row["share_total"])
            sole = int(row["sole_count"] or 0)
            if sole > 1:
                detail = (
                    f"{sole} separate sole-ownership records are live against unit "
                    f"{row['unit_number']}. Each asserts the whole title; they cannot "
                    "all be correct."
                )
            else:
                detail = (
                    f"Ownership shares on unit {row['unit_number']} total "
                    f"{share_total:.4f} rather than 1. "
                    + (
                        "More of the title has been allocated than exists."
                        if share_total > 1
                        else "Part of the title is unaccounted for."
                    )
                )
            out.append(
                self._finding(
                    rule,
                    row,
                    detail,
                    evidence={
                        "owner_count": int(row["owner_count"]),
                        "sole_count": sole,
                        "share_total": round(share_total, 6),
                        "owner_ids": [str(o) for o in (row["owner_ids"] or []) if o],
                    },
                    measured_value=Decimal(str(round(share_total, 6))),
                )
            )
        return out

    # -- Rule 2 --------------------------------------------------------------
    async def _rule_duplicate_ulpin(self, scope: dict) -> list[RuleFinding]:
        rule = FraudRuleCode.DUPLICATE_ULPIN
        out = []
        for row in await self.repo.rule_duplicate_ulpin(**scope):
            codes = [c for c in (row["codes"] or []) if c]
            out.append(
                self._finding(
                    rule,
                    row,
                    (
                        f"Unit {row['unit_number']} carries {row['code_count']} live "
                        f"identifiers ({', '.join(codes)}). Exactly one can be current; "
                        "the others must be superseded or retired."
                    ),
                    evidence={"code_count": int(row["code_count"]), "codes": codes},
                    ulpin_id=row.get("first_ulpin_id"),
                    measured_value=Decimal(int(row["code_count"])),
                )
            )
        return out

    # -- Rule 3 --------------------------------------------------------------
    async def _rule_ownership_mismatch(self, scope: dict) -> list[RuleFinding]:
        rule = FraudRuleCode.OWNERSHIP_MISMATCH
        out = []
        for row in await self.repo.rule_ownership_mismatch(**scope):
            out.append(
                self._finding(
                    rule,
                    row,
                    (
                        f"The last verification of unit {row['unit_number']} found the "
                        "claimed owner absent from the title."
                    ),
                    evidence={
                        "last_verified_at": (
                            row["last_verified_at"].isoformat()
                            if row.get("last_verified_at")
                            else None
                        ),
                        "short_code": row.get("short_code"),
                    },
                    ulpin_id=row.get("ulpin_id"),
                )
            )
        return out

    # -- Rule 4 --------------------------------------------------------------
    async def _rule_tenant_mismatch(self, scope: dict) -> list[RuleFinding]:
        rule = FraudRuleCode.TENANT_MISMATCH
        out = []
        for row in await self.repo.rule_tenant_mismatch(**scope):
            names = [n for n in (row["tenant_names"] or []) if n]
            out.append(
                self._finding(
                    rule,
                    row,
                    (
                        f"{row['tenancy_count']} tenancies are active at once on unit "
                        f"{row['unit_number']} ({', '.join(names)}). At most one lease "
                        "can be live at a time."
                    ),
                    evidence={"tenancy_count": int(row["tenancy_count"]), "tenants": names},
                    tenant_id=row.get("first_tenant_id"),
                    measured_value=Decimal(int(row["tenancy_count"])),
                )
            )
        return out

    # -- Rule 5 --------------------------------------------------------------
    async def _rule_unauthorized_occupancy(self, scope: dict) -> list[RuleFinding]:
        rule = FraudRuleCode.UNAUTHORIZED_OCCUPANCY
        out = []
        for row in await self.repo.rule_unauthorized_occupancy(**scope):
            out.append(
                self._finding(
                    rule,
                    row,
                    (
                        f"Unit {row['unit_number']} is recorded as occupied but has "
                        "neither an active tenancy nor a live ownership. No party of "
                        "record holds a right to occupy it. This is frequently a "
                        "digitisation gap rather than a trespass — check the file "
                        "before escalating."
                    ),
                    evidence={"occupancy_status": "OCCUPIED"},
                )
            )
        return out

    # =======================================================================
    # Finding -> alert
    # =======================================================================
    def _finding(
        self,
        rule: FraudRuleCode,
        row: dict,
        description: str,
        *,
        evidence: dict,
        ulpin_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        measured_value: Decimal | None = None,
    ) -> RuleFinding:
        unit_id = row.get("unit_id")
        return RuleFinding(
            rule_code=rule,
            severity=RULE_SEVERITY[rule],
            title=f"{RULE_TITLES[rule]} — {row.get('building_name') or 'unit'} "
            f"{row.get('unit_number') or ''}".strip(),
            description=description,
            unit_id=unit_id,
            building_id=row.get("building_id"),
            owner_id=owner_id,
            tenant_id=tenant_id,
            ulpin_id=ulpin_id,
            evidence=evidence,
            measured_value=measured_value,
            fingerprint=fingerprint(rule, str(unit_id)),
        )

    async def _raise(self, finding: RuleFinding, *, actor: User) -> tuple[uuid.UUID, bool]:
        new_id = uuid.uuid4()
        values = {
            "alert_id": new_id,
            "rule_code": finding.rule_code,
            "alert_type": finding.rule_code.value,
            "severity": finding.severity,
            "status": AlertStatus.OPEN,
            "title": finding.title[:200],
            "description": finding.description,
            "unit_id": finding.unit_id,
            "building_id": finding.building_id,
            "owner_id": finding.owner_id,
            "tenant_id": finding.tenant_id,
            "ulpin_id": finding.ulpin_id,
            "evidence": finding.evidence,
            "fingerprint": finding.fingerprint,
            "detected_by": actor.user_id,
            "detected_at": datetime.now(UTC),
            "risk_score": self._risk_score(finding.severity),
            "confidence": Decimal("0.8"),
            "measured_value": finding.measured_value,
        }
        alert_id, _ = await self.repo.upsert_alert(values)
        return alert_id, alert_id != new_id

    @staticmethod
    def _risk_score(severity: AlertSeverity) -> Decimal:
        return {
            AlertSeverity.LOW: Decimal("25"),
            AlertSeverity.MEDIUM: Decimal("55"),
            AlertSeverity.HIGH: Decimal("80"),
            AlertSeverity.CRITICAL: Decimal("95"),
        }[severity]

    # =======================================================================
    # Queue
    # =======================================================================
    async def list_alerts(self, *, actor: User, page: int, page_size: int, **filters):
        if not has_permission(actor.role, Permission.FRAUD_READ):
            raise PermissionDeniedException("This action requires fraud:read")
        rows, total = await self.repo.search(page=page, page_size=page_size, **filters)
        return await self._decorate(rows), total

    async def get_alert(self, alert_id: uuid.UUID, *, actor: User) -> AlertResponse:
        if not has_permission(actor.role, Permission.FRAUD_READ):
            raise PermissionDeniedException("This action requires fraud:read")
        alert = await self.repo.get(alert_id)
        if alert is None:
            raise NotFoundException("Fraud alert", alert_id)
        return (await self._decorate([alert]))[0]

    async def decide(
        self, alert_id: uuid.UUID, payload: AlertDecision, *, actor: User
    ) -> AlertResponse:
        if not has_permission(actor.role, Permission.FRAUD_RESOLVE):
            raise PermissionDeniedException("This action requires fraud:resolve")
        alert = await self.repo.get(alert_id)
        if alert is None:
            raise NotFoundException("Fraud alert", alert_id)

        alert.status = payload.status
        alert.resolution_notes = payload.resolution_notes
        alert.is_false_positive = payload.is_false_positive
        if payload.status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED, AlertStatus.CONFIRMED):
            alert.resolved_at = datetime.now(UTC)
            alert.resolved_by = actor.user_id
        await self.session.flush()
        return (await self._decorate([alert]))[0]

    async def assign(
        self, alert_id: uuid.UUID, payload: AlertAssign, *, actor: User
    ) -> AlertResponse:
        if not has_permission(actor.role, Permission.FRAUD_RESOLVE):
            raise PermissionDeniedException("This action requires fraud:resolve")
        alert = await self.repo.get(alert_id)
        if alert is None:
            raise NotFoundException("Fraud alert", alert_id)
        alert.assigned_to = payload.assigned_to
        alert.assigned_at = datetime.now(UTC)
        if alert.status == AlertStatus.OPEN:
            alert.status = AlertStatus.INVESTIGATING
        await self.session.flush()
        return (await self._decorate([alert]))[0]

    async def summary(self, *, actor: User) -> FraudSummaryResponse:
        if not has_permission(actor.role, Permission.FRAUD_READ):
            raise PermissionDeniedException("This action requires fraud:read")
        open_by_rule = await self.repo.open_counts_by_rule()
        total_by_rule = await self.repo.total_counts_by_rule()
        return FraudSummaryResponse(
            open_total=await self.repo.open_total(),
            by_severity=await self.repo.counts_by(FraudAlert.severity),
            by_status=await self.repo.counts_by(FraudAlert.status),
            rules=[
                RuleSummary(
                    rule_code=rule,
                    title=RULE_TITLES[rule],
                    description=RULE_DESCRIPTIONS[rule],
                    severity=RULE_SEVERITY[rule],
                    open_count=open_by_rule.get(rule.value, 0),
                    total_count=total_by_rule.get(rule.value, 0),
                )
                for rule in ALL_RULES
            ],
            newest=await self._decorate(await self.repo.recent(8)),
        )

    async def trend(self, days: int, *, actor: User) -> list[dict]:
        if not has_permission(actor.role, Permission.FRAUD_READ):
            raise PermissionDeniedException("This action requires fraud:read")
        return await self.repo.trend(days)

    # =======================================================================
    # Internals
    # =======================================================================
    async def _decorate(self, alerts: list[FraudAlert]) -> list[AlertResponse]:
        """Attach the property context in one query for the whole page.

        The alert table shows a building and flat number on every row. Resolving
        those per row is a query per row on a page of fifty.
        """
        responses = [AlertResponse.model_validate(a) for a in alerts]
        unit_ids = [a.unit_id for a in alerts if a.unit_id]
        context = await self.repo.context_for_units(unit_ids)
        for response in responses:
            ctx = context.get(response.unit_id) if response.unit_id else None
            if ctx:
                response.unit_number = ctx["unit_number"]
                response.floor_number = ctx["floor_number"]
                response.building_name = ctx["building_name"]
                response.short_code = ctx["short_code"]
        return responses
