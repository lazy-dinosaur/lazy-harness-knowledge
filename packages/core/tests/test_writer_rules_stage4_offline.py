"""Stage 4 writer rules (user option A after p2-s4-1): actor kept, one item only, force kept, code values kept."""
import offline_guard  # noqa: F401
import unittest

import writer_worker as ww


class StageFourRules(unittest.TestCase):
    def test_prompt_version_and_binding_moved(self):
        self.assertEqual(ww.PROMPT_VERSION, 'unit-writer-prompt/10')
        self.assertEqual(ww.BINDING, ww.digest([ww.SCHEMA_VERSION, ww.PROMPT_VERSION, ww.SYSTEM, ww.MODEL]))

    def test_four_rules_present(self):
        for marker in ('[행위자 보존]', '[항목 하나]', '[세기 보존]', '[코드 값 보존]'):
            self.assertIn(marker, ww.SYSTEM)
        self.assertIn('A는 B와 구별되어야 한다', ww.SYSTEM)
        self.assertIn('`/api/v1/auth/exchange`', ww.SYSTEM)

    def test_existing_rules_kept(self):
        self.assertIn('chosen_subject.name은 문장에 verbatim 넣는다', ww.SYSTEM)
        self.assertIn('번역하지 말고 원문 표기 그대로', ww.SYSTEM)


if __name__ == '__main__':
    unittest.main()
