# SPDX-License-Identifier: GPL-3.0-or-later
"""Edits to the two config files the plugin does not own.

Every test here is a shape that broke, or would have broken, a real user's file:
OMSIG-3 (a sed range with no closing marker deleted the rest of bindings.lua)
and OMSIG-4 (a comma-ending comment before the closing brace made the installer
write invalid JSON into omarchy-menu.jsonc).
"""

import json
import re
import unittest

import _helpers  # noqa: F401
from omarchy_signal import confedit

BEGIN = "-- BEGIN omarchy-signal"
END = "-- END omarchy-signal"
BODY = 'hl.unbind("SUPER + SHIFT + G")\no.bind("SUPER + SHIFT + G", "Signal", "omarchy-signal open")'

KEY = "signal-tui"
ENTRY = '"signal-tui": {"icon":"S","label":"Signal (terminal)","action":"omarchy-signal open"}'


def parse_jsonc(text):
    """Parse the way a tolerant JSONC reader does: comments out, trailing commas ok."""
    out, kinds = [], {confedit.CODE, confedit.STRING}
    for _i, ch, kind in confedit.scan(text):
        out.append(ch if kind in kinds else (" " if ch != "\n" else "\n"))
    stripped = "".join(out)
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", stripped))


class MarkedBlockTests(unittest.TestCase):
    def test_appends_to_a_file_without_the_block(self):
        src = 'o.bind("SUPER + T", "Terminal", "alacritty")\n'
        out = confedit.write_block(src, BEGIN, END, BODY)
        self.assertTrue(out.startswith(src))
        self.assertIn(BEGIN, out)
        self.assertTrue(out.endswith(END + "\n"))

    def test_rewrite_keeps_the_block_in_place(self):
        src = f"before()\n{BEGIN}\nold()\n{END}\nafter()\n"
        out = confedit.write_block(src, BEGIN, END, BODY)
        self.assertEqual(out.splitlines()[0], "before()")
        self.assertEqual(out.splitlines()[-1], "after()")
        self.assertNotIn("old()", out)

    def test_rewriting_is_idempotent(self):
        src = "before()\nafter()\n"
        once = confedit.write_block(src, BEGIN, END, BODY)
        self.assertEqual(once, confedit.write_block(once, BEGIN, END, BODY))

    def test_round_trip_restores_the_file_exactly(self):
        src = 'o.bind("SUPER + T", "Terminal", "alacritty")\no.bind("SUPER + B", "Browser", "chromium")\n'
        self.assertEqual(
            confedit.remove_block(confedit.write_block(src, BEGIN, END, BODY), BEGIN, END),
            src,
        )

    def test_unmatched_begin_leaves_the_file_alone(self):
        """OMSIG-3. The sed range this replaces deleted every line below."""
        src = f'a()\n{BEGIN}\norphan()\no.bind("SUPER + B", "Browser", "chromium")\n'
        with self.assertRaises(confedit.UnmatchedMarker):
            confedit.remove_block(src, BEGIN, END)
        with self.assertRaises(confedit.UnmatchedMarker):
            confedit.write_block(src, BEGIN, END, BODY)

    def test_unmatched_end_is_also_refused(self):
        with self.assertRaises(confedit.UnmatchedMarker):
            confedit.remove_block(f"a()\n{END}\nb()\n", BEGIN, END)

    def test_a_marker_inside_a_string_is_not_a_marker(self):
        src = f'local s = "{BEGIN}"\nkeep()\n'
        self.assertEqual(confedit.remove_block(src, BEGIN, END), src)

    def test_removes_every_complete_block(self):
        src = f"a()\n{BEGIN}\nx()\n{END}\nb()\n{BEGIN}\ny()\n{END}\nc()\n"
        self.assertEqual(confedit.remove_block(src, BEGIN, END), "a()\nb()\nc()\n")

    def test_removing_an_absent_block_is_a_no_op(self):
        src = "a()\nb()\n"
        self.assertEqual(confedit.remove_block(src, BEGIN, END), src)


class MenuScannerTests(unittest.TestCase):
    def test_a_brace_in_a_comment_is_not_the_closing_brace(self):
        src = '{\n  "a": 1\n}\n// trailing } in a comment\n'
        self.assertEqual(src[confedit.top_level_close(src)], "}")
        self.assertEqual(confedit.top_level_close(src), src.index("}\n"))

    def test_a_brace_in_a_string_is_not_structure(self):
        src = '{\n  "a": "} not the end"\n}\n'
        self.assertEqual(confedit.top_level_close(src), len(src.rstrip()) - 1)

    def test_a_commented_out_entry_is_not_an_entry(self):
        src = '{\n  // "signal-tui": {"label":"old"},\n  "other": 1\n}\n'
        self.assertIsNone(confedit.entry_span(src, KEY))


class MenuEditTests(unittest.TestCase):
    """Each `src` is a real shape of ~/.config/omarchy/extensions/omarchy-menu.jsonc."""

    SHAPES = {
        "empty object": "{\n}\n",
        "stock omarchy file (comments only, ending in a comma)": (
            "{\n"
            '  // "personal": {"icon":"","label":"Personal"},\n'
            '  // "personal.notes": {"icon":"N","label":"Notes","action":"edit"},\n'
            "}\n"
        ),
        "one entry, no trailing comma": '{\n  "mine": {"label":"Mine"}\n}\n',
        "one entry, trailing comma": '{\n  "mine": {"label":"Mine"},\n}\n',
        "entry then a comment ending in a comma": (
            '{\n  "mine": {"label":"Mine"}\n  // "about": {"label":"About"},\n}\n'
        ),
        "several entries": '{\n  "a": {"label":"A"},\n  "b": {"label":"B"}\n}\n',
        "nested braces in a value": '{\n  "a": {"x":{"y":1}}\n}\n',
        # The last member's value decides where the separating comma goes, so
        # every value type has to be exercised -- a string value ends in `"`,
        # not `}`, which an earlier version of the helper got wrong.
        "string value": '{\n  "a": "text"\n}\n',
        "string value with an escaped quote": '{\n  "a": "say \\"hi\\""\n}\n',
        "string value containing a brace": '{\n  "a": "not } the end"\n}\n',
        "number value": '{\n  "a": 12\n}\n',
        "boolean value": '{\n  "a": true\n}\n',
        "array value": '{\n  "a": [1, {"b": 2}]\n}\n',
        "string value then a comment": '{\n  "a": "text"\n  // trailing note,\n}\n',
        "block comment before the brace": '{\n  "a": "text"\n  /* note, */\n}\n',
    }

    def test_add_keeps_the_file_parseable(self):
        for name, src in self.SHAPES.items():
            with self.subTest(shape=name):
                out = confedit.menu_add_entry(src, KEY, ENTRY)
                parsed = parse_jsonc(out)
                self.assertIn(KEY, parsed)
                self.assertEqual(parsed[KEY]["action"], "omarchy-signal open")

    def test_add_then_remove_restores_the_file_byte_for_byte(self):
        for name, src in self.SHAPES.items():
            with self.subTest(shape=name):
                added = confedit.menu_add_entry(src, KEY, ENTRY)
                self.assertEqual(confedit.menu_remove_entry(added, KEY), src)

    def test_add_preserves_every_comment(self):
        for name, src in self.SHAPES.items():
            with self.subTest(shape=name):
                out = confedit.menu_add_entry(src, KEY, ENTRY)
                self.assertEqual(self._comments(out), self._comments(src))

    def test_the_comma_ending_comment_case_that_broke_the_menu(self):
        """OMSIG-4: `head.endswith(",")` saw the comment's comma and skipped the separator."""
        src = '{\n  "mine": {"label":"Mine"}\n  // "about": {"label":"About"},\n}\n'
        out = confedit.menu_add_entry(src, KEY, ENTRY)
        self.assertEqual(parse_jsonc(out)["mine"]["label"], "Mine")
        self.assertIn('"mine": {"label":"Mine"},', out)

    def test_add_is_idempotent(self):
        once = confedit.menu_add_entry(self.SHAPES["several entries"], KEY, ENTRY)
        self.assertEqual(confedit.menu_add_entry(once, KEY, ENTRY), once)

    def test_removing_an_absent_entry_is_a_no_op(self):
        src = self.SHAPES["several entries"]
        self.assertEqual(confedit.menu_remove_entry(src, KEY), src)

    def test_remove_leaves_a_commented_out_copy_alone(self):
        src = '{\n  // "signal-tui": {"label":"old"},\n  "other": 1\n}\n'
        self.assertEqual(confedit.menu_remove_entry(src, KEY), src)

    def test_other_entries_survive_removal(self):
        src = self.SHAPES["several entries"]
        out = confedit.menu_remove_entry(confedit.menu_add_entry(src, KEY, ENTRY), KEY)
        self.assertEqual(sorted(parse_jsonc(out)), ["a", "b"])

    @staticmethod
    def _comments(text):
        return "".join(c for _i, c, kind in confedit.scan(text) if kind == confedit.COMMENT)


if __name__ == "__main__":
    unittest.main()
