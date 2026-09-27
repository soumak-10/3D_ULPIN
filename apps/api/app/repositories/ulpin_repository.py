"""Data access for identifiers and the per-region building counter.

The counter is the interesting part. See :meth:`UlpinSequenceRepository.next_building_sequence`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import UlpinStatus, UlpinType
from app.models.property import Building, Floor, Unit
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin, UlpinSequence


class UlpinRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Lookups -------------------------------------------------------------
    async def get(self, ulpin_id: uuid.UUID) -> Ulpin | None:
        return await self.session.get(Ulpin, ulpin_id)

    async def get_by_code(self, code: str) -> Ulpin | None:
        """Resolve either spelling.

        Both columns are uniquely indexed, so this cannot match two rows — but
        it is written as an OR rather than two queries because the caller does
        not know which format the citizen typed.
        """
        normalised = code.strip().upper()
        stmt = select(Ulpin).where(
            or_(Ulpin.ulpin_code == normalised, Ulpin.short_code == normalised)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_for_unit(self, unit_id: uuid.UUID, *, live_only: bool = True) -> Ulpin | None:
        """The identifier currently describing a unit.

        Superseded and retired codes stay attached to the unit forever, so this
        filters to live statuses. A unit with two live codes is a database bug —
        ``.first()`` hides it rather than raising, so the fraud engine's
        DUPLICATE_ULPIN rule checks for it explicitly instead.
        """
        conditions = [Ulpin.unit_id == unit_id]
        if live_only:
            conditions.append(Ulpin.status.in_(LIVE_ULPIN_STATUSES))
        stmt = select(Ulpin).where(*conditions).order_by(Ulpin.created_at.desc())
        return (await self.session.execute(stmt)).scalars().first()

    async def get_for_building(
        self, building_id: uuid.UUID, *, ulpin_type: UlpinType | None = None
    ) -> Ulpin | None:
        conditions = [
            Ulpin.building_id == building_id,
            Ulpin.status.in_(LIVE_ULPIN_STATUSES),
        ]
        if ulpin_type is not None:
            conditions.append(Ulpin.ulpin_type == ulpin_type)
        stmt = select(Ulpin).where(*conditions).order_by(Ulpin.created_at)
        return (await self.session.execute(stmt)).scalars().first()

    async def codes_for_units(self, unit_ids: list[uuid.UUID]) -> dict[uuid.UUID, Ulpin]:
        """Bulk variant, so the 3D viewer does not issue one query per unit."""
        if not unit_ids:
            return {}
        stmt = select(Ulpin).where(
            Ulpin.unit_id.in_(unit_ids), Ulpin.status.in_(LIVE_ULPIN_STATUSES)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {r.unit_id: r for r in rows if r.unit_id is not None}

    async def exists_code(self, *, ulpin_code: str | None = None, short_code: str | None = None) -> bool:
        conditions = []
        if ulpin_code:
            conditions.append(Ulpin.ulpin_code == ulpin_code)
        if short_code:
            conditions.append(Ulpin.short_code == short_code)
        if not conditions:
            return False
        stmt = select(select(Ulpin.ulpin_id).where(or_(*conditions)).exists())
        return bool((await self.session.execute(stmt)).scalar())

    async def search(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        status: UlpinStatus | None = None,
        ulpin_type: UlpinType | None = None,
        building_id: uuid.UUID | None = None,
        state_code: str | None = None,
        city_code: str | None = None,
        jurisdiction_prefix: str | None = None,
    ) -> tuple[list[Ulpin], int]:
        conditions = []

        if q:
            pattern = f"%{q.strip().upper()}%"
            conditions.append(
                or_(Ulpin.ulpin_code.ilike(pattern), Ulpin.short_code.ilike(pattern))
            )
        if status is not None:
            conditions.append(Ulpin.status == status)
        if ulpin_type is not None:
            conditions.append(Ulpin.ulpin_type == ulpin_type)
        if building_id is not None:
            conditions.append(Ulpin.building_id == building_id)
        if state_code:
            conditions.append(Ulpin.state_code == state_code.upper())
        if city_code:
            conditions.append(Ulpin.city_code == city_code.upper())
        if jurisdiction_prefix:
            conditions.append(
                or_(
                    Ulpin.jurisdiction_code == jurisdiction_prefix,
                    Ulpin.jurisdiction_code.like(f"{jurisdiction_prefix}-%"),
                )
            )

        total = (
            await self.session.execute(select(func.count()).select_from(Ulpin).where(*conditions))
        ).scalar_one()

        stmt: Select = (
            select(Ulpin)
            .where(*conditions)
            .options(selectinload(Ulpin.unit), selectinload(Ulpin.building))
            .order_by(Ulpin.short_code.nulls_last(), Ulpin.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, int(total)

    # -- Units awaiting an identifier ----------------------------------------
    async def uncoded_units(self, building_id: uuid.UUID) -> list[Unit]:
        """Live units under a building with no live identifier.

        A LEFT JOIN rather than a NOT IN subquery: NOT IN against a column that
        can be NULL returns no rows at all when any NULL is present, and
        ``ulpins.unit_id`` is nullable because building-level codes exist.
        """
        live_codes = (
            select(Ulpin.unit_id)
            .where(Ulpin.unit_id.is_not(None), Ulpin.status.in_(LIVE_ULPIN_STATUSES))
            .subquery()
        )
        stmt = (
            select(Unit)
            .outerjoin(live_codes, live_codes.c.unit_id == Unit.unit_id)
            .join(Floor, Floor.floor_id == Unit.floor_id)
            .where(
                Unit.building_id == building_id,
                Unit.deleted_at.is_(None),
                live_codes.c.unit_id.is_(None),
            )
            .order_by(Floor.floor_number, Unit.unit_ordinal.nulls_last(), Unit.unit_number)
            .options(selectinload(Unit.floor))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_for_building(self, building_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Ulpin)
            .where(Ulpin.building_id == building_id, Ulpin.status.in_(LIVE_ULPIN_STATUSES))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    def add(self, ulpin: Ulpin) -> Ulpin:
        self.session.add(ulpin)
        return ulpin


class UlpinSequenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def next_building_sequence(self, state_code: str, city_code: str) -> int:
        """Allocate the next building number for one state+city.

        Not a PostgreSQL ``SEQUENCE``, for two reasons:

        1. A sequence is global. This counter is per region — Kolkata's B001 and
           Bengaluru's B001 are different buildings and must both exist.
        2. Sequences are explicitly non-transactional. A registration that rolls
           back would burn its number and leave a permanent hole in the series,
           which is the kind of thing officers file tickets about.

        ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` is atomic in one
        statement and takes a row lock, so concurrent allocators for the same
        region serialise on that row and allocators for different regions do not
        contend at all. The increment participates in the surrounding
        transaction, so a rollback returns the number.
        """
        state = state_code.strip().upper()
        city = city_code.strip().upper()

        stmt = (
            pg_insert(UlpinSequence)
            .values(state_code=state, city_code=city, last_value=1)
            .on_conflict_do_update(
                index_elements=[UlpinSequence.state_code, UlpinSequence.city_code],
                set_={
                    "last_value": UlpinSequence.__table__.c.last_value + 1,
                    "updated_at": func.now(),
                },
            )
            .returning(UlpinSequence.__table__.c.last_value)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def peek(self, state_code: str, city_code: str) -> UlpinSequence | None:
        stmt = select(UlpinSequence).where(
            UlpinSequence.state_code == state_code.upper(),
            UlpinSequence.city_code == city_code.upper(),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[UlpinSequence]:
        stmt = select(UlpinSequence).order_by(UlpinSequence.state_code, UlpinSequence.city_code)
        return list((await self.session.execute(stmt)).scalars().all())

    async def reserve_at_least(self, state_code: str, city_code: str, value: int) -> int:
        """Raise the counter to ``value`` if it is below it, and report where it landed.

        Needed when a building already carries a short code assigned outside this
        service — a data migration, a manually corrected record. Without this the
        counter would re-issue a number that is already on a building and the
        unique index would reject the next legitimate registration.
        """
        state = state_code.strip().upper()
        city = city_code.strip().upper()

        stmt = (
            pg_insert(UlpinSequence)
            .values(state_code=state, city_code=city, last_value=value)
            .on_conflict_do_update(
                index_elements=[UlpinSequence.state_code, UlpinSequence.city_code],
                set_={
                    "last_value": func.greatest(UlpinSequence.__table__.c.last_value, value),
                    "updated_at": func.now(),
                },
            )
            .returning(UlpinSequence.__table__.c.last_value)
        )
        return int((await self.session.execute(stmt)).scalar_one())


class BuildingCodeRepository:
    """Reads the building side of the short-code allocation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, building_id: uuid.UUID) -> Building | None:
        stmt = select(Building).where(
            Building.building_id == building_id, Building.deleted_at.is_(None)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def max_sequence_in_region(self, state_code: str, city_code: str) -> int:
        """Highest building number actually in use in a region.

        Used to heal a counter that has fallen behind the data — after a restore
        from a dump taken mid-write, for instance.
        """
        stmt = select(func.coalesce(func.max(Building.building_sequence), 0)).where(
            Building.short_code.like(f"{state_code.upper()}-{city_code.upper()}-%")
        )
        return int((await self.session.execute(stmt)).scalar_one())
