"""3D ULPIN generation and vertical property mapping.

The identifier module already mints ``STATE-CITY-BUILDING-FLOOR-UNIT``. What it
could not do is say *where* that string points: a ULPIN denoted a row, not a
region of space. ``units`` carried three PostGIS columns for the purpose —
``floor_plate`` (POLYGONZ), ``volume_solid`` (POLYHEDRALSURFACEZ) and
``centroid_3d`` (POINTZ) — which the verification engine dutifully read and
nothing ever wrote. This module writes them.

Two representations, deliberately:

*   **The local frame.** ``x_coordinate`` / ``y_coordinate`` are metres east and
    north of the building's own origin, ``z_coordinate`` is metres above its
    ground plane, and width/length/height are the extents of the box. Metres,
    readable, quotable in a deed — this is what the viewer and the property panel
    show a citizen.
*   **The georeferenced solid.** The same box expressed in EPSG:4326 so PostGIS
    can intersect it against parcels, footprints and the flat next door.

Neither is derived from the other at read time. Degrees are a hostile unit for a
5 m wall — round-tripping a 0.3 m balcony through seven decimal places of
longitude loses the very precision a boundary dispute turns on.

Vertical mapping is the point of the exercise. Storey N sits at
``(N - 1) * floor_height``, so floor 1 is at Z = 0, floor 2 at Z = 3, floor 3 at
Z = 6, and two flats on the same landing share a Z while occupying different X.
A surveyed elevation on the floor row is preferred over that arithmetic wherever
one exists, because a real levelling run beats an assumption of uniform storeys —
the formula is the fallback, not the authority.

The box is sized from the registered area rather than stamped out at a fixed
size: given a carpet area, the footprint is the rectangle of that area at the
default aspect ratio. A 30 m² flat and a 200 m² showroom then look as different
in the 3D scene as they are on the register, which is the entire point of
rendering them.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    JurisdictionDeniedException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.core.permissions import Permission, can_access_jurisdiction, has_permission
from app.models.enums import TenancyStatus, UlpinStatus
from app.models.property import Building, Floor, Owner, Tenant, Unit, UnitOwnership
from app.models.ulpin import Ulpin
from app.models.user import User

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Fallback storey height, metres. Indian residential practice is 3.0 m
#: floor-to-floor; only used when the floor row carries no surveyed height.
DEFAULT_FLOOR_HEIGHT_M = Decimal("3.000")

#: Fallback footprint for a unit with no registered area, metres. 5 × 6 = 30 m²,
#: a plausible 1BHK, and the aspect ratio every derived footprint inherits.
DEFAULT_WIDTH_M = Decimal("5.000")
DEFAULT_LENGTH_M = Decimal("6.000")

#: Circulation left between adjacent units, metres. Without a gap the solids of
#: neighbouring flats share a face, and a containment test cannot tell a shared
#: wall from an encroachment.
CORRIDOR_GAP_M = Decimal("1.000")

#: Metres per degree of latitude (WGS84 mean). Longitude is this scaled by
#: cos(latitude), applied per building.
M_PER_DEG_LAT = Decimal("110540")
M_PER_DEG_LON_EQUATOR = Decimal("111320")

#: Degrees carry 7 decimals (~11 mm at the equator); metres carry 3 (1 mm).
_DEG_PLACES = 7
_M_PLACES = 3


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Placement:
    """Where one unit sits, in both frames.

    Frozen because a placement is the *result* of a calculation over a building,
    a floor and a unit's area. Mutating one in isolation would put the scalars
    and the solid out of step, which is the one inconsistency this module exists
    to prevent.
    """

    x: Decimal
    y: Decimal
    z: Decimal
    width: Decimal
    length: Decimal
    height: Decimal
    floor_number: int

    plate_wkt: str
    solid_wkt: str
    centroid_wkt: str

    @property
    def volume_cum(self) -> Decimal:
        return (self.width * self.length * self.height).quantize(Decimal("0.001"))


# --------------------------------------------------------------------------- #
# Pure geometry
# --------------------------------------------------------------------------- #
# Kept free of the session and the ORM: given the same building, floor and unit
# these return the same strings on any machine, which is what makes a geometry
# bug reproducible from a failing row rather than only from a live database.


def grid_slot(index: int, siblings: int) -> tuple[int, int]:
    """Column and row for the ``index``-th unit of ``siblings`` on a landing.

    Row-major over a near-square grid, so two units share a landing side by side
    and eight form a 3 × 3 block with one gap. Square rather than a single long
    row because a 40-unit floor laid out in a line renders as a wall a kilometre
    wide and nothing in the scene is legible.
    """
    if siblings < 1:
        raise ValidationException("A floor must hold at least one unit to place")
    cols = max(1, math.ceil(math.sqrt(siblings)))
    return index % cols, index // cols


def footprint_for(
    carpet_area_sqm: Decimal | None,
    *,
    width: Decimal | None = None,
    length: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Width and length in metres, from an explicit override or the area.

    An explicitly surveyed width and length always win. Otherwise the registered
    carpet area is turned into a rectangle at the default 5:6 ratio, so the
    rendered box encloses the area the register actually claims.
    """
    if width is not None and length is not None:
        return _q(width, _M_PLACES), _q(length, _M_PLACES)

    if not carpet_area_sqm or carpet_area_sqm <= 0:
        return DEFAULT_WIDTH_M, DEFAULT_LENGTH_M

    ratio = DEFAULT_WIDTH_M / DEFAULT_LENGTH_M  # 5/6
    w = Decimal(math.sqrt(float(carpet_area_sqm * ratio)))
    if w <= 0:
        return DEFAULT_WIDTH_M, DEFAULT_LENGTH_M
    return _q(w, _M_PLACES), _q(carpet_area_sqm / w, _M_PLACES)


def storey_elevation(floor: Floor) -> Decimal:
    """Z of a floor's slab, metres above the building ground plane.

    Prefers a surveyed elevation. Falls back to ``(floor_number - 1) * height``,
    which yields the specified ladder — floor 1 at 0, floor 2 at 3, floor 3 at 6 —
    and extends correctly downwards, putting the first basement at -3.
    """
    if floor.elevation_base_m is not None:
        return _q(floor.elevation_base_m, _M_PLACES)
    height = floor.floor_height_m or DEFAULT_FLOOR_HEIGHT_M
    return _q(Decimal(floor.floor_number - 1) * height, _M_PLACES)


def _q(value: Decimal | float | int, places: int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places))


def _to_wgs84(
    base_lat: Decimal, base_lon: Decimal, dx: Decimal, dy: Decimal
) -> tuple[Decimal, Decimal]:
    """Offset a lat/lon origin by ``dx`` metres east and ``dy`` metres north.

    A local tangent-plane approximation, which over the tens of metres a building
    spans is accurate to well under the 11 mm the stored precision can express.
    Using it avoids making every write depend on a projected SRS lookup.
    """
    # cos(90°) is 0 and would divide by zero. No building is surveyed at the pole,
    # so clamping is a guard against corrupt input, not a supported location.
    lat_f = max(-89.9, min(89.9, float(base_lat)))
    m_per_deg_lon = M_PER_DEG_LON_EQUATOR * Decimal(str(math.cos(math.radians(lat_f))))

    lon = base_lon + (dx / m_per_deg_lon)
    lat = base_lat + (dy / M_PER_DEG_LAT)
    return _q(lat, _DEG_PLACES), _q(lon, _DEG_PLACES)


def build_geometry(
    *,
    base_lat: Decimal,
    base_lon: Decimal,
    x: Decimal,
    y: Decimal,
    z: Decimal,
    width: Decimal,
    length: Decimal,
    height: Decimal,
) -> tuple[str, str, str]:
    """WKT for the unit's floor plate, closed solid and centroid.

    The solid is written out face by face rather than extruded by SFCGAL. Both
    produce the same box, but an explicit POLYHEDRALSURFACE is portable to any
    PostGIS build and keeps the winding order under this module's control —
    ``CG_IsSolid`` rejects a shell whose normals disagree, and debugging that
    through an extrusion is far harder than reading six rings.
    """
    half_w, half_l = width / 2, length / 2
    z0, z1 = z, z + height

    # Corners of the footprint, counter-clockwise seen from above.
    corners_m = [
        (x - half_w, y - half_l),  # A  south-west
        (x + half_w, y - half_l),  # B  south-east
        (x + half_w, y + half_l),  # C  north-east
        (x - half_w, y + half_l),  # D  north-west
    ]
    # (lon, lat) per corner — WKT is X Y, which for geographic coordinates is
    # longitude first. Reversing these is the classic silent GIS defect: the
    # building lands in the Indian Ocean and every test still passes.
    corners = [_to_wgs84(base_lat, base_lon, dx, dy)[::-1] for dx, dy in corners_m]
    a, b, c, d = corners

    def ring(points: list[tuple[Decimal, Decimal]], height_z: Decimal) -> str:
        closed = [*points, points[0]]
        return ", ".join(f"{lon} {lat} {_q(height_z, _M_PLACES)}" for lon, lat in closed)

    def wall(p: tuple[Decimal, Decimal], q: tuple[Decimal, Decimal]) -> str:
        """One vertical face, as a closed ring rising from p→q and back down."""
        zb, zt = _q(z0, _M_PLACES), _q(z1, _M_PLACES)
        pts = [
            f"{p[0]} {p[1]} {zb}",
            f"{q[0]} {q[1]} {zb}",
            f"{q[0]} {q[1]} {zt}",
            f"{p[0]} {p[1]} {zt}",
            f"{p[0]} {p[1]} {zb}",
        ]
        return ", ".join(pts)

    plate = f"POLYGON Z (({ring([a, b, c, d], z0)}))"

    # Outward normals: the base wound clockwise (reversed) so it faces down, the
    # roof counter-clockwise so it faces up, and the four walls in a consistent
    # circuit. Together they close the shell.
    faces = [
        ring([a, d, c, b], z0),  # floor, normal -Z
        ring([a, b, c, d], z1),  # ceiling, normal +Z
        wall(a, b),  # south
        wall(b, c),  # east
        wall(c, d),  # north
        wall(d, a),  # west
    ]
    solid = "POLYHEDRALSURFACE Z (" + ", ".join(f"(({f}))" for f in faces) + ")"

    # The centroid is the middle of the volume, not the middle of the slab: it is
    # used to answer "which unit is at this point in space", and half the answers
    # would be a metre below the floor otherwise.
    c_lat, c_lon = _to_wgs84(base_lat, base_lon, x, y)
    centroid = f"POINT Z ({c_lon} {c_lat} {_q(z + height / 2, _M_PLACES)})"

    return plate, solid, centroid


def place_unit(
    *,
    building: Building,
    floor: Floor,
    unit: Unit,
    index: int,
    siblings: int,
    width: Decimal | None = None,
    length: Decimal | None = None,
    height: Decimal | None = None,
) -> Placement:
    """Compute a unit's full 3D placement. No I/O; safe to call in a loop."""
    w, length_m = footprint_for(unit.carpet_area_sqm, width=width, length=length)
    h = _q(
        height
        or unit.ceiling_height_m
        or floor.floor_height_m
        or DEFAULT_FLOOR_HEIGHT_M,
        _M_PLACES,
    )
    if h <= 0:
        raise ValidationException("Unit height must be greater than zero")

    col, row = grid_slot(index, siblings)
    cols = max(1, math.ceil(math.sqrt(siblings)))
    rows = math.ceil(siblings / cols)

    pitch_x, pitch_y = w + CORRIDOR_GAP_M, length_m + CORRIDOR_GAP_M
    # Centre the block on the building origin so the tower grows symmetrically
    # about its own coordinates instead of drifting north-east as floors are
    # added.
    span_x = Decimal(cols) * pitch_x - CORRIDOR_GAP_M
    span_y = Decimal(rows) * pitch_y - CORRIDOR_GAP_M
    x = _q(Decimal(col) * pitch_x - span_x / 2 + w / 2, _M_PLACES)
    y = _q(Decimal(row) * pitch_y - span_y / 2 + length_m / 2, _M_PLACES)
    z = storey_elevation(floor)

    plate, solid, centroid = build_geometry(
        base_lat=building.latitude,
        base_lon=building.longitude,
        x=x,
        y=y,
        z=z,
        width=w,
        length=length_m,
        height=h,
    )
    return Placement(
        x=x,
        y=y,
        z=z,
        width=w,
        length=length_m,
        height=h,
        floor_number=floor.floor_number,
        plate_wkt=plate,
        solid_wkt=solid,
        centroid_wkt=centroid,
    )


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class ThreeDULPINGenerator:
    """Assigns every ULPIN a volume, and reads volumes back out.

    Deliberately does not mint identifiers itself — ``UlpinService`` owns the
    per-region counters and the uniqueness guarantee, and a second minting path
    is how a register ends up with two units holding one code. This service
    places units in space; the endpoint composes the two.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- generation ---------------------------------------------------------

    async def locate_unit(
        self,
        unit: Unit,
        *,
        building: Building | None = None,
        floor: Floor | None = None,
        width: Decimal | None = None,
        length: Decimal | None = None,
        height: Decimal | None = None,
        overwrite: bool = True,
    ) -> Placement | None:
        """Place one unit and write both representations onto the row.

        Returns ``None`` when the unit is already located and ``overwrite`` is
        false, which is what makes a bulk backfill safe to re-run: a re-survey
        must be an explicit act, not a side effect of regenerating a neighbour.

        Flushes but does not commit. The caller owns the transaction, so a
        building generated in one request either lands whole or not at all.
        """
        if unit.volume_solid is not None and not overwrite:
            return None

        building = building or await self.session.get(Building, unit.building_id)
        if building is None:
            raise NotFoundException("Building", unit.building_id)
        floor = floor or await self.session.get(Floor, unit.floor_id)
        if floor is None:
            raise NotFoundException("Floor", unit.floor_id)

        index, siblings = await self._slot_on_floor(unit)
        placement = place_unit(
            building=building,
            floor=floor,
            unit=unit,
            index=index,
            siblings=siblings,
            width=width,
            length=length,
            height=height,
        )

        unit.x_coordinate = placement.x
        unit.y_coordinate = placement.y
        unit.z_coordinate = placement.z
        unit.width_m = placement.width
        unit.length_m = placement.length
        unit.height_m = placement.height
        unit.floor_number = placement.floor_number
        unit.volume_cum = placement.volume_cum

        # ST_GeomFromEWKT parses the SRID-qualified literal; assigning raw WKT to
        # a typed geometry column leaves the SRID at 0 and every spatial
        # predicate against a 4326 footprint then fails on mixed SRID.
        unit.floor_plate = func.ST_GeomFromEWKT(f"SRID=4326;{placement.plate_wkt}")
        unit.volume_solid = func.ST_GeomFromEWKT(f"SRID=4326;{placement.solid_wkt}")
        unit.centroid_3d = func.ST_GeomFromEWKT(f"SRID=4326;{placement.centroid_wkt}")

        await self.session.flush()
        return placement

    async def locate_building(
        self,
        building_id: uuid.UUID,
        *,
        actor: User,
        overwrite: bool = True,
    ) -> list[tuple[Unit, Placement]]:
        """Place every unit in a building, storey by storey.

        Ordered by floor then ordinal so the grid slot a unit receives is stable:
        regenerating a building must not shuffle flat 302 into flat 301's corner.
        """
        building = await self._load_building(building_id, actor=actor)
        rows = (
            (
                await self.session.execute(
                    select(Unit, Floor)
                    .join(Floor, Floor.floor_id == Unit.floor_id)
                    .where(Unit.building_id == building_id)
                    .order_by(Floor.floor_number, Unit.unit_ordinal, Unit.unit_number)
                )
            )
            .all()
        )
        if not rows:
            raise ValidationException(
                f"Building {building.building_name} has no registered units to place"
            )

        placed: list[tuple[Unit, Placement]] = []
        for unit, floor in rows:
            placement = await self.locate_unit(
                unit, building=building, floor=floor, overwrite=overwrite
            )
            if placement is not None:
                placed.append((unit, placement))

        logger.info(
            "3d placement building_id=%s units=%s placed=%s",
            building_id,
            len(rows),
            len(placed),
        )
        return placed

    # -- reads --------------------------------------------------------------

    async def unit_with_context(
        self, unit_id: uuid.UUID, *, actor: User
    ) -> tuple[Unit, Floor, Building]:
        row = (
            await self.session.execute(
                select(Unit, Floor, Building)
                .join(Floor, Floor.floor_id == Unit.floor_id)
                .join(Building, Building.building_id == Unit.building_id)
                .where(Unit.unit_id == unit_id)
            )
        ).first()
        if row is None:
            raise NotFoundException("Unit", unit_id)
        unit, floor, building = row
        self._check_jurisdiction(actor, building.jurisdiction_code)
        return unit, floor, building

    async def building_units(
        self, building_id: uuid.UUID, *, actor: User
    ) -> tuple[Building, list[tuple[Unit, Floor]]]:
        building = await self._load_building(building_id, actor=actor)
        rows = (
            await self.session.execute(
                select(Unit, Floor)
                .join(Floor, Floor.floor_id == Unit.floor_id)
                .where(Unit.building_id == building_id)
                .order_by(Floor.floor_number, Unit.unit_ordinal, Unit.unit_number)
            )
        ).all()
        return building, [(u, f) for u, f in rows]

    # -- spatial checks used by verification --------------------------------

    async def spatial_conflicts(self, unit: Unit) -> list[dict]:
        """Units whose volume genuinely overlaps this one's.

        ``&&&`` is the n-dimensional bounding-box operator, so it is index-backed
        by ``idx_units_volume_solid_nd`` and — unlike the 2D ``&&`` — does not
        report every flat in the column as a conflict simply because they share a
        footprint. The exact ``ST_3DIntersects`` runs only on what survives.

        An overlap is not automatically fraud: adjacent solids may abut along a
        party wall. It is a finding for the verification engine to weigh, which
        is why this returns rows rather than a verdict.
        """
        if unit.volume_solid is None:
            return []

        other = Unit.__table__.alias("other")
        stmt = (
            select(
                other.c.unit_id,
                other.c.unit_number,
                other.c.floor_number,
                func.ST_Volume(
                    func.ST_3DIntersection(Unit.volume_solid, other.c.volume_solid)
                ).label("overlap_cum"),
            )
            .where(
                other.c.unit_id != unit.unit_id,
                other.c.volume_solid.is_not(None),
                Unit.unit_id == unit.unit_id,
                other.c.volume_solid.op("&&&")(Unit.volume_solid),
                func.ST_3DIntersects(Unit.volume_solid, other.c.volume_solid),
            )
            .limit(20)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "unit_id": r.unit_id,
                "unit_number": r.unit_number,
                "floor_number": r.floor_number,
                "overlap_cum": float(r.overlap_cum or 0),
            }
            for r in rows
        ]

    async def within_building_footprint(self, unit: Unit) -> bool | None:
        """Whether the unit's plate falls inside its building's footprint.

        ``None`` means unanswerable — no footprint was surveyed for the building —
        which the verification engine must report as such rather than treating a
        missing polygon as a failed containment test.
        """
        if unit.volume_solid is None:
            return None
        building = await self.session.get(Building, unit.building_id)
        if building is None or building.footprint is None:
            return None

        result = await self.session.execute(
            select(
                func.ST_Covers(
                    func.ST_Force2D(Building.footprint),
                    func.ST_Force2D(Unit.floor_plate),
                )
            ).where(
                Building.building_id == unit.building_id,
                Unit.unit_id == unit.unit_id,
            )
        )
        return result.scalar_one_or_none()

    # -- helpers ------------------------------------------------------------

    async def _slot_on_floor(self, unit: Unit) -> tuple[int, int]:
        """This unit's 0-based position on its landing, and the landing's size.

        Derived from the stored ordinals rather than from the order rows happen
        to come back in, so a placement is reproducible.
        """
        rows = (
            (
                await self.session.execute(
                    select(Unit.unit_id)
                    .where(Unit.floor_id == unit.floor_id)
                    .order_by(Unit.unit_ordinal, Unit.unit_number)
                )
            )
            .scalars()
            .all()
        )
        ids = list(rows)
        siblings = max(len(ids), 1)
        index = ids.index(unit.unit_id) if unit.unit_id in ids else 0
        return index, siblings

    # -- Batch lookups for the viewer ---------------------------------------
    # One query per concern for a whole building, not per unit. The 3D scene
    # asks for every unit of a tower at once; the per-unit shape of these was
    # what made clicking a flat cost a round trip.

    async def ulpin_codes(
        self, unit_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[str | None, str | None]]:
        """Map each unit to its live ``(short_code, ulpin_code)``.

        Superseded and retired codes are excluded: a unit whose identifier was
        replaced after a subdivision still has the old row, and showing it on the
        3D panel would hand someone a code that no longer denotes their property.
        """
        if not unit_ids:
            return {}
        rows = await self.session.execute(
            select(Ulpin.unit_id, Ulpin.short_code, Ulpin.ulpin_code)
            .where(
                Ulpin.unit_id.in_(unit_ids),
                Ulpin.status.notin_((UlpinStatus.SUPERSEDED, UlpinStatus.RETIRED)),
            )
            .order_by(Ulpin.created_at.desc())
        )
        out: dict[uuid.UUID, tuple[str | None, str | None]] = {}
        for unit_id, short, full in rows:
            # Newest first, so the first row wins and later ones are ignored.
            out.setdefault(unit_id, (short, full))
        return out

    async def parties(
        self, unit_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[str | None, str | None]]:
        """Map each unit to ``(owner_name, tenant_name)`` for display.

        Names only. The panel shows who holds and who occupies; the party records
        themselves belong to the ownership and tenancy endpoints, which apply
        their own disclosure rules.
        """
        if not unit_ids:
            return {}

        owner_rows = await self.session.execute(
            select(
                UnitOwnership.unit_id,
                Owner.organisation_name,
                Owner.full_name,
                UnitOwnership.is_primary,
            )
            .join(Owner, Owner.owner_id == UnitOwnership.owner_id)
            .where(
                UnitOwnership.unit_id.in_(unit_ids),
                # A closed title is history, not the current holder.
                UnitOwnership.ended_on.is_(None),
            )
            # Primary holder first, so a jointly held flat names the party the
            # register treats as principal rather than whichever row sorted first.
            .order_by(UnitOwnership.is_primary.desc())
        )
        owners: dict[uuid.UUID, str] = {}
        for unit_id, org, full, _primary in owner_rows:
            name = org or full
            if name:
                owners.setdefault(unit_id, name)

        tenant_rows = await self.session.execute(
            select(Tenant.unit_id, Tenant.full_name).where(
                Tenant.unit_id.in_(unit_ids),
                Tenant.status == TenancyStatus.ACTIVE,
            )
        )
        tenants: dict[uuid.UUID, str] = {}
        for unit_id, full in tenant_rows:
            if full:
                tenants.setdefault(unit_id, full)

        return {
            unit_id: (owners.get(unit_id), tenants.get(unit_id)) for unit_id in unit_ids
        }

    async def solids_geojson(
        self, unit_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict]:
        """Map each unit to its solid as parsed GeoJSON.

        ``ST_AsGeoJSON`` is done in the database rather than by loading WKB and
        converting in Python: PostGIS emits the coordinate array directly, and the
        alternative pulls a geometry parser into a service that otherwise only
        needs numbers.
        """
        if not unit_ids:
            return {}
        rows = await self.session.execute(
            select(Unit.unit_id, func.ST_AsGeoJSON(Unit.volume_solid)).where(
                Unit.unit_id.in_(unit_ids), Unit.volume_solid.is_not(None)
            )
        )
        out: dict[uuid.UUID, dict] = {}
        for unit_id, raw in rows:
            if not raw:
                continue
            try:
                out[unit_id] = json.loads(raw)
            except json.JSONDecodeError:
                # A geometry that will not serialise is a data fault worth a log
                # line, but it must not take down a request for 32 other units.
                logger.warning("unit %s has a solid that will not serialise", unit_id)
        return out

    async def _load_building(self, building_id: uuid.UUID, *, actor: User) -> Building:
        building = await self.session.get(Building, building_id)
        if building is None:
            raise NotFoundException("Building", building_id)
        self._check_jurisdiction(actor, building.jurisdiction_code)
        return building

    @staticmethod
    def require_generate(actor: User) -> None:
        """Placing a unit in space is a registration act, not a read."""
        if not has_permission(actor.role, Permission.ULPIN_GENERATE):
            raise PermissionDeniedException(
                "Generating 3D placements requires ulpin:generate"
            )

    @staticmethod
    def _check_jurisdiction(actor: User, target: str | None) -> None:
        # role.value, matching PropertyService._require_jurisdiction: the helper
        # accepts either, and passing the same shape everywhere keeps one idiom.
        if not can_access_jurisdiction(actor.role.value, actor.jurisdiction_code, target):
            raise JurisdictionDeniedException(
                f"Your jurisdiction ({actor.jurisdiction_code}) does not cover {target}"
            )


__all__ = [
    "CORRIDOR_GAP_M",
    "DEFAULT_FLOOR_HEIGHT_M",
    "DEFAULT_LENGTH_M",
    "DEFAULT_WIDTH_M",
    "Placement",
    "ThreeDULPINGenerator",
    "build_geometry",
    "footprint_for",
    "grid_slot",
    "place_unit",
    "storey_elevation",
]
