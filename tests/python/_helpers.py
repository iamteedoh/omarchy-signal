import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
os.environ.setdefault("OMARCHY_SIGNAL_TEST", "1")
