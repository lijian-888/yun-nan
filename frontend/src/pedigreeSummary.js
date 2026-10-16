// Directory copy belongs to the same graph as the canvas, not an expansion ring.
export function pedigreeRecordSummary(record, graph) {
  const current=graph?.variety&&graph.variety.id===record.id?graph:record;
  if(Number.isInteger(current.node_count)&&current.node_count>0)return `${current.node_count}个来源节点`;
  if(record.count_status==='needs_review')return '图谱节点数待核实';
  return record.pedigree_status==='pending_verification'?'系谱待原站校验':'暂无已核验亲本关系';
}
