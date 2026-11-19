"""prompt_leak finds host code values / phrases in prompts; the writer prompt carries no host terms of a synthetic corpus."""
import offline_guard  # noqa: F401
import unittest

import prompt_leak as pl
import writer_worker as ww

CORPUS = """# Host doc\n- Must: keep `HX_ACTIVE_2024` and `src/host/secret.ts`\n- the host clinic seats prefer the named person\n"""


class PromptLeak(unittest.TestCase):
    def test_finds_code_values_and_phrases(self):
        hits = pl.scan(CORPUS, {'p': 'use `HX_ACTIVE_2024` here; the host clinic seats prefer the named person'})
        self.assertIn('`HX_ACTIVE_2024`', hits['p'])
        self.assertTrue(any('host clinic seats prefer' in h for h in hits['p']))
        self.assertIn('"host clinic seats"', pl.scan(CORPUS, {'q': 'example: "host clinic seats" is wrong here, padded to sixty characters'})['q'])

    def test_clean_prompt(self):
        self.assertEqual(pl.scan(CORPUS, {'w': ww.SYSTEM}), {})
        self.assertEqual(ww.PROMPT_VERSION, 'unit-writer-prompt/14')


if __name__ == '__main__':
    unittest.main()
