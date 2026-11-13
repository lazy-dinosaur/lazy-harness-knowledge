"""Warrant (user option A1): a new domain is created when a second unit proposes the same name; earlier holders join it."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw

DOC = '\n'.join('줄 %d' % i for i in range(1, 6)) + '\n'


def extract(targets):
    rows = [{'record_id': 'R', 'index': i, 'flags': [], 'unit': {'unit_id': 'u%d' % i, 'content': '줄 %d' % (i + 1), 'target_expression': t, 'source_ranges': [[i + 1, i + 1]]}}
            for i, t in enumerate(targets)]
    return sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': DOC}, 'rows': rows}), 'test')


BASE = sw.snapshot('base', [{'name': '온보딩', 'description': '서비스 시작'}], {})


def ports():
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, lambda s, l, q: [.1])


def placer(proposals):
    """proposals: target -> proposed new-domain name held in the coverage band (None = place into 온보딩)."""
    calls = []
    def place(kind, unit, domain, catalog, p, k, document=None):
        calls.append((kind, unit['unit_id'], domain and domain['name']))
        if kind == 'subject':
            return {'chosen': {'name': unit['target_expression'], 'subject_id': unit['unit_id']}, 'new': True}
        name = proposals.get(unit['target_expression'])
        if name is None:
            return {'chosen': catalog[0], 'new': False}
        raise w.PlacementHeld('new domain coverage is not clearly no (<0.3)', {'proposal': {'name': name, 'description': name + ' 업무', 'source_concept': name}})
    return place, calls


class Warrant(unittest.TestCase):
    def test_second_same_proposal_creates_the_domain_and_places_both(self):
        place, calls = placer({'전화번호': '계정', '직원': '계정'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['전화번호', '코드', '직원']), BASE, warrant=True)
        rows = report['rows']
        self.assertEqual([r['domain']['chosen'] if not r.get('held') else 'HOLD' for r in rows], ['계정', '온보딩', '계정'])
        self.assertEqual(rows[0]['warrant']['members'], [0, 2]); self.assertTrue(rows[0]['domain']['new'])
        self.assertIn('계정', [d['name'] for d in report['domains_after']])
        self.assertEqual(rows[0]['warrant']['first_hold']['where'], 'domain')

    def test_a_single_proposal_stays_held_and_different_names_do_not_combine(self):
        place, _ = placer({'전화번호': '계정', '직원': '인사'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['전화번호', '직원']), BASE, warrant=True)
        self.assertTrue(all(r.get('held') for r in report['rows']))
        self.assertEqual([r['warrant'] for r in report['rows']], [{'name': '계정', 'pending': 1}, {'name': '인사', 'pending': 1}])

    def test_off_by_default(self):
        place, _ = placer({'전화번호': '계정', '직원': '계정'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['전화번호', '직원']), BASE)
        self.assertTrue(all(r.get('held') for r in report['rows'])); self.assertNotIn('warrant', report)


if __name__ == '__main__':
    unittest.main()
