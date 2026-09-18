# SPDX-License-Identifier: GPL-3.0-or-later
import json
import pathlib
import re
import subprocess
import sys
import unittest

import _helpers  # noqa: F401
from omarchy_signal import __version__

ROOT = pathlib.Path(__file__).resolve().parents[2]


class VersionTests(unittest.TestCase):
    """`omarchy-signal --version` used to report a hardcoded 0.1.0 that matched no
    release, so a bug report named a version that did not exist (OMSIG-1)."""

    def test_matches_the_manifest(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())["version"]
        self.assertEqual(
            __version__, manifest,
            "__version__ and manifest.json have drifted; release-please bumps both, "
            "so one was edited by hand",
        )

    def test_is_a_release_number(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

    def test_release_please_owns_the_constant(self):
        """Without the annotation the generic updater silently skips the file, and
        the constant quietly freezes again."""
        init = (ROOT / "lib/omarchy_signal/__init__.py").read_text()
        self.assertRegex(
            init,
            re.compile(r'^__version__ = "[^"]+"\s+# x-release-please-version\s*$', re.M),
            msg="the __version__ line lost its x-release-please-version annotation",
        )
        cfg = json.loads((ROOT / "release-please-config.json").read_text())
        paths = [e.get("path") for e in cfg["packages"]["."]["extra-files"]]
        self.assertIn("lib/omarchy_signal/__init__.py", paths)

    def test_launcher_reports_it(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "bin/omarchy-signal"), "--version"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(f"omarchy-signal {__version__}", (out.stdout + out.stderr).strip())


if __name__ == "__main__":
    unittest.main()
