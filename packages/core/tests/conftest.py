import sys
from pathlib import Path
# test-only: several core test files also exercise the experiment runner (packages/eval) in the same file;
# splitting them per test case is tracked in issue #6. Source code boundaries are enforced by test_boundaries.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages/eval/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# test-only: some core tests drive the Pi adapter with injected fakes (split when the port contract lands, issue #1 P4)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hosts/pi/src"))
