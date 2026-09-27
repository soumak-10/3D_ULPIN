-- =============================================================================
--  03_indexes.sql — indexes, including all GiST spatial indexes
--  Depends on: 02_tables.sql
--
--  Index families used here
--    btree  — equality / range / sort on scalars
--    gist   — 2D spatial (default operator class)
--    gist + gist_geometry_ops_nd — true 3D bounding-box search on solids
--    gin    — jsonb containment, array membership, trigram text search
--  Partial indexes carry a WHERE clause so soft-deleted and historical rows do
--  not bloat the hot path.
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- =============================================================================
-- users
-- =============================================================================
CREATE INDEX idx_users_role            ON users (role) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_status          ON users (status) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_jurisdiction    ON users (jurisdiction_code) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_last_login      ON users (last_login_at DESC NULLS LAST);
CREATE INDEX idx_users_created_at      ON users (created_at DESC);

-- Case-insensitive login lookup. The hot path of every authentication request.
CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email)) WHERE deleted_at IS NULL;

-- Password-reset and email-verification lookups are by token hash, and only
-- a handful of rows ever carry one.
CREATE INDEX idx_users_reset_token  ON users (reset_token_hash)  WHERE reset_token_hash  IS NOT NULL;
CREATE INDEX idx_users_verify_token ON users (verify_token_hash) WHERE verify_token_hash IS NOT NULL;

-- Surfaces currently locked-out accounts for the security dashboard.
CREATE INDEX idx_users_locked ON users (locked_until) WHERE locked_until IS NOT NULL;

CREATE INDEX idx_users_name_trgm ON users USING gin (full_name gin_trgm_ops);

-- refresh_tokens
CREATE INDEX idx_refresh_user    ON refresh_tokens (user_id);
CREATE INDEX idx_refresh_expires ON refresh_tokens (expires_at);
CREATE INDEX idx_refresh_active  ON refresh_tokens (user_id, expires_at) WHERE revoked_at IS NULL;

-- =============================================================================
-- owners
-- =============================================================================
CREATE INDEX idx_owners_user      ON owners (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_owners_type      ON owners (owner_type) WHERE deleted_at IS NULL;
CREATE INDEX idx_owners_verified  ON owners (is_verified) WHERE deleted_at IS NULL;
CREATE INDEX idx_owners_email     ON owners (lower(email)) WHERE email IS NOT NULL;
CREATE INDEX idx_owners_phone     ON owners (phone) WHERE phone IS NOT NULL;
CREATE INDEX idx_owners_created   ON owners (created_at DESC);

-- Fuzzy name search for the registry counter, where spellings vary.
CREATE INDEX idx_owners_name_trgm ON owners USING gin (full_name gin_trgm_ops);

-- Identity-collision detection: the same statutory ID appearing under different
-- names is the strongest single signal for IDENTITY_MISMATCH.
CREATE INDEX idx_owners_national_id ON owners (national_id_hash) WHERE national_id_hash IS NOT NULL;

-- SPATIAL: clusters owners by correspondence address.
CREATE INDEX idx_owners_location_gist
    ON owners USING gist (address_location)
    WHERE address_location IS NOT NULL;

CREATE INDEX idx_owners_high_risk ON owners (risk_score DESC)
    WHERE risk_score IS NOT NULL AND risk_score > 0.7;

-- =============================================================================
-- buildings
-- =============================================================================
CREATE INDEX idx_buildings_parcel       ON buildings (parcel_ulpin);
CREATE INDEX idx_buildings_jurisdiction ON buildings (jurisdiction_code) WHERE deleted_at IS NULL;
CREATE INDEX idx_buildings_status       ON buildings (status) WHERE deleted_at IS NULL;
CREATE INDEX idx_buildings_use          ON buildings (building_use);
CREATE INDEX idx_buildings_creator      ON buildings (created_by);
CREATE INDEX idx_buildings_created      ON buildings (created_at DESC);

-- Common list screen: "buildings awaiting verification in my jurisdiction".
CREATE INDEX idx_buildings_pending
    ON buildings (jurisdiction_code, status, created_at DESC)
    WHERE status = 'UNDER_VERIFICATION' AND deleted_at IS NULL;

-- SPATIAL -------------------------------------------------------------------
-- Footprint: drives bbox queries, vector tiles and parcel containment tests.
CREATE INDEX idx_buildings_footprint_gist
    ON buildings USING gist (footprint);

-- Point centroid: cheaper than the polygon for map pins at low zoom.
CREATE INDEX idx_buildings_location_gist
    ON buildings USING gist (location)
    WHERE location IS NOT NULL;

-- 3D envelope. The _nd operator class indexes the full n-dimensional bounding
-- box, so &&& (3D overlap) can use the index. A plain gist index would only
-- consider X/Y and every vertical query would degrade to a sequential scan.
CREATE INDEX idx_buildings_envelope_nd
    ON buildings USING gist (envelope_solid gist_geometry_ops_nd)
    WHERE envelope_solid IS NOT NULL;

-- =============================================================================
-- floors
-- =============================================================================
CREATE INDEX idx_floors_building      ON floors (building_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_floors_number        ON floors (building_id, floor_number);
CREATE INDEX idx_floors_type          ON floors (floor_type);
CREATE INDEX idx_floors_storey_code   ON floors (storey_code);
CREATE INDEX idx_floors_elevation     ON floors (building_id, elevation_m);

-- SPATIAL
CREATE INDEX idx_floors_slab_gist     ON floors USING gist (slab_geom);
CREATE INDEX idx_floors_slab_solid_nd
    ON floors USING gist (slab_solid gist_geometry_ops_nd)
    WHERE slab_solid IS NOT NULL;

-- =============================================================================
-- units
-- =============================================================================
CREATE INDEX idx_units_floor        ON units (floor_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_units_building     ON units (building_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_units_status       ON units (status) WHERE deleted_at IS NULL;
CREATE INDEX idx_units_type         ON units (unit_type);
CREATE INDEX idx_units_occupancy    ON units (occupancy);
CREATE INDEX idx_units_number       ON units (building_id, unit_number);
CREATE INDEX idx_units_created      ON units (created_at DESC);

-- Vertical range scan: "every unit between 40 m and 80 m in this building".
CREATE INDEX idx_units_elevation    ON units (building_id, base_elevation_m, top_elevation_m);

-- Units whose geometry has not passed validation — the generation worklist.
CREATE INDEX idx_units_unvalidated
    ON units (building_id, last_validated_at NULLS FIRST)
    WHERE NOT is_solid_valid AND deleted_at IS NULL;

CREATE INDEX idx_units_disputed ON units (building_id) WHERE status = 'DISPUTED';

-- SPATIAL -------------------------------------------------------------------
CREATE INDEX idx_units_plate_gist
    ON units USING gist (floor_plate);

CREATE INDEX idx_units_centroid_gist
    ON units USING gist (centroid_3d)
    WHERE centroid_3d IS NOT NULL;

-- The index the whole vertical model depends on. Pairwise overlap detection
-- across a 200-unit tower is O(n^2) without it.
CREATE INDEX idx_units_volume_nd
    ON units USING gist (volume_solid gist_geometry_ops_nd)
    WHERE volume_solid IS NOT NULL;

-- 2D GiST on the solid as well: many queries ask "what is in this map extent"
-- and never touch Z, where the 2D operator class is the cheaper plan.
CREATE INDEX idx_units_volume_2d
    ON units USING gist (volume_solid)
    WHERE volume_solid IS NOT NULL;

-- =============================================================================
-- unit_ownerships
-- =============================================================================
CREATE INDEX idx_ownership_unit   ON unit_ownerships (unit_id);
CREATE INDEX idx_ownership_owner  ON unit_ownerships (owner_id);
CREATE INDEX idx_ownership_active ON unit_ownerships (unit_id, owner_id) WHERE is_active;
CREATE INDEX idx_ownership_dates  ON unit_ownerships (acquired_on DESC);

-- RAPID_TRANSFER detection: recent acquisitions, newest first.
-- Deliberately not a partial index. `WHERE acquired_on > CURRENT_DATE -
-- INTERVAL '2 years'` is rejected: CURRENT_DATE is STABLE, not IMMUTABLE, and a
-- predicate that moves with the clock would quietly exclude rows the index
-- already contains. The full index serves the same query — the planner applies
-- the two-year cutoff as a filter after the index scan on (unit_id,
-- acquired_on DESC), which is already ordered for it.
CREATE INDEX idx_ownership_recent
    ON unit_ownerships (unit_id, acquired_on DESC);

-- =============================================================================
-- tenants
-- =============================================================================
CREATE INDEX idx_tenants_unit      ON tenants (unit_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_tenants_user      ON tenants (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX idx_tenants_landlord  ON tenants (landlord_owner_id);
CREATE INDEX idx_tenants_status    ON tenants (status) WHERE deleted_at IS NULL;
CREATE INDEX idx_tenants_lease     ON tenants (lease_start, lease_end);
CREATE INDEX idx_tenants_name_trgm ON tenants USING gin (full_name gin_trgm_ops);

-- Active tenancies expiring soon — drives the renewal reminder job.
CREATE INDEX idx_tenants_expiring
    ON tenants (lease_end)
    WHERE status = 'ACTIVE';

-- =============================================================================
-- ulpins
-- =============================================================================
-- uq_ulpins_code already provides the unique btree on the full identifier.
CREATE INDEX idx_ulpins_parent        ON ulpins (parent_ulpin);
CREATE INDEX idx_ulpins_type          ON ulpins (ulpin_type);
CREATE INDEX idx_ulpins_status        ON ulpins (status);
CREATE INDEX idx_ulpins_building      ON ulpins (building_id) WHERE building_id IS NOT NULL;
CREATE INDEX idx_ulpins_floor         ON ulpins (floor_id)    WHERE floor_id    IS NOT NULL;
CREATE INDEX idx_ulpins_unit          ON ulpins (unit_id)     WHERE unit_id     IS NOT NULL;
CREATE INDEX idx_ulpins_lineage       ON ulpins (parent_ulpin_id) WHERE parent_ulpin_id IS NOT NULL;
CREATE INDEX idx_ulpins_supersedes    ON ulpins (supersedes_id)   WHERE supersedes_id   IS NOT NULL;
CREATE INDEX idx_ulpins_jurisdiction  ON ulpins (jurisdiction_code);
CREATE INDEX idx_ulpins_issued        ON ulpins (issued_at DESC) WHERE status = 'ISSUED';

-- Public lookup only ever resolves live identifiers.
CREATE INDEX idx_ulpins_live
    ON ulpins (ulpin_code)
    WHERE status IN ('VERIFIED', 'ISSUED');

-- Segment search: "every unit on floor F007 of this parcel".
CREATE INDEX idx_ulpins_segments ON ulpins (parent_ulpin, block_code, storey_code);

-- Prefix search for partially typed identifiers in the search bar.
CREATE INDEX idx_ulpins_code_trgm ON ulpins USING gin (ulpin_code gin_trgm_ops);

-- SPATIAL
CREATE INDEX idx_ulpins_centroid_gist
    ON ulpins USING gist (centroid)
    WHERE centroid IS NOT NULL;

-- =============================================================================
-- verification_records
-- =============================================================================
CREATE INDEX idx_verif_ulpin     ON verification_records (ulpin_id);
CREATE INDEX idx_verif_unit      ON verification_records (unit_id)     WHERE unit_id     IS NOT NULL;
CREATE INDEX idx_verif_building  ON verification_records (building_id) WHERE building_id IS NOT NULL;
CREATE INDEX idx_verif_owner     ON verification_records (owner_id)    WHERE owner_id    IS NOT NULL;
CREATE INDEX idx_verif_status    ON verification_records (status);
CREATE INDEX idx_verif_type      ON verification_records (verification_type);
CREATE INDEX idx_verif_verifier  ON verification_records (verified_by, verified_at DESC);
CREATE INDEX idx_verif_requested ON verification_records (requested_at DESC);

-- The officer's queue.
CREATE INDEX idx_verif_pending
    ON verification_records (verification_type, requested_at)
    WHERE status IN ('PENDING', 'IN_PROGRESS');

-- Verifications that lapse if not completed.
CREATE INDEX idx_verif_expiring
    ON verification_records (expires_at)
    WHERE expires_at IS NOT NULL AND status <> 'VERIFIED';

CREATE INDEX idx_verif_findings_gin ON verification_records USING gin (findings jsonb_path_ops);

-- SPATIAL: where the verification was physically captured.
CREATE INDEX idx_verif_location_gist
    ON verification_records USING gist (captured_location)
    WHERE captured_location IS NOT NULL;

-- =============================================================================
-- fraud_alerts
-- =============================================================================
CREATE INDEX idx_fraud_type       ON fraud_alerts (alert_type);
CREATE INDEX idx_fraud_severity   ON fraud_alerts (severity);
CREATE INDEX idx_fraud_status     ON fraud_alerts (status);
CREATE INDEX idx_fraud_ulpin      ON fraud_alerts (ulpin_id)    WHERE ulpin_id    IS NOT NULL;
CREATE INDEX idx_fraud_unit       ON fraud_alerts (unit_id)     WHERE unit_id     IS NOT NULL;
CREATE INDEX idx_fraud_building   ON fraud_alerts (building_id) WHERE building_id IS NOT NULL;
CREATE INDEX idx_fraud_owner      ON fraud_alerts (owner_id)    WHERE owner_id    IS NOT NULL;
CREATE INDEX idx_fraud_assignee   ON fraud_alerts (assigned_to, status) WHERE assigned_to IS NOT NULL;
CREATE INDEX idx_fraud_detected   ON fraud_alerts (detected_at DESC);
CREATE INDEX idx_fraud_rule       ON fraud_alerts (rule_code) WHERE rule_code IS NOT NULL;

-- The triage queue: open alerts, worst first. Severity is an enum, so DESC
-- orders CRITICAL before INFO by the declaration order in 01_types.sql.
CREATE INDEX idx_fraud_open_queue
    ON fraud_alerts (severity DESC, detected_at DESC)
    WHERE status IN ('OPEN', 'TRIAGED', 'INVESTIGATING');

-- Blocking check before a ULPIN may be issued.
CREATE INDEX idx_fraud_blocking
    ON fraud_alerts (unit_id)
    WHERE status IN ('OPEN', 'INVESTIGATING', 'CONFIRMED')
      AND severity IN ('HIGH', 'CRITICAL');

CREATE INDEX idx_fraud_evidence_gin ON fraud_alerts USING gin (evidence jsonb_path_ops);

-- SPATIAL: the fraud hotspot heatmap.
CREATE INDEX idx_fraud_location_gist
    ON fraud_alerts USING gist (alert_location)
    WHERE alert_location IS NOT NULL;

-- =============================================================================
-- audit_logs  (indexes propagate to every partition)
-- =============================================================================
CREATE INDEX idx_audit_actor    ON audit_logs (actor_user_id, created_at DESC);
CREATE INDEX idx_audit_entity   ON audit_logs (entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_action   ON audit_logs (action, created_at DESC);
CREATE INDEX idx_audit_created  ON audit_logs (created_at DESC);
CREATE INDEX idx_audit_request  ON audit_logs (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX idx_audit_ulpin    ON audit_logs (ulpin_code) WHERE ulpin_code IS NOT NULL;
CREATE INDEX idx_audit_failures ON audit_logs (created_at DESC) WHERE NOT success;
CREATE INDEX idx_audit_new_gin  ON audit_logs USING gin (new_values jsonb_path_ops);

-- =============================================================================
-- Statistics targets
-- =============================================================================
-- Geometry columns hold far more distinct values than the default 100-bucket
-- histogram can represent, and the planner's selectivity estimates for GiST
-- suffer badly as a result on large tables.
ALTER TABLE units     ALTER COLUMN volume_solid SET STATISTICS 1000;
ALTER TABLE units     ALTER COLUMN floor_plate  SET STATISTICS 1000;
ALTER TABLE buildings ALTER COLUMN footprint    SET STATISTICS 1000;

-- Correlated predicates: the planner otherwise multiplies these as independent.
CREATE STATISTICS st_units_building_floor  (dependencies) ON building_id, floor_id   FROM units;
CREATE STATISTICS st_units_status_type     (dependencies) ON status, unit_type       FROM units;
CREATE STATISTICS st_fraud_type_severity   (dependencies) ON alert_type, severity    FROM fraud_alerts;

ANALYZE;
