"""Run state lives outside the source tree; experiment inputs are never assumed to be in the repository."""
import importlib, os, unittest
from pathlib import Path
from unittest.mock import patch

import safety

REPO = Path(__file__).resolve().parents[3]


class StateHome(unittest.TestCase):
    def tearDown(self):
        importlib.reload(safety)

    def test_default_state_home_is_ignored_repo_dot_state(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LH_KNOWLEDGE_STATE_HOME', None)
            importlib.reload(safety)
            self.assertEqual(safety.STATE_HOME, (REPO / '.state').resolve())
            self.assertFalse(safety.STATE_HOME.is_relative_to(REPO / 'packages'))
        self.assertIn('.state/', (REPO / '.gitignore').read_text().splitlines())

    def test_state_home_and_inputs_are_configurable(self):
        with patch.dict(os.environ, {'LH_KNOWLEDGE_STATE_HOME': '/tmp/lh-k-state', 'LH_KNOWLEDGE_INPUTS': '/tmp/lh-k-inputs'}):
            importlib.reload(safety)
            self.assertEqual(safety.STATE_HOME, Path('/tmp/lh-k-state').resolve())
            self.assertEqual(safety.inputs_home(), Path('/tmp/lh-k-inputs').resolve())

    def test_missing_inputs_fail_explicitly(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LH_KNOWLEDGE_INPUTS', None)
            importlib.reload(safety)
            with self.assertRaisesRegex(RuntimeError, 'LH_KNOWLEDGE_INPUTS'):
                safety.inputs_home()
