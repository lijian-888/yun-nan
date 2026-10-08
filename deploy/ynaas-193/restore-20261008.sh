#!/usr/bin/env bash
# Restore the verified 2026-10-08 snapshot into a NEW, empty Yunnan-only DB.
# Never runs pg_restore --clean and never touches other Compose projects.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT_DIR/deploy/ynaas-193/.env"
BACKUP_DIR=/home/lijian/apps/ynaas-trial-data-import
BACKUP_FILE="$BACKUP_DIR/ynaas_rice_ai.full.backup"
EXPECTED_SHA=9413f30994b1640d14b24a5a90e628a103c715278029b71dd9988e06a96626d5
EXPECTED_COUNTS='133|5348678|9b28e0a43b3bd4b890660f05c14007ee'
DB_CONTAINER=ynaas-trial-db-1

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ -r "$BACKUP_FILE" ]] || fail "Backup file is missing: $BACKUP_FILE"
[[ -f "$ENV_FILE" ]] || fail "Runtime environment is missing: $ENV_FILE"
actual_sha="$(sha256sum "$BACKUP_FILE" | awk '{print $1}')"
[[ "$actual_sha" == "$EXPECTED_SHA" ]] || fail 'Backup checksum differs from the source snapshot.'

project="$(docker inspect "$DB_CONTAINER" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)"
[[ "$project" == ynaas-trial ]] || fail 'Database container is not in the Yunnan trial project.'
health="$(docker inspect "$DB_CONTAINER" --format '{{.State.Health.Status}}')"
[[ "$health" == healthy ]] || fail "Yunnan database is not healthy: $health"

vector_version="$(docker exec "$DB_CONTAINER" psql -U rice -d ynaas_rice_ai -Atc \
  "SELECT default_version FROM pg_available_extensions WHERE name='vector'")"
[[ "$vector_version" == 0.8.6 ]] || fail "Expected pgvector 0.8.6; found $vector_version"

existing_tables="$(docker exec "$DB_CONTAINER" psql -U rice -d ynaas_rice_ai -Atc \
  "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema','keycloak')")"
[[ "$existing_tables" == 0 ]] || fail "Target database already has $existing_tables user tables; refusing to restore over them."

docker cp "$BACKUP_FILE" "$DB_CONTAINER:/tmp/ynaas_rice_ai.full.backup"
docker exec "$DB_CONTAINER" pg_restore -U rice -d ynaas_rice_ai \
  --no-owner --no-privileges --exit-on-error \
  /tmp/ynaas_rice_ai.full.backup >"$BACKUP_DIR/restore-20261008.log" 2>&1 || {
    echo "Restore stopped with an error. Review $BACKUP_DIR/restore-20261008.log; do not run it again blindly." >&2
    exit 1
  }

docker cp "$ROOT_DIR/deploy/ynaas-193/verify-table-counts.sql" \
  "$DB_CONTAINER:/tmp/verify-table-counts.sql"
counts="$(docker exec "$DB_CONTAINER" psql -X -U rice -d ynaas_rice_ai \
  -f /tmp/verify-table-counts.sql | tail -n 1)"
[[ "$counts" == "$EXPECTED_COUNTS" ]] || fail "Restored table counts differ: $counts"
echo "Yunnan snapshot restored and all table counts match: $counts"
echo 'The model API key remains unset; AI answers are not available yet.'
