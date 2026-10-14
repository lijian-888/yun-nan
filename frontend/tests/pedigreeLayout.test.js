import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { visiblePedigree, layoutPedigree, layoutFanPedigree, catalogCircleLayout, fanLabel, fanSectorPath, expandLevels, fitView, zoomView, NODE_WIDTH, NODE_HEIGHT, nodeNameLines } from "../src/pedigreeLayout.js";

const graph = { root_ids:[1], nodes:[1,2,3,4,5].map(id=>({id,name:id===2||id===3?"同名亲本":String(id)})),
  edges:[{id:1,child_id:1,parent_id:2},{id:2,child_id:1,parent_id:3},{id:3,child_id:2,parent_id:4},{id:4,child_id:3,parent_id:4}] };
test("default starts with root, expanding retains same-named distinct identities",()=>{
  assert.equal(visiblePedigree(graph).nodes.length,1);
  assert.deepEqual(visiblePedigree(graph,new Set(['1'])).nodes.map(n=>n.id),[1,2,3]);
});
test("one click all keeps every saved node, including disconnected nodes",()=>{
  const visible=visiblePedigree(graph,new Set(),true);
  assert.equal(visible.nodes.length,5); assert.equal(visible.edges.length,4);
  assert.equal(layoutPedigree(graph,visible).nodes.find(n=>n.sourceId==='5').disconnected,true);
});
test("shared ancestor survives collapsing one of its visible branches",()=>{
  assert.deepEqual(visiblePedigree(graph,new Set(['1','3'])).nodes.map(n=>n.id),[1,2,3,4]);
});
test("cycles terminate and all mode does not drop back edges",()=>{
  const cyclic={...graph,edges:[...graph.edges,{id:5,child_id:4,parent_id:1}]};
  assert.equal(visiblePedigree(cyclic,new Set(['1','2','3','4']),true).edges.length,5);
  const layout=layoutPedigree(cyclic,visiblePedigree(cyclic,new Set(),true));
  assert.ok(layout.nodes.every(n=>Number.isFinite(n.x)&&Number.isFinite(n.y)));
});
test("level expansion is based on directed graph, not F-generation claims",()=>{
  assert.deepEqual([...expandLevels(graph,1)],['1']);
  assert.equal(expandLevels(graph,3).has('4'),true);
});
test("fit and zoom remain bounded and keep anchor fixed",()=>{
  const view=fitView({width:3000,height:4000},{width:1000,height:600});
  assert.ok(view.scale>=.08&&view.scale<=1.15);
  const next=zoomView({scale:1,x:0,y:0},2,{x:100,y:100});
  assert.deepEqual(next,{scale:2,x:-100,y:-100});
});

function verifyTree(source) {
  const layout=layoutPedigree(source,visiblePedigree(source,new Set(),true));
  assert.equal(layout.edges.length,source.edges.length,"every original relation must remain");
  assert.deepEqual(layout.edges.map(e=>e.id).sort(),source.edges.map(e=>e.id).sort());
  assert.equal(layout.nodes.filter(n=>!n.referenceTo).length,source.nodes.length);
  assert.ok(layout.nodes.length<=source.nodes.length+source.edges.length,"bounded unfolding");
  for(const n of layout.nodes){
    assert.ok(Number.isFinite(n.x)&&Number.isFinite(n.y));
    if(n.referenceTo){const target=layout.nodes.find(t=>t.id===n.referenceTo);assert.equal(target.sourceId,n.sourceId);assert.ok(!target.referenceTo);assert.equal(n.children.length,0);}
  }
  for(let i=0;i<layout.nodes.length;i++)for(let j=i+1;j<layout.nodes.length;j++){
    const a=layout.nodes[i],b=layout.nodes[j];
    assert.ok(Math.abs(a.x-b.x)>=NODE_WIDTH||Math.abs(a.y-b.y)>=NODE_HEIGHT,"cards never overlap");
  }
  const segments=layout.edges.flatMap(edge=>edge.points.slice(1).map((point,index)=>({edge,a:edge.points[index],b:point})))
    .filter(s=>s.a.x!==s.b.x||s.a.y!==s.b.y);
  for(let i=0;i<segments.length;i++)for(let j=i+1;j<segments.length;j++){
    const a=segments[i],b=segments[j];
    if(a.edge.from.id===b.edge.from.id)continue; // One branch's own shared trunk is a junction, not a crossing.
    const ax=[Math.min(a.a.x,a.b.x),Math.max(a.a.x,a.b.x)],ay=[Math.min(a.a.y,a.b.y),Math.max(a.a.y,a.b.y)];
    const bx=[Math.min(b.a.x,b.b.x),Math.max(b.a.x,b.b.x)],by=[Math.min(b.a.y,b.b.y),Math.max(b.a.y,b.b.y)];
    assert.ok(Math.max(ax[0],bx[0])>Math.min(ax[1],bx[1])||Math.max(ay[0],by[0])>Math.min(ay[1],by[1]),`unrelated edges ${a.edge.id}/${b.edge.id} cross or overlap`);
  }
  // No line passes through an unrelated card, even with long or cyclic branches.
  for(const s of segments)for(const n of layout.nodes){
    if(n.id===s.edge.from.id||n.id===s.edge.to.id)continue;
    const x0=Math.min(s.a.x,s.b.x),x1=Math.max(s.a.x,s.b.x),y0=Math.min(s.a.y,s.b.y),y1=Math.max(s.a.y,s.b.y);
    assert.ok(x1<=n.x||x0>=n.x+NODE_WIDTH||y1<=n.y||y0>=n.y+NODE_HEIGHT,"line enters unrelated card");
  }
  return layout;
}
test("shared ancestors are terminal references; identity is never merged by name",()=>{
  const layout=verifyTree(graph);
  assert.equal(layout.referenceCount,1);
  assert.equal(layout.nodes.filter(n=>n.name==='同名亲本').length,2);
});
test("mother before father, source order stable within unspecified relations",()=>{
  const source={root_ids:[1],nodes:[1,2,3,4].map(id=>({id})),edges:[{id:1,child_id:1,parent_id:2,role:'male'},{id:2,child_id:1,parent_id:3,role:'female'},{id:3,child_id:1,parent_id:4,role:'unknown'}]};
  const layout=verifyTree(source),get=id=>layout.nodes.find(n=>n.sourceId===String(id));
  assert.ok(get(3).y<get(2).y&&get(2).y<get(4).y);
});
test("cycles and disconnected components remain visible without crossing remote links",()=>{
  const source={...graph,edges:[...graph.edges,{id:5,child_id:4,parent_id:1},{id:6,child_id:5,parent_id:4}]};
  const layout=verifyTree(source);
  assert.equal(layout.nodes.filter(n=>n.cycle).length,1);
});
test("collapsing one branch rehomes the shared ancestor and its ancestry",()=>{
  const source={...graph,nodes:[...graph.nodes,{id:6}],edges:[...graph.edges,{id:7,child_id:4,parent_id:6}]};
  const visible=visiblePedigree(source,new Set(['1','3','4']));
  const layout=layoutPedigree(source,visible);
  const shared=layout.nodes.filter(n=>n.sourceId==='4');
  assert.equal(shared.length,1);assert.ok(!shared[0].referenceTo);
  assert.equal(shared[0].children[0].sourceId,'6');
  assert.deepEqual(layout.edges.map(e=>e.id).sort(),visible.edges.map(e=>e.id).sort());
});
test("inverted ID order and diamond chains do not create exponential layouts",()=>{
  const source={root_ids:[0],nodes:[{id:0}],edges:[]};let end=0;
  for(let i=1;i<=25;i++){
    const a=i*3,b=a+1,c=a+2;source.nodes.push({id:a},{id:b},{id:c});
    source.edges.push({id:source.edges.length,child_id:end,parent_id:b},{id:source.edges.length+1,child_id:end,parent_id:a},{id:source.edges.length+2,child_id:a,parent_id:c},{id:source.edges.length+3,child_id:b,parent_id:c});end=c;
  }
  verifyTree(source);
});
test("long names wrap; full source name stays untouched",()=>{
  const name='田两优9号（田两优桂99）';assert.equal(nodeNameLines(name).join(''),name);
  assert.ok(nodeNameLines('祖'.repeat(80))[1].endsWith('…'));
});
test("real public server snapshots: all relations retained without crossings",{skip:!process.env.PEDIGREE_PREVIEW_FIXTURE},()=>{
  const fixture=JSON.parse(readFileSync(process.env.PEDIGREE_PREVIEW_FIXTURE,'utf8'));
  for(const snapshot of Object.values(fixture.graphs))verifyTree(snapshot);
});

function verifyFan(source){
  const layout=layoutFanPedigree(source,visiblePedigree(source,new Set(),true));
  assert.equal(layout.nodes.filter(n=>!n.referenceTo).length,source.nodes.length);
  assert.deepEqual(layout.edges.map(e=>e.id).sort(),source.edges.map(e=>e.id).sort());
  assert.ok(layout.nodes.length<=source.nodes.length+source.edges.length);
  for(const edge of layout.edges){
    const child=edge.to,parent=edge.from;
    assert.equal(child.edgeId,edge.id);assert.equal(child.depth,parent.depth+1);
    assert.ok(child.start>=parent.start-1e-9&&child.end<=parent.end+1e-9,'child sector stays in its source branch');
  }
  for(const node of layout.nodes){
    assert.ok(Number.isFinite(node.x)&&Number.isFinite(node.y));
    assert.ok(node.end>node.start);assert.ok(node.inner<node.outer);
    assert.ok(node.x>=0&&node.x<=layout.width&&node.y>=0&&node.y<=layout.height);
    assert.ok(!node.path||!node.path.includes('NaN'));
    for(let i=1;i<node.children.length;i++)assert.ok(node.children[i-1].end<=node.children[i].start+1e-9,'siblings do not overlap');
    if(node.referenceTo){assert.equal(node.children.length,0);assert.equal(layout.nodes.find(n=>n.id===node.referenceTo).sourceId,node.sourceId);}
    for(const scale of [.08,.25,1,3])assert.ok(fanLabel(node,scale).lines.length<=2);
  }
  return layout;
}
test('fan preserves source DAG, cycles and disconnected components without crossing links',()=>{
  const layout=verifyFan({...graph,edges:[...graph.edges,{id:8,child_id:4,parent_id:1},{id:9,child_id:5,parent_id:4}]});
  assert.equal(layout.nodes.filter(n=>n.cycle).length,1);
  assert.equal(layout.nodes.find(n=>n.sourceId==='5').disconnected,true);
});
test('fan supports more than two sources without assigning invented parental roles',()=>{
  const source={root_ids:[1],nodes:[1,2,3,4].map(id=>({id,name:String(id)})),edges:[2,3,4].map(id=>({id,child_id:1,parent_id:id,role:'unknown'}))};
  const layout=verifyFan(source);assert.equal(layout.nodes[0].children.length,3);
  assert.ok(layout.nodes.slice(1).every(n=>n.lineage==='unknown'));
});
test('circle catalogue has exactly four categories and only current page records',()=>{
  const catalog={total:20,groups:['indica','japonica','mixed','other'].map((code,i)=>({code,name:code,count:i+1}))};
  const overview=catalogCircleLayout(catalog,'',[]);assert.equal(overview.nodes.length,5);assert.equal(overview.edges.length,4);
  const page=catalogCircleLayout(catalog,'indica',[{id:41,name:'A'},{id:42,name:'B'}]);
  assert.equal(page.nodes.length,3);assert.deepEqual(page.nodes.slice(1).map(n=>n.variety_id),[41,42]);
  assert.ok(page.nodes.every(n=>n.circle));
});
test('named terminal parent keeps equal branch space beside a deep ancestry',()=>{
  const source={root_ids:[1],nodes:[1,2,3,4,5,6].map(id=>({id,name:id===3?'98P95':String(id)})),
    edges:[{id:1,child_id:1,parent_id:2},{id:2,child_id:1,parent_id:3},
      {id:3,child_id:2,parent_id:4},{id:4,child_id:2,parent_id:5},{id:5,child_id:4,parent_id:6}]};
  const layout=verifyFan(source),terminal=layout.nodes.find(n=>n.sourceId==='3');
  assert.ok(Math.abs(terminal.end-terminal.start-Math.PI/2)<1e-9);
  assert.equal(fanLabel(terminal,1).lines.join(''),'98P95');
  assert.equal(fanLabel({...terminal,name:'IR8'},1).lines.join(''),'IR8');
});
test('fan paths and dense labels stay finite; original full names are not changed',()=>{
  assert.ok(!fanSectorPath(200,200,50,100,-Math.PI,0).includes('NaN'));
  const layout=verifyFan(graph);const source=layout.nodes[0];source.name='祖先'.repeat(50);
  assert.ok(fanLabel(source,.08).lines.length===0);assert.equal(source.name.length,100);
});
test('long known name switches to radial text instead of a blank sector',()=>{
  const node={name:'IR8/科情3号',depth:4,inner:370,outer:470,start:-2.2,end:-2.1};
  const label=fanLabel(node,1);
  assert.equal(label.radial,true);
  assert.equal(label.lines.join(''),node.name);
  assert.equal(label.truncated,false);
  const overview=fanLabel({...node,start:-1.9634954084936207,end:-1.7671458676442586},.63);
  assert.equal(overview.lines.join(''),node.name);
  assert.equal(overview.truncated,false);
});
test('real public server snapshots: complete fans keep every saved source relation',{skip:!process.env.PEDIGREE_PREVIEW_FIXTURE},()=>{
  const fixture=JSON.parse(readFileSync(process.env.PEDIGREE_PREVIEW_FIXTURE,'utf8'));
  for(const snapshot of Object.values(fixture.graphs))verifyFan(snapshot);
});
