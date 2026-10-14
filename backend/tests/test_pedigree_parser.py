import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('refresh_parser',Path(__file__).parents[1]/'scripts'/'refresh_ricedata_pedigree.py')
refresh=importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class ParserTests(unittest.TestCase):
    def test_default_parser_returns_tree_and_postback_fields_together(self):
        html='<input name="__VIEWSTATE" value="A&amp;B"><div id="pedigree"><table><tr><td></td><td><a id="pedigreet0" href="varis/1.htm">根</a></td></tr></table></div>'
        nodes,pending,fields=refresh.parse_source(html,'https://www.ricedata.cn')
        self.assertEqual(nodes[0]['name'],'根')
        self.assertEqual(pending,[])
        self.assertEqual(fields,{'__VIEWSTATE':'A&B'})

    @unittest.skipUnless(importlib.util.find_spec('lxml'),'Direct parser requires lxml')
    def test_direct_matches_reference_for_labels_roles_and_actions(self):
        for label in ['IR8/科情3号','98P95','甲 &amp; 乙','甲<br>乙','<b>甲</b> 乙','甲<!--注释-->乙','甲<script>ignore</script>乙','甲&nbsp;乙']:
            html=f'''<input name="__VIEWSTATE" value="A&amp;B"><input name="__EVENTVALIDATION" value="C"><div id="pedigree">
            <table><tr><td><a href="javascript:__doPostBack('pedigree','t1')"><img alt="折叠 根"></a></td><td><a id="pedigreet0" href="varis/1.htm">根</a></td></tr></table>
            <table><tr><td><table><tr><td></td></tr></table></td><td><a href="javascript:__doPostBack('pedigree','t1\\\\0/2//3')"><img alt="展开 组合"></a></td><td><a id="pedigreet1">{label}</a></td></tr></table>
            <table><tr><td></td><td></td><td><a id="pedigreet2" href="varis/0.htm">98P95</a></td></tr></table></div>'''
            self.assertEqual(refresh.parse_source(html,'https://www.ricedata.cn',mode='direct'),refresh.parse_source(html,'https://www.ricedata.cn',mode='reference'),label)

    @unittest.skipUnless(importlib.util.find_spec('lxml'),'Direct parser requires lxml')
    def test_direct_rejects_unknown_structure_like_reference(self):
        for html in ['<html></html>','<div id="pedigree"><table><tr><td><a id="pedigreet0">x</a></td></tr></table></div>',
                     '<div id="pedigree"><table><tr><td><a><img alt="unknown"></a></td><td><a id="pedigreet0">x</a></td></tr></table></div>']:
            for mode in ['direct','reference']:
                with self.assertRaises((ValueError,IndexError)):
                    refresh.parse_source(html,'https://www.ricedata.cn',mode=mode)

    @unittest.skipUnless(importlib.util.find_spec('lxml'),'Direct parser requires lxml')
    def test_collect_sends_identical_form_state_and_preserves_all_paths(self):
        before='''<input name="__VIEWSTATE" value="A&amp;B"><input name="__EVENTVALIDATION" value="C"><div id="pedigree"><table><tr><td><a href="javascript:__doPostBack('pedigree','t1')"><img alt="展开"></a></td><td><a id="pedigreet0" href="varis/1.htm">根</a></td></tr></table></div>'''
        after=before.replace('展开','折叠').replace('</div>','<table><tr><td></td><td></td><td><a id="pedigreet1">IR8/科情3号</a></td></tr></table></div>')
        outcomes=[]
        real=refresh.parse_source
        for mode in ('reference','direct'):
            with patch.object(refresh,'parse_source',side_effect=lambda html,url:real(html,url,mode=mode)),patch.object(refresh.requests,'Session') as factory,patch.object(refresh.time,'sleep'):
                client=factory.return_value.__enter__.return_value
                client.get.return_value.content=before.encode()
                client.post.return_value.content=after.encode()
                _,records,_=refresh.collect(1)
                outcomes.append((records,client.post.call_args.kwargs['data']))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(outcomes[1][1]['__VIEWSTATE'],'A&B')
        self.assertEqual([n['name'] for n in outcomes[1][0]],['根','IR8/科情3号'])


if __name__=='__main__':
    unittest.main()
