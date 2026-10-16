import unittest
from scripts.refresh_ricedata_pedigree import repeated_action_error, SourceExpansionAmbiguity

class AmbiguityTests(unittest.TestCase):
    def test_different_named_nodes_share_original_action(self):
        html="""<div id="pedigree"><table><tr><td><a href="javascript:__doPostBack('pedigree','t1\\\\0/0')"><img alt="折叠 A/B"></a></td><td><a id="pedigreet1">A/B</a></td></tr></table><table><tr><td><a href="javascript:__doPostBack('pedigree','t1\\\\0/0')"><img alt="展开 C/D"></a></td><td><a id="pedigreet4">C/D</a></td></tr></table></div>"""
        error=repeated_action_error(html,'t1\\0/0')
        self.assertIsInstance(error,SourceExpansionAmbiguity)
        self.assertIn('A/B',str(error));self.assertIn('C/D',str(error))
    def test_single_stalled_action_is_not_claimed_as_source_collision(self):
        self.assertNotIsInstance(repeated_action_error('<div id="pedigree"></div>','t1'),SourceExpansionAmbiguity)

if __name__=='__main__':unittest.main()
