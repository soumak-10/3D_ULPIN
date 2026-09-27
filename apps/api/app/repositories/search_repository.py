"""Search data access.

One base query, four ways in. The join set is identical whichever field matched —
a result card shows building, floor, owner and tenant regardless — so the
difference between the modes is a WHERE clause and a rank expression, not four
separate queries to keep in step.

Matching is trigram, not full-text. The query that has to be fast here is the
half-remembered fragment: "Sunrise" typed at "Sunrise Residency Tower B",
"Banerje" at "Banerjee". A tsvector tokeniser gets the first and misses the
second; ``pg_trgm`` with a GIN index gets both and tolerates the transpositions
people actually make.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import Select, String, and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AlertStatus, TenancyStatus
from app.models.fraud import FraudAlert
from app.models.property import Building, Floor, Owner, Tenant, Unit, UnitOwnership
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin

# Anything with the shape of a code goes down the ULPIN path. Deliberately loose:
# two letters, a separator, then alphanumerics is enough to distinguish
# "WB-KOL-B001" from "Rina Banerjee" without demanding the user type it perfectly.
CODE_HINT = re.compile(r"^[A-Za-z]{2}[-\s/]?[A-Za-z]{2,3}[-\s/]?[A-Za-z0-9-]*$")
LONG_CODE_HINT = re.compile(r"^IN\d{2}[A-Za-z]{3}\d+", re.IGNORECASE)

SIMILARITY_FLOOR = 0.15


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =======================================================================
    # Mode detection
    # =======================================================================
    @staticmethod
    def detect_mode(q: str) -> str:
        """Guess which field the user meant.

        Order matters: a code is unambiguous, so it is tested first and wins. A
        string of words could be a person or a building, and buildings are the
        larger haystack, so the ambiguous case falls through to an all-fields
        search rather than picking one and being wrong half the time.
        """
        text = q.strip()
        if LONG_CODE_HINT.match(text) or (CODE_HINT.match(text) and any(c.isdigit() for c in text)):
            return "ulpin"
        return "auto"

    # =======================================================================
    # Base query
    # =======================================================================
    def _base(self) -> Select:
        """Units with their floor, building, live code, owners, tenant and alert count.

        The owner list is aggregated in SQL rather than assembled from a second
        query per row. A page of 50 results with 1.4 owners each is 70 extra
        round trips done the naive way.
        """
        owner_names = (
            select(func.array_agg(func.coalesce(Owner.full_name, Owner.organisation_name)))
            .select_from(UnitOwnership)
            .join(Owner, Owner.owner_id == UnitOwnership.owner_id)
            .where(
                UnitOwnership.unit_id == Unit.unit_id,
                UnitOwnership.ended_on.is_(None),
                Owner.deleted_at.is_(None),
            )
            .correlate(Unit)
            .scalar_subquery()
        )
        tenant_name = (
            select(Tenant.full_name)
            .where(
                Tenant.unit_id == Unit.unit_id,
                Tenant.status == TenancyStatus.ACTIVE,
                Tenant.deleted_at.is_(None),
            )
            .order_by(Tenant.lease_start.desc())
            .limit(1)
            .correlate(Unit)
            .scalar_subquery()
        )
        alert_count = (
            select(func.count())
            .select_from(FraudAlert)
            .where(
                FraudAlert.unit_id == Unit.unit_id,
                FraudAlert.status.in_((AlertStatus.OPEN, AlertStatus.INVESTIGATING)),
            )
            .correlate(Unit)
            .scalar_subquery()
        )

        return (
            select(
                Unit.unit_id,
                Unit.unit_number,
                Unit.unit_type,
                Unit.unit_status,
                Unit.occupancy_status,
                Unit.carpet_area_sqm,
                Unit.verification_outcome,
                Unit.last_verified_at,
                Floor.floor_id,
                Floor.floor_number,
                Floor.floor_label,
                Building.building_id,
                Building.building_name,
                Building.address_line1,
                Building.city,
                Building.state,
                Building.latitude,
                Building.longitude,
                Ulpin.ulpin_id,
                Ulpin.short_code,
                Ulpin.ulpin_code,
                owner_names.label("owner_names"),
                tenant_name.label("tenant_name"),
                alert_count.label("open_alert_count"),
            )
            .join(Floor, Floor.floor_id == Unit.floor_id)
            .join(Building, Building.building_id == Unit.building_id)
            .outerjoin(
                Ulpin,
                and_(Ulpin.unit_id == Unit.unit_id, Ulpin.status.in_(LIVE_ULPIN_STATUSES)),
            )
            .where(Unit.deleted_at.is_(None))
        )

    # =======================================================================
    # Predicates per mode
    # =======================================================================
    @staticmethod
    def _ulpin_predicate(q: str):
        # Normalised both sides so "wb kol b001" finds "WB-KOL-B001-F03-U301".
        squashed = re.sub(r"[\s\-_/]", "", q).upper()
        norm = func.upper(func.regexp_replace(func.coalesce(Ulpin.short_code, ""), r"[\s\-_/]", "", "g"))
        norm_long = func.upper(
            func.regexp_replace(func.coalesce(Ulpin.ulpin_code, ""), r"[\s\-_/]", "", "g")
        )
        pattern = f"%{squashed}%"
        return or_(
            norm.like(pattern),
            norm_long.like(pattern),
            func.upper(func.coalesce(Unit.unit_code, "")).like(pattern),
        )

    @staticmethod
    def _owner_predicate(q: str):
        pattern = f"%{q}%"
        return Unit.unit_id.in_(
            select(UnitOwnership.unit_id)
            .join(Owner, Owner.owner_id == UnitOwnership.owner_id)
            .where(
                UnitOwnership.ended_on.is_(None),
                Owner.deleted_at.is_(None),
                or_(
                    Owner.full_name.ilike(pattern),
                    Owner.organisation_name.ilike(pattern),
                    func.similarity(func.coalesce(Owner.full_name, ""), q) > SIMILARITY_FLOOR,
                ),
            )
        )

    @staticmethod
    def _tenant_predicate(q: str):
        pattern = f"%{q}%"
        return Unit.unit_id.in_(
            select(Tenant.unit_id).where(
                Tenant.deleted_at.is_(None),
                or_(
                    Tenant.full_name.ilike(pattern),
                    func.similarity(func.coalesce(Tenant.full_name, ""), q) > SIMILARITY_FLOOR,
                ),
            )
        )

    @staticmethod
    def _building_predicate(q: str):
        pattern = f"%{q}%"
        return or_(
            Building.building_name.ilike(pattern),
            Building.address_line1.ilike(pattern),
            Building.locality.ilike(pattern),
            func.similarity(func.coalesce(Building.building_name, ""), q) > SIMILARITY_FLOOR,
        )

    def _predicate_for(self, mode: str, q: str):
        if mode == "ulpin":
            return self._ulpin_predicate(q)
        if mode == "owner":
            return self._owner_predicate(q)
        if mode == "tenant":
            return self._tenant_predicate(q)
        if mode == "building":
            return self._building_predicate(q)
        return or_(
            self._ulpin_predicate(q),
            self._owner_predicate(q),
            self._tenant_predicate(q),
            self._building_predicate(q),
        )

    # =======================================================================
    # Search
    # =======================================================================
    async def search(
        self,
        q: str,
        *,
        mode: str,
        page: int,
        page_size: int,
        filters: dict | None = None,
    ) -> tuple[list[dict], int]:
        filters = filters or {}
        stmt = self._base().where(self._predicate_for(mode, q))
        stmt = self._apply_filters(stmt, filters)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        # Rank by best trigram similarity across the name fields, so an exact
        # building name outranks a flat whose tenant merely resembles the query.
        rank = func.greatest(
            func.similarity(func.coalesce(Building.building_name, ""), q),
            func.similarity(func.coalesce(Unit.unit_number, ""), q),
            func.similarity(func.coalesce(Ulpin.short_code, ""), q),
        ).label("rank")

        stmt = (
            stmt.add_columns(rank)
            .order_by(rank.desc(), Building.building_name, Floor.floor_number, Unit.unit_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [dict(r._mapping) for r in rows], total

    @staticmethod
    def _apply_filters(stmt: Select, f: dict) -> Select:
        if f.get("unit_type"):
            stmt = stmt.where(Unit.unit_type == f["unit_type"])
        if f.get("occupancy_status"):
            stmt = stmt.where(Unit.occupancy_status == f["occupancy_status"])
        if f.get("verification_outcome"):
            stmt = stmt.where(Unit.verification_outcome == f["verification_outcome"])
        if f.get("building_id"):
            stmt = stmt.where(Unit.building_id == f["building_id"])
        if f.get("city"):
            stmt = stmt.where(Building.city.ilike(f["city"]))
        if f.get("state"):
            stmt = stmt.where(Building.state.ilike(f["state"]))
        if f.get("floor_number_min") is not None:
            stmt = stmt.where(Floor.floor_number >= f["floor_number_min"])
        if f.get("floor_number_max") is not None:
            stmt = stmt.where(Floor.floor_number <= f["floor_number_max"])
        if f.get("has_open_alerts") is True:
            stmt = stmt.where(
                Unit.unit_id.in_(
                    select(FraudAlert.unit_id).where(
                        FraudAlert.status.in_((AlertStatus.OPEN, AlertStatus.INVESTIGATING))
                    )
                )
            )
        return stmt

    # =======================================================================
    # Typeahead
    # =======================================================================
    async def suggest(self, q: str, limit: int = 8) -> list[dict]:
        """Short, mixed list for the search box dropdown.

        Four cheap queries rather than one UNION: each is a different index and
        the planner does better with them separate. The list is capped hard —
        a dropdown is a shortcut, not a result page.
        """
        pattern = f"%{q}%"
        per = max(2, limit // 4)
        out: list[dict] = []

        codes = (
            await self.session.execute(
                select(Ulpin.short_code, Ulpin.ulpin_code)
                .where(
                    Ulpin.status.in_(LIVE_ULPIN_STATUSES),
                    or_(Ulpin.short_code.ilike(pattern), Ulpin.ulpin_code.ilike(pattern)),
                )
                .limit(per)
            )
        ).all()
        out += [
            {"label": c.short_code or c.ulpin_code, "value": c.short_code or c.ulpin_code,
             "kind": "ulpin", "hint": "Identifier"}
            for c in codes
        ]

        buildings = (
            await self.session.execute(
                select(Building.building_name, Building.city)
                .where(Building.deleted_at.is_(None), Building.building_name.ilike(pattern))
                .limit(per)
            )
        ).all()
        out += [
            {"label": b.building_name, "value": b.building_name, "kind": "building", "hint": b.city}
            for b in buildings
        ]

        owners = (
            await self.session.execute(
                select(Owner.full_name, Owner.organisation_name)
                .where(
                    Owner.deleted_at.is_(None),
                    or_(Owner.full_name.ilike(pattern), Owner.organisation_name.ilike(pattern)),
                )
                .limit(per)
            )
        ).all()
        out += [
            {
                "label": o.full_name or o.organisation_name,
                "value": o.full_name or o.organisation_name,
                "kind": "owner",
                "hint": "Owner",
            }
            for o in owners
        ]

        tenants = (
            await self.session.execute(
                select(Tenant.full_name)
                .where(Tenant.deleted_at.is_(None), Tenant.full_name.ilike(pattern))
                .limit(per)
            )
        ).all()
        out += [
            {"label": t.full_name, "value": t.full_name, "kind": "tenant", "hint": "Tenant"}
            for t in tenants
        ]

        return [o for o in out if o["label"]][:limit]
