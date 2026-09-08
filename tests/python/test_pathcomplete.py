import os
import tempfile
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import pathcomplete as pc


class PathCompleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "Pictures").mkdir()
        (self.root / "Documents").mkdir()
        (self.root / "photo.jpg").write_bytes(b"x" * 10)
        (self.root / "Photo2.PNG").write_bytes(b"x" * 20)
        (self.root / "notes.txt").write_bytes(b"x")
        (self.root / ".hidden").write_bytes(b"x")
        (self.root / "Pictures" / "cat.webp").write_bytes(b"x" * 5)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_directory_dirs_first(self):
        base, cands = pc.complete(str(self.root) + "/")
        self.assertEqual(base, self.root)
        self.assertEqual([c.name for c in cands], ["Documents/", "Pictures/", "notes.txt", "photo.jpg", "Photo2.PNG"])
        self.assertEqual([c.kind for c in cands], ["dir", "dir", "doc", "image", "image"])
        self.assertEqual(cands[3].size, 10)

    def test_prefix_then_substring_case_insensitive(self):
        _, cands = pc.complete(str(self.root / "ph"))
        self.assertEqual([c.name for c in cands], ["photo.jpg", "Photo2.PNG"])
        _, cands = pc.complete(str(self.root / "oto"))
        self.assertEqual([c.name for c in cands], ["photo.jpg", "Photo2.PNG"])
        _, cands = pc.complete(str(self.root / "zzz"))
        self.assertEqual(cands, [])

    def test_hidden_only_when_asked(self):
        _, cands = pc.complete(str(self.root) + "/")
        self.assertNotIn(".hidden", [c.name for c in cands])
        _, cands = pc.complete(str(self.root / ".h"))
        self.assertEqual([c.name for c in cands], [".hidden"])

    def test_missing_dir_is_empty(self):
        base, cands = pc.complete("/definitely/not/here/x")
        self.assertEqual(cands, [])

    def test_accept_keeps_tilde_and_descends(self):
        home = str(Path.home())
        rel = os.path.relpath(self.root, home) if str(self.root).startswith(home) else None
        base, cands = pc.complete(str(self.root) + "/")
        pictures = next(c for c in cands if c.name == "Pictures/")
        self.assertEqual(pc.accept(str(self.root) + "/", pictures), str(self.root / "Pictures") + "/")
        if rel and not rel.startswith(".."):
            q = "~/" + rel + "/Pi"
            _, cands = pc.complete(q)
            self.assertEqual(pc.accept(q, cands[0]), "~/" + rel + "/Pictures/")
        self.assertEqual(pc.split_query("~/"), (Path.home(), ""))


if __name__ == "__main__":
    unittest.main()
