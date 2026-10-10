-- Yunnan single-institution trial: curated, SELECT-only local query surfaces.
-- Run explicitly as the migration administrator. No source records are changed.
-- These are shared institute datasets for authenticated, approved researchers;
-- private per-project uploads remain behind the existing application/RLS boundary.
BEGIN;
CREATE SCHEMA IF NOT EXISTS agent_data;
REVOKE ALL ON SCHEMA agent_data FROM PUBLIC;

CREATE OR REPLACE VIEW agent_data.material AS
SELECT material_id, preferred_name, material_status, material_type,
       earliest_year, latest_year, has_genotype, has_phenotype, needs_review
FROM core.material
WHERE material_status IN ('active', 'name_only', 'genotype_only');

CREATE OR REPLACE VIEW agent_data.material_alias AS
SELECT a.material_id, a.raw_alias, a.normalized_alias, a.review_status
FROM core.material_alias a JOIN agent_data.material m USING (material_id)
WHERE a.review_status = 'accepted';

CREATE OR REPLACE VIEW agent_data.phenotype AS
SELECT p.phenotype_value_id, p.material_id, p.trait_code, p.registry_trait_code,
       p.raw_header, p.value_numeric, p.value_text, p.raw_value, p.unit,
       p.statistic, p.sample_n, p.sub_sample_no, p.replicate_no,
       COALESCE(p.observed_on, e.observed_on) AS observed_on,
       p.quality_status, p.source_file_id, p.sheet_name, p.row_number,
       p.measurement_event_id, x.trial_year, x.experiment_name,
       x.raw_location_text AS observation_location
FROM core.phenotype_value p JOIN agent_data.material m USING (material_id)
LEFT JOIN core.measurement_event e USING (measurement_event_id)
LEFT JOIN core.experiment x ON x.experiment_id = e.experiment_id;

REVOKE ALL ON ALL TABLES IN SCHEMA agent_data FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ynaas_longyun_api') THEN
        GRANT USAGE ON SCHEMA agent_data TO ynaas_longyun_api;
        GRANT SELECT ON agent_data.material, agent_data.material_alias, agent_data.phenotype TO ynaas_longyun_api;
    END IF;
END $$;
COMMENT ON SCHEMA agent_data IS
'云南单机构试用版：仅供已授权科研人员本地查询的治理数据视图，不授权原始文件路径、不授权写入、不发送外部模型。';
COMMIT;
