"""User option A after the R01 stage-4 source review: the modality/fidelity verdict is advisory in stagewise stage 5."""
import offline_guard  # noqa: F401
import inspect
import unittest

import source_modality as sm
import stagewise as sw
import place_write as w
import writer_worker as ww

UNIT = {'content': 'c', 'source_ranges': [[1, 1]], 'target_expression': 't'}


def receipt(accepted, sentence='s', kind='fact', source='src'):
    return {'version': sm.VERSION, 'binding': sm.binding(UNIT, sentence, kind, source), 'accepted': accepted,
            'fidelity': {'accepted': accepted, 'probabilities': {'supported': .9 if accepted else .1}},
            'source_intent': {'mode': 'description'}}


class Advisory(unittest.TestCase):
    def test_advise_returns_the_verdict_without_raising(self):
        note = sm.advise(receipt(False), UNIT, 's', 'fact', 'src')
        self.assertEqual((note['accepted'], note['fidelity_accepted'], note['mode']), (False, False, 'description'))
        self.assertTrue(sm.advise(receipt(True), UNIT, 's', 'fact', 'src')['accepted'])
        with self.assertRaises(ValueError):
            sm.require(receipt(False), UNIT, 's', 'fact', 'src')  # the veto form is unchanged

    def test_advise_still_rejects_a_foreign_or_missing_receipt(self):
        for bad in (None, dict(receipt(True), binding='other'), dict(receipt(True), version='old')):
            with self.assertRaises(ValueError):
                sm.advise(bad, UNIT, 's', 'fact', 'src')

    def test_stage5_is_advisory_by_default(self):
        self.assertIn('modality_advice', inspect.signature(w.validate_fact).parameters)
        sig = inspect.signature(sw.run_ground)
        self.assertIs(sig.parameters['modality_veto'].default, False)
        self.assertIn("result['modality_advice']", inspect.getsource(sw.run_ground))

    def test_writer_decision_rule(self):
        self.assertEqual(ww.PROMPT_VERSION, 'unit-writer-prompt/14')
        self.assertIn('[결정 항목]', ww.SYSTEM)
        self.assertIn('원문에 없는 조건을 덧붙이지 않는다', ww.SYSTEM)


if __name__ == '__main__':
    unittest.main()
