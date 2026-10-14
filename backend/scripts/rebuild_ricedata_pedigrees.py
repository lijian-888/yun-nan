"""Two resumable lanes; source trees remain sequential and unpublished until complete."""
import argparse
import fcntl
import json
import os
import time
from pathlib import Path
from sqlalchemy import create_engine,text
from refresh_ricedata_pedigree import collect,save_snapshot,CollectionDeferred


def write_json(path,data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def load_json(path,default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def pace_requests(state_dir,interval=1.0):
    """Shared rate limit including retries; do not hold lock during HTTP."""
    if (state_dir/'source-blocked.json').exists():
        raise RuntimeError('Source access blocked; operator review required')
    with (state_dir/'request-rate.lock').open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        handle.seek(0)
        previous=float(handle.read() or '0')
        remaining=min(interval,max(0,interval-(time.monotonic()-previous)))
        if remaining:
            time.sleep(remaining)
        handle.seek(0)
        handle.truncate()
        handle.write(str(time.monotonic()))
        handle.flush()
        fcntl.flock(handle,fcntl.LOCK_UN)
    if (state_dir/'source-blocked.json').exists():
        raise RuntimeError('Source access blocked; operator review required')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--only',help='Comma-separated local variety IDs')
    parser.add_argument('--lane',choices=['normal','complex'],default='normal')
    parser.add_argument('--slice-seconds',type=float,default=60)
    parser.add_argument('--slice-expansions',type=int,default=40)
    args=parser.parse_args()
    if args.slice_seconds<=0 or args.slice_expansions<=0:
        parser.error('Slice limits must be positive')
    args.state_dir.mkdir(parents=True,exist_ok=True)
    deferred_dir=args.state_dir/'deferred'
    deferred_dir.mkdir(exist_ok=True)
    lock=(args.state_dir/f'worker-{args.lane}.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    engine=create_engine(os.environ['MIGRATION_DATABASE_URL'],pool_pre_ping=True)
    with engine.connect() as conn:
        varieties=[dict(r) for r in conn.execute(text('''
            SELECT variety_id,source_variety_id,variety_name FROM ricedata.rice_variety
            ORDER BY CASE variety_id WHEN 2502 THEN 0 ELSE 1 END,variety_id
        ''')).mappings()]
    if args.only:
        wanted={int(v) for v in args.only.split(',')}
        varieties=[v for v in varieties if v['variety_id'] in wanted]
    progress_path=args.state_dir/('progress.json' if args.lane=='normal' else 'complex-progress.json')
    attempted=0
    verified_cache={'at':0,'count':0}

    def done(vid):
        with engine.connect() as conn:
            return conn.scalar(text('SELECT EXISTS(SELECT 1 FROM ricedata.rice_pedigree_snapshot WHERE variety_id=:id AND source_verified)'),{'id':vid})

    def progress(current=None,status='running',branch=None):
        if time.monotonic()-verified_cache['at']>15 or status!='running':
            with engine.connect() as conn:
                verified_cache['count']=conn.scalar(text('SELECT count(DISTINCT variety_id) FROM ricedata.rice_pedigree_snapshot WHERE source_verified'))
            verified_cache['at']=time.monotonic()
        write_json(progress_path,dict(total=len(varieties),verified_global=verified_cache['count'],attempted=attempted,
            lane=args.lane,current=current,branch=branch,status=status,
            updated_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())))

    def process(variety):
        nonlocal attempted
        vid=variety['variety_id']
        marker=deferred_dir/f'{vid}.json'
        folder=args.state_dir/str(vid)
        folder.mkdir(exist_ok=True)
        with (folder/'collection.lock').open('a') as item_lock:
            try:
                fcntl.flock(item_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            if done(vid):
                if marker.exists():
                    write_json(marker,dict(variety=variety,status='verified'))
                return
            job=load_json(marker,{})
            if args.lane=='normal' and marker.exists():
                return
            if job.get('status')=='needs_review':
                return
            checkpoint=folder/'checkpoint.json'
            latest={}
            last_log=[0]
            progress(variety)

            def report(state,metric):
                latest.update(metric)
                write_json(checkpoint,state)
                progress(variety,branch=metric)
                if time.monotonic()-last_log[0]>=15 or metric['pending_branches']==0:
                    print(json.dumps(dict(id=vid,lane=args.lane,status='collecting',**metric),ensure_ascii=False),flush=True)
                    last_log[0]=time.monotonic()
            try:
                url,records,html=collect(variety['source_variety_id'],resume=load_json(checkpoint),
                    on_progress=report,slice_seconds=args.slice_seconds,slice_expansions=args.slice_expansions,
                    before_request=lambda:pace_requests(args.state_dir))
                (folder/'source.html').write_text(html,encoding='utf-8')
                write_json(folder/'nodes.json',records)
                with engine.begin() as conn:
                    sid=save_snapshot(conn,vid,url,records)
                write_json(marker,dict(variety=variety,status='verified',snapshot=sid))
                print(json.dumps(dict(id=vid,name=variety['variety_name'],status='verified',snapshot=sid,**latest),ensure_ascii=False),flush=True)
            except CollectionDeferred:
                write_json(marker,dict(variety=variety,status='deferred',errors=job.get('errors',0),branch=latest))
                print(json.dumps(dict(id=vid,status='deferred',lane=args.lane,**latest),ensure_ascii=False),flush=True)
            except Exception as exc:
                errors=job.get('errors',0)+1
                error=type(exc).__name__+': '+str(exc)
                write_json(marker,dict(variety=variety,status='needs_review' if errors>=3 else 'retry',errors=errors,error=error,branch=latest))
                print(json.dumps(dict(id=vid,status='failed',error=error,attempt=errors),ensure_ascii=False),flush=True)
                if getattr(getattr(exc,'response',None),'status_code',None) in (403,429):
                    write_json(args.state_dir/'source-blocked.json',dict(id=vid,error=error))
            attempted+=1
            progress()

    progress()
    if args.lane=='normal':
        for variety in varieties:
            if (args.state_dir/'source-blocked.json').exists():
                progress(status='source_access_blocked')
                return
            process(variety)
        progress(status='normal_queue_finished')
    else:
        while True:
            if (args.state_dir/'source-blocked.json').exists():
                progress(status='source_access_blocked')
                return
            jobs=[]
            for marker in deferred_dir.glob('*.json'):
                job=load_json(marker)
                if job['status'] in ('deferred','retry'):
                    jobs.append((marker.stat().st_mtime,job['variety']))
            if jobs:
                for _,variety in sorted(jobs,key=lambda entry:entry[0]):
                    if (args.state_dir/'source-blocked.json').exists():
                        break
                    process(variety)
            else:
                normal=load_json(args.state_dir/'progress.json',{})
                if normal.get('status')=='normal_queue_finished':
                    incomplete=any(not done(v['variety_id']) for v in varieties)
                    progress(status='needs_review' if incomplete else 'completed')
                    return
                progress(status='waiting_for_complex_jobs')
                time.sleep(10)


if __name__=='__main__':
    main()
