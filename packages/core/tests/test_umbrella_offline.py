"""Task385 offline: an umbrella proposal becomes an upper-grouping candidate and the unit goes to the covered child."""
import unittest
import place_write as w

UNIT = {'host_id': 'h', 'unit_id': '00000000-0000-4000-8000-000000000385', 'content': '배송함은 배송품을 보관한다.',
        'source_ranges': [[1, 1]], 'target_expression': '배송함'}
CATALOG = [{'name': '물류', 'description': '배송·창고 업무'}, {'name': '인사', 'description': '직원 관리'}]


def ports(member_score, uses_score=.1, overlap=.6, umbrella=.1):
    def model(name, packet, task):
        return {'original_expression': '배송함', 'source_concept': '배송함', 'name': '운영', 'description': '배송·재고·인사를 포괄하는 영역'}
    def choose(kind, packet, options):
        n = len(options) - 1
        return {k: (.99 if k == 'none' else .01 / max(n, 1)) for k in options}
    def ask(state, labels, question):
        if question == w.DOMAIN_QUESTION: return [.99]
        if question == w.DOMAIN_COVERED_QUESTION: return [.01]
        if question == w.DOMAIN_OVERLAP_QUESTION: return [overlap if state['candidate']['name'] == '물류' else .01]
        if question == w.DOMAIN_UMBRELLA_QUESTION: return [umbrella]
        if question == w.DOMAIN_MEMBERSHIP_QUESTION: return [member_score if state['candidate']['name'] == '물류' else .05]
        if question == w.DOMAIN_USES_QUESTION: return [uses_score]
        if question == w.DOMAIN_SAME_QUESTION: return [.01]
        raise AssertionError('unexpected question ' + question[:40])
    return w.Ports(model, lambda xs: [[0., 0.]] * len(xs), choose, ask)


class Umbrella(unittest.TestCase):
    def test_umbrella_becomes_upper_candidate_and_unit_goes_to_child(self):
        result = w.place_identity('domain', UNIT, None, CATALOG, ports(.9), 5, document=UNIT['content'])
        self.assertEqual(result['chosen']['name'], '물류'); self.assertFalse(result.get('new'))
        self.assertEqual(result['umbrella_candidate']['name'], '운영')
        self.assertEqual([c['domain'] for c in result['umbrella_candidate']['covers']], ['물류'])
        self.assertEqual(result['umbrella_child'], '물류')

    def test_umbrella_without_owning_child_stays_hold(self):
        with self.assertRaisesRegex(w.PlacementHeld, 'no covered child owns the unit') as held:
            w.place_identity('domain', UNIT, None, CATALOG, ports(.4), 5, document=UNIT['content'])
        self.assertEqual(held.exception.trace['umbrella_candidate']['name'], '운영')


    def test_shared_kernel_that_is_only_used_is_created_with_a_uses_link(self):
        result = w.place_identity('domain', UNIT, None, CATALOG, ports(.4, uses_score=.9), 5, document=UNIT['content'])
        self.assertTrue(result.get('new')); self.assertEqual(result['chosen']['name'], '운영')
        self.assertEqual(result['context_map_uses'][0]['domain'], '물류')
        self.assertNotIn('umbrella_candidate', result)


    def test_weak_overlap_without_containment_is_noise_and_the_domain_is_created(self):
        result = w.place_identity('domain', UNIT, None, CATALOG, ports(.9, overlap=.35, umbrella=.2), 5, document=UNIT['content'])
        self.assertTrue(result.get('new')); self.assertEqual(result['chosen']['name'], '운영')
        self.assertNotIn('umbrella_candidate', result)

    def test_weak_overlap_with_containment_is_an_umbrella(self):
        result = w.place_identity('domain', UNIT, None, CATALOG, ports(.9, overlap=.35, umbrella=.8), 5, document=UNIT['content'])
        self.assertEqual(result['umbrella_candidate']['name'], '운영'); self.assertEqual(result['umbrella_child'], '물류')


if __name__ == '__main__':
    unittest.main()
