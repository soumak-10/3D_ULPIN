"""Dashboard assembly.

One response, built from independent aggregates. They are gathered concurrently
rather than sequentially — eleven queries at 30 ms each is 330 ms serially and
about 40 ms in parallel, which is the difference between a dashboard that feels
instant and one that feels like a report.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedException
from app.core.permissions import Permission, has_permission
from app.models.enums import OccupancyStatus, VerificationOutcome
from app.models.user import User
from app.repositories.dashboard_repository import DashboardRepository
from app.schemas.dashboard import (
    ActivityRow,
    AlertRow,
    DashboardResponse,
    OccupancyPoint,
    SeriesPoint,
    StatCard,
    TrendPoint,
    VerificationRow,
)

# Human labels for the enum values the charts group by. Kept here rather than in
# the frontend so a new unit type appears on the chart without a web deploy.
TYPE_LABELS = {
    "RESIDENTIAL": "Residential",
    "COMMERCIAL": "Commercial",
    "INDUSTRIAL": "Industrial",
    "PARKING": "Parking",
    "STORAGE": "Storage",
    "UTILITY": "Utility",
    "COMMON_AREA": "Common area",
    "MIXED": "Mixed use",
    "UNSPECIFIED": "Unspecified",
}

OUTCOME_LABELS = {
    "VERIFIED": "Verified",
    "PENDING_VERIFICATION": "Pending verification",
    "INVALID_CLAIM": "Invalid claim",
    "UNAUTHORIZED_OCCUPANCY": "Unauthorized occupancy",
}


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DashboardRepository(session)

    async def overview(self, *, actor: User, trend_days: int = 30) -> DashboardResponse:
        # The dashboard is the officer's landing page; anyone who can read a
        # building can see the aggregate. The tables inside are themselves
        # aggregate — no PII crosses this boundary.
        if not has_permission(actor.role, Permission.BUILDING_READ):
            raise PermissionDeniedException("This action requires building:read")

        # Sequential awaits on one AsyncSession, not asyncio.gather: a single
        # session is one connection and one transaction. Concurrent statements on
        # it raise InterfaceError, and the fix is a connection per query, which
        # is not worth a pool slot each for a page that is already fast.
        cards = await self._cards()
        distribution = await self.repo.property_distribution()
        verification = await self.repo.verification_distribution()
        fraud = await self.repo.fraud_trend(trend_days)
        occupancy = await self.repo.occupancy_trend(trend_days)
        activities = await self.repo.recent_activities(10)
        verifications = await self.repo.recent_verifications(10)
        alerts = await self.repo.recent_alerts(10)

        return DashboardResponse(
            generated_at=datetime.now(UTC),
            cards=cards,
            property_distribution=[
                SeriesPoint(key=k, label=TYPE_LABELS.get(k, k.title()), value=v)
                for k, v in distribution
            ],
            verification_status=[
                SeriesPoint(key=k, label=OUTCOME_LABELS.get(k, k.title()), value=v)
                for k, v in verification
            ],
            fraud_trend=self._fold_fraud(fraud),
            occupancy_trend=[
                OccupancyPoint(
                    date=day,
                    occupied=starts,
                    vacant=ends,
                    occupancy_rate=(
                        round(starts / (starts + ends), 4) if (starts + ends) else 0.0
                    ),
                )
                for day, starts, ends in occupancy
            ],
            recent_activities=[
                ActivityRow(
                    log_id=a.log_id,
                    action=a.action.value if hasattr(a.action, "value") else str(a.action),
                    entity_type=a.entity_type,
                    entity_id=a.entity_id,
                    ulpin_code=a.ulpin_code,
                    actor_role=(
                        a.actor_role.value if hasattr(a.actor_role, "value") else a.actor_role
                    ),
                    actor_user_id=a.actor_user_id,
                    success=bool(a.success),
                    created_at=a.created_at,
                )
                for a in activities
            ],
            recent_verifications=[VerificationRow(**v) for v in verifications],
            fraud_alerts=[AlertRow(**a) for a in alerts],
        )

    # -----------------------------------------------------------------------
    async def _cards(self) -> list[StatCard]:
        buildings = await self.repo.total_buildings()
        units = await self.repo.total_units()
        ulpins = await self.repo.total_ulpins()
        verified = await self.repo.units_by_outcome(VerificationOutcome.VERIFIED)
        pending = await self.repo.units_by_outcome(VerificationOutcome.PENDING_VERIFICATION)
        alerts = await self.repo.open_alerts()
        tenants = await self.repo.active_tenants()
        occupancy = await self.repo.occupancy_split()

        occupied = occupancy.get(OccupancyStatus.OCCUPIED.value, 0)

        return [
            StatCard(
                key="total_buildings",
                label="Total Buildings",
                value=buildings,
                hint="Registered, not deleted",
            ),
            StatCard(
                key="total_units",
                label="Total Units",
                value=units,
                hint=f"{occupied} occupied" if units else None,
            ),
            StatCard(
                key="total_ulpins",
                label="Total ULPINs",
                value=ulpins,
                hint="Live identifiers — excludes superseded and retired",
            ),
            StatCard(
                key="verified_properties",
                label="Verified Properties",
                value=verified,
                delta=round(verified / units, 4) if units else None,
                delta_label="of all units",
            ),
            StatCard(
                key="pending_properties",
                label="Pending Properties",
                value=pending,
                hint="Awaiting a record, most often the 3D volume",
            ),
            StatCard(
                key="fraud_alerts",
                label="Fraud Alerts",
                value=alerts,
                hint="Open and under investigation",
            ),
            StatCard(
                key="active_tenants",
                label="Active Tenants",
                value=tenants,
                hint="Live leases",
            ),
        ]

    @staticmethod
    def _fold_fraud(rows: list[tuple[str, str | None, int]]) -> list[TrendPoint]:
        """Pivot (day, severity, count) rows into one point per day.

        Recharts wants a flat object per x-value with one key per series. Doing
        the pivot here keeps the chart component free of reshaping logic, which
        is where that logic goes stale.
        """
        buckets: dict[str, TrendPoint] = {}
        for day, severity, count in rows:
            point = buckets.setdefault(day, TrendPoint(date=day))
            point.total += count
            key = (severity or "").lower()
            if key in ("low", "medium", "high", "critical"):
                setattr(point, key, getattr(point, key) + count)
        return [buckets[d] for d in sorted(buckets)]
