"""Stage 5 in the runner: user-passed place AND write reports in, one ground report per record, scratch home removed."""
import offline_guard  # noqa: F401
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import stage_run as sr
import stagewise as sw
from safety import save, load, Stop


def plan(d, **over):
    p = {'run': 'g', 'stage': 'ground', 'host': 'h', 'records': [{'record_id': 'R1', 'path': 'R1'}],
         'extracts': {'R1': str(d/'x.json')}, 'places': {'R1': str(d/'p.json')}, 'writes': {'R1': str(d/'w.json')},
         'place_approval': 'user p', 'write_approval': 'user w', 'caps': {'sdk': 1, 'jev_http': 1, 'embedding_post': 1, 'embedding_inputs': 1},
         'cash_cap_usd': 0.05, 'absolute_ceiling_usd': 1.0, 'prior_spent_usd': 0.1, 'jev_reserve_usd': 0.0347, 'approval': 'test'}
    p.update(over); return p


class GroundStage(unittest.TestCase):
    def test_plan_checks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for over in ({'writes': {}}, {'write_approval': ''}, {'place_approval': ' '}):
                with self.assertRaises(Stop):
                    sr.check_plan(plan(d, **over), dry=True)
            sr.check_plan(plan(d), dry=True)

    def test_runner_passes_both_approvals_and_removes_the_scratch_home(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            save(d/'x.json', {'stage': 'extract'})
            save(d/'p.json', sw._sealed({'version': sw.VERSION, 'stage': 'place', 'rows': []}))
            save(d/'w.json', sw._sealed({'version': sw.VERSION, 'stage': 'write', 'rows': []}))
            seen = {}; scratch = d/'scratch'
            def fake_home(extract):
                scratch.mkdir(); return scratch
            def fake_ground(ports, write, place, extract, home=None):
                seen.update(w=write.get('approval'), p=place.get('approval'), home=home)
                return {'sha': 'g1', 'stage': 'ground'}
            with patch.object(sw, '_home', fake_home), patch.object(sw, 'run_ground', fake_ground):
                result = sr.run(plan(d), dry=True, ports_factory=lambda broker: None, out_dir=d/'out')
            self.assertEqual(result['outcome'], 'completed')
            self.assertEqual((seen['w'], seen['p'], seen['home']), ('user w', 'user p', scratch))
            self.assertFalse(scratch.exists())
            self.assertEqual(load(d/'out'/'stage5-ground-R1.json')['sha'], 'g1')


if __name__ == '__main__':
    unittest.main()
