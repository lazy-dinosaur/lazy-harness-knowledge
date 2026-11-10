"""second_pass: units held at the domain step are placed once more against the catalog grown during the run."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw


def extract(units):
    rows = [{'record_id': 'R', 'index': i, 'unit': {'unit_id': u, 'content': u + ' 내용', 'target_expression': u, 'source_ranges': [[1, 1]]}, 'flags': f}
            for i, (u, f) in enumerate(units)]
    report = sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [{'record_id': 'R', 'path': 'p', 'sha256': 's'}],
                         'sources': {'R': '원문'}, 'rows': rows})
    return sw.approve(report, 'test approval')


def fake_place(kind, unit, domain, catalog, ports, k, document=None):
    names = [r['name'] for r in catalog]
    if kind == 'domain':
        if unit['unit_id'] == 'late-creator':
            return {'chosen': {'name': '입력환경', 'description': '공용 입력'}, 'new': True}
        if unit['unit_id'] == 'needs-shared' and '입력환경' in names:
            return {'chosen': {'name': '입력환경', 'description': '공용 입력'}}
        raise w.PlacementHeld('new domain requires explicit Jev none', {'catalog': names})
    return {'chosen': {'name': unit['target_expression'], 'subject_id': unit['unit_id'], 'description': 'd'}}


class SecondPass(unittest.TestCase):
    def setUp(self):
        self.ports = w.Ports(None, None, None, None)
        self.base = sw.snapshot('b', [{'name': '예약', 'description': '예약 업무', 'aliases': []}], {})
        self.ext = extract([('needs-shared', []), ('late-creator', []), ('never', []), ('flagged', ['judge_partial'])])

    def test_without_second_pass_the_early_unit_stays_held(self):
        with patch.object(w, 'place_identity', side_effect=fake_place):
            rep = sw.run_place(self.ports, self.ext, self.base)
        self.assertEqual(rep['rows'][0]['held']['reason'], 'new domain requires explicit Jev none')
        self.assertFalse(rep['second_pass'])

    def test_second_pass_places_the_early_unit_into_the_late_domain_once(self):
        calls = []
        def spy(*a, **k):
            calls.append((a[0], a[1]['unit_id'])); return fake_place(*a, **k)
        rows_seen = []
        with patch.object(w, 'place_identity', side_effect=spy):
            rep = sw.run_place(self.ports, self.ext, self.base, second_pass=True, on_row=lambda i, r, d, s: rows_seen.append(i))
        first = rep['rows'][0]
        self.assertIsNone(first['held']); self.assertEqual(first['domain']['chosen'], '입력환경')
        self.assertEqual(first['pass'], 2); self.assertEqual(first['first_hold']['where'], 'domain')
        self.assertEqual(rep['rows'][2]['held']['reason'], 'new domain requires explicit Jev none')  # still held, tried once more only
        self.assertEqual(rep['rows'][3]['held']['where'], 'extract')  # flagged units are never retried
        self.assertEqual(sum(1 for kind, u in calls if kind == 'domain' and u == 'never'), 2)
        self.assertNotIn(('domain', 'flagged'), calls)
        self.assertEqual(rows_seen, [0, 1, 2, 3, 0, 2])


if __name__ == '__main__':
    unittest.main()
