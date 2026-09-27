"""Dashboard aggregates.

Every method here is a single grouped query. The temptation on a dashboard is to
load the rows and count them in Python — it reads more clearly and it works fine
on the demo dataset, then takes ninety seconds on a real one. Counting belongs
where the index is.
"""

from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.enums import (
    AlertStatus,
    OccupancyStatus,
    TenancyStatus,
    VerificationOutcome,
)
from app.models.fraud import FraudAlert
from app.models.property import Building, Floor, Tenant, Unit
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin
from app.models.verification import VerificationRecord


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =======================================================================
    # Cards
    # =======================================================================
    async def _count(self, stmt) -> int:
        return int((await self.session.execute(stmt)).scalar_one())

    async def total_buildings(self) -> int:
        return await self._count(
            select(func.count()).select_from(Building).where(Building.deleted_at.is_(None))
        )

    async def total_units(self) -> int:
        return await self._count(
            select(func.count()).select_from(Unit).where(Unit.deleted_at.is_(None))
        )

    async def total_ulpins(self) -> int:
        return await self._count(
            select(func.count())
            .select_from(Ulpin)
            .where(Ulpin.status.in_(LIVE_ULPIN_STATUSES))
        )

    async def units_by_outcome(self, outcome: VerificationOutcome) -> int:
        return await self._count(
            select(func.count())
            .select_from(Unit)
            .where(Unit.deleted_at.is_(None), Unit.verification_outcome == outcome)
        )

    async def open_alerts(self) -> int:
        return await self._count(
            select(func.count())
            .select_from(FraudAlert)
            .where(FraudAlert.status.in_((AlertStatus.OPEN, AlertStatus.INVESTIGATING)))
        )

    async def active_tenants(self) -> int:
        return await self._count(
            select(func.count())
            .select_from(Tenant)
            .where(Tenant.deleted_at.is_(None), Tenant.status == TenancyStatus.ACTIVE)
        )

    async def created_since(self, model, column, days: int) -> int:
        """Rows created in the last N days, for the card deltas."""
        return await self._count(
            select(func.count())
            .select_from(model)
            .where(column >= func.now() - func.make_interval(0, 0, 0, days))
        )

    # =======================================================================
    # Charts
    # =======================================================================
    async def property_distribution(self) -> list[tuple[str, int]]:
        stmt = (
            select(Unit.unit_type, func.count())
            .where(Unit.deleted_at.is_(None))
            .group_by(Unit.unit_type)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [(k.value if k else "UNSPECIFIED", int(v)) for k, v in rows]

    async def verification_distribution(self) -> list[tuple[str, int]]:
        stmt = (
            select(Unit.verification_outcome, func.count())
            .where(Unit.deleted_at.is_(None))
            .group_by(Unit.verification_outcome)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(k.value if k else "PENDING_VERIFICATION", int(v)) for k, v in rows]

    async def fraud_trend(self, days: int) -> list[tuple[str, str | None, int]]:
        bucket = func.date_trunc("day", FraudAlert.detected_at).label("day")
        stmt = (
            select(bucket, FraudAlert.severity, func.count())
            .where(FraudAlert.detected_at >= func.now() - func.make_interval(0, 0, 0, days))
            .group_by(bucket, FraudAlert.severity)
            .order_by(bucket)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            (r[0].date().isoformat(), r[1].value if r[1] else None, int(r[2])) for r in rows
        ]

    async def occupancy_trend(self, days: int) -> list[tuple[str, int, int]]:
        """Tenancies starting and ending per day, as a proxy for occupancy movement.

        The register keeps current occupancy, not a history of it, so a true
        point-in-time series is not derivable without a temporal table. Lease
        starts and ends are the closest honest signal — and the chart is labelled
        as movement rather than as a stock, so it does not claim to be what it
        is not.
        """
        start_bucket = func.date_trunc("day", Tenant.lease_start).label("day")
        starts = (
            select(start_bucket, func.count().label("n"))
            .where(
                Tenant.deleted_at.is_(None),
                Tenant.lease_start >= func.now() - func.make_interval(0, 0, 0, days),
            )
            .group_by(start_bucket)
        ).subquery()

        end_bucket = func.date_trunc("day", Tenant.lease_end).label("day")
        ends = (
            select(end_bucket, func.count().label("n"))
            .where(
                Tenant.deleted_at.is_(None),
                Tenant.lease_end >= func.now() - func.make_interval(0, 0, 0, days),
            )
            .group_by(end_bucket)
        ).subquery()

        stmt = (
            select(
                func.coalesce(starts.c.day, ends.c.day).label("day"),
                func.coalesce(starts.c.n, 0),
                func.coalesce(ends.c.n, 0),
            )
            .select_from(starts)
            .outerjoin(ends, ends.c.day == starts.c.day)
            .order_by(func.coalesce(starts.c.day, ends.c.day))
        )
        rows = (await self.session.execute(stmt)).all()
        return [(r[0].date().isoformat(), int(r[1]), int(r[2])) for r in rows if r[0]]

    async def occupancy_split(self) -> dict[str, int]:
        stmt = (
            select(Unit.occupancy_status, func.count())
            .where(Unit.deleted_at.is_(None))
            .group_by(Unit.occupancy_status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {(k.value if k else "UNSPECIFIED"): int(v) for k, v in rows}

    # =======================================================================
    # Tables
    # =======================================================================
    async def recent_activities(self, limit: int) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def recent_verifications(self, limit: int) -> list[dict]:
        stmt = (
            select(
                VerificationRecord.verification_id,
                VerificationRecord.outcome,
                VerificationRecord.verified_at,
                VerificationRecord.requested_at,
                Ulpin.short_code,
                Unit.unit_number,
                Building.building_name,
            )
            .outerjoin(Ulpin, Ulpin.ulpin_id == VerificationRecord.ulpin_id)
            .outerjoin(Unit, Unit.unit_id == VerificationRecord.unit_id)
            .outerjoin(Building, Building.building_id == Unit.building_id)
            .order_by(VerificationRecord.requested_at.desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]

    async def recent_alerts(self, limit: int) -> list[dict]:
        stmt = (
            select(
                FraudAlert.alert_id,
                FraudAlert.rule_code,
                FraudAlert.severity,
                FraudAlert.status,
                FraudAlert.title,
                FraudAlert.detected_at,
                Unit.unit_number,
                Building.building_name,
                Ulpin.short_code,
            )
            .outerjoin(Unit, Unit.unit_id == FraudAlert.unit_id)
            .outerjoin(Building, Building.building_id == Unit.building_id)
            .outerjoin(
                Ulpin,
                and_(Ulpin.unit_id == Unit.unit_id, Ulpin.status.in_(LIVE_ULPIN_STATUSES)),
            )
            .where(FraudAlert.status.in_((AlertStatus.OPEN, AlertStatus.INVESTIGATING)))
            .order_by(FraudAlert.detected_at.desc())
            .limit(limit)
        )
        return [dict(r._mapping) for r in (await self.session.execute(stmt)).all()]
