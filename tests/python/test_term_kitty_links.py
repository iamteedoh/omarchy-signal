import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import kitty, links, term
from omarchy_signal.config import Config
from omarchy_signal.theme import Theme, mix, parse_hex


class WidthTests(unittest.TestCase):
    def test_widths(self):
        self.assertEqual(term.str_width("abc"), 3)
        self.assertEqual(term.str_width("日本"), 4)
        self.assertEqual(term.str_width("🎉"), 2)
        self.assertEqual(term.str_width("é"), 1)

    def test_truncate_and_pad(self):
        self.assertEqual(term.truncate("hello world", 5), "hell…")
        self.assertEqual(term.truncate("hi", 5), "hi")
        self.assertEqual(term.pad("ab", 4), "ab  ")
        self.assertEqual(term.pad("ab", 4, align="right"), "  ab")
        self.assertEqual(term.pad("ab", 5, align="center"), " ab  ")

    def test_wrap(self):
        self.assertEqual(term.wrap("the quick brown fox", 9), ["the quick", "brown fox"])
        self.assertEqual(term.wrap("abcdefghij", 4), ["abcd", "efgh", "ij"])
        self.assertEqual(term.wrap("a\n\nb", 10), ["a", "", "b"])
        self.assertEqual(term.wrap("", 10), [""])


class KeyParserTests(unittest.TestCase):
    def keys(self, data):
        p = term.KeyParser()
        return [str(k) for k in p.feed(data)] + [str(k) for k in p.flush()]

    def test_plain_and_ctrl(self):
        self.assertEqual(self.keys(b"a\r\t\x7f\x03"), ["a", "enter", "tab", "backspace", "ctrl-c"])

    def test_arrows_and_function_keys(self):
        self.assertEqual(self.keys(b"\x1b[A\x1b[B\x1bOC\x1b[3~\x1b[5~\x1b[1;5D\x1b[Z"),
                         ["up", "down", "right", "delete", "pageup", "ctrl-left", "backtab"])

    def test_alt_and_escape(self):
        self.assertEqual(self.keys(b"\x1bx"), ["alt-x"])
        self.assertEqual(self.keys(b"\x1b"), ["escape"])
        self.assertEqual(self.keys(b"\x1b\x1b"), ["escape", "escape"])

    def test_mouse(self):
        keys = term.KeyParser().feed(b"\x1b[<0;12;5M\x1b[<64;1;1M")
        self.assertEqual((keys[0].name, keys[0].x, keys[0].y, keys[0].button, keys[0].release), ("mouse", 12, 5, 0, False))
        self.assertEqual(keys[1].button, 64)

    def test_kitty_protocol_and_responses_swallowed(self):
        self.assertEqual(self.keys(b"\x1b[97;5u"), ["ctrl-a"])
        self.assertEqual(self.keys(b"\x1b_Gi=31;OK\x1b\\\x1b[?62;c"), [])
        self.assertEqual(self.keys(b"\x1b]11;rgb:00/00/00\x07q"), ["q"])

    def test_partial_sequences_wait(self):
        p = term.KeyParser()
        self.assertEqual(p.feed(b"\x1b["), [])
        self.assertEqual([str(k) for k in p.feed(b"A")], ["up"])

    def test_garbage_never_types(self):
        out = self.keys(b"\x1b[999999999999999999999999999999999999999999999999999999999999999999999X")
        self.assertEqual(out, [])


class LinkTests(unittest.TestCase):
    def test_find_urls(self):
        spans = links.find_urls("see https://example.com/a?b=1). and http://x.io/y, mailto:a@b.co ok")
        self.assertEqual([u for _, _, u in spans], ["https://example.com/a?b=1", "http://x.io/y", "mailto:a@b.co"])
        self.assertEqual(links.find_urls("nothing here"), [])
        self.assertEqual(links.find_urls("(https://en.wikipedia.org/wiki/Foo_(bar))"), [(1, 40, "https://en.wikipedia.org/wiki/Foo_(bar)")])

    def test_safe_href(self):
        self.assertEqual(links.safe_href("https://example.com/ä b"), "https://example.com/%C3%A4%20b")
        self.assertIsNone(links.safe_href("javascript:alert(1)"))
        self.assertIsNone(links.safe_href("file:///etc/passwd"))
        self.assertIsNone(links.safe_href("https://x.io/\x1b]8;;evil\x1b\\"))
        self.assertIsNone(links.safe_href("https://x.io/\x07"))
        self.assertIsNone(links.safe_href("https://" + "x" * 3000))
        self.assertEqual(links.file_href("/home/u/a b.png"), "file:///home/u/a%20b.png")
        self.assertIsNone(links.file_href("relative/x"))

    def test_osc8_structure(self):
        s = links.osc8("https://x.io", "X", link_id="a1")
        self.assertEqual(s, "\x1b]8;id=a1;https://x.io\x1b\\X\x1b]8;;\x1b\\")
        self.assertNotIn("id=", links.osc8("https://x.io", "X", link_id="bad id"))


class KittyTests(unittest.TestCase):
    def test_probe_dimensions(self):
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / "a.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")
            info = kitty.probe_dimensions(png)
            self.assertEqual((info.width, info.height, info.format), (640, 480, "png"))
            gif = Path(d) / "a.gif"
            gif.write_bytes(b"GIF89a" + (12).to_bytes(2, "little") + (34).to_bytes(2, "little"))
            info = kitty.probe_dimensions(gif)
            self.assertEqual((info.width, info.height, info.format), (12, 34, "gif"))
            txt = Path(d) / "a.txt"
            txt.write_bytes(b"hello")
            self.assertIsNone(kitty.probe_dimensions(txt))

    @unittest.skipUnless(shutil.which("magick"), "ImageMagick not installed")
    def test_convert_jpeg_and_bomb_limits(self):
        with tempfile.TemporaryDirectory() as d:
            jpg = Path(d) / "a.jpg"
            subprocess.run(["magick", "-size", "3000x2000", "xc:red", str(jpg)], check=True)
            info = kitty.probe_dimensions(jpg)
            self.assertEqual((info.width, info.height, info.format), (3000, 2000, "jpeg"))
            png = kitty.to_png(jpg, max_side=400)
            self.assertTrue(png.startswith(b"\x89PNG"))
            w, h = kitty.probe_dimensions_bytes(png) if hasattr(kitty, "probe_dimensions_bytes") else (None, None)
            # A decoder bomb claiming 100000x100000 must be refused without decoding.
            bomb = Path(d) / "bomb.png"
            bomb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (100000).to_bytes(4, "big") + (100000).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00")
            self.assertIsNone(kitty.to_png(bomb))

    def test_encode(self):
        seq = kitty.encode_transmit(b"\x89PNG" + b"x" * 5000, 7, cols=10, rows=4)
        self.assertTrue(seq.startswith("\x1b_Ga=T,f=100,t=d,q=2,i=7,p=1,c=10,r=4,C=1,m=1;"))
        self.assertTrue(seq.endswith("\x1b\\"))
        self.assertEqual(seq.count("\x1b_G"), 2)
        self.assertIn("m=0", seq.split("\x1b_G")[-1])
        self.assertEqual(kitty.encode_place(7, cols=3, rows=2), "\x1b_Ga=p,i=7,p=1,c=3,r=2,C=1,q=2\x1b\\")
        self.assertRaises(ValueError, kitty.encode_transmit, b"", 0, cols=1, rows=1)
        self.assertEqual(kitty.encode_delete(7), "\x1b_Ga=d,d=i,i=7,q=2\x1b\\")

    def test_fit_cells(self):
        self.assertEqual(kitty.fit_cells(1000, 500, max_cols=40, max_rows=10, cell_w=10, cell_h=20), (40, 10))
        self.assertEqual(kitty.fit_cells(100, 100, max_cols=40, max_rows=10, cell_w=10, cell_h=20), (10, 5))
        self.assertEqual(kitty.fit_cells(0, 0, max_cols=40, max_rows=10), (20, 8))

    def test_support_detection_from_env(self):
        old = dict(os.environ)
        try:
            os.environ.update({"TERM": "xterm-ghostty", "TERM_PROGRAM": ""})
            os.environ.pop("KITTY_WINDOW_ID", None)
            self.assertTrue(kitty.terminal_supports_graphics(probe=False))
            os.environ.update({"TERM": "xterm-256color", "TERM_PROGRAM": "foot"})
            self.assertFalse(kitty.terminal_supports_graphics(probe=False))
            os.environ["OMARCHY_SIGNAL_IMAGES"] = "off"
            os.environ["TERM"] = "xterm-kitty"
            self.assertFalse(kitty.terminal_supports_graphics(probe=False))
        finally:
            os.environ.clear()
            os.environ.update(old)


class ThemeConfigTests(unittest.TestCase):
    def test_parse_hex_and_mix(self):
        self.assertEqual(parse_hex("#ff0000"), (255, 0, 0))
        self.assertEqual(parse_hex("0f0"), (0, 255, 0))
        self.assertEqual(parse_hex("garbage", (1, 2, 3)), (1, 2, 3))
        self.assertEqual(mix((0, 0, 0), (100, 100, 100), 0.5), (50, 50, 50))

    def test_theme_load(self):
        with tempfile.TemporaryDirectory() as d:
            theme_dir = Path(d) / "current" / "theme"
            theme_dir.mkdir(parents=True)
            (theme_dir / "colors.toml").write_text('mode = "light"\naccent = "#123456"\nbad = 5\n')
            (theme_dir.parent / "theme.name").write_text("lumon\n")
            th = Theme.load(theme_dir)
            self.assertEqual(th.accent, (0x12, 0x34, 0x56))
            self.assertEqual(th.name, "lumon")
            self.assertFalse(th.is_dark)
            self.assertFalse(th.changed_on_disk(theme_dir))
            (theme_dir / "colors.toml").write_text("this is = not toml [[[")
            os.utime(theme_dir / "colors.toml", (1, 1))
            self.assertTrue(th.changed_on_disk(theme_dir))
            th2 = Theme.load(theme_dir)
            self.assertEqual(th2.accent, Theme().accent)
            self.assertEqual(Theme.load(Path(d) / "missing").name, "unknown")

    def test_config_defaults_and_clamps(self):
        cfg = Config.from_dict({"notifications": "weird", "image_max_rows": 999, "history_retain_days": -3,
                                "trust_new_identities": "always", "send_read_receipts": 0,
                                "bridge": {"signal_cli": "/opt/signal-cli"}})
        self.assertEqual(cfg.notifications, "popup")
        self.assertEqual(cfg.image_max_rows, 60)
        self.assertEqual(cfg.history_retain_days, 0)
        self.assertEqual(cfg.trust_new_identities, "always")
        self.assertFalse(cfg.send_read_receipts)
        self.assertEqual(cfg.signal_cli, "/opt/signal-cli")
        self.assertEqual(Config.from_dict({}).account, "")


if __name__ == "__main__":
    unittest.main()


class SettingsTests(unittest.TestCase):
    def test_coerce_and_schema(self):
        from omarchy_signal.config import RESTART_REQUIRED, SETTINGS, coerce_setting
        self.assertTrue(coerce_setting("send_read_receipts", "yes"))
        self.assertFalse(coerce_setting("send_read_receipts", "0"))
        self.assertEqual(coerce_setting("notification_timeout_ms", "999999"), 120000)
        self.assertEqual(coerce_setting("notification_content", "none"), "none")
        for bad in (("notification_content", "loud"), ("image_max_rows", "x"), ("send_read_receipts", "maybe"), ("nope", "1"),
                    ("save_dir", "a\nb")):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError, coerce_setting, *bad)
        self.assertIn("download_attachments", RESTART_REQUIRED)
        self.assertNotIn("inline_images", RESTART_REQUIRED)
        keys = [x["key"] for x in SETTINGS]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertTrue(hasattr(Config(), key), key)

    def test_save_roundtrip_and_permissions(self):
        import os, stat
        from omarchy_signal.config import Paths, save_config
        with tempfile.TemporaryDirectory() as d:
            paths = Paths(config_dir=Path(d) / "cfg")
            cfg = Config()
            cfg.notification_content = "none"
            cfg.save_dir = "~/Pictures/Signal \"quoted\""
            cfg.history_retain_days = 30
            cfg.send_read_receipts = False
            path = save_config(cfg, paths)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            back = Config.load(paths)
            self.assertEqual(back.notification_content, "none")
            self.assertFalse(back.notification_preview)
            self.assertEqual(back.save_dir, cfg.save_dir)
            self.assertEqual(back.history_retain_days, 30)
            self.assertFalse(back.send_read_receipts)
            text = path.read_text()
            self.assertIn("# ── Notifications ──", text)
            self.assertIn("(restart)", text)

    def test_legacy_preview_flag_maps_to_name_only(self):
        cfg = Config.from_dict({"notification_preview": False})
        self.assertEqual(cfg.notification_content, "name-only")
        cfg = Config.from_dict({"notification_preview": False, "notification_content": "none"})
        self.assertEqual(cfg.notification_content, "none")
