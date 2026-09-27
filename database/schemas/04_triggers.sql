-- =============================================================================
--  04_triggers.sql — integrity automation
--  Depends on: 02_tables.sql
--
--  Trigger inventory
--    1. updated_at stamping on every mutable table
--    2. ULPIN segment synchronisation (code <-> decomposed columns)
--    3. Ownership share reconciliation (active shares must sum to exactly 1)
--    4. Issued-record immutability
--    5. Denormalised counter maintenance
--    6. Derived geometry and measurement population
--    7. Audit capture with a tamper-evident hash chain
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- =============================================================================
-- 1. updated_at
-- =============================================================================
CREATE TRIGGER trg_users_touch          BEFORE UPDATE ON users              FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_refresh_touch        BEFORE UPDATE ON refresh_tokens     FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_owners_touch         BEFORE UPDATE ON owners             FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_buildings_touch      BEFORE UPDATE ON buildings          FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_floors_touch         BEFORE UPDATE ON floors             FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_units_touch          BEFORE UPDATE ON units              FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_ownership_touch      BEFORE UPDATE ON unit_ownerships    FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_tenants_touch        BEFORE UPDATE ON tenants            FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_ulpins_touch         BEFORE UPDATE ON ulpins             FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_verif_touch          BEFORE UPDATE ON verification_records FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
CREATE TRIGGER trg_fraud_touch          BEFORE UPDATE ON fraud_alerts       FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

-- =============================================================================
-- 2. ULPIN segment synchronisation
-- =============================================================================
-- Keeps the decomposed columns in lockstep with ulpin_code so neither can drift.
-- Segments are derived from the code, never the reverse: the code is the artefact
-- that appears on a deed.
CREATE OR REPLACE FUNCTION fn_ulpin_sync_segments()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_parsed record;
BEGIN
    NEW.ulpin_code := upper(trim(NEW.ulpin_code));

    SELECT * INTO v_parsed FROM fn_ulpin_parse(NEW.ulpin_code);

    IF NOT v_parsed.is_valid THEN
        RAISE EXCEPTION 'ULPIN % fails checksum or structural validation', NEW.ulpin_code
            USING ERRCODE = 'check_violation', HINT = 'Mint identifiers through fn_ulpin_checksum';
    END IF;

    NEW.parent_ulpin := v_parsed.parent_ulpin;
    NEW.block_code   := v_parsed.block_code;
    NEW.storey_code  := v_parsed.storey_code;
    NEW.unit_code    := v_parsed.unit_code;
    NEW.check_char   := v_parsed.check_char;

    -- The storey segment must name the floor the identifier is attached to.
    IF NEW.floor_id IS NOT NULL AND NEW.storey_code IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM floors f
            WHERE f.floor_id = NEW.floor_id AND f.storey_code = NEW.storey_code
        ) THEN
            RAISE EXCEPTION 'ULPIN % claims storey % but its floor is coded differently',
                NEW.ulpin_code, NEW.storey_code
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Likewise the parent prefix must be the parcel the building actually sits on.
    IF NEW.building_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM buildings b
            WHERE b.building_id = NEW.building_id AND b.parcel_ulpin = NEW.parent_ulpin
        ) THEN
            RAISE EXCEPTION 'ULPIN % has parent parcel % which does not match its building',
                NEW.ulpin_code, NEW.parent_ulpin
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ulpin_segments
    BEFORE INSERT OR UPDATE OF ulpin_code, floor_id, building_id ON ulpins
    FOR EACH ROW EXECUTE FUNCTION fn_ulpin_sync_segments();

-- Status transitions ---------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_ulpin_status_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_blocking int;
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;

    -- Legal transitions only. Anything else is a bug or an attack.
    IF NOT (
        (OLD.status = 'DRAFT'       AND NEW.status IN ('PROVISIONAL', 'RETIRED'))
     OR (OLD.status = 'PROVISIONAL' AND NEW.status IN ('VERIFIED', 'DRAFT', 'RETIRED'))
     OR (OLD.status = 'VERIFIED'    AND NEW.status IN ('ISSUED', 'PROVISIONAL', 'RETIRED'))
     OR (OLD.status = 'ISSUED'      AND NEW.status IN ('SUSPENDED', 'SUPERSEDED', 'RETIRED'))
     OR (OLD.status = 'SUSPENDED'   AND NEW.status IN ('ISSUED', 'SUPERSEDED', 'RETIRED'))
    ) THEN
        RAISE EXCEPTION 'Illegal ULPIN status transition % -> % for %',
            OLD.status, NEW.status, NEW.ulpin_code
            USING ERRCODE = 'check_violation';
    END IF;

    -- An unresolved high-severity alert blocks issuance outright.
    IF NEW.status = 'ISSUED' AND NEW.unit_id IS NOT NULL THEN
        SELECT count(*) INTO v_blocking
        FROM fraud_alerts
        WHERE unit_id = NEW.unit_id
          AND status   IN ('OPEN', 'INVESTIGATING', 'CONFIRMED')
          AND severity IN ('HIGH', 'CRITICAL');

        IF v_blocking > 0 THEN
            RAISE EXCEPTION 'Cannot issue %: % unresolved high-severity fraud alert(s)',
                NEW.ulpin_code, v_blocking
                USING ERRCODE = 'check_violation';
        END IF;

        NEW.issued_at := COALESCE(NEW.issued_at, now());
    END IF;

    IF NEW.status = 'SUPERSEDED' THEN
        NEW.superseded_at := COALESCE(NEW.superseded_at, now());
    END IF;

    IF NEW.status = 'RETIRED' THEN
        NEW.retired_at := COALESCE(NEW.retired_at, now());
        IF NEW.retirement_reason IS NULL THEN
            RAISE EXCEPTION 'Retiring % requires a reason', NEW.ulpin_code
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ulpin_status
    BEFORE UPDATE OF status ON ulpins
    FOR EACH ROW EXECUTE FUNCTION fn_ulpin_status_guard();

-- =============================================================================
-- 3. Ownership share reconciliation
-- =============================================================================
-- Active shares on a unit must total exactly 1. Runs as a CONSTRAINT TRIGGER so
-- it fires at COMMIT: a transfer that removes one owner and adds another passes
-- through an invalid intermediate state, which is legitimate.
CREATE OR REPLACE FUNCTION fn_ownership_share_sum()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_unit  uuid := COALESCE(NEW.unit_id, OLD.unit_id);
    v_total numeric(12,9);
    v_count int;
BEGIN
    SELECT COALESCE(sum(share), 0), count(*)
      INTO v_total, v_count
      FROM unit_ownerships
     WHERE unit_id = v_unit AND is_active;

    -- Zero active owners is allowed: an unsold or government-held unit.
    IF v_count = 0 THEN
        RETURN NULL;
    END IF;

    IF v_total <> 1.0 THEN
        RAISE EXCEPTION 'Active ownership shares for unit % sum to %, expected exactly 1',
            v_unit, v_total
            USING ERRCODE = 'check_violation',
                  HINT = 'Adjust the co-owners'' shares within the same transaction';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_ownership_share_sum
    AFTER INSERT OR UPDATE OR DELETE ON unit_ownerships
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_ownership_share_sum();

-- Exactly one primary owner per unit, for correspondence and notices.
CREATE OR REPLACE FUNCTION fn_ownership_single_primary()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.is_primary AND NEW.is_active THEN
        UPDATE unit_ownerships
           SET is_primary = false
         WHERE unit_id     = NEW.unit_id
           AND ownership_id <> NEW.ownership_id
           AND is_primary;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ownership_primary
    AFTER INSERT OR UPDATE OF is_primary ON unit_ownerships
    FOR EACH ROW WHEN (NEW.is_primary) EXECUTE FUNCTION fn_ownership_single_primary();

-- =============================================================================
-- 4. Immutability of issued records
-- =============================================================================
-- Once a unit's identifier is ISSUED, its geometry and areas are part of a legal
-- record. Corrections go through supersession, not UPDATE.
CREATE OR REPLACE FUNCTION fn_units_protect_issued()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_is_issued boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM ulpins
        WHERE unit_id = OLD.unit_id AND status = 'ISSUED'
    ) INTO v_is_issued;

    IF NOT v_is_issued THEN
        RETURN NEW;
    END IF;

    IF NEW.volume_solid  IS DISTINCT FROM OLD.volume_solid
    OR NEW.floor_plate   IS DISTINCT FROM OLD.floor_plate
    OR NEW.carpet_area_sqm  IS DISTINCT FROM OLD.carpet_area_sqm
    OR NEW.base_elevation_m IS DISTINCT FROM OLD.base_elevation_m
    OR NEW.top_elevation_m  IS DISTINCT FROM OLD.top_elevation_m
    THEN
        RAISE EXCEPTION 'Unit % has an ISSUED ULPIN; geometry and area are immutable', OLD.unit_id
            USING ERRCODE = 'check_violation',
                  HINT = 'Supersede the identifier and mint a corrected one';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_units_protect
    BEFORE UPDATE ON units
    FOR EACH ROW EXECUTE FUNCTION fn_units_protect_issued();

-- An issued identifier's code can never be rewritten in place.
CREATE OR REPLACE FUNCTION fn_ulpins_protect_code()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('ISSUED', 'SUPERSEDED', 'RETIRED')
       AND NEW.ulpin_code IS DISTINCT FROM OLD.ulpin_code THEN
        RAISE EXCEPTION 'ULPIN % is % and its code cannot be modified', OLD.ulpin_code, OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ulpins_protect
    BEFORE UPDATE ON ulpins
    FOR EACH ROW EXECUTE FUNCTION fn_ulpins_protect_code();

-- =============================================================================
-- 5. Denormalised counters
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_sync_unit_counts()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_floor    uuid := COALESCE(NEW.floor_id, OLD.floor_id);
    v_building uuid := COALESCE(NEW.building_id, OLD.building_id);
BEGIN
    UPDATE floors
       SET total_units = (
               SELECT count(*) FROM units
                WHERE floor_id = v_floor AND deleted_at IS NULL
           )
     WHERE floor_id = v_floor;

    UPDATE buildings
       SET total_units = (
               SELECT count(*) FROM units
                WHERE building_id = v_building AND deleted_at IS NULL
           )
     WHERE building_id = v_building;

    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_units_count
    AFTER INSERT OR DELETE OR UPDATE OF floor_id, building_id, deleted_at ON units
    FOR EACH ROW EXECUTE FUNCTION fn_sync_unit_counts();

-- =============================================================================
-- 6. Derived geometry and measurements
-- =============================================================================
-- Fills in what the client did not supply. Never overwrites an explicit value:
-- a surveyed figure always outranks a computed one.
CREATE OR REPLACE FUNCTION fn_units_derive_geometry()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_srid int;
BEGIN
    IF NEW.volume_solid IS NULL AND NEW.floor_plate IS NOT NULL THEN
        NEW.volume_solid := fn_make_prism(
            ST_Force2D(NEW.floor_plate),
            NEW.base_elevation_m,
            NEW.top_elevation_m
        );
    END IF;

    IF NEW.centroid_3d IS NULL AND NEW.floor_plate IS NOT NULL THEN
        NEW.centroid_3d := ST_Force3D(
            ST_PointOnSurface(ST_Force2D(NEW.floor_plate)),
            (NEW.base_elevation_m + NEW.top_elevation_m) / 2.0
        );
    END IF;

    IF NEW.volume_cum IS NULL AND NEW.volume_solid IS NOT NULL THEN
        SELECT b.metric_srid INTO v_srid FROM buildings b WHERE b.building_id = NEW.building_id;
        BEGIN
            NEW.volume_cum := fn_volume_cum(NEW.volume_solid, COALESCE(v_srid, 7755));
        EXCEPTION WHEN OTHERS THEN
            -- A non-manifold solid is a validation finding, not a reason to
            -- reject the insert; the geometry stage reports it properly.
            NEW.volume_cum     := NULL;
            NEW.is_solid_valid := false;
        END;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_units_derive
    BEFORE INSERT OR UPDATE OF floor_plate, base_elevation_m, top_elevation_m ON units
    FOR EACH ROW EXECUTE FUNCTION fn_units_derive_geometry();

CREATE OR REPLACE FUNCTION fn_buildings_derive_geometry()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.location IS NULL AND NEW.footprint IS NOT NULL THEN
        NEW.location := ST_PointOnSurface(ST_Force2D(NEW.footprint));
    END IF;

    IF NEW.envelope_solid IS NULL AND NEW.footprint IS NOT NULL AND NEW.height_m IS NOT NULL THEN
        NEW.envelope_solid := fn_make_prism(
            ST_Force2D(NEW.footprint),
            NEW.ground_elevation_m - COALESCE(NEW.basement_floors, 0) * 3.0,
            NEW.ground_elevation_m + NEW.height_m
        );
    END IF;

    IF NEW.plot_area_sqm IS NULL AND NEW.footprint IS NOT NULL THEN
        NEW.plot_area_sqm := fn_area_sqm(NEW.footprint, NEW.metric_srid);
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_buildings_derive
    BEFORE INSERT OR UPDATE OF footprint, height_m, ground_elevation_m ON buildings
    FOR EACH ROW EXECUTE FUNCTION fn_buildings_derive_geometry();

-- =============================================================================
-- 7. Audit capture with hash chaining
-- =============================================================================
-- Generic row-level audit. Attach to any table; the payload is the changed
-- columns only, so a wide table does not produce a wide log row.
--
-- The hash chain makes retroactive edits detectable: each row commits to the
-- previous row's hash, so altering history invalidates every subsequent link.
-- Row-to-jsonb that survives 3D geometry.
--
-- to_jsonb() on a composite routes each column through its cast to json, and
-- PostGIS registers geometry -> json as GeoJSON. GeoJSON has no PolyhedralSurface
-- member, so the cast raises "'PolyhedralSurface' geometry type not supported"
-- and, from inside an AFTER trigger, aborts the write that fired it. Since every
-- solid in this schema (envelope_solid, slab_solid, volume_solid) is a
-- PolyhedralSurfaceZ, auditing any building, floor or unit would be impossible.
--
-- Geometry columns are rendered as EWKT instead, which is lossless, carries the
-- SRID, and is readable in the log. The column list is resolved from the relation
-- oid rather than pg_typeof(), so this works for any audited table.
CREATE OR REPLACE FUNCTION fn_audit_jsonb(p_rec anyelement, p_relid oid)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_cols text;
    v_out  jsonb;
BEGIN
    SELECT string_agg(
               CASE WHEN a.atttypid IN ('geometry'::regtype, 'geography'::regtype)
                    THEN format('ST_AsEWKT(%I) AS %I', a.attname, a.attname)
                    ELSE format('%I', a.attname)
               END,
               ', ' ORDER BY a.attnum)
      INTO v_cols
      FROM pg_attribute a
     WHERE a.attrelid = p_relid
       AND a.attnum > 0
       AND NOT a.attisdropped;

    EXECUTE format(
        'SELECT to_jsonb(t) FROM (SELECT %s FROM (SELECT $1.*) AS r) AS t', v_cols
    ) INTO v_out USING p_rec;

    RETURN v_out;
END;
$$;

COMMENT ON FUNCTION fn_audit_jsonb(anyelement, oid) IS
    'Serialises a table row to jsonb with geometry columns as EWKT, because to_jsonb() cannot represent PolyhedralSurface.';

CREATE OR REPLACE FUNCTION fn_audit_capture()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ulpin, public
AS $$
DECLARE
    v_old        jsonb;
    v_new        jsonb;
    v_changed    text[];
    v_action     audit_action;
    v_entity_id  uuid;
    v_ulpin      varchar(32);
    v_actor      uuid;
    v_role       user_role;
    v_prev_hash  bytea;
    v_row_hash   bytea;
    v_payload    text;
    v_full       jsonb;
BEGIN
    -- The application sets these GUCs per request in its tenant middleware.
    BEGIN
        v_actor := NULLIF(current_setting('app.current_user_id', true), '')::uuid;
        v_role  := NULLIF(current_setting('app.current_user_role', true), '')::user_role;
    EXCEPTION WHEN OTHERS THEN
        v_actor := NULL;
        v_role  := NULL;
    END;

    -- Serialise the surviving row once and reuse it for the payload, the entity
    -- id and the ULPIN. COALESCE(NEW, OLD) is avoided deliberately: it forces a
    -- third serialisation of a row that may carry several megabytes of solid.
    IF TG_OP = 'DELETE' THEN
        v_full := fn_audit_jsonb(OLD, TG_RELID);
    ELSE
        v_full := fn_audit_jsonb(NEW, TG_RELID);
    END IF;

    IF TG_OP = 'INSERT' THEN
        v_action := 'INSERT';
        v_new    := v_full;
        v_old    := NULL;
    ELSIF TG_OP = 'UPDATE' THEN
        v_action := 'UPDATE';
        v_old    := fn_audit_jsonb(OLD, TG_RELID);
        v_new    := v_full;

        SELECT array_agg(key ORDER BY key) INTO v_changed
        FROM jsonb_each(v_new) n
        WHERE n.value IS DISTINCT FROM (v_old -> n.key);

        -- Nothing but updated_at moved; not worth a log row.
        IF v_changed IS NULL OR v_changed = ARRAY['updated_at'] THEN
            RETURN NULL;
        END IF;

        -- Keep only the columns that actually changed.
        SELECT jsonb_object_agg(k, v_old -> k) INTO v_old FROM unnest(v_changed) k;
        SELECT jsonb_object_agg(k, v_new -> k) INTO v_new FROM unnest(v_changed) k;
    ELSE
        v_action := 'DELETE';
        v_old    := v_full;
        v_new    := NULL;
    END IF;

    v_entity_id := (v_full ->> (TG_ARGV[0]))::uuid;
    v_ulpin     := v_full ->> 'ulpin_code';

    -- Geometry serialises to a large EWKB hex string; hashing it wholesale would
    -- dominate the log. Hash the payload digest instead.
    SELECT row_hash INTO v_prev_hash
      FROM audit_logs
     ORDER BY created_at DESC, log_id DESC
     LIMIT 1;

    v_payload := COALESCE(v_actor::text, 'system') || '|' || v_action::text || '|' ||
                 TG_TABLE_NAME || '|' || COALESCE(v_entity_id::text, '') || '|' ||
                 COALESCE(v_old::text, '') || '|' || COALESCE(v_new::text, '');

    v_row_hash := digest(COALESCE(encode(v_prev_hash, 'hex'), '') || v_payload, 'sha256');

    INSERT INTO audit_logs (
        actor_user_id, actor_role, action, entity_type, entity_id, ulpin_code,
        old_values, new_values, changed_fields,
        request_id, row_hash, prev_hash
    ) VALUES (
        v_actor, v_role, v_action, TG_TABLE_NAME, v_entity_id, v_ulpin,
        v_old, v_new, v_changed,
        NULLIF(current_setting('app.request_id', true), ''),
        v_row_hash, v_prev_hash
    );

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION fn_audit_capture() IS
    'Generic audit trigger. TG_ARGV[0] is the audited table''s primary-key column name.';

-- Attach to the tables that carry legal weight. Deliberately not on audit_logs
-- itself, and not on refresh_tokens (high churn, no legal value).
CREATE TRIGGER trg_audit_users        AFTER INSERT OR UPDATE OR DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('user_id');
CREATE TRIGGER trg_audit_owners       AFTER INSERT OR UPDATE OR DELETE ON owners
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('owner_id');
CREATE TRIGGER trg_audit_buildings    AFTER INSERT OR UPDATE OR DELETE ON buildings
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('building_id');
CREATE TRIGGER trg_audit_floors       AFTER INSERT OR UPDATE OR DELETE ON floors
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('floor_id');
CREATE TRIGGER trg_audit_units        AFTER INSERT OR UPDATE OR DELETE ON units
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('unit_id');
CREATE TRIGGER trg_audit_ownerships   AFTER INSERT OR UPDATE OR DELETE ON unit_ownerships
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('ownership_id');
CREATE TRIGGER trg_audit_tenants      AFTER INSERT OR UPDATE OR DELETE ON tenants
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('tenant_id');
CREATE TRIGGER trg_audit_ulpins       AFTER INSERT OR UPDATE OR DELETE ON ulpins
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('ulpin_id');
CREATE TRIGGER trg_audit_verif        AFTER INSERT OR UPDATE OR DELETE ON verification_records
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('verification_id');
CREATE TRIGGER trg_audit_fraud        AFTER INSERT OR UPDATE OR DELETE ON fraud_alerts
    FOR EACH ROW EXECUTE FUNCTION fn_audit_capture('alert_id');

-- Append-only enforcement.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM ulpin_app, ulpin_readonly;
GRANT  INSERT, SELECT             ON audit_logs TO   ulpin_app;
GRANT  SELECT                     ON audit_logs TO   ulpin_readonly;

-- Chain verification. Returns the first row where the recomputed link breaks.
CREATE OR REPLACE FUNCTION fn_audit_verify_chain(p_from timestamptz DEFAULT NULL)
RETURNS TABLE (log_id uuid, created_at timestamptz, expected bytea, actual bytea)
LANGUAGE sql
STABLE
AS $$
    WITH ordered AS (
        SELECT a.log_id,
               a.created_at,
               a.row_hash,
               a.prev_hash,
               lag(a.row_hash) OVER (ORDER BY a.created_at, a.log_id) AS computed_prev
        FROM audit_logs a
        WHERE p_from IS NULL OR a.created_at >= p_from
    )
    SELECT o.log_id, o.created_at, o.computed_prev, o.prev_hash
    FROM ordered o
    WHERE o.prev_hash IS DISTINCT FROM o.computed_prev
    ORDER BY o.created_at
    LIMIT 100;
$$;

-- =============================================================================
-- 8. Login throttling
-- =============================================================================
-- Locks an account for 15 minutes after 5 consecutive failures. Kept in the
-- database so the policy holds regardless of which API instance handled the
-- attempt — a per-process counter is trivially bypassed by load balancing.
CREATE OR REPLACE FUNCTION fn_users_lockout()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.failed_login_attempts >= 5 AND OLD.failed_login_attempts < 5 THEN
        NEW.locked_until := now() + INTERVAL '15 minutes';
        NEW.status := CASE WHEN NEW.status = 'ACTIVE' THEN 'LOCKED' ELSE NEW.status END;
    END IF;

    IF NEW.failed_login_attempts = 0 AND OLD.failed_login_attempts > 0 THEN
        NEW.locked_until := NULL;
        NEW.status := CASE WHEN NEW.status = 'LOCKED' THEN 'ACTIVE' ELSE NEW.status END;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_lockout
    BEFORE UPDATE OF failed_login_attempts ON users
    FOR EACH ROW EXECUTE FUNCTION fn_users_lockout();

-- Rotating a password invalidates every outstanding session.
CREATE OR REPLACE FUNCTION fn_users_password_rotation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
        NEW.password_changed_at   := now();
        NEW.token_version         := OLD.token_version + 1;
        NEW.reset_token_hash      := NULL;
        NEW.reset_token_expires_at := NULL;
        NEW.failed_login_attempts := 0;
        NEW.locked_until          := NULL;

        UPDATE refresh_tokens
           SET revoked_at = now()
         WHERE user_id = NEW.user_id AND revoked_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_users_password
    BEFORE UPDATE OF password_hash ON users
    FOR EACH ROW EXECUTE FUNCTION fn_users_password_rotation();
