# SPDX-License-Identifier: GPL-3.0-or-later
"""The updater, against real git repositories: a throwaway "origin" with real
release tags, a plugins directory holding a git-managed plugin folder (what
``omarchy plugin add`` makes) or a plain copy (what the old ``./install.sh``
made), and an install.sh that records which version's installer actually ran.
Nothing here touches the network or the real install."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal import cli, update

GIT = shutil.which("git")
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def git(cwd, *args):
    return subprocess.run([GIT, *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(GIT, "git is not installed")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.marker = self.root / "installed.log"
        self.work = self.root / "upstream-work"            # where "the maintainer" makes releases
        self.work.mkdir()
        git(self.work, "init", "-q", "-b", "main")
        self.release("0.1.0")
        self.origin = self.root / "origin.git"
        git(self.root, "clone", "-q", "--bare", str(self.work), str(self.origin))
        git(self.work, "remote", "add", "origin", str(self.origin))
        self.plugins = self.root / "config" / "omarchy" / "plugins"
        self.plugins.mkdir(parents=True)
        self.plugin = self.plugins / update.PLUGIN_ID      # what `omarchy plugin add` cloned
        git(self.root, "clone", "-q", str(self.origin), str(self.plugin))
        self.info = update.InstallInfo(self.plugin, "git", str(self.origin))
        self.state = self.root / "state"
        env = {k: v for k, v in GIT_ENV.items() if k.startswith("GIT_")}
        # No omarchy-plugin-validate from the machine running the tests: the
        # updater's own checks are what is under test, and a stub decides the rest.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in ("git", "bash", "sh"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        env["PATH"] = str(self.bin)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def release(self, version, *, tag=True, push=False, plugin_id=update.PLUGIN_ID):
        (self.work / "manifest.json").write_text(json.dumps({"schemaVersion": 1, "id": plugin_id, "version": version}) + "\n")
        installer = self.work / "install.sh"
        installer.write_text(f'#!/bin/bash\necho "{version} $*" >> "{self.marker}"\nexit ${{FAKE_INSTALL_RC:-0}}\n')
        installer.chmod(installer.stat().st_mode | stat.S_IXUSR)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", f"release {version}")
        if tag:
            git(self.work, "tag", f"v{version}")
        if push:
            git(self.work, "push", "-q", "origin", "main", "--tags")

    def installed(self):
        return self.marker.read_text().splitlines() if self.marker.exists() else []

    def version_on_disk(self, directory=None):
        return json.loads(((directory or self.plugin) / "manifest.json").read_text())["version"]

    def make_copy(self):
        """Turn the plugin folder into what the old installer left: files, no .git."""
        shutil.rmtree(self.plugin / ".git")
        return update.InstallInfo(self.plugin, "copy", str(self.origin))

    def validator(self, rc):
        exe = self.bin / "omarchy-plugin-validate"
        exe.write_text(f"#!/bin/sh\necho 'manifest says no' >&2\nexit {rc}\n")
        exe.chmod(0o755)

    # -- which release is newest -------------------------------------------------------------

    def test_newest_tag_ignores_everything_that_is_not_a_release(self):
        listing = "\n".join(f"abc\trefs/tags/{t}" for t in
                            ("v0.9.0", "v0.10.0", "v0.10.0^{}", "v1.0.0-rc1", "nightly", "v2", "release-9.9.9", "v0.2.11", "v99999.0.0", "v1.0.0; rm -rf ~"))
        self.assertEqual(update.newest_tag(listing), "v0.10.0")          # numeric, not alphabetical
        self.assertIsNone(update.newest_tag("abc\trefs/tags/latest\n"))
        self.assertIsNone(update.newest_tag(""))

    def test_versions_compare_as_numbers(self):
        self.assertGreater(update.parse_version("0.10.0"), update.parse_version("0.9.9"))
        self.assertEqual(update.parse_version("v1.2.3"), (1, 2, 3))
        for junk in ("", "1.2", "1.2.3.4", "1.2.x", "v1.2.3-rc1", "../1.2.3", "1.2.3\n"):
            self.assertIsNone(update.parse_version(junk), junk)

    def test_only_sane_remotes_are_handed_to_git(self):
        for good in ("https://github.com/iamteedoh/omarchy-signal", "git@github.com:iamteedoh/omarchy-signal.git",
                     "ssh://git@example.org/x.git", "/srv/git/omarchy-signal.git", update.DEFAULT_ORIGIN):
            self.assertTrue(update.safe_remote(good), good)
        for bad in ("", "--upload-pack=touch /tmp/pwned", "-oProxyCommand=x", "ext::sh -c id", "http://insecure.example/x.git",
                    "file:///etc", "https://x.example/a b", "https://x.example/a\nb", "git://example.org/x.git"):
            self.assertFalse(update.safe_remote(bad), bad)

    # -- how it is installed -------------------------------------------------------------------

    def test_detect_tells_the_four_kinds_of_install_apart(self):
        info = update.InstallInfo.detect(self.plugin)
        self.assertEqual((info.kind, info.origin), ("git", str(self.origin)))

        self.make_copy()
        info = update.InstallInfo.detect(self.plugin)
        self.assertEqual((info.kind, info.origin), ("copy", update.DEFAULT_ORIGIN))    # nothing recorded: upstream

        self.state.mkdir()
        (self.state / "install.json").write_text(json.dumps({"origin": str(self.origin)}))
        self.assertEqual(update.InstallInfo.detect(self.plugin, self.state).origin, str(self.origin))   # a fork stays on the fork
        (self.state / "install.json").write_text(json.dumps({"origin": "--upload-pack=id"}))
        self.assertEqual(update.InstallInfo.detect(self.plugin, self.state).origin, update.DEFAULT_ORIGIN)
        (self.state / "install.json").write_text("not json")
        self.assertEqual(update.InstallInfo.detect(self.plugin, self.state).origin, update.DEFAULT_ORIGIN)

        link = self.plugins / "linked"
        link.symlink_to(self.work)
        self.assertEqual(update.InstallInfo.detect(link).kind, "link")
        self.assertEqual(update.InstallInfo.detect(self.plugins / "missing").kind, "none")

    # -- checking ------------------------------------------------------------------------------

    def test_check_reports_a_newer_release_and_touches_nothing(self):
        self.assertFalse(update.check(self.info, current="0.1.0").available)
        self.release("0.2.0", push=True)
        result = update.check(self.info, current="0.1.0")
        self.assertEqual((result.available, result.latest, result.error), (True, "0.2.0", ""))
        self.assertEqual(self.version_on_disk(), "0.1.0")                          # a check never moves the plugin
        self.assertFalse(update.check(self.info, current="0.2.0").available)
        self.assertFalse(update.check(self.info, current="0.3.0").available)      # never offers a downgrade

    def test_an_ssh_remote_is_asked_over_https_first_so_no_key_is_needed(self):
        cases = {"git@github.com:iamteedoh/omarchy-signal.git": "https://github.com/iamteedoh/omarchy-signal.git",
                 "git@github.com:iamteedoh/omarchy-signal": "https://github.com/iamteedoh/omarchy-signal.git",
                 "ssh://git@codeberg.org/someone/fork.git": "https://codeberg.org/someone/fork.git",
                 "ssh://git@host.example:2222/someone/fork.git": "",       # a port: no https twin to guess
                 "https://github.com/iamteedoh/omarchy-signal.git": "", "/srv/git/x.git": "", "": "",
                 "git@github.com:--upload-pack=id": "", "git@github.com:a/b c": ""}
        for url, want in cases.items():
            self.assertEqual(update.anonymous_url(url), want, url)

        asked = []

        def run(argv, **kw):
            asked.append(argv)
            if "https://github.com/iamteedoh/omarchy-signal.git" in argv:
                return subprocess.CompletedProcess(argv, 0, "abc\trefs/tags/v0.9.0\n", "")
            return subprocess.CompletedProcess(argv, 128, "", "fatal: no key\n")
        info = update.InstallInfo(self.plugin, "copy", "git@github.com:iamteedoh/omarchy-signal.git")
        result = update.check(info, current="0.1.0", run=run)
        self.assertEqual((result.available, result.latest, result.error), (True, "0.9.0", ""))
        self.assertEqual(len(asked), 1, "the ssh remote is not tried when https answers")
        self.assertIn("credential.helper=", asked[0], "and no stored credential is offered")

    def test_a_private_remote_falls_back_to_ssh_and_a_failure_says_what_failed(self):
        asked = []
        advice = ("fatal: Could not read from remote repository.\n\nPlease make sure you have the correct access rights\n"
                  "and the repository exists.\n")

        def run(argv, **kw):
            asked.append(argv[-1])
            return subprocess.CompletedProcess(argv, 128, "", advice)
        origin = "git@github.com:someone/private.git"
        result = update.check(update.InstallInfo(self.plugin, "copy", origin), current="0.1.0", run=run)
        self.assertEqual(asked, ["https://github.com/someone/private.git", origin])
        self.assertEqual(result.error, f"could not list the releases at {origin}: Could not read from remote repository.")
        self.assertEqual(update.git_error("warning: x\nerror: bad thing\nhint: y\n"), "bad thing")
        self.assertEqual(update.git_error(""), "git failed")

    def test_check_before_any_release_exists(self):
        empty = self.root / "empty.git"
        git(self.root, "init", "-q", "--bare", str(empty))
        result = update.check(update.InstallInfo(self.plugin, "git", str(empty)), current="0.0.0")
        self.assertEqual((result.available, result.latest, result.error), (False, None, ""))

    def test_check_failures_are_reported_not_raised(self):
        result = update.check(update.InstallInfo(self.plugin, "git", str(self.root / "gone.git")), current="0.1.0")
        self.assertFalse(result.available)
        self.assertTrue(result.error)
        self.assertIn("no usable git remote", update.check(update.InstallInfo(self.plugin, "git", ""), current="0.1.0").error)
        self.assertTrue(update.check(update.InstallInfo(self.plugin, "git", "--upload-pack=id"), current="0.1.0").error)
        self.assertIn("developer link", update.check(update.InstallInfo(self.plugin, "link", ""), current="0.1.0").error)
        self.assertIn("omarchy plugin add", update.check(update.InstallInfo(self.plugin, "none", ""), current="0.1.0").error)

    def test_the_daily_check_is_cached_and_can_be_switched_off(self):
        calls = []

        def counting_run(argv, **kw):
            calls.append(argv)
            return subprocess.run(argv, **kw)
        self.assertIsNone(update.check_if_due(self.info, self.state, enabled=False, run=counting_run))
        self.assertEqual(calls, [])                                               # off means no network at all
        first = update.check_if_due(self.info, self.state, run=counting_run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(stat.S_IMODE(update.cache_file(self.state).stat().st_mode), 0o600)
        update.check_if_due(self.info, self.state, run=counting_run)
        self.assertEqual(len(calls), 1)                                           # within a day: answered from the cache
        update.check_if_due(self.info, self.state, run=counting_run, now=first.checked_at + update.CHECK_INTERVAL + 1)
        self.assertEqual(len(calls), 2)

    def test_a_cached_answer_is_judged_against_the_running_version(self):
        update.store(self.state, update.CheckResult("0.1.0", "0.2.0", True, time.time()))
        self.assertTrue(update.cached(self.state, current="0.1.0").available)
        self.assertFalse(update.cached(self.state, current="0.2.0").available)    # just updated: the old "available" must not linger
        update.cache_file(self.state).write_text('{"latest": "9.9.9; rm -rf ~", "checkedAt": 1}')
        self.assertIsNone(update.cached(self.state))                              # a tampered cache is ignored, not shown
        update.cache_file(self.state).write_text("not json")
        self.assertIsNone(update.cached(self.state))

    # -- the command ---------------------------------------------------------------------------------

    def run_update(self, info, **flags):
        args = SimpleNamespace(check=False, yes=True, json=False, then_tui=False)
        args.__dict__.update(flags)
        paths = SimpleNamespace(state_dir=self.state)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "_paths", return_value=paths), \
                mock.patch.object(update.InstallInfo, "detect", return_value=info), \
                mock.patch.object(cli, "__version__", "0.1.0"), mock.patch.object(update, "__version__", "0.1.0"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.cmd_update(args)
        return rc, out.getvalue(), err.getvalue()

    def host(self, rc=0):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, rc)
        return calls, mock.patch.object(cli.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(update.shutil, "which", side_effect=lambda n: "/usr/bin/" + n)

    def test_the_plugin_has_no_way_to_install_code_itself(self):
        for name in ("plan", "fast_forward", "adopt", "run_installer", "validate"):
            self.assertFalse(hasattr(update, name), name)

    def test_update_hands_off_to_omarchys_plugin_updater(self):
        self.release("0.2.0", push=True)
        update.store(self.state, update.CheckResult("0.1.0", "0.2.0", True, time.time()))
        calls, run, which = self.host(0)
        with run, which:
            rc, out, _ = self.run_update(self.info)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [["/usr/bin/omarchy", "plugin", "update", update.PLUGIN_ID, "--yes"]])
        self.assertIn("omarchy plugin update", out)
        self.assertEqual(self.version_on_disk(), "0.1.0", "the plugin never moves its own folder")
        self.assertEqual(self.installed(), [], "and never runs an installer")
        self.assertFalse(update.cache_file(self.state).exists())                   # the "update available" note goes

    def test_without_a_yes_omarchy_asks(self):
        calls, run, which = self.host(0)
        with run, which:
            self.run_update(self.info, yes=False)
        self.assertEqual(calls, [["/usr/bin/omarchy", "plugin", "update", update.PLUGIN_ID]])

    def test_a_failed_host_update_is_reported_and_keeps_the_note(self):
        update.store(self.state, update.CheckResult("0.1.0", "0.2.0", True, time.time()))
        calls, run, which = self.host(1)
        with run, which:
            rc, _, _ = self.run_update(self.info)
        self.assertEqual(rc, 1)
        self.assertTrue(update.cache_file(self.state).exists())

    def test_a_copy_made_from_a_checkout_points_back_at_that_checkout(self):
        self.make_copy()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "install.json").write_text(json.dumps({"origin": str(self.origin), "source": str(self.work)}))
        (self.work / "install.sh").touch()
        info = update.InstallInfo.detect(self.plugin, self.state)
        self.assertEqual((info.kind, info.source), ("copy", str(self.work)))
        self.assertIn(f"copied from {self.work}", update.advice(info))
        self.assertIn("./install.sh", update.advice(info))
        # A checkout that is gone, or a record that is not a path, is not mentioned.
        for bad in (str(self.root / "gone"), "relative/path", 7, "/tmp\nrm -rf ~"):
            (self.state / "install.json").write_text(json.dumps({"origin": str(self.origin), "source": bad}))
            info = update.InstallInfo.detect(self.plugin, self.state)
            self.assertEqual(info.source, "")
            self.assertIn("omarchy plugin remove", update.advice(info))

    def test_other_installs_get_advice_and_nothing_runs(self):
        cases = [(self.make_copy(), "omarchy plugin add"), (update.InstallInfo(self.plugin, "link", ""), "developer link"),
                 (update.InstallInfo(self.plugin, "none", ""), "omarchy plugin add")]
        for info, says in cases:
            calls, run, which = self.host(0)
            with self.subTest(kind=info.kind), run, which:
                rc, _, err = self.run_update(info)
                self.assertEqual(rc, 1)
                self.assertIn(says, err)
                self.assertEqual(calls, [])
        self.assertEqual(self.installed(), [])

    def test_check_only_reports_and_records_what_it_found(self):
        self.release("0.2.0", push=True)
        rc, out, _ = self.run_update(self.info, check=True, json=True)
        self.assertEqual(rc, 0)
        found = json.loads(out)
        self.assertEqual((found["available"], found["latest"], found["install"]), (True, "0.2.0", "git"))
        self.assertEqual(found["how"], "omarchy plugin update " + update.PLUGIN_ID)
        self.assertEqual(self.version_on_disk(), "0.1.0")
        self.assertTrue(update.cached(self.state, current="0.1.0").available)      # what `status` and the shell show later


if __name__ == "__main__":
    unittest.main()
