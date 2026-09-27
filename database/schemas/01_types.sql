-- =============================================================================
--  01_types.sql — enums, domains and helper functions
--  Depends on: 00_extensions.sql
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- -----------------------------------------------------------------------------
-- 1. Enumerated types
-- -----------------------------------------------------------------------------

-- Four principal roles, plus two non-interactive ones.
-- This enum is the single source of truth: app/core/permissions.py and
-- packages/types/src/auth.ts must mirror it exactly.
CREATE TYPE user_role AS ENUM (
    'ADMIN',            -- full system administration, all jurisdictions
    'PROPERTY_OFFICER', -- surveyor/verifier/registrar: creates and approves records
    'OWNER',            -- holds title to one or more units; read-only on own records
    'TENANT',           -- occupies a unit under a tenancy; narrowest scope
    'AUDITOR',          -- read-only across jurisdictions, sees the audit trail
    'SERVICE'           -- machine-to-machine API client
);

CREATE TYPE user_status AS ENUM ('PENDING', 'ACTIVE', 'SUSPENDED', 'LOCKED', 'DISABLED');

CREATE TYPE building_status AS ENUM (
    'DRAFT',            -- footprint captured, not yet modelled
    'MODELLED',         -- 3D envelope generated
    'UNDER_VERIFICATION',
    'VERIFIED',
    'REJECTED',
    'DEMOLISHED'
);

CREATE TYPE building_use AS ENUM (
    'RESIDENTIAL', 'COMMERCIAL', 'MIXED_USE', 'INDUSTRIAL',
    'INSTITUTIONAL', 'GOVERNMENT', 'RELIGIOUS', 'AGRICULTURAL', 'OTHER'
);

CREATE TYPE construction_status AS ENUM (
    'PROPOSED', 'UNDER_CONSTRUCTION', 'COMPLETED', 'OCCUPIED', 'DERELICT', 'DEMOLISHED'
);

-- Storey classification. Drives the ULPIN storey code prefix (B/G/M/F/T/A).
CREATE TYPE floor_type AS ENUM (
    'BASEMENT',     -- B — counts downward from 1
    'GROUND',       -- G — level 0
    'MEZZANINE',    -- M — attached to the storey below
    'UPPER',        -- F — normal above-ground floor
    'TERRACE',      -- T — roof-level rights
    'AIR_RIGHTS',   -- A — unbuilt volume above the envelope
    'STILT',        -- G-level open parking; coded as G with a flag
    'PODIUM'
);

CREATE TYPE unit_type AS ENUM (
    'APARTMENT', 'OFFICE', 'SHOP', 'SHOWROOM', 'WAREHOUSE',
    'PARKING', 'STORAGE', 'UTILITY', 'COMMON_AREA', 'SERVICE_SHAFT',
    'TERRACE_UNIT', 'AIR_RIGHT', 'OTHER'
);

CREATE TYPE unit_status AS ENUM (
    'DRAFT', 'PROVISIONAL', 'VERIFIED', 'ISSUED',
    'DISPUTED', 'SUPERSEDED', 'RETIRED'
);

CREATE TYPE occupancy_status AS ENUM (
    'VACANT', 'OWNER_OCCUPIED', 'TENANT_OCCUPIED', 'LOCKED', 'UNDER_FITOUT'
);

CREATE TYPE owner_type AS ENUM (
    'INDIVIDUAL', 'JOINT', 'COMPANY', 'PARTNERSHIP', 'TRUST',
    'SOCIETY', 'HUF', 'GOVERNMENT', 'RELIGIOUS_BODY'
);

CREATE TYPE ownership_mode AS ENUM (
    'FREEHOLD', 'LEASEHOLD', 'CO_OPERATIVE', 'POWER_OF_ATTORNEY',
    'INHERITED', 'ALLOTMENT', 'GOVERNMENT_GRANT'
);

CREATE TYPE tenancy_status AS ENUM (
    'DRAFT', 'ACTIVE', 'EXPIRED', 'TERMINATED', 'RENEWED', 'DISPUTED'
);

-- What a given ULPIN identifies. The 2D parcel code is inherited from the
-- existing cadastre; everything below it is minted by this system.
CREATE TYPE ulpin_type AS ENUM ('PARCEL_2D', 'BUILDING', 'FLOOR', 'UNIT_3D');

CREATE TYPE ulpin_status AS ENUM (
    'DRAFT', 'PROVISIONAL', 'VERIFIED', 'ISSUED', 'SUSPENDED', 'SUPERSEDED', 'RETIRED'
);

CREATE TYPE verification_type AS ENUM (
    'DOCUMENT',     -- sanctioned plan, deed, occupancy certificate
    'FIELD',        -- physical site inspection
    'GEOMETRIC',    -- automated solid/overlap/area checks
    'BIOMETRIC',    -- owner identity confirmation
    'SURVEY',       -- DGPS / total station re-survey
    'THIRD_PARTY'   -- external registry cross-check
);

CREATE TYPE verification_status AS ENUM (
    'PENDING', 'IN_PROGRESS', 'VERIFIED', 'REJECTED', 'INCONCLUSIVE', 'EXPIRED'
);

CREATE TYPE fraud_alert_type AS ENUM (
    'DUPLICATE_ULPIN',        -- same identifier minted twice
    'OVERLAPPING_VOLUME',     -- two units claim intersecting 3D space
    'AREA_MISMATCH',          -- sum of units exceeds the sanctioned plate
    'GHOST_UNIT',             -- unit with no physical envelope
    'OWNERSHIP_CONFLICT',     -- competing claims on one unit
    'RAPID_TRANSFER',         -- suspicious churn of ownership
    'SHARE_OVERFLOW',         -- ownership shares sum above 1
    'FORGED_DOCUMENT',
    'IDENTITY_MISMATCH',
    'UNAUTHORISED_MODIFICATION',
    'GEOMETRY_TAMPERING',     -- issued geometry changed outside supersession
    'ENCROACHMENT',           -- volume extends beyond the parent parcel
    'OTHER'
);

CREATE TYPE alert_severity AS ENUM ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL');

CREATE TYPE alert_status AS ENUM (
    'OPEN', 'TRIAGED', 'INVESTIGATING', 'CONFIRMED', 'DISMISSED', 'RESOLVED'
);

CREATE TYPE detection_source AS ENUM ('RULE_ENGINE', 'ML_MODEL', 'MANUAL_REPORT', 'EXTERNAL_FEED', 'AUDIT_SWEEP');

CREATE TYPE audit_action AS ENUM (
    'INSERT', 'UPDATE', 'DELETE',
    'LOGIN', 'LOGIN_FAILED', 'LOGOUT',
    'ULPIN_MINT', 'ULPIN_ISSUE', 'ULPIN_SUPERSEDE', 'ULPIN_RETIRE',
    'VERIFY', 'APPROVE', 'REJECT',
    'EXPORT', 'DOWNLOAD', 'PERMISSION_CHANGE'
);

-- -----------------------------------------------------------------------------
-- 2. Domains
-- -----------------------------------------------------------------------------

-- Canonical ULPIN forms accepted by the database:
--   2D parcel : 14 alphanumerics                       e.g. IN29BLR0001234
--   3D unit   : parent-block-storey-unit-check         e.g. IN29BLR0001234-A1-F007-U0412-K
-- The storey segment is constrained to the six legal prefixes so a malformed
-- storey code cannot reach storage even if the application misbehaves.
CREATE DOMAIN ulpin_code AS varchar(32)
    CONSTRAINT ulpin_code_format CHECK (
        VALUE ~ '^[0-9A-Z]{14}$'
     OR VALUE ~ '^[0-9A-Z]{14}-[0-9A-Z]{2}-[BGMFTA][0-9]{3}-U[0-9A-Z]{4}-[0-9A-Z]$'
    );

CREATE DOMAIN email_address AS varchar(320)
    CONSTRAINT email_format CHECK (VALUE ~* '^[^@[:space:]]+@[^@[:space:]]+\.[a-z]{2,}$');

CREATE DOMAIN phone_number AS varchar(20)
    CONSTRAINT phone_format CHECK (VALUE ~ '^\+?[0-9]{8,15}$');

-- Ownership share as an exact rational. Numeric, never float — a float share
-- ledger will not sum to 1 and the reconciliation trigger would reject valid data.
CREATE DOMAIN share_fraction AS numeric(12,9)
    CONSTRAINT share_range CHECK (VALUE > 0 AND VALUE <= 1);

CREATE DOMAIN positive_area AS numeric(14,4)
    CONSTRAINT area_positive CHECK (VALUE > 0);

CREATE DOMAIN confidence_score AS numeric(5,4)
    CONSTRAINT confidence_range CHECK (VALUE >= 0 AND VALUE <= 1);

-- -----------------------------------------------------------------------------
-- 3. Helper functions
-- -----------------------------------------------------------------------------

-- 3.1 updated_at maintenance -------------------------------------------------
CREATE OR REPLACE FUNCTION fn_touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION fn_touch_updated_at() IS
    'Generic BEFORE UPDATE trigger: stamps updated_at with the transaction timestamp.';

-- 3.2 ULPIN check character --------------------------------------------------
-- ISO 7064 MOD 36,36 hybrid. Over the 36-character alphanumeric alphabet it
-- detects every single-character substitution and every adjacent transposition.
--
-- Validation accepts all 36 characters because inherited 2D parcel codes may
-- already contain I/L/O/U. Minting (application side) restricts NEW characters to
-- the Crockford subset so freshly issued identifiers stay transcription-safe.
CREATE OR REPLACE FUNCTION fn_ulpin_checksum(p_payload text)
RETURNS char(1)
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    c_alphabet  constant text := '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
    c_modulus   constant int  := 36;
    v_clean     text;
    v_p         int := c_modulus;
    v_s         int;
    v_val       int;
    i           int;
BEGIN
    v_clean := upper(regexp_replace(p_payload, '[^0-9A-Za-z]', '', 'g'));

    IF v_clean = '' THEN
        RAISE EXCEPTION 'fn_ulpin_checksum: payload contains no alphanumeric characters';
    END IF;

    FOR i IN 1 .. length(v_clean) LOOP
        v_val := position(substring(v_clean FROM i FOR 1) IN c_alphabet) - 1;
        IF v_val < 0 THEN
            RAISE EXCEPTION 'fn_ulpin_checksum: illegal character % at position %',
                substring(v_clean FROM i FOR 1), i;
        END IF;

        v_s := (v_p % (c_modulus + 1)) + v_val;
        v_s := v_s % c_modulus;
        IF v_s = 0 THEN
            v_s := c_modulus;
        END IF;
        v_p := 2 * v_s;
    END LOOP;

    RETURN substring(c_alphabet FROM ((c_modulus + 1 - (v_p % (c_modulus + 1))) % c_modulus) + 1 FOR 1);
END;
$$;

COMMENT ON FUNCTION fn_ulpin_checksum(text) IS
    'ISO 7064 MOD 36,36 check character. Must stay byte-identical to the Python and TypeScript codecs; shared vectors live in tests/fixtures/ulpin-vectors.json.';

-- 3.3 Full identifier verification -------------------------------------------
CREATE OR REPLACE FUNCTION fn_ulpin_verify(p_code text)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    v_code    text := upper(trim(p_code));
    v_payload text;
    v_check   char(1);
BEGIN
    -- A bare 14-character 2D parcel code carries the legacy cadastral check digit,
    -- which this system does not re-derive. Structure is all that is asserted.
    IF v_code ~ '^[0-9A-Z]{14}$' THEN
        RETURN true;
    END IF;

    IF v_code !~ '^[0-9A-Z]{14}-[0-9A-Z]{2}-[BGMFTA][0-9]{3}-U[0-9A-Z]{4}-[0-9A-Z]$' THEN
        RETURN false;
    END IF;

    v_payload := replace(left(v_code, length(v_code) - 2), '-', '');
    v_check   := right(v_code, 1);

    RETURN fn_ulpin_checksum(v_payload) = v_check;
END;
$$;

-- 3.4 Identifier decomposition -----------------------------------------------
CREATE OR REPLACE FUNCTION fn_ulpin_parse(p_code text)
RETURNS TABLE (
    parent_ulpin  text,
    block_code    text,
    storey_code   text,
    unit_code     text,
    check_char    text,
    floor_number  int,
    is_valid      boolean
)
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    v_code text := upper(trim(p_code));
    v_parts text[];
BEGIN
    IF v_code ~ '^[0-9A-Z]{14}$' THEN
        RETURN QUERY SELECT v_code, NULL::text, NULL::text, NULL::text, NULL::text, NULL::int, true;
        RETURN;
    END IF;

    v_parts := string_to_array(v_code, '-');
    IF array_length(v_parts, 1) <> 5 THEN
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::text, NULL::text, NULL::text, NULL::int, false;
        RETURN;
    END IF;

    RETURN QUERY
    SELECT v_parts[1],
           v_parts[2],
           v_parts[3],
           v_parts[4],
           v_parts[5],
           CASE left(v_parts[3], 1)
               WHEN 'B' THEN -substring(v_parts[3] FROM 2)::int   -- basements go negative
               WHEN 'G' THEN 0
               ELSE substring(v_parts[3] FROM 2)::int
           END,
           fn_ulpin_verify(v_code);
END;
$$;

-- 3.5 Storey code construction ------------------------------------------------
CREATE OR REPLACE FUNCTION fn_storey_code(p_floor_number int, p_floor_type floor_type)
RETURNS char(4)
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    v_prefix char(1);
BEGIN
    v_prefix := CASE p_floor_type
        WHEN 'BASEMENT'   THEN 'B'
        WHEN 'GROUND'     THEN 'G'
        WHEN 'STILT'      THEN 'G'
        WHEN 'PODIUM'     THEN 'G'
        WHEN 'MEZZANINE'  THEN 'M'
        WHEN 'UPPER'      THEN 'F'
        WHEN 'TERRACE'    THEN 'T'
        WHEN 'AIR_RIGHTS' THEN 'A'
    END;

    IF abs(p_floor_number) > 999 THEN
        RAISE EXCEPTION 'fn_storey_code: floor number % exceeds the 3-digit storey field', p_floor_number;
    END IF;

    RETURN v_prefix || lpad(abs(p_floor_number)::text, 3, '0');
END;
$$;

-- 3.6 Prism construction ------------------------------------------------------
-- Builds a closed PolyhedralSurface Z from a 2D polygon plus a base and top
-- elevation: bottom face (reversed for a downward normal), top face, and one quad
-- per exterior edge.
--
-- Deliberately does not use SFCGAL's ST_Extrude — that function operates in
-- cartesian space and extruding by metres along Z while X/Y are degrees produces a
-- geometrically valid but dimensionally incoherent solid. Constructing the WKT
-- directly keeps the mixed-unit storage convention explicit and auditable.
CREATE OR REPLACE FUNCTION fn_make_prism(
    p_footprint geometry,
    p_z_base    double precision,
    p_z_top     double precision
)
RETURNS geometry
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    v_ring    geometry;
    v_n       int;
    v_faces   text[] := ARRAY[]::text[];
    v_face    text;
    v_srid    int;
    i         int;
    x1 numeric; y1 numeric;
    x2 numeric; y2 numeric;
    zb text; zt text;
BEGIN
    IF ST_GeometryType(p_footprint) <> 'ST_Polygon' THEN
        RAISE EXCEPTION 'fn_make_prism: expected ST_Polygon, got %', ST_GeometryType(p_footprint);
    END IF;

    IF p_z_top <= p_z_base THEN
        RAISE EXCEPTION 'fn_make_prism: z_top (%) must be greater than z_base (%)', p_z_top, p_z_base;
    END IF;

    v_srid := ST_SRID(p_footprint);
    v_ring := ST_ExteriorRing(ST_Force2D(p_footprint));
    v_n    := ST_NPoints(v_ring);       -- closed ring: point n equals point 1

    IF v_n < 4 THEN
        RAISE EXCEPTION 'fn_make_prism: exterior ring needs at least 4 points, found %', v_n;
    END IF;

    -- numeric (not float8) text output avoids scientific notation in the WKT
    zb := round(p_z_base::numeric, 6)::text;
    zt := round(p_z_top::numeric,  6)::text;

    -- bottom face, wound in reverse so its normal points down
    v_face := '';
    FOR i IN REVERSE v_n .. 1 LOOP
        x1 := round(ST_X(ST_PointN(v_ring, i))::numeric, 9);
        y1 := round(ST_Y(ST_PointN(v_ring, i))::numeric, 9);
        v_face := v_face || CASE WHEN v_face = '' THEN '' ELSE ',' END
                  || x1 || ' ' || y1 || ' ' || zb;
    END LOOP;
    v_faces := array_append(v_faces, '((' || v_face || '))');

    -- top face, forward winding
    v_face := '';
    FOR i IN 1 .. v_n LOOP
        x1 := round(ST_X(ST_PointN(v_ring, i))::numeric, 9);
        y1 := round(ST_Y(ST_PointN(v_ring, i))::numeric, 9);
        v_face := v_face || CASE WHEN v_face = '' THEN '' ELSE ',' END
                  || x1 || ' ' || y1 || ' ' || zt;
    END LOOP;
    v_faces := array_append(v_faces, '((' || v_face || '))');

    -- one vertical quad per edge
    FOR i IN 1 .. v_n - 1 LOOP
        x1 := round(ST_X(ST_PointN(v_ring, i))::numeric, 9);
        y1 := round(ST_Y(ST_PointN(v_ring, i))::numeric, 9);
        x2 := round(ST_X(ST_PointN(v_ring, i + 1))::numeric, 9);
        y2 := round(ST_Y(ST_PointN(v_ring, i + 1))::numeric, 9);

        v_faces := array_append(v_faces,
            '((' || x1 || ' ' || y1 || ' ' || zb || ',' ||
                    x2 || ' ' || y2 || ' ' || zb || ',' ||
                    x2 || ' ' || y2 || ' ' || zt || ',' ||
                    x1 || ' ' || y1 || ' ' || zt || ',' ||
                    x1 || ' ' || y1 || ' ' || zb || '))');
    END LOOP;

    RETURN ST_GeomFromText(
        'POLYHEDRALSURFACE Z (' || array_to_string(v_faces, ',') || ')',
        v_srid
    );
END;
$$;

COMMENT ON FUNCTION fn_make_prism(geometry, double precision, double precision) IS
    'Closed PolyhedralSurface Z from a polygon footprint between two elevations. Used by the LoD1 extrusion stage and by seed data.';

-- 3.7 Metric measurement helpers ---------------------------------------------
-- Area and volume must be computed in a projected CRS. These wrappers make the
-- transform mandatory so no caller can accidentally measure in square degrees.

CREATE OR REPLACE FUNCTION fn_area_sqm(p_geom geometry, p_metric_srid int DEFAULT 7755)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT round(ST_Area(ST_Transform(ST_Force2D(p_geom), p_metric_srid))::numeric, 4);
$$;

CREATE OR REPLACE FUNCTION fn_volume_cum(p_solid geometry, p_metric_srid int DEFAULT 7755)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    -- ST_Volume needs a closed solid; ST_MakeSolid wraps the polyhedral shell.
    SELECT round(ST_Volume(ST_MakeSolid(ST_Transform(p_solid, p_metric_srid)))::numeric, 4);
$$;

COMMENT ON FUNCTION fn_volume_cum(geometry, int) IS
    'SFCGAL volume in cubic metres. Requires a closed, manifold PolyhedralSurface — validate with ST_IsSolid first.';
