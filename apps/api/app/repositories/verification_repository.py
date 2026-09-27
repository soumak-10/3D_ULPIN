"""Data access for the verification engine.

Everything the five steps need, loaded in as few round trips as the steps allow.
The engine is called from a citizen-facing page, so its latency budget is a
page load, not a batch job.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import TenancyStatus, VerificationOutcome, VerificationStatus
from app.models.property import Building, Floor, Owner, Tenant, Unit, UnitOwnership
from app.models.verification import VerificationRecord


class VerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Subject loading -----------------------------------------------------
    async def unit_with_context(self, unit_id: uuid.UUID) -> Unit | None:
        """The unit plus everything the five checks read.

        One query with eager loads rather than five lazy ones: the engine runs
        synchronously behind a page load, and N+1 here is the difference between
        80 ms and a timeout on a building with 400 flats.
        """
        stmt = (
            select(Unit)
            .where(Unit.unit_id == unit_id, Unit.deleted_at.is_(None))
            .options(
                selectinload(Unit.ownerships).selectinload(UnitOwnership.owner),
                selectinload(Unit.tenancies),
                selectinload(Unit.floor),
                selectinload(Unit.building),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def geometry_facts(self, unit_id: uuid.UUID) -> dict:
        """Geometry presence and validity, as SQL rather than as Python.

        ``ST_IsValid`` and ``ST_Volume`` belong in the database — pulling the
        solid into Python to measure it would move megabytes per unit for a
        number PostGIS already has.

        ``is_solid_valid`` is the stored verdict from registration; this
        re-derives presence live, because a geometry can be dropped by a later
        correction without anyone resetting the flag.
        """
        stmt = select(
            Unit.volume_solid.is_not(None).label("has_solid"),
            Unit.centroid_3d.is_not(None).label("has_centroid"),
            Unit.is_solid_valid.label("stored_valid"),
            Unit.volume_cum.label("volume_cum"),
            Unit.floor_plate.is_not(None).label("has_plate"),
        ).where(Unit.unit_id == unit_id)
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            return {}
        return {
            "has_solid": bool(row.has_solid),
            "has_centroid": bool(row.has_centroid),
            "stored_valid": row.stored_valid,
            "volume_cum": float(row.volume_cum) if row.volume_cum is not None else None,
            "has_plate": bool(row.has_plate),
        }

    async def building_geometry_facts(self, building_id: uuid.UUID) -> dict:
        stmt = select(
            Building.footprint.is_not(None).label("has_footprint"),
            Building.envelope_solid.is_not(None).label("has_envelope"),
            Building.location.is_not(None).label("has_point"),
        ).where(Building.building_id == building_id)
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            return {}
        return {
            "has_footprint": bool(row.has_footprint),
            "has_envelope": bool(row.has_envelope),
            "has_point": bool(row.has_point),
        }

    async def ownership_share_total(self, unit_id: uuid.UUID) -> float:
        stmt = select(func.coalesce(func.sum(UnitOwnership.share_fraction), 0)).where(
            UnitOwnership.unit_id == unit_id, UnitOwnership.ended_on.is_(None)
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def match_owner_name(self, unit_id: uuid.UUID, name: str) -> Owner | None:
        """Does anyone on the title answer to this name?

        Deliberately fuzzy at the edges — ``ILIKE`` on a contains pattern — because
        the claimant types "R. Banerjee" for "Rina Banerjee" and refusing that as a
        mismatch would produce false accusations at scale. The strict comparison
        that matters legally is against ``claimed_owner_id``.
        """
        pattern = f"%{name.strip()}%"
        stmt = (
            select(Owner)
            .join(UnitOwnership, UnitOwnership.owner_id == Owner.owner_id)
            .where(
                UnitOwnership.unit_id == unit_id,
                UnitOwnership.ended_on.is_(None),
                Owner.deleted_at.is_(None),
                or_(Owner.full_name.ilike(pattern), Owner.organisation_name.ilike(pattern)),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def active_tenancy(self, unit_id: uuid.UUID) -> Tenant | None:
        stmt = (
            select(Tenant)
            .where(
                Tenant.unit_id == unit_id,
                Tenant.status == TenancyStatus.ACTIVE,
                Tenant.deleted_at.is_(None),
            )
            .order_by(Tenant.lease_start.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def active_tenancy_count(self, unit_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Tenant)
            .where(
                Tenant.unit_id == unit_id,
                Tenant.status == TenancyStatus.ACTIVE,
                Tenant.deleted_at.is_(None),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    # -- Records -------------------------------------------------------------
    async def get(self, verification_id: uuid.UUID) -> VerificationRecord | None:
        return await self.session.get(VerificationRecord, verification_id)

    async def history_for_ulpin(
        self, ulpin_id: uuid.UUID, *, page: int = 1, page_size: int = 25
    ) -> tuple[list[VerificationRecord], int]:
        conditions = [VerificationRecord.ulpin_id == ulpin_id]
        total = (
            await self.session.execute(
                select(func.count()).select_from(VerificationRecord).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(VerificationRecord)
            .where(*conditions)
            .order_by(VerificationRecord.requested_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), int(total)

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        outcome: VerificationOutcome | None = None,
        verification_status: VerificationStatus | None = None,
        building_id: uuid.UUID | None = None,
        unit_id: uuid.UUID | None = None,
    ) -> tuple[list[VerificationRecord], int]:
        conditions = []
        if outcome is not None:
            conditions.append(VerificationRecord.outcome == outcome)
        if verification_status is not None:
            conditions.append(VerificationRecord.status == verification_status)
        if building_id is not None:
            conditions.append(VerificationRecord.building_id == building_id)
        if unit_id is not None:
            conditions.append(VerificationRecord.unit_id == unit_id)

        total = (
            await self.session.execute(
                select(func.count()).select_from(VerificationRecord).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(VerificationRecord)
            .where(*conditions)
            .options(selectinload(VerificationRecord.ulpin))
            .order_by(VerificationRecord.requested_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), int(total)

    async def recent(self, limit: int = 10) -> list[VerificationRecord]:
        stmt = (
            select(VerificationRecord)
            .options(selectinload(VerificationRecord.ulpin))
            .order_by(VerificationRecord.requested_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def outcome_counts(self) -> dict[str, int]:
        """Distribution across units, not across records.

        The dashboard's "Verified / Pending" split is a count of *properties*. A
        unit verified four times must count once, which is why this reads
        ``units.verification_outcome`` rather than grouping verification_records.
        """
        stmt = (
            select(Unit.verification_outcome, func.count())
            .where(Unit.deleted_at.is_(None))
            .group_by(Unit.verification_outcome)
        )
        rows = (await self.session.execute(stmt)).all()
        counts = {o.value: 0 for o in VerificationOutcome}
        for outcome, count in rows:
            if outcome is not None:
                counts[outcome.value] = int(count)
        return counts

    def add(self, record: VerificationRecord) -> VerificationRecord:
        self.session.add(record)
        return record
