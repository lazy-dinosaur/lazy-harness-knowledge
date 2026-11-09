"""The experiment runner modules import from their new home on top of the core."""
import importlib, unittest


class EvalImports(unittest.TestCase):
    def test_modules_load(self):
        for name in ('prepare', 'pipeline', 'reembed', 'calibrate', 'calibration_evidence', 'report', 'fresh',
                     'run_paid_pilot', 'area_report', 'local_db'):
            self.assertTrue(importlib.import_module(name).__file__.endswith('packages/eval/src/%s.py' % name))
