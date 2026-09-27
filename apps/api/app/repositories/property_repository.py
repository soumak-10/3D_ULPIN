"""Data access for buildings, floors, units, owners and tenancies.

Spatial predicates live here rather than in the service, because they are SQL:
expressing a radius search through the ORM's Python layer would pull the whole
table into memory and defeat the GiST index that exists precisely to avoid that.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import (
    BuildingStatus,
    BuildingUse,
    OccupancyStatus,
    TenancyStatus,
    UnitStatus,
    UnitType,
)
from app.models.property import Building, Floor, Owner, Tenant, Unit, UnitOwnership


class BuildingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, building_id: uuid.UUID, *, with_floors: bool = False) -> Building | None:
        stmt: Select = select(Building).where(
            Building.building_id == building_id, Building.deleted_at.is_(None)
        )
        if with_floors:
            stmt = stmt.options(selectinload(Building.floors))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_parcel_block(self, parcel_ulpin: str, block_code: str) -> Building | None:
        stmt = select(Building).where(
            Building.parcel_ulpin == parcel_ulpin,
            Building.block_code == block_code,
            Building.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        city: str | None = None,
        state: str | None = None,
        status: BuildingStatus | None = None,
        building_use: BuildingUse | None = None,
        parcel_ulpin: str | None = None,
        jurisdiction_prefix: str | None = None,
        near: tuple[float, float] | None = None,
        radius_m: int | None = None,
    ) -> tuple[list[Building], int]:
        conditions = [Building.deleted_at.is_(None)]

        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(
                or_(
                    Building.building_name.ilike(pattern),
                    Building.address_line1.ilike(pattern),
                    Building.locality.ilike(pattern),
                    Building.parcel_ulpin.ilike(f"%{q.strip().upper()}%"),
                )
            )
        if city:
            conditions.append(Building.city.ilike(city))
        if state:
            conditions.append(Building.state.ilike(state))
        if status is not None:
            conditions.append(Building.status == status)
        if building_use is not None:
            conditions.append(Building.building_use == building_use)
        if parcel_ulpin:
            conditions.append(Building.parcel_ulpin == parcel_ulpin.upper())
        if jurisdiction_prefix:
            conditions.append(
                or_(
                    Building.jurisdiction_code == jurisdiction_prefix,
                    Building.jurisdiction_code.like(f"{jurisdiction_prefix}-%"),
                )
            )

        if near and radius_m:
            lon, lat = near
            # ST_DWithin on geography measures in metres and is index-assisted;
            # ST_Distance on geometry(4326) would measure in degrees, which is
            # not a length at all outside the equator.
            conditions.append(
                func.ST_DWithin(
                    func.cast(Building.location, text("geography")),
                    func.cast(
                        func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), text("geography")
                    ),
                    radius_m,
                )
            )

        total = (
            await self.session.execute(
                select(func.count()).select_from(Building).where(*conditions)
            )
        ).scalar_one()

        stmt = select(Building).where(*conditions)
        if near and radius_m:
            lon, lat = near
            stmt = stmt.order_by(
                func.ST_Distance(
                    func.cast(Building.location, text("geography")),
                    func.cast(
                        func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326), text("geography")
                    ),
                )
            )
        else:
            stmt = stmt.order_by(Building.created_at.desc())

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, int(total)

    async def unit_counts(self, building_id: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(Unit.occupancy_status, func.count())
            .where(Unit.building_id == building_id, Unit.deleted_at.is_(None))
            .group_by(Unit.occupancy_status)
        )
        rows = (await self.session.execute(stmt)).all()
        counts = {status.value: 0 for status in OccupancyStatus}
        for status, count in rows:
            counts[status.value] = int(count)
        counts["TOTAL"] = sum(counts[s.value] for s in OccupancyStatus)
        return counts

    def add(self, building: Building) -> Building:
        self.session.add(building)
        return building


class FloorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, floor_id: uuid.UUID) -> Floor | None:
        return await self.session.get(Floor, floor_id)

    async def get_by_number(self, building_id: uuid.UUID, floor_number: int) -> Floor | None:
        stmt = select(Floor).where(
            Floor.building_id == building_id, Floor.floor_number == floor_number
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_building(self, building_id: uuid.UUID) -> list[Floor]:
        stmt = (
            select(Floor)
            .where(Floor.building_id == building_id)
            .order_by(Floor.floor_number)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    def add(self, floor: Floor) -> Floor:
        self.session.add(floor)
        return floor


class UnitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, unit_id: uuid.UUID) -> Unit | None:
        stmt = select(Unit).where(Unit.unit_id == unit_id, Unit.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_relations(self, unit_id: uuid.UUID) -> Unit | None:
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

    async def get_by_number(self, floor_id: uuid.UUID, unit_number: str) -> Unit | None:
        stmt = select(Unit).where(
            Unit.floor_id == floor_id,
            func.upper(Unit.unit_number) == unit_number.strip().upper(),
            Unit.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        building_id: uuid.UUID | None = None,
        floor_id: uuid.UUID | None = None,
        floor_number: int | None = None,
        unit_type: UnitType | None = None,
        occupancy_status: OccupancyStatus | None = None,
        unit_status: UnitStatus | None = None,
        owner_id: uuid.UUID | None = None,
        q: str | None = None,
    ) -> tuple[list[Unit], int]:
        conditions = [Unit.deleted_at.is_(None)]

        if building_id:
            conditions.append(Unit.building_id == building_id)
        if floor_id:
            conditions.append(Unit.floor_id == floor_id)
        if unit_type is not None:
            conditions.append(Unit.unit_type == unit_type)
        if occupancy_status is not None:
            conditions.append(Unit.occupancy_status == occupancy_status)
        if unit_status is not None:
            conditions.append(Unit.unit_status == unit_status)
        if q:
            conditions.append(Unit.unit_number.ilike(f"%{q.strip()}%"))

        stmt = select(Unit).where(*conditions)
        count_stmt = select(func.count()).select_from(Unit).where(*conditions)

        if floor_number is not None:
            stmt = stmt.join(Floor, Floor.floor_id == Unit.floor_id).where(
                Floor.floor_number == floor_number
            )
            count_stmt = count_stmt.join(Floor, Floor.floor_id == Unit.floor_id).where(
                Floor.floor_number == floor_number
            )

        if owner_id:
            active_owner = (
                select(UnitOwnership.unit_id)
                .where(UnitOwnership.owner_id == owner_id, UnitOwnership.ended_on.is_(None))
                .scalar_subquery()
            )
            stmt = stmt.where(Unit.unit_id.in_(active_owner))
            count_stmt = count_stmt.where(Unit.unit_id.in_(active_owner))

        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            stmt.order_by(Unit.unit_ordinal.nulls_last(), Unit.unit_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, int(total)

    async def next_ordinal(self, floor_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Unit.unit_ordinal), 0)).where(
            Unit.floor_id == floor_id
        )
        return int((await self.session.execute(stmt)).scalar_one()) + 1

    async def is_owned_by_user(self, unit_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        stmt = select(
            select(UnitOwnership.ownership_id)
            .join(Owner, Owner.owner_id == UnitOwnership.owner_id)
            .where(
                UnitOwnership.unit_id == unit_id,
                UnitOwnership.ended_on.is_(None),
                Owner.user_id == user_id,
            )
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar())

    async def is_tenanted_by_user(self, unit_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        stmt = select(
            select(Tenant.tenant_id)
            .where(
                Tenant.unit_id == unit_id,
                Tenant.user_id == user_id,
                Tenant.status == TenancyStatus.ACTIVE,
                Tenant.deleted_at.is_(None),
            )
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar())

    def add(self, unit: Unit) -> Unit:
        self.session.add(unit)
        return unit


class OwnerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, owner_id: uuid.UUID) -> Owner | None:
        stmt = select(Owner).where(Owner.owner_id == owner_id, Owner.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_national_id_hash(self, id_hash: bytes) -> Owner | None:
        stmt = select(Owner).where(
            Owner.national_id_hash == id_hash, Owner.deleted_at.is_(None)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_by_user(self, user_id: uuid.UUID) -> Owner | None:
        stmt = select(Owner).where(Owner.user_id == user_id, Owner.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalars().first()

    async def search(
        self, *, page: int, page_size: int, q: str | None = None
    ) -> tuple[list[Owner], int]:
        conditions = [Owner.deleted_at.is_(None)]
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(
                or_(
                    Owner.full_name.ilike(pattern),
                    Owner.organisation_name.ilike(pattern),
                    Owner.phone.ilike(pattern),
                    Owner.email.ilike(pattern),
                )
            )
        total = (
            await self.session.execute(
                select(func.count()).select_from(Owner).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(Owner)
            .where(*conditions)
            .order_by(Owner.full_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, int(total)

    def add(self, owner: Owner) -> Owner:
        self.session.add(owner)
        return owner


class OwnershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_for_unit(self, unit_id: uuid.UUID) -> list[UnitOwnership]:
        stmt = (
            select(UnitOwnership)
            .where(UnitOwnership.unit_id == unit_id, UnitOwnership.ended_on.is_(None))
            .options(selectinload(UnitOwnership.owner))
            .order_by(UnitOwnership.is_primary.desc(), UnitOwnership.share_fraction.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def end_active(self, unit_id: uuid.UUID, ended_on: date) -> int:
        rows = await self.active_for_unit(unit_id)
        for row in rows:
            row.ended_on = ended_on
        await self.session.flush()
        return len(rows)

    def add(self, ownership: UnitOwnership) -> UnitOwnership:
        self.session.add(ownership)
        return ownership


class TenancyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.tenant_id == tenant_id, Tenant.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def current_for_unit(self, unit_id: uuid.UUID) -> Tenant | None:
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
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_unit(
        self, unit_id: uuid.UUID, *, page: int = 1, page_size: int = 25
    ) -> tuple[list[Tenant], int]:
        conditions = [Tenant.unit_id == unit_id, Tenant.deleted_at.is_(None)]
        total = (
            await self.session.execute(
                select(func.count()).select_from(Tenant).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(Tenant)
            .where(*conditions)
            .order_by(Tenant.lease_start.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), int(total)

    async def list_for_user(
        self, user_id: uuid.UUID, *, page: int = 1, page_size: int = 25
    ) -> tuple[list[Tenant], int]:
        """Tenancies attached to a login.

        Only tenancies created with ``link_user_id`` appear here. A tenancy
        recorded against a walk-in name with no account is invisible to this
        query by design — there is nobody to show it to.
        """
        conditions = [Tenant.user_id == user_id, Tenant.deleted_at.is_(None)]
        total = (
            await self.session.execute(
                select(func.count()).select_from(Tenant).where(*conditions)
            )
        ).scalar_one()
        stmt = (
            select(Tenant)
            .where(*conditions)
            .order_by(Tenant.lease_start.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(stmt)).scalars().all()), int(total)

    async def overlaps(
        self, unit_id: uuid.UUID, start: date, end: date | None, exclude: uuid.UUID | None = None
    ) -> Tenant | None:
        """Pre-flight check for the exclusion constraint.

        The constraint is authoritative; this exists so the API can return a
        clear 409 naming the conflicting tenancy instead of surfacing a raw
        PostgreSQL error.
        """
        conditions = [
            Tenant.unit_id == unit_id,
            Tenant.status == TenancyStatus.ACTIVE,
            Tenant.deleted_at.is_(None),
            or_(Tenant.lease_end.is_(None), Tenant.lease_end >= start),
        ]
        if end is not None:
            conditions.append(Tenant.lease_start <= end)
        if exclude is not None:
            conditions.append(Tenant.tenant_id != exclude)

        stmt = select(Tenant).where(and_(*conditions)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def add(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        return tenant
