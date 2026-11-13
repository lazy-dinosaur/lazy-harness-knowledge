"""Warrant (user option A1): a new domain is created when a second unit proposes the same name; earlier holders join it."""
import offline_guard  # noqa: F401
import collections
import copy
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


def ports(overlap=None):
    def ask(state, labels, question):
        if overlap and question == w.DOMAIN_OVERLAP_QUESTION and state['candidate']['name'] == overlap: return [.8]
        if overlap and question == w.DOMAIN_UMBRELLA_QUESTION: return [.1]
        return [.1]
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask)


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
        raise w.PlacementHeld('new domain coverage is not clearly no (<0.3)', {'proposal': {'name': name, 'description': name + ' 업무', 'source_concept': name}, 'domain_criterion': {'score': .9, 'pass': name != '직함'}})
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


class WarrantGates(unittest.TestCase):
    """B: a warrant-created domain passes the same overlap / no-catch-all gates as normal creation."""
    def test_a_proposal_that_largely_overlaps_an_existing_domain_is_not_created(self):
        place, _ = placer({'일정': '진료일정', '헤더': '진료일정'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(overlap='온보딩'), extract(['일정', '헤더']), BASE, warrant=True)
        self.assertTrue(all(r.get('held') for r in report['rows']))
        self.assertEqual(report['rows'][1]['warrant']['blocked_by'], ['온보딩'])
        self.assertNotIn('진료일정', [d['name'] for d in report['domains_after']])

    def test_a_proposal_that_failed_the_business_area_criterion_does_not_count(self):
        place, _ = placer({'일정': '직함', '헤더': '직함'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['일정', '헤더']), BASE, warrant=True)
        self.assertTrue(all(r.get('held') and 'warrant' not in r for r in report['rows']))


class WarrantPassesAndResume(unittest.TestCase):
    """#24 gaps: the warrant counts second-pass holds and re-registers proposals of reused rows; a unit counts once."""
    def _place(self, script):
        seen = collections.Counter()
        def place(kind, unit, domain, catalog, p, k, document=None):
            if kind == 'subject':
                return {'chosen': {'name': unit['target_expression'], 'subject_id': unit['unit_id']}, 'new': True}
            seen[unit['unit_id']] += 1
            name = script[unit['unit_id']][min(seen[unit['unit_id']], len(script[unit['unit_id']])) - 1]
            raise w.PlacementHeld('new domain coverage is not clearly no (<0.3)', {'proposal': {'name': name, 'description': name + ' 업무', 'source_concept': name}, 'domain_criterion': {'score': .9, 'pass': True}})
        return place

    def test_second_pass_hold_counts_toward_the_warrant(self):
        place = self._place({'u0': ['계정'], 'u1': ['인사', '계정']})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['가', '나']), BASE, warrant=True, second_pass=True)
        self.assertEqual([r['domain']['chosen'] for r in report['rows']], ['계정', '계정'])

    def test_one_unit_held_twice_is_not_two_proposals(self):
        place = self._place({'u0': ['계정'], 'u1': ['인사']})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(ports(), extract(['가', '나']), BASE, warrant=True, second_pass=True)
        self.assertTrue(all(r.get('held') for r in report['rows']))

    def test_resumed_row_proposal_is_re_registered(self):
        place = self._place({'u0': ['계정'], 'u1': ['계정']})
        saved = {}
        with patch.object(sw.w, 'place_identity', place):
            sw.run_place(ports(), extract(['가']), BASE, on_row=lambda i, row, d, s, g: saved.__setitem__(i, {'row': copy.deepcopy(row), 'groupings': g}))
            report = sw.run_place(ports(), extract(['가', '나']), BASE, warrant=True, resume_rows={0: saved[0]})
        self.assertEqual([r['domain']['chosen'] for r in report['rows']], ['계정', '계정'])


if __name__ == '__main__':
    unittest.main()
