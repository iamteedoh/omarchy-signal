# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
os.environ.setdefault("OMARCHY_SIGNAL_TEST", "1")

# Tests must never reach the real desktop: a test that draws the TUI's link
# screen used to ask the running Omarchy shell to show its (fake) QR code, so
# every test run popped a linking dialog. Replace both calls with ones that act
# like no shell is running.
from omarchy_signal import qr as _qr  # noqa: E402

_qr.shell_show_qr = lambda path: False
_qr.shell_hide_qr = lambda: None
