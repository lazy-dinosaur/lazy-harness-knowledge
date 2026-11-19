"""User option A after the targeted probe: stage 5 asks the imported-condition question; writer rule for Must-not actors."""
import offline_guard  # noqa: F401
import inspect
import unittest

import stagewise as sw
import writer_worker as ww


class ImportedCondition(unittest.TestCase):
    def test_question_runs_after_q1_q2_and_triggers_one_rewrite(self):
        src = inspect.getsource(sw.run_ground)
        self.assertIs(inspect.signature(sw.run_ground).parameters['imported_check'].default, True)
        self.assertIn('E_IMPORTED_CONDITION', src)
        self.assertLess(src.index('check_many('), src.index('IMPORTED_CONDITION_QUESTION'))
        self.assertIn('source_lines', sw.IMPORTED_CONDITION_QUESTION)

    def test_writer_must_not_actor_rule(self):
        self.assertEqual(ww.PROMPT_VERSION, 'unit-writer-prompt/12')
        self.assertIn('[금지 항목 행위자]', ww.SYSTEM)
        self.assertIn('혼동되어서는 안 된다', ww.SYSTEM)


if __name__ == '__main__':
    unittest.main()
