# SPDX-License-Identifier: GPL-3.0-or-later
"""Wayland clipboard through wl-clipboard (wl-copy / wl-paste).

Both the regular clipboard and the primary selection (select to copy,
middle-click to paste) are supported. A picture on the clipboard is written
to a file under the data directory so it can be sent as an attachment: the
bridge only accepts files under the home directory."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

MAX_PASTE_TEXT = 60000
MAX_PASTE_IMAGE = 100 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "TEXT", "STRING")


class ClipboardError(Exception):
    pass


def _exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise ClipboardError(f"{name} not found (install wl-clipboard)")
    return exe


def copy(text: str, *, primary: bool = False) -> None:
    argv = [_exe("wl-copy")] + (["--primary"] if primary else []) + ["--", text]
    try:
        # wl-copy forks a server that owns the selection; the parent returns at once.
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=3, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClipboardError("copy failed") from exc


def _paste(args: list[str], limit: int) -> bytes:
    try:
        res = subprocess.run([_exe("wl-paste")] + args, stdin=subprocess.DEVNULL, capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClipboardError("paste failed") from exc
    if res.returncode != 0:
        return b""        # empty clipboard, or nothing of that type
    return res.stdout[:limit]


def types(*, primary: bool = False) -> list[str]:
    raw = _paste((["--primary"] if primary else []) + ["--list-types"], 65536)
    return [t.strip() for t in raw.decode("utf-8", "replace").splitlines() if t.strip()]


def paste_text(*, primary: bool = False) -> str:
    raw = _paste((["--primary"] if primary else []) + ["--no-newline", "--type", "text"], MAX_PASTE_TEXT * 4)
    return raw.decode("utf-8", "replace")[:MAX_PASTE_TEXT]


def image_type(offered: list[str]) -> str:
    """The picture type to ask for, when the clipboard holds a picture and no text."""
    if any(t in TEXT_TYPES or t.startswith("text/") for t in offered):
        return ""
    return next((t for t in IMAGE_TYPES if t in offered), "")


def paste_image(dest_dir: Path, mime: str) -> Path:
    data = _paste(["--type", mime], MAX_PASTE_IMAGE + 1)
    if not data:
        raise ClipboardError("the clipboard is empty")
    if len(data) > MAX_PASTE_IMAGE:
        raise ClipboardError("the picture is larger than 100 MiB")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir.chmod(0o700)
    dest = dest_dir / time.strftime(f"pasted-%Y%m%d-%H%M%S{IMAGE_TYPES[mime]}")
    n = 1
    while dest.exists():
        dest = dest.with_name(f"{dest.stem.split('~')[0]}~{n}{dest.suffix}")
        n += 1
    dest.write_bytes(data)
    dest.chmod(0o600)
    return dest


def pasted_dir(data_dir: Path) -> Path:
    return data_dir / "pasted"
