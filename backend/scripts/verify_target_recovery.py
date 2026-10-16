"""Independent full-source re-fetch and exact occurrence-path comparison."""
import sys,os,json,time,hashlib
from pathlib import Path
from collections import Counter,defaultdict
sys.path.insert(0,'/app/scripts')
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.pedigree_direct import load_graph
from refresh_ricedata_pedigree import collect,CollectionDeferred,occurrence_paths
from rebuild_ricedata_pedigrees import pace_requests,write_json,load_json
out=Path('/data/research/pedigree-direct/recovery-audit-20261016/independent-631806')
out.mkdir(exist_ok=True)
checkpoint=out/'checkpoint.json'
def progress(state,metric):
 write_json(checkpoint,state)
 write_json(out/'progress.json',dict(metric,updated_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
while True:
 try:
  url,records,html=collect('631806',resume=load_json(checkpoint),on_progress=progress,slice_seconds=55,slice_expansions=55,before_request=lambda:pace_requests(Path('/data/research/pedigree-rebuild')))
  break
 except CollectionDeferred:continue
 except Exception as exc:
  write_json(out/'result.json',{'status':'source_collection_incomplete','error':str(exc),'checked_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
  raise
(out/'source.html').write_text(html)
write_json(out/'nodes.json',records)
engine=create_engine(os.environ['MIGRATION_DATABASE_URL'])
with Session(engine) as s:graph=load_graph(s,'631806')
assert graph['fully_expanded']
names={n['id']:n['name'] for n in graph['nodes']}
adj=defaultdict(list)
for edge in graph['edges']:adj[edge['child_id']].append(edge['parent_id'])
actual=Counter();stack=[(graph['root_ids'][0],(),frozenset())]
expected=Counter(occurrence_paths(records))
while stack:
 node,path,seen=stack.pop()
 assert node not in seen,'cycle in graph'
 path=path+(names[node],);actual[path]+=1
 assert sum(actual.values())<=len(records)+10000,'extra occurrences'
 for parent in adj[node]:stack.append((parent,path,seen|{node}))
missing=expected-actual;extra=actual-expected
result={'status':'verified' if not missing and not extra else 'differences_found','url':url,'checked_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source_sha256':hashlib.sha256(html.encode()).hexdigest(),'source_occurrences':len(records),'graph_occurrences':sum(actual.values()),'missing_paths':[{'path':list(p),'count':c} for p,c in missing.items()],'extra_paths':[{'path':list(p),'count':c} for p,c in extra.items()]}
write_json(out/'result.json',result)
print(json.dumps(result,ensure_ascii=False),flush=True)
