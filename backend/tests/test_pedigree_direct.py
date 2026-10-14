import unittest
from app.pedigree_direct import fragment,assemble,material_key,semantic_signature


def node(name,sid,parent=None,pending=False):
    return dict(name=name,source_id=sid,parent=parent,pending_expansion=pending,url='https://www.ricedata.cn/variety/')


class DirectTests(unittest.TestCase):
    def test_frontier_and_named_zero_are_preserved(self):
        data=[node('根','1'),node('IR8/科情3号',None,0),node('IR8','8',1,True),node('科情3号',None,1)]
        f=fragment(data);self.assertEqual(len(f['nodes']),4)
        self.assertEqual(len(f['references']),1)
        g=assemble(f['root'],{f['root']:dict(fragment=f,status='verified_direct')})
        self.assertEqual([n['name'] for n in g['nodes']],['根','IR8/科情3号','IR8','科情3号'])
        self.assertFalse(g['fully_expanded'])
        self.assertEqual(g['missing_count'],1)

    def test_unknown_pending_is_not_accepted(self):
        self.assertIsNone(fragment([node('根','1'),node('组合',None,0,True)]))

    def test_no_name_based_merge_and_duplicate_positions_survive(self):
        f=fragment([node('根','1'),node('同名',None,0),node('同名',None,0)])
        self.assertNotEqual(f['nodes'][1]['key'],f['nodes'][2]['key'])
        same=fragment([node('根','1'),node('父本','2',0),node('父本','2',0)])
        leaf=fragment([node('父本','2')])
        g=assemble(same['root'],{same['root']:dict(fragment=same,status='verified_direct'),leaf['root']:dict(fragment=leaf,status='verified_direct')})
        self.assertEqual(len(g['edges']),2)
        self.assertTrue(g['fully_expanded'])

    def test_cycle_is_quarantined_not_published_as_complete(self):
        a=fragment([node('A','1'),node('B','2',0)])
        b=fragment([node('B','2'),node('A','1',0)])
        g=assemble(a['root'],{a['root']:dict(fragment=a,status='verified_direct'),b['root']:dict(fragment=b,status='verified_direct')})
        self.assertEqual(g['blocked_relation_count'],2)
        self.assertFalse(g['fully_expanded'])
        self.assertEqual(g['edges'],[])

    def test_conflicting_fact_is_not_followed_and_zero_is_not_a_label(self):
        f=fragment([node('根','1'),node('0',None,0),node('P','2',0)])
        g=assemble(f['root'],{f['root']:dict(fragment=f,status='verified_direct'),material_key('2','P'):dict(status='conflict')})
        self.assertNotIn('0',[n['name'] for n in g['nodes']])
        self.assertEqual(g['conflict_count'],2)
        self.assertFalse(g['fully_expanded'])

    def test_conflicted_root_keeps_its_name_and_reports_conflict(self):
        f=fragment([node('已知品种','1'),node('P','2',0)])
        g=assemble(f['root'],{f['root']:dict(fragment=f,status='conflict')})
        self.assertEqual(g['nodes'][0]['name'],'已知品种')
        self.assertEqual(g['edges'],[])
        self.assertEqual(g['conflict_count'],1)
        self.assertFalse(g['fully_expanded'])


if __name__=='__main__':unittest.main()
