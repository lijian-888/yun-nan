"""Offline context-reuse audit against archived full trees; never writes database."""
import argparse,json,time
from pathlib import Path
from collections import Counter,defaultdict
from app.pedigree_direct import fragment,semantic_signature,assemble
from refresh_ricedata_pedigree import parse_source,occurrence_paths


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    facts={};variants=defaultdict(set);complete=[];observations=0
    paths=list(args.state_dir.glob('*/source.html'))+list(args.state_dir.glob('*/checkpoint.json'))
    for path in paths:
        if path.name=='checkpoint.json':
            s=json.loads(path.read_text());html,url=s['html'],s['url']
        else:
            stored=json.loads(path.with_name('nodes.json').read_text());html=path.read_text()
            url=next((n['url'] for n in stored if not n['source_id']),f"https://www.ricedata.cn/variety/pedigree.aspx?id={stored[0]['source_id']}&fromid=")
        records,pending,_=parse_source(html,url)
        if not pending:complete.append((path.parent.name,records))
        for i in range(len(records)):
            value=fragment(records,i)
            if value:
                observations+=1;signature=semantic_signature(value);key=value['root'];variants[key].add(signature)
                facts.setdefault(key,dict(fragment=value,status='verified_direct'))
    for key,values in variants.items():
        if len(values)>1:facts[key]['status']='conflict'
    results=[]
    for vid,records in complete:
        root=fragment(records)['root'];graph=assemble(root,facts);adj=defaultdict(list);names={n['id']:n.get('raw_name',n['name']) for n in graph['nodes']};actual=Counter()
        for e in graph['edges']:adj[e['child_id']].append(e['parent_id'])
        stack=[(root,(),frozenset())]
        while stack:
            key,path,seen=stack.pop()
            if key in seen or sum(actual.values())>len(records):break
            path=path+(names[key],);actual[path]+=1
            stack.extend((p,path,seen|{key}) for p in adj[key])
        gaps=sum(n['kind']=='unknown' for n in graph['nodes'])
        matched=dict(actual)==occurrence_paths(records) and not graph['missing_count'] and not graph['blocked_relation_count'] and graph['conflict_count']==gaps
        status=('matched_with_source_name_gap' if gaps else 'matched') if matched else 'mismatch'
        results.append(dict(variety_id=vid,name=records[0]['name'],source_occurrences=len(records),reconstructed_occurrences=sum(actual.values()),status=status,fully_expanded=graph['fully_expanded'],warnings=graph['warnings']))
    report=dict(created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),observations=observations,unique_materials=len(facts),
                conflicting_materials=sum(len(v)>1 for v in variants.values()),complete_trees=len(results),source_name_gaps=sum(r['status']=='matched_with_source_name_gap' for r in results),failures=sum(r['status']=='mismatch' for r in results),results=results)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    for result in results:
        if result['status']!='matched':print(json.dumps(result,ensure_ascii=False))
    if not results or report['failures']:raise SystemExit(1)


if __name__=='__main__':main()
