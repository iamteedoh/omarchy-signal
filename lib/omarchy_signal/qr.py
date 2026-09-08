"""QR code rendering for device linking.

Two paths: a compact image over the kitty graphics protocol when the terminal
supports it (about 14 rows tall, crisp at any font size), otherwise half-block
Unicode from ``qrencode -t UTF8`` at the lowest error-correction level, which
is the smallest text rendering ``qrencode`` offers.
"""

from __future__ import annotations

import shutil
import subprocess

from . import kitty

QR_ROWS = 14           # image height in terminal rows
QR_TIMEOUT = 5


class QrUnavailable(RuntimeError):
    pass


def _qrencode(args: list[str], uri: str) -> bytes:
    exe = shutil.which("qrencode")
    if not exe:
        raise QrUnavailable("qrencode is not installed (omarchy pkg add qrencode)")
    if not uri.startswith("sgnl://") or any(ord(c) < 0x20 for c in uri) or len(uri) > 2048:
        raise QrUnavailable("refusing to encode a non-Signal link URI")
    try:
        res = subprocess.run([exe, *args, "--", uri], capture_output=True, timeout=QR_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise QrUnavailable(f"qrencode failed: {exc}") from exc
    if res.returncode != 0 or not res.stdout:
        raise QrUnavailable("qrencode failed")
    return res.stdout


def qr_png(uri: str) -> bytes:
    """PNG bytes, oversampled so the terminal downscales it sharply."""
    return _qrencode(["-o", "-", "-t", "PNG", "-s", "8", "-m", "2", "-l", "L"], uri)


def qr_text_lines(uri: str) -> list[str]:
    out = _qrencode(["-t", "UTF8", "-m", "1", "-l", "L"], uri).decode("utf-8", "replace")
    return [line for line in out.splitlines() if line.strip()]


def qr_cells(cell_w: int, cell_h: int, *, rows: int = QR_ROWS) -> tuple[int, int]:
    """Square cell box for the image: ``rows`` tall, as wide as that is high."""
    rows = max(6, rows)
    cols = max(8, round(rows * max(1, cell_h) / max(1, cell_w)))
    return cols, rows


def qr_image_sequence(uri: str, image_id: int, *, cell_w: int, cell_h: int, rows: int = QR_ROWS) -> tuple[str, int, int]:
    """Kitty transmit-and-display sequence plus the (cols, rows) it occupies."""
    cols, rows = qr_cells(cell_w, cell_h, rows=rows)
    return kitty.encode_transmit(qr_png(uri), image_id, cols=cols, rows=rows), cols, rows
