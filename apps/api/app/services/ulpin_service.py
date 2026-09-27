"""Automatic 3D ULPIN generation.

Format, per specification: ``STATE-CITY-BUILDING-FLOOR-UNIT`` — ``WB-KOL-B001-F03-U301``.

Each minted row carries **two** identifiers and they answer different questions:

``short_code``
    The specified format. Allocated from a per-region counter, human-quotable.

``ulpin_code``
    The parcel-derived long form from :mod:`app.ulpin.codec`, which embeds the
    14-character parcel ULPIN verbatim so existing 2D cadastral systems resolve
    it by slicing the first fourteen characters.

Both are stored, both are uniquely indexed, so "no duplicate ULPINs" holds for
both spellings and neither system has to be taught the other's grammar.

Uniqueness is enforced by the database, not by a pre-flight SELECT. Checking
first and inserting second is a race with a window wide enough to hit in
practice — two officers registering the same building minute-by-minute during a
digitisation drive is the normal case, not the exotic one. The service catches
the integrity error and reports it as a 409.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    ImmutableRecordException,
    NotFoundException,
    PermissionDeniedException,
    ULPINGenerationException,
)
from app.core.permissions import Permission, can_access_jurisdiction, has_permission
from app.models.enums import UlpinStatus, UlpinType
from app.models.property import Building, Floor, Unit
from app.models.ulpin import LIVE_ULPIN_STATUSES, Ulpin
from app.models.user import User
from app.repositories.ulpin_repository import (
    BuildingCodeRepository,
    UlpinRepository,
    UlpinSequenceRepository,
)
from app.schemas.ulpin import (
    UlpinGenerateRequest,
    UlpinGenerateResponse,
    UlpinResponse,
    UlpinSkip,
    UlpinValidateResponse,
)
from app.ulpin import codec
from app.ulpin.shortcode import (
    ShortCodeError,
    city_code,
    compose,
    compose_building,
    is_building_code,
    looks_like_shortcode,
    normalise,
    parse,
    state_code,
    unit_sequence_for,
)


class UlpinService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UlpinRepository(session)
        self.sequences = UlpinSequenceRepository(session)
        self.buildings = BuildingCodeRepository(session)

    # =======================================================================
    # Generation
    # =======================================================================
    async def generate(
        self, payload: UlpinGenerateRequest, *, actor: User
    ) -> UlpinGenerateResponse:
        self._require(actor, Permission.ULPIN_GENERATE)
        if payload.issue:
            self._require(actor, Permission.ULPIN_ISSUE)

        if payload.unit_id is not None:
            unit = await self._load_unit(payload.unit_id)
            building = await self._load_building(unit.building_id, actor=actor)
            units = [unit]
        else:
            building = await self._load_building(payload.building_id, actor=actor)  # type: ignore[arg-type]
            units = await self.repo.uncoded_units(building.building_id)

        # Allocate the building number once for the whole run. Doing it per unit
        # would give every flat in the tower a different building code.
        building_seq, region = await self._ensure_building_code(
            building, dry_run=payload.dry_run
        )

        generated: list[UlpinResponse] = []
        skipped: list[UlpinSkip] = []

        for unit in units:
            existing = await self.repo.get_for_unit(unit.unit_id)
            if existing is not None:
                skipped.append(
                    UlpinSkip(
                        unit_id=unit.unit_id,
                        unit_number=unit.unit_number,
                        reason="Unit already carries a live identifier",
                        existing_short_code=existing.short_code,
                    )
                )
                continue

            # session.get() rather than unit.floor: the relationship is lazy and
            # touching it from async code raises MissingGreenlet. get() consults
            # the identity map first, so it costs nothing when already loaded.
            floor = await self.session.get(Floor, unit.floor_id)
            if floor is None:
                skipped.append(
                    UlpinSkip(
                        unit_id=unit.unit_id,
                        unit_number=unit.unit_number,
                        reason="Unit is not attached to a floor",
                    )
                )
                continue

            try:
                _row, dto = await self._mint(
                    unit=unit,
                    floor=floor,
                    building=building,
                    building_sequence=building_seq,
                    state=region[0],
                    city=region[1],
                    issue=payload.issue,
                    actor=actor,
                    dry_run=payload.dry_run,
                )
            except ShortCodeError as exc:
                skipped.append(
                    UlpinSkip(
                        unit_id=unit.unit_id,
                        unit_number=unit.unit_number,
                        reason=str(exc),
                    )
                )
                continue

            generated.append(dto)

        if not payload.dry_run:
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                raise self._translate_integrity_error(exc) from exc

        return UlpinGenerateResponse(
            generated=generated, skipped=skipped, dry_run=payload.dry_run
        )

    async def _mint(
        self,
        *,
        unit: Unit,
        floor: Floor,
        building: Building,
        building_sequence: int,
        state: str,
        city: str,
        issue: bool,
        actor: User,
        dry_run: bool,
    ) -> tuple[Ulpin, UlpinResponse]:
        unit_seq = unit_sequence_for(
            floor.floor_number,
            unit.unit_ordinal or 1,
            unit.unit_number,
        )
        short = compose(
            state=state,
            city=city,
            building_sequence=building_sequence,
            floor_number=floor.floor_number,
            unit_sequence=unit_seq,
            floor_type=floor.floor_type.value if floor.floor_type else "UPPER",
        )

        # The long form needs the parcel ULPIN. A building registered without one
        # is legitimate during digitisation — the parcel record may not have been
        # located yet — so the short code is minted anyway and the long form is
        # left for the parcel to be attached later.
        long_code: str | None = None
        if building.parcel_ulpin:
            long_code = codec.compose(
                parcel_ulpin=building.parcel_ulpin,
                block_code=building.block_code or "00",
                floor_number=floor.floor_number,
                unit_ordinal=unit.unit_ordinal or unit_seq,
                floor_type=floor.floor_type.value if floor.floor_type else None,
            )

        now = datetime.now(UTC)
        # The primary key is assigned here rather than left to the column's
        # gen_random_uuid() default, so the response can carry the real id
        # without a round trip. The default still covers rows inserted by SQL.
        row = Ulpin(
            ulpin_id=uuid.uuid4(),
            ulpin_code=long_code or short,
            ulpin_type=UlpinType.UNIT_3D,
            status=UlpinStatus.ISSUED if issue else UlpinStatus.PROVISIONAL,
            parent_ulpin=building.parcel_ulpin,
            block_code=building.block_code,
            storey_code=floor.storey_code,
            unit_code=unit.unit_code,
            short_code=short,
            building_short_code=compose_building(
                state=state, city=city, building_sequence=building_sequence
            ),
            state_code=state,
            city_code=city,
            building_id=building.building_id,
            floor_id=floor.floor_id,
            unit_id=unit.unit_id,
            jurisdiction_code=building.jurisdiction_code,
            issued_at=now if issue else None,
            issued_by=actor.user_id if issue else None,
        )

        if not dry_run:
            self.repo.add(row)

        dto = UlpinResponse(
            ulpin_id=row.ulpin_id,
            ulpin_code=row.ulpin_code,
            short_code=row.short_code,
            building_short_code=row.building_short_code,
            state_code=row.state_code,
            city_code=row.city_code,
            ulpin_type=row.ulpin_type,
            status=row.status,
            building_id=row.building_id,
            floor_id=row.floor_id,
            unit_id=row.unit_id,
            jurisdiction_code=row.jurisdiction_code,
            issued_at=row.issued_at,
            created_at=now,
            unit_number=unit.unit_number,
            floor_number=floor.floor_number,
            building_name=building.building_name,
        )
        return row, dto

    async def _ensure_building_code(
        self, building: Building, *, dry_run: bool
    ) -> tuple[int, tuple[str, str]]:
        """Return the building's sequence number and its (state, city) region.

        Idempotent: a building that already has a short code keeps it. Allocating
        a second number for the same building would give two flats in one tower
        different building codes, which is the one thing the format must never do.
        """
        state = state_code(building.state or "")
        city = city_code(building.city or "")

        if building.building_sequence:
            return int(building.building_sequence), (state, city)

        if dry_run:
            # Show what the next number *would* be without consuming it. Reading
            # the counter is safe; incrementing it is what we are avoiding.
            current = await self.sequences.peek(state, city)
            return (current.last_value if current else 0) + 1, (state, city)

        sequence = await self.sequences.next_building_sequence(state, city)
        if sequence > 9999:
            raise ULPINGenerationException(
                f"Building sequence exhausted for {state}-{city}: the four-digit "
                "field allows 9999 buildings per city. Split the city code."
            )

        building.building_sequence = sequence
        building.short_code = compose_building(
            state=state, city=city, building_sequence=sequence
        )
        return sequence, (state, city)

    # =======================================================================
    # Validation and resolution
    # =======================================================================
    async def validate(self, code: str) -> UlpinValidateResponse:
        """Grammar first, existence second, reported separately.

        A malformed string and a well-formed code that is not in the register are
        different problems with different remedies. Collapsing them is how a typo
        gets reported to a citizen as "this property does not exist".
        """
        raw = code.strip()
        result = UlpinValidateResponse(input=raw, is_well_formed=False, exists=False)

        if looks_like_shortcode(raw):
            try:
                normalised = normalise(raw)
                parts = parse(normalised)
            except ShortCodeError as exc:
                result.error = str(exc)
                result.format = "SHORT_CODE"
                return result

            result.normalised = normalised
            result.is_well_formed = True
            result.format = "BUILDING_CODE" if is_building_code(normalised) else "SHORT_CODE"
            result.state_code = parts.state
            result.city_code = parts.city
            result.building_sequence = parts.building_sequence
            result.floor_number = parts.floor_number
            result.floor_type = parts.floor_type
            result.unit_sequence = parts.unit_sequence
        else:
            try:
                normalised = codec.normalise(raw)
                codec.verify(normalised)
            except codec.ULPINFormatError as exc:
                result.error = str(exc)
                return result
            result.normalised = normalised
            result.is_well_formed = True
            result.format = "ULPIN_CODE"

        found = await self.repo.get_by_code(result.normalised or raw)
        if found is not None:
            result.exists = True
            result.ulpin = await self.to_response(found)
        return result

    async def resolve(self, code: str, *, actor: User | None = None) -> Ulpin:
        found = await self.repo.get_by_code(code)
        if found is None:
            # Try again through the normaliser, so " wb kol b001 f03 u301 " works.
            try:
                found = await self.repo.get_by_code(normalise(code))
            except ShortCodeError:
                found = None
        if found is None:
            raise NotFoundException("ULPIN", code)
        if actor is not None:
            self._check_jurisdiction(actor, found.jurisdiction_code)
        return found

    # =======================================================================
    # Lifecycle
    # =======================================================================
    async def issue(self, ulpin_id: uuid.UUID, *, actor: User, remarks: str | None = None) -> Ulpin:
        self._require(actor, Permission.ULPIN_ISSUE)
        row = await self._load(ulpin_id, actor=actor)

        if row.status == UlpinStatus.ISSUED:
            return row  # Idempotent: re-issuing is a no-op, not an error.
        if row.status not in (UlpinStatus.DRAFT, UlpinStatus.PROVISIONAL, UlpinStatus.VERIFIED):
            raise ConflictException(
                f"A {row.status.value} identifier cannot be issued. Supersede it instead."
            )

        row.status = UlpinStatus.ISSUED
        row.issued_at = datetime.now(UTC)
        row.issued_by = actor.user_id
        await self.session.flush()
        return row

    async def supersede(
        self,
        ulpin_id: uuid.UUID,
        *,
        reason: str,
        successor_unit_id: uuid.UUID | None,
        actor: User,
    ) -> tuple[Ulpin, Ulpin]:
        """Retire a code in favour of a new one, keeping the old one resolvable.

        Returns ``(old, new)``. The old row is not deleted and not edited beyond
        its status and supersession timestamp — an issued identifier is part of
        the legal record, and rewriting it would break every document that quotes it.
        """
        self._require(actor, Permission.ULPIN_SUPERSEDE)
        old = await self._load(ulpin_id, actor=actor)

        if old.status in (UlpinStatus.SUPERSEDED, UlpinStatus.RETIRED):
            raise ConflictException(f"This identifier is already {old.status.value.lower()}")

        unit_id = successor_unit_id or old.unit_id
        if unit_id is None:
            raise ULPINGenerationException(
                "Cannot supersede a code with no unit. Name a successor_unit_id."
            )

        unit = await self._load_unit(unit_id)
        building = await self._load_building(unit.building_id, actor=actor)
        floor = await self.session.get(Floor, unit.floor_id)
        if floor is None:
            raise ULPINGenerationException("Successor unit is not attached to a floor")

        sequence, region = await self._ensure_building_code(building, dry_run=False)

        now = datetime.now(UTC)
        old.status = UlpinStatus.SUPERSEDED
        old.superseded_at = now
        old.retirement_reason = reason

        new_row, _dto = await self._mint(
            unit=unit,
            floor=floor,
            building=building,
            building_sequence=sequence,
            state=region[0],
            city=region[1],
            issue=True,
            actor=actor,
            dry_run=False,
        )
        new_row.supersedes_id = old.ulpin_id
        new_row.parent_ulpin_id = old.parent_ulpin_id

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise self._translate_integrity_error(exc) from exc

        return old, new_row

    async def retire(self, ulpin_id: uuid.UUID, *, reason: str, actor: User) -> Ulpin:
        self._require(actor, Permission.ULPIN_RETIRE)
        row = await self._load(ulpin_id, actor=actor)

        if row.status == UlpinStatus.RETIRED:
            return row
        if row.status == UlpinStatus.ISSUED and not has_permission(
            actor.role, Permission.ULPIN_SUPERSEDE
        ):
            raise ImmutableRecordException()

        row.status = UlpinStatus.RETIRED
        row.retired_at = datetime.now(UTC)
        row.retirement_reason = reason
        await self.session.flush()
        return row

    # =======================================================================
    # Reads
    # =======================================================================
    async def list_ulpins(
        self,
        *,
        actor: User,
        page: int,
        page_size: int,
        q: str | None = None,
        status: UlpinStatus | None = None,
        ulpin_type: UlpinType | None = None,
        building_id: uuid.UUID | None = None,
        state: str | None = None,
        city: str | None = None,
    ) -> tuple[list[UlpinResponse], int]:
        self._require(actor, Permission.ULPIN_READ)
        rows, total = await self.repo.search(
            page=page,
            page_size=page_size,
            q=q,
            status=status,
            ulpin_type=ulpin_type,
            building_id=building_id,
            state_code=state,
            city_code=city,
            jurisdiction_prefix=self._jurisdiction_filter(actor),
        )
        return [await self.to_response(r) for r in rows], total

    async def get_by_id(self, ulpin_id: uuid.UUID, *, actor: User) -> UlpinResponse:
        row = await self._load(ulpin_id, actor=actor)
        return await self.to_response(row)

    async def sequences_report(self, *, actor: User) -> list:
        self._require(actor, Permission.ULPIN_READ)
        return await self.sequences.list_all()

    # =======================================================================
    # Internals
    # =======================================================================
    async def to_response(self, row: Ulpin) -> UlpinResponse:
        response = UlpinResponse.model_validate(row)
        unit = row.unit
        if unit is not None:
            response.unit_number = unit.unit_number
        building = row.building
        if building is not None:
            response.building_name = building.building_name
        floor = row.floor
        if floor is not None:
            response.floor_number = floor.floor_number
        return response

    async def _load(self, ulpin_id: uuid.UUID, *, actor: User) -> Ulpin:
        row = await self.repo.get(ulpin_id)
        if row is None:
            raise NotFoundException("ULPIN", ulpin_id)
        self._check_jurisdiction(actor, row.jurisdiction_code)
        return row

    async def _load_unit(self, unit_id: uuid.UUID) -> Unit:
        unit = await self.session.get(Unit, unit_id)
        if unit is None or unit.deleted_at is not None:
            raise NotFoundException("Unit", unit_id)
        return unit

    async def _load_building(self, building_id: uuid.UUID, *, actor: User) -> Building:
        building = await self.buildings.get(building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._check_jurisdiction(actor, building.jurisdiction_code)
        if not building.state or not building.city:
            raise ULPINGenerationException(
                "Building has no state or city recorded, so no region code can be "
                "derived. Complete the address before generating identifiers."
            )
        return building

    @staticmethod
    def _require(actor: User, permission: Permission) -> None:
        if not has_permission(actor.role, permission):
            raise PermissionDeniedException(f"This action requires {permission.value}")

    @staticmethod
    def _check_jurisdiction(actor: User, target: str | None) -> None:
        if not can_access_jurisdiction(actor.role, actor.jurisdiction_code, target):
            raise PermissionDeniedException(
                "This identifier is outside your jurisdiction", code="jurisdiction_denied"
            )

    @staticmethod
    def _jurisdiction_filter(actor: User) -> str | None:
        from app.core.permissions import is_global_scope

        if is_global_scope(actor.role):
            return None
        return actor.jurisdiction_code

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> Exception:
        """Turn a unique-violation into an answer the officer can act on.

        The constraint names are the contract here: they are declared in
        05_modules.sql and appear in the error text, so branching on them is
        stable in a way that parsing the message prose is not.
        """
        text = str(getattr(exc, "orig", exc))
        if "uq_ulpins_short_code" in text:
            return ConflictException(
                "That short code is already in use. This usually means the "
                "building's sequence number was assigned twice — check "
                "GET /ulpins/sequences for the region.",
                code="duplicate_ulpin",
            )
        if "ulpins_ulpin_code_key" in text or "uq_ulpins_code" in text:
            return ConflictException(
                "That ULPIN already exists in the register.", code="duplicate_ulpin"
            )
        if "uq_buildings_short_code" in text:
            return ConflictException(
                "That building code is already in use in this city.", code="duplicate_ulpin"
            )
        return ConflictException("The identifier conflicts with an existing record")
