# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal client driven end to end against the real bridge (fake signal-cli)."""

import asyncio
import unittest

import _helpers  # noqa: F401
from omarchy_signal import tui
from omarchy_signal.client import BridgeClient
from omarchy_signal.term import Key
from test_bridge_integration import BridgeHarness, incoming
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


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    """Deleting from the terminal client, against the real bridge."""

    async def _app(self, h):
        app = make_app()
        app.paths = h.paths
        app.client = BridgeClient(h.paths, on_event=app._on_event)
        app.status = await app.client.connect()
        await app.client.request("subscribe")
        return app

    async def test_d_for_me_takes_the_message_out_of_the_thread(self):
        async with BridgeHarness() as h:
            app = await self._app(h)
            key = "number:+15550002222"
            sent = await app.client.request("send", conversation=key, text="a line")
            app.active_key = key
            await app._load_history(key)
            self.assertIn(sent["ts"], [m["ts"] for m in app.messages[key]])

            await app.client.request("deleteLocal", conversation=key, ts=sent["ts"])
            for _ in range(60):
                if sent["ts"] not in [m["ts"] for m in app.messages.get(key, [])]:
                    break
                await asyncio.sleep(0.05)
            self.assertNotIn(sent["ts"], [m["ts"] for m in app.messages.get(key, [])],
                             "the event did not take it out of the open thread")
            # And it is gone from the bridge too, not just from the screen.
            self.assertEqual(await app.client.request("history", conversation=key), [])
            await app.client.close()

    async def test_d_says_why_instead_of_offering_a_delete_signal_would_not_honour(self):
        import time
        from omarchy_signal.sanitize import parse_conversation_key
        async with BridgeHarness() as h:
            app = await self._app(h)
            key = "number:+15550002222"
            old = int(time.time() * 1000) - 25 * 60 * 60 * 1000
            h.bridge.store.add_outgoing(parse_conversation_key(key), old, "number:+15550001111", "yesterday's")
            fresh = (await app.client.request("send", conversation=key, text="just now"))["ts"]
            app.active_key = key
            await app._load_history(key)
            app.pick_items = list(app.messages[key])
            by_ts = {m["ts"]: i for i, m in enumerate(app.pick_items)}

            app.overlay = "pick"
            app.pick_index = by_ts[old]
            await app._pick_key(Key("char", char="d"))
            self.assertIn("24 hours", app.toast)
            self.assertNotEqual(app.overlay, "prompt", "it must not ask to confirm something that will be refused")

            app.pick_index = by_ts[fresh]
            await app._pick_key(Key("char", char="d"))
            self.assertEqual((app.overlay, app.pick_action), ("prompt", "delete"))
            self.assertIn("delete for everyone", app.overlay_message)
            await app.client.close()

    async def test_a_reply_sent_from_elsewhere_does_not_hide_the_history(self):
        # A reply from the chat window or a notification arrives here as "sent".
        # It used to create the thread with only that reply in it, and the
        # history of a chat opened afterwards never loaded.
        async with BridgeHarness() as h:
            app = await self._app(h)
            key = "number:+15550002222"
            h.inject(incoming("earlier, from them", 1700000000301))
            for _ in range(100):
                if (await app.client.request("history", conversation=key)):
                    break
                await asyncio.sleep(0.05)
            ts = (await app.client.request("send", conversation=key, text="my reply"))["ts"]
            for _ in range(60):
                if any(c["key"] == key and c.get("lastTs") == ts for c in app.conversations):
                    break
                await asyncio.sleep(0.05)
            self.assertNotIn(key, app.messages, "a thread nobody opened must not be cached")
            app.active_key = key
            await app._load_history(key)
            self.assertEqual([m["body"] for m in app.messages[key]], ["earlier, from them", "my reply"])
            await app.client.close()

    async def test_deleting_the_chat_closes_it_and_drops_it_from_the_list(self):
        async with BridgeHarness() as h:
            app = await self._app(h)
            key = "number:+15550002222"
            await app.client.request("send", conversation=key, text="a line")
            app.conversations = await app.client.request("conversations")
            app.active_key = key
            await app._load_history(key)

            await app.client.request("deleteConversation", conversation=key)
            for _ in range(60):
                if key not in [c["key"] for c in app.conversations]:
                    break
                await asyncio.sleep(0.05)
            self.assertNotIn(key, [c["key"] for c in app.conversations])
            self.assertNotIn(key, app.messages)
            self.assertEqual(app.active_key, "", "the deleted chat stayed open")
            await app.client.close()


if __name__ == "__main__":
    unittest.main()
