#!/usr/bin/env bash
# =============================================================================
#  Database bootstrap — runs once, on an empty data directory.
#
#  The Postgres entrypoint executes files in /docker-entrypoint-initdb.d in
#  alphabetical order, but only the files directly inside it — subdirectories
#  are ignored. The schema lives in ../database mounted at /sql, so this script
#  drives it explicitly. Explicit ordering is the point: the numbering in
#  database/schemas is load-bearing (types before tables, tables before
#  indexes, indexes before triggers), and alphabetical merging of two mounted
#  directories would silently interleave the seeds into the middle of it.
# =============================================================================
set -euo pipefail

SCHEMA_DIR=/sql/schemas
SEED_DIR=/sql/seeds
DB="${POSTGRES_DB:-ulpin_db}"
SUPERUSER="${POSTGRES_USER:-postgres}"

run() {
  echo "==> $(basename "$1")"
  psql --username "$SUPERUSER" --dbname "$DB" \
       --set ON_ERROR_STOP=1 --no-psqlrc --quiet --file "$1"
}

echo "=== ULPIN schema bootstrap ==============================================="

# Order is fixed rather than globbed: a file added out of sequence should fail
# loudly here, not be picked up in whatever position its name sorts to.
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

# 00_extensions.sql creates ulpin_app with a placeholder password, because a
# schema file checked into git cannot hold a real one. Align it with what the
# API container was configured with.
if [ -n "${APP_DB_PASSWORD:-}" ]; then
  echo "==> aligning ulpin_app password"
  psql --username "$SUPERUSER" --dbname "$DB" --set ON_ERROR_STOP=1 --quiet <<-SQL
	ALTER ROLE ulpin_app     WITH PASSWORD '${APP_DB_PASSWORD}';
	ALTER ROLE ulpin_migrate WITH PASSWORD '${APP_DB_PASSWORD}';
	ALTER ROLE ulpin_readonly WITH PASSWORD '${APP_DB_PASSWORD}';
	SQL
fi

# Seeds are demonstration data — buildings, units, owners, and deliberately
# planted fraud cases so the detection rules have something to find on a fresh
# checkout. Never load them into an environment that holds real records.
if [ "${SEED_DATABASE:-true}" = "true" ] && [ -d "$SEED_DIR" ]; then
  for f in "$SEED_DIR"/*.sql; do
    [ -e "$f" ] || break
    run "$f"
  done
else
  echo "==> seeds skipped (SEED_DATABASE=${SEED_DATABASE:-true})"
fi

echo "=== bootstrap complete ==================================================="
