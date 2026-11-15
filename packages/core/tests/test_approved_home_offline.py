"""User option A after p2-warm-2: a plain-word target takes the exact-name home only inside a user-approved base catalog."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw

DOMAINS = [{'name': '디자인시스템', 'description': '공용 입력 요소'}, {'name': '계정', 'description': '사용자 계정'}]
SUBJECTS = {'디자인시스템': [{'name': '자동완성', 'subject_id': 'a1', 'description': '자동완성', 'aliases': ['Autocomplete']}],
            '계정': [{'name': '사용자', 'subject_id': 'u1', 'description': '사용자', 'aliases': ['User']}]}
UNIT = {'unit_id': 'x', 'content': 'c', 'target_expression': 'autocomplete', 'source_ranges': [[1, 1]]}
APPROVED = {('디자인시스템', 'a1'), ('계정', 'u1')}


class ApprovedHome(unittest.TestCase):
    def test_plain_word_matching_an_approved_subject_is_the_home(self):
        home = w.cross_domain_home(UNIT, DOMAINS, SUBJECTS, approved=APPROVED)
        self.assertEqual(home['domain']['name'], '디자인시스템')
        self.assertEqual(home['trace']['rule'], 'exact-name-approved')

    def test_plain_word_without_approval_keeps_normal_placement(self):
        self.assertIsNone(w.cross_domain_home(UNIT, DOMAINS, SUBJECTS))

    def test_plain_word_matching_only_an_in_run_subject_keeps_normal_placement(self):
        subjects = dict(SUBJECTS, **{'디자인시스템': [dict(SUBJECTS['디자인시스템'][0], subject_id='new')]})
        self.assertIsNone(w.cross_domain_home(UNIT, DOMAINS, subjects, approved=APPROVED))

    def test_code_like_rule_unchanged(self):
        subjects = dict(SUBJECTS, **{'계정': [dict(SUBJECTS['계정'][0], aliases=['UserAccount'])]})
        home = w.cross_domain_home(dict(UNIT, target_expression='UserAccount'), DOMAINS, subjects)
        self.assertEqual(home['domain']['name'], '계정'); self.assertEqual(home['trace']['rule'], 'exact-name')

    def test_run_place_passes_approved_homes_only_for_an_approved_base(self):
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': dict(UNIT, unit_id='u0')}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': '줄\n'}, 'rows': rows}), 'test')
        p = w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, lambda s, l, q: [.1])
        seen = []
        def home(unit, domains, subjects, ports, counts=None, approved=None):
            seen.append(approved); return None
        def place(kind, unit, domain, catalog, ports, k, document=None):
            return {'chosen': catalog[0] if catalog else {'name': 's', 'subject_id': 's'}, 'new': not catalog}
        for approval, expect in ((None, None), ('user ok', APPROVED)):
            base = sw.snapshot('b', DOMAINS, SUBJECTS, approval=approval)
            seen.clear()
            with patch.object(sw.w, 'cross_domain_home', home), patch.object(sw.w, 'place_identity', place):
                sw.run_place(p, extract, base, one_home=True)
            self.assertEqual(seen[0], expect)


if __name__ == '__main__':
    unittest.main()
