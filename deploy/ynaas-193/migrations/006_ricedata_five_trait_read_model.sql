BEGIN;

CREATE OR REPLACE VIEW agent_query.ricedata__five_trait_comprehensive_evaluation
WITH (security_barrier=true) AS
SELECT
    variety_id,
    variety_name,
    variety_type,
    yield_score,
    stability_score,
    disease_score,
    stress_score,
    quality_score,
    comprehensive_score,
    comprehensive_rank,
    scored_dimension_count,
    missing_dimensions,
    evaluation_status,
    evaluation_profile_id,
    evidence_summary,
    interpretation_boundary
FROM ai.ricedata_five_trait_comprehensive_evaluation;

GRANT SELECT ON agent_query.ricedata__five_trait_comprehensive_evaluation TO ynaas_longyun_api;

COMMIT;
