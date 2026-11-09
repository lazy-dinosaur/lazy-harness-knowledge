import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (REPO / "hosts/pi/src", REPO / "packages/core/src", HERE.parent / "src", HERE):
    sys.path.insert(0, str(p))
