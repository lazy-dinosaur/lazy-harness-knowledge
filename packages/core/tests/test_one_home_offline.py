"""One home per target at write time: deterministic exact-name rule for code-like names only."""
import offline_guard  # noqa: F401
import copy
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw

DOMAINS = [{'name': '단말', 'description': '단말 설치'}, {'name': '온보딩', 'description': '서비스 시작'}]
SUBJECTS = {'단말': [{'name': '설치 코드', 'subject_id': 's1', 'description': '직원 단말 설치 코드', 'aliases': ['InstallationCode']}],
            '온보딩': [{'name': '사용자', 'subject_id': 's2', 'description': '사용자', 'aliases': ['User']}]}
UNIT = {'unit_id': 'u1', 'content': 'InstallationCode 는 6자리다.', 'target_expression': '`InstallationCode`', 'source_ranges': [[1, 1]]}


def ports():
    asked = []
    def ask(state, labels, question):
        asked.append(question); return [.1]
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask), asked


class CodeLike(unittest.TestCase):
    def test_identifier_shapes(self):
        for name in ('InstallationCode', '`CodeInput`', '03080614', 'EmrSyncService.ts#resolvePatient', 'POST /api/v1/activate', 'is_active', 'HTTPClient'):
            self.assertTrue(w.code_like(name), name)
        for name in ('User', 'patient', '병원', '사용자', 'Homepage issued activation codes', '캘린더 일정'):
            self.assertFalse(w.code_like(name), name)


class OneHome(unittest.TestCase):
    def test_code_like_exact_name_is_the_home_without_a_question(self):
        p, asked = ports()
        home = w.cross_domain_home(UNIT, DOMAINS, SUBJECTS, p)
        self.assertEqual(home['domain']['name'], '단말'); self.assertEqual(home['trace']['chosen_by'], 'only-home'); self.assertEqual(asked, [])

    def test_plain_word_takes_normal_placement(self):
        self.assertIsNone(w.cross_domain_home(dict(UNIT, target_expression='User'), DOMAINS, SUBJECTS))

    def test_no_existing_subject_means_normal_placement(self):
        self.assertIsNone(w.cross_domain_home(dict(UNIT, target_expression='CodeInput'), DOMAINS, SUBJECTS))

    def test_several_domains_choose_the_one_with_most_units_then_catalog_order(self):
        subjects = dict(SUBJECTS, **{'온보딩': [{'name': 'InstallationCode', 'subject_id': 's3', 'description': '설치 코드'}]})
        many = w.cross_domain_home(UNIT, DOMAINS, subjects, counts={('온보딩', 'InstallationCode'): 5, ('단말', '설치 코드'): 2})
        self.assertEqual((many['domain']['name'], many['trace']['chosen_by']), ('온보딩', 'most-units'))
        tie = w.cross_domain_home(UNIT, DOMAINS, subjects, counts={})
        self.assertEqual(tie['domain']['name'], '단말')

    def test_run_place_uses_the_home_and_counts_units(self):
        doc = 'InstallationCode 는 6자리다.\n'
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
        self.assertEqual(kinds, [('subject', '단말'), ('subject', '단말')]); self.assertEqual(asked, [])
        self.assertEqual(report['rows'][1]['one_home']['hits'][0]['units'], 1)


if __name__ == '__main__':
    unittest.main()
