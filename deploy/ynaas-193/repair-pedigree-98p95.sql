-- Source checked 2026-10-14: RiceData pedigree 619325, expanded
-- t619325 and t619325\602404/0. The displayed terminal text is 98P95;
-- its postback value 0 is NOT a material name or variety ID.
-- Scope: the reported 02F6-155 snapshot only; no guessed ancestors.
BEGIN;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM ricedata.rice_pedigree_node n
    JOIN ricedata.rice_pedigree_edge e ON e.parent_node_id=n.pedigree_node_id
    JOIN ricedata.rice_pedigree_node c ON c.pedigree_node_id=e.child_node_id
    WHERE n.pedigree_node_id=1544178 AND n.pedigree_snapshot_id=23885
      AND n.source_node_key='v:0' AND n.material_name IN ('0','98P95')
      AND c.material_name='吉89-45/98P95'
  ) THEN RAISE EXCEPTION 'Source identity changed; review before repair'; END IF;
END $$;
UPDATE ricedata.rice_pedigree_node
SET material_name='98P95',normalized_name='98P95',node_type='text',
    source_href='https://www.ricedata.cn/variety/pedigree.aspx?id=619325&fromid='
WHERE pedigree_node_id=1544178 AND pedigree_snapshot_id=23885
  AND source_node_key='v:0' AND linked_variety_id IS NULL;
COMMIT;
