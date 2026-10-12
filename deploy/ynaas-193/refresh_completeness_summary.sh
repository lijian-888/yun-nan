#!/usr/bin/env bash
# Run after a source-data import and nightly during a quiet period. Requires
# migration 004 and the local Yunnan PostgreSQL container; no model/API key.
set -euo pipefail

echo "$(date '+%Y-%m-%d %H:%M:%S %Z') completeness refresh started"
docker exec ynaas-trial-db-1 psql -X -q -U rice -d ynaas_rice_ai \
  -v ON_ERROR_STOP=1 \
  -c "SET statement_timeout='10min'" \
  -c "SET lock_timeout='5s'" \
  -c 'REFRESH MATERIALIZED VIEW CONCURRENTLY agent_query.completeness_summary_snapshot'
docker exec ynaas-trial-db-1 psql -X -At -U rice -d ynaas_rice_ai \
  -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) || ' dimensions; snapshot=' || max(snapshot_refreshed_at)::text FROM agent_query.completeness_summary_snapshot"
echo "$(date '+%Y-%m-%d %H:%M:%S %Z') completeness refresh finished"
