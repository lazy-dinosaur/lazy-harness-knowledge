"""Description refresh at save time (user requirement: descriptions are updated and managed as knowledge accumulates)."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw
import upper_grouping as ug

N = 7
DOC = '\n'.join('줄 %d' % i for i in range(1, N + 1)) + '\n'
BASE = sw.snapshot('base', [{'name': '기존', 'description': '기존 업무'}], {})


def extract(n=N):
    rows = [{'record_id': 'R', 'index': i, 'flags': [], 'unit': {'unit_id': 'u%d' % i, 'content': '줄 %d' % (i + 1), 'target_expression': '계정', 'source_ranges': [[i + 1, i + 1]]}}
            for i in range(n)]
    return sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': DOC}, 'rows': rows}), 'test')


def ports(overlap=.1):
    calls = []
    def worker(role, packet):
        calls.append((role, packet.get('name'), len(packet.get('fragments') or [])))
        return {'description': '%s: 단위 %d개를 보고 다시 쓴 설명' % (packet.get('name'), len(packet.get('fragments') or packet.get('domains') or []))}
    p = w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o},
                lambda s, l, q: [overlap if q == w.DOMAIN_OVERLAP_QUESTION else .1], worker=worker)
    return p, calls


def place(target_domain='계정'):
    def f(kind, unit, domain, catalog, p, k, document=None):
        if kind == 'domain':
            hit = next((c for c in catalog if c['name'] == target_domain), None)
            return {'chosen': hit, 'new': False} if hit else {'chosen': {'name': target_domain, 'description': '처음 설명'}, 'new': True}
        hit = next((c for c in catalog if c['name'] == '사용자'), None)
        return {'chosen': hit, 'new': False} if hit else {'chosen': {'name': '사용자', 'subject_id': 's1', 'description': '처음 주어'}, 'new': True}
    return f


class DescribeRefresh(unittest.TestCase):
    def test_new_domain_and_subject_are_rewritten_at_3_and_6_units(self):
        p, calls = ports()
        with patch.object(sw.w, 'place_identity', place()):
            report = sw.run_place(p, extract(), BASE, refresh_descriptions=True)
        ev = [(e['kind'], e['at_units'], e['accepted']) for e in report['description_events']]
        self.assertEqual(ev, [('domain', 3, True), ('subject', 3, True), ('domain', 6, True), ('subject', 6, True)])
        dom = next(d for d in report['domains_after'] if d['name'] == '계정')
        self.assertIn('단위 6개', dom['description']); self.assertEqual(dom['description_history'][0]['description'], '처음 설명')
        self.assertEqual(dom['vector'], [0., 1.])

    def test_base_snapshot_domain_keeps_its_description(self):
        p, calls = ports()
        with patch.object(sw.w, 'place_identity', place('기존')):
            report = sw.run_place(p, extract(), BASE, refresh_descriptions=True)
        self.assertEqual([e['kind'] for e in report['description_events']], ['subject', 'subject'])
        self.assertEqual(next(d for d in report['domains_after'] if d['name'] == '기존')['description'], '기존 업무')

    def test_a_domain_description_that_overlaps_others_is_rejected(self):
        p, _ = ports(overlap=.8)
        with patch.object(sw.w, 'place_identity', place()):
            report = sw.run_place(p, extract(3), BASE, refresh_descriptions=True)
        dom_ev = [e for e in report['description_events'] if e['kind'] == 'domain'][0]
        self.assertFalse(dom_ev['accepted']); self.assertIn('기존', dom_ev['reason'])
        self.assertEqual(next(d for d in report['domains_after'] if d['name'] == '계정')['description'], '처음 설명')

    def test_off_by_default(self):
        p, calls = ports()
        with patch.object(sw.w, 'place_identity', place()):
            report = sw.run_place(p, extract(), BASE)
        self.assertEqual(calls, []); self.assertNotIn('description_events', report)


class DescriptionShape(unittest.TestCase):
    def test_rule_summary_with_evidence_note_is_rejected(self):
        p, _ = ports()
        p = w.Ports(p.model, p.encode, p.choose, p.ask, worker=lambda role, packet: {'description': '계정은 번호 이력을 보존한다. 근거: src/user.ts'})
        with patch.object(sw.w, 'place_identity', place()):
            report = sw.run_place(p, extract(3), BASE, refresh_descriptions=True)
        self.assertTrue(all(not e['accepted'] and 'evidence' in e['reason'] for e in report['description_events']))
        self.assertEqual(next(d for d in report['domains_after'] if d['name'] == '계정')['description'], '처음 설명')

    def test_long_domain_description_is_rejected(self):
        p, _ = ports()
        p = w.Ports(p.model, p.encode, p.choose, p.ask, worker=lambda role, packet: {'description': '가' * 250})
        with patch.object(sw.w, 'place_identity', place()):
            report = sw.run_place(p, extract(3), BASE, refresh_descriptions=True)
        dom = [e for e in report['description_events'] if e['kind'] == 'domain'][0]
        self.assertFalse(dom['accepted']); self.assertIn('200', dom['reason'])

    def test_prompt_asks_for_scope_only(self):
        import active_worker as aw
        self.assertIn('범위', aw.PROMPTS['describe']); self.assertEqual(aw.PROMPT_VERSION, 'active-worker-prompt/13')


if __name__ == '__main__':
    unittest.main()
