-- ===========================================================================
-- 07_auth_otp.sql — one-time passcodes for email verification and recovery
-- ===========================================================================
--
-- Adds OTP challenge state to ulpin.users.
--
-- Two independent challenges, not one with a "purpose" column: a registration
-- code and a reset code can be live at the same time, and separate columns make
-- it structurally impossible for a code emailed to confirm an address to be
-- accepted as authority to change a password. A single shared slot would need a
-- purpose check on every read, and one missed check is an account takeover.
--
-- Both live on the user row rather than in an otp_codes table because a user
-- holds at most one live code per purpose: issuing a new one must invalidate the
-- old one, and an UPDATE does that atomically where a delete-then-insert can
-- leave two valid codes if it half-fails.
--
-- What is stored is a SHA-256 of 'user_id:code', never the six digits. A digest
-- of a six-digit code is of course brute-forcible offline in microseconds — what
-- stops guessing is elsewhere (ten-minute expiry, five-attempt cap, single use).
-- Hashing earns its place against the cheaper exposures: a support engineer
-- reading the table, a logged row, a backup on a laptop. The user_id prefix means
-- one precomputed table of 10^6 digests does not unlock every row at once.
--
-- The existing link-token columns (verify_token_hash, reset_token_hash) are left
-- untouched and still work. Administrative invitations send a link, not a code,
-- and the reset flow re-uses reset_token_hash as the short-lived ticket minted
-- once a reset OTP has been verified.
--
-- Idempotent: safe to re-run.

BEGIN;

SET search_path TO ulpin, public;

-- --------------------------------------------------------------------------
-- Registration / email verification challenge
-- --------------------------------------------------------------------------
ALTER TABLE ulpin.users
    ADD COLUMN IF NOT EXISTS otp_code_hash   varchar(64),
    ADD COLUMN IF NOT EXISTS otp_expires_at  timestamptz,
    ADD COLUMN IF NOT EXISTS otp_attempts    smallint NOT NULL DEFAULT 0;

-- --------------------------------------------------------------------------
-- Password reset challenge
-- --------------------------------------------------------------------------
ALTER TABLE ulpin.users
    ADD COLUMN IF NOT EXISTS reset_otp_hash       varchar(64),
    ADD COLUMN IF NOT EXISTS reset_otp_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS reset_otp_attempts   smallint NOT NULL DEFAULT 0;

-- --------------------------------------------------------------------------
-- Send throttling (shared)
-- --------------------------------------------------------------------------
-- Shared across both purposes deliberately: these protect one mailbox, and a
-- flood is a flood whichever endpoint produced it. otp_window_started_at anchors
-- a rolling hour, so the count resets on its own without a scheduled job.
ALTER TABLE ulpin.users
    ADD COLUMN IF NOT EXISTS otp_sent_at           timestamptz,
    ADD COLUMN IF NOT EXISTS otp_send_count        smallint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS otp_window_started_at timestamptz;

COMMENT ON COLUMN ulpin.users.otp_code_hash IS
    'SHA-256 of ''<user_id>:<code>'' for the email-verification OTP. Never the code.';
COMMENT ON COLUMN ulpin.users.otp_expires_at IS
    'Verification challenge deadline; 10 minutes from issue (settings.OTP_EXPIRE_MINUTES).';
COMMENT ON COLUMN ulpin.users.otp_attempts IS
    'Wrong guesses against the current verification code. Reset when a new code is issued.';
COMMENT ON COLUMN ulpin.users.reset_otp_hash IS
    'SHA-256 of ''<user_id>:<code>'' for the password-reset OTP. Never the code.';
COMMENT ON COLUMN ulpin.users.reset_otp_expires_at IS
    'Reset challenge deadline; 10 minutes from issue.';
COMMENT ON COLUMN ulpin.users.reset_otp_attempts IS
    'Wrong guesses against the current reset code. Reset when a new code is issued.';
COMMENT ON COLUMN ulpin.users.otp_send_count IS
    'Codes of any purpose sent inside the window starting at otp_window_started_at.';

-- A hash without a deadline would never expire; a deadline without a hash is
-- dead state. Neither is reachable through the service, so reject both.
ALTER TABLE ulpin.users DROP CONSTRAINT IF EXISTS users_otp_paired_check;
ALTER TABLE ulpin.users
    ADD CONSTRAINT users_otp_paired_check
    CHECK ((otp_code_hash IS NULL) = (otp_expires_at IS NULL));

ALTER TABLE ulpin.users DROP CONSTRAINT IF EXISTS users_reset_otp_paired_check;
ALTER TABLE ulpin.users
    ADD CONSTRAINT users_reset_otp_paired_check
    CHECK ((reset_otp_hash IS NULL) = (reset_otp_expires_at IS NULL));

-- --------------------------------------------------------------------------
-- Lookup
-- --------------------------------------------------------------------------
-- Verification always arrives as (email, code): the address finds the row via the
-- existing uq_users_email_lower, then the digest is compared in constant time.
-- These partial indexes exist only for the sweep that clears expired challenges,
-- keeping that scan off the full citizen population.
CREATE INDEX IF NOT EXISTS ix_users_otp_expires_at
    ON ulpin.users (otp_expires_at) WHERE otp_code_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_users_reset_otp_expires_at
    ON ulpin.users (reset_otp_expires_at) WHERE reset_otp_hash IS NOT NULL;

-- --------------------------------------------------------------------------
-- Existing rows
-- --------------------------------------------------------------------------
-- Login now refuses an account whose address is unconfirmed, and it checks
-- email_verified rather than status. Seeded and administratively provisioned
-- accounts that are already ACTIVE have by definition been through
-- provisioning, so confirm them — otherwise this migration locks out every
-- existing user, including the demo admin.
UPDATE ulpin.users
   SET email_verified    = true,
       email_verified_at = COALESCE(email_verified_at, created_at)
 WHERE status = 'ACTIVE'
   AND email_verified IS NOT true
   AND deleted_at IS NULL;

COMMIT;

-- ===========================================================================
-- Verification
-- ===========================================================================
-- SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--  WHERE table_schema = 'ulpin' AND table_name = 'users'
--    AND (column_name LIKE 'otp%' OR column_name LIKE 'reset_otp%')
--  ORDER BY column_name;
--
-- SELECT email, status, email_verified FROM ulpin.users ORDER BY created_at;
