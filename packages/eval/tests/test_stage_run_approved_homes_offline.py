"""p2-warm-4 defect: the user-approved home set must survive the runner's in-run snapshots between records."""
import offline_guard  # noqa: F401
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch

import stage_run as sr
import stagewise as sw
from safety import save

DOMAINS = [{'name': 'A', 'description': 'a'}]
SUBJECTS = {'A': [{'name': 's', 'subject_id': 'id1', 'description': 's', 'aliases': []}]}


class CarryApprovedHomes(unittest.TestCase):
    def test_helper(self):
        self.assertIsNone(sw.approved_homes_of(sw.snapshot('b', DOMAINS, SUBJECTS)))
        self.assertEqual(sw.approved_homes_of(sw.snapshot('b', DOMAINS, SUBJECTS, approval='ok')), {('A', 'id1')})
        grown = dict(SUBJECTS, A=SUBJECTS['A'] + [{'name': 'n', 'subject_id': 'id2', 'description': 'n', 'aliases': []}])
        carried = sw.snapshot('in-run', DOMAINS, grown, approved_homes={('A', 'id1')})
        self.assertEqual(sw.approved_homes_of(carried), {('A', 'id1')})  # in-run subject id2 is not approved

    def test_runner_carries_the_root_set_to_every_record(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            save(d/'base.json', sw.snapshot('root', DOMAINS, SUBJECTS, approval='user ok'))
            for rid in ('R1', 'R2', 'R3'):
                save(d/(rid + '.json'), {'stage': 'extract'})
            plan = {'run': 't', 'stage': 'place', 'host': 'h', 'records': [{'record_id': r, 'path': r} for r in ('R1', 'R2', 'R3')],
                    'extracts': {r: str(d/(r + '.json')) for r in ('R1', 'R2', 'R3')}, 'base': str(d/'base.json'),
                    'caps': {'sdk': 1, 'jev_http': 1, 'embedding_post': 1, 'embedding_inputs': 1},
                    'cash_cap_usd': 0.05, 'absolute_ceiling_usd': 1.0, 'prior_spent_usd': 0.1, 'jev_reserve_usd': 0.0347, 'approval': 'test'}
            seen = []
            def fake_place(ports, extract, base, **kw):
                seen.append(sw.approved_homes_of(base))
                return {'sha': 'x', 'domains_after': base['domains'], 'subjects_after': base['subjects'], 'groupings_after': []}
            with patch.object(sw, 'run_place', fake_place):
                result = sr.run(plan, dry=True, ports_factory=lambda broker: None, out_dir=d/'out')
            self.assertEqual(result['outcome'], 'completed')
            self.assertEqual(seen, [{('A', 'id1')}] * 3)


if __name__ == '__main__':
    unittest.main()
