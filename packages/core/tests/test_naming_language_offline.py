"""#20: domain naming sees existing names by default and re-asks once in the catalog language before holding."""
import offline_guard  # noqa: F401
import unittest

import place_write as w

UNIT = {'host_id': 'h', 'unit_id': '00000000-0000-4000-8000-000000000020', 'content': '설치 코드는 단말을 활성화한다.',
        'source_ranges': [[1, 1]], 'target_expression': '설치 코드'}
CATALOG = [{'name': '물류', 'description': '배송·창고 업무'}, {'name': '인사', 'description': '직원 관리'}]


def ports(names):
    calls = []
    def model(name, packet, task):
        calls.append(packet)
        n = names[min(len(calls), len(names)) - 1]
        return {'original_expression': '설치 코드', 'source_concept': '설치 코드', 'name': n, 'description': '단말 활성화와 설치를 다루는 영역'}
    def choose(kind, packet, options):
        n = len(options) - 1
        return {k: (.99 if k == 'none' else .01 / max(n, 1)) for k in options}
    def ask(state, labels, question):
        if question == w.DOMAIN_QUESTION: return [.99]
        return [.01 for _ in labels]
    return w.Ports(model, lambda xs: [[0., 1.]] * len(xs), choose, ask), calls


class NamingLanguage(unittest.TestCase):
    def test_default_on_and_catalog_names_reach_the_naming_packet(self):
        p, calls = ports(['단말관리'])
        self.assertTrue(p.area_naming_context)
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '단말관리')
        self.assertEqual(calls[0]['catalog_names'], ['물류', '인사'])
        self.assertNotIn('language_retry', result)

    def test_wrong_script_is_re_asked_once_in_the_catalog_language(self):
        p, calls = ports(['Onboarding', '단말관리'])
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '단말관리')
        self.assertEqual(len(calls), 2)
        self.assertIn('E_AREA_LANGUAGE', calls[1]['language_feedback']); self.assertIn('Onboarding', calls[1]['language_feedback'])
        self.assertEqual(result['language_retry'], {'first': 'Onboarding', 'error': result['language_retry']['error'], 'second': '단말관리'})

    def test_still_wrong_after_one_re_ask_is_held(self):
        p, calls = ports(['Onboarding', 'Activation'])
        with self.assertRaisesRegex(w.PlacementHeld, 'E_AREA_LANGUAGE') as held:
            w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(held.exception.trace['language_retry']['second'], 'Activation')


if __name__ == '__main__':
    unittest.main()
