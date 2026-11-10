"""Experiment-runner cases split out of packages/core/tests/test_place_write_offline.py.py (issue #6 step 7)."""
import offline_guard  # noqa: F401
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pipeline as p
import place_write as w
import place_write_absorb as a
import source_grounding as sg
import store_pg as pg
import precompute
from safety import Stop, digest, load, save
import test_place_write_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith('__') and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class UnitFirstEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.UnitFirst).items() if not k.startswith("test") and not k.startswith("__")})

    def setUp(self):
        _core.UnitFirst.setUp(self)
        patcher = patch.object(p, 'HERE', self.home); patcher.start(); self.addCleanup(patcher.stop)  # the runner reads its own HERE

    def test_plan_code_drift_guard_unchanged(self):
        plan={'records':[], 'questions':[], 'code':{'src/place_write.py':hashlib.sha256((p.RUNNER/'src/place_write.py').read_bytes()).hexdigest()}}
        with patch.object(p,'inputs',return_value=([],[])):
            p.verify_plan(plan)
            plan['code']['src/place_write.py']='bad'
            with self.assertRaises(Stop): p.verify_plan(plan)

    def test_unit_ids_one_per_fact_and_readback_uses_them(self):
        self.run_phases(); g=load(self.home/'state/generated/R05.json')
        units=p.generated_units(self.r,g)
        self.assertEqual(len(units),3); self.assertTrue(all(len(fs)==1 for _,_,fs in units))
        self.assertEqual(len({unit for unit,_,_ in units}),3)


if __name__ == '__main__':
    unittest.main()
