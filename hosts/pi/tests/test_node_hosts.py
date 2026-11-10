"""Runs the Node fake-wire host test against the installed Pi model runtime; skipped when Pi is not installed.

Python builds the real packets (core test with --capture-root), the Node test replays them through the installed
ModelRuntime with synthetic models/home and rejecting credentials (no network, no auth).
"""
import subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def runtime():
    sys.path.insert(0, str(REPO / 'packages/core/src'))
    sys.path.insert(0, str(HERE.parent / 'src'))
    import writer_transport
    try:
        return writer_transport.runtime_path()
    except writer_transport.WriterFailure:
        return None


class NodeHosts(unittest.TestCase):
    def test_active_worker_host_replays_python_packets(self):
        rt = runtime()
        if rt is None:
            self.skipTest('installed Pi model runtime not found')
        with tempfile.TemporaryDirectory() as tmp:
            core = REPO / 'packages/core/tests/test_active_worker_offline.py'
            import os
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(str(x) for x in (REPO / 'packages/core/src', REPO / 'packages/eval/src', core.parent, HERE.parent / 'src')))
            made = subprocess.run([sys.executable, str(core), '--capture-root', tmp], capture_output=True, text=True, cwd=core.parent, env=env)
            self.assertEqual(made.returncode, 0, made.stderr[-2000:])
            out = subprocess.run(['node', str(HERE / 'test_active_worker_host_offline.mjs'), tmp, str(rt)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr[-2000:])

    @unittest.skip('the writer capture producer (caller-capture.json) was not found in the source folder; tracked in issue #1 P0')
    def test_writer_host_replays_python_packets(self):
        pass
