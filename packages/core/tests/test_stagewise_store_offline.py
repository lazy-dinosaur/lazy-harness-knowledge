"""Stage 6 core (user option A): store passed stage-5 facts with the placed domain/subject and re-find them."""
import offline_guard  # noqa: F401
import unittest

import stagewise as sw

DOM = {'name': '주문', 'description': 'd'}
SUB = {'name': '주문서', 'subject_id': 's1', 'description': 's'}


def reports(fact_domain='주문'):
    place = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'place', 'rows': [
        {'unit_id': 'u1', 'trace': {'domain': {'chosen': DOM}, 'subject': {'chosen': SUB}}},
        {'unit_id': 'u2', 'trace': {'domain': {'chosen': DOM}, 'subject': {'chosen': SUB}}},
        {'unit_id': 'u3', 'trace': {'domain': {'chosen': DOM}, 'subject': {'chosen': SUB}}}]}), 'p')
    ground = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'ground', 'rows': [
        {'unit_id': 'u1', 'fact': {'fact': '주문서는 결제 전에 취소할 수 있다.', 'domain': fact_domain}, 'held': None},
        {'unit_id': 'u2', 'fact': None, 'held': [{'code': 'E_X'}]},
        {'unit_id': 'u3', 'fact': {'fact': '주문서는 배송 후 잠긴다.', 'domain': fact_domain}, 'held': None}]}), 'g')
    extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'rows': []}), 'e')
    return ground, place, extract


class FakeStore:
    def __init__(self, hold=()):
        self.hold = set(hold); self.facts = {}; self.digested = []
    def install(self, rows):
        self.rows = rows
        return {r['unit_id']: 'collision' for r in rows if r['unit_id'] in self.hold}
    def record(self, uid, fact):
        self.facts[uid] = fact; return 'e-' + uid
    def digest(self, uid, domain):
        self.digested.append((uid, domain)); return {'decision': 'new'}
    def find(self, text, k):
        return [{'unit_id': u, 'domain': f['domain'], 'subject': '주문서', 'fact': f['fact']} for u, f in self.facts.items() if f['fact'] == text]


class StoreStage(unittest.TestCase):
    def test_stores_passed_facts_and_refinds_them(self):
        st = FakeStore()
        rep = sw.run_store(st, *reports(), host='h')
        self.assertEqual(rep['summary'], {'stored': 2, 'found': 2, 'path_ok': 2, 'not_stored': 1})
        self.assertEqual(st.digested, [('u1', '주문'), ('u3', '주문')])
        self.assertEqual([r['subject']['name'] for r in st.rows], ['주문서', '주문서'])

    def test_install_hold_is_not_recorded(self):
        st = FakeStore(hold={'u3'})
        rep = sw.run_store(st, *reports(), host='h')
        self.assertNotIn('u3', st.facts)
        self.assertEqual(rep['summary']['stored'], 1)

    def test_refuses_a_domain_that_differs_from_the_placement(self):
        with self.assertRaises(Exception):
            sw.run_store(FakeStore(), *reports(fact_domain='다른'), host='h')

    def test_needs_user_passed_reports(self):
        g, p, e = reports()
        g = dict(g); g.pop('approval')
        with self.assertRaises(Exception):
            sw.run_store(FakeStore(), g, p, e, host='h')


if __name__ == '__main__':
    unittest.main()
