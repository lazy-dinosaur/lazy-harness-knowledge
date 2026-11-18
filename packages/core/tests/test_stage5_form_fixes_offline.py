"""p2-s5-1 fixes (user option A): no forced `why` for document decisions, prose parentheses, code values kept."""
import offline_guard  # noqa: F401
import inspect
import unittest

import fact_form as ff
import runner
import stagewise as sw


class FormFixes(unittest.TestCase):
    def test_prose_parentheses_are_text(self):
        self.assertEqual(ff.check('발급 활성화 코드는 6-character Crockford Base32 code (30-bit)다.'), [])
        self.assertEqual(ff.check('IF (A는 있다 OR B는 있다) THEN C는 열린다.'), [])
        self.assertIn('괄호가 짝이 맞지 않는다', ff.check('IF (A는 있다 OR B는 있다 THEN C는 열린다.'))

    def test_document_decision_without_why(self):
        codes = lambda f: {e['code'] for e in runner.lint_fact(f)['errors']} if isinstance(runner.lint_fact(f), dict) else {e['code'] for e in runner.lint_fact(f)}
        doc = {'kind': 'decision', 'fact': 'X는 Y하기로 한다.', 'evidence_source': 'official_doc'}
        chat = dict(doc, evidence_source='user_confirmed')
        self.assertNotIn('E_WHY', codes(doc))
        self.assertIn('E_WHY', codes(chat))

    def test_stage5_checks_code_values_before_q1(self):
        src = inspect.getsource(sw.run_ground)
        self.assertIn('E_CODE_VALUE_MISSING', src)
        self.assertLess(src.index('E_CODE_VALUE_MISSING'), src.index('check_many('))


if __name__ == '__main__':
    unittest.main()
