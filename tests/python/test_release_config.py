# SPDX-License-Identifier: GPL-3.0-or-later
"""What reaches the release notes (OMSIG-7).

release-please hides most commit types unless the config says otherwise, so a
release's notes listed only `feat` and `fix` work and silently dropped
everything shipped as `docs:`, `test:`, `ci:`, `refactor:` or `build:`. Deleting
`changelog-sections` would bring that back with no other symptom until a release
is cut, so the sections are asserted here rather than trusted.
"""

import json
import pathlib
import unittest

import _helpers  # noqa: F401

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = ROOT / "release-please-config.json"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

# Every type CONTRIBUTING.md tells contributors to use, plus the rest of the
# Conventional Commit set this project ships under.
MUST_BE_VISIBLE = ["feat", "fix", "perf", "refactor", "docs", "test", "build", "ci", "style"]


class ChangelogSectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(CONFIG.read_text())
        self.sections = self.cfg.get("changelog-sections")

    def test_changelog_sections_are_configured(self):
        """Without this key release-please falls back to its hidden-by-default set."""
        self.assertIsInstance(
            self.sections, list,
            "release-please-config.json has no changelog-sections, so only feat/fix/perf/revert "
            "would appear in the release notes",
        )

    def test_every_meaningful_type_is_visible(self):
        by_type = {s["type"]: s for s in self.sections}
        for kind in MUST_BE_VISIBLE:
            with self.subTest(type=kind):
                self.assertIn(kind, by_type, f"{kind!r} is not in changelog-sections")
                self.assertIs(
                    by_type[kind].get("hidden", False), False,
                    f"{kind!r} is hidden, so those changes would not reach the release notes",
                )

    def test_every_section_has_a_heading(self):
        for section in self.sections:
            with self.subTest(type=section.get("type")):
                self.assertTrue(section.get("section"), "a section needs a human-readable heading")

    def test_chore_stays_hidden(self):
        """release-please's own `chore(main): release X.Y.Z` commits are chore."""
        chore = next((s for s in self.sections if s["type"] == "chore"), None)
        self.assertIsNotNone(chore, "chore should be listed, and hidden")
        self.assertIs(chore.get("hidden"), True, "unhiding chore puts release commits in the notes")

    def test_no_duplicate_types(self):
        types = [s["type"] for s in self.sections]
        self.assertEqual(sorted(types), sorted(set(types)), "a type is listed twice")

    def test_the_multi_change_mechanism_is_documented(self):
        """A squash merge contributes one line unless a PR body overrides it.

        Documented rather than discovered: a PR covering several tickets would
        otherwise silently reduce to its title in the notes.
        """
        text = CONTRIBUTING.read_text()
        self.assertIn("BEGIN_COMMIT_OVERRIDE", text)
        self.assertIn("END_COMMIT_OVERRIDE", text)


if __name__ == "__main__":
    unittest.main()
