# SPDX-License-Identifier: GPL-3.0-or-later
"""Disappearing messages are enforced, not just displayed: a message is gone
from every query once its timer (started when sent, or when read) runs out,
and the bridge deletes it and its downloaded files."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal import store as store_mod
from omarchy_signal.envelope import Attachment, Event
from omarchy_signal.sanitize import Recipient
from omarchy_signal.store import Store

ALICE = Recipient("number", "+15550002222")
ME = Recipient("number", "+15550001111")


def msg(ts, text="hi", outgoing=False, expires_in=0, **kw):
    return Event(kind="message", conversation=ALICE, timestamp=ts, sender=ME if outgoing else ALICE,
                 sender_name="Alice", outgoing=outgoing, text=text, expires_in=expires_in, **kw)


class StoreExpiryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "h.sqlite3"
        self.store = Store(self.path)
        self.now = 1_800_000_000_000
        patcher = mock.patch.object(store_mod, "_now_ms", side_effect=lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def bodies(self):
        return [m.body for m in self.store.history(ALICE.key)]

    def test_received_message_starts_its_timer_when_read(self):
        self.store.add_event_message(msg(self.now - 5000, "secret", expires_in=30))
        self.store.add_event_message(msg(self.now - 4000, "keep"))
        self.now += 3600 * 1000                       # unread for an hour: still there, as in Signal
        self.assertEqual(self.bodies(), ["secret", "keep"])
        self.store.mark_read(ALICE.key)
        self.now += 29_000
        self.assertEqual(self.bodies(), ["secret", "keep"])
        self.now += 1_000
        self.assertEqual(self.bodies(), ["keep"], "hidden from history the moment it expires")
        self.assertEqual(self.store.search("secret"), [])
        self.assertIsNone(self.store.message(ALICE.key, self.now - 3600 * 1000 - 30_000 - 5000))
        gone = self.store.expire()
        self.assertEqual([m.body for m in gone], ["secret"])
        rows = self.store.db.execute("SELECT body FROM messages").fetchall()
        self.assertEqual([r["body"] for r in rows], ["keep"], "and deleted from the database")

    def test_sent_message_starts_its_timer_when_sent(self):
        self.store.add_outgoing(ALICE, self.now, ME.key, "mine", expires_in=10)
        self.store.add_event_message(msg(self.now + 1, "from phone", outgoing=True, expires_in=10))
        self.now += 10_001
        self.assertEqual(self.bodies(), [])
        self.assertEqual(len(self.store.expire()), 2)

    def test_preview_does_not_outlive_the_message(self):
        self.store.add_event_message(msg(self.now - 2000, "older"))
        self.store.add_event_message(msg(self.now - 1000, "secret", expires_in=5))
        self.assertEqual(self.store.conversation(ALICE.key).last_preview, "secret")
        self.store.mark_read(ALICE.key)
        self.now += 5000
        self.store.expire()
        self.assertEqual(self.store.conversation(ALICE.key).last_preview, "older")

    def test_no_timer_means_no_expiry(self):
        self.store.add_event_message(msg(self.now, "plain"))
        self.store.mark_read(ALICE.key)
        self.now += 10 ** 12
        self.assertEqual(self.store.expire(), [])
        self.assertEqual(self.store.next_expiry(), 0)
        self.assertEqual(self.bodies(), ["plain"])

    def test_next_expiry(self):
        self.store.add_outgoing(ALICE, self.now, ME.key, "a", expires_in=60)
        self.store.add_outgoing(ALICE, self.now + 1, ME.key, "b", expires_in=30)
        self.assertEqual(self.store.next_expiry(), self.now + 1 + 30_000)

    def test_old_database_starts_timers_on_upgrade(self):
        self.store.close()
        db = sqlite3.connect(self.path)
        db.execute("DROP INDEX IF EXISTS messages_expire_at")
        db.execute("ALTER TABLE messages DROP COLUMN expire_at")
        db.execute("INSERT INTO messages(conversation, ts, sender, body, expires_in, received_at) VALUES (?, 1, ?, 'old', 60, 0)",
                   (ALICE.key, ALICE.key))
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.next_expiry(), self.now + 60_000)
        self.now += 60_000
        self.assertEqual([m.body for m in self.store.expire()], ["old"])


class BridgeExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_deletes_expired_messages_and_their_files(self):
        from test_bridge_integration import BridgeHarness, incoming
        from omarchy_signal.client import BridgeClient
        async with BridgeHarness() as h:
            events = []
            c = BridgeClient(h.paths, on_event=lambda n, d: events.append((n, d)))
            await c.connect()
            await c.request("subscribe")
            # A received picture signal-cli downloaded, and a file outside its folder.
            att_dir = h.paths.signal_cli_data / "attachments"
            att_dir.mkdir(parents=True, exist_ok=True)
            pic = att_dir / "pic1"
            pic.write_bytes(b"x")
            elsewhere = h.paths.data_dir / "mine.png"
            elsewhere.write_bytes(b"y")
            key = "number:+15550002222"
            ts = int(time.time() * 1000)
            env = incoming("burn after reading", ts, attachments=[{"id": "pic1", "contentType": "image/png", "size": 1}])
            env["envelope"]["dataMessage"]["expiresInSeconds"] = 1
            h.inject(env)
            for _ in range(100):
                if any(n == "message" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            h.bridge.store.db.execute("UPDATE messages SET attachments = ? WHERE ts = ?", (json.dumps([
                Attachment(id="pic1", content_type="image/png", filename="pic1", size=1, path=str(pic)).to_json(),
                Attachment(id="x", content_type="image/png", filename="mine.png", size=1, path=str(elsewhere)).to_json()]), ts))
            self.assertEqual(len(await c.request("history", conversation=key)), 1)
            await c.request("markRead", conversation=key)
            for _ in range(80):
                if any(n == "removed" and d.get("expired") for n, d in events):
                    break
                await asyncio.sleep(0.1)
            removed = [d for n, d in events if n == "removed"]
            self.assertTrue(removed and removed[-1]["ts"] == ts and removed[-1]["expired"])
            self.assertEqual(await c.request("history", conversation=key), [])
            self.assertEqual(removed[-1]["preview"], "", "the event carries the list's new preview line")
            self.assertFalse(removed[-1]["outgoing"])
            self.assertFalse(pic.exists(), "the downloaded picture goes with the message")
            self.assertTrue(elsewhere.exists(), "nothing outside signal-cli's attachments folder is touched")
            await c.close()


    async def test_the_timer_follows_what_messages_say(self):
        """A timer set on the phone or by the other person arrives with their
        messages; the conversation adopts it, and turning it off is seen too."""
        from test_bridge_integration import BridgeHarness, incoming
        from omarchy_signal.client import BridgeClient
        async with BridgeHarness() as h:
            events = []
            c = BridgeClient(h.paths, on_event=lambda n, d: events.append((n, d)))
            await c.connect()
            await c.request("subscribe")
            key = "number:+15550002222"

            async def timer_after(env):
                n = len([1 for name, _ in events if name == "message"])
                h.inject(env)
                for _ in range(100):
                    if len([1 for name, _ in events if name == "message"]) > n:
                        break
                    await asyncio.sleep(0.05)
                return h.bridge.store.conversation(key).expiration

            ts = int(time.time() * 1000)
            env = incoming("now with a timer", ts)
            env["envelope"]["dataMessage"]["expiresInSeconds"] = 3600
            self.assertEqual(await timer_after(env), 3600)
            self.assertIn(("conversation", {"conversation": key, "expiration": 3600}), events)
            off = incoming("", ts + 1)
            off["envelope"]["dataMessage"].update(message=None, expiresInSeconds=0, isExpirationUpdate=True)
            self.assertEqual(await timer_after(off), 0)
            # An envelope that does not state a timer leaves it alone.
            env = incoming("timer again", ts + 2)
            env["envelope"]["dataMessage"]["expiresInSeconds"] = 60
            self.assertEqual(await timer_after(env), 60)
            silent = incoming("no word on timers", ts + 3)
            del silent["envelope"]["dataMessage"]["expiresInSeconds"]
            self.assertEqual(await timer_after(silent), 60)
            await c.close()


if __name__ == "__main__":
    unittest.main()
