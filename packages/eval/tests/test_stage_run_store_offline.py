"""Stage 6 in the runner: plan checks, both approvals passed, one store report per record (fake DB + fake store)."""
import offline_guard  # noqa: F401
import contextlib, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import stage_run as sr
import stagewise as sw
from safety import save, load, Stop


def plan(d, **over):
    p = {'run': 's', 'stage': 'store', 'host': 'h', 'records': [{'record_id': 'R1', 'path': 'R1'}],
         'extracts': {'R1': str(d/'x.json')}, 'places': {'R1': str(d/'p.json')}, 'grounds': {'R1': str(d/'g.json')},
         'place_approval': 'user p', 'ground_approval': 'user g', 'caps': {'sdk': 1, 'jev_http': 1, 'embedding_post': 1, 'embedding_inputs': 1},
         'cash_cap_usd': 0.05, 'absolute_ceiling_usd': 1.0, 'prior_spent_usd': 0.1, 'jev_reserve_usd': 0.0347, 'approval': 'test'}
    p.update(over); return p


class StoreStage(unittest.TestCase):
    def test_plan_checks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for over in ({'grounds': {}}, {'ground_approval': ''}, {'place_approval': ' '}):
                with self.assertRaises(Stop):
                    sr.check_plan(plan(d, **over), dry=True)
            sr.check_plan(plan(d), dry=True)

    def test_runner_passes_approvals_and_saves_a_store_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            save(d/'x.json', {'stage': 'extract'})
            save(d/'p.json', sw._sealed({'version': sw.VERSION, 'stage': 'place', 'rows': []}))
            save(d/'g.json', sw._sealed({'version': sw.VERSION, 'stage': 'ground', 'rows': []}))
            seen = {}
            @contextlib.contextmanager
            def fake_db(plan_, out):
                seen['db'] = True; yield 'dsn://fake'
            def fake_store(store, ground, place, extract, host=None):
                seen.update(g=ground.get('approval'), p=place.get('approval'), store=store, host=host)
                return {'sha': 's1', 'stage': 'store'}
            with patch.object(sw, 'run_store', fake_store):
                result = sr.run(plan(d), dry=True, ports_factory=lambda broker: None, out_dir=d/'out',
                                store_db=fake_db, store_factory=lambda dsn, host, plan_, broker: ('store', dsn, host))
            self.assertEqual(result['outcome'], 'completed')
            self.assertEqual((seen['g'], seen['p'], seen['host']), ('user g', 'user p', 'h'))
            self.assertEqual(seen['store'], ('store', 'dsn://fake', 'h'))
            self.assertEqual(load(d/'out'/'stage6-store-R1.json')['sha'], 's1')


if __name__ == '__main__':
    unittest.main()
