"""Co-retrieval grouping: domains a unit's search returned together are judged once per pair in the same save."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw
import upper_grouping as ug

CAT = [{'name': n, 'description': n + ' 업무'} for n in ('채팅', '고객', '설정', '예약')]


def ports(high):
    asked = []
    def ask(state, labels, question):
        a, b = state['proposed']['name'], state['candidate']['name']
        asked.append(frozenset((a, b)))
        return [.9 if frozenset((a, b)) in high else .1]
    p = w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask,
                worker=lambda role, packet: {'name': '소통', 'description': '채팅과 고객 응대를 묶는 업무군'})
    return p, asked


class CoRetrieval(unittest.TestCase):
    def test_pair_judged_once_and_grouped(self):
        p, asked = ports({frozenset(('채팅', '고객'))})
        groupings, cache = [], {}
        ev = ug.judge_co_retrieved(['채팅', '고객', '설정'], groupings, CAT, p, cache)
        self.assertEqual(groupings[0]['members'], ['채팅', '고객']); self.assertEqual(groupings[0]['origin'], 'co-retrieval')
        self.assertEqual([e['action'] for e in ev], ['created'])  # grouped members are not paired again
        n = len(asked)
        ug.judge_co_retrieved(['채팅', '고객', '설정', '예약'], groupings, CAT, p, cache)
        self.assertEqual(len(asked), n + 1)  # only the new ungrouped pair 설정/예약

    def test_cached_pair_is_not_asked_again(self):
        p, asked = ports(set())
        cache = {}
        ug.judge_co_retrieved(['설정', '예약'], [], CAT, p, cache)
        ug.judge_co_retrieved(['예약', '설정'], [], CAT, p, cache)
        self.assertEqual(len(asked), 1)

    def test_run_place_wires_it_and_is_off_by_default(self):
        doc = '줄 1\n'
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': {'unit_id': 'u0', 'content': '줄 1', 'target_expression': 'x', 'source_ranges': [[1, 1]]}}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': doc}, 'rows': rows}), 'test')
        base = sw.snapshot('b', CAT, {})
        def place(kind, unit, domain, catalog, pp, k, document=None):
            if kind == 'domain':
                return {'chosen': catalog[0], 'new': False, 'searches': [{'candidates': [{'name': '채팅'}, {'name': '고객'}]}]}
            return {'chosen': {'name': 's', 'subject_id': 's'}, 'new': True}
        p, asked = ports({frozenset(('채팅', '고객'))})
        with patch.object(sw.w, 'place_identity', place):
            on = sw.run_place(p, extract, base, upper_grouping=True, co_grouping=True)
            off = sw.run_place(p, extract, base, upper_grouping=True)
        self.assertEqual(on['groupings_after'][0]['members'], ['채팅', '고객'])
        self.assertEqual(on['rows'][0]['grouping']['co_retrieval'][0]['action'], 'created')
        self.assertEqual(off['groupings_after'], [])


if __name__ == '__main__':
    unittest.main()
