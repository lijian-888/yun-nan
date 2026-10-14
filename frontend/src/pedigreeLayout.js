// Pure layout/traversal. Snapshot identity, not names, determines each node.
const key = id => String(id);
export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 100;

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
  // A source DAG is not necessarily planar. Unfold only its spanning forest;
  // each remaining edge ends at a labelled reference, NEVER a remote cross-link.
  // Every source node has exactly one canonical card; every source edge appears
  // exactly once. References are leaves, so size is O(V+E), not exponential.
  const sources = new Map(visible.nodes.map(node => [key(node.id), node]));
  const adjacency = new Map();
  const rank = {female:0,male:1,parent:2,unknown:3};
  visible.edges.forEach(edge => {
    if (!sources.has(key(edge.child_id)) || !sources.has(key(edge.parent_id))) return;
    const items = adjacency.get(key(edge.child_id)) || [];
    items.push(edge); adjacency.set(key(edge.child_id),items);
  });
  adjacency.forEach(items => items.sort((a,b) => (rank[a.role]??3)-(rank[b.role]??3) ||
    Number(a.display_order||0)-Number(b.display_order||0) || key(a.id).localeCompare(key(b.id),"en",{numeric:true})));
  const reachable = pedigreeDepths(graph);
  const rootIds = new Set(graph.root_ids.map(key));
  const canonical = new Map(), active = new Set(), nodes = [], edges = [], roots = [];
  function card(sourceId, depth, disconnected, edge) {
    const existing = canonical.get(sourceId);
    const node = {...sources.get(sourceId),sourceId,id:`card-${nodes.length}`,depth,
      disconnected,relation:edge?.role,is_root:!edge && rootIds.has(sourceId),
      children:[],referenceTo:existing?.id,cycle:Boolean(existing&&active.has(sourceId))};
    nodes.push(node);
    if (!existing) canonical.set(sourceId,node);
    return node;
  }
  // Iterative DFS also handles very deep source trees without call-stack overflow.
  const rootOrder = [...graph.root_ids.map(key), ...visible.nodes.map(n=>key(n.id))];
  rootOrder.forEach(sourceId => {
    if (!sources.has(sourceId) || canonical.has(sourceId)) return;
    const root = card(sourceId,0,!reachable.has(sourceId)); roots.push(root);
    active.add(sourceId);
    const stack = [{node:root,index:0}];
    while (stack.length) {
      const frame = stack[stack.length-1], outgoing = adjacency.get(frame.node.sourceId)||[];
      if (frame.index===outgoing.length) {active.delete(frame.node.sourceId);stack.pop();continue;}
      const edge = outgoing[frame.index++];
      const child = card(key(edge.parent_id),frame.node.depth+1,frame.node.disconnected,edge);
      frame.node.children.push(child);
      edges.push({...edge,from:frame.node,to:child});
      if (!child.referenceTo) {active.add(child.sourceId);stack.push({node:child,index:0});}
    }
  });
  // Each subtree owns a disjoint vertical interval. Orthogonal routes stay in
  // its own inter-column gutter; only siblings from one source share a trunk.
  const gap = 26, column = NODE_WIDTH+100;
  for(let i=nodes.length-1;i>=0;i--) {
    const node = nodes[i];
    node.span = node.children.length ? node.children.reduce((sum,n)=>sum+n.span,0)+gap*(node.children.length-1) : NODE_HEIGHT;
  }
  let top = 64;
  roots.forEach(root => {
    root.top = top; top += root.span+80;
    const pending = [root];
    while(pending.length) {
      const node = pending.pop(); node.x = 40+node.depth*column;
      // Top-align each branch: even a large all-expanded tree starts with a
      // visible root-to-mother path, rather than a root floating in empty space.
      node.y = node.top;
      let childTop = node.top;
      node.children.forEach(child => {child.top=childTop;childTop+=child.span+gap;pending.push(child);});
    }
  });
  edges.forEach(edge => {
    const sx=edge.from.x+NODE_WIDTH,sy=edge.from.y+NODE_HEIGHT/2,tx=edge.to.x,ty=edge.to.y+NODE_HEIGHT/2;
    edge.points=[{x:sx,y:sy},{x:sx+50,y:sy},{x:sx+50,y:ty},{x:tx,y:ty}];
  });
  const labels = roots.map(root=>({x:root.x,y:root.top-22,label:root.disconnected?"未连接来源节点（不认定为当前品种祖先）":"当前品种 → 亲本 → 更早祖先"}));
  return {nodes,edges,labels,width:Math.max(380,...nodes.map(n=>n.x+NODE_WIDTH+40)),height:Math.max(260,top),
    referenceCount:nodes.filter(n=>n.referenceTo).length};
}

export function nodeNameLines(value) {
  const characters = Array.from(String(value||""));
  const lines = [characters.slice(0,16).join(""),characters.slice(16,32).join("")].filter(Boolean);
  if(characters.length>32) lines[1]+="…";
  return lines;
}

export function fanSectorPath(cx, cy, inner, outer, start, end) {
  const polar=(radius,angle)=>({x:cx+radius*Math.cos(angle),y:cy+radius*Math.sin(angle)});
  const a=polar(outer,start),b=polar(outer,end),c=polar(inner,end),d=polar(inner,start);
  return `M${a.x},${a.y} A${outer},${outer} 0 ${end-start>Math.PI?1:0} 1 ${b.x},${b.y} L${c.x},${c.y} A${inner},${inner} 0 ${end-start>Math.PI?1:0} 0 ${d.x},${d.y} Z`;
}

export function layoutFanPedigree(graph, visible) {
  // Reuse source-ID based bounded unfolding: references end locally, never
  // connect across sectors. Unknown roles and source text are retained as-is.
  const forest=layoutPedigree(graph,visible);
  const roots=forest.nodes.filter(n=>n.depth===0);
  for(let i=forest.nodes.length-1;i>=0;i--){
    const node=forest.nodes[i];node.leaves=node.children.length?node.children.reduce((sum,child)=>sum+child.leaves,0):1;
  }
  let top=46,width=420;const labels=[];
  roots.forEach(root=>{
    const members=[];const pending=[root];
    while(pending.length){const node=pending.pop();members.push(node);pending.push(...node.children);}
    const depth=Math.max(...members.map(n=>n.depth)),radius=58+depth*104;
    const cx=radius+36,cy=top+radius;
    width=Math.max(width,cx+radius+36);
    root.start=-Math.PI;root.end=0;root.lineage='root';
    members.forEach(node=>{
      node.cx=cx;node.cy=cy;node.inner=node.depth?58+(node.depth-1)*104:0;
      node.outer=node.depth?58+node.depth*104-4:50;
      const angle=(node.start+node.end)/2,r=(node.inner+node.outer)/2;
      node.angle=angle;node.x=node.depth?cx+r*Math.cos(angle):cx;node.y=node.depth?cy+r*Math.sin(angle):cy;
      node.path=node.depth?fanSectorPath(cx,cy,node.inner,node.outer,node.start,node.end):null;
      let cursor=node.start;
      node.children.forEach(child=>{
        // Each recorded parent gets an equal branch, even when its ancestors
        // or linked variety dossier are missing. Leaf-count weighting made
        // named terminal parents almost invisible beside a deep sibling.
        child.start=cursor;child.end=cursor+(node.end-node.start)/node.children.length;cursor=child.end;
        child.lineage=node.depth===0?(child.relation||'unknown'):node.lineage;
      });
    });
    labels.push({x:36,y:top-18,label:root.disconnected?'未连接来源节点 · 不认定为当前品种祖先':'扇形祖先谱 · 从内向外追溯亲本来源'});
    top=cy+104;
  });
  // Rectangular-tree routes are deliberately NOT rendered in a fan. Each
  // original edge is represented by its nested child sector and original ID.
  forest.edges.forEach(edge=>{edge.to.edgeId=edge.id;delete edge.points;});
  return {...forest,labels,width,height:Math.max(270,top+24),fan:true};
}

export function catalogCircleLayout(catalog, group, records) {
  const category=(catalog?.groups||[]).find(g=>g.code===group);
  const items=category?records:(catalog?.groups||[]);
  const radius=category?Math.max(205,items.length*110/(2*Math.PI)):180;
  const cx=radius+110,cy=radius+110;
  const center={id:'catalog',name:category?.name||'国家水稻品种库',subtitle:`${Number(category?.count??catalog?.total??0).toLocaleString()} 个品种`,x:cx,y:cy,radius:56,circle:true,kind:'catalog',is_root:true};
  const nodes=[center],edges=[];
  items.forEach((item,index)=>{
    const angle=-Math.PI/2+index*2*Math.PI/items.length;
    const node={...item,id:category?`variety-${item.id}`:`group-${item.code}`,name:item.name,
      variety_id:category?item.id:undefined,group:category?undefined:item.code,kind:category?'catalog-variety':'catalog',circle:true,
      subtitle:category?(item.edge_count?'已有系谱':'未收录亲本关系'):`${Number(item.count).toLocaleString()} 个品种`,
      x:cx+radius*Math.cos(angle),y:cy+radius*Math.sin(angle),radius:category?28:42};
    nodes.push(node);edges.push({id:`contains-${node.id}`,from:center,to:node,catalog:true});
  });
  return {nodes,edges,width:2*cx,height:2*cy+30,labels:[],fan:false};
}

export function fanLabel(node, scale) {
  // Fit names to actual on-screen sector space. Dense all-tree overview never
  // overlays names; the full name remains available in title and node details.
  let font=Math.max(12,13/scale);
  let screenFont=font*scale;
  let capacity=node.depth?Math.floor((node.inner+(node.outer-node.inner)*.35)*(node.end-node.start)*scale/screenFont)-1:node.outer*2*scale/screenFont-.3;
  const value=Array.from(node.name||'');
  const glyphWidth=character=>/[\u0000-\u007f]/.test(character)?.6:1;
  const textWidth=value.reduce((sum,character)=>sum+glyphWidth(character),0);
  let rows=Math.min(2,Math.floor((node.depth?node.outer-node.inner:node.outer*2)*scale/(screenFont*1.3)));
  let radial=false;
  // A long label can fit along the ring thickness even if it cannot fit across
  // the arc. Reserve the outer area for the expansion control.
  if(node.depth&&(capacity<2||rows<1||textWidth>capacity*rows||(capacity<5&&textWidth>capacity))){
    radial=true;
    capacity=Math.floor((node.outer-node.inner-30)*scale/screenFont);
    rows=Math.min(2,Math.floor(((node.inner+node.outer)/2)*(node.end-node.start)*scale/(screenFont*1.3)));
    if(capacity*rows<textWidth){
      font=Math.max(10,10/scale);screenFont=font*scale;
      capacity=Math.floor((node.outer-node.inner-30)*scale/screenFont);
      rows=Math.min(2,Math.floor(((node.inner+node.outer)/2)*(node.end-node.start)*scale/(screenFont*1.3)));
    }
  }
  if(capacity<2||rows<1)return {lines:[],font,radial};
  const limit=Math.min(18,capacity);
  const lines=[];let offset=0;
  for(let row=0;row<rows&&offset<value.length;row++){
    let line='',width=0;
    while(offset<value.length&&width+glyphWidth(value[offset])<=limit){
      const character=value[offset++];line+=character;width+=glyphWidth(character);
    }
    if(line)lines.push(line);
  }
  if(offset<value.length&&lines.length)lines[lines.length-1]=lines[lines.length-1].slice(0,-1)+'…';
  return {lines,font,radial,truncated:offset<value.length};
}

export function fitView(layout, viewport) {
  const scale = Math.min(1.15, Math.max(.08, Math.min((viewport.width-48)/layout.width,(viewport.height-48)/layout.height)));
  return {scale,x:(viewport.width-layout.width*scale)/2,y:(viewport.height-layout.height*scale)/2};
}

export function zoomView(view, factor, point) {
  const scale = Math.max(.08, Math.min(3, view.scale*factor));
  return {scale,x:point.x-(point.x-view.x)*scale/view.scale,y:point.y-(point.y-view.y)*scale/view.scale};
}
