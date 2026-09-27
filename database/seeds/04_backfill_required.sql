-- =============================================================================
--  04_backfill_required.sql — supply values the API declares non-optional
--
--  Fourth reconciliation pass, and a different failure from the first three.
--  02 fixed columns whose names diverged and 03 fixed values whose labels
--  diverged; this file fills columns that exist, are correctly named, and are
--  simply empty — because the SQL schema left them nullable while the response
--  models in app/schemas/ declare them required.
--
--  A NULL in one of those columns fails validation for the *whole row*, and the
--  endpoint returns 500 rather than a row with one blank field. `state` is the
--  clearest case: buildings.state was never populated by the seed, so
--  GET /buildings raised
--      ValidationError: state — Input should be a valid string, input_value=None
--  even though every other field on the building was present and correct.
--
--  Idempotent: every assignment is COALESCE-guarded, so a value already set is
--  never overwritten. Run after 03, as a superuser, from the repository root:
--
--    psql -U postgres -d ulpin_db -f database/seeds/04_backfill_required.sql
-- =============================================================================

\set ON_ERROR_STOP on
SET search_path TO ulpin, public;

-- buildings.state / district. The state is already recorded twice over, just
-- not in this column: jurisdiction_code carries it as an ISO-3166-2 style
-- prefix ('KA-BLR-001') and parcel_ulpin carries the LGD state number
-- ('IN29BLR0001234' -> 29 -> Karnataka). The prefix is the more readable of
-- the two, so it is the one expanded here; the numeric form stays the
-- authority on the parcel.
--
-- District falls back to city: for the metropolitan jurisdictions in this
-- dataset the city *is* the district, and a named district beats a null one in
-- the address block the UI renders.
UPDATE buildings
   SET state = COALESCE(
           state,
           CASE split_part(jurisdiction_code, '-', 1)
               WHEN 'AP' THEN 'Andhra Pradesh'    WHEN 'AS' THEN 'Assam'
               WHEN 'BR' THEN 'Bihar'             WHEN 'CG' THEN 'Chhattisgarh'
               WHEN 'DL' THEN 'Delhi'             WHEN 'GA' THEN 'Goa'
               WHEN 'GJ' THEN 'Gujarat'           WHEN 'HR' THEN 'Haryana'
               WHEN 'HP' THEN 'Himachal Pradesh'  WHEN 'JH' THEN 'Jharkhand'
               WHEN 'JK' THEN 'Jammu and Kashmir' WHEN 'KA' THEN 'Karnataka'
               WHEN 'KL' THEN 'Kerala'            WHEN 'MP' THEN 'Madhya Pradesh'
               WHEN 'MH' THEN 'Maharashtra'       WHEN 'OD' THEN 'Odisha'
               WHEN 'PB' THEN 'Punjab'            WHEN 'RJ' THEN 'Rajasthan'
               WHEN 'TN' THEN 'Tamil Nadu'        WHEN 'TS' THEN 'Telangana'
               WHEN 'UK' THEN 'Uttarakhand'       WHEN 'UP' THEN 'Uttar Pradesh'
               WHEN 'WB' THEN 'West Bengal'
               ELSE split_part(jurisdiction_code, '-', 1)
           END
       ),
       district = COALESCE(district, city);

-- Fail loudly rather than leave the endpoint returning 500 on the next request.
DO $$
DECLARE
    v_bad int;
BEGIN
    SELECT count(*) INTO v_bad FROM buildings WHERE state IS NULL OR state = '';
    IF v_bad > 0 THEN
        RAISE EXCEPTION '% building(s) still have no state', v_bad;
    END IF;
    RAISE NOTICE 'every building has a state';
END
$$;

-- ---------------------------------------------------------------------------
-- Denormalised verification state
-- ---------------------------------------------------------------------------
-- units.verification_outcome is a cached summary of the unit's verification
-- history, and the seed left it at PENDING_VERIFICATION for all 32 units while
-- simultaneously writing 33 passed and 1 failed verification_records against
-- them. The two disagree, and the detailed records are the evidence, so they
-- win: the cache is recomputed from them rather than the other way round.
--
-- This is not cosmetic. The 3D viewer colours each unit from this field —
-- green verified, yellow pending, red invalid or unauthorised — so a column
-- stuck on one value renders the entire building in one colour and the legend
-- becomes decoration.
--
-- Note what this does *not* do: it does not invent a spread of outcomes to
-- make the viewer look livelier. Every seeded unit genuinely has a completed
-- verification, so the honest result is mostly green with one invalid claim,
-- and the pending tone shows up on units created after this point.
UPDATE units u
   SET verification_outcome = v.outcome
  FROM (
      SELECT DISTINCT ON (unit_id)
             unit_id,
             CASE status::text
                 WHEN 'PASSED' THEN 'VERIFIED'
                 WHEN 'FAILED' THEN 'INVALID_CLAIM'
                 ELSE 'PENDING_VERIFICATION'
             END::verification_outcome AS outcome
        FROM verification_records
       WHERE unit_id IS NOT NULL
       ORDER BY unit_id, verified_at DESC NULLS LAST, created_at DESC
  ) AS v
 WHERE v.unit_id = u.unit_id
   AND u.verification_outcome IS DISTINCT FROM v.outcome;

-- verification_records.outcome is the same finding stated on the record
-- itself, and was likewise never populated.
UPDATE verification_records
   SET outcome = CASE status::text
           WHEN 'PASSED' THEN 'VERIFIED'
           WHEN 'FAILED' THEN 'INVALID_CLAIM'
           ELSE 'PENDING_VERIFICATION'
       END::verification_outcome
 WHERE outcome IS NULL;

DO $$
DECLARE
    v_row record;
BEGIN
    FOR v_row IN
        SELECT coalesce(verification_outcome::text, '(null)') AS outcome, count(*) AS n
          FROM units GROUP BY 1 ORDER BY 2 DESC
    LOOP
        RAISE NOTICE 'units %: %', v_row.outcome, v_row.n;
    END LOOP;
END
$$;

