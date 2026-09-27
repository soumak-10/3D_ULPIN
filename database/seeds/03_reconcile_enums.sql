-- =============================================================================
--  03_reconcile_enums.sql — move seeded rows onto the API's enum vocabulary
--
--  Third and last reconciliation pass. 02_reconcile.sql copied values between
--  columns whose *names* diverged; this one fixes values whose *labels*
--  diverged. database/schemas/ chose a fine-grained, survey-office vocabulary
--  (APARTMENT, SHOP, OWNER_OCCUPIED, GEOMETRIC) while apps/api/app/models/
--  enums.py chose the coarser vocabulary the statute and the UI use
--  (RESIDENTIAL, COMMERCIAL, OCCUPIED, GEOMETRY). Both sets of labels exist in
--  every PostgreSQL enum here, so nothing needs ALTER TYPE — only the rows move.
--
--  The API is the authority on direction: app/models/enums.py and the
--  TypeScript unions in apps/web/src/types/api.ts already agree with each
--  other, so the data is what has to move. SQLAlchemy resolves an enum column
--  by label, and a label it has never heard of raises
--  `LookupError: 'APARTMENT' is not among the defined enum values` on the way
--  out of the database — so a single stale row takes down the whole endpoint,
--  not just its own field.
--
--  Where the schema's vocabulary is genuinely richer than the API's, the detail
--  is preserved rather than discarded: units.status and units.occupancy keep
--  their original labels and only their ORM-mapped twins (unit_status,
--  occupancy_status) are coarsened. The owner/tenant distinction that
--  OCCUPIED flattens is also still recoverable from unit_ownerships and
--  tenancies, which is where it belongs.
--
--  Idempotent: every CASE falls through to the column's current value, so a
--  label already in the API's vocabulary is left alone and re-running changes
--  nothing. Must run *after* 02_reconcile.sql — that file assigns
--  unit_status := status, which would undo the mapping below. Run as a
--  superuser, from the repository root:
--
--    psql -U postgres -d ulpin_db -f database/seeds/03_reconcile_enums.sql
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- units.unit_type: the schema names the built form, the API names the use
-- class. Every commercial built form collapses onto COMMERCIAL; the shaft and
-- the terrace are not lettable space, so they become UTILITY and COMMON_AREA.
UPDATE units
   SET unit_type = CASE unit_type::text
           WHEN 'APARTMENT'     THEN 'RESIDENTIAL'
           WHEN 'OFFICE'        THEN 'COMMERCIAL'
           WHEN 'SHOP'          THEN 'COMMERCIAL'
           WHEN 'SHOWROOM'      THEN 'COMMERCIAL'
           WHEN 'WAREHOUSE'     THEN 'INDUSTRIAL'
           WHEN 'SERVICE_SHAFT' THEN 'UTILITY'
           WHEN 'TERRACE_UNIT'  THEN 'COMMON_AREA'
           WHEN 'AIR_RIGHT'     THEN 'MIXED'
           WHEN 'OTHER'         THEN 'MIXED'
           ELSE unit_type::text
       END::unit_type
 WHERE unit_type::text IN ('APARTMENT','OFFICE','SHOP','SHOWROOM','WAREHOUSE',
                           'SERVICE_SHAFT','TERRACE_UNIT','AIR_RIGHT','OTHER');

-- units.unit_status: the schema tracks the ULPIN's issuance lifecycle, the API
-- tracks the unit's own lifecycle. A unit whose identifier has been ISSUED or
-- VERIFIED is simply ACTIVE as a unit; the issuance state itself is not lost,
-- it lives on ulpins.status, which is where the API reads it from.
UPDATE units
   SET unit_status = CASE unit_status::text
           WHEN 'ISSUED'      THEN 'ACTIVE'
           WHEN 'VERIFIED'    THEN 'ACTIVE'
           WHEN 'PROVISIONAL' THEN 'DRAFT'
           WHEN 'DISPUTED'    THEN 'UNDER_DISPUTE'
           WHEN 'SUPERSEDED'  THEN 'MERGED'
           WHEN 'RETIRED'     THEN 'DEMOLISHED'
           ELSE unit_status::text
       END::unit_status
 WHERE unit_status::text IN ('ISSUED','VERIFIED','PROVISIONAL','DISPUTED',
                             'SUPERSEDED','RETIRED');

-- units.occupancy_status: who occupies is a different question from whether it
-- is occupied, and the API only asks the second. units.occupancy above keeps
-- the answer to the first.
UPDATE units
   SET occupancy_status = CASE occupancy_status::text
           WHEN 'OWNER_OCCUPIED'  THEN 'OCCUPIED'
           WHEN 'TENANT_OCCUPIED' THEN 'OCCUPIED'
           WHEN 'LOCKED'          THEN 'SEALED'
           WHEN 'UNDER_FITOUT'    THEN 'UNDER_RENOVATION'
           ELSE occupancy_status::text
       END::occupancy_status
 WHERE occupancy_status::text IN ('OWNER_OCCUPIED','TENANT_OCCUPIED',
                                  'LOCKED','UNDER_FITOUT');

-- verification_records.status: the schema conflated the check's own state with
-- its result (VERIFIED/REJECTED); the API keeps state and outcome apart, so
-- the state becomes PASSED/FAILED and the finding stays on `outcome`.
UPDATE verification_records
   SET status = CASE status::text
           WHEN 'VERIFIED' THEN 'PASSED'
           WHEN 'REJECTED' THEN 'FAILED'
           WHEN 'EXPIRED'  THEN 'INCONCLUSIVE'
           ELSE status::text
       END::verification_status
 WHERE status::text IN ('VERIFIED','REJECTED','EXPIRED');

-- verification_records.verification_type: same checks, different names.
-- BIOMETRIC is an identity check by another name; a third-party feed is an
-- automated one.
UPDATE verification_records
   SET verification_type = CASE verification_type::text
           WHEN 'FIELD'       THEN 'FIELD_SURVEY'
           WHEN 'SURVEY'      THEN 'FIELD_SURVEY'
           WHEN 'GEOMETRIC'   THEN 'GEOMETRY'
           WHEN 'BIOMETRIC'   THEN 'IDENTITY'
           WHEN 'THIRD_PARTY' THEN 'AUTOMATED'
           ELSE verification_type::text
       END::verification_type
 WHERE verification_type::text IN ('FIELD','SURVEY','GEOMETRIC','BIOMETRIC','THIRD_PARTY');

-- fraud_alerts.alert_type: the schema enumerates seventeen detector names; the
-- API exposes the five statutory rules the brief asks for, so each detector
-- reports under the rule it is evidence of. The three seeded alerts land on
-- three different rules, which is also what makes the dashboard's distribution
-- chart worth looking at:
--   "declared carpet area exceeds the surveyed floor plate" -> claiming volume
--      the record does not grant     -> UNAUTHORIZED_OCCUPANCY
--   "duplicate statutory ID across two owner records"       -> OWNERSHIP_MISMATCH
--   "three ownership changes within twelve months"          -> MULTIPLE_OWNERS
UPDATE fraud_alerts
   SET alert_type = CASE alert_type::text
           WHEN 'AREA_MISMATCH'              THEN 'UNAUTHORIZED_OCCUPANCY'
           WHEN 'OVERLAPPING_VOLUME'         THEN 'UNAUTHORIZED_OCCUPANCY'
           WHEN 'ENCROACHMENT'               THEN 'UNAUTHORIZED_OCCUPANCY'
           WHEN 'UNAUTHORISED_MODIFICATION'  THEN 'UNAUTHORIZED_OCCUPANCY'
           WHEN 'GEOMETRY_TAMPERING'         THEN 'UNAUTHORIZED_OCCUPANCY'
           WHEN 'IDENTITY_MISMATCH'          THEN 'OWNERSHIP_MISMATCH'
           WHEN 'OWNERSHIP_CONFLICT'         THEN 'OWNERSHIP_MISMATCH'
           WHEN 'FORGED_DOCUMENT'            THEN 'OWNERSHIP_MISMATCH'
           WHEN 'OTHER'                      THEN 'OWNERSHIP_MISMATCH'
           WHEN 'RAPID_TRANSFER'             THEN 'MULTIPLE_OWNERS'
           WHEN 'SHARE_OVERFLOW'             THEN 'MULTIPLE_OWNERS'
           WHEN 'GHOST_UNIT'                 THEN 'DUPLICATE_ULPIN'
           ELSE alert_type::text
       END::fraud_alert_type
 WHERE alert_type::text NOT IN ('MULTIPLE_OWNERS','DUPLICATE_ULPIN','OWNERSHIP_MISMATCH',
                                'TENANT_MISMATCH','UNAUTHORIZED_OCCUPANCY');

-- fraud_alerts.rule_code: a plain text column, so nothing stopped the seed
-- writing an internal detector reference ('ID-002', 'AR-001') into it. The API
-- declares it a FraudRuleCode, and Pydantic rejects the row rather than the
-- field, which is why one such value took down /dashboard and /fraud/alerts
-- together. The rule a detector fires under is exactly what alert_type now
-- holds, so read it from there; the detector's own reference is still in the
-- title, description and evidence.
UPDATE fraud_alerts
   SET rule_code = alert_type::text
 WHERE rule_code IS NULL
    OR rule_code NOT IN ('MULTIPLE_OWNERS','DUPLICATE_ULPIN','OWNERSHIP_MISMATCH',
                         'TENANT_MISMATCH','UNAUTHORIZED_OCCUPANCY');

-- unit_ownerships.ownership_mode: the schema records how title was acquired,
-- the API records how it is held. The two are not interchangeable, so the
-- holding pattern is derived from the facts rather than guessed from the label:
-- a unit with one live owner is held SOLE, a unit with several is held
-- TENANCY_IN_COMMON — which is what a fractional share is. LEASEHOLD and
-- COOPERATIVE describe the holding already and are kept.
UPDATE unit_ownerships o
   SET ownership_mode = CASE
           WHEN o.ownership_mode::text = 'CO_OPERATIVE' THEN 'COOPERATIVE'
           WHEN (SELECT count(*) FROM unit_ownerships s
                  WHERE s.unit_id = o.unit_id AND s.ended_on IS NULL) > 1
                THEN 'TENANCY_IN_COMMON'
           ELSE 'SOLE'
       END::ownership_mode
 WHERE o.ownership_mode::text IN ('FREEHOLD','INHERITED','ALLOTMENT',
                                  'GOVERNMENT_GRANT','POWER_OF_ATTORNEY','CO_OPERATIVE');

-- Report what is left, so a partial mapping is visible instead of silent.
DO $$
DECLARE
    v_left int := 0;
    v_row  record;
BEGIN
    FOR v_row IN
        SELECT 'units.unit_type'        AS col, unit_type::text        AS val FROM units
         WHERE unit_type::text NOT IN ('RESIDENTIAL','COMMERCIAL','INDUSTRIAL','PARKING',
                                       'STORAGE','UTILITY','COMMON_AREA','MIXED')
        UNION ALL
        SELECT 'units.unit_status', unit_status::text FROM units
         WHERE unit_status::text NOT IN ('DRAFT','ACTIVE','UNDER_DISPUTE','MERGED',
                                         'SUBDIVIDED','DEMOLISHED')
        UNION ALL
        SELECT 'units.occupancy_status', occupancy_status::text FROM units
         WHERE occupancy_status::text NOT IN ('OCCUPIED','VACANT','UNDER_RENOVATION','SEALED')
        UNION ALL
        SELECT 'verification_records.status', status::text FROM verification_records
         WHERE status::text NOT IN ('PENDING','IN_PROGRESS','PASSED','FAILED',
                                    'INCONCLUSIVE','WAIVED')
        UNION ALL
        SELECT 'verification_records.verification_type', verification_type::text
          FROM verification_records
         WHERE verification_type::text NOT IN ('DOCUMENT','FIELD_SURVEY','GEOMETRY',
                                               'IDENTITY','OWNERSHIP','AUTOMATED')
        UNION ALL
        SELECT 'fraud_alerts.alert_type', alert_type::text FROM fraud_alerts
         WHERE alert_type::text NOT IN ('MULTIPLE_OWNERS','DUPLICATE_ULPIN',
                                        'OWNERSHIP_MISMATCH','TENANT_MISMATCH',
                                        'UNAUTHORIZED_OCCUPANCY')
        UNION ALL
        SELECT 'fraud_alerts.rule_code', coalesce(rule_code,'(null)') FROM fraud_alerts
         WHERE rule_code IS NULL
            OR rule_code NOT IN ('MULTIPLE_OWNERS','DUPLICATE_ULPIN','OWNERSHIP_MISMATCH',
                                 'TENANT_MISMATCH','UNAUTHORIZED_OCCUPANCY')
        UNION ALL
        SELECT 'unit_ownerships.ownership_mode', ownership_mode::text FROM unit_ownerships
         WHERE ownership_mode::text NOT IN ('SOLE','JOINT_TENANCY','TENANCY_IN_COMMON',
                                            'LEASEHOLD','COOPERATIVE')
    LOOP
        v_left := v_left + 1;
        RAISE WARNING 'still outside the API vocabulary: % = %', v_row.col, v_row.val;
    END LOOP;

    IF v_left = 0 THEN
        RAISE NOTICE 'every enum value is in the API vocabulary';
    ELSE
        RAISE EXCEPTION '% row(s) would still raise LookupError', v_left;
    END IF;
END
$$;
