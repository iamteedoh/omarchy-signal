"""QR code rendering for device linking.

Two paths: a small image over the kitty graphics protocol when the terminal
supports it (9 rows tall by default, crisp at any font size), otherwise
quarter-block glyphs (two modules per column, two per row) built from
``qrencode``'s module matrix at the lowest error-correction level. For a
typical link URI that is about 22 rows by 22 columns: half the width of
``qrencode -t UTF8`` and the smallest rendering that every monospace font
draws as solid blocks.
"""

from __future__ import annotations

import shutil
import subprocess

from . import kitty

QR_ROWS = 9            # image height in terminal rows (override with qr_rows / --qr-size)
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


def qr_matrix(uri: str) -> list[list[bool]]:
    """Module matrix (True = dark) including a one-module quiet zone."""
    out = _qrencode(["-t", "ASCII", "-m", "1", "-l", "L"], uri).decode("ascii", "replace")
    rows = [line for line in out.splitlines() if line]
    width = max((len(r) for r in rows), default=0)
    matrix = []
    for line in rows:
        line = line.ljust(width)
        matrix.append([line[i] == "#" for i in range(0, width, 2)])
    if not matrix or len(matrix) < 21:
        raise QrUnavailable("qrencode produced an unexpected matrix")
    return matrix


# Quadrant block glyphs indexed by bits: top-left=1, top-right=2, bottom-left=4, bottom-right=8.
_QUADRANTS = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"


def qr_text_lines(uri: str) -> list[str]:
    """Quarter-block rendering: each character carries a 2x2 block of modules.
    Draw it with a dark foreground on a light background."""
    m = qr_matrix(uri)
    h = len(m)
    w = max(len(r) for r in m)
    if h % 2:
        m = m + [[False] * w]
        h += 1
    lines = []
    for y in range(0, h, 2):
        chars = []
        for x in range(0, w, 2):
            def bit(yy, xx):
                return 1 if (yy < len(m) and xx < len(m[yy]) and m[yy][xx]) else 0
            idx = bit(y, x) + bit(y, x + 1) * 2 + bit(y + 1, x) * 4 + bit(y + 1, x + 1) * 8
            chars.append(_QUADRANTS[idx])
        lines.append("".join(chars))
    return lines


def qr_cells(cell_w: int, cell_h: int, *, rows: int = QR_ROWS) -> tuple[int, int]:
    """Square cell box for the image: ``rows`` tall, as wide as that is high."""
    rows = max(6, rows)
    cols = max(8, round(rows * max(1, cell_h) / max(1, cell_w)))
    return cols, rows


def qr_image_sequence(uri: str, image_id: int, *, cell_w: int, cell_h: int, rows: int = QR_ROWS) -> tuple[str, int, int]:
    """Kitty transmit-and-display sequence plus the (cols, rows) it occupies."""
    cols, rows = qr_cells(cell_w, cell_h, rows=rows)
    return kitty.encode_transmit(qr_png(uri), image_id, cols=cols, rows=rows), cols, rows
