"""run_place(resume_rows=...): saved first-pass rows are reused without calls and their catalog effects replayed."""
import offline_guard  # noqa: F401
import copy
import unittest
from unittest.mock import patch

import stagewise as sw
import place_write as w

DOC = '가는 1이다.\n나는 2다.\n다는 3이다.\n'


def extract():
    rows = [{'record_id': 'R', 'index': i, 'flags': [], 'unit': {'unit_id': 'u%d' % i, 'content': c, 'target_expression': t, 'source_ranges': [[i + 1, i + 1]]}}
            for i, (t, c) in enumerate([('가', '가는 1이다.'), ('나', '나는 2다.'), ('다', '다는 3이다.')])]
    return sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': DOC}, 'rows': rows}), 'test')


def ports():
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, lambda s, l, q: [.1])


class ResumeRows(unittest.TestCase):
    def test_saved_rows_are_reused_and_their_new_domain_is_in_the_catalog(self):
        base = sw.snapshot('base', [{'name': '기존', 'description': '기존'}], {})
        calls = []
        def place(kind, unit, domain, catalog, p, k, document=None):
            calls.append((kind, unit['unit_id'], [c['name'] for c in catalog]))
            if kind == 'domain':
                return {'chosen': {'name': '새', 'description': '새 영역'}, 'new': True} if unit['unit_id'] == 'u0' else {'chosen': catalog[-1], 'new': False}
            return {'chosen': {'name': unit['target_expression'], 'subject_id': unit['unit_id']}, 'new': True}
        saved = {}
        with patch.object(sw.w, 'place_identity', place):
            full = sw.run_place(ports(), extract(), base, on_row=lambda i, row, d, s, g: saved.__setitem__(i, {'row': copy.deepcopy(row), 'groupings': copy.deepcopy(g)}))
            calls.clear()
            partial = {0: saved[0], 1: saved[1]}
            again = sw.run_place(ports(), extract(), base, resume_rows=partial)
        self.assertEqual([c[1] for c in calls], ['u2', 'u2'])  # only the unfinished unit is placed
        self.assertIn('새', calls[0][2])  # the domain created by a reused row is in the catalog
        self.assertEqual([r['unit_id'] for r in again['rows']], ['u0', 'u1', 'u2'])
        self.assertEqual([d['name'] for d in again['domains_after']], [d['name'] for d in full['domains_after']])

    def test_a_saved_row_of_another_unit_is_not_reused(self):
        base = sw.snapshot('base', [{'name': '기존', 'description': '기존'}], {})
        calls = []
        def place(kind, unit, domain, catalog, p, k, document=None):
            calls.append(unit['unit_id']); return {'chosen': catalog[0], 'new': False} if kind == 'domain' else {'chosen': {'name': 'x', 'subject_id': 'x'}, 'new': True}
        with patch.object(sw.w, 'place_identity', place):
            sw.run_place(ports(), extract(), base, resume_rows={0: {'row': {'unit_id': 'other', 'index': 0}}})
        self.assertIn('u0', calls)


if __name__ == '__main__':
    unittest.main()
