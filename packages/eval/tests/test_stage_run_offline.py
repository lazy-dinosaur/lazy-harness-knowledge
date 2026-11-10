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
        broker = sr.Broker(plan(), dry=False, judgement_http=lambda req, timeout=60: Reply(json.dumps({'answers': {}, 'cost_usd': 0.0002}).encode()))
        body = broker.judgement('request')
        self.assertEqual(json.loads(body.read())['cost_usd'], 0.0002)
        self.assertEqual(broker.used['jev_http'], 1); self.assertAlmostEqual(broker.cash, 0.0002)
        broker.cash = 0.049
        with self.assertRaisesRegex(Stop, 'run cash cap'): broker.judgement('request')


if __name__ == '__main__':
    unittest.main()
