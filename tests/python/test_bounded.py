# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers whose output comes from somewhere else (wl-paste, git, ImageMagick)
are read with a ceiling and one deadline, and their process group is killed
past either."""
import os
import tempfile
import time
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import bounded, clipboard


class BoundedRunTests(unittest.TestCase):
    def test_ordinary_output(self):
        res = bounded.run(["sh", "-c", "printf hello; printf oops >&2; exit 3"], limit=100, timeout=5)
        self.assertEqual((res.returncode, res.stdout, res.stderr, res.overflow, res.timed_out), (3, b"hello", b"oops", False, False))

    def test_endless_output_is_cut_off_at_the_limit(self):
        start = time.monotonic()
        res = bounded.run(["yes"], limit=10_000, timeout=10)
        self.assertTrue(res.overflow)
        self.assertEqual(res.returncode, -1)
        self.assertEqual(len(res.stdout), 10_001)
        self.assertLess(time.monotonic() - start, 5)

    def test_a_trickle_does_not_reset_the_deadline(self):
        # One byte every 0.2 s for ever: each read is quick, the whole run is not.
        start = time.monotonic()
        res = bounded.run(["sh", "-c", "while :; do printf x; sleep 0.2; done"], limit=10_000, timeout=1)
        self.assertTrue(res.timed_out)
        self.assertEqual(res.returncode, -1)
        self.assertLess(time.monotonic() - start, 3)

    def test_the_whole_process_group_is_killed(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "child"
            bounded.run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], limit=10, timeout=0.5)
            pid = int(pidfile.read_text())
            time.sleep(0.1)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_stderr_is_capped_too(self):
        res = bounded.run(["sh", "-c", "yes 1>&2 & sleep 0.3; kill $!; printf done"], limit=100, timeout=5, stderr_limit=50)
        self.assertEqual(res.stdout, b"done")
        self.assertLessEqual(len(res.stderr), 50)


class ClipboardBoundsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self.old_path}"

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def fake_wl_paste(self, body):
        f = self.bin / "wl-paste"
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)

    def test_a_huge_text_offer_is_cut_to_the_limit(self):
        self.fake_wl_paste('case "$*" in *--list-types*) echo text/plain ;; *) yes ;; esac')
        text = clipboard.paste_text(max_chars=1000)
        self.assertEqual(len(text), 1000)

    def test_a_slow_offer_pastes_nothing(self):
        self.fake_wl_paste("while :; do printf a; sleep 0.2; done")
        old = clipboard.PASTE_TIMEOUT
        clipboard.PASTE_TIMEOUT = 0.5
        try:
            start = time.monotonic()
            self.assertEqual(clipboard.paste_text(), "")
            self.assertLess(time.monotonic() - start, 3)
        finally:
            clipboard.PASTE_TIMEOUT = old

    def test_an_oversized_picture_is_refused(self):
        self.fake_wl_paste("head -c 2000 /dev/zero")
        old = clipboard.MAX_PASTE_IMAGE
        clipboard.MAX_PASTE_IMAGE = 1000
        try:
            with self.assertRaises(clipboard.ClipboardError):
                clipboard.paste_image(self.bin / "pasted", "image/png")
        finally:
            clipboard.MAX_PASTE_IMAGE = old


if __name__ == "__main__":
    unittest.main()
