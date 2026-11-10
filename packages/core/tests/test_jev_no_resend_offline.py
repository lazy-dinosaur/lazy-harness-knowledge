"""#24 external review: core Jev judgement paths make one attempt and never re-send a request that may have reached Jev."""
import offline_guard  # noqa: F401
import unittest

import capture_audit as ca
import worker_tools as wt

CFG = {'jev_base_url': 'http://jev.invalid', 'jev_api_key': 'k', 'jev_model': 'm'}


class NoResend(unittest.TestCase):
    def setUp(self):
        self.sends = []

    def boom(self, req, timeout=60):
        self.sends.append(req.full_url); raise OSError('connection reset after send')

    def test_request_answers_sends_once_by_default(self):
        with self.assertRaisesRegex(RuntimeError, 'after 1 attempts'):
            ca.request_answers(CFG, {'questions': {'m0': {}}}, http=self.boom)
        self.assertEqual(len(self.sends), 1)

    def test_make_ask_sends_once(self):
        with self.assertRaisesRegex(RuntimeError, 'not re-sent'):
            wt.make_ask(CFG, http=self.boom)({}, ['a'], 'q {i}')
        self.assertEqual(len(self.sends), 1)


if __name__ == '__main__':
    unittest.main()
