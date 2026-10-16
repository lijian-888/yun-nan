"""Persistent ancestor-closure queue. Direct job completion is NOT graph completion.

One request channel, existing shared limiter, source evidence before alias reuse.
Independent full-source snapshots repair residual/context-specific differences.
"""
import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, '/app/scripts')
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.pedigree_direct import assemble, fragment, semantic_signature
from app.pedigree_completion import alias_fragment, graph_paths, validate_source_records, explicit_alias
from app.pedigree_graph import pedigree_graph
from collect_ricedata_direct import store
from refresh_ricedata_pedigree import (collect, CollectionDeferred, SourceExpansionAmbiguity,
                                      occurrence_paths, save_snapshot, snapshot_plan)
from rebuild_ricedata_pedigrees import pace_requests, write_json, load_json


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class CompletionWorker:
    def __init__(self, state):
        self.state = state
        self.rate = Path('/data/research/pedigree-rebuild')
        self.engine = create_engine(os.environ['MIGRATION_DATABASE_URL'], pool_pre_ping=True)
        self.db = sqlite3.connect(state / 'queue.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS jobs (
            variety_id INTEGER PRIMARY KEY, source_id TEXT, name TEXT, root_key TEXT,
            status TEXT NOT NULL, updated_utc TEXT, metrics TEXT, error TEXT,
            slices INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
          CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,variety_id);
          CREATE TABLE IF NOT EXISTS validated_sources (source_id TEXT PRIMARY KEY, proof TEXT);
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.current = None
        self.facts = {}
        self.by_id = defaultdict(list)
        self.reload_facts()

    def reload_facts(self):
        with self.engine.connect() as c:
            self.facts = {r.fact_key: dict(r) for r in c.execute(text(
                'select fact_key,status,fragment,signature,evidence from ricedata.rice_pedigree_direct_fact')).mappings()}
        self.by_id.clear()
        for f in self.facts.values():
            if f['status'] == 'verified_direct':
                self.by_id[str(f['fragment']['nodes'][0]['source_id'])].append(f)

    def report(self, status='running', branch=None):
        counts = dict(self.db.execute('select status,count(*) from jobs group by status').fetchall())
        write_json(self.state / 'progress.json', dict(
            mode='ancestor_completion', status=status, current=self.current,
            counts=counts, queued_total=sum(counts.values()), branch=branch, updated_utc=utc(),
            definitions={'closed_local':'ancestor graph closed; NOT independent full-source verification',
                         'verified_source':'full source collected and API occurrence paths compared exactly',
                         'source_limited':'incomplete, retained under user-approved source limitation policy'}))

    def update(self, vid, status, metrics=None, error=None):
        self.db.execute('update jobs set status=?,metrics=?,error=?,updated_utc=? where variety_id=?',
                        (status, json.dumps(metrics or {}, ensure_ascii=False), error, utc(), vid))
        self.db.commit()
        self.report()

    def seed(self):
        if self.db.execute("select value from meta where key='seeded'").fetchone():
            return
        with self.engine.connect() as c:
            rows = c.execute(text('''select v.variety_id,v.source_variety_id,v.variety_name,j.root_key
                from ricedata.rice_variety v left join ricedata.rice_pedigree_direct_job j
                on j.source_id=v.source_variety_id order by v.variety_id''')).mappings().all()
            snapshots = {r.variety_id: r.fully_expanded for r in c.execute(text('''
                select distinct on (p.variety_id) p.variety_id,
                (p.fully_expanded and not exists (
                    select 1 from ricedata.rice_pedigree_node n where n.pedigree_snapshot_id=p.pedigree_snapshot_id
                    and (n.material_name is null or btrim(n.material_name) in ('','0','?','？'))
                )) as fully_expanded from ricedata.rice_pedigree_snapshot p
                where p.source_verified order by p.variety_id,p.crawled_at desc,p.pedigree_snapshot_id desc''')).mappings()}
        counts = Counter()
        for i, row in enumerate(rows):
            try:
                g = assemble(row['root_key'], self.facts) if row['root_key'] else None
                closed = snapshots.get(row['variety_id'], bool(g and g['fully_expanded']))
            except ValueError:
                closed = False
            counts['closed_baseline' if closed else 'queued_incomplete'] += 1
            if not closed:
                self.db.execute('insert or ignore into jobs(variety_id,source_id,name,root_key,status,updated_utc) values(?,?,?,?,?,?)',
                                (row['variety_id'], row['source_variety_id'], row['variety_name'], row['root_key'], 'pending', utc()))
            if i % 2000 == 0:
                self.db.commit()
                print(json.dumps(dict(event='seeding',scanned=i,total=len(rows))), flush=True)
        self.db.execute("insert into meta values('seeded',?)", (utc(),))
        self.db.commit()
        write_json(self.state / 'baseline.json', dict(checked_utc=utc(), total=len(rows), counts=dict(counts)))
        self.report()

    def source(self, sid, folder, direct=False):
        folder.mkdir(parents=True, exist_ok=True)
        checkpoint = folder / 'checkpoint.json'
        def progress(state, metric):
            write_json(checkpoint, state)
            self.report(branch=dict(source_id=sid, direct_only=direct, **metric))
        result = collect(sid, direct_only=direct, resume=load_json(checkpoint), on_progress=progress,
                         slice_seconds=55, slice_expansions=40,
                         before_request=lambda: pace_requests(self.rate))
        url, records, html = result
        if str(records[0]['source_id']) != str(sid):
            raise ValueError('Source root identity mismatch')
        (folder / 'source.html').write_text(html, encoding='utf-8')
        write_json(folder / 'nodes.json', records)
        return url, records, html

    def repair_alias(self, node):
        sid, alias = node['source_id'], node['name']
        candidates = []
        for f in self.by_id.get(str(sid), []):
            value = alias_fragment(f['fragment'], sid, alias)
            if value is not None:
                candidates.append((f, value))
        if not candidates or len({semantic_signature(v) for _,v in candidates}) != 1:
            return False
        # The mapping must already occur in original source-linked evidence.
        refs = [k for k,f in self.facts.items() if f['status'] in ('verified_direct','verified_partial') and any(
            n.get('source_id') == sid and n['name'] == alias for n in f['fragment']['nodes'][1:])]
        if not refs:
            return False
        cached = self.db.execute('select proof from validated_sources where source_id=?', (sid,)).fetchone()
        if cached:
            proof = json.loads(cached['proof'])
            fresh = proof['fragment']
        else:
            url, records, html = self.source(sid, self.state / 'canonical' / sid, direct=True)
            fresh = fragment(records)
            if fresh is None:
                raise ValueError('Canonical direct fragment still incomplete')
            proof = dict(source_id=sid, canonical_name=records[0]['name'], url=url,
                         sha256=hashlib.sha256(html.encode()).hexdigest(), checked_utc=utc(), fragment=fresh)
            self.db.execute('insert into validated_sources values(?,?)', (sid,json.dumps(proof,ensure_ascii=False)))
            self.db.commit()
        value = alias_fragment(fresh, sid, alias)
        if value is None or semantic_signature(value) not in {semantic_signature(v) for _,v in candidates}:
            return False
        if not any(semantic_signature(fresh) == f['signature'] for f in self.by_id.get(str(sid), [])):
            return False
        evidence = dict(kind='current_source_explicit_alias',original_name=alias,
                        reference_facts=refs,**{k:v for k,v in proof.items() if k != 'fragment'})
        with self.engine.begin() as c:
            if c.execute(text('select 1 from ricedata.rice_pedigree_direct_fact where fact_key=:key'),{'key':value['root']}).scalar():
                return False
            store(c,value,proof['url'],proof['sha256'])
            c.execute(text('update ricedata.rice_pedigree_direct_fact set evidence=evidence || CAST(:proof AS jsonb) where fact_key=:key'),
                      {'proof':json.dumps([evidence],ensure_ascii=False),'key':value['root']})
        self.facts[value['root']] = dict(fragment=value,status='verified_direct',signature=semantic_signature(value),evidence=[evidence])
        with (self.state / 'alias-evidence.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps(evidence,ensure_ascii=False)+'\n')
        print(json.dumps(dict(event='alias_repaired',source_id=sid,name=alias),ensure_ascii=False),flush=True)
        return True

    def repair(self, job):
        for _ in range(12):
            g = assemble(job['root_key'],self.facts) if job['root_key'] else None
            if not g:
                self.update(job['variety_id'],'awaiting_source');return
            if g['fully_expanded']:
                # API may prioritize an older snapshot with unknown labels over this graph.
                with Session(self.engine) as s:
                    api=pedigree_graph(s,job['variety_id'])
                status='closed_local' if api and api['snapshot'] and api['snapshot']['fully_expanded'] else 'awaiting_source'
                self.update(job['variety_id'],status,self.metrics(g));return
            gaps = [n for n in g['nodes'] if n['id'] not in self.facts and n.get('source_id')]
            changed = False
            for n in gaps:
                if self.repair_alias(n):
                    changed = True
                    break
            if not changed:
                self.update(job['variety_id'],'source_limited' if g['source_limited_count'] else 'awaiting_source',self.metrics(g))
                return
        self.update(job['variety_id'],'pending',self.metrics(g))

    @staticmethod
    def metrics(g):
        return {k:g[k] for k in ('fully_expanded','missing_count','conflict_count','blocked_relation_count','source_limited_count')} | dict(nodes=len(g['nodes']),edges=len(g['edges']))

    def full_source(self, job):
        vid,sid=job['variety_id'],job['source_id']
        folder=self.state/'full-source'/sid
        self.update(vid,'collecting_source')
        url,records,html=self.source(sid,folder)
        if not (explicit_alias(records[0]['name'],job['name']) or explicit_alias(job['name'],records[0]['name'])):
            raise ValueError('Current source root name differs from selected variety without explicit alias evidence')
        validate_source_records(records)
        canonical,edges=snapshot_plan(records)
        if len(set(canonical.values()))>5000 or len(edges)>10000:
            raise ValueError('Full source exceeds API display bound; evidence retained, snapshot not published')
        expected=Counter(occurrence_paths(records))
        before=assemble(job['root_key'],self.facts) if job['root_key'] else None
        try:
            observed=graph_paths(before) if before else Counter()
            delta=dict(missing_paths=[dict(path=list(p),count=c) for p,c in (expected-observed).items()],
                       extra_paths=[dict(path=list(p),count=c) for p,c in (observed-expected).items()])
        except ValueError:
            delta=dict(comparison='local occurrence traversal blocked; full source snapshot replaces display only')
        write_json(folder/'before-differences.json',delta)
        with self.engine.begin() as c:
            snapshot=save_snapshot(c,vid,url,records)
        # Compare the actual API graph serializer path, including duplicate occurrences.
        with Session(self.engine) as s:
            api=pedigree_graph(s,vid)
        actual=graph_paths(api)
        if actual != expected or not api['snapshot']['fully_expanded']:
            raise ValueError('Published API graph differs from independently collected full source')
        result=dict(status='verified_source',variety_id=vid,name=job['name'],source_id=sid,url=url,
                    checked_utc=utc(),source_sha256=hashlib.sha256(html.encode()).hexdigest(),
                    source_occurrences=sum(expected.values()),graph_occurrences=sum(actual.values()),
                    missing_paths=[],extra_paths=[],snapshot_id=snapshot,nodes=api['node_count'],edges=api['edge_count'])
        write_json(folder/'result.json',result)
        self.update(vid,'verified_source',result)
        print(json.dumps(result,ensure_ascii=False),flush=True)

    def run(self):
        self.seed()
        turn=0
        while True:
            if (self.rate/'source-blocked.json').exists():
                self.report('source_access_blocked');return
            target=self.db.execute('select * from jobs where variety_id=969').fetchone()
            unfinished=target and target['status'] not in ('verified_source','source_limited','needs_review')
            job=target if unfinished and turn%4==0 else self.db.execute(
                "select * from jobs where status='pending' order by variety_id limit 1").fetchone()
            if not job and unfinished:
                job=target
            if not job:
                job=self.db.execute("select * from jobs where status in ('awaiting_source','collecting_source') order by slices,variety_id limit 1").fetchone()
            if not job:
                self.report('queue_pass_finished');return
            self.current=dict(variety_id=job['variety_id'],source_id=job['source_id'],name=job['name'])
            try:
                if job['status']=='pending':
                    self.repair(job)
                else:
                    self.full_source(job)
                # Target always receives independent full-source verification, even if locally closed.
                if job['variety_id']==969:
                    now=self.db.execute('select status from jobs where variety_id=969').fetchone()[0]
                    if now=='closed_local':self.update(969,'awaiting_source')
            except CollectionDeferred:
                self.db.execute('update jobs set slices=slices+1 where variety_id=?',(job['variety_id'],))
                self.db.commit()
            except SourceExpansionAmbiguity:
                self.update(job['variety_id'],'source_limited',error='SourceExpansionAmbiguity; maximum observed checkpoint retained, not complete')
            except Exception as exc:
                code=getattr(getattr(exc,'response',None),'status_code',None)
                if code in (403,429):
                    write_json(self.rate/'source-blocked.json',dict(source_id=job['source_id'],status_code=code,checked_utc=utc()))
                    self.report('source_access_blocked');return
                # Scientific/validation failures are not retried. Only transport errors retry (bounded).
                import requests
                retry=isinstance(exc,requests.RequestException) and job['attempts']<2
                self.db.execute('update jobs set attempts=attempts+1 where variety_id=?',(job['variety_id'],))
                self.db.commit()
                safe_error=type(exc).__name__ + (': '+str(exc) if isinstance(exc,ValueError) else '')
                self.update(job['variety_id'],'awaiting_source' if retry else 'needs_review',error=safe_error)
                print(json.dumps(dict(event='job_exception',**self.current,error=safe_error)),flush=True)
            turn+=1


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',type=Path,default=Path('/data/research/pedigree-direct/ancestor-completion'))
    args=parser.parse_args()
    args.state_dir.mkdir(parents=True,exist_ok=True)
    with (args.state_dir/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        CompletionWorker(args.state_dir).run()


if __name__=='__main__':main()
