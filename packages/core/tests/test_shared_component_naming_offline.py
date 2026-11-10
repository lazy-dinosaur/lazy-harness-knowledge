"""S003 review A: a new domain named after a business that only uses the unit's shared component is re-asked once."""
import offline_guard  # noqa: F401
import unittest

import place_write as w

UNIT = {'host_id': 'h', 'unit_id': '00000000-0000-4000-8000-0000000000a1', 'content': '공통 자동완성은 입력을 300ms 모아 요청한다.',
        'source_ranges': [[1, 1]], 'target_expression': '공통 자동완성'}
CATALOG = [{'name': '물류', 'description': '배송·창고 업무'}, {'name': '인사', 'description': '직원 관리'}]


def ports(names, shared_by_name):
    calls = []
    def model(name, packet, task):
        calls.append(packet)
        n = names[min(len(calls), len(names)) - 1]
        return {'original_expression': '공통 자동완성', 'source_concept': '공통 자동완성', 'name': n, 'description': n + ' 맥락의 영역'}
    def choose(kind, packet, options):
        n = len(options) - 1
        return {k: (.99 if k == 'none' else .01 / max(n, 1)) for k in options}
    def ask(state, labels, question):
        if question == w.DOMAIN_QUESTION: return [.99]
        if question == w.DOMAIN_SHARED_COMPONENT_QUESTION: return [shared_by_name[state['proposed']['name']]]
        return [.01 for _ in labels]
    return w.Ports(model, lambda xs: [[0., 1.]] * len(xs), choose, ask, shared_component_check=True), calls


class SharedComponentNaming(unittest.TestCase):
    def test_business_owned_unit_is_not_re_asked(self):
        p, calls = ports(['주문'], {'주문': .1})
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '주문'); self.assertEqual(len(calls), 1)

    def test_consuming_business_name_is_re_asked_once_into_the_shared_context(self):
        p, calls = ports(['환자관리', '디자인시스템'], {'환자관리': .9, '디자인시스템': .1})
        result = w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '디자인시스템'); self.assertEqual(len(calls), 2)
        self.assertIn('E_DOMAIN_SHARED_COMPONENT', calls[1]['shared_component_feedback'])
        self.assertIn('환자관리', calls[1]['shared_component_feedback'])

    def test_still_a_consuming_business_after_one_re_ask_is_held(self):
        p, calls = ports(['환자관리', '진료운영'], {'환자관리': .9, '진료운영': .85})
        with self.assertRaisesRegex(w.PlacementHeld, 'still names a consuming business') as held:
            w.place_identity('domain', UNIT, None, CATALOG, p, 5, document=UNIT['content'])
        self.assertEqual(held.exception.trace['shared_component_check']['second'], '진료운영'); self.assertEqual(len(calls), 2)


    def test_production_ports_turn_the_check_on(self):
        import inspect
        self.assertIn('shared_component_check=True', inspect.getsource(w.production))
        self.assertFalse(w.Ports(None, None, None, None).shared_component_check)


if __name__ == '__main__':
    unittest.main()
