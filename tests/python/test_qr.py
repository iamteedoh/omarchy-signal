import shutil
import unittest

import _helpers  # noqa: F401
from omarchy_signal import kitty, qr

URI = "sgnl://linkdevice?uuid=AbCdEfGhIjKlMnOpQrStUv%3D%3D&pub_key=BQ1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ%2F%2B12"


class QrTests(unittest.TestCase):
    def test_cells_are_square_and_compact(self):
        cols, rows = qr.qr_cells(10, 20)
        self.assertEqual(rows, qr.QR_ROWS)
        self.assertEqual(cols, 28)
        self.assertEqual(qr.qr_cells(8, 16, rows=4), (12, 6))   # never below a scannable minimum

    def test_refuses_non_signal_uris(self):
        for bad in ("https://example.com", "sgnl://x\x1b[31m", "", "sgnl://" + "a" * 3000):
            with self.subTest(bad=bad[:20]):
                self.assertRaises(qr.QrUnavailable, qr.qr_text_lines, bad)

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode not installed")
    def test_text_qr_is_small(self):
        lines = qr.qr_text_lines(URI)
        self.assertLessEqual(len(lines), 24)
        self.assertLessEqual(max(len(l) for l in lines), 48)

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode not installed")
    def test_image_sequence(self):
        seq, cols, rows = qr.qr_image_sequence(URI, 42, cell_w=10, cell_h=20)
        self.assertTrue(seq.startswith("\x1b_Ga=T,f=100,t=d,q=2,i=42,p=1,c=28,r=14,C=1"))
        self.assertTrue(seq.endswith("\x1b\\"))
        self.assertEqual((cols, rows), (28, 14))
        png = qr.qr_png(URI)
        self.assertTrue(png.startswith(b"\x89PNG"))
        info = kitty.probe_dimensions_bytes(png) if hasattr(kitty, "probe_dimensions_bytes") else None
        self.assertIsNone(info)  # helper not needed; header sanity covered above


if __name__ == "__main__":
    unittest.main()
