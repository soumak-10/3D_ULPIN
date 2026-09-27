-- =============================================================================
--  02_reconcile.sql — backfill the ORM-named columns from their SQL-named twins
--
--  database/schemas/ and apps/api/app/models/ were written independently and
--  chose different names for the same concepts. scripts/sync_schema.py adds the
--  columns the ORM expects; this file copies the seeded values across so the
--  demo data is visible through the API rather than appearing as a set of empty
--  fields next to fully populated ones.
--
--  Idempotent: every statement is a plain assignment, so re-running is safe.
--  Run after the seed, as a superuser:
--
--    psql -U postgres -d ulpin_db -f database/seeds/02_reconcile.sql
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- users: the ORM keeps a boolean flag, the schema only kept the timestamp.
UPDATE users
   SET email_verified = (email_verified_at IS NOT NULL);

-- buildings: latitude/longitude are stored on the model as plain numerics but
-- only ever existed here inside the `location` point. Falling back to the
-- footprint centroid covers rows captured before a location was set.
UPDATE buildings
   SET building_number              = building_code,
       building_height_m            = height_m,
       occupancy_certificate_number = occupancy_cert_no,
       locality                     = COALESCE(locality, address_line2),
       latitude  = ROUND(ST_Y(COALESCE(location, ST_Centroid(footprint)))::numeric, 7),
       longitude = ROUND(ST_X(COALESCE(location, ST_Centroid(footprint)))::numeric, 7);

UPDATE owners
   SET father_or_spouse_name = father_or_spouse,
       country               = COALESCE(country_code, 'IN');

-- floors: elevation_top_m has no stored twin; it is the base plus the storey's
-- own ceiling height, which is what the 3D viewer needs to stack the slabs.
UPDATE floors
   SET floor_label      = label,
       elevation_base_m = elevation_m,
       elevation_top_m  = elevation_m + COALESCE(ceiling_height_m, 3.0),
       floor_plate      = slab_geom,
       gross_area_sqm   = plate_area_sqm,
       unit_count       = total_units;

-- units: ceiling height is likewise derived from the pair of elevations that
-- the seed did store.
UPDATE units
   SET unit_status             = status,
       occupancy_status        = occupancy,
       super_built_up_area_sqm = super_area_sqm,
       ceiling_height_m        = ROUND(top_elevation_m - base_elevation_m, 2),
       balconies               = CASE WHEN has_balcony THEN 1 ELSE 0 END;

UPDATE tenants
   SET agreement_number = agreement_no;

UPDATE unit_ownerships
   SET share_fraction = share,
       ended_on       = relinquished_on,
       deed_number    = COALESCE(registration_no, acquisition_doc_no);

-- Report, so a silent no-op is visible.
DO $$
DECLARE
    v_units    int;
    v_unmapped int;
BEGIN
    SELECT count(*) INTO v_units FROM units WHERE unit_status IS NOT NULL;
    SELECT count(*) INTO v_unmapped FROM units WHERE unit_status IS NULL;
    RAISE NOTICE 'reconciled % unit(s), % still unmapped', v_units, v_unmapped;
END
$$;
