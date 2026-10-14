"""Offline differential audit. Read evidence only; no network or database writes."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from refresh_ricedata_pedigree import parse_source,occurrence_paths,snapshot_plan,validate_plan


def audit(html,url,stored=None):
    timings={}
    results={}
    for mode in ('reference','direct'):
        clock=time.perf_counter()
        results[mode]=parse_source(html,url,mode=mode)
        timings[mode]=time.perf_counter()-clock
    old,new=results['reference'],results['direct']
    if old[0]!=new[0]:
        raise AssertionError('Node names, depth, parent indexes, IDs, URLs or expansion state differ')
    if old[1]!=new[1]:
        raise AssertionError('Ordered source expansion actions differ')
    if old[2]!=new[2]:
        raise AssertionError('POST fields differ; continuation is unsafe')
    if occurrence_paths(old[0])!=occurrence_paths(new[0]):
        raise AssertionError('Source path multiplicities differ')
    if stored is not None and new[0]!=stored:
        raise AssertionError('Parsed records differ from saved source records')
    canonical,edges=snapshot_plan(new[0])
    validate_plan(new[0],canonical,edges)
    return dict(nodes=len(new[0]),pending=len(new[1]),name=new[0][0]['name'],
                source_sha256=hashlib.sha256(html.encode()).hexdigest(),
                fields=len(new[2]),reference_seconds=round(timings['reference'],6),
                direct_seconds=round(timings['direct'],6),status='matched')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    results=[]
    for folder in sorted(args.state_dir.iterdir()):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        for kind in ('source','checkpoint'):
            path=folder/('source.html' if kind=='source' else 'checkpoint.json')
            if not path.exists():
                continue
            try:
                if kind=='checkpoint':
                    state=json.loads(path.read_text(encoding='utf-8'))
                    html,url=state['html'],state['url']
                    stored=None
                else:
                    html=path.read_text(encoding='utf-8')
                    stored=json.loads((folder/'nodes.json').read_text(encoding='utf-8'))
                    url=next((n['url'] for n in stored if not n['source_id']),
                             f"https://www.ricedata.cn/variety/pedigree.aspx?id={stored[0]['source_id']}&fromid=")
                results.append(dict(id=int(folder.name),kind=kind,**audit(html,url,stored)))
            except Exception as exc:
                results.append(dict(id=int(folder.name),kind=kind,status='failed',error=type(exc).__name__+': '+str(exc)))
    failures=[r for r in results if r['status']!='matched']
    report=dict(created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),documents=len(results),
                complete=sum(r['kind']=='source' for r in results),checkpoints=sum(r['kind']=='checkpoint' for r in results),
                failures=len(failures),results=results)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    for failure in failures:
        print(json.dumps(failure,ensure_ascii=False))
    if not results or failures:
        raise SystemExit(1)


if __name__=='__main__':
    main()
