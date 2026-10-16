"""Evidence audit for stalled source TreeView branches and explicit source aliases."""
import hashlib,json,os,re,time,sys
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from lxml import etree
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from refresh_ricedata_pedigree import parse_source,decode_response,occurrence_paths
from rebuild_ricedata_pedigrees import pace_requests
from app.pedigree_direct import load_graph,material_key
import requests

BASE=Path('/data/research/pedigree-direct')
OUT=BASE/'recovery-audit-20261016'
OUT.mkdir(exist_ok=True)
engine=create_engine(os.environ['MIGRATION_DATABASE_URL'])
report={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'jobs':[]}
with engine.connect() as c:
 ids=c.execute(text("select source_id from ricedata.rice_pedigree_direct_job where status='needs_review' order by source_id")).scalars().all()
for sid in ids:
 state=json.loads((BASE/sid/'checkpoint.json').read_text())
 records,pending,fields=parse_source(state['html'],state['url'])
 doc=etree.HTML(state['html']);toggles=[]
 for table in doc.xpath('//*[@id="pedigree"]/table'):
  cells=table.xpath('./tr/td')
  if len(cells)<2:continue
  a=cells[-2].xpath('./a')
  if not a:continue
  match=re.search(r"__doPostBack\('pedigree','([^']+)'\)",a[0].get('href',''))
  if match:toggles.append({'action':match[1].replace('\\\\','\\'),'label':''.join(cells[-1].itertext()).strip(),'alt':a[0].xpath('string(./img/@alt)')})
 collisions=[]
 for action in pending:
  matches=[x for x in toggles if x['action']==action]
  if len(matches)>1 and action in state['expanded']:collisions.append(matches)
 item={'source_id':sid,'url':state['url'],'html_sha256':hashlib.sha256(state['html'].encode()).hexdigest(),'collisions':collisions}
 report['jobs'].append(item)
print('OFFLINE',len(ids),'failed tasks;',sum(bool(i['collisions']) for i in report['jobs']),'with duplicate source expansion actions',flush=True)
# Fresh, independently fetched HTML: verify representative ambiguous requests.
if '--live' in sys.argv:
 for sid in ids:
  state=json.loads((BASE/sid/'checkpoint.json').read_text())
  with requests.Session() as client:
   pace_requests(Path('/data/research/pedigree-rebuild'))
   html=decode_response(client.get(state['url'],timeout=30))
   trace=[]
   for action in state['expanded']:
    records,pending,fields=parse_source(html,state['url'])
    if action not in pending:continue
    fields.update(__EVENTTARGET='pedigree',__EVENTARGUMENT=action)
    pace_requests(Path('/data/research/pedigree-rebuild'))
    html=decode_response(client.post(state['url'],data=fields,timeout=30))
   before,actions,fields=parse_source(html,state['url'])
   action=next((a for a in actions if a in state['expanded']),None)
   if action:
    fields.update(__EVENTTARGET='pedigree',__EVENTARGUMENT=action)
    pace_requests(Path('/data/research/pedigree-rebuild'))
    after_html=decode_response(client.post(state['url'],data=fields,timeout=30))
    after,_,_=parse_source(after_html,state['url'])
    bp=occurrence_paths(before);ap=occurrence_paths(after)
    lost=[list(p) for p,n in bp.items() if ap.get(p,0)<n]
    result={'source_id':sid,'url':state['url'],'action':action,'lost_paths':lost,'before_count':len(before),'after_count':len(after),'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    (OUT/(sid+'-before.html')).write_text(html)
    (OUT/(sid+'-after.html')).write_text(after_html)
    trace.append(result);print('LIVE',json.dumps(result,ensure_ascii=False),flush=True)
   report.setdefault('live',[]).extend(trace)
with Session(engine) as s:
 graph=load_graph(s,'631806')
 report['target_missing']=[{'name':n['name'],'source_id':n['source_id'],'key':n['key']} for n in graph['nodes'] if not n['expansion_succeeded']]
(OUT/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('REPORT',OUT/'audit.json',flush=True)
