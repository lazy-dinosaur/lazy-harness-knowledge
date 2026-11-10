"""#24: a unit's inherited list introducers reach the worker and Jev projections, and naming roles are told how to read them."""
import offline_guard  # noqa: F401
import unittest

import active_worker as aw
import active_judgements as aj

UNIT = {'content': '다른 고객의 주문을 보여 준다', 'target_expression': '주문', 'source_ranges': [[7, 7]],
        'inherited_context': [{'line': 5, 'quote': '- Must not:'}], 'vector': [0.1]}


class InheritedContextPackets(unittest.TestCase):
    def test_worker_unit_projection_keeps_the_introducer(self):
        out = aw.project(UNIT, aw.UNIT)
        self.assertEqual(out['inherited_context'], [{'line': 5, 'quote': '- Must not:'}]); self.assertNotIn('vector', out)

    def test_jev_state_projection_keeps_the_introducer(self):
        out = aj.state_projection('placement', {'unit': UNIT})
        self.assertEqual(out['unit']['inherited_context'], [{'line': 5, 'quote': '- Must not:'}])

    def test_naming_roles_explain_the_field(self):
        for role in ('reextract', 'domain-name', 'subject-name'):
            self.assertIn('unit.inherited_context', aw.PROMPTS[role], role)
        self.assertEqual(aw.PROMPT_VERSION, 'active-worker-prompt/11')

    def test_malformed_introducer_is_refused(self):
        with self.assertRaises(aw.WorkerFailure):
            aw.project(dict(UNIT, inherited_context=[{'line': '5', 'quote': '- Must not:'}]), aw.UNIT)


if __name__ == '__main__':
    unittest.main()
