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
ALLOWED = re.compile(r"\x1b\[[0-9;?<>]* ?[A-Za-z]|\x1b\]8;[^;\x1b]*;[^\x1b]*\x1b\\|\x1b_G[^\x1b]*\x1b\\|\x1b\]0;[^\x07]*\x07")

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

    def test_cursor_sits_on_the_typed_line(self):
        app = make_app(cols=120, rows=40)
        seed(app)
        app.composer.text = "Hello! Let me know"
        app.composer.cursor = len(app.composer.text)
        app.draw()
        out = app.term.text()
        # One composer row: it is drawn at rows-2 (footer is the last row); the
        # cursor must be on that same row, right after the text, blinking.
        self.assertTrue(out.endswith("\x1b[38;19H\x1b[5 q\x1b[?25h\x1b[?2026l".replace("38;19", f"38;{tui.LIST_WIDTH + 4 + len(app.composer.text)}")), out[-60:])
        # A trailing space keeps the cursor after the space.
        app.composer.text = "Hello "
        app.composer.cursor = 6
        app.draw()
        self.assertIn(f"\x1b[38;{tui.LIST_WIDTH + 4 + 6}H\x1b[5 q", app.term.text())
        # Wrapped onto a second line: cursor on the second composer row.
        app.composer.text = "x" * 100 + " tail"
        app.composer.cursor = len(app.composer.text)
        app.draw()
        rows_used = app._composer_rows()
        self.assertEqual(rows_used, 2)
        # 84 x's fill the first line; the second holds the remaining 16 plus " tail" (21 columns).
        self.assertIn(f"\x1b[38;{tui.LIST_WIDTH + 4 + 21}H\x1b[5 q", app.term.text())

    def test_outgoing_bubble_wraps_as_one_block(self):
        app = make_app(cols=120, rows=40)
        seed(app)
        app.cfg.message_layout = "bubbles"
        key = app.active_key
        app.messages[key] = [{"conversation": key, "ts": 1700000050000, "sender": "number:+15550001111", "senderName": "You",
                              "outgoing": True, "body": "Oh, and I'm just testing signal from the command line. If you do get this, send me a picture. Any picture is fine. I want to see if it shows up",
                              "attachments": [], "status": "read", "reactions": {}}]
        app.draw()
        out = app.term.text()
        cols = [int(m.group(1)) for m in re.finditer(r"\x1b\[\d+;(\d+)H\x1b\[38;2;\d+;\d+;\d+m▏", out)]
        self.assertGreaterEqual(len(cols), 2, "expected a wrapped bubble")
        self.assertEqual(len(set(cols)), 1, f"bubble lines start at different columns: {cols}")
        self.assertGreater(cols[0], tui.LIST_WIDTH + 10)   # still on the right-hand side
        # Default layout keeps everything on the left.
        app.cfg.message_layout = "left"
        app.term.out.clear()
        app.draw()
        cols = [int(m.group(1)) for m in re.finditer(r"\x1b\[\d+;(\d+)H\x1b\[38;2;\d+;\d+;\d+m▏", app.term.text())]
        self.assertEqual(set(cols), {tui.LIST_WIDTH + 3})

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


class SettingsOverlayTests(unittest.TestCase):
    def test_settings_overlay_cycles_and_saves(self):
        import tempfile
        from pathlib import Path
        from omarchy_signal.config import SETTINGS, Paths
        with tempfile.TemporaryDirectory() as d:
            app = make_app()
            seed(app)
            app.paths = Paths(config_dir=Path(d) / "cfg")
            asyncio.run(app.handle_key(Key("char", char="s", ctrl=True)))
            self.assertEqual(app.overlay, "settings")
            app.draw()
            out = app.term.text()
            self.assertIn("SETTINGS", out)
            self.assertIn("NOTIFICATIONS", out)
            self.assertIn("Notification content", out)
            idx = next(i for i, x in enumerate(SETTINGS) if x["key"] == "notification_content")
            app.settings_index = idx
            asyncio.run(app.handle_key(Key("enter")))
            self.assertEqual(app.cfg.notification_content, "name-only")
            asyncio.run(app.handle_key(Key("left")))
            self.assertEqual(app.cfg.notification_content, "name-and-message")
            self.assertTrue((Path(d) / "cfg" / "config.toml").is_file())
            # a restart-only key marks itself pending
            app.settings_index = next(i for i, x in enumerate(SETTINGS) if x["key"] == "download_attachments")
            asyncio.run(app.handle_key(Key("enter")))
            self.assertIn("download_attachments", app.settings_pending)
            app.term.out.clear()
            app.draw()
            self.assertIn("⟳ restart", app.term.text())
            # path setting opens the completion prompt
            app.settings_index = next(i for i, x in enumerate(SETTINGS) if x["key"] == "save_dir")
            asyncio.run(app.handle_key(Key("enter")))
            self.assertEqual(app.overlay, "setting-text")
            app.overlay_query = d
            asyncio.run(app.handle_key(Key("enter")))
            self.assertEqual(app.cfg.save_dir, d)
            self.assertEqual(app.overlay, "settings")
            asyncio.run(app.handle_key(Key("escape")))
            self.assertEqual(app.overlay, "")


class AttachmentFlowTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.tmp.name)
        (self.root / "Pictures").mkdir()
        self.img = self.root / "Pictures" / "cat.png"
        self.img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (64).to_bytes(4, "big") + (48).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"0" * 50)
        (self.root / "notes.txt").write_bytes(b"hi")
        self.app = make_app(cols=120, rows=40)
        seed(self.app)
        self.app.cfg.save_dir = str(self.root / "saved")

    def tearDown(self):
        self.tmp.cleanup()

    def test_attach_prompt_completes_and_descends(self):
        app = self.app
        asyncio.run(self._run_attach())
        self.assertEqual(app.composer.attachments, [str(self.img)])

    async def _run_attach(self):
        app = self.app
        await app.handle_key(Key("char", char="a", ctrl=True))
        self.assertEqual(app.overlay, "attach")
        self.assertEqual(app.overlay_query, "~/")
        self.assertTrue(app.overlay_results)          # home directory listing
        app.overlay_query = str(self.root) + "/"
        app._path_candidates()
        self.assertEqual([c.name for c in app.overlay_results], ["Pictures/", "notes.txt"])
        app.draw()
        out = app.term.text()
        self.assertIn("Pictures/", out)
        self.assertIn("ATTACH FILE", out)
        for ch in "Pi":
            await app.handle_key(Key("char", char=ch))
        self.assertEqual([c.name for c in app.overlay_results], ["Pictures/"])
        await app.handle_key(Key("tab"))               # accept → descends into the folder
        self.assertTrue(app.overlay_query.endswith("/Pictures/"))
        self.assertEqual([c.name for c in app.overlay_results], ["cat.png"])
        app.draw()
        self.assertIn("64×48", app.term.text())        # image dimensions shown in the dropdown
        await app.handle_key(Key("enter"))             # Enter on a file candidate attaches it
        self.assertEqual(app.overlay, "")

    def test_inline_images_click_mode(self):
        app = self.app
        app.graphics = True
        app.cfg.inline_images = "click"
        key = app.active_key
        att = {"id": "a1", "contentType": "image/png", "filename": "cat.png", "size": 91, "path": str(self.img)}
        app.messages[key] = [{"conversation": key, "ts": 1700000000000, "sender": "number:+15550002222", "senderName": "Trinity",
                              "outgoing": False, "body": "", "attachments": [att], "status": "", "reactions": {}}]
        app.draw()
        out = app.term.text()
        self.assertIn("click to show", out)
        self.assertNotIn("\x1b_Ga=p", out)              # not placed yet
        row = next(r for r, spans in app.link_map.items() if any(h.startswith("file://") for _, _, h in spans))
        start, _, _ = app.link_map[row][0]
        asyncio.run(app._handle_mouse(Key("mouse", x=start + 1, y=row, button=0)))
        self.assertEqual(app.overlay, "")                 # revealed directly, no menu
        app.term.out.clear()
        app.draw()
        out = app.term.text()
        self.assertIn("\x1b_Ga=t,f=100", out)             # transmitted once…
        self.assertIn("\x1b_Ga=p,i=", out)                # …and placed
        self.assertNotIn("click to show", out)
        # Second click now opens the menu, and v hides it again.
        app.draw()
        row = next(r for r, spans in app.link_map.items() if any(h.startswith("file://") for _, _, h in spans))
        start, _, _ = app.link_map[row][0]
        asyncio.run(app._handle_mouse(Key("mouse", x=start + 1, y=row, button=0)))
        self.assertEqual(app.overlay, "attachment")
        app.close_overlay()
        # Clicking the picture itself (the rows above the name) opens the menu too.
        app.term.out.clear()
        app.draw()
        image_rows = [r for r, spans in app.link_map.items() if any(h.startswith("file://") for _, _, h in spans)]
        self.assertGreaterEqual(len(image_rows), 2, "image rows should be click targets")
        top_row = min(image_rows)
        x0, _, _ = app.link_map[top_row][0]
        asyncio.run(app._handle_mouse(Key("mouse", x=x0 + 1, y=top_row, button=0)))
        self.assertEqual(app.overlay, "attachment")
        asyncio.run(app.handle_key(Key("char", char="v")))
        app.term.out.clear()
        app.draw()
        self.assertIn("click to show", app.term.text())
        # never: no hint, no image, menu still offers open/save
        app.cfg.inline_images = "never"
        app.hidden.clear(); app.revealed.clear()
        app.term.out.clear()
        app.draw()
        self.assertNotIn("\x1b_Ga=p", app.term.text())

    def test_attachment_menu_open_save_saveas(self):
        app = self.app
        key = app.active_key
        att = {"id": "a1", "contentType": "image/png", "filename": "cat", "size": 91, "path": str(self.img)}
        app.messages[key] = [{"conversation": key, "ts": 1700000000000, "sender": "number:+15550002222", "senderName": "Trinity",
                              "outgoing": False, "body": "", "attachments": [att], "status": "", "reactions": {}}]

        async def go():
            await app.handle_key(Key("char", char="o", ctrl=True))
            self.assertEqual(app.overlay, "attachment")
            app.draw()
            out = app.term.text()
            self.assertIn("ATTACHMENT", out)
            self.assertIn("cat.png", out)               # extension inferred from the content type
            self.assertIn("64×48", out)
            self.assertIn("Trinity · ", out)            # sender and time on the row
            self.assertIn(app._fmt_time(1700000000000), out)
            await app.handle_key(Key("char", char="s"))  # save to save_dir
            self.assertEqual(app.overlay, "")
            saved = self.root / "saved" / "cat.png"
            self.assertTrue(saved.is_file())
            self.assertEqual(saved.read_bytes(), self.img.read_bytes())
            await app.handle_key(Key("char", char="o", ctrl=True))
            await app.handle_key(Key("char", char="s"))  # again: never overwrites
            self.assertTrue((self.root / "saved" / "cat-1.png").is_file())
            # save as: prompt prefilled with save_dir/name, completion works, Enter saves
            await app.handle_key(Key("char", char="o", ctrl=True))
            await app.handle_key(Key("char", char="a"))
            self.assertEqual(app.overlay, "saveas")
            self.assertTrue(app.overlay_query.endswith("/saved/cat.png"))
            app.overlay_query = str(self.root / "renamed.png")
            app._path_candidates()
            await app.handle_key(Key("enter"))
            self.assertTrue((self.root / "renamed.png").is_file())
            # mouse click on the attachment link opens the menu instead of xdg-open
            app.draw()
            row = next(r for r, spans in app.link_map.items() if any(h.startswith("file://") for _, _, h in spans))
            start, _, _ = app.link_map[row][0]
            await app._handle_mouse(Key("mouse", x=start + 1, y=row, button=0))
            self.assertEqual(app.overlay, "attachment")
        asyncio.run(go())
