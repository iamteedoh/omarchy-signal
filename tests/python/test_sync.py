# SPDX-License-Identifier: GPL-3.0-or-later
"""What the shell runs after `omarchy plugin update` moved the plugin's files:
the unit, the hook and the keybinding block are refreshed, and the running
bridge is handed the new code. Everything runs in a temporary HOME with
systemctl and hyprctl replaced by a recorder; nothing on the machine is touched."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal import __version__, confedit, sync

ROOT = _helpers.ROOT


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.unit = self.home / ".config/systemd/user" / sync.UNIT
        self.hook = self.home / ".config/omarchy/hooks/post-update.d/omarchy-signal"
        self.bindings = self.home / ".config/hypr/bindings.lua"
        self.calls = []
        self.active = True
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_stub(self, argv, **kw):
        self.calls.append([Path(argv[0]).name] + list(argv[1:]))
        rc = 0
        if "is-active" in argv:
            rc = 0 if self.active else 3
        return subprocess.CompletedProcess(argv, rc, "", "")

    def do_sync(self, running=__version__):
        with mock.patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            return sync.sync(ROOT, home=self.home, run=self.run_stub, bridge_version=lambda: running)

    def systemctl(self):
        return [c[2:] for c in self.calls if c[0] == "systemctl"]

    def install_everything(self, stale=True):
        for path, source in ((self.unit, ROOT / "systemd" / sync.UNIT), (self.hook, ROOT / "scripts/post-update-hook.sh")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# an older release wrote this\n" if stale else source.read_text())
        self.bindings.parent.mkdir(parents=True, exist_ok=True)
        body = 'o.bind("SUPER + SHIFT + G", "Signal", "old-command")' if stale else sync.bindings_body(ROOT)
        self.bindings.write_text('o.bind("SUPER + T", "Terminal", "alacritty")\n'
                                 + confedit.write_block("", confedit.BINDINGS_BEGIN, confedit.BINDINGS_END, body))

    def test_the_block_written_is_the_file_without_its_header(self):
        body = sync.bindings_body(ROOT)
        self.assertTrue(body.startswith("-- SUPER+SHIFT+G"), body[:60])
        self.assertNotIn("SPDX", body)
        self.assertIn('o.bind("SUPER + SHIFT + G", "Signal", "omarchy-signal open")', body)
        self.assertFalse(body.endswith("\n"))

    def test_install_sh_and_sync_write_the_same_block(self):
        # install.sh strips the header with sed; sync with str.partition. If the
        # two ever disagree, every update would rewrite bindings.lua and reload Hyprland.
        via_sed = subprocess.run(["sed", "1,/^$/d", str(ROOT / "config/bindings.lua")], capture_output=True, text=True, check=True).stdout
        self.assertEqual(via_sed.rstrip("\n"), sync.bindings_body(ROOT))
        self.assertIn("sed '1,/^$/d' \"$HERE/config/bindings.lua\"", (ROOT / "install.sh").read_text())

    def test_stale_pieces_are_refreshed_and_the_bridge_restarted(self):
        self.install_everything()
        result = self.do_sync(running="0.0.1")
        self.assertEqual((result["unit"], result["hook"], result["bindings"], result["bridge"]),
                         ("updated", "updated", "updated", "restarted"))
        self.assertEqual(self.unit.read_text(), (ROOT / "systemd" / sync.UNIT).read_text())
        self.assertEqual(self.hook.read_text(), (ROOT / "scripts/post-update-hook.sh").read_text())
        self.assertTrue(os.access(self.hook, os.X_OK))
        text = self.bindings.read_text()
        self.assertIn('o.bind("SUPER + T", "Terminal", "alacritty")', text)           # the user's own bindings survive
        self.assertNotIn("old-command", text)
        self.assertIn("omarchy-signal open", text)
        self.assertEqual(self.systemctl(), [["daemon-reload"], ["is-active", "--quiet", sync.UNIT], ["restart", sync.UNIT]])
        self.assertIn(["hyprctl", "reload"], self.calls)

    def test_a_second_run_changes_nothing(self):
        self.install_everything(stale=False)
        before = self.bindings.read_text()
        result = self.do_sync()
        self.assertEqual((result["unit"], result["hook"], result["bindings"], result["bridge"]),
                         ("unchanged", "unchanged", "unchanged", "unchanged"))
        self.assertEqual(self.bindings.read_text(), before)
        self.assertEqual(self.systemctl(), [["is-active", "--quiet", sync.UNIT]])    # no reload, no restart
        self.assertNotIn(["hyprctl", "reload"], self.calls)

    def test_a_bridge_running_old_code_is_restarted_even_when_no_file_changed(self):
        self.install_everything(stale=False)
        self.assertEqual(self.do_sync(running="0.0.1")["bridge"], "restarted")
        self.assertEqual(self.systemctl()[-1], ["restart", sync.UNIT])

    def test_a_stopped_bridge_is_never_started(self):
        # Stopped, disabled or masked is the user's choice. The marketplace review
        # blocked a hook that turned the bridge back on; this must not repeat it.
        self.install_everything()
        self.active = False
        result = self.do_sync(running="0.0.1")
        self.assertEqual(result["bridge"], "not running")
        for call in self.systemctl():
            self.assertNotIn(call[0], ("start", "restart", "enable", "unmask", "reenable"), call)

    def test_what_was_never_installed_stays_not_installed(self):
        # `omarchy plugin add` alone, or --no-bind: sync must not finish an install nobody asked for.
        self.bindings.parent.mkdir(parents=True)
        self.bindings.write_text('o.bind("SUPER + T", "Terminal", "alacritty")\n')
        self.active = False
        result = self.do_sync()
        self.assertEqual((result["unit"], result["hook"], result["bindings"]), ("absent", "absent", "absent"))
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.hook.exists())
        self.assertEqual(self.bindings.read_text(), 'o.bind("SUPER + T", "Terminal", "alacritty")\n')

    def test_a_bindings_file_with_a_lost_marker_is_left_byte_identical(self):
        self.install_everything(stale=False)
        broken = 'keep_me()\n-- BEGIN omarchy-signal\norphan()\no.bind("SUPER + B", "Browser", "chromium")\n'
        self.bindings.write_text(broken)
        result = self.do_sync()
        self.assertTrue(result["bindings"].startswith("failed"), result)
        self.assertEqual(self.bindings.read_text(), broken)


if __name__ == "__main__":
    unittest.main()
