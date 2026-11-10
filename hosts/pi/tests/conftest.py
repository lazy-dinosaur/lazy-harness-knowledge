import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
# a host may import the core (never the reverse)
sys.path.insert(0, str(HERE.parents[2] / "packages/core/src"))
sys.path.insert(0, str(HERE.parent / "src"))
