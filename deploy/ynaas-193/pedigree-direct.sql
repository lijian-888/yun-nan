BEGIN;
CREATE TABLE IF NOT EXISTS ricedata.rice_pedigree_direct_fact (
 fact_key text PRIMARY KEY,source_id text NOT NULL,material_name text NOT NULL,
 fragment jsonb NOT NULL,signature text NOT NULL,status text NOT NULL DEFAULT 'verified_direct',
 evidence jsonb NOT NULL DEFAULT '[]'::jsonb,conflict_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
 collected_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ricedata.rice_pedigree_direct_job (
 source_id text PRIMARY KEY,root_key text,status text NOT NULL DEFAULT 'pending',priority integer NOT NULL DEFAULT 10,
 attempts integer NOT NULL DEFAULT 0,error text,collected_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_pedigree_direct_pending ON ricedata.rice_pedigree_direct_job(priority,source_id) WHERE status='pending';
CREATE OR REPLACE VIEW agent_query.ricedata__rice_pedigree_direct_fact AS
 SELECT fact_key,source_id,material_name,fragment,status,collected_at FROM ricedata.rice_pedigree_direct_fact;
CREATE OR REPLACE VIEW agent_query.ricedata__rice_pedigree_direct_job AS
 SELECT source_id,root_key,status,collected_at FROM ricedata.rice_pedigree_direct_job;
GRANT SELECT ON agent_query.ricedata__rice_pedigree_direct_fact,agent_query.ricedata__rice_pedigree_direct_job TO ynaas_longyun_api;
COMMIT;
