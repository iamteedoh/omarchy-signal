# SPDX-License-Identifier: GPL-3.0-or-later
"""The CLI and TUI only talk to a bridge socket that is ours: in a private
directory, served by a process running as this user. Nothing falls back to a
shared path such as /tmp that another local user could create first."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal import client as client_mod
from omarchy_signal import config
from omarchy_signal.client import BridgeClient, BridgeUnavailable
from omarchy_signal.config import Paths


class RuntimeDirTests(unittest.TestCase):
    def test_no_tmp_fallback(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "XDG_STATE_HOME": home + "/state"}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(config, "private_dir", return_value=False):
                path = config.runtime_dir()
        self.assertFalse(str(path).startswith("/tmp/omarchy-signal"))
        self.assertEqual(path, Path(home) / "state" / "omarchy-signal" / "run")

    def test_systemd_runtime_dir_preferred_when_private(self):
        with mock.patch.dict(os.environ, {"HOME": "/nonexistent"}, clear=True), \
                mock.patch.object(config, "private_dir", return_value=True):
            self.assertEqual(config.runtime_dir(), Path(f"/run/user/{os.getuid()}"))

    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1234"}, clear=True):
            self.assertEqual(config.runtime_dir(), Path("/run/user/1234"))
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/a", "OMARCHY_SIGNAL_RUNTIME_DIR": "/b"}, clear=True):
            self.assertEqual(config.runtime_dir(), Path("/b"))

    def test_private_dir(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run"
            self.assertFalse(config.private_dir(p))            # missing
            p.mkdir(mode=0o700)
            self.assertTrue(config.private_dir(p))
            p.chmod(0o755)
            self.assertFalse(config.private_dir(p))            # others can enter
            p.chmod(0o700)
            link = Path(d) / "link"
            link.symlink_to(p)
            self.assertFalse(config.private_dir(link))         # a symlink, even to a good dir


class ClientTrustTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.paths = Paths(config_dir=self.base / "c", data_dir=self.base / "d", state_dir=self.base / "s",
                           run_dir=self.base / "run")

    async def asyncTearDown(self):
        server = getattr(self, "server", None)
        if server is not None:
            pass
            server.close_clients()
            await asyncio.sleep(0.05)
        self.tmp.cleanup()

    async def _serve(self):
        self.paths.run_dir.mkdir(mode=0o700, exist_ok=True)

        async def handle(reader, writer):
            try:
                writer.write(b'{"event":"hello","data":{"version":"x"}}\n')
                await writer.drain()
                await reader.read()
            except (ConnectionError, OSError):
                pass
            finally:
                writer.close()
        self.server = await asyncio.start_unix_server(handle, path=str(self.paths.socket))
        return self.server

    async def test_connects_to_our_own_bridge(self):
        server = await self._serve()
        c = BridgeClient(self.paths)
        try:
            hello = await c.connect(timeout=2)
            self.assertEqual(hello.get("version"), "x")
        finally:
            await c.close()
            pass

    async def test_refuses_a_directory_others_can_use(self):
        server = await self._serve()
        self.paths.run_dir.chmod(0o777)
        try:
            with self.assertRaises(BridgeUnavailable) as cm:
                await BridgeClient(self.paths).connect(timeout=2)
            self.assertEqual(cm.exception.code, "untrusted")
        finally:
            self.paths.run_dir.chmod(0o700)
            pass

    async def test_refuses_a_server_run_by_another_user(self):
        server = await self._serve()
        try:
            with mock.patch.object(client_mod, "server_is_us", return_value=False):
                with self.assertRaises(BridgeUnavailable) as cm:
                    await BridgeClient(self.paths).connect(timeout=2)
            self.assertEqual(cm.exception.code, "untrusted")
        finally:
            pass

    async def test_peer_credentials_are_read(self):
        server = await self._serve()
        try:
            _r, w = await asyncio.open_unix_connection(str(self.paths.socket))
            self.assertTrue(client_mod.server_is_us(w))
            w.close()
            await w.wait_closed()
        finally:
            pass

    async def test_missing_bridge_is_offline_not_untrusted(self):
        with self.assertRaises(BridgeUnavailable) as cm:
            await BridgeClient(self.paths).connect(timeout=1)
        self.assertEqual(cm.exception.code, "offline")


if __name__ == "__main__":
    unittest.main()
