"""Single-worker resumable full-site source-context rebuild; no legacy graph reuse."""
import argparse
import fcntl
import json
import os
import time
from pathlib import Path
from sqlalchemy import create_engine,text
from refresh_ricedata_pedigree import collect,save_snapshot


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--state-dir",type=Path,required=True)
    parser.add_argument("--only",help="Comma-separated local variety IDs")
    args=parser.parse_args()
    args.state_dir.mkdir(parents=True,exist_ok=True)
    lock=(args.state_dir/"worker.lock").open("a")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    engine=create_engine(os.environ["MIGRATION_DATABASE_URL"],pool_pre_ping=True)
    with engine.connect() as conn:
        varieties=[dict(r) for r in conn.execute(text("""
            SELECT variety_id,source_variety_id,variety_name FROM ricedata.rice_variety
            ORDER BY CASE variety_id WHEN 33791 THEN 0 WHEN 34195 THEN 1 WHEN 23885 THEN 2
              WHEN 32460 THEN 3 WHEN 41859 THEN 4 WHEN 2502 THEN 5 ELSE 6 END,variety_id
        """)).mappings()]
    if args.only:
        wanted={int(v) for v in args.only.split(",")}
        varieties=[v for v in varieties if v["variety_id"] in wanted]
    attempted,failed=0,{}
    def progress(current=None):
        with engine.connect() as conn:
            verified=conn.scalar(text("SELECT count(DISTINCT variety_id) FROM ricedata.rice_pedigree_snapshot WHERE source_verified"))
        data=dict(total=len(varieties),verified_global=verified,attempted=attempted,
                  failed=len(failed),current=current,updated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                  status="running",failures=failed)
        temporary=args.state_dir/"progress.tmp"
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(args.state_dir/"progress.json")
        return data
    for attempt in range(3):
        for variety in varieties:
            vid=variety["variety_id"]
            with engine.connect() as conn:
                done=conn.scalar(text("SELECT EXISTS(SELECT 1 FROM ricedata.rice_pedigree_snapshot WHERE variety_id=:id AND source_verified)"),{"id":vid})
            if done:
                failed.pop(str(vid),None)
                continue
            progress(variety)
            folder=args.state_dir/str(vid)
            folder.mkdir(exist_ok=True)
            try:
                url,records,html=collect(variety["source_variety_id"])
                (folder/"source.html").write_text(html,encoding="utf-8")
                (folder/"nodes.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")
                with engine.begin() as conn:
                    sid=save_snapshot(conn,vid,url,records)
                failed.pop(str(vid),None)
                print(json.dumps(dict(variety=variety["variety_name"],id=vid,snapshot=sid,status="verified",source_occurrences=len(records)),ensure_ascii=False),flush=True)
            except Exception as exc:
                failed[str(vid)]=dict(name=variety["variety_name"],attempt=attempt+1,error=type(exc).__name__+": "+str(exc))
                print(json.dumps(dict(id=vid,status="failed",attempt=attempt+1,error=failed[str(vid)]["error"]),ensure_ascii=False),flush=True)
                time.sleep(3)
            attempted+=1
            progress()
        if not failed:
            break
    data=progress()
    data["status"]="needs_review" if failed else "completed"
    (args.state_dir/"progress.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(data,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
