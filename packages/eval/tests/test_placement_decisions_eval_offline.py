"""Experiment-runner cases split out of packages/core/tests/test_placement_decisions_offline.py.py (issue #6 step 7)."""
import offline_guard  # noqa: F401
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import place_write as w
import place_write_absorb as a
import pipeline as p
import local_db
import subject_contract as sc
import subject_dict as sd
import store_pg as pg
from ddd_criterion import DDD2, CALIBRATED_SHA256, criterion_sha, DDD_FOR_AUTHOR
from safety import Stop, digest, load, save, RUNNER
import test_place_write_offline as previous
import test_placement_decisions_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith('__') and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class PlacementDecisionsEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.PlacementDecisions).items() if not k.startswith("test") and not k.startswith("__")})

    def test_none_threshold_and_no_cosine_floor(self):
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'일정표 시간','target_expression':'일정표'}
        ports=w.Ports(Mock(return_value={'original_expression':'일정표'}),lambda xs:[[0.,0.]]*len(xs),
                      lambda k,p,o:{n:.69 if n=='none' else .31 for n in o},Mock(return_value=[.5]))
        result=w.place_identity('domain',unit,None,[{'name':'예약','description':'예약 영역'}],ports,5)
        self.assertFalse(result['new'])
        self.assertEqual(result['ambiguous_domain_membership']['score'],.5)
        self.assertTrue(result['ambiguous_domain_membership']['member'])
        self.assertNotIn('ambiguous_same_meaning',result)
        self.assertNotIn('일정표',result['chosen']['aliases'])  # ambiguous placement is not alias proof

    def test_schema_guard_only_exact_owned_local_pilot(self):
        case=self.fixture(); scope='pilot-area-10'
        plan={**case.plan,'scope':scope,'host':'knowledge-local-'+scope,'container':'lh-knowledge-local-'+scope+'-local'}
        save(case.home/'state/plan.json',plan)
        with patch.object(local_db,'HERE',case.home.parent),patch.object(pg,'connect') as connect:
            # A different home basename must be rejected before a connection.
            with self.assertRaises(Stop): local_db.apply_unit_first_schema(case.home,plan['host'],plan['container'],digest(plan),'postgresql://postgres:localtest@127.0.0.1:5555/postgres')
            self.assertFalse(connect.called)
        with patch.object(local_db,'HERE',case.home.parent),patch.object(pg,'connect') as connect:
            renamed=case.home.parent/scope
            # Mock only path ownership resolution; don't move any actual pilot state.
            with patch.object(local_db,'load',return_value=plan),patch.object(Path,'exists',return_value=True),patch.object(Path,'resolve',return_value=renamed):
                with self.assertRaises(Stop): local_db.apply_unit_first_schema(case.home,plan['host'],plan['container'],digest(plan),'postgresql://remote/db')
                self.assertFalse(connect.called)

    def test_schema_sql_preserves_description_constraint_and_records_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'pilot-area-10'; home.mkdir()
            plan={'write_path':2,'scope':'pilot-area-10','host':'knowledge-local-pilot-area-10','container':'lh-knowledge-local-pilot-area-10-local'}
            save(home/'state/plan.json',plan)
            cur=Mock(); conn=MagicMock(); conn.__enter__.return_value=conn
            conn.cursor.return_value.__enter__.return_value=cur
            with patch.object(local_db,'HERE',root),patch.object(pg,'connect',return_value=conn):
                local_db.apply_unit_first_schema(home,plan['host'],plan['container'],digest(plan),'postgresql://postgres:localtest@127.0.0.1:5555/postgres')
            sql=' '.join(c.args[0] for c in cur.execute.call_args_list)
            self.assertIn('subject_type drop not null',sql); self.assertIn('subject_type_fixed',sql)
            self.assertNotIn('subject_description_present',sql)
            self.assertFalse(load(home/'state/unit-first-schema.json')['placeholder_type'])


if __name__ == '__main__':
    unittest.main()
