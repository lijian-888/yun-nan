import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("refresh",Path(__file__).parents[1]/"scripts"/"refresh_ricedata_pedigree.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class RefreshTests(unittest.TestCase):
    def test_zero_postback_keeps_displayed_name_and_branch(self):
        html = """<div id="pedigree"><table><tr><td></td><td><a id="pedigreet0" href="varis/619325.htm">02F6-155</a></td></tr></table>
        <table><tr><td><table width="20"><tr><td></td></tr></table></td><td></td><td><a id="pedigreet1" href="javascript:x">98P95</a></td></tr></table></div>"""
        nodes,pending = refresh.parse_tree(html,"https://www.ricedata.cn/variety/pedigree.aspx?id=619325")
        self.assertEqual(nodes[1]["name"],"98P95")
        self.assertIsNone(nodes[1]["source_id"])
        self.assertEqual(nodes[1]["parent"],0)
        self.assertFalse(pending)

    def test_shared_branches_require_positive_link_and_equal_expanded_content(self):
        records=[dict(name="root",source_id="1",parent=None),
                 dict(name="C57",source_id="57",parent=0),
                 dict(name="A",source_id=None,parent=1),
                 dict(name="C57",source_id="57",parent=0),
                 dict(name="B",source_id=None,parent=3),
                 dict(name="C57",source_id="57",parent=0),
                 dict(name="A",source_id=None,parent=5)]
        canonical,edges=refresh.snapshot_plan(records)
        self.assertNotEqual(canonical[1],canonical[3])
        self.assertEqual(canonical[1],canonical[5])
        self.assertEqual(canonical[2],canonical[6])

    def test_missing_source_is_error_not_empty_success(self):
        with self.assertRaises(ValueError):
            refresh.parse_tree("<html></html>","https://www.ricedata.cn")


if __name__=="__main__":
    unittest.main()
