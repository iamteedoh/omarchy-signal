import shutil
import unittest

import _helpers  # noqa: F401
from omarchy_signal import kitty, qr

URI = "sgnl://linkdevice?uuid=AbCdEfGhIjKlMnOpQrStUv%3D%3D&pub_key=BQ1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ%2F%2B12"


class QrTests(unittest.TestCase):
    def test_cells_are_square_and_compact(self):
        cols, rows = qr.qr_cells(10, 20)
        self.assertEqual(rows, qr.QR_ROWS)
        self.assertEqual(cols, 18)
        self.assertEqual(qr.qr_cells(8, 16, rows=4), (12, 6))   # never below a scannable minimum

    def test_refuses_non_signal_uris(self):
        for bad in ("https://example.com", "sgnl://x\x1b[31m", "", "sgnl://" + "a" * 3000):
            with self.subTest(bad=bad[:20]):
                self.assertRaises(qr.QrUnavailable, qr.qr_text_lines, bad)

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode not installed")
    def test_text_styles(self):
        m = qr.qr_matrix(URI)
        self.assertEqual(len(m), 43)
        self.assertTrue(m[1][1] and m[1][7] and not m[0][0])   # finder pattern inside the quiet zone
        half = qr.qr_text_lines(URI, "half")
        self.assertEqual((len(half), len(half[0])), (22, 43))
        self.assertEqual(half[0][:9], " ▄▄▄▄▄▄▄ ")
        self.assertTrue(all(ch in " ▀▄█" for line in half for ch in line))
        quad = qr.qr_text_lines(URI, "quad")
        self.assertEqual((len(quad), len(quad[0])), (22, 22))
        self.assertEqual(quad[0][:5], "▗▄▄▄ ")
        braille = qr.qr_text_lines(URI, "braille")
        self.assertEqual((len(braille), len(braille[0])), (11, 22))
        self.assertTrue(all(0x2800 <= ord(ch) <= 0x28FF for line in braille for ch in line))
        self.assertEqual(qr.qr_text_lines(URI, "nonsense"), half)

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode not installed")
    def test_png_file_is_private(self):
        import os, stat, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = qr.qr_png_file(URI, os.path.join(d, "run"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode not installed")
    def test_image_sequence(self):
        seq, cols, rows = qr.qr_image_sequence(URI, 42, cell_w=10, cell_h=20)
        self.assertTrue(seq.startswith("\x1b_Ga=T,f=100,t=d,q=2,i=42,p=1,c=18,r=9,C=1"))
        self.assertTrue(seq.endswith("\x1b\\"))
        self.assertEqual((cols, rows), (18, 9))
        _, cols, rows = qr.qr_image_sequence(URI, 43, cell_w=10, cell_h=20, rows=14)
        self.assertEqual((cols, rows), (28, 14))
        png = qr.qr_png(URI)
        self.assertTrue(png.startswith(b"\x89PNG"))
        info = kitty.probe_dimensions_bytes(png) if hasattr(kitty, "probe_dimensions_bytes") else None
        self.assertIsNone(info)  # helper not needed; header sanity covered above


if __name__ == "__main__":
    unittest.main()
