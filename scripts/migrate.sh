#!/usr/bin/env bash
# Applies migrations from scripts/migrations/*.sql in numerical order.
# Tracks applied versions in the schema_migrations table. Idempotent.
#
# Usage:
#   DATABASE_URL=postgresql://app:xxx@db:5432/lead_qualifier ./scripts/migrate.sh
# or:
#   ./scripts/migrate.sh postgresql://app:xxx@db:5432/lead_qualifier
#
# Exits non-zero on the first failure. Each migration runs in its own
# transaction (psql -1), so partial failure leaves the DB at the previous
# version and schema_migrations is updated only on success.

set -euo pipefail

DB_URL="${1:-${DATABASE_URL:-}}"
if [[ -z "${DB_URL}" ]]; then
  echo "error: DATABASE_URL not set and no argument provided" >&2
  exit 2
fi

MIGRATIONS_DIR="$(cd "$(dirname "$0")" && pwd)/migrations"
if [[ ! -d "${MIGRATIONS_DIR}" ]]; then
  echo "error: migrations dir not found: ${MIGRATIONS_DIR}" >&2
  exit 2
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "error: psql not installed" >&2
  exit 2
fi

echo "[migrate] ensuring schema_migrations table"
psql "${DB_URL}" -v ON_ERROR_STOP=1 -c "
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);" >/dev/null

APPLIED="$(psql "${DB_URL}" -t -A -c 'SELECT version FROM schema_migrations ORDER BY version;' | sed '/^$/d')"

shopt -s nullglob
PENDING_COUNT=0
for file in "${MIGRATIONS_DIR}"/*.sql; do
  base="$(basename "${file}")"
  version="${base%.sql}"

  if grep -Fxq "${version}" <<<"${APPLIED}"; then
    continue
  fi

  PENDING_COUNT=$((PENDING_COUNT + 1))
  echo "[migrate] applying ${version}"
  psql "${DB_URL}" -v ON_ERROR_STOP=1 -1 -f "${file}"
  psql "${DB_URL}" -v ON_ERROR_STOP=1 -c \
    "INSERT INTO schema_migrations (version) VALUES ('${version}');" >/dev/null
  echo "[migrate] applied ${version}"
done

if [[ "${PENDING_COUNT}" -eq 0 ]]; then
  echo "[migrate] no pending migrations"
else
  echo "[migrate] done (${PENDING_COUNT} applied)"
fi
