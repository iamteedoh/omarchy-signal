# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal client driven end to end against the real bridge (fake signal-cli)."""

import asyncio
import unittest

import _helpers  # noqa: F401
from omarchy_signal import tui
from omarchy_signal.client import BridgeClient
from omarchy_signal.term import Key
from test_bridge_integration import BridgeHarness
from test_tui_render import FakeTerminal, make_app


class LiveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_in_flight_does_not_swallow_a_send(self):
        async with BridgeHarness() as h:
            app = make_app()
            app.paths = h.paths
            app.client = BridgeClient(h.paths, on_event=app._on_event)
            app.status = await app.client.connect()
            await app.client.request("subscribe")
            me = "number:+15550001111"
            app._ensure_conversation(me, "Note to Self", "")
            app.active_key = me
            app.selected = 0
            # Simulate the race: the send lands while the first history load is still pending.
            app.pending_history.add(me)
            for ch in "Testing":
                await app.handle_key(Key("char", char=ch))
            await app.handle_key(Key("enter"))
            for _ in range(60):
                if app.messages.get(me) and app.messages[me][-1].get("status") == "sent":
                    break
                await asyncio.sleep(0.05)
            app.pending_history.discard(me)
            await app._load_history(me)          # the late history reply arrives now
            self.assertEqual([m["body"] for m in app.messages[me]], ["Testing"])
            self.assertEqual(app.messages[me][0]["status"], "sent")
            await app.client.close()

    async def test_send_to_self_shows_and_settles(self):
        async with BridgeHarness() as h:
            app = make_app()
            app.paths = h.paths
            app.client = BridgeClient(h.paths, on_event=app._on_event)
            app.status = await app.client.connect()
            app.connected = True
            await app.client.request("subscribe")
            app.contacts = await app.client.request("contacts")
            self.assertEqual(app.contacts[0]["displayName"], "Note to Self")
            me = app.contacts[0]["key"]
            app._ensure_conversation(me, "Note to Self", "")
            app._select_key(me)
            for _ in range(40):
                if me in app.messages:
                    break
                await asyncio.sleep(0.05)
            self.assertIn(me, app.messages)
            for ch in "Testing":
                await app.handle_key(Key("char", char=ch))
            await app.handle_key(Key("enter"))
            self.assertEqual(app.composer.text, "")
            self.assertEqual(app.messages[me][-1]["status"], "sending")
            for _ in range(60):
                if app.messages.get(me) and all(m.get("status") != "sending" for m in app.messages[me]):
                    break
                await asyncio.sleep(0.05)
            msgs = app.messages[me]
            self.assertEqual(len(msgs), 1, msgs)
            self.assertEqual(msgs[0]["status"], "sent")
            self.assertEqual(msgs[0]["body"], "Testing")
            app.term.out.clear()
            app.draw()
            self.assertIn("Testing", app.term.text())
            sent = [s for s in h.sent() if s["method"] == "send"][-1]
            self.assertTrue(sent["params"].get("noteToSelf"))
            await app.client.close()


if __name__ == "__main__":
    unittest.main()
