# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal link preview tests
## Date Created: 2026-11-17
## Description: The Link previews setting: parsing, storage, the bounded fetcher and sending through the bridge.
"""Link previews: off by default, received cards parsed and stored, and the
fetcher for cards on links you send refusing anything it should not touch."""

import asyncio
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal import linkpreview
from omarchy_signal.config import SETTINGS, Config
from omarchy_signal.envelope import parse_receive
from omarchy_signal.store import Store
from omarchy_signal.client import BridgeClient
from test_bridge_integration import BridgeHarness, incoming

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def preview_envelope(ts, preview):
    env = incoming("look https://example.com/post", ts)
    env["envelope"]["dataMessage"]["previews"] = [preview]
    return env


class SettingTests(unittest.TestCase):
    def test_off_by_default_and_live(self):
        self.assertFalse(Config().link_previews)
        self.assertTrue(Config.from_dict({"link_previews": True}).link_previews)
        spec = next(s for s in SETTINGS if s["key"] == "link_previews")
        self.assertEqual(spec["type"], "bool")
        self.assertEqual(spec["section"], "Chats & media")
        self.assertFalse(spec.get("restart"))


class EnvelopeTests(unittest.TestCase):
    def test_preview_parsed_and_sanitised(self):
        evs = parse_receive(preview_envelope(5, {
            "url": "https://example.com/post", "title": "A \x1b[31mtitle‮", "description": "line\nnext",
            "image": {"id": "abc.jpg", "contentType": "image/jpeg", "filename": "x.jpg", "size": 10}}))
        pv = evs[0].previews[0]
        self.assertEqual(pv.url, "https://example.com/post")
        self.assertEqual(pv.title, "A [31mtitle")
        self.assertEqual(pv.description, "line next")
        self.assertEqual(pv.image.id, "abc.jpg")

    def test_bad_previews_dropped(self):
        for bad in ({"url": "javascript:alert(1)", "title": "x"}, {"url": "file:///etc/passwd", "title": "x"},
                    {"url": "https://a.example/ b", "title": "x"}, {"url": "https://a.example/‮", "title": "x"},
                    {"title": "no url"}, "not a dict"):
            self.assertEqual(parse_receive(preview_envelope(6, bad))[0].previews, [], bad)
        # A picture that is not an image is dropped, the card is kept.
        ev = parse_receive(preview_envelope(7, {"url": "https://a.example", "title": "t",
                                                "image": {"id": "x", "contentType": "text/html"}}))[0]
        self.assertIsNone(ev.previews[0].image)

    def test_only_one_preview(self):
        env = incoming("x", 8)
        env["envelope"]["dataMessage"]["previews"] = [{"url": "https://a.example", "title": "a"},
                                                      {"url": "https://b.example", "title": "b"}]
        self.assertEqual([p.url for p in parse_receive(env)[0].previews], ["https://a.example"])


class StoreTests(unittest.TestCase):
    def test_round_trip_and_cleared_on_delete(self):
        store = Store(":memory:")
        ev = parse_receive(preview_envelope(9, {"url": "https://example.com/post", "title": "Post"}))[0]
        store.add_event_message(ev)
        hist = store.history(ev.conversation.key)
        self.assertEqual(hist[0].to_json()["previews"][0]["title"], "Post")
        store.apply_remote_delete(type(ev)(kind="remote_delete", conversation=ev.conversation, timestamp=10,
                                           sender=ev.sender, delete_target_ts=9))
        self.assertEqual(store.history(ev.conversation.key)[0].to_json()["previews"], [])

    def test_old_database_gains_the_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "h.sqlite"
            Store(str(db)).close()
            con = sqlite3.connect(db)
            con.execute("ALTER TABLE messages DROP COLUMN previews")
            con.commit()
            con.close()
            store = Store(str(db))
            cols = {r["name"] for r in store.db.execute("PRAGMA table_info(messages)")}
            self.assertIn("previews", cols)


class FakeResponse:
    def __init__(self, status, ctype, body, location=None):
        self.status, self._body = status, body
        self._headers = {"Content-Type": ctype, "Location": location, "Content-Length": str(len(body))}

    def getheader(self, name, default=None):
        return self._headers.get(name) or default

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]


class FakeWeb:
    """An opener for linkpreview.fetch: host -> {path: response}."""

    def __init__(self, sites):
        self.sites, self.requested = sites, []

    def __call__(self, host):
        web = self

        class Conn:
            def request(self, method, path, headers=None):
                web.requested.append((host, path))
                self.resp = web.sites.get(host, {}).get(path) or FakeResponse(404, "text/plain", b"")

            def getresponse(self):
                return self.resp

            def close(self):
                pass
        return Conn()


PAGE = b"""<html><head><title>Fallback</title>
<meta property="og:title" content="The &amp; Title">
<meta property="og:description" content="About \x1b[2J it">
<meta property="og:image" content="/img/card.png"></head><body><meta property="og:title" content="late"></body></html>"""


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "previews"

    def tearDown(self):
        self.tmp.cleanup()

    def test_page_with_picture(self):
        web = FakeWeb({"example.com": {"/post": FakeResponse(200, "text/html; charset=utf-8", PAGE),
                                       "/img/card.png": FakeResponse(200, "image/png", PNG)}})
        pv = linkpreview.fetch("https://example.com/post", self.dir, opener=web)
        self.assertEqual(pv.title, "The & Title")
        self.assertEqual(pv.description, "About [2J it")
        self.assertEqual(pv.image_type, "image/png")
        self.assertEqual(Path(pv.image_path).read_bytes(), PNG)
        self.assertEqual(os.stat(pv.image_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)

    def test_picture_gets_the_time_the_page_left(self):
        # GitHub draws its picture on the first request, so the picture is not
        # held to the page's short timeout: it gets the rest of the budget.
        timeouts = []

        class Pinned:
            def __init__(self, host, ip, timeout):
                timeouts.append(timeout)
                self.conn = web(host)

            def __getattr__(self, name):
                return getattr(self.conn, name)

        web = FakeWeb({"example.com": {"/post": FakeResponse(200, "text/html", PAGE),
                                       "/img/card.png": FakeResponse(200, "image/png", PNG)}})
        clock = iter([100.0, 101.0])       # the page took a second
        with mock.patch.object(linkpreview, "_PinnedHTTPS", Pinned), \
             mock.patch.object(linkpreview, "_resolve", return_value="93.184.216.34"), \
             mock.patch.object(linkpreview.time, "monotonic", lambda: next(clock)):
            pv = linkpreview.fetch("https://example.com/post", self.dir, budget=7.5)
        self.assertTrue(pv.image_path)
        self.assertEqual(timeouts, [linkpreview.TIMEOUT_S, 6.5])

    def test_out_of_time_keeps_the_title_without_the_picture(self):
        web = FakeWeb({"example.com": {"/post": FakeResponse(200, "text/html", PAGE),
                                       "/img/card.png": FakeResponse(200, "image/png", PNG)}})
        clock = iter([100.0, 108.0])       # the page used up the whole budget
        with mock.patch.object(linkpreview.time, "monotonic", lambda: next(clock)):
            pv = linkpreview.fetch("https://example.com/post", self.dir, opener=web, budget=7.5)
        self.assertEqual(pv.title, "The & Title")
        self.assertEqual(pv.image_path, "")
        self.assertNotIn(("example.com", "/img/card.png"), web.requested)

    def test_link_straight_to_a_picture(self):
        web = FakeWeb({"i.example": {"/cat.jpg": FakeResponse(200, "image/jpeg", JPEG)}})
        pv = linkpreview.fetch("https://i.example/cat.jpg", self.dir, opener=web)
        self.assertEqual((pv.title, pv.description, pv.image_type), ("cat.jpg", "i.example", "image/jpeg"))

    def test_picture_that_is_not_one_is_skipped(self):
        web = FakeWeb({"example.com": {"/post": FakeResponse(200, "text/html", PAGE),
                                       "/img/card.png": FakeResponse(200, "image/png", b"<svg onload=x>")}})
        pv = linkpreview.fetch("https://example.com/post", self.dir, opener=web)
        self.assertEqual(pv.title, "The & Title")
        self.assertEqual(pv.image_path, "")

    def test_redirects_are_checked(self):
        web = FakeWeb({"a.example": {"/": FakeResponse(301, "text/html", b"", location="http://b.example/")}})
        self.assertIsNone(linkpreview.fetch("https://a.example/", self.dir, opener=web))
        loop = FakeWeb({"a.example": {"/": FakeResponse(302, "text/html", b"", location="/")}})
        self.assertIsNone(linkpreview.fetch("https://a.example/", self.dir, opener=loop))
        self.assertEqual(len(loop.requested), linkpreview.MAX_REDIRECTS + 1)

    def test_refusals(self):
        web = FakeWeb({})
        for url in ("http://example.com/", "https://example.com:8443/", "https://user:pw@example.com/", "ftp://x/"):
            self.assertIsNone(linkpreview.fetch(url, self.dir, opener=web), url)
        self.assertEqual(web.requested, [])
        no_title = FakeWeb({"e.example": {"/": FakeResponse(200, "text/html", b"<html><head></head></html>")}})
        self.assertIsNone(linkpreview.fetch("https://e.example/", self.dir, opener=no_title))
        binary = FakeWeb({"e.example": {"/": FakeResponse(200, "application/zip", b"PK")}})
        self.assertIsNone(linkpreview.fetch("https://e.example/", self.dir, opener=binary))

    def test_private_addresses_refused(self):
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.4.53", "169.254.169.254", "::1", "fd00::1", "::ffff:192.168.1.1", "0.0.0.0"):
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            with mock.patch("socket.getaddrinfo", return_value=[(fam, socket.SOCK_STREAM, 6, "", (ip, 443))]):
                with self.assertRaises(linkpreview.PreviewError, msg=ip):
                    linkpreview._resolve("intranet.example")
        mixed = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
                 (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.1.1", 443))]
        with mock.patch("socket.getaddrinfo", return_value=mixed):
            with self.assertRaises(linkpreview.PreviewError):
                linkpreview._resolve("rebind.example")
        with mock.patch("socket.getaddrinfo", return_value=mixed[:1]):
            self.assertEqual(linkpreview._resolve("example.com"), "93.184.215.14")

    def test_first_link(self):
        self.assertEqual(linkpreview.first_link("see http://a.example and https://b.example/x."), "https://b.example/x")
        self.assertEqual(linkpreview.first_link("nothing"), "")


class BridgePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, h, on_event=None):
        c = BridgeClient(h.paths, on_event=on_event)
        return c, await c.connect()

    async def test_off_sends_no_preview_and_fetches_nothing(self):
        async with BridgeHarness() as h:
            c, _ = await self._client(h)
            with mock.patch.object(linkpreview, "fetch") as fetch:
                await c.request("send", conversation="number:+15550002222", text="see https://example.com/post")
            fetch.assert_not_called()
            sent = [s for s in h.sent() if s["method"] == "send"][-1]["params"]
            self.assertNotIn("previewUrl", sent)
            hello = await c.request("status")
            self.assertFalse(hello["linkPreviews"])
            await c.close()

    async def test_on_attaches_the_preview(self):
        async with BridgeHarness(link_previews=True) as h:
            c, _ = await self._client(h)
            img = h.paths.attachments_dir / "outgoing-previews" / "preview-tmp.png"
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(PNG)
            fake = linkpreview.LinkPreview(url="https://example.com/post", title="Post", description="About",
                                           image_path=str(img), image_type="image/png")
            with mock.patch.object(linkpreview, "fetch", return_value=fake) as fetch:
                res = await c.request("send", conversation="number:+15550002222", text="see https://example.com/post.")
            self.assertEqual(fetch.call_args[0][0], "https://example.com/post")
            sent = [s for s in h.sent() if s["method"] == "send"][-1]["params"]
            self.assertEqual((sent["previewUrl"], sent["previewTitle"], sent["previewDescription"]),
                             ("https://example.com/post", "Post", "About"))
            self.assertEqual(sent["previewImage"], str(img))
            hist = await c.request("history", conversation="number:+15550002222")
            pv = hist[-1]["previews"][0]
            self.assertEqual(pv["title"], "Post")
            kept = Path(pv["image"]["path"])
            self.assertEqual(kept, h.paths.attachments_dir / f"preview-{res['ts']}.png")
            self.assertTrue(kept.is_file())
            self.assertFalse(img.exists())
            await c.close()

    async def test_slow_site_does_not_hold_the_message(self):
        async with BridgeHarness(link_previews=True) as h:
            c, _ = await self._client(h)
            h.bridge.PREVIEW_BUDGET_S = 0.2

            def slow(*a, **k):
                import time
                time.sleep(1)
            with mock.patch.object(linkpreview, "fetch", side_effect=slow):
                res = await c.request("send", conversation="number:+15550002222", text="https://slow.example/")
            self.assertEqual(res["status"], "sent")
            self.assertNotIn("previewUrl", [s for s in h.sent() if s["method"] == "send"][-1]["params"])
            await c.close()

    async def test_received_preview_reaches_history(self):
        async with BridgeHarness() as h:
            events = []
            c, _ = await self._client(h, on_event=lambda n, d: events.append((n, d)))
            await c.request("subscribe")
            h.inject(preview_envelope(1700000000123, {"url": "https://example.com/post", "title": "Post"}))
            for _ in range(100):
                if any(n == "message" for n, _ in events):
                    break
                await asyncio.sleep(0.05)
            msg = [d for n, d in events if n == "message"][0]
            self.assertEqual(msg["previews"][0]["url"], "https://example.com/post")
            hist = await c.request("history", conversation="number:+15550002222")
            self.assertEqual(json.dumps(hist[-1]["previews"][0]["title"]), '"Post"')
            await c.close()


if __name__ == "__main__":
    unittest.main()
