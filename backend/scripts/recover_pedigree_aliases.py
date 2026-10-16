"""Repair two evidence-backed aliases, never perform name-only matching."""
import sys,os,json,hashlib,copy,time
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from app.pedigree_direct import fragment,semantic_signature,material_key,load_graph
from refresh_ricedata_pedigree import collect
from rebuild_ricedata_pedigrees import pace_requests
from collect_ricedata_direct import store

engine=create_engine(os.environ['MIGRATION_DATABASE_URL'])
out=Path('/data/research/pedigree-direct/recovery-audit-20261016')
out.mkdir(exist_ok=True)
report=[]
for sid,alias,canonical in [('607900','YR293','YR293（Y恢293）'),('614041','七丝软占','七丝占2号（七丝软占）')]:
 with engine.connect() as c:
  own=c.execute(text('select f.* from ricedata.rice_pedigree_direct_job j join ricedata.rice_pedigree_direct_fact f on f.fact_key=j.root_key where j.source_id=:id'),{'id':sid}).mappings().one()
  assert own['material_name']==canonical and own['status']=='verified_direct'
  refs=c.execute(text("select fact_key,evidence from ricedata.rice_pedigree_direct_fact where status='verified_direct' and fragment->'nodes' @> CAST(:node AS jsonb)"),{'node':json.dumps([{'source_id':sid,'name':alias}])}).mappings().all()
  assert refs, 'No source-ID linked alias evidence'
 # Verify the canonical direct relationships again against current source.
 url,records,html=collect(sid,direct_only=True,before_request=lambda:pace_requests(Path('/data/research/pedigree-rebuild')))
 current=fragment(records)
 assert current and records[0]['name']==canonical
 assert semantic_signature(current)==own['signature'], 'Current source relationships changed; review required'
 (out/(sid+'-alias-source.html')).write_text(html)
 value=copy.deepcopy(current);old=value['root'];new=material_key(sid,alias)
 value['root']=new
 for n in value['nodes']:
  if n['key']==old:n['key']=new;n['name']=alias
 for edge in value['edges']:
  for k in ['child','parent']:
   if edge[k]==old:edge[k]=new
 value['references']=[new if k==old else k for k in value['references']]
 proof={'kind':'explicit_source_id_alias','source_id':sid,'original_name':alias,'canonical_name':canonical,'reference_facts':[r['fact_key'] for r in refs],'reference_evidence':[r['evidence'] for r in refs],'canonical_url':url,'canonical_sha256':hashlib.sha256(html.encode()).hexdigest(),'checked_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
 if '--apply' in sys.argv:
  with engine.begin() as c:
   store(c,value,url,proof['canonical_sha256'])
   c.execute(text("update ricedata.rice_pedigree_direct_fact set evidence=evidence || CAST(:proof AS jsonb) where fact_key=:key"),{'proof':json.dumps([proof],ensure_ascii=False),'key':new})
 report.append(proof)
 print('alias_verified',sid,alias,canonical,'applied', '--apply' in sys.argv,flush=True)
with Session(engine) as s:
 graph=load_graph(s,'631806')
 print('target',json.dumps({k:v for k,v in graph.items() if k not in ['nodes','edges']},default=str,ensure_ascii=False),flush=True)
 (out/'target-after-alias.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2,default=str))
(out/'alias-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
