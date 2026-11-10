"""paid-5 review: the coverage .3-.7 band asks membership in the top covering domain; the re-ask may reuse its name."""
import offline_guard  # noqa: F401
import unittest

import place_write as w

UNIT = {'host_id': 'h', 'unit_id': '00000000-0000-4000-8000-0000000000b1', 'content': '공통 자동완성은 입력을 300ms 모아 요청한다.',
        'source_ranges': [[1, 1]], 'target_expression': '공통 자동완성'}
CATALOG = [{'name': '물류', 'description': '배송·창고 업무'}, {'name': '디자인시스템', 'description': '공통 Button·Modal·입력 부품'}]


def ports(names, *, coverage=0.0, member=0.0, shared=None):
    calls = []; asked = []
    def model(name, packet, task):
        calls.append(packet)
        n = names[min(len(calls), len(names)) - 1]
        return {'original_expression': '공통 자동완성', 'source_concept': '공통 자동완성', 'name': n, 'description': n + ' 맥락의 영역'}
    def choose(kind, packet, options):
        n = len(options) - 1
        return {k: (.99 if k == 'none' else .01 / max(n, 1)) for k in options}
    def ask(state, labels, question):
        asked.append(question)
        if question == w.DOMAIN_QUESTION: return [.99]
        if question == w.DOMAIN_SHARED_COMPONENT_QUESTION: return [(shared or {}).get(state['proposed']['name'], .05)]
        if question == w.DOMAIN_COVERED_QUESTION:
            return [coverage if state['candidate']['name'] == '디자인시스템' else .01]
        if question == w.DOMAIN_MEMBERSHIP_QUESTION: return [member]
        return [.01 for _ in labels]
    return w.Ports(model, lambda xs: [[0., 1.]] * len(xs), choose, ask, shared_component_check=True), calls, asked


class CoverageBand(unittest.TestCase):
    def test_band_member_reuses_the_top_covering_domain(self):
        p, calls, asked = ports(['인터랙션'], coverage=.55, member=.9)
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '디자인시스템'); self.assertFalse(result.get('new'))
        self.assertEqual(result['coverage_band_membership']['candidate'], '디자인시스템')

    def test_band_non_member_still_holds(self):
        p, calls, asked = ports(['인터랙션'], coverage=.55, member=.3)
        with self.assertRaisesRegex(w.PlacementHeld, 'coverage is not clearly no') as held:
            w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertFalse(held.exception.trace['coverage_band_membership']['member'])

    def test_re_ask_reusing_the_existing_shared_name_is_the_existing_domain(self):
        p, calls, asked = ports(['진료', '디자인시스템'], shared={'진료': .9, '디자인시스템': .05})
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '디자인시스템'); self.assertFalse(result.get('new'))
        self.assertIn('reuse that exact name', calls[1]['shared_component_feedback'])


if __name__ == '__main__':
    unittest.main()
