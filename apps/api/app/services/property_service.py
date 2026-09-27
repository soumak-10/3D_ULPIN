"""Property registration use cases.

Owns the transaction boundary for buildings, floors, units, owners and
tenancies. Scope enforcement lives here rather than in the endpoints because
only this layer has the loaded row to check a jurisdiction against: the endpoint
knows the *role* holds ``building:update``, but only the service knows whether
*this* building is inside the officer's patch.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    DuplicateResourceException,
    ImmutableRecordException,
    JurisdictionDeniedException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.core.permissions import (
    Permission,
    Role,
    can_access_jurisdiction,
    has_permission,
    is_global_scope,
)
from app.core.security import hash_national_id, national_id_last4
from app.models.enums import (
    BuildingStatus,
    FloorType,
    OccupancyStatus,
    TenancyStatus,
    UnitStatus,
)
from app.models.property import Building, Floor, Owner, Tenant, Unit, UnitOwnership
from app.models.user import User
from app.repositories.property_repository import (
    BuildingRepository,
    FloorRepository,
    OwnerRepository,
    OwnershipRepository,
    TenancyRepository,
    UnitRepository,
)
from app.schemas.property import (
    BuildingCreate,
    BuildingSearchParams,
    BuildingUpdate,
    FloorCreate,
    FloorUpdate,
    OwnerCreate,
    OwnershipAssign,
    OwnerUpdate,
    TenantCreate,
    TenantUpdate,
    UnitBase,
    UnitBulkCreate,
    UnitCreate,
    UnitUpdate,
)
from app.ulpin import codec

logger = logging.getLogger(__name__)

# Nominal storey height, used only to seed floor elevations when a plan gives
# none, so the 3D extrusion has a starting geometry. A surveyed figure replaces
# it the moment one is entered.
DEFAULT_FLOOR_HEIGHT_M = Decimal("3.000")


class PropertyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.buildings = BuildingRepository(session)
        self.floors = FloorRepository(session)
        self.units = UnitRepository(session)
        self.owners = OwnerRepository(session)
        self.ownerships = OwnershipRepository(session)
        self.tenancies = TenancyRepository(session)

    # =======================================================================
    # Buildings
    # =======================================================================
    async def create_building(self, payload: BuildingCreate, *, actor: User) -> Building:
        self._require_jurisdiction(actor, payload.jurisdiction_code)

        existing = await self.buildings.get_by_parcel_block(
            payload.parcel_ulpin, payload.block_code
        )
        if existing is not None:
            raise DuplicateResourceException(
                f"Block {payload.block_code} of parcel {payload.parcel_ulpin} is already "
                f"registered as '{existing.building_name}'"
            )

        building = Building(
            parcel_ulpin=payload.parcel_ulpin,
            block_code=payload.block_code,
            building_name=payload.building_name,
            building_number=payload.building_number,
            address_line1=payload.address_line1,
            address_line2=payload.address_line2,
            locality=payload.locality,
            city=payload.city,
            district=payload.district,
            state=payload.state,
            postal_code=payload.postal_code,
            jurisdiction_code=payload.jurisdiction_code,
            latitude=payload.latitude,
            longitude=payload.longitude,
            # Longitude first: ST_MakePoint takes (x, y). Transposing them
            # silently relocates the building to the wrong hemisphere, and the
            # BBOX validator on the schema is the only thing that would have
            # caught it — so keep the order obvious here.
            location=func.ST_SetSRID(
                func.ST_MakePoint(float(payload.longitude), float(payload.latitude)), 4326
            ),
            ground_elevation_m=payload.ground_elevation_m,
            building_height_m=payload.building_height_m,
            plot_area_sqm=payload.plot_area_sqm,
            built_up_area_sqm=payload.built_up_area_sqm,
            metric_srid=payload.metric_srid,
            vertical_datum=payload.vertical_datum,
            total_floors=payload.total_floors,
            basement_floors=payload.basement_floors,
            building_use=payload.building_use,
            construction_status=payload.construction_status,
            status=BuildingStatus.DRAFT,
            sanction_number=payload.sanction_number,
            sanction_date=payload.sanction_date,
            occupancy_certificate_number=payload.occupancy_certificate_number,
            occupancy_certificate_date=payload.occupancy_certificate_date,
            year_built=payload.year_built,
            created_by=actor.user_id,
        )
        self.buildings.add(building)
        await self.session.flush()

        if payload.auto_create_floors:
            await self._generate_floors(building)

        await self.session.commit()
        await self.session.refresh(building)
        logger.info(
            "building registered building_id=%s parcel=%s by=%s",
            building.building_id,
            building.parcel_ulpin,
            actor.user_id,
        )
        return building

    async def _generate_floors(self, building: Building) -> list[Floor]:
        """Create one row per storey, basements first.

        Numbering runs -n…-1, 0, 1…m and skips nothing: a later gap in this
        sequence means a floor was deliberately removed, not that generation
        misfired. Elevations are seeded from the ground elevation and the
        nominal storey height.
        """
        base = building.ground_elevation_m or Decimal("0")
        created: list[Floor] = []

        numbers = list(range(-building.basement_floors, 0)) + list(range(0, building.total_floors))
        for number in numbers:
            floor_type = FloorType(codec.infer_floor_type(number))
            elevation_base = base + Decimal(number) * DEFAULT_FLOOR_HEIGHT_M
            floor = Floor(
                building_id=building.building_id,
                floor_number=number,
                floor_type=floor_type,
                storey_code=codec.storey_code(number, floor_type.value),
                floor_label=_floor_label(number),
                elevation_base_m=elevation_base,
                elevation_top_m=elevation_base + DEFAULT_FLOOR_HEIGHT_M,
                floor_height_m=DEFAULT_FLOOR_HEIGHT_M,
            )
            self.floors.add(floor)
            created.append(floor)

        await self.session.flush()
        return created

    async def get_building(self, building_id: uuid.UUID, *, actor: User) -> Building:
        building = await self.buildings.get(building_id, with_floors=True)
        if building is None:
            raise NotFoundException("Building", building_id)
        await self._assert_can_read_building(building, actor)
        return building

    async def update_building(
        self, building_id: uuid.UUID, payload: BuildingUpdate, *, actor: User
    ) -> Building:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code)

        if not building.is_editable:
            raise ImmutableRecordException(
                f"This building is {building.status.value}. Registered records are "
                "amended by supersession, not by editing."
            )

        changes = payload.model_dump(exclude_unset=True, exclude_none=True)

        # Shrinking the tower below its occupied storeys would orphan units.
        if "total_floors" in changes or "basement_floors" in changes:
            await self._assert_floor_count_shrinkable(
                building,
                new_total=changes.get("total_floors", building.total_floors),
                new_basements=changes.get("basement_floors", building.basement_floors),
            )

        for field, value in changes.items():
            setattr(building, field, value)

        if "latitude" in changes or "longitude" in changes:
            building.location = func.ST_SetSRID(
                func.ST_MakePoint(float(building.longitude), float(building.latitude)), 4326
            )

        await self.session.commit()
        await self.session.refresh(building)
        return building

    async def _assert_floor_count_shrinkable(
        self, building: Building, *, new_total: int, new_basements: int
    ) -> None:
        stmt = select(func.min(Floor.floor_number), func.max(Floor.floor_number)).where(
            Floor.building_id == building.building_id,
            Floor.unit_count > 0,
        )
        lowest, highest = (await self.session.execute(stmt)).one()
        if highest is not None and highest >= new_total:
            raise ConflictException(
                f"Floor {highest} holds registered units; the building cannot be "
                f"reduced to {new_total} floors"
            )
        if lowest is not None and lowest < -new_basements:
            raise ConflictException(
                f"Basement level {lowest} holds registered units; it cannot be removed"
            )

    async def set_building_status(
        self, building_id: uuid.UUID, new_status: BuildingStatus, *, actor: User
    ) -> Building:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code)

        # The legal transition graph. Anything absent is refused, including every
        # path out of ARCHIVED — an archived record is terminal.
        allowed: dict[BuildingStatus, set[BuildingStatus]] = {
            BuildingStatus.DRAFT: {BuildingStatus.SUBMITTED, BuildingStatus.ARCHIVED},
            BuildingStatus.SUBMITTED: {
                BuildingStatus.UNDER_VERIFICATION,
                BuildingStatus.REJECTED,
                BuildingStatus.DRAFT,
            },
            BuildingStatus.UNDER_VERIFICATION: {
                BuildingStatus.VERIFIED,
                BuildingStatus.REJECTED,
            },
            BuildingStatus.VERIFIED: {BuildingStatus.REGISTERED, BuildingStatus.REJECTED},
            BuildingStatus.REGISTERED: {BuildingStatus.ARCHIVED},
            BuildingStatus.REJECTED: {BuildingStatus.DRAFT, BuildingStatus.ARCHIVED},
            BuildingStatus.ARCHIVED: set(),
        }
        if new_status not in allowed[building.status]:
            permitted = ", ".join(sorted(s.value for s in allowed[building.status])) or "none"
            raise ConflictException(
                f"{building.status.value} cannot move to {new_status.value}. "
                f"Permitted: {permitted}"
            )

        if new_status in (BuildingStatus.VERIFIED, BuildingStatus.REGISTERED):
            if not has_permission(actor.role.value, Permission.BUILDING_VERIFY):
                raise PermissionDeniedException(
                    "Only a property officer may verify or register a building"
                )
            building.verified_by = actor.user_id
            building.verified_at = datetime.now(UTC)

        building.status = new_status
        await self.session.commit()
        await self.session.refresh(building)
        return building

    async def delete_building(self, building_id: uuid.UUID, *, actor: User) -> None:
        """Soft delete. A registered building is never removed: identifiers
        already issued against it must keep resolving."""
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code)

        if building.status is BuildingStatus.REGISTERED:
            raise ImmutableRecordException(
                "A registered building cannot be deleted. Archive it instead."
            )

        counts = await self.buildings.unit_counts(building_id)
        if counts["TOTAL"] > 0:
            raise ConflictException(
                f"This building still holds {counts['TOTAL']} unit(s). Remove them first."
            )

        building.deleted_at = datetime.now(UTC)
        await self.session.commit()

    async def search_buildings(
        self, params: BuildingSearchParams, *, actor: User, page: int, page_size: int
    ) -> tuple[list[Building], int]:
        # Officers see only their own patch; national roles see everything.
        scope = None if is_global_scope(actor.role.value) else actor.jurisdiction_code
        near = (
            (float(params.near_lon), float(params.near_lat))
            if params.near_lat is not None and params.near_lon is not None
            else None
        )
        return await self.buildings.search(
            page=page,
            page_size=page_size,
            q=params.q,
            city=params.city,
            state=params.state,
            status=params.status,
            building_use=params.building_use,
            parcel_ulpin=params.parcel_ulpin,
            jurisdiction_prefix=scope,
            near=near,
            radius_m=params.radius_m,
        )

    # =======================================================================
    # Floors
    # =======================================================================
    async def add_floor(
        self, building_id: uuid.UUID, payload: FloorCreate, *, actor: User
    ) -> Floor:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code)

        if await self.floors.get_by_number(building_id, payload.floor_number):
            raise DuplicateResourceException(
                f"Floor {payload.floor_number} already exists in this building"
            )

        if payload.floor_number >= building.total_floors:
            raise ValidationException(
                f"Floor {payload.floor_number} exceeds the building's {building.total_floors} "
                "floors. Raise total_floors first.",
                errors={"floor_number": ["Above the declared floor count"]},
            )
        if payload.floor_number < -building.basement_floors:
            raise ValidationException(
                f"Basement level {payload.floor_number} exceeds the declared "
                f"{building.basement_floors} basement(s)",
                errors={"floor_number": ["Below the declared basement count"]},
            )

        floor_type = payload.floor_type or FloorType(codec.infer_floor_type(payload.floor_number))
        floor = Floor(
            building_id=building_id,
            floor_number=payload.floor_number,
            floor_type=floor_type,
            storey_code=codec.storey_code(payload.floor_number, floor_type.value),
            floor_label=payload.floor_label or _floor_label(payload.floor_number),
            elevation_base_m=payload.elevation_base_m,
            elevation_top_m=payload.elevation_top_m,
            gross_area_sqm=payload.gross_area_sqm,
            common_area_sqm=payload.common_area_sqm,
        )
        self.floors.add(floor)
        await self.session.commit()
        await self.session.refresh(floor)
        return floor

    async def update_floor(
        self, floor_id: uuid.UUID, payload: FloorUpdate, *, actor: User
    ) -> Floor:
        floor = await self.floors.get(floor_id)
        if floor is None:
            raise NotFoundException("Floor", floor_id)
        building = await self.buildings.get(floor.building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        changes = payload.model_dump(exclude_unset=True, exclude_none=True)
        for field, value in changes.items():
            setattr(floor, field, value)

        if "floor_type" in changes:
            # storey_code is a pure function of (number, type); recompute it or
            # the table's own CHECK constraint rejects the row.
            floor.storey_code = codec.storey_code(floor.floor_number, floor.floor_type.value)

        await self.session.commit()
        await self.session.refresh(floor)
        return floor

    async def list_floors(self, building_id: uuid.UUID, *, actor: User) -> list[Floor]:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        await self._assert_can_read_building(building, actor)
        return await self.floors.list_for_building(building_id)

    # =======================================================================
    # Units
    # =======================================================================
    async def create_unit(
        self, building_id: uuid.UUID, payload: UnitCreate, *, actor: User
    ) -> Unit:
        building, floor = await self._resolve_floor(building_id, payload.floor_number, actor)
        unit = await self._build_unit(building, floor, payload, actor=actor)
        await self.session.commit()
        await self.session.refresh(unit)
        logger.info(
            "unit registered unit_id=%s building_id=%s floor=%s number=%s",
            unit.unit_id,
            building_id,
            payload.floor_number,
            unit.unit_number,
        )
        return unit

    async def create_units_bulk(
        self, building_id: uuid.UUID, payload: UnitBulkCreate, *, actor: User
    ) -> list[Unit]:
        """Register a floor's worth of units atomically.

        All or nothing: a batch that fails on unit nine does not leave eight
        half-registered units behind and a floor counter that disagrees.
        """
        building, floor = await self._resolve_floor(building_id, payload.floor_number, actor)

        created: list[Unit] = []
        for item in payload.units:
            created.append(await self._build_unit(building, floor, item, actor=actor))

        await self.session.commit()
        for unit in created:
            await self.session.refresh(unit)
        logger.info(
            "bulk unit registration building_id=%s floor=%s count=%s",
            building_id,
            payload.floor_number,
            len(created),
        )
        return created

    async def _resolve_floor(
        self, building_id: uuid.UUID, floor_number: int, actor: User
    ) -> tuple[Building, Floor]:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code)

        floor = await self.floors.get_by_number(building_id, floor_number)
        if floor is None:
            raise NotFoundException(
                "Floor", f"{floor_number} of building {building.building_name}"
            )
        return building, floor

    async def _build_unit(
        self,
        building: Building,
        floor: Floor,
        payload: UnitBase | UnitCreate,
        *,
        actor: User,
    ) -> Unit:
        number = payload.unit_number.strip()
        if await self.units.get_by_number(floor.floor_id, number):
            raise DuplicateResourceException(
                f"Unit {number} already exists on floor {floor.floor_number}"
            )

        # next_ordinal reads MAX(unit_ordinal); each _build_unit flushes before
        # returning, so the next call in a bulk loop sees the row just added.
        ordinal = await self.units.next_ordinal(floor.floor_id)
        try:
            unit_code = codec.unit_code(ordinal, floor_number=floor.floor_number)
        except codec.ULPINFormatError as exc:
            raise ValidationException(str(exc)) from exc

        unit = Unit(
            floor_id=floor.floor_id,
            building_id=building.building_id,
            unit_number=number,
            unit_code=unit_code,
            unit_ordinal=ordinal,
            unit_type=payload.property_type,
            unit_status=getattr(payload, "unit_status", UnitStatus.DRAFT),
            occupancy_status=payload.occupancy_status,
            carpet_area_sqm=payload.carpet_area_sqm,
            built_up_area_sqm=payload.built_up_area_sqm,
            super_built_up_area_sqm=payload.super_built_up_area_sqm,
            ceiling_height_m=payload.ceiling_height_m or floor.floor_height_m,
            bedrooms=payload.bedrooms,
            bathrooms=payload.bathrooms,
            balconies=payload.balconies,
            parking_slots=payload.parking_slots,
            created_by=actor.user_id,
        )
        self.units.add(unit)
        await self.session.flush()

        # Registering a unit is also, in this system, declaring where it sits in
        # space: "Register Property -> Automatically create a 3D unit block". The
        # placement happens here rather than in the endpoint so that every path
        # that creates a unit gets one — single, bulk, or the 3D generator's own
        # scaffolding — and no caller has to remember to ask.

        # Imported at call time: the generator imports PropertyService's Building
        # and Floor models, and a module-level import here would close the cycle.
        from app.services.three_d_ulpin_service import ThreeDULPINGenerator

        try:
            await ThreeDULPINGenerator(self.session).locate_unit(
                unit, building=building, floor=floor
            )
        except Exception:
            # A unit that exists without geometry is a survey pending, which the
            # register already models as a valid state. Failing the registration
            # instead would mean a bad footprint could stop a citizen from
            # recording their title at all.
            logger.exception(
                "3d placement failed for unit_id=%s; registered without geometry",
                unit.unit_id,
            )
        return unit

    async def get_unit(self, unit_id: uuid.UUID, *, actor: User) -> Unit:
        unit = await self.units.get_with_relations(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        await self._assert_can_read_unit(unit, actor)
        return unit

    async def update_unit(self, unit_id: uuid.UUID, payload: UnitUpdate, *, actor: User) -> Unit:
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        building = await self.buildings.get(unit.building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        changes = payload.model_dump(exclude_unset=True, exclude_none=True)

        if unit.unit_status is UnitStatus.ACTIVE:
            # Once a unit is live its geometry and areas are frozen; the DB
            # trigger refuses them. Reject here with a readable reason first.
            frozen = {"carpet_area_sqm", "built_up_area_sqm", "super_built_up_area_sqm"}
            attempted = frozen & changes.keys()
            if attempted:
                raise ImmutableRecordException(
                    f"This unit is active; {', '.join(sorted(attempted))} can only be "
                    "changed through a re-survey."
                )

        if "unit_number" in changes:
            clash = await self.units.get_by_number(unit.floor_id, changes["unit_number"])
            if clash is not None and clash.unit_id != unit.unit_id:
                raise DuplicateResourceException(
                    f"Unit {changes['unit_number']} already exists on this floor"
                )

        if "property_type" in changes:
            unit.unit_type = changes.pop("property_type")

        for field, value in changes.items():
            setattr(unit, field, value)

        await self.session.commit()
        await self.session.refresh(unit)
        return unit

    async def set_occupancy(
        self, unit_id: uuid.UUID, status: OccupancyStatus, *, actor: User
    ) -> Unit:
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        building = await self.buildings.get(unit.building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        if status is OccupancyStatus.VACANT:
            current = await self.tenancies.current_for_unit(unit_id)
            if current is not None:
                raise ConflictException(
                    f"This unit has an active tenancy ({current.full_name}, from "
                    f"{current.lease_start}). End it before marking the unit vacant."
                )

        unit.occupancy_status = status
        await self.session.commit()
        await self.session.refresh(unit)
        return unit

    async def delete_unit(self, unit_id: uuid.UUID, *, actor: User) -> None:
        unit = await self.units.get_with_relations(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        building = await self.buildings.get(unit.building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        if unit.unit_status is UnitStatus.ACTIVE:
            raise ImmutableRecordException(
                "An active unit cannot be deleted. Mark it MERGED, SUBDIVIDED or "
                "DEMOLISHED so the identifier's fate is recorded."
            )
        if any(o.ended_on is None for o in unit.ownerships):
            raise ConflictException(
                "This unit still has active ownership records. Transfer or end them first."
            )

        unit.deleted_at = datetime.now(UTC)
        await self.session.commit()

    async def list_units(
        self,
        *,
        actor: User,
        page: int,
        page_size: int,
        building_id: uuid.UUID | None = None,
        floor_number: int | None = None,
        **filters: object,
    ) -> tuple[list[Unit], int]:
        if building_id is not None:
            building = await self.buildings.get(building_id)
            if building is None:
                raise NotFoundException("Building", building_id)
            await self._assert_can_read_building(building, actor)
        return await self.units.search(
            page=page,
            page_size=page_size,
            building_id=building_id,
            floor_number=floor_number,
            **filters,  # type: ignore[arg-type]
        )

    # =======================================================================
    # Owners
    # =======================================================================
    async def create_owner(self, payload: OwnerCreate, *, actor: User) -> Owner:
        id_hash = None
        last4 = None
        if payload.national_id and payload.national_id_type:
            id_hash = hash_national_id(payload.national_id_type, payload.national_id)
            last4 = national_id_last4(payload.national_id)

            duplicate = await self.owners.get_by_national_id_hash(id_hash)
            if duplicate is not None:
                # Not fatal — one person legitimately owns several units — but it
                # is exactly the signal the fraud module watches for.
                logger.warning(
                    "owner created with an identity already on file existing_owner_id=%s",
                    duplicate.owner_id,
                )

        owner = Owner(
            user_id=payload.link_user_id,
            owner_type=payload.owner_type,
            full_name=payload.full_name,
            father_or_spouse_name=payload.father_or_spouse_name,
            date_of_birth=payload.date_of_birth,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            national_id_type=payload.national_id_type,
            national_id_hash=id_hash,
            national_id_last4=last4,
            address_line1=payload.address_line1,
            address_line2=payload.address_line2,
            city=payload.city,
            state=payload.state,
            postal_code=payload.postal_code,
            organisation_name=payload.organisation_name,
            registration_number=payload.registration_number,
        )
        self.owners.add(owner)
        await self.session.commit()
        await self.session.refresh(owner)
        return owner

    async def get_owner(self, owner_id: uuid.UUID, *, actor: User) -> Owner:
        owner = await self.owners.get(owner_id)
        if owner is None:
            raise NotFoundException("Owner", owner_id)
        if (
            owner.user_id != actor.user_id
            and not has_permission(actor.role.value, Permission.OWNER_READ)
        ):
            raise PermissionDeniedException("You may only view your own owner record")
        return owner

    async def update_owner(
        self, owner_id: uuid.UUID, payload: OwnerUpdate, *, actor: User
    ) -> Owner:
        owner = await self.owners.get(owner_id)
        if owner is None:
            raise NotFoundException("Owner", owner_id)

        # An owner may correct their own contact details; changing anyone else's
        # requires the officer permission.
        if owner.user_id != actor.user_id and not has_permission(
            actor.role.value, Permission.OWNER_UPDATE
        ):
            raise PermissionDeniedException("You may only edit your own owner record")

        for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(owner, field, value)

        await self.session.commit()
        await self.session.refresh(owner)
        return owner

    async def search_owners(
        self, *, actor: User, page: int, page_size: int, q: str | None = None
    ) -> tuple[list[Owner], int]:
        return await self.owners.search(page=page, page_size=page_size, q=q)

    # =======================================================================
    # Ownership
    # =======================================================================
    async def assign_ownership(
        self, unit_id: uuid.UUID, payload: OwnershipAssign, *, actor: User
    ) -> list[UnitOwnership]:
        """Record title over a unit.

        The share-sum rule is a DEFERRABLE constraint trigger, so the old and new
        sets may both be present mid-transaction and the database validates only
        at COMMIT. That is what lets a transfer be expressed atomically.
        """
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        building = await self.buildings.get(unit.building_id)
        self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        for share in payload.owners:
            if await self.owners.get(share.owner_id) is None:
                raise NotFoundException("Owner", share.owner_id)

        if payload.replace_existing:
            await self.ownerships.end_active(unit_id, payload.acquired_on)
        else:
            existing = await self.ownerships.active_for_unit(unit_id)
            if existing:
                raise ConflictException(
                    f"This unit already has {len(existing)} active ownership record(s). "
                    "Send replace_existing=true to transfer title."
                )

        created: list[UnitOwnership] = []
        for share in payload.owners:
            ownership = UnitOwnership(
                unit_id=unit_id,
                owner_id=share.owner_id,
                share_fraction=share.share_fraction,
                ownership_mode=payload.ownership_mode,
                is_primary=share.is_primary or len(payload.owners) == 1,
                acquired_on=payload.acquired_on,
                acquisition_mode=payload.acquisition_mode,
                deed_number=payload.deed_number,
                deed_date=payload.deed_date,
                consideration_amount=payload.consideration_amount,
            )
            self.ownerships.add(ownership)
            created.append(ownership)

        await self.session.flush()
        await self.session.commit()

        for row in created:
            await self.session.refresh(row)
        logger.info(
            "ownership assigned unit_id=%s owners=%s by=%s",
            unit_id,
            len(created),
            actor.user_id,
        )
        return created

    async def list_ownership(self, unit_id: uuid.UUID, *, actor: User) -> list[UnitOwnership]:
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        await self._assert_can_read_unit(unit, actor)
        return await self.ownerships.active_for_unit(unit_id)

    # =======================================================================
    # Tenancies
    # =======================================================================
    async def create_tenancy(
        self, unit_id: uuid.UUID, payload: TenantCreate, *, actor: User
    ) -> Tenant:
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        building = await self.buildings.get(unit.building_id)

        # An owner may let their own unit; an officer may record any tenancy in
        # their jurisdiction.
        if not has_permission(actor.role.value, Permission.TENANT_CREATE):
            raise PermissionDeniedException("Your role cannot record tenancies")
        if actor.role is Role.OWNER:
            if not await self.units.is_owned_by_user(unit_id, actor.user_id):
                raise PermissionDeniedException("You do not hold title to this unit")
        else:
            self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        clash = await self.tenancies.overlaps(unit_id, payload.lease_start, payload.lease_end)
        if clash is not None:
            raise ConflictException(
                f"An active tenancy ({clash.full_name}, {clash.lease_start} to "
                f"{clash.lease_end or 'open-ended'}) overlaps these dates"
            )

        id_hash = None
        last4 = None
        if payload.national_id and payload.national_id_type:
            id_hash = hash_national_id(payload.national_id_type, payload.national_id)
            last4 = national_id_last4(payload.national_id)

        # A future-dated lease is PENDING until it starts; a current one is
        # ACTIVE. Only an ACTIVE lease flips the unit to Occupied.
        status = (
            TenancyStatus.ACTIVE if payload.lease_start <= date.today() else TenancyStatus.PENDING
        )
        tenant = Tenant(
            unit_id=unit_id,
            user_id=payload.link_user_id,
            full_name=payload.full_name,
            email=str(payload.email) if payload.email else None,
            phone=payload.phone,
            national_id_type=payload.national_id_type,
            national_id_hash=id_hash,
            national_id_last4=last4,
            lease_start=payload.lease_start,
            lease_end=payload.lease_end,
            monthly_rent=payload.monthly_rent,
            security_deposit=payload.security_deposit,
            agreement_number=payload.agreement_number,
            is_registered_agreement=payload.is_registered_agreement,
            occupant_count=payload.occupant_count,
            status=status,
            notes=payload.notes,
        )
        self.tenancies.add(tenant)

        # Keeping this flag in step here is what makes the Occupied/Vacant figure
        # on the dashboard trustworthy.
        if status is TenancyStatus.ACTIVE:
            unit.occupancy_status = OccupancyStatus.OCCUPIED

        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant

    async def update_tenancy(
        self, tenant_id: uuid.UUID, payload: TenantUpdate, *, actor: User
    ) -> Tenant:
        tenant = await self.tenancies.get(tenant_id)
        if tenant is None:
            raise NotFoundException("Tenancy", tenant_id)

        unit = await self.units.get(tenant.unit_id)
        building = await self.buildings.get(unit.building_id) if unit else None
        if actor.role is Role.OWNER:
            if not await self.units.is_owned_by_user(tenant.unit_id, actor.user_id):
                raise PermissionDeniedException("You do not hold title to this unit")
        elif actor.role is Role.TENANT:
            if tenant.user_id != actor.user_id:
                raise PermissionDeniedException("This is not your tenancy")
        else:
            self._require_jurisdiction(actor, building.jurisdiction_code if building else None)

        changes = payload.model_dump(exclude_unset=True, exclude_none=True)

        if "lease_end" in changes:
            clash = await self.tenancies.overlaps(
                tenant.unit_id, tenant.lease_start, changes["lease_end"], exclude=tenant_id
            )
            if clash is not None:
                raise ConflictException(
                    f"Extending to {changes['lease_end']} collides with {clash.full_name}'s tenancy"
                )

        for field, value in changes.items():
            setattr(tenant, field, value)

        # A tenancy that has just ended releases the unit — unless another active
        # one still covers today.
        if tenant.status in (TenancyStatus.TERMINATED, TenancyStatus.EXPIRED) and unit:
            remaining = await self.tenancies.overlaps(
                tenant.unit_id, date.today(), None, exclude=tenant_id
            )
            if remaining is None:
                unit.occupancy_status = OccupancyStatus.VACANT

        await self.session.commit()
        await self.session.refresh(tenant)
        return tenant

    async def end_tenancy(self, tenant_id: uuid.UUID, *, actor: User) -> Tenant:
        return await self.update_tenancy(
            tenant_id,
            TenantUpdate(status=TenancyStatus.TERMINATED, lease_end=date.today()),
            actor=actor,
        )

    async def get_tenancy(self, tenant_id: uuid.UUID, *, actor: User) -> Tenant:
        tenant = await self.tenancies.get(tenant_id)
        if tenant is None:
            raise NotFoundException("Tenancy", tenant_id)

        # The tenant named on the lease always sees it. Everyone else has to
        # earn it through an interest in the unit.
        if tenant.user_id == actor.user_id:
            return tenant
        unit = await self.units.get(tenant.unit_id)
        if unit is None:
            raise NotFoundException("Unit", tenant.unit_id)
        await self._assert_can_read_unit(unit, actor)
        return tenant

    async def list_tenancies(
        self, unit_id: uuid.UUID, *, actor: User, page: int, page_size: int
    ) -> tuple[list[Tenant], int]:
        unit = await self.units.get(unit_id)
        if unit is None:
            raise NotFoundException("Unit", unit_id)
        await self._assert_can_read_unit(unit, actor)
        return await self.tenancies.list_for_unit(unit_id, page=page, page_size=page_size)

    async def my_tenancies(
        self, *, actor: User, page: int, page_size: int
    ) -> tuple[list[Tenant], int]:
        """The caller's own leases. No scope check — the query is the check."""
        return await self.tenancies.list_for_user(
            actor.user_id, page=page, page_size=page_size
        )

    # =======================================================================
    # Scope checks
    # =======================================================================
    def _require_jurisdiction(self, actor: User, target: str | None) -> None:
        """Assert the actor's authority reaches ``target``.

        ADMIN/AUDITOR/SERVICE pass unconditionally; a PROPERTY_OFFICER scoped to
        ``KA-BLR`` passes for ``KA-BLR-001`` and fails for ``MH-MUM-004``. This
        duplicates ``deps.require_jurisdiction`` deliberately so the service does
        not import from the API layer.
        """
        if not can_access_jurisdiction(actor.role.value, actor.jurisdiction_code, target):
            raise JurisdictionDeniedException(
                f"Your jurisdiction ({actor.jurisdiction_code}) does not cover {target}"
            )

    async def _assert_can_read_building(self, building: Building, actor: User) -> None:
        if is_global_scope(actor.role.value):
            return
        if actor.role is Role.PROPERTY_OFFICER:
            self._require_jurisdiction(actor, building.jurisdiction_code)
            return
        # A registered building is public record; a DRAFT one is visible only to
        # someone with a registered interest in one of its units.
        if building.status is BuildingStatus.REGISTERED:
            return
        if not await self._has_interest_in_building(building.building_id, actor.user_id):
            raise PermissionDeniedException("You have no registered interest in this building")

    async def _has_interest_in_building(
        self, building_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        owned = (
            select(UnitOwnership.ownership_id)
            .join(Unit, Unit.unit_id == UnitOwnership.unit_id)
            .join(Owner, Owner.owner_id == UnitOwnership.owner_id)
            .where(
                Unit.building_id == building_id,
                Unit.deleted_at.is_(None),
                UnitOwnership.ended_on.is_(None),
                Owner.user_id == user_id,
            )
            .exists()
        )
        tenanted = (
            select(Tenant.tenant_id)
            .join(Unit, Unit.unit_id == Tenant.unit_id)
            .where(
                Unit.building_id == building_id,
                Unit.deleted_at.is_(None),
                Tenant.user_id == user_id,
                Tenant.status == TenancyStatus.ACTIVE,
                Tenant.deleted_at.is_(None),
            )
            .exists()
        )
        return bool((await self.session.execute(select(or_(owned, tenanted)))).scalar())

    async def _assert_can_read_unit(self, unit: Unit, actor: User) -> None:
        if is_global_scope(actor.role.value):
            return
        if actor.role is Role.PROPERTY_OFFICER:
            building = await self.buildings.get(unit.building_id)
            self._require_jurisdiction(actor, building.jurisdiction_code if building else None)
            return
        if await self.units.is_owned_by_user(unit.unit_id, actor.user_id):
            return
        if await self.units.is_tenanted_by_user(unit.unit_id, actor.user_id):
            return
        raise PermissionDeniedException("You have no registered interest in this unit")


def _floor_label(number: int) -> str:
    if number < 0:
        return f"Basement {abs(number)}"
    if number == 0:
        return "Ground Floor"
    # 11th/12th/13th are "th"; otherwise the last digit decides.
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix} Floor"


__all__ = ["PropertyService"]
