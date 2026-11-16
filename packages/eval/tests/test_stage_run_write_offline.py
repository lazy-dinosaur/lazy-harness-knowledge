"""Stage 4 in the runner: user-passed place reports in, one write report per record out, resume skips finished records."""
import offline_guard  # noqa: F401
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import stage_run as sr
import stagewise as sw
from safety import save, load, Stop


def plan(d, **over):
    p = {'run': 'w', 'stage': 'write', 'host': 'h', 'records': [{'record_id': r, 'path': r} for r in ('R1', 'R2')],
         'extracts': {r: str(d/('x-' + r + '.json')) for r in ('R1', 'R2')}, 'places': {r: str(d/('p-' + r + '.json')) for r in ('R1', 'R2')},
         'place_approval': 'user ok', 'caps': {'sdk': 1, 'jev_http': 1, 'embedding_post': 1, 'embedding_inputs': 1},
         'cash_cap_usd': 0.05, 'absolute_ceiling_usd': 1.0, 'prior_spent_usd': 0.1, 'jev_reserve_usd': 0.0347, 'approval': 'test'}
    p.update(over); return p


def files(d):
    for r in ('R1', 'R2'):
        save(d/('x-' + r + '.json'), {'stage': 'extract', 'r': r})
        save(d/('p-' + r + '.json'), sw._sealed({'version': sw.VERSION, 'stage': 'place', 'rows': [], 'r': r}))


class WriteStage(unittest.TestCase):
    def test_plan_checks(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for over in ({'places': {}}, {'places': {'R1': 'x'}}, {'place_approval': ''}, {'stage': 'ground'}):
                with self.assertRaises(Stop):
                    sr.check_plan(plan(d, **over), dry=True)
            sr.check_plan(plan(d), dry=True)

    def test_runner_passes_place_reports_and_saves_one_write_report_per_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); files(d)
            seen = []
            def fake_write(ports, place, extract, out=None):
                seen.append((place['r'], place.get('approval'), extract['r']))
                return {'sha': 'w-' + place['r'], 'stage': 'write'}
            with patch.object(sw, 'run_write', fake_write):
                result = sr.run(plan(d), dry=True, ports_factory=lambda broker: None, out_dir=d/'out')
            self.assertEqual(result['outcome'], 'completed')
            self.assertEqual(seen, [('R1', 'user ok', 'R1'), ('R2', 'user ok', 'R2')])
            self.assertEqual(load(d/'out'/'stage4-write-R2.json')['sha'], 'w-R2')
            self.assertEqual(result['reports'], {'R1': 'w-R1', 'R2': 'w-R2'})


if __name__ == '__main__':
    unittest.main()
