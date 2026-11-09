import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# test-only: some core tests drive the Pi adapter with injected fakes (split when the port contract lands, issue #1 P4)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hosts/pi/src"))
