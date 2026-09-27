-- =============================================================================
--  06_3d_ulpin.sql — vertical property mapping: coordinates, dimensions, indexes
--
--  The 3D columns that already existed on `units` (floor_plate POLYGONZ,
--  volume_solid POLYHEDRALSURFACEZ, centroid_3d POINTZ) were declared, read by
--  the verification engine, and never written by anything. A ULPIN was therefore
--  a string with no volume behind it. This file adds what was missing so a
--  ULPIN can denote an actual region of space:
--
--    * the scalar frame — x/y/z offsets and width/length/height in metres, the
--      numbers a citizen or a registrar can read off a deed without a GIS client
--    * floor_number denormalised onto the unit, so the vertical axis can be
--      queried and indexed without joining floors
--    * the spatial indexes the geometry columns never got (all three were
--      declared spatial_index=False on the ORM model)
--
--  On the two representations, and why both are kept:
--
--    The scalars are a LOCAL building frame — metres east (x), metres north (y)
--    and metres above the building's ground plane (z), origin at the building
--    footprint centroid. They are exact, unit-bearing, and what the 3D viewer
--    and the property panel display.
--
--    The geometry columns are the same box GEOREFERENCED in EPSG:4326, so it can
--    be intersected against parcels, footprints and neighbouring units by
--    PostGIS. Degrees are a poor unit for a 5 m wall, which is precisely why the
--    scalars are not derived from them at read time.
--
--  `geometry_3d` in the API is this schema's `volume_solid`. A second solid
--  column would have meant two answers to "where is this unit", and the
--  verification engine already reads volume_solid; the name is mapped in the
--  response model rather than duplicated in storage.
--
--  Idempotent — ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
--  throughout, so it is safe to re-run over a populated database. Run as a
--  superuser from the repository root:
--
--    psql -U postgres -d ulpin_db -f database/schemas/06_3d_ulpin.sql
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- ---------------------------------------------------------------------------
-- Scalar frame on units
-- ---------------------------------------------------------------------------
-- Nullable by design: a unit may be registered from a paper record before it is
-- surveyed, and a NULL here is an honest "not yet located" that the viewer can
-- report. It is not a default of zero, which would place the unit at the
-- building origin and look like real data.
ALTER TABLE units
    ADD COLUMN IF NOT EXISTS x_coordinate numeric(10, 3),
    ADD COLUMN IF NOT EXISTS y_coordinate numeric(10, 3),
    ADD COLUMN IF NOT EXISTS z_coordinate numeric(10, 3),
    ADD COLUMN IF NOT EXISTS width_m      numeric(7, 3),
    ADD COLUMN IF NOT EXISTS length_m     numeric(7, 3),
    ADD COLUMN IF NOT EXISTS height_m     numeric(6, 3),
    ADD COLUMN IF NOT EXISTS floor_number smallint;

COMMENT ON COLUMN units.x_coordinate IS
    'Metres east of the building footprint centroid (local building frame).';
COMMENT ON COLUMN units.y_coordinate IS
    'Metres north of the building footprint centroid (local building frame).';
COMMENT ON COLUMN units.z_coordinate IS
    'Metres above the building ground plane. Floor 1 = 0, and each storey adds '
    'its floor height, so storey N sits at (N-1) * floor_height.';
COMMENT ON COLUMN units.width_m  IS 'Extent along local x, metres.';
COMMENT ON COLUMN units.length_m IS 'Extent along local y, metres.';
COMMENT ON COLUMN units.height_m IS 'Floor-to-ceiling extent along z, metres.';
COMMENT ON COLUMN units.floor_number IS
    'Denormalised from floors.floor_number. The vertical axis of every query in '
    'this system; joining floors to filter one storey was the common case.';

-- The dimensions describe a physical room, so reject the impossible rather than
-- storing a unit with negative width and letting the viewer draw it inside out.
-- NOT VALID: existing rows are not re-checked, because the pre-existing 32 units
-- are backfilled by the generator immediately after this file runs, and a
-- blocking validation here would fail on their NULLs first.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'units_3d_extent_positive'
    ) THEN
        ALTER TABLE units
            ADD CONSTRAINT units_3d_extent_positive CHECK (
                (width_m  IS NULL OR width_m  > 0) AND
                (length_m IS NULL OR length_m > 0) AND
                (height_m IS NULL OR height_m > 0)
            ) NOT VALID;
    END IF;
END
$$;

-- floor_number must agree with the floor the unit actually sits on. Keeping a
-- denormalised copy honest is the trigger's job, not the caller's: a unit moved
-- to another floor must not keep the old storey number and silently render one
-- level down in the 3D scene.
CREATE OR REPLACE FUNCTION fn_units_sync_floor_number() RETURNS trigger AS $$
BEGIN
    SELECT f.floor_number INTO NEW.floor_number
      FROM floors f
     WHERE f.floor_id = NEW.floor_id;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_units_sync_floor_number ON units;
CREATE TRIGGER trg_units_sync_floor_number
    BEFORE INSERT OR UPDATE OF floor_id ON units
    FOR EACH ROW EXECUTE FUNCTION fn_units_sync_floor_number();

-- Backfill the copy for rows that predate the trigger.
UPDATE units u
   SET floor_number = f.floor_number
  FROM floors f
 WHERE f.floor_id = u.floor_id
   AND u.floor_number IS DISTINCT FROM f.floor_number;

-- ---------------------------------------------------------------------------
-- Spatial indexes
-- ---------------------------------------------------------------------------
-- The three geometry columns were created with spatial_index=False and never
-- indexed. Every spatial predicate the verification engine runs — does this
-- unit's solid fall inside its building footprint, does it intersect a
-- neighbour — was therefore a sequential scan over every unit in the country.
--
-- GIST on a 3D geometry indexes the 2D bounding box, which is the correct
-- primary filter: it eliminates all units outside the footprint cheaply, and
-- the Z comparison then discriminates between storeys on the surviving handful.
CREATE INDEX IF NOT EXISTS idx_units_volume_solid_gist ON units USING GIST (volume_solid);
CREATE INDEX IF NOT EXISTS idx_units_floor_plate_gist  ON units USING GIST (floor_plate);
CREATE INDEX IF NOT EXISTS idx_units_centroid_3d_gist   ON units USING GIST (centroid_3d);

-- ND variant: an overlap test between two storeys of the same tower has an
-- identical 2D footprint, so the 2D box cannot separate them. gist_geometry_ops_nd
-- indexes Z as a third dimension and makes "same column, different storey" a
-- cheap index probe rather than a filter over the whole stack.
CREATE INDEX IF NOT EXISTS idx_units_volume_solid_nd
    ON units USING GIST (volume_solid gist_geometry_ops_nd);

-- The vertical axis. Composite rather than two single-column indexes: listing
-- one storey of one building is the viewer's hot query, and it wants both
-- columns as a leading prefix.
CREATE INDEX IF NOT EXISTS idx_units_building_floor
    ON units (building_id, floor_number);

-- Units awaiting a survey. Partial, because once the backfill completes this
-- matches almost nothing and costs almost nothing to keep.
CREATE INDEX IF NOT EXISTS idx_units_unlocated
    ON units (building_id) WHERE volume_solid IS NULL;

CREATE INDEX IF NOT EXISTS idx_buildings_footprint_gist ON buildings USING GIST (footprint);

-- ---------------------------------------------------------------------------
-- Report
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_total   int;
    v_located int;
BEGIN
    SELECT count(*), count(volume_solid) INTO v_total, v_located FROM units;
    RAISE NOTICE '3D schema ready: % unit(s), % with geometry, % awaiting the generator',
        v_total, v_located, v_total - v_located;
END
$$;
