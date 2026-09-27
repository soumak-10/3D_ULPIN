-- =============================================================================
--  3D ULPIN Generation and Vertical Property Mapping System
--  00_extensions.sql — PostGIS configuration and required extensions
--
--  Run order: 00 → 01 → 02 → 03 → 04 → seeds
--  Run as a superuser (extension creation requires it).
-- =============================================================================

\set ON_ERROR_STOP on

-- -----------------------------------------------------------------------------
-- 1. Extensions
-- -----------------------------------------------------------------------------

-- Core spatial engine. Provides the geometry type, GiST operator classes,
-- ST_* functions and the spatial_ref_sys catalogue.
CREATE EXTENSION IF NOT EXISTS postgis;

-- Topology support (used later for parcel-boundary editing without gaps/overlaps).
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- SFCGAL: the only way to do real 3D solid work in PostGIS.
-- Supplies ST_3DIntersection, ST_Volume, ST_IsSolid, ST_Extrude, ST_MakeSolid.
-- Without this the vertical-property model cannot be validated.
CREATE EXTENSION IF NOT EXISTS postgis_sfcgal;

-- Raster support — only needed if DEM/DSM rasters are used to derive ground level.
-- Comment out if terrain is supplied as vector contours instead.
CREATE EXTENSION IF NOT EXISTS postgis_raster;

-- gen_random_uuid(), digest(), crypt() — UUID keys and PII hashing.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Trigram indexes for fuzzy owner-name / address search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Lets B-tree-comparable columns participate in GiST indexes, which is what makes
-- exclusion constraints such as "no two active tenancies on one unit" possible.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Query performance telemetry.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- -----------------------------------------------------------------------------
-- 2. Verify the install
-- -----------------------------------------------------------------------------

DO $$
DECLARE
    v_postgis  text;
    v_sfcgal   text;
BEGIN
    SELECT extversion INTO v_postgis FROM pg_extension WHERE extname = 'postgis';
    SELECT extversion INTO v_sfcgal  FROM pg_extension WHERE extname = 'postgis_sfcgal';

    IF v_postgis IS NULL THEN
        RAISE EXCEPTION 'PostGIS is not installed';
    END IF;
    IF v_sfcgal IS NULL THEN
        RAISE EXCEPTION 'postgis_sfcgal is not installed — 3D solid validation is impossible without it';
    END IF;
    IF string_to_array(v_postgis, '.')::int[] < ARRAY[3,3] THEN
        RAISE EXCEPTION 'PostGIS >= 3.3 required, found %', v_postgis;
    END IF;

    RAISE NOTICE 'PostGIS % / SFCGAL % ready', v_postgis, v_sfcgal;
END
$$;

-- -----------------------------------------------------------------------------
-- 3. Coordinate reference systems
-- -----------------------------------------------------------------------------
--   4326  WGS84 lon/lat  — CANONICAL STORAGE SRID for every geometry column.
--                          Z is stored in metres above the vertical datum, so
--                          these are "mixed-unit" coordinates: never measure on them.
--   7755  WGS84 / India NSF LCC — national metric CRS for area and volume.
--   32642..32647  WGS84 / UTM 42N..47N — per-state metric CRS where a local
--                          projection is legally mandated.
--
--   RULE: geometry lives in 4326, measurement happens after ST_Transform to the
--   jurisdiction's metric SRID. Computing ST_Area on 4326 yields square degrees,
--   which is meaningless in a land record.
-- -----------------------------------------------------------------------------

DO $$
DECLARE
    v_missing int;
BEGIN
    SELECT count(*) INTO v_missing
    FROM (VALUES (4326), (7755), (32643), (32644)) AS req(srid)
    WHERE NOT EXISTS (SELECT 1 FROM spatial_ref_sys s WHERE s.srid = req.srid);

    IF v_missing > 0 THEN
        RAISE WARNING '% required SRID(s) missing from spatial_ref_sys', v_missing;
    END IF;
END
$$;

-- Vertical datum reference. PostGIS does not model vertical CRS, so the datum that
-- every Z value is measured against is tracked explicitly in application tables.
CREATE TABLE IF NOT EXISTS vertical_datums (
    datum_code      varchar(24)  PRIMARY KEY,
    datum_name      text         NOT NULL,
    description     text,
    is_orthometric  boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now()
);

INSERT INTO vertical_datums (datum_code, datum_name, description, is_orthometric) VALUES
    ('EGM2008',  'EGM2008 geoid',            'Orthometric height (MSL) from the EGM2008 geoid model', true),
    ('EGM96',    'EGM96 geoid',              'Legacy orthometric height, EGM96 geoid model',          true),
    ('WGS84_ELL','WGS84 ellipsoidal height', 'Raw GNSS height above the WGS84 ellipsoid',             false),
    ('LOCAL_PBM','Local permanent benchmark','Height relative to a surveyed local benchmark',         true)
ON CONFLICT (datum_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 4. Roles
-- -----------------------------------------------------------------------------
-- Two application roles with different privilege ceilings. The API never holds
-- DDL rights; only the migration runner does.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulpin_app') THEN
        CREATE ROLE ulpin_app LOGIN PASSWORD 'change_me_in_production';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulpin_migrate') THEN
        CREATE ROLE ulpin_migrate LOGIN PASSWORD 'change_me_in_production';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulpin_readonly') THEN
        CREATE ROLE ulpin_readonly LOGIN PASSWORD 'change_me_in_production';
    END IF;
END
$$;

-- Schema for application objects; keep `public` clean for extensions.
CREATE SCHEMA IF NOT EXISTS ulpin AUTHORIZATION ulpin_migrate;

-- postgis lives in public, so both must be on the search path.
-- `ALTER DATABASE` takes a literal identifier, not an expression: writing
-- CURRENT_DATABASE here makes the parser look for a database of that name and
-- fail. format(%I) with current_database() applies it to whichever database
-- this file is being run against, so the name stays configurable.
DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET search_path TO ulpin, public',
        current_database()
    );
END
$$;

GRANT USAGE ON SCHEMA ulpin TO ulpin_app, ulpin_readonly;
GRANT USAGE ON SCHEMA public TO ulpin_app, ulpin_readonly;

-- Defaults so future tables are reachable without re-granting each migration.
ALTER DEFAULT PRIVILEGES IN SCHEMA ulpin
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ulpin_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA ulpin
    GRANT SELECT ON TABLES TO ulpin_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ulpin
    GRANT USAGE, SELECT ON SEQUENCES TO ulpin_app;

-- -----------------------------------------------------------------------------
-- 5. Session defaults relevant to spatial work
-- -----------------------------------------------------------------------------
-- PostGIS geometry output defaults to EWKB; keep it that way for the driver.
-- JIT is disabled at the database level in postgresql.custom.conf because it
-- consistently regresses GiST-heavy spatial plans.

SELECT postgis_full_version();
