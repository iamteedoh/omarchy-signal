# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end: real Bridge + real Store + real socket server, fake signal-cli."""

import asyncio
import contextlib
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import protocol
from omarchy_signal.bridge import Bridge
from omarchy_signal.client import BridgeClient, BridgeError
from omarchy_signal.config import Config, Paths

FAKE = Path(__file__).with_name("fake_signal_cli.py")
GID = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="


class BridgeHarness:
    def __init__(self, unlinked=False, **cfg_over):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        root = Path(self.tmp.name)
        self.events_file = root / "events.jsonl"
        self.sent_log = root / "sent.jsonl"
        self.paths = Paths(config_dir=root / "cfg", data_dir=root / "data", state_dir=root / "state",
                           run_dir=root / "run", signal_cli_data=root / "signal-cli", omarchy_theme_dir=root / "theme")
        os.environ["FAKE_SIGNAL_EVENTS"] = str(self.events_file)
        os.environ["FAKE_SIGNAL_SENT_LOG"] = str(self.sent_log)
        if unlinked:
            os.environ["FAKE_SIGNAL_UNLINKED"] = "1"
        else:
            os.environ.pop("FAKE_SIGNAL_UNLINKED", None)
        self.cfg = Config.from_dict({"signal_cli": str(FAKE), **cfg_over})
        self.bridge = Bridge(self.cfg, self.paths)
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.bridge.run())
        for _ in range(200):
            if self.paths.socket.exists() and self.bridge.supervisor.ready.is_set() and (self.bridge.account or os.environ.get("FAKE_SIGNAL_UNLINKED")):
                break
            await asyncio.sleep(0.05)
        # Wait for the directory refresh so contacts exist.
        for _ in range(100):
            if self.bridge.store.contacts() or os.environ.get("FAKE_SIGNAL_UNLINKED"):
                break
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        self.bridge._shutdown.set()
        try:
            await asyncio.wait_for(self.task, 10)
        except BaseException:
            pass
        self.tmp.cleanup()

    def inject(self, params):
        with open(self.events_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(params) + "\n")

    def sent(self):
        if not self.sent_log.exists():
            return []
        return [json.loads(l) for l in self.sent_log.read_text().splitlines() if l.strip()]


def incoming(text, ts, number="+15550002222", name="Trinity", group=None, attachments=None):
    data = {"timestamp": ts, "message": text, "expiresInSeconds": 0, "viewOnce": False}
    if group:
        data["groupInfo"] = {"groupId": group, "groupName": "Crew", "type": "DELIVER"}
    if attachments:
        data["attachments"] = attachments
    return {"envelope": {"source": number, "sourceNumber": number, "sourceUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                         "sourceName": name, "sourceDevice": 1, "timestamp": ts, "dataMessage": data}}


class BridgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, h, on_event=None):
        c = BridgeClient(h.paths, on_event=on_event)
        hello = await c.connect()
        return c, hello

    async def test_socket_permissions(self):
        async with BridgeHarness() as h:
            self.assertEqual(stat.S_IMODE(h.paths.socket.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(h.paths.run_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(h.paths.database.stat().st_mode), 0o600)

    async def test_hello_status_and_directory(self):
        async with BridgeHarness() as h:
            c, hello = await self._client(h)
            self.assertTrue(hello["connected"])
            self.assertTrue(hello["linked"])
            status = await c.request("status")
            self.assertEqual(status["accountCount"], 1)
            contacts = await c.request("contacts")
            names = sorted(x["displayName"] for x in contacts)
            # Blocked contact hidden, garbage number dropped, profile name sanitised.
            self.assertEqual(names, ["Morpheus", "Note to Self", "Trinity"])
            trinity = [x for x in contacts if x["displayName"] == "Trinity"][0]
            self.assertEqual(trinity["profileName"], "Trin [31mEvil")
            groups = await c.request("groups")
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["name"], "Nebuchadnezzar crew")
            self.assertNotIn("‮", groups[0]["name"])
            await c.close()

    async def test_incoming_message_flow(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            h.inject(incoming("hello \x1b[2Jworld", 1700000000001))
            for _ in range(100):
                if any(n == "message" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            msg = [d for n, d in events if n == "message"][0]
            self.assertEqual(msg["text"], "hello [2Jworld")
            self.assertEqual(msg["senderName"], "Trinity")
            self.assertEqual(msg["conversationName"], "Trinity")
            self.assertTrue(msg["notify"])
            self.assertFalse(msg["isGroup"])
            unread = [d for n, d in events if n == "unread"][-1]
            self.assertEqual(unread["total"], 1)
            convs = await c.request("conversations")
            self.assertEqual(convs[0]["key"], "number:+15550002222")
            self.assertEqual(convs[0]["unread"], 1)
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertEqual(hist[0]["body"], "hello [2Jworld")
            # Duplicate delivery is ignored.
            h.inject(incoming("hello \x1b[2Jworld", 1700000000001))
            await asyncio.sleep(0.3)
            self.assertEqual(len([1 for n, _ in events if n == "message"]), 1)
            # markRead sends a read receipt to the author.
            res = await c.request("markRead", conversation="number:+15550002222")
            self.assertEqual(res["receipts"], 1)
            for _ in range(50):
                if any(s["method"] == "sendReceipt" for s in h.sent()):
                    break
                await asyncio.sleep(0.05)
            receipt = [s for s in h.sent() if s["method"] == "sendReceipt"][0]
            self.assertEqual(receipt["params"]["recipient"], "+15550002222")   # a string: this is what syncs the read to the phone
            self.assertEqual(receipt["params"]["targetTimestamp"], [1700000000001])
            self.assertEqual((await c.request("status"))["unread"], 0)
            await c.close()

    async def test_send_and_group_and_react(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            res = await c.request("send", conversation="number:+15550002222", text="yo \x1b]52;c;x\x07")
            self.assertEqual(res["status"], "sent")
            sent = [s for s in h.sent() if s["method"] == "send"][0]
            self.assertEqual(sent["params"]["recipient"], ["+15550002222"])
            self.assertEqual(sent["params"]["message"], "yo ]52;c;x")
            self.assertEqual(sent["params"]["account"], "+15550001111")
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertTrue(hist[-1]["outgoing"])
            self.assertEqual(hist[-1]["status"], "sent")
            res = await c.request("send", conversation="group:" + GID, text="group hi")
            sent = [s for s in h.sent() if s["method"] == "send"][-1]
            self.assertEqual(sent["params"]["groupId"], GID)
            self.assertNotIn("recipient", sent["params"])
            await c.request("react", conversation="number:+15550002222", ts=res["ts"], author="number:+15550002222", emoji="🔥")
            sent = [s for s in h.sent() if s["method"] == "sendReaction"][-1]
            self.assertEqual(sent["params"]["emoji"], "🔥")
            self.assertEqual(sent["params"]["targetAuthor"], "+15550002222")
            with self.assertRaises(BridgeError):
                await c.request("react", conversation="number:+15550002222", ts=1, author="number:+15550002222", emoji="abc")
            with self.assertRaises(BridgeError) as ctx:
                await c.request("send", conversation="number:+15550002222", text="FAIL")
            self.assertEqual(ctx.exception.code, "signal")
            await c.close()

    async def test_long_message_keeps_connection(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            res = await c.request("send", conversation="number:+15550002222", text="x" * 60000)
            self.assertEqual(res["status"], "sent")                    # a long message goes through
            with self.assertRaises(BridgeError) as cm:                   # an oversized one fails fast, client-side
                await c.request("send", conversation="number:+15550002222", text="x" * 70000)
            self.assertEqual(cm.exception.code, "bad_request")
            # ...and a raw oversized request gets an error carrying its id, not a hang or a dropped connection.
            r, w = await asyncio.open_unix_connection(str(h.paths.socket), limit=protocol.MAX_REQUEST_BYTES)
            await r.readuntil(b"\n")                                   # hello
            w.write(json.dumps({"id": 9, "op": "send", "conversation": "number:+15550002222", "text": "x" * 70000}).encode() + b"\n")
            w.write(b'{"id":10,"op":"ping"}\n')
            await w.drain()
            first = json.loads(await asyncio.wait_for(r.readuntil(b"\n"), 5))
            second = json.loads(await asyncio.wait_for(r.readuntil(b"\n"), 5))
            self.assertEqual((first["id"], first["ok"], first["code"]), (9, False, "bad_request"))
            self.assertEqual((second["id"], second["result"]), (10, "pong"))
            w.close()
            self.assertEqual(await c.request("ping"), "pong")          # the original connection survived
            await c.close()

    async def test_send_rejects_bad_input(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            for kwargs in (dict(conversation="--note-to-self", text="x"),
                           dict(conversation="number:+15550002222", text="x", attachments=["/etc/passwd"]),
                           dict(conversation="number:+15550002222", text="   "),
                           dict(conversation="number:+15550002222", text="x", attachments=["/dev/null"]),
                           dict(conversation="nope:foo", text="x")):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(BridgeError) as ctx:
                        await c.request("send", **kwargs)
                    self.assertEqual(ctx.exception.code, "invalid")
            self.assertEqual([s for s in h.sent() if s["method"] == "send"], [])
            await c.close()

    async def test_raw_protocol_abuse(self):
        async with BridgeHarness() as h:
            r, w = await asyncio.open_unix_connection(str(h.paths.socket))
            await r.readline()  # hello
            for raw in (b"garbage\n", b"[]\n", b'{"id":1}\n', b'{"id":1,"op":"__class__"}\n',
                        b'{"id":true,"op":"ping"}\n', b'{"id":1,"op":"history","conversation":5}\n'):
                w.write(raw)
                await w.drain()
                resp = json.loads(await r.readline())
                self.assertFalse(resp["ok"])
                self.assertEqual(resp["code"], "bad_request")
            w.write(b'{"id":7,"op":"ping"}\n')
            await w.drain()
            self.assertEqual(json.loads(await r.readline())["result"], "pong")
            # Oversized request line: connection is dropped, bridge keeps serving.
            w.write(b'{"id":8,"op":"send","conversation":"number:+15550002222","text":"' + b"x" * (protocol.MAX_REQUEST_BYTES + 10) + b'"}\n')
            try:
                await w.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            try:
                tail = await r.read()
            except ConnectionResetError:
                tail = b""
            w.close()
            self.assertTrue(tail == b"" or b"too_large" in tail)
            c, hello = await self._client(h)
            self.assertEqual(await c.request("ping"), "pong")
            await c.close()

    async def test_resolve(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            self.assertEqual((await c.request("resolve", query="me"))["name"], "Note to Self")
            self.assertEqual((await c.request("resolve", query="+15550001111"))["key"], "number:+15550001111")
            self.assertEqual((await c.request("contacts"))[0]["displayName"], "Note to Self")
            self.assertEqual((await c.request("resolve", query="trin"))["key"], "number:+15550002222")
            self.assertEqual((await c.request("resolve", query="+15550007777"))["key"], "number:+15550007777")
            self.assertEqual((await c.request("resolve", query="crew"))["kind"], "group")
            with self.assertRaises(BridgeError):
                await c.request("resolve", query="zzzz")
            await c.close()

    async def test_unlinked_and_link_flow(self):
        async with BridgeHarness(unlinked=True) as h:
            c, hello = await self._client(h)
            self.assertFalse(hello["linked"])
            with self.assertRaises(BridgeError) as ctx:
                await c.request("send", conversation="number:+15550002222", text="x")
            self.assertIn("link", ctx.exception.args[0])
            res = await c.request("link", deviceName="test box \x1b[0m")
            self.assertTrue(res["uri"].startswith("sgnl://"))
            fin = await c.request("linkFinish")
            self.assertTrue(fin["linked"])
            await c.close()

    async def test_signal_cli_restart_after_crash(self):
        os.environ["FAKE_SIGNAL_CRASH_ON_EOF"] = "1"
        try:
            async with BridgeHarness() as h:
                proc = h.bridge.supervisor.proc
                proc.stdin.close()
                for _ in range(200):
                    if h.bridge.supervisor.proc is not proc and h.bridge.supervisor.ready.is_set():
                        break
                    await asyncio.sleep(0.05)
                self.assertIsNot(h.bridge.supervisor.proc, proc)
                c, hello = await self._client(h)
                self.assertTrue(hello["connected"])
                await c.close()
        finally:
            os.environ.pop("FAKE_SIGNAL_CRASH_ON_EOF", None)

    async def test_demo_broadcasts_without_storing(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            await c.request("demo", text="hi \x1b[31mthere")
            for _ in range(50):
                if any(n == "message" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            msg = [d for n, d in events if n == "message"][0]
            self.assertTrue(msg["notify"])
            self.assertTrue(msg["demo"])
            self.assertEqual(msg["text"], "hi [31mthere")
            self.assertEqual(await c.request("conversations"), [])
            await c.close()

    async def test_notification_content_levels_and_reload(self):
        from omarchy_signal.config import save_config
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")

            async def next_message(ts):
                h.inject(incoming("secret text", ts))
                for _ in range(100):
                    msgs = [d for n, d in events if n == "message" and d.get("ts") == ts]
                    if msgs:
                        return msgs[0]
                    await asyncio.sleep(0.05)
                self.fail("no message event")

            m = await next_message(1700000000001)
            self.assertEqual((m["senderName"], m["preview"]), ("Trinity", "secret text"))
            # Change the setting on disk (a separate Config, as the CLI would) and
            # ask the bridge to reload: no restart.
            disk = Config.from_dict({k: getattr(h.cfg, k) for k in h.cfg.__dataclass_fields__})
            disk.notification_content = "name-only"
            save_config(disk, h.paths)
            res = await c.request("reloadConfig")
            self.assertIn("notification_content", res["applied"])
            m = await next_message(1700000000002)
            self.assertEqual((m["senderName"], m["preview"]), ("Trinity", "New message"))
            disk.notification_content = "none"
            disk.download_attachments = False      # restart-only key
            save_config(disk, h.paths)
            res = await c.request("reloadConfig")
            self.assertIn("download_attachments", res["restartRequired"])
            m = await next_message(1700000000003)
            self.assertEqual((m["senderName"], m["conversationName"], m["preview"]), ("Signal", "New message", "New message"))
            self.assertEqual(m["sender"], "number:+15550002222")   # the key still routes a reply
            status = await c.request("status")
            self.assertEqual(status["notificationContent"], "none")
            # History keeps the real name regardless of the popup setting.
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertEqual(hist[-1]["senderName"], "Trinity")
            await c.close()

    async def test_message_actions_delete_edit(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            res = await c.request("send", conversation="number:+15550002222", text="typo :smile:")
            ts = res["ts"]
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertEqual(hist[-1]["body"], "typo 😄")          # shortcode expanded by the bridge
            await c.request("edit", conversation="number:+15550002222", ts=ts, text="fixed")
            sent = [s for s in h.sent() if s["method"] == "send"][-1]
            self.assertEqual((sent["params"]["editTimestamp"], sent["params"]["message"]), (ts, "fixed"))
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertEqual((hist[-1]["body"], hist[-1]["edited"]), ("fixed", True))
            await c.request("delete", conversation="number:+15550002222", ts=ts)
            dl = [s for s in h.sent() if s["method"] == "remoteDelete"][-1]
            self.assertEqual(dl["params"]["targetTimestamp"], ts)
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertTrue(hist[-1]["deleted"])
            # only our own messages
            h.inject(incoming("theirs", 1700000000009))
            for _ in range(50):
                if any(n == "message" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            with self.assertRaises(BridgeError):
                await c.request("delete", conversation="number:+15550002222", ts=1700000000009)
            with self.assertRaises(BridgeError):
                await c.request("edit", conversation="number:+15550002222", ts=1700000000009, text="x")
            await c.close()

    async def test_emoji_setting_off_sends_raw(self):
        from omarchy_signal.config import save_config
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            disk = Config.from_dict({k: getattr(h.cfg, k) for k in h.cfg.__dataclass_fields__})
            disk.emoji_autoconvert = False
            save_config(disk, h.paths)
            await c.request("reloadConfig")
            await c.request("send", conversation="number:+15550002222", text="raw :smile: :D")
            sent = [s for s in h.sent() if s["method"] == "send"][-1]
            self.assertEqual(sent["params"]["message"], "raw :smile: :D")
            self.assertFalse((await c.request("status"))["emojiAutoconvert"])
            await c.close()

    async def test_conversation_actions(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            conv = "number:+15550002222"
            self.assertEqual((await c.request("setExpiration", conversation=conv, seconds=3600))["expiration"], 3600)
            uc = [s for s in h.sent() if s["method"] == "updateContact"][-1]
            self.assertEqual((uc["params"]["recipient"], uc["params"]["expiration"]), ("+15550002222", 3600))
            await c.request("setExpiration", conversation="group:" + GID, seconds=86400)
            ug = [s for s in h.sent() if s["method"] == "updateGroup"][-1]
            self.assertEqual((ug["params"]["groupId"], ug["params"]["expiration"]), (GID, 86400))
            convs = await c.request("conversations", includeArchived=True)
            self.assertEqual(next(x for x in convs if x["key"] == conv)["expiration"], 3600)
            await c.request("block", conversation=conv, blocked=True)
            self.assertEqual([s for s in h.sent() if s["method"] == "block"][-1]["params"]["recipient"], ["+15550002222"])
            await c.request("block", conversation=conv, blocked=False)
            self.assertTrue(any(s["method"] == "unblock" for s in h.sent()))
            await c.request("messageRequest", conversation=conv, accept=True)
            self.assertEqual([s for s in h.sent() if s["method"] == "sendMessageRequestResponse"][-1]["params"]["type"], "accept")
            ids = await c.request("identities", conversation=conv)
            self.assertEqual(ids[0]["trustLevel"], "TRUSTED_UNVERIFIED")
            self.assertTrue(ids[0]["safetyNumber"].startswith("12345"))
            await c.request("trust", conversation=conv, safetyNumber="12345 67890")
            tr = [s for s in h.sent() if s["method"] == "trust"][-1]
            self.assertEqual(tr["params"]["verifiedSafetyNumber"], "1234567890")
            with self.assertRaises(BridgeError):
                await c.request("trust", conversation=conv, safetyNumber="12 ab")
            info = await c.request("groupInfo", conversation="group:" + GID)
            self.assertEqual(info["name"], "Nebuchadnezzar crew")
            self.assertIn("Trinity", [m["name"] for m in info["members"]])
            made = await c.request("createGroup", name="Ops\x1b[0m", members=["number:+15550002222", "+15550003333"])
            self.assertTrue(made["key"].startswith("group:"))
            mk = [s for s in h.sent() if s["method"] == "updateGroup"][-1]
            self.assertEqual((mk["params"]["name"], mk["params"]["members"]), ("Ops[0m", ["+15550002222", "+15550003333"]))
            await c.request("renameGroup", conversation="group:" + GID, name="Crew 2")
            await c.request("leaveGroup", conversation="group:" + GID)
            self.assertEqual([s for s in h.sent() if s["method"] == "quitGroup"][-1]["params"]["groupId"], GID)
            # note to self
            await c.request("send", conversation="number:+15550001111", text="remember milk")
            sent = [s for s in h.sent() if s["method"] == "send"][-1]
            self.assertTrue(sent["params"].get("noteToSelf"))
            self.assertNotIn("recipient", sent["params"])
            await c.close()

    async def test_second_instance_refuses(self):
        async with BridgeHarness() as h:
            other = Bridge(h.cfg, h.paths)
            with self.assertRaises(SystemExit):
                await other.run()


class DeleteTests(unittest.IsolatedAsyncioTestCase):
    """Deleting for everyone is Signal's remote delete and leaves a tombstone;
    deleting for me, and deleting a chat, are local and send nothing."""

    async def _client(self, h, on_event=None):
        c = BridgeClient(h.paths, on_event=on_event)
        await c.connect()
        return c, None

    async def _incoming(self, h, c, events, ts, text="hello"):
        h.inject(incoming(text, ts))
        for _ in range(100):
            if any(n == "message" and d.get("ts") == ts for n, d in events):
                return
            await asyncio.sleep(0.05)
        self.fail(f"the message never arrived: {events}")

    async def test_delete_for_me_removes_a_received_message_and_sends_nothing(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            ts = 1700000000101
            await self._incoming(h, c, events, ts)
            key = "number:+15550002222"
            before = len(await c.request("history", conversation=key))
            sent_before = h.sent()

            res = await c.request("deleteLocal", conversation=key, ts=ts)
            self.assertTrue(res["removed"])
            self.assertEqual(h.sent(), sent_before, "a local delete must not reach signal-cli")
            after = await c.request("history", conversation=key)
            self.assertEqual(len(after), before - 1)
            self.assertNotIn(ts, [m["ts"] for m in after], "the message is gone, not tombstoned")
            for _ in range(60):
                if any(n == "removed" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            removed = [d for n, d in events if n == "removed"][-1]
            self.assertEqual((removed["conversation"], removed["ts"]), (key, ts))
            # Deleting it again is not an error, and says nothing happened.
            self.assertFalse((await c.request("deleteLocal", conversation=key, ts=ts))["removed"])
            await c.close()

    async def test_delete_for_everyone_still_leaves_a_tombstone(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            key = "number:+15550002222"
            sent = await c.request("send", conversation=key, text="mine")
            ts = sent["ts"]
            await c.request("delete", conversation=key, ts=ts)
            row = [m for m in await c.request("history", conversation=key) if m["ts"] == ts]
            self.assertEqual(len(row), 1, "a remote delete keeps the row")
            self.assertTrue(row[0]["deleted"])
            self.assertEqual(row[0]["body"], "")
            await c.close()

    async def test_deleting_a_chat_takes_its_messages_and_the_thread(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            key = "number:+15550002222"
            await self._incoming(h, c, events, 1700000000201)
            await self._incoming(h, c, events, 1700000000202, text="and another")
            self.assertTrue(any(conv["key"] == key for conv in await c.request("conversations")))
            sent_before = h.sent()

            res = await c.request("deleteConversation", conversation=key)
            self.assertTrue(res["removed"])
            self.assertGreaterEqual(res["messages"], 2)
            self.assertEqual(h.sent(), sent_before, "deleting a chat must not reach signal-cli")
            self.assertEqual(await c.request("history", conversation=key), [])
            self.assertFalse(any(conv["key"] == key for conv in await c.request("conversations")))
            for _ in range(60):
                if any(n == "conversationRemoved" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            gone = [d for n, d in events if n == "conversationRemoved"][-1]
            self.assertEqual(gone["conversation"], key)
            self.assertEqual([d for n, d in events if n == "unread"][-1]["total"], 0)

            # It comes back by itself when the next message arrives, as on Signal.
            await self._incoming(h, c, events, 1700000000203, text="back again")
            self.assertTrue(any(conv["key"] == key for conv in await c.request("conversations")))
            self.assertEqual(len(await c.request("history", conversation=key)), 1)
            await c.close()

    async def test_delete_for_everyone_is_refused_after_a_day_and_sends_nothing(self):
        # Past Signal's window the other phone ignores the delete while ours
        # applies it, so it would look gone and not be. Refuse, and say why.
        from omarchy_signal.sanitize import parse_conversation_key
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            key = "number:+15550002222"
            old = int(time.time() * 1000) - 25 * 60 * 60 * 1000
            h.bridge.store.add_outgoing(parse_conversation_key(key), old, "number:+15550001111", "yesterday's")
            with self.assertRaises(BridgeError) as caught:
                await c.request("delete", conversation=key, ts=old)
            self.assertIn("24 hours", str(caught.exception))
            self.assertFalse([s for s in h.sent() if s["method"] == "remoteDelete"])
            row = [m for m in await c.request("history", conversation=key) if m["ts"] == old][0]
            self.assertEqual((row["deleted"], row["body"]), (False, "yesterday's"), "a refused delete touches nothing")
            await c.close()

    async def test_note_to_self_is_deleted_as_note_to_self_at_any_age(self):
        from omarchy_signal.sanitize import parse_conversation_key
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            key = "number:+15550001111"                    # the fake account: Note to Self
            old = int(time.time() * 1000) - 30 * 24 * 60 * 60 * 1000
            h.bridge.store.add_outgoing(parse_conversation_key(key), old, key, "a note from last month")
            await c.request("delete", conversation=key, ts=old)
            dl = [s for s in h.sent() if s["method"] == "remoteDelete"][-1]["params"]
            self.assertTrue(dl.get("noteToSelf"), dl)
            self.assertNotIn("recipient", dl, "Note to Self goes as --note-to-self, as send and edit do")
            self.assertEqual(dl["targetTimestamp"], old)
            await c.close()

    async def test_a_message_already_deleted_for_everyone_is_not_deleted_twice(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            key = "number:+15550002222"
            ts = (await c.request("send", conversation=key, text="once"))["ts"]
            await c.request("delete", conversation=key, ts=ts)
            before = len([s for s in h.sent() if s["method"] == "remoteDelete"])
            with self.assertRaises(BridgeError) as caught:
                await c.request("delete", conversation=key, ts=ts)
            self.assertIn("already deleted", str(caught.exception))
            self.assertEqual(len([s for s in h.sent() if s["method"] == "remoteDelete"]), before)
            await c.close()

    async def test_neither_delete_takes_anything_but_a_conversation_key(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            for op, kw in (("deleteLocal", {"ts": 1}), ("deleteConversation", {})):
                for bad in ("", "number:+1 2", "../etc", "number:$(id)"):
                    with self.assertRaises(BridgeError, msg=f"{op} {bad}"):
                        await c.request(op, conversation=bad, **kw)
            with self.assertRaises(BridgeError):
                await c.request("deleteLocal", conversation="number:+15550002222", ts=1, sender="not a key")
            await c.close()


class LinkedStateTests(unittest.IsolatedAsyncioTestCase):
    """`linked: false` must never mean "no account" while the bridge has not yet
    asked signal-cli which accounts exist. Clients offer to link on that answer,
    so a linked computer was being asked to link itself, and shown a QR code,
    after every restart."""

    async def test_status_never_claims_no_account_before_it_has_looked(self):
        # Sampled from the moment the bridge starts, which is where the gap is:
        # signal-cli reports itself ready before listAccounts has answered.
        h = BridgeHarness()
        task = asyncio.create_task(h.bridge.run())
        try:
            seen = []
            for _ in range(400):
                st = h.bridge.status()
                seen.append((bool(st["connected"]), bool(st["accountsKnown"]), bool(st["linked"])))
                if st["linked"]:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(seen[-1][2], f"the fake account never came up: {seen[-3:]}")
            for connected, known, linked in seen:
                # What a client acts on: "there is no account". It must never be said here.
                self.assertFalse(known and not linked, f"claimed there is no account: {seen}")
                # And an account is never reported before it was looked for.
                self.assertFalse(linked and not known, f"linked without having looked: {seen}")
                self.assertFalse(known and not connected, f"knew the accounts while signal-cli was down: {seen}")
        finally:
            h.bridge._shutdown.set()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, 10)
            h.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
