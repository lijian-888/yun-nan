import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pedigreeRecordSummary } from '../src/pedigreeSummary.js';

test('138B selected graph supersedes an older directory count',()=>{
  const record={id:15073,node_count:5,edge_count:4};
  const graph={variety:{id:15073},node_count:165,edge_count:188};
  assert.equal(pedigreeRecordSummary(record,graph),'165个来源节点');
});

test('directory uses assembled graph totals without counting references twice',()=>{
  assert.equal(pedigreeRecordSummary({id:15073,node_count:165,edge_count:188}),'165个来源节点');
});

test('a different selected variety cannot overwrite a directory entry',()=>{
  assert.equal(pedigreeRecordSummary({id:2,node_count:32,edge_count:34},
    {variety:{id:1},node_count:165}),'32个来源节点');
});

test('known root with no valid edges is not described as no parents at the source',()=>{
  assert.equal(pedigreeRecordSummary({node_count:1,edge_count:0}),'1个来源节点');
  assert.equal(pedigreeRecordSummary({node_count:0,edge_count:0}),'暂无已核验亲本关系');
});

test('unknown or unsafe totals are explicitly pending, not zero or fragment counts',()=>{
  assert.equal(pedigreeRecordSummary({node_count:null,count_status:'needs_review'}),'图谱节点数待核实');
  assert.equal(pedigreeRecordSummary({node_count:null,pedigree_status:'pending_verification'}),'系谱待原站校验');
});
