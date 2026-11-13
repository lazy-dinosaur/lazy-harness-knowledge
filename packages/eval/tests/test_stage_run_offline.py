"""Stage runner: plan checks, dry-run refusal, counted caps, stage 1 through injected fake ports (no network, no host)."""
import offline_guard  # noqa: F401
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import stage_run as sr
import place_write as w
import safety
from safety import Stop

DOC = '# 주문\n주문은 결제 전에 취소할 수 있다.\n'


def plan(**over):
    p = {'run': 't1', 'stage': 'extract', 'host': 'knowledge-local', 'records': [{'record_id': 'S1', 'path': 'docs/order.md'}],
         'caps': {'sdk': 5, 'jev_http': 5, 'embedding_post': 5, 'embedding_inputs': 50},
         'cash_cap_usd': 0.05, 'absolute_ceiling_usd': 1.0, 'prior_spent_usd': 0.5, 'jev_reserve_usd': 0.0347, 'approval': 'test'}
    p.update(over); return p


def fake_ports(broker):
    """Production-shaped ports whose only provider is the broker-wrapped worker."""
    reply = {'items': [{'content': '주문은 결제 전에 취소할 수 있다.', 'target_expression': '주문', 'source_ranges': [[2, 2]]}]}
    worker = broker.worker_factory(None)
    return w.Ports(lambda n, p, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o},
                   lambda s, l, q: [.9 for _ in l], worker=worker)


class StageRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'v1/docs').mkdir(parents=True); (root / 'v1/docs/order.md').write_text(DOC)
        for m in (patch.object(sr, 'STATE_HOME', root), patch.object(safety, 'INPUTS_HOME', root)):
            m.start(); self.addCleanup(m.stop)
        self.root = root

    def test_plan_checks_refuse_before_any_effect(self):
        with self.assertRaisesRegex(Stop, 'absolute ceiling'): sr.check_plan(plan(prior_spent_usd=0.99), dry=False)
        with self.assertRaisesRegex(Stop, 'approval'): sr.check_plan(plan(approval=''), dry=False)
        self.assertTrue(sr.check_plan(plan(approval=''), dry=True))
        with self.assertRaisesRegex(Stop, 'caps'): sr.check_plan(plan(caps={'sdk': 1}), dry=True)
        with self.assertRaisesRegex(Stop, 'place needs'): sr.check_plan(plan(stage='place'), dry=True)

    def test_dry_run_refuses_the_first_provider_attempt(self):
        calls = []
        broker = sr.Broker(plan(), dry=True, worker_factory=lambda paid: (lambda role, packet: calls.append(role) or {}))
        result = sr.run(plan(), dry=True, ports_factory=fake_ports, broker=broker, out_dir=self.root / 'out')
        self.assertTrue(result['outcome'].startswith('DRYRUN'), result['outcome'])
        self.assertEqual(calls, []); self.assertEqual(result['accounting']['used']['sdk'], 0)
        self.assertTrue((self.root / 'out/accounting-dryrun.json').exists())

    def test_paid_path_counts_calls_and_writes_the_report(self):
        reply = {'items': [{'content': '주문은 결제 전에 취소할 수 있다.', 'target_expression': '주문', 'source_ranges': [[2, 2]]}]}
        broker = sr.Broker(plan(), dry=False, worker_factory=lambda paid: (lambda role, packet: reply))
        result = sr.run(plan(), dry=False, ports_factory=fake_ports, broker=broker, out_dir=self.root / 'out')
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertEqual(result['accounting']['used']['sdk'], 1)
        report = json.loads((self.root / 'out/stage1-extract-S1.json').read_text())
        self.assertEqual(report['rows'][0]['unit']['target_expression'], '주문')

    def test_cap_stops_before_effect(self):
        calls = []
        p = plan(caps={'sdk': 0, 'jev_http': 5, 'embedding_post': 5, 'embedding_inputs': 50})
        broker = sr.Broker(p, dry=False, worker_factory=lambda paid: (lambda role, packet: calls.append(role) or {}))
        result = sr.run(p, dry=False, ports_factory=fake_ports, broker=broker, out_dir=self.root / 'out')
        self.assertIn('cap reached before effect: sdk', result['outcome']); self.assertEqual(calls, [])

    def test_judgement_transport_is_counted_and_charged_from_the_reply(self):
        import io
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        broker = sr.Broker(plan(), dry=False, judgement_http=lambda req, timeout=60: Reply(json.dumps({'answers': {}, 'usage': {'cost': 0.0002}}).encode()))
        body = broker.judgement('request')
        self.assertEqual(json.loads(body.read())['usage']['cost'], 0.0002)
        self.assertEqual(broker.used['jev_http'], 1); self.assertAlmostEqual(broker.cash, 0.0002)
        broker.cash = 0.049
        with self.assertRaisesRegex(Stop, 'run cash cap'): broker.judgement('request')


    def test_reply_without_known_cost_stops_the_run(self):
        import io
        class Reply(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        for body in ({'answers': {}}, {'answers': {}, 'cost_usd': 0.0002}, {'answers': {}, 'usage': {'cost': None}}):
            broker = sr.Broker(plan(), dry=False, judgement_http=lambda req, timeout=60, b=body: Reply(json.dumps(b).encode()))
            with self.assertRaisesRegex(Stop, 'without a known usage.cost'): broker.judgement('request')
            self.assertEqual(broker.accounting()['unknown_cost_calls'], 1)


class Safety(unittest.TestCase):
    """#24 external review: no Jev re-send, one cash cap per run name across restarts, nonzero exit on a stop."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name); (root / 'v1/docs').mkdir(parents=True); (root / 'v1/docs/order.md').write_text(DOC)
        for m in (patch.object(sr, 'STATE_HOME', root), patch.object(safety, 'INPUTS_HOME', root)):
            m.start(); self.addCleanup(m.stop)
        self.root = root
        self.reply = {'items': [{'content': '주문은 결제 전에 취소할 수 있다.', 'target_expression': '주문', 'source_ranges': [[2, 2]]}]}

    def test_lost_jev_reply_counts_the_reserve_and_stops_without_resend(self):
        sends = []
        def boom(req, timeout=60):
            sends.append(1); raise TimeoutError('read timed out')
        broker = sr.Broker(plan(), dry=False, judgement_http=boom)
        with self.assertRaisesRegex(Stop, 'outcome unknown'): broker.judgement('request')
        self.assertEqual(sends, [1]); self.assertAlmostEqual(broker.cash, 0.0347); self.assertEqual(broker.uncertain, 1)

    def test_core_judgement_paths_send_once_through_the_broker(self):
        import capture_audit as ca, worker_tools as wt
        sends = []
        def boom(req, timeout=60):
            sends.append(1); raise OSError('connection reset')
        # A cap with room for two reserves: after the first uncertain send the second path must still send once only.
        broker = sr.Broker(plan(cash_cap_usd=0.2), dry=False, judgement_http=boom); broker.install(); self.addCleanup(sr.Broker.uninstall)
        cfg = {'jev_base_url': 'http://jev.invalid', 'jev_api_key': 'k', 'jev_model': 'm'}
        with self.assertRaises(Stop): ca.request_answers(cfg, {'questions': {'m0': {}}})
        with self.assertRaises(Stop): wt.make_ask(cfg)({}, ['a'], 'q {i}')
        self.assertEqual(sends, [1, 1])

    def test_existing_accounting_refuses_a_paid_rerun_without_resume(self):
        out = self.root / 'out'; out.mkdir()
        before = {'outcome': 'completed', 'accounting': {'used': {'sdk': 3, 'jev_http': 0, 'embedding_post': 0, 'embedding_inputs': 0}, 'cash_usd': 0.03}}
        (out / 'accounting.json').write_text(json.dumps(before))
        calls = []
        broker = sr.Broker(plan(), dry=False, worker_factory=lambda paid: (lambda role, packet: calls.append(role) or self.reply))
        result = sr.run(plan(), dry=False, ports_factory=fake_ports, broker=broker, out_dir=out)
        self.assertIn('already has accounting', result['outcome']); self.assertEqual(calls, [])
        self.assertEqual(json.loads((out / 'accounting.json').read_text()), before)

    def test_resume_continues_cash_and_counts_an_in_flight_reserve(self):
        out = self.root / 'out'; out.mkdir()
        live = {'used': {'sdk': 1, 'jev_http': 2, 'embedding_post': 0, 'embedding_inputs': 0}, 'cash_usd': 0.01,
                'unknown_cost_calls': 0, 'uncertain_calls': 0, 'pending_reserve_usd': 0.0347}
        (out / 'accounting-live.json').write_text(json.dumps(live))
        broker = sr.Broker(plan(resume=True), dry=False, worker_factory=lambda paid: (lambda role, packet: self.reply))
        result = sr.run(plan(resume=True), dry=False, ports_factory=fake_ports, broker=broker, out_dir=out)
        self.assertEqual(result['outcome'], 'completed', result)
        self.assertAlmostEqual(result['accounting']['cash_usd'], 0.0447); self.assertEqual(result['accounting']['used']['sdk'], 2)
        self.assertEqual(json.loads((out / 'accounting-live.json').read_text())['used']['sdk'], 2)

    def test_cli_exit_code_is_nonzero_on_a_stop(self):
        for outcome, dry, code in (('completed', False, 0), ('DRYRUN: provider attempt refused (sdk)', True, 0),
                                   ('run cash cap reached before effect', False, 2)):
            with patch.object(sr, 'load', return_value={}), patch.object(sr, 'production_ports', return_value=None), \
                    patch.object(sr, 'run', return_value={'outcome': outcome}), patch('builtins.print'):
                with self.assertRaises(SystemExit) as exc: sr.main(['--plan', 'x'] + (['--dry-run'] if dry else []))
            self.assertEqual(exc.exception.code, code, outcome)


class ResumeRecords(unittest.TestCase):
    """#24 runner defects: a resumed place run skips finished records, and groupings carry between records."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for m in (patch.object(sr, 'STATE_HOME', root), patch.object(safety, 'INPUTS_HOME', root)):
            m.start(); self.addCleanup(m.stop)
        self.root = root

    def test_finished_record_is_skipped_and_groupings_carry(self):
        import stagewise as sw
        out = self.root / 'out'; out.mkdir()
        base = sw.snapshot('b', [{'name': '기존', 'description': '기존'}], {})
        (self.root / 'base.json').write_text(json.dumps(base))
        for rid in ('A', 'B'): (self.root / ('ex-%s.json' % rid)).write_text('{}')
        done = {'sha': 'shaA', 'domains_after': base['domains'], 'subjects_after': {}, 'groupings_after': [{'name': 'g', 'description': 'd', 'members': ['기존', 'x']}]}
        (out / 'stage2-place-A.json').write_text(json.dumps(done))
        (out / 'accounting-live.json').write_text(json.dumps({'used': {'sdk': 0, 'jev_http': 0, 'embedding_post': 0, 'embedding_inputs': 0}, 'cash_usd': 0.0}))
        seen = []
        def run_place(ports, extract_report, base, **kw):
            seen.append((base.get('groupings'), kw.get('resume_rows')))
            return {'sha': 'shaB', 'domains_after': base['domains'], 'subjects_after': {}, 'groupings_after': base.get('groupings')}
        p = plan(stage='place', resume=True, extracts={'A': str(self.root / 'ex-A.json'), 'B': str(self.root / 'ex-B.json')}, base=str(self.root / 'base.json'))
        with patch.object(sw, 'run_place', run_place):
            result = sr.run(p, dry=False, ports_factory=lambda b: None, broker=sr.Broker(p, dry=False), out_dir=out)
        self.assertEqual(result['outcome'], 'completed', result); self.assertEqual(result['reports'], {'A': 'shaA', 'B': 'shaB'})
        self.assertEqual(len(seen), 1)  # only B ran
        self.assertEqual(seen[0][0][0]['name'], 'g'); self.assertEqual(seen[0][1], {})


if __name__ == '__main__':
    unittest.main()
