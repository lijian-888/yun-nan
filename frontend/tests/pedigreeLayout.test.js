import { test } from "node:test";
import assert from "node:assert/strict";
import { visiblePedigree, layoutPedigree, expandLevels, fitView, zoomView } from "../src/pedigreeLayout.js";

const graph = { root_ids:[1], nodes:[1,2,3,4,5].map(id=>({id,name:id===2||id===3?"同名亲本":String(id)})),
  edges:[{id:1,child_id:1,parent_id:2},{id:2,child_id:1,parent_id:3},{id:3,child_id:2,parent_id:4},{id:4,child_id:3,parent_id:4}] };
test("default starts with root, expanding retains same-named distinct identities",()=>{
  assert.equal(visiblePedigree(graph).nodes.length,1);
  assert.deepEqual(visiblePedigree(graph,new Set(['1'])).nodes.map(n=>n.id),[1,2,3]);
});
test("one click all keeps every saved node, including disconnected nodes",()=>{
  const visible=visiblePedigree(graph,new Set(),true);
  assert.equal(visible.nodes.length,5); assert.equal(visible.edges.length,4);
  assert.equal(layoutPedigree(graph,visible).nodes.find(n=>n.id===5).disconnected,true);
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
