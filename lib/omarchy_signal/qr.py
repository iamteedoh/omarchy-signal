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

TEXT_STYLES = ("half", "quad", "braille")


def _bit(m: list[list[bool]], y: int, x: int) -> int:
    return 1 if (0 <= y < len(m) and 0 <= x < len(m[y]) and m[y][x]) else 0


def qr_text_lines(uri: str, style: str = "half") -> list[str]:
    """Text rendering of the code, dark modules on a light background.

    half     one column per module, two modules per row (▀▄█). Square on a
             normal 1:2 terminal cell and solid; ~43 x 21 for a link URI.
             The smallest rendering every scanner reads.
    quad     two modules per column and per row (quarter blocks); half the
             width of ``half`` but twice as tall as it is wide.
    braille  two per column, four per row: ~22 x 11 and square, but dotted;
             some phone cameras refuse it.
    """
    m = qr_matrix(uri)
    h = len(m)
    w = max(len(r) for r in m)
    lines: list[str] = []
    if style == "quad":
        for y in range(0, h, 2):
            lines.append("".join(_QUADRANTS[_bit(m, y, x) + _bit(m, y, x + 1) * 2 + _bit(m, y + 1, x) * 4 + _bit(m, y + 1, x + 1) * 8]
                                 for x in range(0, w, 2)))
    elif style == "braille":
        # Braille dot numbering: (row, col) -> bit: (0,0)=1 (1,0)=2 (2,0)=4 (0,1)=8 (1,1)=16 (2,1)=32 (3,0)=64 (3,1)=128
        weights = {(0, 0): 1, (1, 0): 2, (2, 0): 4, (0, 1): 8, (1, 1): 16, (2, 1): 32, (3, 0): 64, (3, 1): 128}
        for y in range(0, h, 4):
            chars = []
            for x in range(0, w, 2):
                code = 0x2800
                for (dy, dx), weight in weights.items():
                    if _bit(m, y + dy, x + dx):
                        code |= weight
                chars.append(chr(code))
            lines.append("".join(chars))
    else:
        for y in range(0, h, 2):
            row = []
            for x in range(w):
                top, bottom = _bit(m, y, x), _bit(m, y + 1, x)
                row.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
            lines.append("".join(row))
    return lines


def qr_png_file(uri: str, directory) -> "Path":
    """Write the PNG to an owner-only file for the shell popup to load."""
    import os
    from pathlib import Path
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "link-qr.png"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(qr_png(uri))
    return path


def shell_show_qr(path) -> bool:
    """Ask the Omarchy shell (our Service.qml) to display the PNG. Returns
    True if the shell acknowledged; False if it is not running or the
    plugin is not loaded, in which case the caller falls back to text."""
    exe = shutil.which("omarchy-shell")
    if not exe:
        return False
    try:
        res = subprocess.run([exe, "iamteedoh.signal", "showQr", str(path)], capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and b"ok" in res.stdout


def shell_hide_qr() -> None:
    exe = shutil.which("omarchy-shell")
    if not exe:
        return
    with __import__("contextlib").suppress(OSError, subprocess.SubprocessError):
        subprocess.run([exe, "-q", "iamteedoh.signal", "hideQr"], capture_output=True, timeout=3, check=False)


def qr_cells(cell_w: int, cell_h: int, *, rows: int = QR_ROWS) -> tuple[int, int]:
    """Square cell box for the image: ``rows`` tall, as wide as that is high."""
    rows = max(6, rows)
    cols = max(8, round(rows * max(1, cell_h) / max(1, cell_w)))
    return cols, rows


def qr_image_sequence(uri: str, image_id: int, *, cell_w: int, cell_h: int, rows: int = QR_ROWS) -> tuple[str, int, int]:
    """Kitty transmit-and-display sequence plus the (cols, rows) it occupies."""
    cols, rows = qr_cells(cell_w, cell_h, rows=rows)
    return kitty.encode_transmit(qr_png(uri), image_id, cols=cols, rows=rows), cols, rows
