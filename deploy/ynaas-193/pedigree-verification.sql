-- Retain all old data; only source-checked snapshots become active.
BEGIN;
ALTER TABLE ricedata.rice_pedigree_snapshot
  ADD COLUMN IF NOT EXISTS source_verified boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS source_evidence_sha256 varchar(64);
CREATE OR REPLACE VIEW agent_query.ricedata__rice_pedigree_snapshot AS
SELECT pedigree_snapshot_id,variety_id,source_url,fully_expanded,
       node_count,edge_count,max_depth,crawled_at,source_verified,source_evidence_sha256
FROM ricedata.rice_pedigree_snapshot;
CREATE OR REPLACE VIEW agent_query.ricedata__rice_pedigree_node AS
SELECT n.* FROM ricedata.rice_pedigree_node n
JOIN ricedata.rice_pedigree_snapshot s USING(pedigree_snapshot_id)
WHERE s.source_verified;
CREATE OR REPLACE VIEW agent_query.ricedata__rice_pedigree_edge AS
SELECT e.* FROM ricedata.rice_pedigree_edge e
JOIN ricedata.rice_pedigree_snapshot s USING(pedigree_snapshot_id)
WHERE s.source_verified;
CREATE INDEX IF NOT EXISTS idx_pedigree_verified_latest
ON ricedata.rice_pedigree_snapshot(variety_id,crawled_at DESC,pedigree_snapshot_id DESC)
WHERE source_verified;
COMMIT;
