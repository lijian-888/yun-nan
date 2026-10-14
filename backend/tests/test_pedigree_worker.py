import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('fcntl'),'Worker uses Linux file locks')
class WorkerTests(unittest.TestCase):
    def setUp(self):
        scripts=str(Path(__file__).parents[1]/'scripts')
        sys.path.insert(0,scripts)
        import rebuild_ricedata_pedigrees
        self.worker=rebuild_ricedata_pedigrees
        sys.path.pop(0)

    def test_normal_moves_on_and_complex_resumes_without_partial_publication(self):
        worker=self.worker
        verified=set()
        varieties=[dict(variety_id=i,source_variety_id=str(i),variety_name=f'品种{i}') for i in (1,2)]
        class Result:
            def mappings(self): return varieties
        class Connection:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,*args): return Result()
            def scalar(self,query,params=None):
                return params['id'] in verified if params else len(verified)
        class Engine:
            def connect(self): return Connection()
            def begin(self): return Connection()
        calls=[]
        def collect(source_id,**kwargs):
            calls.append((source_id,bool(kwargs['resume'])))
            state=dict(url='test',html='test',expanded=[])
            kwargs['on_progress'](state,dict(expansions=0,pending_branches=1))
            if source_id=='1' and not kwargs['resume']:
                raise worker.CollectionDeferred(state)
            return 'test',[dict(name=source_id)],'test'
        def save(conn,vid,*args):
            verified.add(vid)
            return vid
        with tempfile.TemporaryDirectory() as directory,patch.object(worker,'create_engine',return_value=Engine()),patch.object(worker,'collect',side_effect=collect),patch.object(worker,'save_snapshot',side_effect=save),patch.dict(worker.os.environ,{'MIGRATION_DATABASE_URL':'test'}):
            with patch.object(sys,'argv',['worker','--state-dir',directory,'--lane','normal']):
                worker.main()
            self.assertEqual(verified,{2})
            self.assertEqual(json.loads((Path(directory)/'deferred/1.json').read_text())['status'],'deferred')
            with patch.object(sys,'argv',['worker','--state-dir',directory,'--lane','complex']):
                worker.main()
            self.assertEqual(verified,{1,2})
            self.assertEqual(calls,[('1',False),('2',False),('1',True)])
            self.assertEqual(json.loads((Path(directory)/'complex-progress.json').read_text())['status'],'completed')

    def test_global_block_prevents_both_lanes_from_requesting(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            (state/'source-blocked.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'blocked'):
                self.worker.pace_requests(state)


if __name__=='__main__':
    unittest.main()
