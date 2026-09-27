#!/usr/bin/env bash
# =============================================================================
#  Database bootstrap for a native PostgreSQL install (no Docker).
#
#  infra/postgres/initdb/00-bootstrap.sh does this job inside the Postgres
#  container, driven by the image's entrypoint on an empty data directory.
#  Neither of those conditions holds when Postgres is installed on the host:
#  there is no entrypoint to hook, and the data directory already exists. This
#  script is the host-side equivalent — same files, same fixed order, same
#  password alignment — and unlike the container version it is safe to re-run.
#
#  Usage, from the repository root:
#
#      PGPASSWORD=yourpostgrespassword database/scripts/bootstrap_local.sh
#
#  Omit PGPASSWORD to be prompted once. Set SEED_DATABASE=false to load the
#  schema without the demonstration data.
# =============================================================================
set -euo pipefail

DB="${POSTGRES_DB:-ulpin_db}"
SUPERUSER="${POSTGRES_SUPERUSER:-postgres}"
HOST="${POSTGRES_SERVER:-localhost}"
PORT="${POSTGRES_PORT:-5432}"
# Must match POSTGRES_PASSWORD in apps/api/.env, or the API authenticates as
# ulpin_app with the wrong password and every request fails at the pool.
APP_DB_PASSWORD="${APP_DB_PASSWORD:-change_me_in_production}"

# -- locate psql --------------------------------------------------------------
# The Windows installer does not put its bin directory on PATH, so fall back to
# the standard install locations, newest first.
if command -v psql >/dev/null 2>&1; then
  PSQL=psql
else
  PSQL=""
  for v in 18 17 16 15; do
    candidate="/c/Program Files/PostgreSQL/$v/bin/psql.exe"
    if [ -x "$candidate" ]; then PSQL="$candidate"; break; fi
  done
  if [ -z "$PSQL" ]; then
    echo "psql not found. Install PostgreSQL, or add its bin directory to PATH." >&2
    exit 1
  fi
fi
echo "psql: $PSQL"

# -- resolve the repository root ----------------------------------------------
# psql.exe is a native Windows binary: it resolves --file against a Windows
# working directory. Running from the repo root and passing relative paths
# sidesteps POSIX-to-Windows path translation entirely.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SCHEMA_DIR=database/schemas
SEED_DIR=database/seeds

if [ -z "${PGPASSWORD:-}" ]; then
  read -rsp "Password for PostgreSQL role '$SUPERUSER': " PGPASSWORD
  echo
fi
export PGPASSWORD

pg() { "$PSQL" --host "$HOST" --port "$PORT" --username "$SUPERUSER" --no-psqlrc "$@"; }

run() {
  echo "==> $(basename "$1")"
  pg --dbname "$DB" --set ON_ERROR_STOP=1 --quiet --file "$1"
}

echo "=== ULPIN schema bootstrap ==============================================="

# -- reachability -------------------------------------------------------------
# Checked before anything else so an unreachable server or a wrong password
# reports itself plainly, rather than as a confusing failure midway through the
# schema.
if ! pg --dbname postgres --set ON_ERROR_STOP=1 --quiet --command "SELECT 1" >/dev/null 2>&1; then
  echo "Cannot connect to $HOST:$PORT as '$SUPERUSER'." >&2
  echo "Check the service is running and the password is correct." >&2
  exit 1
fi

# -- database ----------------------------------------------------------------
if pg --dbname postgres --tuples-only --no-align \
      --command "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  echo "==> database $DB already exists"
else
  echo "==> creating database $DB"
  pg --dbname postgres --set ON_ERROR_STOP=1 --quiet \
     --command "CREATE DATABASE $DB ENCODING 'UTF8' TEMPLATE template0"
fi

# -- schema ------------------------------------------------------------------
# Order is fixed rather than globbed: types before tables, tables before
# indexes, indexes before triggers. A file added out of sequence should fail
# loudly here, not be picked up wherever its name happens to sort.
for f in \
  "$SCHEMA_DIR/00_extensions.sql" \
  "$SCHEMA_DIR/01_types.sql" \
  "$SCHEMA_DIR/02_tables.sql" \
  "$SCHEMA_DIR/03_indexes.sql" \
  "$SCHEMA_DIR/04_triggers.sql" \
  "$SCHEMA_DIR/05_modules.sql"
do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
  run "$f"
done

# 00_extensions.sql creates the three application roles with a placeholder
# password, because a schema file checked into git cannot hold a real one.
echo "==> aligning role passwords"
pg --dbname "$DB" --set ON_ERROR_STOP=1 --quiet <<SQL
ALTER ROLE ulpin_app      WITH PASSWORD '$APP_DB_PASSWORD';
ALTER ROLE ulpin_migrate  WITH PASSWORD '$APP_DB_PASSWORD';
ALTER ROLE ulpin_readonly WITH PASSWORD '$APP_DB_PASSWORD';
SQL

# -- seeds -------------------------------------------------------------------
# Demonstration data: buildings, units, owners, and deliberately planted fraud
# cases so the detection rules have something to find on a fresh checkout.
# Never load these into an environment holding real records.
if [ "${SEED_DATABASE:-true}" = "true" ] && [ -d "$SEED_DIR" ]; then
  for f in "$SEED_DIR"/*.sql; do
    [ -e "$f" ] || break
    run "$f"
  done
else
  echo "==> seeds skipped (SEED_DATABASE=${SEED_DATABASE:-true})"
fi

# -- verify ------------------------------------------------------------------
# The schema's own guard already raises if PostGIS or SFCGAL is absent; this
# echoes what actually landed, so a successful run is visibly successful.
echo "==> verifying"
pg --dbname "$DB" --tuples-only --no-align --set ON_ERROR_STOP=1 <<'SQL'
SELECT '    postgis        ' || postgis_version();
SELECT '    sfcgal         ' || postgis_sfcgal_version();
SELECT '    buildings      ' || count(*) FROM ulpin.buildings;
SELECT '    units          ' || count(*) FROM ulpin.units;
SELECT '    ulpins         ' || count(*) FROM ulpin.ulpins;
SELECT '    fraud_alerts   ' || count(*) FROM ulpin.fraud_alerts;
SQL

echo "=== bootstrap complete ==================================================="
echo
echo "Next: create a sign-in account (the seeded users carry placeholder hashes"
echo "and cannot log in):"
echo
echo "    cd apps/api && ./.venv/Scripts/python.exe scripts/create_admin.py \\"
echo "        --email admin@ulpin.gov.in"
