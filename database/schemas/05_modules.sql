-- =============================================================================
--  05_modules.sql — ULPIN generation, verification, fraud detection
--  Depends on: 04_triggers.sql
--
--  Additive and idempotent. Every statement is guarded, so this file can be
--  replayed against a database that already has some of it. That property is
--  what lets the same file serve a fresh install and an upgrade.
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- -----------------------------------------------------------------------------
-- 1. Enum alignment
-- -----------------------------------------------------------------------------
-- The application maps every enum column with create_type=False: SQLAlchemy
-- sends the Python member's value as a literal and PostgreSQL resolves it. A
-- member the type lacks is therefore not a type error and not a migration
-- error — it is `22P02 invalid input value for enum` the first time that code
-- path runs in production.
--
-- tests/test_schema_alignment.py asserts the containment that makes that
-- impossible, and generated the statements below.
--
-- ADD VALUE is not transactional before PostgreSQL 12 and cannot be used on a
-- type created in the same transaction. psql's default autocommit satisfies
-- both conditions; do not wrap this section in BEGIN/COMMIT.

ALTER TYPE user_status          ADD VALUE IF NOT EXISTS 'DEACTIVATED';

ALTER TYPE building_status      ADD VALUE IF NOT EXISTS 'SUBMITTED';
ALTER TYPE building_status      ADD VALUE IF NOT EXISTS 'REGISTERED';
ALTER TYPE building_status      ADD VALUE IF NOT EXISTS 'ARCHIVED';

ALTER TYPE building_use         ADD VALUE IF NOT EXISTS 'PUBLIC';

-- The registration form offers exactly three property types. The rest are
-- assigned by officers during plan digitisation and are not citizen-selectable.
ALTER TYPE unit_type            ADD VALUE IF NOT EXISTS 'RESIDENTIAL';
ALTER TYPE unit_type            ADD VALUE IF NOT EXISTS 'COMMERCIAL';
ALTER TYPE unit_type            ADD VALUE IF NOT EXISTS 'INDUSTRIAL';
ALTER TYPE unit_type            ADD VALUE IF NOT EXISTS 'MIXED';

ALTER TYPE unit_status          ADD VALUE IF NOT EXISTS 'ACTIVE';
ALTER TYPE unit_status          ADD VALUE IF NOT EXISTS 'UNDER_DISPUTE';
ALTER TYPE unit_status          ADD VALUE IF NOT EXISTS 'MERGED';
ALTER TYPE unit_status          ADD VALUE IF NOT EXISTS 'SUBDIVIDED';
ALTER TYPE unit_status          ADD VALUE IF NOT EXISTS 'DEMOLISHED';

-- OCCUPIED/VACANT is the pair the public register prints. OWNER_OCCUPIED and
-- TENANT_OCCUPIED remain for the finer distinction an officer records.
ALTER TYPE occupancy_status     ADD VALUE IF NOT EXISTS 'OCCUPIED';
ALTER TYPE occupancy_status     ADD VALUE IF NOT EXISTS 'UNDER_RENOVATION';
ALTER TYPE occupancy_status     ADD VALUE IF NOT EXISTS 'SEALED';

ALTER TYPE ownership_mode       ADD VALUE IF NOT EXISTS 'SOLE';
ALTER TYPE ownership_mode       ADD VALUE IF NOT EXISTS 'JOINT_TENANCY';
ALTER TYPE ownership_mode       ADD VALUE IF NOT EXISTS 'TENANCY_IN_COMMON';
ALTER TYPE ownership_mode       ADD VALUE IF NOT EXISTS 'COOPERATIVE';

ALTER TYPE tenancy_status       ADD VALUE IF NOT EXISTS 'PENDING';

ALTER TYPE verification_type    ADD VALUE IF NOT EXISTS 'FIELD_SURVEY';
ALTER TYPE verification_type    ADD VALUE IF NOT EXISTS 'GEOMETRY';
ALTER TYPE verification_type    ADD VALUE IF NOT EXISTS 'IDENTITY';
ALTER TYPE verification_type    ADD VALUE IF NOT EXISTS 'OWNERSHIP';
ALTER TYPE verification_type    ADD VALUE IF NOT EXISTS 'AUTOMATED';

-- PASSED/FAILED rather than VERIFIED/REJECTED: a *check* passes, a *property*
-- is verified. Keeping the two vocabularies apart stops "the document check was
-- rejected" from being read as "the property was rejected".
ALTER TYPE verification_status   ADD VALUE IF NOT EXISTS 'PASSED';
ALTER TYPE verification_status   ADD VALUE IF NOT EXISTS 'FAILED';
ALTER TYPE verification_status   ADD VALUE IF NOT EXISTS 'WAIVED';

-- The five rules the fraud engine implements.
ALTER TYPE fraud_alert_type     ADD VALUE IF NOT EXISTS 'MULTIPLE_OWNERS';
ALTER TYPE fraud_alert_type     ADD VALUE IF NOT EXISTS 'OWNERSHIP_MISMATCH';
ALTER TYPE fraud_alert_type     ADD VALUE IF NOT EXISTS 'TENANT_MISMATCH';
ALTER TYPE fraud_alert_type     ADD VALUE IF NOT EXISTS 'UNAUTHORIZED_OCCUPANCY';

ALTER TYPE audit_action         ADD VALUE IF NOT EXISTS 'ISSUE';

-- The verdict the verification engine returns for a property as a whole. It is
-- not verification_status: that tracks one check, this answers the citizen.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'verification_outcome' AND n.nspname = 'ulpin'
    ) THEN
        CREATE TYPE verification_outcome AS ENUM (
            'VERIFIED',                 -- title, geometry and occupancy all agree
            'PENDING_VERIFICATION',     -- nothing is wrong; the checks have not been done
            'INVALID_CLAIM',            -- the ownership asserted is not the ownership recorded
            'UNAUTHORIZED_OCCUPANCY'    -- someone is in the unit with no lease behind them
        );
    END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 2. ULPIN short code
-- -----------------------------------------------------------------------------
-- STATE-CITY-BUILDING-FLOOR-UNIT, e.g. WB-KOL-B001-F03-U301.
--
-- The canonical ulpin_code carries the 14-character parcel identifier so the
-- existing 2D cadastre can still resolve it. That is the right property for a
-- machine and the wrong one for a human: nobody reads IN29BLR0001234-A1-F007-
-- U0412-Y down a telephone. The short code is the form printed on notices and
-- typed into the search box, so it needs its own uniqueness guarantee — "no
-- duplicate ULPINs" has to hold for the form people actually quote.

ALTER DOMAIN ulpin_code DROP CONSTRAINT IF EXISTS ulpin_code_format;
ALTER DOMAIN ulpin_code ADD CONSTRAINT ulpin_code_format CHECK (
        VALUE ~ '^[0-9A-Z]{14}$'                                                    -- 2D parcel
     OR VALUE ~ '^[0-9A-Z]{14}-[0-9A-Z]{2}-[BGMFTA][0-9]{3}-U[0-9A-Z]{4}-[0-9A-Z]$' -- 3D canonical
);

CREATE DOMAIN ulpin_short_code AS varchar(24)
    CONSTRAINT ulpin_short_code_format CHECK (
        VALUE ~ '^[A-Z]{2}-[A-Z]{3}-B[0-9]{3,4}-[BGMFTASP][0-9]{2,3}-U[0-9]{3,4}$'
);

CREATE DOMAIN ulpin_building_code AS varchar(12)
    CONSTRAINT ulpin_building_code_format CHECK (
        VALUE ~ '^[A-Z]{2}-[A-Z]{3}-B[0-9]{3,4}$'
);

ALTER TABLE ulpins ADD COLUMN IF NOT EXISTS short_code          ulpin_short_code;
ALTER TABLE ulpins ADD COLUMN IF NOT EXISTS building_short_code ulpin_building_code;
ALTER TABLE ulpins ADD COLUMN IF NOT EXISTS state_code          char(2);
ALTER TABLE ulpins ADD COLUMN IF NOT EXISTS city_code           char(3);

-- Partial, because only UNIT_3D rows carry a unit-level short code and NULLs
-- must not collide with each other.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ulpins_short_code
    ON ulpins (short_code) WHERE short_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ulpins_building_short
    ON ulpins (building_short_code) WHERE building_short_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ulpins_region
    ON ulpins (state_code, city_code);

ALTER TABLE buildings ADD COLUMN IF NOT EXISTS short_code        ulpin_building_code;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS building_sequence integer;

CREATE UNIQUE INDEX IF NOT EXISTS uq_buildings_short_code
    ON buildings (short_code) WHERE short_code IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 3. Sequence allocation
-- -----------------------------------------------------------------------------
-- Building numbers run per (state, city), which is the scope a citizen can hold
-- in their head: "building 1 in Kolkata".
--
-- A PostgreSQL SEQUENCE is the wrong tool here for two reasons: it is global
-- where this counter is per-region, and it is explicitly non-transactional, so
-- a rolled-back registration would burn a number and leave a permanent hole in
-- the register. A counter row taken with UPDATE ... RETURNING is transactional,
-- serialises concurrent allocators on the row lock, and rolls back cleanly.
CREATE TABLE IF NOT EXISTS ulpin_sequences (
    state_code      char(2)     NOT NULL,
    city_code       char(3)     NOT NULL,
    last_value      integer     NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_ulpin_sequences PRIMARY KEY (state_code, city_code),
    CONSTRAINT ck_ulpin_sequences_value CHECK (last_value >= 0 AND last_value <= 9999)
);

COMMENT ON TABLE ulpin_sequences IS
    'Per-region building counter. Taken with INSERT ... ON CONFLICT DO UPDATE '
    'RETURNING, which is atomic: the second concurrent allocator blocks on the '
    'row lock rather than reading a stale value.';

-- -----------------------------------------------------------------------------
-- 4. Verification
-- -----------------------------------------------------------------------------
-- The original constraint named the VERIFIED/REJECTED pair. The application
-- writes PASSED/FAILED, so the constraint would have been satisfied by rows it
-- was written to reject.
ALTER TABLE verification_records DROP CONSTRAINT IF EXISTS ck_verif_closed;
ALTER TABLE verification_records ADD CONSTRAINT ck_verif_closed CHECK (
    status NOT IN ('PASSED', 'FAILED') OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)
);

ALTER TABLE verification_records DROP CONSTRAINT IF EXISTS ck_verif_rejection;
ALTER TABLE verification_records ADD CONSTRAINT ck_verif_rejection CHECK (
    status <> 'FAILED' OR rejection_reason IS NOT NULL
);

-- The engine's verdict on the property, as distinct from the status of any one
-- check. Denormalised onto the row so the search results page can colour a
-- thousand units without running the engine a thousand times.
ALTER TABLE verification_records
    ADD COLUMN IF NOT EXISTS outcome verification_outcome;

ALTER TABLE units
    ADD COLUMN IF NOT EXISTS verification_outcome verification_outcome
        NOT NULL DEFAULT 'PENDING_VERIFICATION';
ALTER TABLE units
    ADD COLUMN IF NOT EXISTS last_verified_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_units_verification
    ON units (verification_outcome) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_verif_ulpin_recent
    ON verification_records (ulpin_id, requested_at DESC);

-- -----------------------------------------------------------------------------
-- 5. Fraud detection
-- -----------------------------------------------------------------------------
-- A rule that fires on every sweep must not create a new alert every sweep, or
-- the queue becomes unreadable within a day and officers stop looking at it.
-- The fingerprint is the rule code plus its subject; the partial unique index
-- means re-running the engine updates the open alert instead of cloning it.
ALTER TABLE fraud_alerts ADD COLUMN IF NOT EXISTS fingerprint varchar(128);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fraud_open_fingerprint
    ON fraud_alerts (fingerprint)
    WHERE fingerprint IS NOT NULL AND status IN ('OPEN', 'TRIAGED', 'INVESTIGATING');

CREATE INDEX IF NOT EXISTS idx_fraud_open_severity
    ON fraud_alerts (severity, detected_at DESC)
    WHERE status IN ('OPEN', 'TRIAGED', 'INVESTIGATING');

CREATE INDEX IF NOT EXISTS idx_fraud_rule_code ON fraud_alerts (rule_code);

-- -----------------------------------------------------------------------------
-- 6. Search support
-- -----------------------------------------------------------------------------
-- Trigram indexes, not full-text: the search box takes names and identifiers,
-- and the query that has to be fast is the misremembered fragment — "Sunrise"
-- against "Sunrise Residency Tower B". to_tsvector cannot answer that;
-- pg_trgm can, and it serves ILIKE '%...%' without a rewrite at the call site.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_buildings_name_trgm
    ON buildings USING gin (building_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_owners_name_trgm
    ON owners USING gin (full_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tenants_name_trgm
    ON tenants USING gin (full_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_ulpins_code_trgm
    ON ulpins USING gin (ulpin_code gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_ulpins_short_trgm
    ON ulpins USING gin (short_code gin_trgm_ops);

-- -----------------------------------------------------------------------------
-- 7. Region derivation
-- -----------------------------------------------------------------------------
-- Keeps ulpins.state_code / city_code consistent with the short code they were
-- built from, so the dashboard's per-region aggregates never need to parse a
-- string at query time.
CREATE OR REPLACE FUNCTION fn_ulpin_region() RETURNS trigger AS $$
BEGIN
    IF NEW.short_code IS NOT NULL THEN
        NEW.state_code := split_part(NEW.short_code, '-', 1);
        NEW.city_code  := split_part(NEW.short_code, '-', 2);
        NEW.building_short_code := COALESCE(
            NEW.building_short_code,
            split_part(NEW.short_code, '-', 1) || '-' ||
            split_part(NEW.short_code, '-', 2) || '-' ||
            split_part(NEW.short_code, '-', 3)
        );
    ELSIF NEW.building_short_code IS NOT NULL THEN
        NEW.state_code := split_part(NEW.building_short_code, '-', 1);
        NEW.city_code  := split_part(NEW.building_short_code, '-', 2);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ulpin_region ON ulpins;
CREATE TRIGGER trg_ulpin_region
    BEFORE INSERT OR UPDATE OF short_code, building_short_code ON ulpins
    FOR EACH ROW EXECUTE FUNCTION fn_ulpin_region();
