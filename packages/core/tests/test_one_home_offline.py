"""One home per target at write time (one_home) and searchable upper groupings."""
import offline_guard  # noqa: F401
import copy
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw
import upper_grouping as ug

DOMAINS = [{'name': '계정', 'description': '사용자 계정'}, {'name': '고객', 'description': '고객 응대'}]
SUBJECTS = {'계정': [{'name': '사용자', 'subject_id': 's1', 'description': '병원 직원 계정', 'aliases': ['User']}]}
UNIT = {'unit_id': 'u1', 'content': '사용자 전화번호는 활성 사용자 안에서 유일하다.', 'target_expression': 'user', 'source_ranges': [[1, 1]]}


def ports(same):
    asked = []
    def ask(state, labels, question):
        asked.append(question); return [same]
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask), asked


class OneHome(unittest.TestCase):
    def test_same_entity_in_another_domain_is_the_home(self):
        p, asked = ports(.9)
        home = w.cross_domain_home(UNIT, DOMAINS, SUBJECTS, p)
        self.assertEqual(home['domain']['name'], '계정'); self.assertTrue(home['trace']['home']); self.assertEqual(asked, [w.SAME_QUESTION])

    def test_homonym_stays_on_the_normal_path(self):
        p, _ = ports(.2)
        home = w.cross_domain_home(UNIT, DOMAINS, SUBJECTS, p)
        self.assertIsNone(home['domain']); self.assertFalse(home['trace']['home'])

    def test_no_existing_subject_means_no_extra_question(self):
        p, asked = ports(.9)
        self.assertIsNone(w.cross_domain_home(dict(UNIT, target_expression='병원'), DOMAINS, SUBJECTS, p)); self.assertEqual(asked, [])

    def test_target_in_several_domains_is_not_forced(self):
        p, asked = ports(.9)
        subjects = dict(SUBJECTS, **{'고객': [{'name': '사용자', 'subject_id': 's2', 'description': '앱 사용자'}]})
        home = w.cross_domain_home(dict(UNIT, target_expression='사용자'), DOMAINS, subjects, p)
        self.assertIsNone(home['domain']); self.assertEqual(asked, [])

    def test_run_place_uses_the_home_and_skips_domain_search(self):
        doc = '사용자 전화번호는 활성 사용자 안에서 유일하다.\n'
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': copy.deepcopy(UNIT)}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': doc}, 'rows': rows}), 'test')
        base = sw.snapshot('base', DOMAINS, SUBJECTS)
        kinds = []
        def place(kind, unit, domain, catalog, p, k, document=None):
            kinds.append((kind, domain and domain['name']))
            return {'chosen': catalog[0], 'new': False}
        p, _ = ports(.9)
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(p, extract, base, one_home=True)
        self.assertEqual(kinds, [('subject', '계정')]); self.assertTrue(report['rows'][0]['one_home']['home'])
        self.assertEqual(report['rows'][0]['domain']['chosen'], '계정')


class SearchableGroupings(unittest.TestCase):
    def test_member_check_uses_a_vector_shortlist_when_many_groupings(self):
        groupings = [{'name': 'g%d' % i, 'description': 'd', 'members': ['m%d' % i], 'vector': [1., 0.] if i < 2 else [0., 1.]} for i in range(8)]
        seen = []
        def ask(state, labels, question):
            seen.append(state['candidate']['name']); return [.1]
        p = w.Ports(lambda n, pk, t: {}, lambda xs: [[1., 0.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask)
        ug.attach_new_domain({'name': 'x', 'description': 'x', 'vector': [1., 0.]}, groupings, [], p)
        self.assertEqual(len(seen), ug.TOP_K); self.assertEqual(seen[:2], ['g0', 'g1'])


if __name__ == '__main__':
    unittest.main()
