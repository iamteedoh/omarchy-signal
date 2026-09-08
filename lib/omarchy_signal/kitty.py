"""Kitty graphics protocol: show images inline in terminals that support it
(kitty, Ghostty, WezTerm, Konsole, ...).

Images are transmitted as PNG in base64 chunks with the *direct* medium
(``t=d``), which works over SSH and inside sandboxes, unlike file/shm
mediums. Anything that is not already PNG is converted with ImageMagick in a
subprocess with hard resource limits: a decoder bomb from a stranger must not
take the terminal down with it.
"""

from __future__ import annotations

import base64
import os
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

CHUNK = 4096
MAX_PIXELS = 40_000_000       # 40 Mpx ≈ the ceiling of anything a phone produces
MAX_PNG_BYTES = 32 * 1024 * 1024
CONVERT_TIMEOUT = 20

_SUPPORTED_TERMS = ("kitty", "ghostty", "wezterm", "konsole", "warp")


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str   # png | jpeg | gif | webp | unknown


def probe_dimensions(path: Path) -> ImageInfo | None:
    """Read image dimensions from the header without decoding pixels."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64 * 1024)
    except OSError:
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24 and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return ImageInfo(w, h, "png")
    if head.startswith(b"GIF8") and len(head) >= 10:
        w, h = struct.unpack("<HH", head[6:10])
        return ImageInfo(w, h, "gif")
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        chunk = head[12:16]
        if chunk == b"VP8 " and len(head) >= 30:
            w, h = struct.unpack("<HH", head[26:30])
            return ImageInfo(w & 0x3FFF, h & 0x3FFF, "webp")
        if chunk == b"VP8L" and len(head) >= 25:
            b = head[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return ImageInfo(w, h, "webp")
        if chunk == b"VP8X" and len(head) >= 30:
            w = 1 + int.from_bytes(head[24:27], "little")
            h = 1 + int.from_bytes(head[27:30], "little")
            return ImageInfo(w, h, "webp")
        return ImageInfo(0, 0, "webp")
    if head.startswith(b"\xff\xd8"):
        i = 2
        data = head
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if i + 4 > len(data):
                break
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 9 <= len(data):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return ImageInfo(w, h, "jpeg")
                break
            i += 2 + seg_len
        return ImageInfo(0, 0, "jpeg")
    return None


def terminal_supports_graphics(*, probe: bool = True, timeout: float = 0.25) -> bool:
    """Decide whether the attached terminal speaks the kitty graphics protocol.

    Cheap environment checks first; then, if stdin/stdout are a tty, an
    actual protocol query (``a=q``) followed by a DA1 request so we always get
    *some* answer back and never block on a terminal that ignores the query.
    """
    if os.environ.get("OMARCHY_SIGNAL_IMAGES") == "off":
        return False
    term = (os.environ.get("TERM") or "").lower()
    program = (os.environ.get("TERM_PROGRAM") or "").lower()
    if os.environ.get("KITTY_WINDOW_ID") or "kitty" in term:
        return True
    if any(t in program for t in _SUPPORTED_TERMS) or any(t in term for t in ("ghostty", "wezterm")):
        return True
    if not probe or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return _probe_terminal(timeout)


def _probe_terminal(timeout: float) -> bool:
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return False
    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ECHO | termios.ICANON)
    try:
        termios.tcsetattr(fd, termios.TCSANOW, new)
        # 1x1 RGB image, id 31, query action: terminal answers "\x1b_Gi=31;OK\x1b\\" if supported.
        sys.stdout.write("\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\\x1b[c")
        sys.stdout.flush()
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not r:
                break
            buf += os.read(fd, 4096)
            if b"\x1b[?" in buf and buf.endswith(b"c"):
                break
        return b"_Gi=31;OK" in buf
    except OSError:
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)


def to_png(path: Path, *, max_side: int = 1600) -> bytes | None:
    """Return PNG bytes for ``path``, converting and downscaling if needed.

    A PNG that is already small enough is passed through untouched, so no
    decoder runs on it at all. Everything else goes through ImageMagick with
    memory/time/pixel limits and ``-strip`` (drops EXIF, including GPS)."""
    info = probe_dimensions(path)
    if info is None:
        return None
    if info.width * info.height > MAX_PIXELS:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if info.format == "png" and size <= MAX_PNG_BYTES and max(info.width, info.height) <= max_side:
        try:
            return path.read_bytes()
        except OSError:
            return None
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return path.read_bytes() if info.format == "png" and size <= MAX_PNG_BYTES else None
    fmt = {"png": "PNG", "jpeg": "JPEG", "gif": "GIF", "webp": "WEBP"}.get(info.format, "")
    src = f"{fmt}:{path}" if fmt else str(path)
    cmd = [magick, "-limit", "memory", "256MiB", "-limit", "map", "512MiB", "-limit", "time", str(CONVERT_TIMEOUT),
           "-limit", "width", "16000", "-limit", "height", "16000",
           src + ("[0]" if info.format == "gif" else ""), "-auto-orient", "-strip",
           "-resize", f"{max_side}x{max_side}>", "PNG:-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=CONVERT_TIMEOUT + 5, check=False,
                              env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/"),
                                   "MAGICK_TEMPORARY_PATH": os.environ.get("TMPDIR", "/tmp")})
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG") or len(proc.stdout) > MAX_PNG_BYTES:
        return None
    return proc.stdout


def encode_transmit(png: bytes, image_id: int, *, cols: int, rows: int, placement_id: int = 1) -> str:
    """Escape sequence(s) that transmit ``png`` and display it in a
    ``cols`` x ``rows`` cell box at the cursor. Quiet mode (``q=2``) so the
    terminal never answers, which keeps the input stream clean."""
    if not 1 <= image_id <= 4294967295:
        raise ValueError("image id out of range")
    data = base64.standard_b64encode(png).decode("ascii")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [""]
    out = []
    for i, chunk in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        if i == 0:
            ctrl = f"a=T,f=100,t=d,q=2,i={image_id},p={placement_id},c={cols},r={rows},C=1,m={more}"
        else:
            ctrl = f"m={more},q=2"
        out.append(f"\x1b_G{ctrl};{chunk}\x1b\\")
    return "".join(out)


def encode_transmit_only(png: bytes, image_id: int) -> str:
    """Upload the image without displaying it; pair with :func:`encode_place`."""
    if not 1 <= image_id <= 4294967295:
        raise ValueError("image id out of range")
    data = base64.standard_b64encode(png).decode("ascii")
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [""]
    out = []
    for i, chunk in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        ctrl = f"a=t,f=100,t=d,q=2,i={image_id},m={more}" if i == 0 else f"m={more},q=2"
        out.append(f"\x1b_G{ctrl};{chunk}\x1b\\")
    return "".join(out)


def encode_place(image_id: int, *, cols: int, rows: int, placement_id: int = 1) -> str:
    """Display an already transmitted image at the cursor in a cell box."""
    return f"\x1b_Ga=p,i={image_id},p={placement_id},c={cols},r={rows},C=1,q=2\x1b\\"


def encode_delete_placements() -> str:
    """Remove every visible placement but keep the uploaded image data."""
    return "\x1b_Ga=d,d=a,q=2\x1b\\"


def encode_delete(image_id: int) -> str:
    """Remove the placements of one image (its data may stay cached)."""
    return f"\x1b_Ga=d,d=i,i={image_id},q=2\x1b\\"


def encode_delete_all() -> str:
    return "\x1b_Ga=d,d=A,q=2\x1b\\"


def fit_cells(width_px: int, height_px: int, *, max_cols: int, max_rows: int,
              cell_w: int = 10, cell_h: int = 20) -> tuple[int, int]:
    """Cell box that shows the image without distortion inside the limits."""
    if width_px <= 0 or height_px <= 0:
        return max(1, min(max_cols, 20)), max(1, min(max_rows, 8))
    max_cols = max(1, max_cols)
    max_rows = max(1, max_rows)
    scale = min(max_cols * cell_w / width_px, max_rows * cell_h / height_px, 1.0)
    cols = max(1, int(width_px * scale / cell_w))
    rows = max(1, int(height_px * scale / cell_h))
    return min(cols, max_cols), min(rows, max_rows)


def image_id_for(path: str) -> int:
    """Stable, non-zero 31-bit id derived from the path."""
    return (zlib.crc32(path.encode("utf-8", "surrogateescape")) & 0x7FFFFFFF) or 1
