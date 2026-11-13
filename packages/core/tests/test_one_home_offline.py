"""One home per target at write time: deterministic exact-name rule (no judgement call)."""
import offline_guard  # noqa: F401
import copy
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw

DOMAINS = [{'name': '계정', 'description': '사용자 계정'}, {'name': '고객', 'description': '고객 응대'}]
SUBJECTS = {'계정': [{'name': '사용자', 'subject_id': 's1', 'description': '병원 직원 계정', 'aliases': ['User']}]}
UNIT = {'unit_id': 'u1', 'content': '사용자 전화번호는 활성 사용자 안에서 유일하다.', 'target_expression': 'user', 'source_ranges': [[1, 1]]}


def ports():
    asked = []
    def ask(state, labels, question):
        asked.append(question); return [.1]
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask), asked


class OneHome(unittest.TestCase):
    def test_exact_name_or_alias_is_the_home_without_a_question(self):
        p, asked = ports()
        home = w.cross_domain_home(UNIT, DOMAINS, SUBJECTS, p)
        self.assertEqual(home['domain']['name'], '계정'); self.assertEqual(home['trace']['chosen_by'], 'only-home'); self.assertEqual(asked, [])

    def test_no_existing_subject_means_normal_placement(self):
        self.assertIsNone(w.cross_domain_home(dict(UNIT, target_expression='병원'), DOMAINS, SUBJECTS))

    def test_several_domains_choose_the_one_with_most_units_then_catalog_order(self):
        subjects = dict(SUBJECTS, **{'고객': [{'name': 'User', 'subject_id': 's2', 'description': '앱 사용자'}]})
        many = w.cross_domain_home(UNIT, DOMAINS, subjects, counts={('고객', 'User'): 5, ('계정', '사용자'): 2})
        self.assertEqual((many['domain']['name'], many['trace']['chosen_by']), ('고객', 'most-units'))
        tie = w.cross_domain_home(UNIT, DOMAINS, subjects, counts={})
        self.assertEqual(tie['domain']['name'], '계정')

    def test_run_place_uses_the_home_and_counts_units(self):
        doc = '사용자 전화번호는 활성 사용자 안에서 유일하다.\n'
        rows = [{'record_id': 'R', 'index': i, 'flags': [], 'unit': dict(copy.deepcopy(UNIT), unit_id='u%d' % i)} for i in range(2)]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': doc}, 'rows': rows}), 'test')
        base = sw.snapshot('base', DOMAINS, SUBJECTS)
        kinds = []
        def place(kind, unit, domain, catalog, p, k, document=None):
            kinds.append((kind, domain and domain['name']))
            return {'chosen': catalog[0], 'new': False}
        p, asked = ports()
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(p, extract, base, one_home=True)
        self.assertEqual(kinds, [('subject', '계정'), ('subject', '계정')]); self.assertEqual(asked, [])
        self.assertEqual(report['rows'][1]['one_home']['hits'][0]['units'], 1)


if __name__ == '__main__':
    unittest.main()
