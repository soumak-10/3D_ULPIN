"""The property verification engine.

Workflow, per specification:

1. Fetch Property
2. Fetch Geometry
3. Validate Ownership
4. Validate Tenant
5. Return Result — one of VERIFIED, PENDING_VERIFICATION, INVALID_CLAIM,
   UNAUTHORIZED_OCCUPANCY.

The single design decision that shapes everything else is the difference between
*missing* and *contradictory*.

A unit whose 3D solid has not been digitised yet is **missing** data. It cannot
be VERIFIED — there is nothing to verify against — but calling it an
INVALID_CLAIM would be an accusation drawn from an empty field in a backlog.
That is PENDING_VERIFICATION.

A unit whose register says one owner and whose claimant says another is
**contradictory**. Something asserted is false. That is INVALID_CLAIM.

So each check carries a ``blocking`` flag: only a blocking failure can produce an
adverse verdict. Non-blocking failures hold the result at PENDING_VERIFICATION
and say exactly what is missing, which is the difference between a citizen who
knows to go and file the missing document and one who has been told their claim
is invalid.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.core.permissions import Permission, can_access_jurisdiction, has_permission
from app.models.enums import (
    OccupancyStatus,
    VerificationOutcome,
    VerificationStatus,
)
from app.models.property import Unit
from app.models.ulpin import Ulpin
from app.models.user import User
from app.models.verification import VerificationRecord
from app.repositories.verification_repository import VerificationRepository
from app.schemas.verification import (
    CHECK_LABELS,
    CheckResult,
    VerificationDecision,
    VerificationRecordResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.services.ulpin_service import UlpinService

# How long a verification stands before it should be repeated. Not a legal
# expiry — the record is permanent — but the point at which the dashboard stops
# counting it as current, because occupancy and tenancy move faster than title.
VERIFICATION_VALID_DAYS = 365


class VerificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = VerificationRepository(session)
        self.ulpins = UlpinService(session)

    # =======================================================================
    # Step 5 orchestrates steps 1-4
    # =======================================================================
    async def verify(self, payload: VerifyRequest, *, actor: User) -> VerifyResponse:
        if not has_permission(actor.role, Permission.VERIFICATION_CREATE):
            raise PermissionDeniedException("This action requires verification:create")

        now = datetime.now(UTC)
        checks: list[CheckResult] = []

        # -- Step 1: Fetch Property ------------------------------------------
        ulpin = await self.ulpins.resolve(payload.ulpin)
        self._check_jurisdiction(actor, ulpin.jurisdiction_code)

        unit = await self._fetch_property(ulpin, checks)
        if unit is None:
            # The identifier exists but names nothing resolvable. Pending, not
            # invalid: a building-level ULPIN legitimately has no unit.
            return self._finish(
                payload=payload,
                ulpin=ulpin,
                unit=None,
                checks=checks,
                outcome=VerificationOutcome.PENDING_VERIFICATION,
                now=now,
                verification_id=None,
            )

        # -- Step 2: Fetch Geometry ------------------------------------------
        await self._fetch_geometry(unit, checks)

        # -- Step 2b: Locate In Space ----------------------------------------
        # Runs on the geometry step's facts rather than re-reading them. A unit
        # whose solid is absent or invalid has nothing to locate, and reporting
        # both a missing solid and a failed location for one cause would read as
        # two defects.
        await self._locate_in_space(unit, checks)

        # -- Step 3: Validate Ownership --------------------------------------
        owner_names = await self._validate_ownership(unit, payload, checks)

        # -- Step 4: Validate Tenant -----------------------------------------
        tenant_name = await self._validate_tenant(unit, payload, checks)

        # -- Step 5: Return Result -------------------------------------------
        outcome = self._decide(checks)

        record_id: uuid.UUID | None = None
        if payload.persist:
            record_id = await self._persist(
                ulpin=ulpin,
                unit=unit,
                payload=payload,
                checks=checks,
                outcome=outcome,
                actor=actor,
                now=now,
            )

        return self._finish(
            payload=payload,
            ulpin=ulpin,
            unit=unit,
            checks=checks,
            outcome=outcome,
            now=now,
            verification_id=record_id,
            owner_names=owner_names,
            tenant_name=tenant_name,
        )

    # =======================================================================
    # Step 1
    # =======================================================================
    async def _fetch_property(self, ulpin: Ulpin, checks: list[CheckResult]) -> Unit | None:
        if ulpin.unit_id is None:
            checks.append(
                self._check(
                    "FETCH_PROPERTY",
                    passed=False,
                    blocking=False,
                    detail="This identifier describes a building or floor, not a unit.",
                    evidence={"ulpin_type": ulpin.ulpin_type.value},
                )
            )
            return None

        unit = await self.repo.unit_with_context(ulpin.unit_id)
        if unit is None:
            # An identifier pointing at a deleted or absent unit is a genuine
            # contradiction in the register, not a gap. Blocking.
            checks.append(
                self._check(
                    "FETCH_PROPERTY",
                    passed=False,
                    blocking=True,
                    detail=(
                        "The identifier resolves to a property record that no longer "
                        "exists. The register is inconsistent."
                    ),
                    evidence={"unit_id": str(ulpin.unit_id)},
                )
            )
            return None

        if not ulpin.is_live:
            checks.append(
                self._check(
                    "FETCH_PROPERTY",
                    passed=False,
                    blocking=False,
                    detail=(
                        f"This identifier has been {ulpin.status.value.lower()}. It still "
                        "resolves for historic reference but no longer identifies the "
                        "property."
                    ),
                    evidence={"status": ulpin.status.value},
                )
            )
            return unit

        checks.append(
            self._check(
                "FETCH_PROPERTY",
                passed=True,
                detail=f"Located unit {unit.unit_number}.",
                evidence={
                    "unit_id": str(unit.unit_id),
                    "unit_number": unit.unit_number,
                    "unit_type": unit.unit_type.value if unit.unit_type else None,
                },
            )
        )
        return unit

    # =======================================================================
    # Step 2
    # =======================================================================
    async def _fetch_geometry(self, unit: Unit, checks: list[CheckResult]) -> None:
        facts = await self.repo.geometry_facts(unit.unit_id)
        building_facts = (
            await self.repo.building_geometry_facts(unit.building_id)
            if unit.building_id
            else {}
        )
        evidence = {**facts, "building": building_facts}

        if not facts:
            checks.append(
                self._check(
                    "FETCH_GEOMETRY",
                    passed=False,
                    blocking=False,
                    detail="No geometry record could be read for this unit.",
                    evidence=evidence,
                )
            )
            return

        # An invalid solid is a contradiction — somebody stored a shape that is
        # not a shape — while an absent one is a backlog. They are not the same
        # failure and must not produce the same verdict.
        if facts.get("stored_valid") is False:
            checks.append(
                self._check(
                    "FETCH_GEOMETRY",
                    passed=False,
                    blocking=True,
                    detail=(
                        "The recorded 3D volume for this unit is not a valid solid. "
                        "The geometry must be corrected before the unit can be verified."
                    ),
                    evidence=evidence,
                )
            )
            return

        if not facts.get("has_solid"):
            checks.append(
                self._check(
                    "FETCH_GEOMETRY",
                    passed=False,
                    blocking=False,
                    detail=(
                        "The 3D volume for this unit has not been digitised yet. "
                        "Verification is on hold pending survey."
                    ),
                    evidence=evidence,
                )
            )
            return

        checks.append(
            self._check(
                "FETCH_GEOMETRY",
                passed=True,
                detail=(
                    f"Valid 3D volume present"
                    + (
                        f" ({facts['volume_cum']:.1f} m³)."
                        if facts.get("volume_cum")
                        else "."
                    )
                ),
                evidence=evidence,
            )
        )

    # =======================================================================
    # Step 2b
    # =======================================================================
    async def _locate_in_space(self, unit: Unit, checks: list[CheckResult]) -> None:
        """Ask where the unit is, not only whether its record is well-formed.

        Steps 1-2 establish that a claim exists and that a solid backs it. This
        step is the vertical dimension's own test: a ULPIN denotes a region of
        space, so a claim is only consistent if that region is inside the
        building it belongs to and does not substantially occupy another unit.

        Two outcomes are deliberately not failures:

        * No footprint surveyed on the building. The register permits a building
          to be recorded before its boundary is digitised, and a check that
          cannot be evaluated must not be reported as a check that failed.
        * A small shared volume. Party walls and a stairwell landing shared by two
          flats are ordinary, so only an overlap large enough to be somebody
          living inside somebody else's flat is treated as blocking.
        """
        from app.services.three_d_ulpin_service import ThreeDULPINGenerator

        gen = ThreeDULPINGenerator(self.session)

        if unit.volume_solid is None:
            checks.append(
                self._check(
                    "LOCATE_IN_SPACE",
                    passed=False,
                    blocking=False,
                    detail=(
                        "This unit has no 3D placement yet, so its position in the "
                        "building cannot be assessed."
                    ),
                    evidence={"placed": False},
                )
            )
            return

        inside = await gen.within_building_footprint(unit)

        if inside is None:
            checks.append(
                self._check(
                    "LOCATE_IN_SPACE",
                    passed=False,
                    blocking=False,
                    detail=(
                        "The building footprint has not been digitised, so the unit's "
                        "position within it cannot be assessed."
                    ),
                    evidence={"placed": True, "footprint_surveyed": False},
                )
            )
            return

        if not inside:
            checks.append(
                self._check(
                    "LOCATE_IN_SPACE",
                    passed=False,
                    blocking=True,
                    detail=(
                        "The recorded 3D volume falls outside the building footprint. "
                        "Either the unit was placed on the wrong building or the "
                        "footprint is out of date."
                    ),
                    evidence={"placed": True, "inside_footprint": False},
                )
            )
            return

        conflicts = await gen.spatial_conflicts(unit)
        own_volume = float(unit.volume_cum) if unit.volume_cum else None
        # Overlap is judged as a share of the unit's own volume, not in cubic
        # metres: 5 m³ is noise on a warehouse and most of a bedsit.
        significant = [
            c for c in conflicts if own_volume and c["overlap_cum"] > 0.05 * own_volume
        ]

        checks.append(
            self._check(
                "LOCATE_IN_SPACE",
                passed=not significant,
                blocking=bool(significant),
                detail=(
                    f"Located within the building footprint"
                    + (
                        f"; overlaps {len(conflicts)} neighbouring "
                        f"unit{'s' if len(conflicts) != 1 else ''}, "
                        f"{len(significant)} substantially."
                        if conflicts
                        else "; no overlap with another unit."
                    )
                    if not significant
                    else (
                        f"The 3D volume substantially overlaps "
                        f"{len(significant)} other unit"
                        f"{'s' if len(significant) != 1 else ''} in the same building."
                    )
                ),
                evidence={
                    "placed": True,
                    "inside_footprint": True,
                    "own_volume_cum": own_volume,
                    "overlaps": conflicts[:10],
                },
            )
        )

    # =======================================================================
    # Step 3
    # =======================================================================
    async def _validate_ownership(
        self, unit: Unit, payload: VerifyRequest, checks: list[CheckResult]
    ) -> list[str]:
        active = [o for o in unit.ownerships if o.ended_on is None]
        owner_names = [
            o.owner.display_name for o in active if o.owner is not None and o.owner.display_name
        ]

        if not active:
            checks.append(
                self._check(
                    "VALIDATE_OWNERSHIP",
                    passed=False,
                    blocking=False,
                    detail="No current owner is recorded against this unit.",
                    evidence={"active_ownerships": 0},
                )
            )
            return owner_names

        share_total = await self.repo.ownership_share_total(unit.unit_id)
        evidence: dict = {
            "active_ownerships": len(active),
            "owners": owner_names,
            "share_total": round(share_total, 6),
        }

        # Shares that do not sum to 1 mean part of the title is unaccounted for.
        # The database defers this constraint to COMMIT so a transfer can run in
        # one transaction; a row that is live and wrong got there another way.
        if abs(share_total - 1.0) > 1e-6:
            checks.append(
                self._check(
                    "VALIDATE_OWNERSHIP",
                    passed=False,
                    blocking=True,
                    detail=(
                        f"Recorded ownership shares total {share_total:.4f}, not 1. "
                        "Part of the title is unaccounted for."
                    ),
                    evidence=evidence,
                )
            )
            return owner_names

        if payload.claimed_owner_id is not None:
            matched = any(o.owner_id == payload.claimed_owner_id for o in active)
            evidence["claimed_owner_id"] = str(payload.claimed_owner_id)
            checks.append(
                self._check(
                    "VALIDATE_OWNERSHIP",
                    passed=matched,
                    blocking=True,
                    detail=(
                        "The claimed owner holds title to this unit."
                        if matched
                        else "The claimed owner does not hold title to this unit."
                    ),
                    evidence=evidence,
                )
            )
            return owner_names

        if payload.claimed_owner_name:
            match = await self.repo.match_owner_name(unit.unit_id, payload.claimed_owner_name)
            evidence["claimed_owner_name"] = payload.claimed_owner_name
            evidence["matched_owner"] = match.display_name if match else None
            checks.append(
                self._check(
                    "VALIDATE_OWNERSHIP",
                    passed=match is not None,
                    blocking=True,
                    detail=(
                        f"Title matches the claimed name ({match.display_name})."
                        if match
                        else (
                            "No owner on this unit's title matches the claimed name. "
                            f"The register shows: {', '.join(owner_names) or 'no named owner'}."
                        )
                    ),
                    evidence=evidence,
                )
            )
            return owner_names

        checks.append(
            self._check(
                "VALIDATE_OWNERSHIP",
                passed=True,
                detail=(
                    f"{len(active)} owner(s) recorded with shares totalling 1: "
                    f"{', '.join(owner_names) or 'unnamed'}."
                ),
                evidence=evidence,
            )
        )
        return owner_names

    # =======================================================================
    # Step 4
    # =======================================================================
    async def _validate_tenant(
        self, unit: Unit, payload: VerifyRequest, checks: list[CheckResult]
    ) -> str | None:
        tenancy = await self.repo.active_tenancy(unit.unit_id)
        active_count = await self.repo.active_tenancy_count(unit.unit_id)
        tenant_name = tenancy.full_name if tenancy else None

        evidence: dict = {
            "active_tenancies": active_count,
            "tenant": tenant_name,
            "occupancy_status": unit.occupancy_status.value if unit.occupancy_status else None,
        }

        # Two live leases on one flat is the classic double-letting. The database
        # has an exclusion constraint against overlapping dates; this catches the
        # case where both are open-ended, which the constraint permits.
        if active_count > 1:
            checks.append(
                self._check(
                    "VALIDATE_TENANT",
                    passed=False,
                    blocking=True,
                    detail=(
                        f"{active_count} concurrent active tenancies are recorded against "
                        "this unit. At most one lease can be live at a time."
                    ),
                    evidence=evidence,
                )
            )
            return tenant_name

        if payload.claimed_tenant_id is not None or payload.claimed_tenant_name:
            if tenancy is None:
                # A tenancy asserted against a unit that has none. If the unit is
                # standing occupied, somebody is in there on the strength of a
                # lease the register does not hold — that is the unauthorized
                # occupancy case. If it is vacant, it is simply a false claim.
                in_occupation = unit.occupancy_status == OccupancyStatus.OCCUPIED
                checks.append(
                    self._check(
                        "VALIDATE_TENANT",
                        passed=False,
                        blocking=True,
                        detail=(
                            "A tenancy is claimed and the unit is occupied, but no lease "
                            "is recorded against it."
                            if in_occupation
                            else (
                                "A tenancy is claimed but no active lease is recorded "
                                "against this unit."
                            )
                        ),
                        evidence={**evidence, "unauthorized_occupancy": in_occupation},
                    )
                )
                return tenant_name

            if payload.claimed_tenant_id is not None:
                matched = tenancy.tenant_id == payload.claimed_tenant_id
            else:
                claimed = (payload.claimed_tenant_name or "").strip().lower()
                matched = claimed in (tenant_name or "").strip().lower()

            evidence["claimed_tenant"] = (
                str(payload.claimed_tenant_id)
                if payload.claimed_tenant_id
                else payload.claimed_tenant_name
            )
            checks.append(
                self._check(
                    "VALIDATE_TENANT",
                    passed=matched,
                    blocking=True,
                    detail=(
                        "The claimed tenant holds the current lease."
                        if matched
                        else "The claimed tenant does not match the recorded lease."
                    ),
                    evidence=evidence,
                )
            )
            return tenant_name

        # No tenancy claimed. The check is then about the register agreeing with
        # itself.
        occupied = unit.occupancy_status == OccupancyStatus.OCCUPIED
        has_owner = any(o.ended_on is None for o in unit.ownerships)

        # An occupied unit with no lease is *not* suspicious on its own: owner
        # occupation is the normal case and the register has no column that
        # records it, so there is nothing here to distinguish "the owner lives
        # here" from "a stranger does". Inferring the latter would flag most
        # owner-occupied flats in the country as fraud.
        #
        # What the register *can* prove is occupation with no recorded right at
        # all — occupied, no active lease, and no live ownership either. Nobody
        # in the record has any claim to be in there. That is the only shape of
        # unauthorized occupancy this data can support, so it is the only one
        # asserted.
        if occupied and tenancy is None and not has_owner:
            checks.append(
                self._check(
                    "VALIDATE_TENANT",
                    passed=False,
                    blocking=True,
                    detail=(
                        "The unit is recorded as occupied but carries neither an active "
                        "tenancy nor a live ownership. No party of record holds a right "
                        "to occupy it."
                    ),
                    evidence={**evidence, "unauthorized_occupancy": True, "active_owners": 0},
                )
            )
            return tenant_name

        if occupied and tenancy is None:
            checks.append(
                self._check(
                    "VALIDATE_TENANT",
                    passed=True,
                    detail=(
                        "No tenancy recorded; the unit is occupied under its ownership "
                        "record."
                    ),
                    evidence={**evidence, "presumed_owner_occupied": True},
                )
            )
            return tenant_name

        if not occupied and tenancy is not None:
            checks.append(
                self._check(
                    "VALIDATE_TENANT",
                    passed=False,
                    blocking=True,
                    detail=(
                        f"The unit is recorded as {unit.occupancy_status.value.lower()} but an "
                        "active tenancy runs against it."
                    ),
                    evidence=evidence,
                )
            )
            return tenant_name

        checks.append(
            self._check(
                "VALIDATE_TENANT",
                passed=True,
                detail=(
                    f"Tenancy consistent: {tenant_name} in occupation."
                    if tenancy
                    else "No tenancy recorded, consistent with the unit's occupancy status."
                ),
                evidence=evidence,
            )
        )
        return tenant_name

    # =======================================================================
    # Step 5
    # =======================================================================
    @staticmethod
    def _decide(checks: list[CheckResult]) -> VerificationOutcome:
        """Map the check results onto the four permitted verdicts.

        Order matters. Unauthorized occupancy is reported specifically rather
        than folded into INVALID_CLAIM, because the remedy is different: one is a
        title dispute, the other is an eviction or a regularisation.
        """
        failed = [c for c in checks if not c.passed]
        blocking = [c for c in failed if c.blocking]

        if not failed:
            return VerificationOutcome.VERIFIED

        if any(
            c.check == "VALIDATE_TENANT" and c.evidence.get("unauthorized_occupancy")
            for c in blocking
        ):
            return VerificationOutcome.UNAUTHORIZED_OCCUPANCY

        if blocking:
            return VerificationOutcome.INVALID_CLAIM

        return VerificationOutcome.PENDING_VERIFICATION

    async def _persist(
        self,
        *,
        ulpin: Ulpin,
        unit: Unit | None,
        payload: VerifyRequest,
        checks: list[CheckResult],
        outcome: VerificationOutcome,
        actor: User,
        now: datetime,
    ) -> uuid.UUID:
        passed = sum(1 for c in checks if c.passed)
        failed = len(checks) - passed

        record = VerificationRecord(
            verification_id=uuid.uuid4(),
            ulpin_id=ulpin.ulpin_id,
            unit_id=unit.unit_id if unit else None,
            building_id=ulpin.building_id,
            verification_type=payload.verification_type,
            # A run that reached a verdict is a completed check, whatever the
            # verdict was. PASSED/FAILED here describes the *check*; the verdict
            # on the property lives in `outcome`. Conflating the two is how "the
            # geometry check failed" comes to read as "this claim is fraudulent".
            status=(
                VerificationStatus.PASSED
                if outcome == VerificationOutcome.VERIFIED
                else VerificationStatus.FAILED
                if outcome != VerificationOutcome.PENDING_VERIFICATION
                else VerificationStatus.PENDING
            ),
            outcome=outcome,
            verified_by=actor.user_id,
            requested_by=actor.user_id,
            requested_at=now,
            verified_at=now if outcome != VerificationOutcome.PENDING_VERIFICATION else None,
            expires_at=(
                now + timedelta(days=VERIFICATION_VALID_DAYS)
                if outcome == VerificationOutcome.VERIFIED
                else None
            ),
            method="RULE_ENGINE_V1",
            confidence=self._confidence(checks),
            checks_passed=passed,
            checks_failed=failed,
            findings={
                "checks": [c.model_dump() for c in checks],
                "outcome": outcome.value,
                "engine": "RULE_ENGINE_V1",
            },
            rejection_reason=(
                next((c.detail for c in checks if not c.passed and c.blocking), None)
                if outcome in (
                    VerificationOutcome.INVALID_CLAIM,
                    VerificationOutcome.UNAUTHORIZED_OCCUPANCY,
                )
                else None
            ),
        )
        self.repo.add(record)

        if unit is not None:
            unit.verification_outcome = outcome
            unit.last_verified_at = now

        await self.session.flush()
        return record.verification_id

    # =======================================================================
    # Officer override
    # =======================================================================
    async def decide(
        self, verification_id: uuid.UUID, payload: VerificationDecision, *, actor: User
    ) -> VerificationRecord:
        if not has_permission(actor.role, Permission.VERIFICATION_DECIDE):
            raise PermissionDeniedException("This action requires verification:decide")

        record = await self.repo.get(verification_id)
        if record is None:
            raise NotFoundException("Verification record", verification_id)

        now = datetime.now(UTC)
        record.status = payload.status
        record.outcome = payload.outcome
        record.verified_by = actor.user_id
        record.verified_at = now
        record.reference_no = payload.reference_no
        record.remarks = payload.remarks
        if payload.expires_in_days:
            record.expires_at = now + timedelta(days=payload.expires_in_days)

        # The engine's finding is kept beside the human's. When they disagree the
        # disagreement is the most useful thing in the record — it is how rule
        # precision gets measured instead of assumed.
        findings = dict(record.findings or {})
        findings["manual_decision"] = {
            "by": str(actor.user_id),
            "at": now.isoformat(),
            "status": payload.status.value,
            "outcome": payload.outcome.value,
            "remarks": payload.remarks,
            "overrode_engine_outcome": findings.get("outcome"),
        }
        record.findings = findings

        if record.unit_id:
            unit = await self.session.get(Unit, record.unit_id)
            if unit is not None:
                unit.verification_outcome = payload.outcome
                unit.last_verified_at = now

        await self.session.flush()
        return record

    # =======================================================================
    # Reads
    # =======================================================================
    async def history(
        self, code: str, *, actor: User, page: int, page_size: int
    ) -> tuple[list[VerificationRecordResponse], int]:
        if not has_permission(actor.role, Permission.VERIFICATION_READ):
            raise PermissionDeniedException("This action requires verification:read")
        ulpin = await self.ulpins.resolve(code, actor=actor)
        rows, total = await self.repo.history_for_ulpin(
            ulpin.ulpin_id, page=page, page_size=page_size
        )
        return [self._record_response(r, ulpin) for r in rows], total

    async def list_records(self, *, actor: User, page: int, page_size: int, **filters):
        if not has_permission(actor.role, Permission.VERIFICATION_READ):
            raise PermissionDeniedException("This action requires verification:read")
        rows, total = await self.repo.search(page=page, page_size=page_size, **filters)
        return [self._record_response(r, r.ulpin) for r in rows], total

    # =======================================================================
    # Internals
    # =======================================================================
    @staticmethod
    def _check(
        name: str,
        *,
        passed: bool,
        detail: str,
        blocking: bool = False,
        evidence: dict | None = None,
    ) -> CheckResult:
        return CheckResult(
            check=name,
            label=CHECK_LABELS.get(name, name),
            passed=passed,
            blocking=blocking,
            detail=detail,
            evidence=evidence or {},
        )

    @staticmethod
    def _confidence(checks: list[CheckResult]) -> Decimal:
        if not checks:
            return Decimal("0")
        passed = sum(1 for c in checks if c.passed)
        return Decimal(passed) / Decimal(len(checks))

    @staticmethod
    def _headline(outcome: VerificationOutcome, checks: list[CheckResult]) -> str:
        first_failure = next((c.detail for c in checks if not c.passed), None)
        return {
            VerificationOutcome.VERIFIED: "Verified. The register is consistent for this property.",
            VerificationOutcome.PENDING_VERIFICATION: (
                first_failure or "Verification is pending further records."
            ),
            VerificationOutcome.INVALID_CLAIM: (
                first_failure or "The claim does not match the register."
            ),
            VerificationOutcome.UNAUTHORIZED_OCCUPANCY: (
                first_failure or "Occupancy is recorded with no supporting right."
            ),
        }[outcome]

    def _finish(
        self,
        *,
        payload: VerifyRequest,
        ulpin: Ulpin,
        unit: Unit | None,
        checks: list[CheckResult],
        outcome: VerificationOutcome,
        now: datetime,
        verification_id: uuid.UUID | None,
        owner_names: list[str] | None = None,
        tenant_name: str | None = None,
    ) -> VerifyResponse:
        passed = sum(1 for c in checks if c.passed)
        return VerifyResponse(
            ulpin=payload.ulpin,
            resolved_code=ulpin.display_code,
            outcome=outcome,
            headline=self._headline(outcome, checks),
            confidence=self._confidence(checks),
            checks=checks,
            checks_passed=passed,
            checks_failed=len(checks) - passed,
            unit_id=unit.unit_id if unit else None,
            building_id=ulpin.building_id,
            floor_number=unit.floor.floor_number if unit and unit.floor else None,
            unit_number=unit.unit_number if unit else None,
            building_name=unit.building.building_name if unit and unit.building else None,
            owner_names=owner_names or [],
            tenant_name=tenant_name,
            occupancy_status=(
                unit.occupancy_status.value if unit and unit.occupancy_status else None
            ),
            verification_id=verification_id,
            verified_at=now,
            expires_at=(
                now + timedelta(days=VERIFICATION_VALID_DAYS)
                if outcome == VerificationOutcome.VERIFIED
                else None
            ),
        )

    @staticmethod
    def _record_response(
        record: VerificationRecord, ulpin: Ulpin | None
    ) -> VerificationRecordResponse:
        response = VerificationRecordResponse.model_validate(record)
        if ulpin is not None:
            response.ulpin_code = ulpin.ulpin_code
            response.short_code = ulpin.short_code
        return response

    @staticmethod
    def _check_jurisdiction(actor: User, target: str | None) -> None:
        if not can_access_jurisdiction(actor.role, actor.jurisdiction_code, target):
            raise PermissionDeniedException(
                "This property is outside your jurisdiction", code="jurisdiction_denied"
            )
