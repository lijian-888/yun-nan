"""Publish maximum observed direct fragments under explicit partial-source policy."""
import os,sys,json,hashlib,time
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from sqlalchemy import create_engine,text
from app.pedigree_direct import fragment,semantic_signature
from collect_ricedata_direct import store
from refresh_ricedata_pedigree import parse_source,repeated_action_error,SourceExpansionAmbiguity
from rebuild_ricedata_pedigrees import write_json

def main():
 root=Path('/data/research/pedigree-direct')
 audit=root/'recovery-audit-20261016'
 evidence=json.loads((audit/'audit.json').read_text())
 engine=create_engine(os.environ['MIGRATION_DATABASE_URL'])
 results=[]
 for item in evidence['jobs']:
  sid=item['source_id'];folder=root/sid
  state=json.loads((folder/'checkpoint.json').read_text())
  records,pending,_=parse_source(state['html'],state['url'])
  assert records[0]['source_id']==sid
  assert any(isinstance(repeated_action_error(state['html'],a),SourceExpansionAmbiguity) for a in pending if a in state['expanded'])
  value=fragment(records,allow_source_limited=True)
  assert value and any(n.get('source_limited') for n in value['nodes'])
  digest=hashlib.sha256(state['html'].encode()).hexdigest()
  with engine.begin() as c:
   existing=c.execute(text('select signature,status from ricedata.rice_pedigree_direct_fact where fact_key=:key'),{'key':value['root']}).mappings().first()
   if existing and (existing['signature']!=semantic_signature(value) or existing['status']!='verified_partial'):
    raise ValueError('Existing source evidence differs; refusing to replace '+sid)
   store(c,value,state['url'],digest,status='verified_partial')
   c.execute(text("UPDATE ricedata.rice_pedigree_direct_job SET root_key=:key,status='source_limited',error='SourceExpansionAmbiguity: maximum observed expansion retained by user-approved policy',collected_at=now() WHERE source_id=:id AND status IN ('needs_review','source_limited')"),{'key':value['root'],'id':sid})
  (folder/'source-limited.html').write_text(state['html'],encoding='utf-8')
  result={'source_id':sid,'name':records[0]['name'],'observed_occurrences':len(records),'direct_nodes':len(value['nodes']),'source_limited_names':[n['name'] for n in value['nodes'] if n.get('source_limited')],'url':state['url'],'sha256':digest}
  results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
 write_json(audit/'retained-source-limited.json',{'checked_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'policy':'maximum observed expansion; no inferred ancestry','results':results})
 with engine.connect() as c:counts=dict(c.execute(text('select status,count(*) from ricedata.rice_pedigree_direct_job group by status')).all())
 write_json(root/'progress.json',{'mode':'direct_relations','counts':counts,'status':'direct_queue_finished','source_limited_policy':'maximum observed expansion retained; not complete trees','updated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
 print('retained',len(results),'counts',counts,flush=True)

if __name__=='__main__':main()
