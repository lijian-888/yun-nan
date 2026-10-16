import unittest
from app.pedigree_direct import fragment, assemble, material_key
from app.pedigree_completion import alias_fragment, graph_paths, validate_source_records


def node(name, sid, parent=None):
    return dict(name=name, source_id=sid, parent=parent, pending_expansion=False,
                url='https://www.ricedata.cn/variety/')


class CompletionTests(unittest.TestCase):
    def test_yunjing39_explicit_alias_gap_closes_without_renaming_source_label(self):
        root=fragment([node('云粳39号','612639'),node('合系41号','602264',0)])
        own=fragment([node('合系41号（94-26;合靖6号）','602264'),node('滇靖8号',None,0)])
        facts={v['root']:dict(fragment=v,status='verified_direct') for v in [root,own]}
        self.assertFalse(assemble(root['root'],facts)['fully_expanded'])
        proven=alias_fragment(own,'602264','合系41号')
        self.assertIsNotNone(proven)
        facts[proven['root']]=dict(fragment=proven,status='verified_direct')
        g=assemble(root['root'],facts)
        self.assertTrue(g['fully_expanded'])
        self.assertEqual([n['name'] for n in g['nodes']],['云粳39号','合系41号','滇靖8号'])
        self.assertEqual(len(g['edges']),2)
        self.assertEqual(own['nodes'][0]['name'],'合系41号（94-26;合靖6号）')

    def test_same_id_alone_or_name_prefix_does_not_prove_alias(self):
        f=fragment([node('云粳优10号（滇粳优10号）','610513')])
        self.assertIsNone(alias_fragment(f,'610513','云粳优1'))
        self.assertIsNone(alias_fragment(f,'610513','未知名称'))
        self.assertIsNone(alias_fragment(f,'123','云粳优10号'))

    def test_explicit_parenthetical_synonym_preserves_contextual_nodes(self):
        f=fragment([node('云粳优10号（滇粳优10号）','610513'),node('A/B',None,0),node('A',None,1),node('B',None,1)])
        a=alias_fragment(f,'610513','滇粳优10号')
        self.assertEqual(a['root'],material_key('610513','滇粳优10号'))
        self.assertEqual([n['name'] for n in a['nodes']],['滇粳优10号','A/B','A','B'])
        self.assertNotEqual(a['nodes'][1]['key'],f['nodes'][1]['key'])

    def test_duplicate_parent_occurrences_are_compared_not_deduplicated(self):
        graph=dict(nodes=[dict(id='a',name='A'),dict(id='b',name='B')],
                   root_ids=['a'],edges=[dict(child_id='a',parent_id='b'),dict(child_id='a',parent_id='b')])
        self.assertEqual(graph_paths(graph)[('A','B')],2)

    def test_unknown_names_pending_branches_and_source_cycles_cannot_pass(self):
        for records in ([node('0',None)],
                        [dict(node('A','1'),pending_expansion=True)],
                        [node('A','1'),node('B','2',0),node('A','1',1)],
                        [node('A','1'),node('B','2',0),node('A（别名）','1',1)]):
            with self.assertRaises(ValueError):validate_source_records(records)

    def test_repeated_material_in_different_branches_is_valid(self):
        validate_source_records([node('R','1'),node('A','2',0),node('A','2',0)])

    def test_partial_fragment_is_not_reused_as_complete_alias(self):
        f=fragment([node('A（别名）','1')]);f['nodes'][0]['source_limited']=True
        self.assertIsNone(alias_fragment(f,'1','别名'))


if __name__=='__main__':unittest.main()
