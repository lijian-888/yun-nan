// Pure layout/traversal. Snapshot identity, not names, determines each node.
const key = id => String(id);
export const NODE_WIDTH = 210;
export const NODE_HEIGHT = 60;

export function visiblePedigree(graph, expanded = new Set(), showAll = false) {
  if (!graph) return { nodes: [], edges: [] };
  const ids = new Set(graph.nodes.map(n => key(n.id)));
  const adjacency = new Map();
  graph.edges.forEach(edge => {
    if (!ids.has(key(edge.child_id)) || !ids.has(key(edge.parent_id))) return;
    const children = adjacency.get(key(edge.child_id)) || [];
    children.push(key(edge.parent_id)); adjacency.set(key(edge.child_id), children);
  });
  const visible = new Set(showAll ? ids : graph.root_ids.map(key));
  const pending = [...visible];
  while (pending.length) {
    const child = pending.pop();
    if (!expanded.has(child) && !showAll) continue;
    (adjacency.get(child) || []).forEach(parent => { if (!visible.has(parent)) { visible.add(parent); pending.push(parent); } });
  }
  return { nodes: graph.nodes.filter(n => visible.has(key(n.id))),
    edges: graph.edges.filter(e => visible.has(key(e.child_id)) && visible.has(key(e.parent_id)) && (showAll || expanded.has(key(e.child_id)))) };
}

export function pedigreeDepths(graph) {
  const depths = new Map(graph.root_ids.map(id => [key(id), 0]));
  const adjacency = new Map();
  graph.edges.forEach(e => { const parents = adjacency.get(key(e.child_id)) || []; parents.push(key(e.parent_id)); adjacency.set(key(e.child_id), parents); });
  const pending = [...depths.keys()];
  for (let index=0; index<pending.length; index++) {
    const child = pending[index];
    (adjacency.get(child) || []).forEach(parent => {
      if (!depths.has(parent)) { depths.set(parent, depths.get(child)+1); pending.push(parent); }
    });
  }
  return depths;
}

export function expandLevels(graph, levels) {
  return new Set([...pedigreeDepths(graph)].filter(([,depth]) => depth<levels).map(([id]) => id));
}

export function layoutPedigree(graph, visible) {
  const depths = pedigreeDepths(graph);
  const disconnectedLevel = Math.max(0, ...depths.values())+1;
  const groups = new Map();
  visible.nodes.forEach(node => {
    const depth = depths.get(key(node.id)) ?? disconnectedLevel;
    const items = groups.get(depth) || []; items.push(node); groups.set(depth, items);
  });
  const rowCount = Math.max(1, ...[...groups.values()].map(nodes => nodes.length));
  const height = Math.max(260, rowCount*86+100);
  const positions = new Map();
  groups.forEach((nodes, depth) => {
    nodes.sort((a,b) => Number(a.id)-Number(b.id));
    const offset = (height - nodes.length*86)/2;
    nodes.forEach((node,index) => positions.set(key(node.id), {x:60+depth*286,y:offset+index*86}));
  });
  const nodes = visible.nodes.map(node => ({ ...node, ...positions.get(key(node.id)), disconnected: !depths.has(key(node.id)) }));
  const edges = visible.edges.map(edge => ({ ...edge, from:positions.get(key(edge.child_id)), to:positions.get(key(edge.parent_id)) })).filter(e => e.from && e.to);
  const width = Math.max(380, ...nodes.map(n => n.x+NODE_WIDTH+60));
  const labels = [...groups.keys()].map(depth => ({x:60+depth*286,y:30,label:depth===disconnectedLevel ? "未连接来源节点" : depth===0 ? "当前品种" : `亲本系谱第 ${depth} 层`}));
  return {nodes,edges,width,height,labels};
}

export function fitView(layout, viewport) {
  const scale = Math.min(1.15, Math.max(.08, Math.min((viewport.width-48)/layout.width,(viewport.height-48)/layout.height)));
  return {scale,x:(viewport.width-layout.width*scale)/2,y:(viewport.height-layout.height*scale)/2};
}

export function zoomView(view, factor, point) {
  const scale = Math.max(.08, Math.min(3, view.scale*factor));
  return {scale,x:point.x-(point.x-view.x)*scale/view.scale,y:point.y-(point.y-view.y)*scale/view.scale};
}
