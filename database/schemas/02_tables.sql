-- =============================================================================
--  02_tables.sql — core schema
--  Depends on: 00_extensions.sql, 01_types.sql
--
--  Storage conventions
--    * Every geometry column is SRID 4326. Z is metres above the row's
--      vertical datum. Measurements are always taken after ST_Transform.
--    * Every table carries created_at / updated_at (timestamptz, UTC).
--    * UUID v4 primary keys — identifiers are minted across services and must
--      not collide or leak row counts.
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- =============================================================================
-- 1. users
-- =============================================================================
CREATE TABLE users (
    user_id                 uuid            PRIMARY KEY DEFAULT gen_random_uuid(),

    email                   email_address   NOT NULL,
    username                varchar(64),
    password_hash           text            NOT NULL,           -- argon2id
    full_name               varchar(200)    NOT NULL,
    phone                   phone_number,

    role                    user_role       NOT NULL DEFAULT 'TENANT',
    status                  user_status     NOT NULL DEFAULT 'PENDING',

    -- Jurisdiction scope. NULL means national scope for staff roles and is the
    -- normal state for OWNER / TENANT; enforced by ck_users_scope below.
    jurisdiction_code       varchar(16),

    -- Verification and recovery
    email_verified_at       timestamptz,
    phone_verified_at       timestamptz,
    reset_token_hash        text,                               -- sha256 of the raw token
    reset_token_expires_at  timestamptz,
    verify_token_hash       text,
    verify_token_expires_at timestamptz,

    -- Credential lifecycle
    password_changed_at     timestamptz     NOT NULL DEFAULT now(),
    must_change_password    boolean         NOT NULL DEFAULT false,

    -- Bumping this invalidates every outstanding refresh token for the user,
    -- which is what "log out everywhere" and forced password reset rely on.
    token_version           integer         NOT NULL DEFAULT 0,

    -- Brute-force throttling
    failed_login_attempts   smallint        NOT NULL DEFAULT 0,
    locked_until            timestamptz,
    last_login_at           timestamptz,
    last_login_ip           inet,

    mfa_enabled             boolean         NOT NULL DEFAULT false,
    mfa_secret_encrypted    bytea,

    created_at              timestamptz     NOT NULL DEFAULT now(),
    updated_at              timestamptz     NOT NULL DEFAULT now(),
    deleted_at              timestamptz,

    CONSTRAINT uq_users_email      UNIQUE (email),
    CONSTRAINT uq_users_username   UNIQUE (username),
    CONSTRAINT ck_users_attempts   CHECK (failed_login_attempts >= 0 AND failed_login_attempts <= 100),
    CONSTRAINT ck_users_token_ver  CHECK (token_version >= 0),
    -- A jurisdiction is a grant of authority over a set of parcels, so only the
    -- role that exercises such authority needs one. PROPERTY_OFFICER acts on
    -- records within a specific circle and must be scoped. ADMIN, AUDITOR and
    -- SERVICE are national. OWNER and TENANT are members of the public: they
    -- hold no authority, they self-register through a public form that asks for
    -- no code, and they generally do not know their circle's code — requiring
    -- one here made self-registration impossible.
    CONSTRAINT ck_users_scope      CHECK (
        jurisdiction_code IS NOT NULL OR role <> 'PROPERTY_OFFICER'
    ),
    CONSTRAINT ck_users_reset_pair CHECK (
        (reset_token_hash IS NULL) = (reset_token_expires_at IS NULL)
    )
);

COMMENT ON TABLE  users IS 'System principals. Authentication subjects for the JWT issuer.';
COMMENT ON COLUMN users.token_version IS 'Incremented to revoke all refresh tokens for this user.';
COMMENT ON COLUMN users.reset_token_hash IS 'SHA-256 of the password-reset token. The raw token is emailed and never stored.';

-- Refresh-token registry. Access tokens stay stateless; refresh tokens are
-- tracked so they can be revoked individually and reuse can be detected.
CREATE TABLE refresh_tokens (
    token_id        uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid         NOT NULL,
    token_hash      text         NOT NULL,
    issued_at       timestamptz  NOT NULL DEFAULT now(),
    expires_at      timestamptz  NOT NULL,
    revoked_at      timestamptz,
    replaced_by     uuid,                                   -- rotation chain
    user_agent      text,
    ip_address      inet,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT uq_refresh_hash   UNIQUE (token_hash),
    CONSTRAINT fk_refresh_user   FOREIGN KEY (user_id)     REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_refresh_next   FOREIGN KEY (replaced_by) REFERENCES refresh_tokens(token_id) ON DELETE SET NULL,
    CONSTRAINT ck_refresh_window CHECK (expires_at > issued_at)
);

COMMENT ON TABLE refresh_tokens IS 'One row per issued refresh token. A presented token whose row is already revoked indicates theft: revoke the whole chain.';

-- =============================================================================
-- 2. owners
-- =============================================================================
CREATE TABLE owners (
    owner_id            uuid            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Optional link to a login. Most owners in a legacy cadastre have no account.
    user_id             uuid,

    owner_type          owner_type      NOT NULL DEFAULT 'INDIVIDUAL',
    full_name           varchar(200)    NOT NULL,
    father_or_spouse    varchar(200),
    date_of_birth       date,

    -- Statutory identity is NEVER stored in the clear. Only a salted hash is kept,
    -- which supports duplicate detection without holding the number itself.
    national_id_hash    bytea,
    national_id_type    varchar(24),            -- 'AADHAAR' | 'PAN' | 'CIN' | 'PASSPORT'
    national_id_last4   char(4),                -- display only

    email               email_address,
    phone               phone_number,

    address_line1       varchar(200),
    address_line2       varchar(200),
    city                varchar(100),
    district            varchar(100),
    state               varchar(100),
    postal_code         varchar(12),
    country_code        char(2)         NOT NULL DEFAULT 'IN',

    -- Correspondence address as a point, for jurisdiction checks and fraud
    -- clustering (e.g. many "owners" resolving to one address).
    address_location    geometry(Point, 4326),

    is_verified         boolean         NOT NULL DEFAULT false,
    verified_at         timestamptz,
    risk_score          confidence_score,

    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT fk_owners_user       FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT ck_owners_dob        CHECK (date_of_birth IS NULL OR date_of_birth < CURRENT_DATE),
    CONSTRAINT ck_owners_id_pair    CHECK (
        (national_id_hash IS NULL) = (national_id_type IS NULL)
    ),
    CONSTRAINT ck_owners_location   CHECK (
        address_location IS NULL OR ST_SRID(address_location) = 4326
    )
);

COMMENT ON COLUMN owners.national_id_hash IS 'HMAC-SHA256 of the statutory ID under a server-side pepper. Enables duplicate detection without retaining the identifier.';

-- =============================================================================
-- 3. buildings
-- =============================================================================
CREATE TABLE buildings (
    building_id         uuid                PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Parent 2D cadastral parcel. Inherited from the existing cadastre, so it is
    -- stored as a plain code rather than an FK to a table this system owns.
    parcel_ulpin        ulpin_code          NOT NULL,
    jurisdiction_code   varchar(16)         NOT NULL,

    building_name       varchar(200),
    building_code       varchar(32),                        -- block label on the sanctioned plan, e.g. 'A1'
    address_line1       varchar(200),
    address_line2       varchar(200),
    city                varchar(100),
    postal_code         varchar(12),

    building_use        building_use        NOT NULL DEFAULT 'RESIDENTIAL',
    construction_status construction_status NOT NULL DEFAULT 'COMPLETED',
    status              building_status     NOT NULL DEFAULT 'DRAFT',

    total_floors        smallint            NOT NULL DEFAULT 1,      -- above ground
    basement_floors     smallint            NOT NULL DEFAULT 0,
    total_units         integer             NOT NULL DEFAULT 0,      -- maintained by trigger

    -- Vertical reference
    vertical_datum      varchar(24)         NOT NULL DEFAULT 'EGM2008',
    ground_elevation_m  numeric(8,3)        NOT NULL DEFAULT 0,      -- datum height at the plinth
    height_m            numeric(8,3),                                -- plinth to roof
    metric_srid         integer             NOT NULL DEFAULT 7755,

    plot_area_sqm       positive_area,
    built_up_area_sqm   positive_area,

    year_built          smallint,
    sanction_number     varchar(64),
    sanction_date       date,
    occupancy_cert_no   varchar(64),

    -- Geometry -----------------------------------------------------------------
    footprint           geometry(PolygonZ, 4326)            NOT NULL,
    envelope_solid      geometry(PolyhedralSurfaceZ, 4326),
    location            geometry(Point, 4326),                       -- centroid, for fast map pins

    created_by          uuid,
    verified_by         uuid,
    verified_at         timestamptz,

    created_at          timestamptz         NOT NULL DEFAULT now(),
    updated_at          timestamptz         NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT uq_buildings_code   UNIQUE (parcel_ulpin, building_code),
    CONSTRAINT fk_buildings_creator  FOREIGN KEY (created_by)     REFERENCES users(user_id)              ON DELETE SET NULL,
    CONSTRAINT fk_buildings_verifier FOREIGN KEY (verified_by)    REFERENCES users(user_id)              ON DELETE SET NULL,
    CONSTRAINT fk_buildings_datum    FOREIGN KEY (vertical_datum) REFERENCES vertical_datums(datum_code),

    CONSTRAINT ck_buildings_floors    CHECK (total_floors >= 0 AND total_floors <= 300),
    CONSTRAINT ck_buildings_basements CHECK (basement_floors >= 0 AND basement_floors <= 20),
    CONSTRAINT ck_buildings_units     CHECK (total_units >= 0),
    CONSTRAINT ck_buildings_height    CHECK (height_m IS NULL OR (height_m > 0 AND height_m <= 1200)),
    CONSTRAINT ck_buildings_year      CHECK (year_built IS NULL OR year_built BETWEEN 1600 AND 2200),
    CONSTRAINT ck_buildings_parcel    CHECK (parcel_ulpin ~ '^[0-9A-Z]{14}$'),
    CONSTRAINT ck_buildings_srid      CHECK (ST_SRID(footprint) = 4326),
    CONSTRAINT ck_buildings_ring      CHECK (ST_NPoints(footprint) >= 4),
    CONSTRAINT ck_buildings_valid     CHECK (ST_IsValid(ST_Force2D(footprint)))
);

COMMENT ON COLUMN buildings.parcel_ulpin IS 'The 14-character 2D ULPIN this building sits on. Every 3D identifier minted here inherits it as a prefix.';
COMMENT ON COLUMN buildings.metric_srid  IS 'Projected CRS used for all area and volume figures on this building and its descendants.';

-- =============================================================================
-- 4. floors
-- =============================================================================
CREATE TABLE floors (
    floor_id            uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id         uuid            NOT NULL,

    -- Signed level: -2 = second basement, 0 = ground, 7 = seventh floor.
    -- Follows the sanctioned plan, so a skipped floor 13 leaves a real gap.
    floor_number        smallint        NOT NULL,
    floor_type          floor_type      NOT NULL DEFAULT 'UPPER',

    -- Original label from the plan, preserved verbatim ('LG', 'G+1', '12A').
    label               varchar(24),

    -- Four-character storey segment of the ULPIN ('F007', 'B002', 'G000').
    storey_code         char(4)         NOT NULL,

    -- Elevations are relative to the building's vertical datum.
    elevation_m         numeric(8,3)    NOT NULL,       -- slab top
    floor_height_m      numeric(6,3)    NOT NULL,       -- floor to floor
    ceiling_height_m    numeric(6,3),                   -- clear internal height

    total_units         integer         NOT NULL DEFAULT 0,
    plate_area_sqm      positive_area,                  -- gross slab area
    common_area_sqm     numeric(14,4)   NOT NULL DEFAULT 0,

    is_habitable        boolean         NOT NULL DEFAULT true,

    slab_geom           geometry(PolygonZ, 4326)        NOT NULL,
    slab_solid          geometry(PolyhedralSurfaceZ, 4326),

    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT fk_floors_building FOREIGN KEY (building_id) REFERENCES buildings(building_id) ON DELETE CASCADE,

    CONSTRAINT uq_floors_number   UNIQUE (building_id, floor_number),
    CONSTRAINT uq_floors_storey   UNIQUE (building_id, storey_code),

    -- Composite target so children can prove their denormalised building_id is honest.
    CONSTRAINT uq_floors_identity UNIQUE (floor_id, building_id),

    CONSTRAINT ck_floors_number   CHECK (floor_number BETWEEN -20 AND 300),
    CONSTRAINT ck_floors_height   CHECK (floor_height_m > 0 AND floor_height_m <= 30),
    CONSTRAINT ck_floors_ceiling  CHECK (ceiling_height_m IS NULL OR ceiling_height_m <= floor_height_m),
    CONSTRAINT ck_floors_common   CHECK (common_area_sqm >= 0),
    CONSTRAINT ck_floors_code     CHECK (storey_code ~ '^[BGMFTA][0-9]{3}$'),
    CONSTRAINT ck_floors_code_num CHECK (storey_code = fn_storey_code(floor_number, floor_type)),
    CONSTRAINT ck_floors_type_num CHECK (
        (floor_type = 'BASEMENT' AND floor_number < 0) OR
        (floor_type IN ('GROUND', 'STILT', 'PODIUM') AND floor_number = 0) OR
        (floor_type IN ('UPPER', 'MEZZANINE', 'TERRACE', 'AIR_RIGHTS') AND floor_number >= 0)
    ),
    CONSTRAINT ck_floors_srid     CHECK (ST_SRID(slab_geom) = 4326)
);

COMMENT ON CONSTRAINT ck_floors_code_num ON floors IS
    'The stored storey code must be derivable from floor_number and floor_type, so the ULPIN segment can never drift from the floor it names.';

-- =============================================================================
-- 5. units  — the Vertical Property Unit, carrier of the 3D ULPIN
-- =============================================================================
CREATE TABLE units (
    unit_id             uuid                PRIMARY KEY DEFAULT gen_random_uuid(),
    floor_id            uuid                NOT NULL,
    building_id         uuid                NOT NULL,       -- denormalised, FK-verified below

    unit_number         varchar(32)         NOT NULL,       -- as marked on the door, e.g. '402'
    unit_code           char(5)             NOT NULL,       -- ULPIN unit segment, e.g. 'U0412'
    unit_ordinal        integer             NOT NULL,       -- Hilbert-sorted position within the floor

    unit_type           unit_type           NOT NULL DEFAULT 'APARTMENT',
    status              unit_status         NOT NULL DEFAULT 'DRAFT',
    occupancy           occupancy_status    NOT NULL DEFAULT 'VACANT',

    -- Areas. carpet <= built_up <= super_built_up is a legal requirement, not a hint.
    carpet_area_sqm     positive_area       NOT NULL,
    built_up_area_sqm   positive_area,
    super_area_sqm      positive_area,
    volume_cum          numeric(16,4),

    bedrooms            smallint,
    bathrooms           smallint,
    facing              varchar(16),                        -- N, NE, E, ...
    has_balcony         boolean             NOT NULL DEFAULT false,
    parking_slots       smallint            NOT NULL DEFAULT 0,

    -- Elevations, relative to the building datum
    base_elevation_m    numeric(8,3)        NOT NULL,
    top_elevation_m     numeric(8,3)        NOT NULL,

    -- Geometry -----------------------------------------------------------------
    floor_plate         geometry(PolygonZ, 4326)            NOT NULL,
    volume_solid        geometry(PolyhedralSurfaceZ, 4326),
    centroid_3d         geometry(PointZ, 4326),

    -- Geometry QA, written by the validation stage
    is_solid_valid      boolean             NOT NULL DEFAULT false,
    last_validated_at   timestamptz,

    created_by          uuid,
    created_at          timestamptz         NOT NULL DEFAULT now(),
    updated_at          timestamptz         NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT fk_units_floor    FOREIGN KEY (floor_id)    REFERENCES floors(floor_id)       ON DELETE CASCADE,
    CONSTRAINT fk_units_building FOREIGN KEY (building_id) REFERENCES buildings(building_id) ON DELETE CASCADE,

    -- Guarantees the denormalised building_id matches the floor's actual parent.
    CONSTRAINT fk_units_floor_building
        FOREIGN KEY (floor_id, building_id) REFERENCES floors(floor_id, building_id) ON DELETE CASCADE,

    CONSTRAINT fk_units_creator  FOREIGN KEY (created_by)  REFERENCES users(user_id)         ON DELETE SET NULL,

    CONSTRAINT uq_units_number   UNIQUE (floor_id, unit_number),
    CONSTRAINT uq_units_ordinal  UNIQUE (floor_id, unit_ordinal),
    CONSTRAINT uq_units_code     UNIQUE (floor_id, unit_code),

    CONSTRAINT ck_units_code     CHECK (unit_code ~ '^U[0-9A-Z]{4}$'),
    CONSTRAINT ck_units_ordinal  CHECK (unit_ordinal > 0),
    CONSTRAINT ck_units_areas    CHECK (
        (built_up_area_sqm IS NULL OR built_up_area_sqm >= carpet_area_sqm) AND
        (super_area_sqm    IS NULL OR built_up_area_sqm IS NULL OR super_area_sqm >= built_up_area_sqm)
    ),
    CONSTRAINT ck_units_elev     CHECK (top_elevation_m > base_elevation_m),
    CONSTRAINT ck_units_rooms    CHECK (
        (bedrooms  IS NULL OR bedrooms  BETWEEN 0 AND 50) AND
        (bathrooms IS NULL OR bathrooms BETWEEN 0 AND 50)
    ),
    CONSTRAINT ck_units_parking  CHECK (parking_slots >= 0),
    CONSTRAINT ck_units_srid     CHECK (ST_SRID(floor_plate) = 4326),
    CONSTRAINT ck_units_valid    CHECK (ST_IsValid(ST_Force2D(floor_plate)))
);

COMMENT ON TABLE  units IS 'Vertical Property Unit (VPU): an independently ownable volume. One 3D ULPIN per row.';
COMMENT ON COLUMN units.unit_ordinal IS 'Deterministic Hilbert-curve position within the floor. Regeneration reproduces it exactly, which is what makes identifiers stable.';

-- =============================================================================
-- 6. unit_ownerships  — joint ownership with exact shares
-- =============================================================================
-- Not in the original ten tables, but unavoidable: a unit can have several owners
-- (joint holding) and an owner can hold several units. Hanging unit_id off owners
-- would force duplicate owner rows, which breaks identity resolution — the very
-- thing OWNERSHIP_CONFLICT and IDENTITY_MISMATCH detection depends on.
CREATE TABLE unit_ownerships (
    ownership_id        uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id             uuid            NOT NULL,
    owner_id            uuid            NOT NULL,

    share               share_fraction  NOT NULL DEFAULT 1.0,
    ownership_mode      ownership_mode  NOT NULL DEFAULT 'FREEHOLD',
    is_primary          boolean         NOT NULL DEFAULT false,

    acquired_on         date            NOT NULL,
    relinquished_on     date,
    acquisition_doc_no  varchar(64),
    registration_no     varchar(64),

    is_active           boolean         NOT NULL DEFAULT true,

    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now(),

    CONSTRAINT fk_ownership_unit  FOREIGN KEY (unit_id)  REFERENCES units(unit_id)   ON DELETE CASCADE,
    CONSTRAINT fk_ownership_owner FOREIGN KEY (owner_id) REFERENCES owners(owner_id) ON DELETE RESTRICT,

    CONSTRAINT ck_ownership_dates  CHECK (relinquished_on IS NULL OR relinquished_on >= acquired_on),
    CONSTRAINT ck_ownership_active CHECK (
        (is_active AND relinquished_on IS NULL) OR (NOT is_active)
    )
);

-- One active holding per owner per unit; historical rows are unconstrained.
CREATE UNIQUE INDEX uq_ownership_active
    ON unit_ownerships (unit_id, owner_id)
    WHERE is_active;

COMMENT ON TABLE unit_ownerships IS 'Share ledger. Active shares per unit must sum to exactly 1 — enforced by trg_ownership_share_sum in 04_triggers.sql.';

-- =============================================================================
-- 7. tenants
-- =============================================================================
-- One row per tenancy agreement. A person renting two units produces two rows;
-- that matches how lease registries actually work.
CREATE TABLE tenants (
    tenant_id           uuid            PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id             uuid            NOT NULL,

    -- Optional login, and the landlord this tenancy was granted by.
    user_id             uuid,
    landlord_owner_id   uuid,

    full_name           varchar(200)    NOT NULL,
    email               email_address,
    phone               phone_number,
    national_id_hash    bytea,
    national_id_last4   char(4),

    status              tenancy_status  NOT NULL DEFAULT 'DRAFT',
    lease_start         date            NOT NULL,
    lease_end           date            NOT NULL,
    monthly_rent        numeric(14,2),
    security_deposit    numeric(14,2),
    currency_code       char(3)         NOT NULL DEFAULT 'INR',

    agreement_no        varchar(64),
    registered_on       date,
    occupant_count      smallint,

    is_verified         boolean         NOT NULL DEFAULT false,
    vacated_on          date,

    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now(),
    deleted_at          timestamptz,

    CONSTRAINT fk_tenants_unit     FOREIGN KEY (unit_id)           REFERENCES units(unit_id)   ON DELETE CASCADE,
    CONSTRAINT fk_tenants_user     FOREIGN KEY (user_id)           REFERENCES users(user_id)   ON DELETE SET NULL,
    CONSTRAINT fk_tenants_landlord FOREIGN KEY (landlord_owner_id) REFERENCES owners(owner_id) ON DELETE SET NULL,

    CONSTRAINT ck_tenants_lease    CHECK (lease_end > lease_start),
    CONSTRAINT ck_tenants_rent     CHECK (monthly_rent     IS NULL OR monthly_rent     >= 0),
    CONSTRAINT ck_tenants_deposit  CHECK (security_deposit IS NULL OR security_deposit >= 0),
    CONSTRAINT ck_tenants_vacated  CHECK (vacated_on IS NULL OR vacated_on >= lease_start),
    CONSTRAINT ck_tenants_occupants CHECK (occupant_count IS NULL OR occupant_count > 0)
);

-- Two ACTIVE tenancies must never overlap in time on the same unit. btree_gist
-- lets the uuid and the date range share one exclusion constraint.
ALTER TABLE tenants ADD CONSTRAINT ex_tenants_no_overlap
    EXCLUDE USING gist (
        unit_id WITH =,
        daterange(lease_start, lease_end, '[]') WITH &&
    ) WHERE (status = 'ACTIVE');

-- =============================================================================
-- 8. ulpins  — the identifier registry
-- =============================================================================
CREATE TABLE ulpins (
    ulpin_id            uuid            PRIMARY KEY DEFAULT gen_random_uuid(),

    ulpin_code          ulpin_code      NOT NULL,
    ulpin_type          ulpin_type      NOT NULL,
    status              ulpin_status    NOT NULL DEFAULT 'DRAFT',

    -- Decomposed segments, stored so they can be indexed and queried without
    -- string surgery. Kept consistent with ulpin_code by trg_ulpin_segments.
    parent_ulpin        varchar(14)     NOT NULL,
    block_code          char(2),
    storey_code         char(4),
    unit_code           char(5),
    check_char          char(1),

    -- Exactly one subject, matching ulpin_type (ck_ulpins_subject).
    building_id         uuid,
    floor_id            uuid,
    unit_id             uuid,

    -- Lineage for subdivision and amalgamation.
    parent_ulpin_id     uuid,
    supersedes_id       uuid,

    jurisdiction_code   varchar(16)     NOT NULL,
    centroid            geometry(PointZ, 4326),

    issued_at           timestamptz,
    issued_by           uuid,
    superseded_at       timestamptz,
    retired_at          timestamptz,
    retirement_reason   text,

    created_at          timestamptz     NOT NULL DEFAULT now(),
    updated_at          timestamptz     NOT NULL DEFAULT now(),

    CONSTRAINT uq_ulpins_code    UNIQUE (ulpin_code),

    CONSTRAINT fk_ulpins_building FOREIGN KEY (building_id)     REFERENCES buildings(building_id) ON DELETE CASCADE,
    CONSTRAINT fk_ulpins_floor    FOREIGN KEY (floor_id)        REFERENCES floors(floor_id)       ON DELETE CASCADE,
    CONSTRAINT fk_ulpins_unit     FOREIGN KEY (unit_id)         REFERENCES units(unit_id)         ON DELETE CASCADE,
    CONSTRAINT fk_ulpins_parent   FOREIGN KEY (parent_ulpin_id) REFERENCES ulpins(ulpin_id)       ON DELETE SET NULL,
    CONSTRAINT fk_ulpins_super    FOREIGN KEY (supersedes_id)   REFERENCES ulpins(ulpin_id)       ON DELETE SET NULL,
    CONSTRAINT fk_ulpins_issuer   FOREIGN KEY (issued_by)       REFERENCES users(user_id)         ON DELETE SET NULL,

    -- The subject columns populated must match what the type claims to identify.
    CONSTRAINT ck_ulpins_subject CHECK (
        (ulpin_type = 'PARCEL_2D' AND building_id IS NULL     AND floor_id IS NULL     AND unit_id IS NULL)
     OR (ulpin_type = 'BUILDING'  AND building_id IS NOT NULL AND floor_id IS NULL     AND unit_id IS NULL)
     OR (ulpin_type = 'FLOOR'     AND building_id IS NOT NULL AND floor_id IS NOT NULL AND unit_id IS NULL)
     OR (ulpin_type = 'UNIT_3D'   AND building_id IS NOT NULL AND floor_id IS NOT NULL AND unit_id IS NOT NULL)
    ),

    -- A 3D identifier must carry every segment; a 2D one must carry none.
    CONSTRAINT ck_ulpins_segments CHECK (
        (ulpin_type = 'PARCEL_2D'
            AND block_code IS NULL AND storey_code IS NULL AND unit_code IS NULL AND check_char IS NULL)
     OR (ulpin_type <> 'PARCEL_2D'
            AND block_code IS NOT NULL AND storey_code IS NOT NULL AND unit_code IS NOT NULL AND check_char IS NOT NULL)
    ),

    CONSTRAINT ck_ulpins_checksum CHECK (fn_ulpin_verify(ulpin_code)),
    CONSTRAINT ck_ulpins_parent   CHECK (parent_ulpin ~ '^[0-9A-Z]{14}$'),
    CONSTRAINT ck_ulpins_prefix   CHECK (left(ulpin_code, 14) = parent_ulpin),
    CONSTRAINT ck_ulpins_issued   CHECK (
        (status = 'ISSUED') = (issued_at IS NOT NULL)
    ),
    CONSTRAINT ck_ulpins_no_self  CHECK (supersedes_id IS DISTINCT FROM ulpin_id)
);

COMMENT ON TABLE ulpins IS 'Every identifier this system has minted. Rows are never deleted: corrections go through SUPERSEDED, demolition through RETIRED.';
COMMENT ON CONSTRAINT ck_ulpins_checksum ON ulpins IS 'Storage-level checksum gate. A malformed identifier cannot be written even if the application layer is wrong.';

-- One unit may hold only one live identifier at a time.
CREATE UNIQUE INDEX uq_ulpins_live_unit
    ON ulpins (unit_id)
    WHERE unit_id IS NOT NULL AND status IN ('PROVISIONAL', 'VERIFIED', 'ISSUED');

-- =============================================================================
-- 9. verification_records
-- =============================================================================
CREATE TABLE verification_records (
    verification_id     uuid                PRIMARY KEY DEFAULT gen_random_uuid(),

    ulpin_id            uuid                NOT NULL,
    unit_id             uuid,
    building_id         uuid,
    owner_id            uuid,

    verification_type   verification_type   NOT NULL,
    status              verification_status NOT NULL DEFAULT 'PENDING',

    verified_by         uuid,
    requested_by        uuid,
    requested_at        timestamptz         NOT NULL DEFAULT now(),
    verified_at         timestamptz,
    expires_at          timestamptz,

    method              varchar(64),                        -- 'DGPS', 'TOTAL_STATION', 'OCR', 'SITE_VISIT'
    reference_no        varchar(64),
    remarks             text,
    rejection_reason    text,

    -- Evidence integrity: the document lives in object storage, its digest here.
    evidence_uri        text,
    evidence_sha256     bytea,

    -- Where the verification physically happened. Comparing this against the
    -- unit centroid is the cheapest possible check on a fabricated site visit.
    captured_location   geometry(Point, 4326),
    captured_accuracy_m numeric(6,2),

    confidence          confidence_score,
    checks_passed       integer             NOT NULL DEFAULT 0,
    checks_failed       integer             NOT NULL DEFAULT 0,
    findings            jsonb               NOT NULL DEFAULT '{}'::jsonb,

    created_at          timestamptz         NOT NULL DEFAULT now(),
    updated_at          timestamptz         NOT NULL DEFAULT now(),

    CONSTRAINT fk_verif_ulpin     FOREIGN KEY (ulpin_id)     REFERENCES ulpins(ulpin_id)       ON DELETE CASCADE,
    CONSTRAINT fk_verif_unit      FOREIGN KEY (unit_id)      REFERENCES units(unit_id)         ON DELETE SET NULL,
    CONSTRAINT fk_verif_building  FOREIGN KEY (building_id)  REFERENCES buildings(building_id) ON DELETE SET NULL,
    CONSTRAINT fk_verif_owner     FOREIGN KEY (owner_id)     REFERENCES owners(owner_id)       ON DELETE SET NULL,
    CONSTRAINT fk_verif_verifier  FOREIGN KEY (verified_by)  REFERENCES users(user_id)         ON DELETE SET NULL,
    CONSTRAINT fk_verif_requester FOREIGN KEY (requested_by) REFERENCES users(user_id)         ON DELETE SET NULL,

    CONSTRAINT ck_verif_counts    CHECK (checks_passed >= 0 AND checks_failed >= 0),
    CONSTRAINT ck_verif_closed    CHECK (
        status NOT IN ('VERIFIED', 'REJECTED') OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)
    ),
    CONSTRAINT ck_verif_rejection CHECK (status <> 'REJECTED' OR rejection_reason IS NOT NULL),
    CONSTRAINT ck_verif_accuracy  CHECK (captured_accuracy_m IS NULL OR captured_accuracy_m >= 0),
    CONSTRAINT ck_verif_findings  CHECK (jsonb_typeof(findings) = 'object')
);

COMMENT ON COLUMN verification_records.evidence_sha256 IS 'Digest of the evidence file. Re-hash on retrieval to prove the artefact was not swapped after approval.';

-- =============================================================================
-- 10. fraud_alerts
-- =============================================================================
CREATE TABLE fraud_alerts (
    alert_id            uuid                PRIMARY KEY DEFAULT gen_random_uuid(),

    alert_type          fraud_alert_type    NOT NULL,
    severity            alert_severity      NOT NULL DEFAULT 'MEDIUM',
    status              alert_status        NOT NULL DEFAULT 'OPEN',
    detection_source    detection_source    NOT NULL DEFAULT 'RULE_ENGINE',

    -- Subjects. All nullable: an alert may point at any combination, and a
    -- DUPLICATE_ULPIN alert names two identifiers rather than one unit.
    ulpin_id            uuid,
    related_ulpin_id    uuid,
    unit_id             uuid,
    building_id         uuid,
    owner_id            uuid,
    tenant_id           uuid,

    rule_code           varchar(32),                        -- 'GEO-002', 'ULP-001', ...
    title               varchar(200)        NOT NULL,
    description         text,
    confidence          confidence_score,
    risk_score          numeric(6,2),

    -- Quantified impact, e.g. the cubic metres two units both claim.
    measured_value      numeric(16,4),
    threshold_value     numeric(16,4),
    evidence            jsonb               NOT NULL DEFAULT '{}'::jsonb,

    -- Alert location drives the fraud hotspot map.
    alert_location      geometry(Point, 4326),

    detected_at         timestamptz         NOT NULL DEFAULT now(),
    detected_by         uuid,
    assigned_to         uuid,
    assigned_at         timestamptz,
    resolved_by         uuid,
    resolved_at         timestamptz,
    resolution_notes    text,
    is_false_positive   boolean             NOT NULL DEFAULT false,

    created_at          timestamptz         NOT NULL DEFAULT now(),
    updated_at          timestamptz         NOT NULL DEFAULT now(),

    CONSTRAINT fk_fraud_ulpin      FOREIGN KEY (ulpin_id)         REFERENCES ulpins(ulpin_id)       ON DELETE CASCADE,
    CONSTRAINT fk_fraud_rel_ulpin  FOREIGN KEY (related_ulpin_id) REFERENCES ulpins(ulpin_id)       ON DELETE SET NULL,
    CONSTRAINT fk_fraud_unit       FOREIGN KEY (unit_id)          REFERENCES units(unit_id)         ON DELETE SET NULL,
    CONSTRAINT fk_fraud_building   FOREIGN KEY (building_id)      REFERENCES buildings(building_id) ON DELETE SET NULL,
    CONSTRAINT fk_fraud_owner      FOREIGN KEY (owner_id)         REFERENCES owners(owner_id)       ON DELETE SET NULL,
    CONSTRAINT fk_fraud_tenant     FOREIGN KEY (tenant_id)        REFERENCES tenants(tenant_id)     ON DELETE SET NULL,
    CONSTRAINT fk_fraud_detector   FOREIGN KEY (detected_by)      REFERENCES users(user_id)         ON DELETE SET NULL,
    CONSTRAINT fk_fraud_assignee   FOREIGN KEY (assigned_to)      REFERENCES users(user_id)         ON DELETE SET NULL,
    CONSTRAINT fk_fraud_resolver   FOREIGN KEY (resolved_by)      REFERENCES users(user_id)         ON DELETE SET NULL,

    CONSTRAINT ck_fraud_subject    CHECK (
        num_nonnulls(ulpin_id, unit_id, building_id, owner_id, tenant_id) >= 1
    ),
    CONSTRAINT ck_fraud_resolved   CHECK (
        status NOT IN ('RESOLVED', 'DISMISSED') OR (resolved_at IS NOT NULL AND resolution_notes IS NOT NULL)
    ),
    CONSTRAINT ck_fraud_assigned   CHECK ((assigned_to IS NULL) = (assigned_at IS NULL)),
    CONSTRAINT ck_fraud_evidence   CHECK (jsonb_typeof(evidence) = 'object'),
    -- "An alert must not point a ULPIN at itself." The NULL cases have to be
    -- excused explicitly: NULL IS DISTINCT FROM NULL evaluates to false, so the
    -- bare IS DISTINCT FROM form rejected every alert that names no ULPIN at
    -- all — which is most of them, since IDENTITY_MISMATCH and RAPID_TRANSFER
    -- are raised against an owner.
    CONSTRAINT ck_fraud_distinct   CHECK (
        related_ulpin_id IS NULL
        OR ulpin_id IS NULL
        OR related_ulpin_id <> ulpin_id
    )
);

COMMENT ON TABLE fraud_alerts IS 'Anomalies raised by the rule engine, ML scoring, or manual report. An OPEN CRITICAL alert on a unit blocks its ULPIN from reaching ISSUED.';

-- =============================================================================
-- 11. audit_logs  — append-only, range-partitioned by month
-- =============================================================================
CREATE TABLE audit_logs (
    log_id          uuid            NOT NULL DEFAULT gen_random_uuid(),

    actor_user_id   uuid,                                   -- NULL for system actions
    actor_role      user_role,
    actor_ip        inet,
    user_agent      text,
    request_id      varchar(64),                            -- correlates with API logs
    session_id      varchar(64),

    action          audit_action    NOT NULL,
    entity_type     varchar(64)     NOT NULL,               -- 'units', 'ulpins', ...
    entity_id       uuid,
    ulpin_code      varchar(32),                            -- denormalised for fast forensic search

    old_values      jsonb,
    new_values      jsonb,
    changed_fields  text[],

    -- Tamper-evidence: each row hashes its own payload together with the previous
    -- row's hash, so any retro-edit breaks the chain from that point forward.
    row_hash        bytea,
    prev_hash       bytea,

    success         boolean         NOT NULL DEFAULT true,
    error_message   text,

    created_at      timestamptz     NOT NULL DEFAULT now(),

    -- The partition key must participate in the primary key.
    CONSTRAINT pk_audit_logs PRIMARY KEY (log_id, created_at),
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT ck_audit_old   CHECK (old_values IS NULL OR jsonb_typeof(old_values) = 'object'),
    CONSTRAINT ck_audit_new   CHECK (new_values IS NULL OR jsonb_typeof(new_values) = 'object')
) PARTITION BY RANGE (created_at);

COMMENT ON TABLE audit_logs IS 'Append-only. UPDATE and DELETE are revoked from every application role; retention is enforced by detaching old partitions.';

-- Initial partitions. maintenance_tasks.create_audit_partitions() rolls these
-- forward; the DEFAULT partition catches anything that slips past.
CREATE TABLE audit_logs_2026_01 PARTITION OF audit_logs FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE audit_logs_2026_02 PARTITION OF audit_logs FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE audit_logs_2026_03 PARTITION OF audit_logs FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE audit_logs_2026_04 PARTITION OF audit_logs FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE audit_logs_2026_05 PARTITION OF audit_logs FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE audit_logs_2026_06 PARTITION OF audit_logs FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE audit_logs_2026_07 PARTITION OF audit_logs FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE audit_logs_2026_08 PARTITION OF audit_logs FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE audit_logs_2026_09 PARTITION OF audit_logs FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE audit_logs_2026_10 PARTITION OF audit_logs FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE audit_logs_2026_11 PARTITION OF audit_logs FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE audit_logs_2026_12 PARTITION OF audit_logs FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE audit_logs_default PARTITION OF audit_logs DEFAULT;
