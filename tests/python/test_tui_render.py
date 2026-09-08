"""Headless rendering tests for the terminal client.

A fake Terminal captures the byte stream; we assert the screen is drawn
without exceptions for every mode and that hostile message content never
leaks raw control characters into the output (only our own escape codes,
which all start with ESC followed by '[', ']8;', '_G' or a known SGR).
"""

import asyncio
import re
import unittest

import _helpers  # noqa: F401
from omarchy_signal import tui
from omarchy_signal.config import Config, Paths
from omarchy_signal.term import Key
from omarchy_signal.theme import Theme


class FakeTerminal:
    RESET = tui.T.RESET
    BOLD = tui.T.BOLD
    DIM = tui.T.DIM
    ITALIC = tui.T.ITALIC
    UNDERLINE = tui.T.UNDERLINE
    REVERSE = tui.T.REVERSE
    CLEAR = tui.T.CLEAR
    CLEAR_LINE = tui.T.CLEAR_LINE
    SHOW_CURSOR = tui.T.SHOW_CURSOR
    HIDE_CURSOR = tui.T.HIDE_CURSOR

    def __init__(self, cols=120, rows=40):
        self.cols, self.rows = cols, rows
        self.cell_w, self.cell_h = 10, 20
        self.fd_in = 0
        self.out = []

    def write(self, s):
        self.out.append(s)

    def flush(self):
        pass

    def measure(self):
        pass

    def text(self):
        return "".join(self.out)

    move = staticmethod(tui.T.move)
    fg = staticmethod(tui.T.fg)
    bg = staticmethod(tui.T.bg)
    title = staticmethod(tui.T.title)


# Allowed escape sequences in our output.
ALLOWED = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z]|\x1b\]8;[^;\x1b]*;[^\x1b]*\x1b\\|\x1b_G[^\x1b]*\x1b\\|\x1b\]0;[^\x07]*\x07")

HOSTILE = "hi \x1b]52;c;cHduZWQ=\x07 \x1b[2J \x1b_Ga=T;AAAA\x1b\\ a‮b \x9b1m end"


def make_app(cols=120, rows=40):
    app = tui.App(Config(), Paths(), initial="")
    app.term = FakeTerminal(cols, rows)
    app.theme = Theme()
    app.connected = True
    app.status = {"linked": True, "connected": True}
    app.graphics = False
    return app


def seed(app):
    convs = [
        {"key": "number:+15550002222", "kind": "number", "name": "Trinity", "lastTs": 1700000100000, "preview": HOSTILE, "unread": 2},
        {"key": "group:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==", "kind": "group", "name": "Crew", "lastTs": 1700000000000, "preview": "yo", "unread": 0, "muted": True},
    ]
    app.conversations = convs
    app.active_key = convs[0]["key"]
    app.selected = 0
    app.messages[convs[0]["key"]] = [
        {"conversation": convs[0]["key"], "ts": 1700000000000, "sender": "number:+15550002222", "senderName": "Trin\x1bity",
         "outgoing": False, "body": HOSTILE + " see https://example.com/x?y=1 and https://x.io/\x1b]8;;evil\x1b\\", "attachments": [
             {"id": "a", "contentType": "image/png", "filename": "..\\..\\x\x1b.png", "size": 12345, "path": "/nonexistent/a.png"},
             {"id": "b", "contentType": "application/pdf", "filename": "doc.pdf", "size": 999}],
         "status": "", "reactions": {"number:+1": "🔥"}, "quoteText": "q\x1b[31muote", "expiresIn": 3600},
        {"conversation": convs[0]["key"], "ts": 1700000050000, "sender": "number:+15550001111", "senderName": "You",
         "outgoing": True, "body": "I know kung fu.\n" * 3, "attachments": [], "status": "read", "reactions": {}, "edited": True},
        {"conversation": convs[0]["key"], "ts": 1700000060000, "sender": "number:+15550002222", "senderName": "Trinity",
         "outgoing": False, "body": "", "attachments": [], "status": "", "deleted": True},
    ]
    app.typing[convs[0]["key"]] = ("Trinity", 9e12)


class RenderTests(unittest.TestCase):
    def assert_clean(self, output):
        stripped = ALLOWED.sub("", output)
        self.assertNotIn("\x1b", stripped, "unexpected escape in output")
        self.assertNotIn("\x9b", stripped)
        self.assertNotIn("\x07", stripped)
        self.assertNotIn("‮", stripped)
        # The literal word may survive as plain text; it must never become a link target.
        for href in re.findall(r"\x1b\]8;[^;]*;([^\x1b]*)\x1b\\\\", output):
            self.assertNotIn("\x1b", href)
            self.assertTrue(href.startswith(("https://", "http://", "mailto:", "file:///")), href)
            self.assertNotIn(";evil", href)

    def test_draws_all_panes_with_hostile_content(self):
        app = make_app()
        seed(app)
        app.draw()
        out = app.term.text()
        self.assert_clean(out)
        self.assertIn("Trinity", out)
        self.assertIn("SIGNAL // OMARCHY", out)
        self.assertIn("\x1b]8;;https://example.com/x?y=1\x1b\\", out)
        self.assertIn("kung fu", out)
        self.assertIn("message deleted", out)
        self.assertIn("🔥", out)

    def test_every_overlay_renders(self):
        app = make_app()
        seed(app)
        app.contacts = [{"key": "number:+15550003333", "displayName": "Morp\x1bheus", "number": "+15550003333"}]
        app.groups = [{"key": "group:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==", "name": "Crew", "members": []}]
        for overlay in ("contacts", "search", "attach", "react", "help", "quit", "link"):
            with self.subTest(overlay=overlay):
                app.term.out.clear()
                app.overlay = overlay
                app.focus = "overlay"
                app.overlay_query = "\x1b[31mq"
                app.overlay_results = app.messages[app.active_key] if overlay == "search" else []
                if overlay == "contacts":
                    app.overlay_items = [{"key": c["key"], "name": c["displayName"], "sub": c["number"], "kind": "contact"} for c in app.contacts]
                    app._filter_overlay()
                if overlay == "link":
                    app.link_uri = "sgnl://linkdevice?uuid=abc&pub_key=def"
                app.draw()
                self.assert_clean(app.term.text())
        app.overlay = ""

    def test_small_terminal_and_empty_state(self):
        app = make_app(cols=40, rows=10)
        app.draw()
        self.assertIn("terminal too small", app.term.text())
        app = make_app()
        app.draw()
        self.assertIn("No conversation selected", app.term.text())

    def test_boot_screen(self):
        app = make_app()
        app.boot_progress = 0.5
        app._draw_boot("Decrypting channel")
        self.assertIn("DECRYPTING CHANNEL", app.term.text())

    def test_composer_editing_keys(self):
        app = make_app()
        seed(app)

        async def go():
            for ch in "hello world":
                await app.handle_key(Key("char", char=ch))
            await app.handle_key(Key("char", char="w", ctrl=True))
            self.assertEqual(app.composer.text, "hello ")
            await app.handle_key(Key("backspace"))
            await app.handle_key(Key("home"))
            await app.handle_key(Key("char", char=">"))
            self.assertEqual(app.composer.text, ">hello")
            await app.handle_key(Key("enter", alt=True))
            self.assertIn("\n", app.composer.text)
            await app.handle_key(Key("char", char="x", ctrl=True))
            self.assertEqual(app.composer.text, "")
            # navigation
            await app.handle_key(Key("tab"))
            self.assertEqual(app.focus, "list")
            await app.handle_key(Key("char", char="j"))
            self.assertEqual(app.selected, 1)
            await app.handle_key(Key("escape"))
            self.assertEqual(app.focus, "composer")
            await app.handle_key(Key("char", char="?"))
            self.assertEqual(app.overlay, "help")
            await app.handle_key(Key("escape"))
            await app.handle_key(Key("char", char="c", ctrl=True))
            self.assertEqual(app.overlay, "quit")
            await app.handle_key(Key("char", char="y"))
            self.assertFalse(app.running)
        asyncio.run(go())
        app.draw()
        self.assert_clean(app.term.text())

    def test_mouse_selects_conversation(self):
        app = make_app()
        seed(app)
        asyncio.run(app._handle_mouse(Key("mouse", x=3, y=5, button=0)))
        # Two-line rows: row 5 is the second conversation's name line.
        self.assertEqual(app.selected, 1)

    def test_events_update_state(self):
        app = make_app()
        seed(app)
        key = app.active_key

        async def go():
            await app._on_event("message", {"conversation": key, "conversationName": "Trinity", "sender": "number:+15550002222",
                                            "senderName": "Trinity", "ts": 1700000200000, "text": "new\x1b[0m", "preview": "new",
                                            "attachments": [], "outgoing": False, "notify": True})
            await app._on_event("typing", {"conversation": key, "senderName": "Trinity", "typing": True})
            await app._on_event("receipt", {"type": "read", "timestamps": [1700000050000]})
            await app._on_event("reaction", {"conversation": key, "ts": 1700000200000, "sender": "number:+1", "emoji": "👍"})
            await app._on_event("deleted", {"conversation": key, "ts": 1700000200000})
            await app._on_event("unread", {"total": 0, "conversation": key})
            await app._on_event("status", {"connected": False})
            await app._on_event("message", "garbage")
        asyncio.run(go())
        self.assertEqual(app.messages[key][-1]["ts"], 1700000200000)
        self.assertTrue(app.messages[key][-1]["deleted"])
        self.assertFalse(app.connected)
        app.draw()
        self.assert_clean(app.term.text())


if __name__ == "__main__":
    unittest.main()
