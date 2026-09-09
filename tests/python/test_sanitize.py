# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import unicodedata

import _helpers  # noqa: F401
from omarchy_signal import sanitize
from omarchy_signal.sanitize import (InvalidRecipient, classify_recipient, clean_name, clean_text,
                                     contains_control, parse_conversation_key, safe_filename)

# A corpus of terminal-injection payloads a hostile sender could put in a message.
INJECTIONS = [
    "\x1b]0;pwned\x07",                       # OSC title change
    "\x1b]52;c;cHduZWQ=\x07",                 # OSC 52 clipboard write
    "\x1b]8;;http://evil\x1b\\click\x1b]8;;\x1b\\",  # OSC 8 hyperlink
    "\x1b_Ga=T,f=100;AAAA\x1b\\",             # kitty graphics
    "\x1b[2J\x1b[H",                          # clear screen / home
    "\x1b[?1049h",                            # alternate screen
    "\x1bP+q\x1b\\",                          # DCS
    "\x9b2J",                                 # C1 CSI
    "\x9d0;x\x9c",                            # C1 OSC/ST
    "\x1b[6n",                                # cursor position report (can echo input)
    "\x07\x08\x0c\x7f",                       # bell, backspace, formfeed, delete
    "a‮bc‬",                        # RLO override
    "⁦hidden⁩",                     # isolates
    "﻿",                                 # BOM
    "\ud800",                                 # lone surrogate
    "\U000f0431",                             # private use (nerd font glyph)
    "\x1b(0lqqk\x1b(B",                       # charset switch to line drawing
]


class CleanTextTests(unittest.TestCase):
    def test_injections_are_neutralised(self):
        for payload in INJECTIONS:
            with self.subTest(payload=repr(payload)):
                out = clean_text("hello " + payload + " world")
                self.assertFalse(contains_control(out), repr(out))
                self.assertNotIn("\x1b", out)
                for ch in out:
                    self.assertNotIn(unicodedata.category(ch), ("Cc", "Cf", "Cs", "Co", "Cn"),
                                     f"{ch!r} survived in {out!r}")
                self.assertIn("hello", out)
                self.assertIn("world", out)

    def test_preserves_ordinary_unicode_and_emoji(self):
        text = "Ünïcödé ✓ 日本語 🎉 👨‍👩‍👧 ❤️"
        self.assertEqual(clean_text(text), unicodedata.normalize("NFC", text))

    def test_newlines_kept_tabs_expanded(self):
        self.assertEqual(clean_text("a\r\nb\tc\rd"), "a\nb    c\nd")
        self.assertEqual(clean_text("a\nb", single_line=True), "a b")

    def test_length_capped(self):
        self.assertEqual(len(clean_text("x" * 100000)), sanitize.MAX_TEXT_LENGTH)
        self.assertEqual(clean_text("abcdef", max_length=3), "abc")

    def test_non_strings(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(42), "42")
        self.assertEqual(clean_text({"a": 1}), "{'a': 1}")

    def test_clean_name_is_single_line_and_trimmed(self):
        self.assertEqual(clean_name("  Neo\n\x1b[31m Anderson  "), "Neo [31m Anderson")
        self.assertEqual(clean_name("x" * 500), "x" * 120)


class RecipientTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(classify_recipient("+15551234567").key, "number:+15551234567")
        self.assertEqual(classify_recipient(" +4915112345678 ").kind, "number")
        for bad in ("15551234567", "+0123", "+1 555 123 4567", "+1555123456789012345", "+", "+abc"):
            with self.subTest(bad=bad):
                self.assertRaises(InvalidRecipient, classify_recipient, bad)

    def test_uuid(self):
        rec = classify_recipient("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
        self.assertEqual(rec.kind, "uuid")
        self.assertEqual(rec.value, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_group(self):
        gid = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="
        self.assertEqual(classify_recipient(gid).kind, "group")
        self.assertEqual(classify_recipient("group:" + gid).value, gid)
        self.assertRaises(InvalidRecipient, classify_recipient, "group:../../etc/passwd")
        self.assertRaises(InvalidRecipient, classify_recipient, "group:")

    def test_username(self):
        self.assertEqual(classify_recipient("trinity.42").kind, "username")
        self.assertEqual(classify_recipient("u:neo_1.007").value, "neo_1.007")
        self.assertRaises(InvalidRecipient, classify_recipient, "u:no.digits")

    def test_option_injection_rejected(self):
        # Nothing that argparse could read as a flag may become a recipient.
        for bad in ("--note-to-self", "-g", "-e", "--attachment=/etc/passwd", "-", "--"):
            with self.subTest(bad=bad):
                self.assertRaises(InvalidRecipient, classify_recipient, bad)

    def test_control_and_whitespace_rejected(self):
        for bad in ("+1555123\x004567", "+1555\n1234567", "+1555 1234567", "\x1b+15551234567", ""):
            with self.subTest(bad=repr(bad)):
                self.assertRaises(InvalidRecipient, classify_recipient, bad)
        self.assertRaises(InvalidRecipient, classify_recipient, 123)
        self.assertRaises(InvalidRecipient, classify_recipient, None)

    def test_conversation_key_roundtrip(self):
        for raw in ("+15551234567", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "trinity.42",
                    "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="):
            rec = classify_recipient(raw)
            self.assertEqual(parse_conversation_key(rec.key), rec)
        for bad in ("number:trinity.42", "nope:+15551234567", "number", "", None, "group:+15551234567"):
            with self.subTest(bad=bad):
                self.assertRaises(InvalidRecipient, parse_conversation_key, bad)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.tmp.name)
        self.file = self.root / "photo.png"
        self.file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_regular_file_in_home(self):
        self.assertEqual(sanitize.safe_attachment_path(str(self.file)), self.file.resolve())

    def test_rejects_traversal_outside_roots(self):
        for bad in ("/etc/passwd", "/etc/shadow", str(self.root / ".." / ".." / ".." / "etc" / "hostname"), "/proc/self/environ"):
            with self.subTest(bad=bad):
                self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, bad)

    def test_rejects_symlink_escaping_root(self):
        link = self.root / "escape"
        link.symlink_to("/etc/hostname")
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, str(link))

    def test_rejects_non_regular_and_empty(self):
        (self.root / "empty").write_bytes(b"")
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, str(self.root / "empty"))
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, str(self.root))
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, "/dev/zero")
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, "data:text/plain;base64,QUJD")
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, "a\x00b")
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, "")

    def test_rejects_oversize(self):
        big = self.root / "big"
        with open(big, "wb") as fh:
            fh.truncate(sanitize.MAX_ATTACHMENT_BYTES + 1)
        self.assertRaises(sanitize.InvalidAttachment, sanitize.safe_attachment_path, str(big))

    def test_safe_filename(self):
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(safe_filename("..\\..\\x.png"), "x.png")
        self.assertEqual(safe_filename("\x1b[31mred.png"), "[31mred.png")
        self.assertEqual(safe_filename(""), "attachment")
        self.assertEqual(safe_filename(".."), "attachment")
        self.assertEqual(safe_filename("   "), "attachment")
        self.assertEqual(safe_filename("a/b/c"), "c")
        self.assertEqual(safe_filename(".hidden"), "hidden")


if __name__ == "__main__":
    unittest.main()
