"""Data access for the fraud rules.

Each rule is one set-based query returning offending subjects, not a Python loop
over units. A register with two million units cannot be walked row by row on a
nightly job, and a rule expressed as SQL is a rule a DBA can read and argue with.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    FraudRuleCode,
    OccupancyStatus,
    OwnershipMode,
    TenancyStatus,
    VerificationOutcome,
)
from app.models.fraud import FraudAlert
from app.models.property import Building, Floor, Tenant, Unit, UnitOwnership
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin

OPEN_STATUSES = (AlertStatus.OPEN, AlertStatus.INVESTIGATING)


class FraudRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =======================================================================
    # Scope
    # =======================================================================
    def _scope(self, stmt: Select, building_id, unit_id, unit_col=Unit.unit_id) -> Select:
        if unit_id is not None:
            stmt = stmt.where(unit_col == unit_id)
        if building_id is not None:
            stmt = stmt.where(Unit.building_id == building_id)
        return stmt

    async def count_units(self, building_id=None, unit_id=None) -> int:
        stmt = select(func.count()).select_from(Unit).where(Unit.deleted_at.is_(None))
        if unit_id is not None:
            stmt = stmt.where(Unit.unit_id == unit_id)
        if building_id is not None:
            stmt = stmt.where(Unit.building_id == building_id)
        return int((await self.session.execute(stmt)).scalar_one())

    # =======================================================================
    # Rule 1 — Multiple owners for the same property
    # =======================================================================
    async def rule_multiple_owners(self, building_id=None, unit_id=None) -> list[dict]:
        """Two live sole-ownership rows, or shares that do not total 1.

        A joint holding is legitimate and common — two names on one flat is not a
        fraud signal. What *is* one is two records each asserting to be the whole
        title (``SOLE``), or a set of shares that leaves part of the title
        unaccounted for.
        """
        sole_count = func.count(
            func.nullif(UnitOwnership.ownership_mode != OwnershipMode.SOLE, True)
        )
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.building_id,
                Building.building_name,
                func.count(UnitOwnership.ownership_id).label("owner_count"),
                sole_count.label("sole_count"),
                func.coalesce(func.sum(UnitOwnership.share_fraction), 0).label("share_total"),
                func.array_agg(UnitOwnership.owner_id).label("owner_ids"),
            )
            .join(UnitOwnership, UnitOwnership.unit_id == Unit.unit_id)
            .join(Building, Building.building_id == Unit.building_id)
            .where(Unit.deleted_at.is_(None), UnitOwnership.ended_on.is_(None))
            .group_by(Unit.unit_id, Unit.unit_number, Unit.building_id, Building.building_name)
            .having(
                or_(
                    sole_count > 1,
                    func.abs(func.coalesce(func.sum(UnitOwnership.share_fraction), 0) - 1) > 1e-6,
                )
            )
        )
        stmt = self._scope(stmt, building_id, unit_id)
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    # =======================================================================
    # Rule 2 — Duplicate ULPIN
    # =======================================================================
    async def rule_duplicate_ulpin(self, building_id=None, unit_id=None) -> list[dict]:
        """One unit carrying more than one live identifier.

        The reverse direction — one code on two units — is made unrepresentable
        by the unique indexes on ``ulpin_code`` and ``short_code``, so it cannot
        be detected here and does not need to be. This catches the case the
        indexes permit: a unit that was coded twice, e.g. by a re-run of a bulk
        import against a partially-committed batch.
        """
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.building_id,
                Building.building_name,
                func.count(Ulpin.ulpin_id).label("code_count"),
                func.array_agg(Ulpin.short_code).label("codes"),
                func.min(Ulpin.ulpin_id).label("first_ulpin_id"),
            )
            .join(Ulpin, Ulpin.unit_id == Unit.unit_id)
            .join(Building, Building.building_id == Unit.building_id)
            .where(
                Unit.deleted_at.is_(None),
                Ulpin.status.in_(LIVE_ULPIN_STATUSES),
            )
            .group_by(Unit.unit_id, Unit.unit_number, Unit.building_id, Building.building_name)
            .having(func.count(Ulpin.ulpin_id) > 1)
        )
        stmt = self._scope(stmt, building_id, unit_id)
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    # =======================================================================
    # Rule 3 — Ownership mismatch
    # =======================================================================
    async def rule_ownership_mismatch(self, building_id=None, unit_id=None) -> list[dict]:
        """Units the verification engine found to be contradicted claims.

        Reads the engine's own verdict rather than re-deriving it. The engine is
        the single place that knows how a claim is compared to a title; a second
        implementation here would drift from it within a release.
        """
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.building_id,
                Building.building_name,
                Unit.last_verified_at,
                Ulpin.ulpin_id,
                Ulpin.short_code,
            )
            .join(Building, Building.building_id == Unit.building_id)
            .outerjoin(
                Ulpin,
                and_(Ulpin.unit_id == Unit.unit_id, Ulpin.status.in_(LIVE_ULPIN_STATUSES)),
            )
            .where(
                Unit.deleted_at.is_(None),
                Unit.verification_outcome == VerificationOutcome.INVALID_CLAIM,
            )
        )
        stmt = self._scope(stmt, building_id, unit_id)
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    # =======================================================================
    # Rule 4 — Tenant mismatch
    # =======================================================================
    async def rule_tenant_mismatch(self, building_id=None, unit_id=None) -> list[dict]:
        """Concurrent active tenancies against one unit — double-letting.

        The table has an exclusion constraint against overlapping lease *dates*;
        this finds the pairs it cannot see, where both leases are open-ended.
        """
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.building_id,
                Building.building_name,
                func.count(Tenant.tenant_id).label("tenancy_count"),
                func.array_agg(Tenant.full_name).label("tenant_names"),
                func.min(Tenant.tenant_id).label("first_tenant_id"),
            )
            .join(Tenant, Tenant.unit_id == Unit.unit_id)
            .join(Building, Building.building_id == Unit.building_id)
            .where(
                Unit.deleted_at.is_(None),
                Tenant.deleted_at.is_(None),
                Tenant.status == TenancyStatus.ACTIVE,
            )
            .group_by(Unit.unit_id, Unit.unit_number, Unit.building_id, Building.building_name)
            .having(func.count(Tenant.tenant_id) > 1)
        )
        stmt = self._scope(stmt, building_id, unit_id)
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    # =======================================================================
    # Rule 5 — Unauthorized occupancy
    # =======================================================================
    async def rule_unauthorized_occupancy(self, building_id=None, unit_id=None) -> list[dict]:
        """Occupied, with neither a live lease nor a live ownership behind it.

        LEFT JOIN with a null test rather than ``NOT IN`` on the two subqueries:
        ``unit_id`` is nullable on both tenants and ownerships, and ``NOT IN``
        against a set containing a single NULL returns no rows at all — the rule
        would silently never fire.
        """
        live_tenancy = (
            select(Tenant.unit_id)
            .where(Tenant.status == TenancyStatus.ACTIVE, Tenant.deleted_at.is_(None))
            .subquery()
        )
        live_owner = (
            select(UnitOwnership.unit_id).where(UnitOwnership.ended_on.is_(None)).subquery()
        )
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.building_id,
                Building.building_name,
                Unit.occupancy_status,
            )
            .join(Building, Building.building_id == Unit.building_id)
            .outerjoin(live_tenancy, live_tenancy.c.unit_id == Unit.unit_id)
            .outerjoin(live_owner, live_owner.c.unit_id == Unit.unit_id)
            .where(
                Unit.deleted_at.is_(None),
                Unit.occupancy_status == OccupancyStatus.OCCUPIED,
                live_tenancy.c.unit_id.is_(None),
                live_owner.c.unit_id.is_(None),
            )
        )
        stmt = self._scope(stmt, building_id, unit_id)
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    # =======================================================================
    # Alert persistence
    # =======================================================================
    async def upsert_alert(self, values: dict) -> tuple[uuid.UUID, bool]:
        """Open an alert, or re-touch the open one that already says this.

        Re-running the engine must not clone every finding. The partial unique
        index on ``(fingerprint)`` over open statuses makes the second run an
        UPDATE, so an alert an officer is already working keeps its assignment
        and its notes and merely gets fresher evidence.

        Returns ``(alert_id, was_existing)``.
        """
        stmt = (
            pg_insert(FraudAlert)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[FraudAlert.fingerprint],
                index_where=FraudAlert.status.in_(OPEN_STATUSES),
                set_={
                    "evidence": values.get("evidence", {}),
                    "detected_at": func.now(),
                    "severity": values["severity"],
                    "description": values.get("description"),
                    "measured_value": values.get("measured_value"),
                    "updated_at": func.now(),
                },
            )
            .returning(FraudAlert.__table__.c.alert_id, FraudAlert.__table__.c.created_at)
        )
        row = (await self.session.execute(stmt)).one()
        # created_at untouched by the UPDATE branch, so a row whose id we were
        # given but whose id we did not generate is one that already existed.
        return row.alert_id, row.alert_id != values.get("alert_id")

    async def get(self, alert_id: uuid.UUID) -> FraudAlert | None:
        return await self.session.get(FraudAlert, alert_id)

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        severity: AlertSeverity | None = None,
        alert_status: AlertStatus | None = None,
        rule_code: FraudRuleCode | None = None,
        building_id: uuid.UUID | None = None,
        unit_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> tuple[list[FraudAlert], int]:
        conditions = []
        if severity is not None:
            conditions.append(FraudAlert.severity == severity)
        if alert_status is not None:
            conditions.append(FraudAlert.status == alert_status)
        if rule_code is not None:
            conditions.append(FraudAlert.rule_code == rule_code)
        if building_id is not None:
            conditions.append(FraudAlert.building_id == building_id)
        if unit_id is not None:
            conditions.append(FraudAlert.unit_id == unit_id)
        if open_only:
            conditions.append(FraudAlert.status.in_(OPEN_STATUSES))

        total = (
            await self.session.execute(
                select(func.count()).select_from(FraudAlert).where(*conditions)
            )
        ).scalar_one()

        # Severity first, then recency: a HIGH alert from Monday outranks a LOW
        # one from this morning, and the queue is read top-down. The ordinal is
        # spelled out as a CASE rather than relying on the enum's storage order,
        # which is an implementation detail of the type.
        severity_rank = case(
            (FraudAlert.severity == AlertSeverity.CRITICAL, 0),
            (FraudAlert.severity == AlertSeverity.HIGH, 1),
            (FraudAlert.severity == AlertSeverity.MEDIUM, 2),
            (FraudAlert.severity == AlertSeverity.LOW, 3),
            else_=4,
        )
        stmt = (
            select(FraudAlert)
            .where(*conditions)
            .order_by(severity_rank, FraudAlert.detected_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), int(total)

    async def recent(self, limit: int = 10) -> list[FraudAlert]:
        stmt = (
            select(FraudAlert)
            .where(FraudAlert.status.in_(OPEN_STATUSES))
            .order_by(FraudAlert.detected_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def counts_by(self, column) -> dict[str, int]:
        stmt = select(column, func.count()).group_by(column)
        rows = (await self.session.execute(stmt)).all()
        return {(k.value if hasattr(k, "value") else str(k)): int(v) for k, v in rows if k}

    async def open_counts_by_rule(self) -> dict[str, int]:
        stmt = (
            select(FraudAlert.rule_code, func.count())
            .where(FraudAlert.status.in_(OPEN_STATUSES))
            .group_by(FraudAlert.rule_code)
        )
        rows = (await self.session.execute(stmt)).all()
        # rule_code is stored as text, not as a native enum, so a grouped SELECT
        # hands back plain strings and `.value` would raise AttributeError.
        # Tolerate both shapes, as counts_by() above already does.
        return {(k.value if hasattr(k, "value") else str(k)): int(v) for k, v in rows if k}

    async def total_counts_by_rule(self) -> dict[str, int]:
        stmt = select(FraudAlert.rule_code, func.count()).group_by(FraudAlert.rule_code)
        rows = (await self.session.execute(stmt)).all()
        return {(k.value if hasattr(k, "value") else str(k)): int(v) for k, v in rows if k}

    async def open_total(self) -> int:
        stmt = (
            select(func.count())
            .select_from(FraudAlert)
            .where(FraudAlert.status.in_(OPEN_STATUSES))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def trend(self, days: int = 30) -> list[dict]:
        """Alerts opened per day, for the fraud trend chart.

        ``date_trunc`` in SQL rather than bucketing in Python: the series is
        drawn for a fixed window and the database can answer it from the index on
        ``detected_at``.
        """
        bucket = func.date_trunc("day", FraudAlert.detected_at).label("day")
        stmt = (
            select(bucket, FraudAlert.severity, func.count().label("count"))
            .where(FraudAlert.detected_at >= func.now() - func.make_interval(0, 0, 0, days))
            .group_by(bucket, FraudAlert.severity)
            .order_by(bucket)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "day": r.day.date().isoformat(),
                "severity": r.severity.value if r.severity else None,
                "count": int(r.count),
            }
            for r in rows
        ]

    async def context_for_units(self, unit_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        """Unit number, floor and building name for a page of alerts, in one query."""
        if not unit_ids:
            return {}
        stmt = (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Floor.floor_number,
                Building.building_name,
                Ulpin.short_code,
            )
            .join(Floor, Floor.floor_id == Unit.floor_id)
            .join(Building, Building.building_id == Unit.building_id)
            .outerjoin(
                Ulpin,
                and_(Ulpin.unit_id == Unit.unit_id, Ulpin.status.in_(LIVE_ULPIN_STATUSES)),
            )
            .where(Unit.unit_id.in_(unit_ids))
        )
        return {
            r.unit_id: {
                "unit_number": r.unit_number,
                "floor_number": r.floor_number,
                "building_name": r.building_name,
                "short_code": r.short_code,
            }
            for r in (await self.session.execute(stmt)).all()
        }

    def add(self, alert: FraudAlert) -> FraudAlert:
        self.session.add(alert)
        return alert
