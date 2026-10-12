-- Cache only the verified slow, whole-institution completeness aggregate.
-- Source ai.completeness_summary remains unchanged. Run explicitly as the
-- migration administrator with psql -v ON_ERROR_STOP=1, after a database backup.
-- The catalog is switched only after the first refresh succeeds.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_query.completeness_summary_snapshot AS
SELECT source.*, CURRENT_TIMESTAMP AS snapshot_refreshed_at
FROM ai.completeness_summary AS source
WITH NO DATA;
REVOKE ALL ON agent_query.completeness_summary_snapshot FROM PUBLIC;
COMMIT;

-- The first calculation is intentionally bounded but longer than an HTTP read.
SET statement_timeout='10min';
REFRESH MATERIALIZED VIEW agent_query.completeness_summary_snapshot;
RESET statement_timeout;

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
CREATE UNIQUE INDEX IF NOT EXISTS completeness_summary_snapshot_dimension_idx
    ON agent_query.completeness_summary_snapshot (data_dimension);
CREATE OR REPLACE VIEW agent_query.ai_completeness_cached WITH (security_barrier=true) AS
SELECT data_dimension, dimension_name_zh, material_count, materials_with_evidence,
       materials_missing, material_coverage_rate, average_coverage_score,
       evidence_count, comparable_count, warning_count,
       max_year_count_per_material, max_environment_count_per_material,
       status_distribution, snapshot_refreshed_at
FROM agent_query.completeness_summary_snapshot;
REVOKE ALL ON agent_query.ai_completeness_cached FROM PUBLIC;
GRANT SELECT ON agent_query.ai_completeness_cached TO ynaas_longyun_api;
UPDATE agent_query.dataset_registry
SET query_view='ai_completeness_cached',
    description='院内材料按数据维度汇总的材料总数、有证据数、缺失数、覆盖率及证据质量；每日更新的只读统计快照，snapshot_refreshed_at为计算时间。'
WHERE dataset_id='ai.completeness_summary';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM agent_query.dataset_registry
        WHERE dataset_id='ai.completeness_summary' AND query_view='ai_completeness_cached'
    ) THEN
        RAISE EXCEPTION 'Completeness summary catalog entry is missing';
    END IF;
END $$;
COMMENT ON MATERIALIZED VIEW agent_query.completeness_summary_snapshot IS
    '每日或数据导入后刷新；仅缓存ai.completeness_summary的全院维度汇总。';
COMMIT;
