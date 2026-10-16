"""Collect each material's proven direct fragment once; keep full-tree evidence for audit."""
import argparse,fcntl,hashlib,json,os,time
from pathlib import Path
from sqlalchemy import create_engine,text
from app.pedigree_direct import fragment,semantic_signature
from refresh_ricedata_pedigree import collect,parse_source,CollectionDeferred,SourceExpansionAmbiguity
from rebuild_ricedata_pedigrees import pace_requests,write_json,load_json


def store(conn,value,url,digest):
    root=value['nodes'][0];signature=semantic_signature(value)
    evidence=json.dumps([dict(url=url,sha256=digest)])
    conn.execute(text('''INSERT INTO ricedata.rice_pedigree_direct_fact
        (fact_key,source_id,material_name,fragment,signature,evidence)
        VALUES (:key,:source,:name,CAST(:fragment AS jsonb),:signature,CAST(:evidence AS jsonb))
        ON CONFLICT(fact_key) DO UPDATE SET
        status=CASE WHEN rice_pedigree_direct_fact.signature=EXCLUDED.signature THEN rice_pedigree_direct_fact.status ELSE 'conflict' END,
        conflict_evidence=CASE WHEN rice_pedigree_direct_fact.signature=EXCLUDED.signature THEN rice_pedigree_direct_fact.conflict_evidence
          ELSE rice_pedigree_direct_fact.conflict_evidence || jsonb_build_array(jsonb_build_object('fragment',EXCLUDED.fragment,'evidence',EXCLUDED.evidence)) END
    '''),dict(key=value['root'],source=root['source_id'],name=root['name'],fragment=json.dumps(value,ensure_ascii=False),signature=signature,evidence=evidence))
    for node in value['nodes'][1:]:
        if node['source_id']:
            conn.execute(text('''INSERT INTO ricedata.rice_pedigree_direct_job(source_id,priority) VALUES (:id,0)
                ON CONFLICT(source_id) DO UPDATE SET priority=LEAST(rice_pedigree_direct_job.priority,0)'''),{'id':node['source_id']})


def prime(engine,original,state):
    if (state/'seed-complete.json').exists():return
    variants={};roots={}
    for path in list(original.glob('*/source.html'))+list(original.glob('*/checkpoint.json')):
        if path.name=='checkpoint.json':item=load_json(path)
        else:
            stored=load_json(path.with_name('nodes.json'))
            if not stored:continue
            url=next((n['url'] for n in stored if not n['source_id']),f"https://www.ricedata.cn/variety/pedigree.aspx?id={stored[0]['source_id']}&fromid=")
            item=dict(html=path.read_text(encoding='utf-8'),url=url)
        records,_,_=parse_source(item['html'],item['url'])
        digest=hashlib.sha256(item['html'].encode()).hexdigest()
        for index in range(len(records)):
            value=fragment(records,index)
            if value:
                key=(value['root'],semantic_signature(value));variants.setdefault(key,(value,item['url'],digest))
                if index==0:roots[records[0]['source_id']]=value['root']
    with engine.begin() as conn:
        for value,url,digest in variants.values():store(conn,value,url,digest)
        for source,key in roots.items():
            conn.execute(text('''UPDATE ricedata.rice_pedigree_direct_job SET root_key=:key,status='complete',collected_at=now()
                WHERE source_id=:id'''),dict(key=key,id=source))
    write_json(state/'seed-complete.json',dict(fragments=len(variants),root_materials=len(roots),created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    print('seeded',len(variants),'direct source fragments',flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--original-state',type=Path,required=True);args=parser.parse_args()
    args.state_dir.mkdir(parents=True,exist_ok=True)
    with (args.state_dir/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run(args)


def run(args):
    engine=create_engine(os.environ['MIGRATION_DATABASE_URL'],pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text('''INSERT INTO ricedata.rice_pedigree_direct_job(source_id)
            SELECT DISTINCT source_variety_id FROM ricedata.rice_variety WHERE source_variety_id ~ '^[1-9][0-9]*$'
            ON CONFLICT DO NOTHING'''))
    prime(engine,args.original_state,args.state_dir)
    current=None;attempted=0
    def report(branch=None,status='running'):
        with engine.connect() as c:
            counts=dict(c.execute(text('SELECT status,count(*) FROM ricedata.rice_pedigree_direct_job GROUP BY status')).all())
            conflicts=c.scalar(text("SELECT count(*) FROM ricedata.rice_pedigree_direct_fact WHERE status='conflict'"))
        write_json(args.state_dir/'progress.json',dict(mode='direct_relations',current=current,branch=branch,attempted=attempted,
            counts=counts,conflicts=conflicts,status=status,updated_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))
    while True:
        if (args.original_state/'source-blocked.json').exists():report(status='source_access_blocked');return
        with engine.connect() as c:
            job=c.execute(text("SELECT source_id FROM ricedata.rice_pedigree_direct_job WHERE status='pending' ORDER BY CASE WHEN source_id='631806' THEN -1 ELSE priority END,source_id LIMIT 1")).mappings().first()
        if not job:
            report(status='direct_queue_finished');return
        current=dict(job);sid=job['source_id'];folder=args.state_dir/sid;folder.mkdir(exist_ok=True);checkpoint=folder/'checkpoint.json'
        report()
        def checkpointed(state,metric):write_json(checkpoint,state);report(metric)
        try:
            url,records,html=collect(sid,direct_only=True,resume=load_json(checkpoint),on_progress=checkpointed,
                                    slice_seconds=120,slice_expansions=100,before_request=lambda:pace_requests(args.original_state))
            if str(records[0]['source_id'])!=sid:raise ValueError('Root source identity mismatch')
            value=fragment(records)
            if value is None:raise ValueError('Direct fragment still contains unnamed pending branches')
            (folder/'source.html').write_text(html,encoding='utf-8');write_json(folder/'nodes.json',records)
            with engine.begin() as c:
                store(c,value,url,hashlib.sha256(html.encode()).hexdigest())
                c.execute(text("UPDATE ricedata.rice_pedigree_direct_job SET root_key=:key,status='complete',error=NULL,collected_at=now() WHERE source_id=:id"),dict(key=value['root'],id=sid))
            print(json.dumps(dict(source_id=sid,name=records[0]['name'],status='direct_collected',nodes=len(value['nodes']),relations=len(value['edges'])),ensure_ascii=False),flush=True)
        except CollectionDeferred:
            with engine.begin() as c:c.execute(text("UPDATE ricedata.rice_pedigree_direct_job SET priority=100 WHERE source_id=:id"),{'id':sid})
            print(json.dumps(dict(source_id=sid,status='direct_fragment_deferred')),flush=True)
        except SourceExpansionAmbiguity as exc:
            # A deterministic source UI collision is not a transient request error.
            with engine.begin() as c:
                c.execute(text("UPDATE ricedata.rice_pedigree_direct_job SET error=:error,status='needs_review' WHERE source_id=:id"),dict(id=sid,error=type(exc).__name__+': '+str(exc)))
            print(json.dumps(dict(source_id=sid,status='source_action_collision',error=str(exc)),ensure_ascii=False),flush=True)
        except Exception as exc:
            with engine.begin() as c:c.execute(text("UPDATE ricedata.rice_pedigree_direct_job SET attempts=attempts+1,error=:error,priority=100,status=CASE WHEN attempts>=2 THEN 'needs_review' ELSE 'pending' END WHERE source_id=:id"),dict(id=sid,error=type(exc).__name__+': '+str(exc)))
            if getattr(getattr(exc,'response',None),'status_code',None) in (403,429):
                write_json(args.original_state/'source-blocked.json',dict(source_id=sid,error=str(exc)));report(status='source_access_blocked');return
            print(json.dumps(dict(source_id=sid,status='failed',error=type(exc).__name__+': '+str(exc))),flush=True)
        attempted+=1;report()


if __name__=='__main__':main()
