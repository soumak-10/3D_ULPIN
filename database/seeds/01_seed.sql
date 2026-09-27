-- =============================================================================
--  01_seed.sql — sample data
--  Depends on: 00 → 04 having run.
--
--  Everything is built programmatically rather than as literal INSERTs:
--    * ULPIN check characters come from fn_ulpin_checksum(), so the seed can
--      never contain an identifier that fails ck_ulpins_checksum.
--    * Storey codes come from fn_storey_code(), satisfying ck_floors_code_num.
--    * Solids, centroids and volumes are filled in by the derive triggers.
--  Hand-written literals would drift from the functions the moment either changes.
--
--  Scenario: one commercial-residential tower in Bengaluru.
--    Parcel  IN29BLR0001234, block A1
--    2 basements (parking) + ground (2 shops) + 7 floors x 4 apartments = 32 units
--    Includes two deliberate data defects so the fraud rules have something to find.
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

BEGIN;

-- -----------------------------------------------------------------------------
-- 0. Constants
-- -----------------------------------------------------------------------------
-- !! The password hashes below are PLACEHOLDERS, not working argon2id digests. !!
-- Generate real ones before the seed is usable for login:
--     python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('YourDevPassword'))"
-- and substitute, or run scripts/bootstrap.sh which does it for you.

DO $seed$
DECLARE
    c_parcel      constant varchar(14) := 'IN29BLR0001234';
    c_block       constant char(2)     := 'A1';
    c_jurisdiction constant varchar(16):= 'KA-BLR-001';
    c_srid        constant int         := 4326;
    c_metric      constant int         := 7755;
    c_pw          constant text        := '$argon2id$v=19$m=65536,t=3,p=4$PLACEHOLDER$REPLACE_BEFORE_USE';

    -- Footprint: ~40 m x 30 m near MG Road, Bengaluru.
    c_x0 constant double precision := 77.594600;
    c_x1 constant double precision := 77.594969;
    c_y0 constant double precision := 12.971600;
    c_y1 constant double precision := 12.971871;

    c_ground_elev constant numeric := 920.000;   -- metres MSL, Bengaluru plateau
    c_floor_h     constant numeric := 3.200;
    c_basement_h  constant numeric := 3.000;

    v_admin_id      uuid;
    v_officer_id    uuid;
    v_owner_user_id uuid;
    v_tenant_user   uuid;
    v_auditor_id    uuid;

    v_building_id   uuid;
    v_footprint     geometry;
    v_floor_id      uuid;
    v_unit_id       uuid;
    v_ulpin_id      uuid;
    v_first_unit    uuid;
    v_second_unit   uuid;

    v_owner_ids     uuid[] := ARRAY[]::uuid[];
    v_owner_id      uuid;

    v_floor_no      int;
    v_ftype         floor_type;
    v_storey        char(4);
    v_elev          numeric;
    v_height        numeric;
    v_units_here    int;
    v_ordinal       int;
    v_unit_code     char(5);
    v_code          text;
    v_payload       text;
    v_utype         unit_type;
    v_plate         geometry;
    v_qx            int;
    v_qy            int;
    v_carpet        numeric;
    i               int;
BEGIN

-- -----------------------------------------------------------------------------
-- 1. users — one per role
-- -----------------------------------------------------------------------------
INSERT INTO users (email, username, password_hash, full_name, phone, role, status,
                   jurisdiction_code, email_verified_at)
VALUES
    ('admin@ulpin.gov.in',    'admin',    c_pw, 'System Administrator', '+919800000001',
     'ADMIN',            'ACTIVE', NULL,           now()),
    ('officer@ulpin.gov.in',  'rmenon',   c_pw, 'R. Menon',             '+919800000002',
     'PROPERTY_OFFICER', 'ACTIVE', c_jurisdiction, now()),
    ('owner@example.com',     'skulkarni',c_pw, 'S. Kulkarni',          '+919800000003',
     'OWNER',            'ACTIVE', c_jurisdiction, now()),
    ('tenant@example.com',    'aiyer',    c_pw, 'A. Iyer',              '+919800000004',
     'TENANT',           'ACTIVE', c_jurisdiction, now()),
    ('auditor@ulpin.gov.in',  'auditor',  c_pw, 'Internal Audit',       '+919800000005',
     'AUDITOR',          'ACTIVE', NULL,           now());

SELECT user_id INTO v_admin_id      FROM users WHERE email = 'admin@ulpin.gov.in';
SELECT user_id INTO v_officer_id    FROM users WHERE email = 'officer@ulpin.gov.in';
SELECT user_id INTO v_owner_user_id FROM users WHERE email = 'owner@example.com';
SELECT user_id INTO v_tenant_user   FROM users WHERE email = 'tenant@example.com';
SELECT user_id INTO v_auditor_id    FROM users WHERE email = 'auditor@ulpin.gov.in';

-- The audit trigger reads these to attribute every seeded row.
PERFORM set_config('app.current_user_id',   v_admin_id::text, true);
PERFORM set_config('app.current_user_role', 'ADMIN',          true);
PERFORM set_config('app.request_id',        'seed-0001',      true);

-- -----------------------------------------------------------------------------
-- 2. owners
-- -----------------------------------------------------------------------------
INSERT INTO owners (user_id, owner_type, full_name, father_or_spouse, date_of_birth,
                    national_id_hash, national_id_type, national_id_last4,
                    email, phone, address_line1, city, district, state, postal_code,
                    address_location, is_verified, verified_at, risk_score)
VALUES
    (v_owner_user_id, 'INDIVIDUAL', 'S. Kulkarni', 'V. Kulkarni', '1978-04-12',
     digest('AADHAAR:999912345678:pepper', 'sha256'), 'AADHAAR', '5678',
     'owner@example.com', '+919800000003', '14 Residency Road', 'Bengaluru',
     'Bengaluru Urban', 'Karnataka', '560025',
     ST_SetSRID(ST_MakePoint(77.6033, 12.9698), c_srid), true, now(), 0.08),

    (NULL, 'INDIVIDUAL', 'M. Kulkarni', 'S. Kulkarni', '1981-09-30',
     digest('AADHAAR:999987654321:pepper', 'sha256'), 'AADHAAR', '4321',
     'm.kulkarni@example.com', '+919800000006', '14 Residency Road', 'Bengaluru',
     'Bengaluru Urban', 'Karnataka', '560025',
     ST_SetSRID(ST_MakePoint(77.6033, 12.9698), c_srid), true, now(), 0.05),

    (NULL, 'COMPANY', 'Nandi Retail Ventures Pvt Ltd', NULL, NULL,
     digest('CIN:U52100KA2011PTC057342:pepper', 'sha256'), 'CIN', '7342',
     'legal@nandiretail.example', '+918000000007', 'Prestige Meridian, MG Road',
     'Bengaluru', 'Bengaluru Urban', 'Karnataka', '560001',
     ST_SetSRID(ST_MakePoint(77.6045, 12.9750), c_srid), true, now(), 0.15),

    (NULL, 'HUF', 'Raghavan (HUF)', NULL, NULL,
     digest('PAN:AAHHR1234K:pepper', 'sha256'), 'PAN', '234K',
     'raghavan.huf@example.com', '+919800000008', '22 Church Street', 'Bengaluru',
     'Bengaluru Urban', 'Karnataka', '560001',
     ST_SetSRID(ST_MakePoint(77.6010, 12.9756), c_srid), false, NULL, 0.42),

    -- Deliberate defect: same national_id_hash as the first owner under a
    -- different name. idx_owners_national_id surfaces this as IDENTITY_MISMATCH.
    (NULL, 'INDIVIDUAL', 'Suresh K.', 'V. Kulkarni', '1978-04-12',
     digest('AADHAAR:999912345678:pepper', 'sha256'), 'AADHAAR', '5678',
     'suresh.k@example.com', '+919800000009', '14 Residency Road', 'Bengaluru',
     'Bengaluru Urban', 'Karnataka', '560025',
     ST_SetSRID(ST_MakePoint(77.6033, 12.9698), c_srid), false, NULL, 0.88);

SELECT array_agg(owner_id ORDER BY created_at) INTO v_owner_ids FROM owners;

-- -----------------------------------------------------------------------------
-- 3. buildings
-- -----------------------------------------------------------------------------
v_footprint := ST_Force3D(
    ST_SetSRID(
        ST_MakePolygon(ST_MakeLine(ARRAY[
            ST_MakePoint(c_x0, c_y0),
            ST_MakePoint(c_x1, c_y0),
            ST_MakePoint(c_x1, c_y1),
            ST_MakePoint(c_x0, c_y1),
            ST_MakePoint(c_x0, c_y0)
        ])),
        c_srid
    ),
    c_ground_elev
);

INSERT INTO buildings (
    parcel_ulpin, jurisdiction_code, building_name, building_code,
    address_line1, address_line2, city, postal_code,
    building_use, construction_status, status,
    total_floors, basement_floors,
    vertical_datum, ground_elevation_m, height_m, metric_srid,
    built_up_area_sqm, year_built, sanction_number, sanction_date, occupancy_cert_no,
    footprint, created_by, verified_by, verified_at
) VALUES (
    c_parcel, c_jurisdiction, 'Nandi Heights', c_block,
    '27 Residency Road', 'Ashok Nagar', 'Bengaluru', '560025',
    'MIXED_USE', 'OCCUPIED', 'VERIFIED',
    7, 2,
    'EGM2008', c_ground_elev, 7 * c_floor_h + 0.5, c_metric,
    8400.0000, 2019, 'BBMP/SANC/2017/04412', '2017-06-14', 'BBMP/OC/2019/00921',
    v_footprint, v_officer_id, v_officer_id, now() - INTERVAL '90 days'
) RETURNING building_id INTO v_building_id;

-- -----------------------------------------------------------------------------
-- 4. floors, units and ULPINs
-- -----------------------------------------------------------------------------
FOR v_floor_no IN -2 .. 7 LOOP

    IF v_floor_no < 0 THEN
        v_ftype      := 'BASEMENT';
        v_height     := c_basement_h;
        v_elev       := c_ground_elev + (v_floor_no * c_basement_h);
        v_units_here := 1;                       -- one undivided parking deck
        v_utype      := 'PARKING';
    ELSIF v_floor_no = 0 THEN
        v_ftype      := 'GROUND';
        v_height     := c_floor_h;
        v_elev       := c_ground_elev;
        v_units_here := 2;                       -- two retail units
        v_utype      := 'SHOP';
    ELSE
        v_ftype      := 'UPPER';
        v_height     := c_floor_h;
        v_elev       := c_ground_elev + (v_floor_no * c_floor_h);
        v_units_here := 4;                       -- four apartments
        v_utype      := 'APARTMENT';
    END IF;

    v_storey := fn_storey_code(v_floor_no, v_ftype);

    INSERT INTO floors (
        building_id, floor_number, floor_type, label, storey_code,
        elevation_m, floor_height_m, ceiling_height_m,
        plate_area_sqm, common_area_sqm, is_habitable, slab_geom
    ) VALUES (
        v_building_id, v_floor_no, v_ftype,
        CASE
            WHEN v_floor_no < 0 THEN 'B' || abs(v_floor_no)::text
            WHEN v_floor_no = 0 THEN 'G'
            ELSE v_floor_no::text
        END,
        v_storey,
        v_elev, v_height, v_height - 0.35,
        1200.0000,
        CASE WHEN v_floor_no > 0 THEN 180.0000 ELSE 60.0000 END,
        v_floor_no >= 0,
        ST_Force3D(ST_Force2D(v_footprint), v_elev)
    ) RETURNING floor_id INTO v_floor_id;

    -- Units within this floor ---------------------------------------------
    FOR v_ordinal IN 1 .. v_units_here LOOP

        IF v_units_here = 1 THEN
            v_plate  := ST_Force2D(v_footprint);
            v_carpet := 1100.0000;
        ELSIF v_units_here = 2 THEN
            -- split east/west
            v_plate := ST_SetSRID(ST_MakePolygon(ST_MakeLine(ARRAY[
                ST_MakePoint(c_x0 + (v_ordinal - 1) * (c_x1 - c_x0) / 2, c_y0),
                ST_MakePoint(c_x0 + v_ordinal       * (c_x1 - c_x0) / 2, c_y0),
                ST_MakePoint(c_x0 + v_ordinal       * (c_x1 - c_x0) / 2, c_y1),
                ST_MakePoint(c_x0 + (v_ordinal - 1) * (c_x1 - c_x0) / 2, c_y1),
                ST_MakePoint(c_x0 + (v_ordinal - 1) * (c_x1 - c_x0) / 2, c_y0)
            ])), c_srid);
            v_carpet := 520.0000;
        ELSE
            -- quadrants, ordered the way the Hilbert sort would place them
            v_qx := CASE WHEN v_ordinal IN (1, 4) THEN 0 ELSE 1 END;
            v_qy := CASE WHEN v_ordinal IN (1, 2) THEN 0 ELSE 1 END;
            v_plate := ST_SetSRID(ST_MakePolygon(ST_MakeLine(ARRAY[
                ST_MakePoint(c_x0 + v_qx       * (c_x1 - c_x0) / 2, c_y0 + v_qy       * (c_y1 - c_y0) / 2),
                ST_MakePoint(c_x0 + (v_qx + 1) * (c_x1 - c_x0) / 2, c_y0 + v_qy       * (c_y1 - c_y0) / 2),
                ST_MakePoint(c_x0 + (v_qx + 1) * (c_x1 - c_x0) / 2, c_y0 + (v_qy + 1) * (c_y1 - c_y0) / 2),
                ST_MakePoint(c_x0 + v_qx       * (c_x1 - c_x0) / 2, c_y0 + (v_qy + 1) * (c_y1 - c_y0) / 2),
                ST_MakePoint(c_x0 + v_qx       * (c_x1 - c_x0) / 2, c_y0 + v_qy       * (c_y1 - c_y0) / 2)
            ])), c_srid);
            v_carpet := 232.0000;
        END IF;

        v_unit_code := 'U' || lpad((v_floor_no * 100 + v_ordinal)::text, 4, '0');
        -- floor -2 would render as U-201; use an offset so basements stay in-domain
        IF v_floor_no < 0 THEN
            v_unit_code := 'U9' || lpad((abs(v_floor_no) * 10 + v_ordinal)::text, 3, '0');
        END IF;

        INSERT INTO units (
            floor_id, building_id, unit_number, unit_code, unit_ordinal,
            unit_type, status, occupancy,
            carpet_area_sqm, built_up_area_sqm, super_area_sqm,
            bedrooms, bathrooms, facing, has_balcony, parking_slots,
            base_elevation_m, top_elevation_m, floor_plate, created_by
        ) VALUES (
            v_floor_id, v_building_id,
            CASE
                WHEN v_floor_no < 0 THEN 'P' || abs(v_floor_no)::text || '-' || v_ordinal::text
                WHEN v_floor_no = 0 THEN 'S' || v_ordinal::text
                ELSE (v_floor_no * 100 + v_ordinal)::text
            END,
            v_unit_code, v_ordinal,
            v_utype,
            'ISSUED',
            -- Cast required: a bare literal coerces to the target enum, but a
            -- CASE over several of them resolves to text first, and there is no
            -- assignment cast from text to an enum.
            CASE WHEN v_floor_no > 0 AND v_ordinal = 1 THEN 'TENANT_OCCUPIED'
                 WHEN v_floor_no > 0 THEN 'OWNER_OCCUPIED'
                 ELSE 'VACANT' END::occupancy_status,
            v_carpet,
            v_carpet * 1.15,
            v_carpet * 1.35,
            CASE WHEN v_utype = 'APARTMENT' THEN 3 ELSE NULL END,
            CASE WHEN v_utype = 'APARTMENT' THEN 3 ELSE NULL END,
            CASE v_ordinal WHEN 1 THEN 'SW' WHEN 2 THEN 'SE' WHEN 3 THEN 'NE' ELSE 'NW' END,
            v_utype = 'APARTMENT',
            CASE WHEN v_utype = 'APARTMENT' THEN 1 ELSE 0 END,
            v_elev,
            v_elev + v_height - 0.35,
            -- floor_plate is declared PolygonZ: the plate is the unit outline
            -- laid at its own base elevation, not a flat 2D shape.
            ST_Force3D(v_plate, v_elev),
            v_officer_id
        ) RETURNING unit_id INTO v_unit_id;

        -- Mint the identifier. The check character is computed, never typed.
        v_payload := c_parcel || c_block || v_storey || v_unit_code;
        v_code    := c_parcel || '-' || c_block || '-' || v_storey || '-' ||
                     v_unit_code || '-' || fn_ulpin_checksum(v_payload);

        INSERT INTO ulpins (
            ulpin_code, ulpin_type, status, parent_ulpin,
            building_id, floor_id, unit_id, jurisdiction_code,
            centroid, issued_at, issued_by
        ) VALUES (
            v_code, 'UNIT_3D', 'ISSUED', c_parcel,
            v_building_id, v_floor_id, v_unit_id, c_jurisdiction,
            ST_Force3D(ST_PointOnSurface(v_plate), v_elev + v_height / 2),
            now() - INTERVAL '80 days', v_officer_id
        ) RETURNING ulpin_id INTO v_ulpin_id;

        -- Remember two upper-floor units for the ownership and fraud samples.
        IF v_floor_no = 4 AND v_ordinal = 1 THEN v_first_unit  := v_unit_id; END IF;
        IF v_floor_no = 4 AND v_ordinal = 2 THEN v_second_unit := v_unit_id; END IF;

    END LOOP;
END LOOP;

-- -----------------------------------------------------------------------------
-- 5. unit_ownerships
-- -----------------------------------------------------------------------------
-- Joint holding, 60/40. The deferred share-sum trigger checks this at COMMIT.
INSERT INTO unit_ownerships (unit_id, owner_id, share, ownership_mode, is_primary,
                             acquired_on, acquisition_doc_no, registration_no)
VALUES
    (v_first_unit,  v_owner_ids[1], 0.600000000, 'FREEHOLD', true,
     '2019-11-08', 'SD/2019/114522', 'BLR-SRO-2019-114522'),
    (v_first_unit,  v_owner_ids[2], 0.400000000, 'FREEHOLD', false,
     '2019-11-08', 'SD/2019/114522', 'BLR-SRO-2019-114522'),
    (v_second_unit, v_owner_ids[4], 1.000000000, 'INHERITED', true,
     '2021-03-22', 'SUCC/2021/00874', 'BLR-SRO-2021-008741');

-- Sole ownership for every remaining issued apartment.
INSERT INTO unit_ownerships (unit_id, owner_id, share, ownership_mode, is_primary, acquired_on)
SELECT u.unit_id, v_owner_ids[1 + (row_number() OVER (ORDER BY u.unit_id))::int % 4],
       1.000000000, 'FREEHOLD', true, DATE '2019-12-01'
FROM units u
WHERE u.unit_type = 'APARTMENT'
  AND u.unit_id NOT IN (v_first_unit, v_second_unit);

-- Retail units held by the company.
INSERT INTO unit_ownerships (unit_id, owner_id, share, ownership_mode, is_primary, acquired_on)
SELECT u.unit_id, v_owner_ids[3], 1.000000000, 'LEASEHOLD', true, DATE '2020-02-17'
FROM units u WHERE u.unit_type = 'SHOP';

-- -----------------------------------------------------------------------------
-- 6. tenants
-- -----------------------------------------------------------------------------
INSERT INTO tenants (unit_id, user_id, landlord_owner_id, full_name, email, phone,
                     national_id_hash, national_id_last4, status,
                     lease_start, lease_end, monthly_rent, security_deposit,
                     agreement_no, registered_on, occupant_count, is_verified)
VALUES
    (v_first_unit, v_tenant_user, v_owner_ids[1], 'A. Iyer',
     'tenant@example.com', '+919800000004',
     digest('AADHAAR:999900001111:pepper', 'sha256'), '1111', 'ACTIVE',
     CURRENT_DATE - INTERVAL '8 months', CURRENT_DATE + INTERVAL '4 months',
     62000.00, 372000.00, 'LEASE/2026/0413', CURRENT_DATE - INTERVAL '8 months', 3, true);

-- An expired tenancy on the same unit. The exclusion constraint permits this
-- because it is not ACTIVE, which is exactly the intent.
INSERT INTO tenants (unit_id, landlord_owner_id, full_name, phone, status,
                     lease_start, lease_end, monthly_rent, security_deposit,
                     agreement_no, occupant_count, vacated_on)
VALUES
    (v_first_unit, v_owner_ids[1], 'P. Desai', '+919800000010', 'EXPIRED',
     CURRENT_DATE - INTERVAL '32 months', CURRENT_DATE - INTERVAL '9 months',
     55000.00, 330000.00, 'LEASE/2023/0288', 2, CURRENT_DATE - INTERVAL '9 months');

-- Tenancy on a retail unit.
INSERT INTO tenants (unit_id, landlord_owner_id, full_name, email, phone, status,
                     lease_start, lease_end, monthly_rent, security_deposit,
                     agreement_no, occupant_count, is_verified)
SELECT u.unit_id, v_owner_ids[3], 'Chai Point Franchise', 'ops@chaipoint.example',
       '+918000000011', 'ACTIVE',
       CURRENT_DATE - INTERVAL '14 months', CURRENT_DATE + INTERVAL '22 months',
       185000.00, 1110000.00, 'LEASE/2025/0099', 8, true
FROM units u WHERE u.unit_type = 'SHOP' ORDER BY u.unit_number LIMIT 1;

-- -----------------------------------------------------------------------------
-- 7. verification_records
-- -----------------------------------------------------------------------------
INSERT INTO verification_records (
    ulpin_id, unit_id, building_id, verification_type, status,
    verified_by, requested_by, requested_at, verified_at, expires_at,
    method, reference_no, remarks,
    evidence_uri, evidence_sha256,
    captured_location, captured_accuracy_m,
    confidence, checks_passed, checks_failed, findings
)
SELECT
    up.ulpin_id, up.unit_id, up.building_id,
    'GEOMETRIC', 'VERIFIED',
    v_officer_id, v_officer_id,
    now() - INTERVAL '85 days', now() - INTERVAL '84 days', NULL,
    'AUTOMATED_SOLID_CHECK', 'GEO/2026/' || lpad((row_number() OVER (ORDER BY up.ulpin_code))::text, 5, '0'),
    'Closed manifold solid; no overlap with sibling units.',
    's3://ulpin-evidence/geo/' || up.ulpin_code || '.json',
    digest(up.ulpin_code || ':geometric', 'sha256'),
    NULL, NULL,
    0.9900, 9, 0,
    jsonb_build_object('rules', jsonb_build_array('GEO-001','GEO-002','GEO-003','GEO-004'),
                       'tolerance_m', 0.01)
FROM ulpins up
WHERE up.ulpin_type = 'UNIT_3D';

-- A field inspection with a GNSS fix, so the location check has something to compare.
INSERT INTO verification_records (
    ulpin_id, unit_id, building_id, owner_id, verification_type, status,
    verified_by, requested_by, requested_at, verified_at,
    method, reference_no, remarks,
    captured_location, captured_accuracy_m, confidence,
    checks_passed, checks_failed, findings
)
SELECT up.ulpin_id, up.unit_id, up.building_id, v_owner_ids[1],
       'FIELD', 'VERIFIED',
       v_officer_id, v_officer_id,
       now() - INTERVAL '40 days', now() - INTERVAL '38 days',
       'DGPS', 'FLD/2026/00231',
       'Site visit confirmed unit boundaries against the sanctioned plan.',
       ST_SetSRID(ST_MakePoint(77.594780, 12.971730), c_srid), 0.85, 1.0000,
       6, 0, jsonb_build_object('inspector', 'R. Menon', 'weather', 'clear')
FROM ulpins up WHERE up.unit_id = v_first_unit;

-- A rejected document verification.
INSERT INTO verification_records (
    ulpin_id, unit_id, building_id, owner_id, verification_type, status,
    verified_by, requested_by, requested_at, verified_at,
    method, reference_no, rejection_reason, confidence,
    checks_passed, checks_failed, findings
)
SELECT up.ulpin_id, up.unit_id, up.building_id, v_owner_ids[4],
       'DOCUMENT', 'REJECTED',
       v_officer_id, v_officer_id,
       now() - INTERVAL '20 days', now() - INTERVAL '18 days',
       'OCR', 'DOC/2026/01877',
       'Succession certificate lacks the registrar seal; resubmission required.',
       0.3100, 2, 3,
       jsonb_build_object('missing', jsonb_build_array('registrar_seal', 'witness_signature'))
FROM ulpins up WHERE up.unit_id = v_second_unit;

-- -----------------------------------------------------------------------------
-- 8. fraud_alerts
-- -----------------------------------------------------------------------------
INSERT INTO fraud_alerts (
    alert_type, severity, status, detection_source,
    owner_id, rule_code, title, description,
    confidence, risk_score, evidence, alert_location,
    detected_at, assigned_to, assigned_at
) VALUES (
    'IDENTITY_MISMATCH', 'HIGH', 'INVESTIGATING', 'RULE_ENGINE',
    v_owner_ids[5], 'ID-002',
    'Duplicate statutory ID across two owner records',
    'Owners "S. Kulkarni" and "Suresh K." share an identical national ID hash but differ in name and contact details.',
    0.9400, 78.50,
    jsonb_build_object(
        'matched_on', 'national_id_hash',
        'owner_ids', jsonb_build_array(v_owner_ids[1]::text, v_owner_ids[5]::text),
        'name_similarity', 0.41
    ),
    ST_SetSRID(ST_MakePoint(77.6033, 12.9698), c_srid),
    now() - INTERVAL '6 days', v_admin_id, now() - INTERVAL '5 days'
);

INSERT INTO fraud_alerts (
    alert_type, severity, status, detection_source,
    unit_id, building_id, rule_code, title, description,
    confidence, risk_score, measured_value, threshold_value,
    evidence, alert_location, detected_at
)
SELECT
    'AREA_MISMATCH', 'MEDIUM', 'OPEN', 'RULE_ENGINE',
    v_second_unit, v_building_id, 'GEO-005',
    'Declared carpet area exceeds the surveyed floor plate',
    'Sum of declared unit areas on storey F004 exceeds the sanctioned plate area beyond the 0.5 percent tolerance.',
    0.7600, 44.00, 1248.0000, 1206.0000,
    jsonb_build_object('storey', 'F004', 'declared_sqm', 1248.0, 'plate_sqm', 1200.0, 'tolerance_pct', 0.5),
    ST_SetSRID(ST_MakePoint(77.594780, 12.971730), c_srid),
    now() - INTERVAL '3 days';

INSERT INTO fraud_alerts (
    alert_type, severity, status, detection_source,
    owner_id, rule_code, title, description,
    confidence, risk_score, evidence, detected_at,
    resolved_by, resolved_at, resolution_notes, is_false_positive
) VALUES (
    'RAPID_TRANSFER', 'LOW', 'DISMISSED', 'ML_MODEL',
    v_owner_ids[3], 'TXN-007',
    'Three ownership changes within twelve months',
    'Model flagged transfer velocity above the jurisdiction baseline for commercial units.',
    0.5200, 22.00,
    jsonb_build_object('transfers', 3, 'window_months', 12, 'baseline', 1.2),
    now() - INTERVAL '45 days',
    v_admin_id, now() - INTERVAL '44 days',
    'Transfers were internal group restructuring, supported by board resolutions.', true
);

RAISE NOTICE 'Seed complete: building % with % units', v_building_id,
    (SELECT count(*) FROM units WHERE building_id = v_building_id);

END
$seed$;

COMMIT;

-- -----------------------------------------------------------------------------
-- 9. Verification queries
-- -----------------------------------------------------------------------------
-- Run these after seeding; each should return sensible, non-empty output.

-- Every identifier passes its own checksum.
SELECT count(*) AS total_ulpins,
       count(*) FILTER (WHERE fn_ulpin_verify(ulpin_code)) AS valid_checksums
FROM ulpins;

-- Solid geometry actually closed, and volumes plausible.
SELECT u.unit_number,
       up.ulpin_code,
       round(u.carpet_area_sqm, 1)                      AS carpet_sqm,
       round(u.volume_cum, 1)                           AS volume_cum,
       ST_IsSolid(ST_MakeSolid(u.volume_solid))         AS is_closed_solid
FROM units u
JOIN ulpins up ON up.unit_id = u.unit_id
ORDER BY u.base_elevation_m, u.unit_ordinal
LIMIT 10;

-- No two units in the tower share 3D space. Uses idx_units_volume_nd.
SELECT a.unit_number AS unit_a, b.unit_number AS unit_b
FROM units a
JOIN units b
  ON a.building_id = b.building_id
 AND a.unit_id < b.unit_id
 AND a.volume_solid &&& b.volume_solid
WHERE ST_3DIntersects(a.volume_solid, b.volume_solid);

-- Active shares reconcile to exactly 1 on every owned unit.
SELECT u.unit_number, sum(o.share) AS total_share
FROM units u
JOIN unit_ownerships o ON o.unit_id = u.unit_id AND o.is_active
GROUP BY u.unit_number
HAVING sum(o.share) <> 1.0;

-- The audit hash chain is intact (no rows returned means unbroken).
SELECT * FROM fn_audit_verify_chain();
