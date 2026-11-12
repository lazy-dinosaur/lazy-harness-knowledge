"""Upper grouping (user-accepted option A): attach at once, create from two members or an umbrella, never revise here."""
import offline_guard  # noqa: F401
import copy
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw
import upper_grouping as ug

CATALOG = [{'name': '예약', 'description': '예약 시간과 변경'}, {'name': '채팅', 'description': '채팅방과 메시지'}]


def ports(scores, name=None):
    """scores: {(question, proposed, candidate): value}; name: the group-name worker reply."""
    seen = []
    def ask(state, labels, question):
        key = (question, state['proposed']['name'], state['candidate']['name'])
        return [scores.get(key, .05)]
    def worker(role, packet):
        seen.append((role, copy.deepcopy(packet)))
        if isinstance(name, Exception): raise name
        return name
    p = w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, ask, worker=worker)
    return p, seen


class UpperGrouping(unittest.TestCase):
    def test_a_lone_new_domain_stays_ungrouped(self):
        p, seen = ports({})
        groupings = []
        event = ug.attach_new_domain({'name': '일정', 'description': '일정'}, groupings, CATALOG + [{'name': '일정', 'description': '일정'}], p)
        self.assertEqual(event['action'], 'none'); self.assertEqual(groupings, []); self.assertEqual(seen, [])

    def test_two_domains_that_belong_together_create_a_grouping(self):
        new = {'name': '일정', 'description': '캘린더 일정'}
        p, seen = ports({(ug.PAIR_QUESTION, '일정', '예약'): .9}, {'name': '시간관리', 'description': '예약과 일정처럼 시간을 다루는 업무군'})
        groupings = []
        event = ug.attach_new_domain(new, groupings, CATALOG + [new], p)
        self.assertEqual(event['action'], 'created')
        self.assertEqual(groupings, [{'name': '시간관리', 'description': '예약과 일정처럼 시간을 다루는 업무군', 'members': ['일정', '예약'], 'origin': 'pair'}])
        self.assertEqual(seen[0][0], 'group-name'); self.assertEqual([d['name'] for d in seen[0][1]['domains']], ['일정', '예약'])

    def test_a_new_domain_joins_an_existing_grouping_without_revising_it(self):
        groupings = [{'name': '시간관리', 'description': '시간 업무군', 'members': ['예약', '일정'], 'origin': 'pair'}]
        p, seen = ports({(ug.MEMBER_QUESTION, '연차', '시간관리'): .8})
        event = ug.attach_new_domain({'name': '연차', 'description': '연차'}, groupings, CATALOG, p)
        self.assertEqual(event['action'], 'attached'); self.assertEqual(groupings[0]['members'], ['예약', '일정', '연차'])
        self.assertEqual(groupings[0]['description'], '시간 업무군'); self.assertEqual(seen, [])

    def test_a_name_equal_to_a_domain_is_rejected_and_nothing_is_created(self):
        new = {'name': '일정', 'description': '일정'}
        p, _ = ports({(ug.PAIR_QUESTION, '일정', '예약'): .9}, {'name': '예약', 'description': '예약 묶음'})
        groupings = []
        event = ug.attach_new_domain(new, groupings, CATALOG + [new], p)
        self.assertEqual(event['action'], 'naming-rejected'); self.assertEqual(groupings, [])

    def test_umbrella_over_two_domains_becomes_a_grouping_and_over_one_stays_a_candidate(self):
        groupings = []
        one = ug.from_umbrella({'name': '의료행정', 'description': '의료 행정 업무', 'covers': [{'domain': '예약', 'score': .6}]}, groupings, CATALOG)
        self.assertEqual(one['action'], 'kept-as-candidate'); self.assertEqual(groupings, [])
        two = ug.from_umbrella({'name': '소통', 'description': '고객과 직원의 소통 업무', 'covers': [{'domain': '채팅', 'score': .6}, {'domain': '예약', 'score': .5}]}, groupings, CATALOG)
        self.assertEqual(two['action'], 'created'); self.assertEqual(groupings[0]['members'], ['채팅', '예약'])

    def test_run_place_records_groupings_and_promote_carries_them(self):
        doc = '# 일정\n일정은 주 단위로 본다.\n'
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': {'unit_id': 'u0', 'content': '일정은 주 단위로 본다.', 'target_expression': '일정', 'source_ranges': [[2, 2]]}}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': doc}, 'rows': rows}), 'test')
        base = sw.snapshot('base', CATALOG, {})
        new = {'name': '일정', 'description': '캘린더 일정'}
        def place(kind, unit, domain, catalog, p, k, document=None):
            return {'chosen': new, 'new': True} if kind == 'domain' else {'chosen': {'name': '주간 보기', 'subject_id': 's1'}, 'new': True}
        p, _ = ports({(ug.PAIR_QUESTION, '일정', '예약'): .9}, {'name': '시간관리', 'description': '시간을 다루는 업무군'})
        with patch.object(sw.w, 'place_identity', place):
            report = sw.run_place(p, extract, base, upper_grouping=True)
            plain = sw.run_place(p, extract, base)
        self.assertEqual(report['rows'][0]['grouping']['action'], 'created')
        self.assertEqual(report['groupings_after'][0]['members'], ['일정', '예약'])
        self.assertNotIn('groupings_after', plain); self.assertNotIn('grouping', plain['rows'][0])
        nxt = sw.promote(base, sw.approve(report, 'test'), 'next')
        self.assertEqual(nxt['groupings'][0]['name'], '시간관리')
        self.assertNotIn('groupings', base)


if __name__ == '__main__':
    unittest.main()
