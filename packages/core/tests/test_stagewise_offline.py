"""Task376 stage 0: offline stagewise runner (original pipeline only, gated stages, snapshot carry-over)."""
import hashlib
import unittest
from dataclasses import replace

import stagewise as sw
import test_place_write_offline as previous

TEXT = ('주문관리파트의 일정표 시간은 환경설정의 포장단위에서 정해진다.\n'
    '주문관리파트의 일정표는 환경설정의 포장단위를 공유한다.\n검색은 Context retrieval로 기록을 조회한다.\n')
RECORD = {'record_id': 'R05', 'path': 'record.md', 'sha256': hashlib.sha256(TEXT.encode()).hexdigest(), 'text': TEXT}
BASE = sw.snapshot('baseline-0', [{'name': '주문관리', 'description': '예약을 다루는 업무 영역'}], {})


class Stagewise(unittest.TestCase):
    def ports(self):
        return previous.Fake(TEXT).ports()

    def extract(self):
        return sw.run_extract(self.ports(), [RECORD], host='fake-host')

    def test_baseline_refuses_sdk_rescue_roles(self):
        for field in sw.FORBIDDEN_PORT_FIELDS:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'SDK rescue role'):
                sw.run_extract(replace(self.ports(), **{field: object()}), [RECORD], host='fake-host')

    def test_extract_lists_every_unit_with_owner_and_ranges(self):
        report = self.extract()
        self.assertEqual([r['unit']['content'] for r in report['rows']], TEXT.splitlines())
        self.assertEqual([r['unit']['source_ranges'] for r in report['rows']], [[[1, 1]], [[2, 2]], [[3, 3]]])
        self.assertTrue(all(not r['flags'] for r in report['rows']))
        self.assertEqual(report['rows'][0]['unit']['source_text'], TEXT.splitlines()[0])

    def test_place_requires_user_passed_extract(self):
        with self.assertRaisesRegex(ValueError, 'not passed by the user'):
            sw.run_place(self.ports(), self.extract(), BASE)
        with self.assertRaisesRegex(ValueError, 'explicit user approval'):
            sw.approve(self.extract(), ' ')
        tampered = sw.approve(self.extract(), 'user-msg-1'); tampered['rows'] = []
        with self.assertRaisesRegex(ValueError, 'report drift'):
            sw.run_place(self.ports(), tampered, BASE)

    def test_place_reuses_existing_creates_by_rule_and_is_repeatable(self):
        extract = sw.approve(self.extract(), 'user-msg-1')
        first = sw.run_place(self.ports(), extract, BASE)
        self.assertEqual([r['domain']['chosen'] for r in first['rows']], ['주문관리', '주문관리', '검색'])
        self.assertEqual([r['domain']['new'] for r in first['rows']], [False, False, True])
        self.assertEqual([r['subject']['new'] for r in first['rows']], [True, False, True])
        self.assertTrue(all(r['held'] is None for r in first['rows']))
        second = sw.run_place(self.ports(), extract, BASE)
        self.assertEqual([(r['domain']['chosen'], r['subject']['chosen']) for r in second['rows']],
                         [(r['domain']['chosen'], r['subject']['chosen']) for r in first['rows']])

    def test_place_reports_every_unit_as_it_goes(self):
        extract = sw.approve(self.extract(), 'user-msg-1'); seen = []
        report = sw.run_place(self.ports(), extract, BASE, on_row=lambda i, row, d, s: seen.append((i, row['unit_id'], [x['name'] for x in d])))
        self.assertEqual([i for i, _, _ in seen], [0, 1, 2])
        self.assertEqual([u for _, u, _ in seen], [r['unit_id'] for r in report['rows']])
        self.assertEqual(seen[-1][2], ['주문관리', '검색'])

    def test_skip_units_hold_without_calls(self):
        extract = sw.approve(self.extract(), 'user-msg-1'); target = extract['rows'][1]['unit']['unit_id']
        report = sw.run_place(self.ports(), extract, BASE, skip_units={target: 'carried uncertain (fixture)'})
        self.assertEqual(report['rows'][1]['held'], {'where': 'carried', 'reason': 'carried uncertain (fixture)'})
        self.assertIsNone(report['rows'][0]['held'])

    def test_promoted_snapshot_carries_new_domain_and_subjects(self):
        extract = sw.approve(self.extract(), 'user-msg-1')
        place = sw.run_place(self.ports(), extract, BASE)
        with self.assertRaisesRegex(ValueError, 'not passed by the user'):
            sw.promote(BASE, place, 'baseline-1')
        nxt = sw.promote(BASE, sw.approve(place, 'user-msg-2'), 'baseline-1')
        self.assertEqual([d['name'] for d in nxt['domains']], ['주문관리', '검색'])
        self.assertEqual((nxt['parent'], nxt['approval']), ('baseline-0', 'user-msg-2'))
        again = sw.run_place(self.ports(), extract, nxt)
        self.assertEqual([r['domain']['new'] for r in again['rows']], [False, False, False])
        self.assertEqual([r['subject']['new'] for r in again['rows']], [False, False, False])
        drift = dict(nxt, domains=[]);
        with self.assertRaisesRegex(ValueError, 'snapshot drift'):
            sw.run_place(self.ports(), extract, drift)


class StagewiseWriting(unittest.TestCase):
    def setUp(self):
        import shutil, tempfile
        from pathlib import Path
        self.home = tempfile.mkdtemp(prefix='stagewise-test-', dir=Path(sw.__file__).parent)
        self.addCleanup(shutil.rmtree, self.home, True)

    def chain(self):
        ports = previous.Fake(TEXT).ports()
        extract = sw.approve(sw.run_extract(ports, [RECORD], host='fake-host'), 'u1')
        place = sw.approve(sw.run_place(ports, extract, BASE), 'u2')
        return ports, extract, place

    def test_write_and_ground_match_the_pipeline_without_holds(self):
        ports, extract, place = self.chain()
        with self.assertRaisesRegex(ValueError, 'not passed by the user'):
            sw.run_ground(ports, sw.run_write(ports, place, extract), place, extract, home=self.home)
        write = sw.approve(sw.run_write(ports, place, extract), 'u3')
        self.assertTrue(all(r['modality']['accepted'] for r in write['rows']))
        self.assertEqual([r['kind'] for r in write['rows']], ['fact', 'fact', 'fact'])
        ground = sw.run_ground(ports, write, place, extract, home=self.home)
        self.assertTrue(all(r['held'] is None for r in ground['rows']), [r['held'] for r in ground['rows']])
        self.assertIn('예약 시트', ground['rows'][0]['fact']['fact'])
        self.assertEqual(ground['rows'][0]['fact']['domain'], '주문관리')

    def test_leak_is_rewritten_once_then_held_if_persistent(self):
        _, extract, place = self.chain()
        leaky = previous.Fake(TEXT, leak=True, repair=False).ports()
        write = sw.approve(sw.run_write(leaky, place, extract), 'u3')
        ground = sw.run_ground(leaky, write, place, extract, home=self.home)
        row = ground['rows'][2]
        self.assertTrue(row['rewritten']); self.assertTrue(row['held'])
        fixed = previous.Fake(TEXT, leak=True).ports()
        ground = sw.run_ground(fixed, sw.approve(sw.run_write(fixed, place, extract), 'u3'), place, extract, home=self.home)
        self.assertTrue(ground['rows'][2]['rewritten']); self.assertIsNone(ground['rows'][2]['held'])


if __name__ == '__main__':
    unittest.main()
