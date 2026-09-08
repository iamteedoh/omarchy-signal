import unittest

import _helpers  # noqa: F401
from omarchy_signal import emoji


class EmojiTests(unittest.TestCase):
    def test_lookup_and_replace(self):
        self.assertEqual(emoji.lookup("smile"), "😄")
        self.assertEqual(emoji.lookup("SMILE"), "😄")
        self.assertIsNone(emoji.lookup("nope"))
        self.assertEqual(emoji.replace_shortcodes("hi :smile: and :+1:!"), "hi 😄 and 👍!")
        self.assertEqual(emoji.replace_shortcodes("time 10:30:45 and :unknown_code:"), "time 10:30:45 and :unknown_code:")
        self.assertEqual(emoji.replace_shortcodes("http://x.io:8080/a"), "http://x.io:8080/a")
        self.assertEqual(emoji.replace_shortcodes("no colons"), "no colons")

    def test_search(self):
        codes = [c for c, _ in emoji.search("sm")]
        self.assertEqual(codes[0], "smile")
        self.assertIn("smiley", codes)
        self.assertEqual(emoji.search(""), [])
        self.assertEqual(len(emoji.search("a", limit=3)), 3)
        self.assertEqual([c for c, _ in emoji.search("thumb")][:2], ["thumbsup", "thumbsdown"])

    def test_partial(self):
        self.assertEqual(emoji.partial_at("hello :sm", 9), (6, "sm"))
        self.assertIsNone(emoji.partial_at("hello :sm x", 11))
        self.assertIsNone(emoji.partial_at("10:30", 5))
        self.assertIsNone(emoji.partial_at("hello", 5))
        self.assertEqual(emoji.partial_at(":+1", 3), (0, "+1"))

    def test_table_is_sane(self):
        for code, glyph in emoji.SHORTCODES.items():
            self.assertRegex(code, r"^[a-z0-9_+\-]+$")
            self.assertTrue(glyph and not glyph.isascii(), code)


if __name__ == "__main__":
    unittest.main()
