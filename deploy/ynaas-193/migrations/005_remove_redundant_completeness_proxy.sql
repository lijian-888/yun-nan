-- One-time simplification for installations that already ran migration 004.
-- The materialized result contains exactly the same public columns as the
-- redundant cache proxy. Switch the catalog and remove only that proxy.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
GRANT SELECT ON agent_query.completeness_summary_snapshot TO ynaas_longyun_api;
UPDATE agent_query.dataset_registry
SET query_view='completeness_summary_snapshot'
WHERE dataset_id='ai.completeness_summary'
  AND query_view IN ('ai_completeness_cached','completeness_summary_snapshot');
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM agent_query.dataset_registry
        WHERE dataset_id='ai.completeness_summary'
          AND query_view='completeness_summary_snapshot'
    ) THEN
        RAISE EXCEPTION 'Completeness catalog was not switched';
    END IF;
END $$;
DROP VIEW IF EXISTS agent_query.ai_completeness_cached;
COMMIT;
