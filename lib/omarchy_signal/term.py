# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal terminal driver: raw mode, alternate screen, key decoding, cell
widths. Stdlib only; no curses, so the output stream stays under our control
(which matters for the graphics protocol and for guaranteeing that nothing
but our own escape sequences ever reach the terminal)."""

from __future__ import annotations

import fcntl
import os
import re
import select
import signal
import struct
import sys
import termios
import unicodedata
from dataclasses import dataclass

ESC = "\x1b"
CSI = "\x1b["


# --- width -----------------------------------------------------------------

_ZERO_WIDTH = {"Mn", "Me", "Cf"}


def char_width(ch: str) -> int:
    if ch in ("‍", "️"):
        return 0
    cat = unicodedata.category(ch)
    if cat in _ZERO_WIDTH:
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    o = ord(ch)
    if 0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF and unicodedata.east_asian_width(ch) == "W":
        return 2
    return 1


def str_width(s: str) -> int:
    """Display width. U+FE0F after a one-cell glyph turns it into a two-cell
    emoji (☺︎ vs ☺️), which is how terminals render it."""
    total = 0
    prev_w = 0
    for ch in s:
        if ch == "\ufe0f":
            if prev_w == 1:
                total += 1
                prev_w = 2
            continue
        w = char_width(ch)
        total += w
        prev_w = w
    return total


def truncate(s: str, width: int, *, ellipsis: str = "…") -> str:
    if str_width(s) <= width:
        return s
    ew = str_width(ellipsis)
    out = []
    w = 0
    for ch in s:
        cw = char_width(ch)
        if w + cw > width - ew:
            break
        out.append(ch)
        w += cw
    return "".join(out) + ellipsis


def pad(s: str, width: int, *, align: str = "left") -> str:
    w = str_width(s)
    if w >= width:
        return s
    fill = " " * (width - w)
    if align == "right":
        return fill + s
    if align == "center":
        left = (width - w) // 2
        return " " * left + s + " " * (width - w - left)
    return s + fill


def wrap(text: str, width: int) -> list[str]:
    """Word-wrap on display width; hard-breaks words longer than a line."""
    width = max(1, width)
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        cur_w = 0
        for word in re.split(r"(\s+)", para):
            if not word:
                continue
            ww = str_width(word)
            if word.isspace():
                if cur_w + ww <= width and cur:
                    cur += word
                    cur_w += ww
                continue
            if cur_w + ww <= width:
                cur += word
                cur_w += ww
                continue
            if cur.strip():
                lines.append(cur.rstrip())
            cur, cur_w = "", 0
            while ww > width:
                piece = truncate(word, width, ellipsis="")
                lines.append(piece)
                word = word[len(piece):]
                ww = str_width(word)
            cur, cur_w = word, ww
        if cur.strip() or not lines:
            lines.append(cur.rstrip())
    return lines or [""]


# --- keys ------------------------------------------------------------------

@dataclass(frozen=True)
class Key:
    name: str            # "char", "enter", "up", "ctrl-c", "f1", "mouse", ...
    char: str = ""
    alt: bool = False
    ctrl: bool = False
    shift: bool = False
    x: int = 0           # mouse column (1-based)
    y: int = 0           # mouse row
    button: int = 0      # mouse button (0 left, 1 middle, 2 right, 64/65 wheel)
    release: bool = False

    def __str__(self) -> str:
        mods = ("alt-" if self.alt else "") + ("ctrl-" if self.ctrl else "") + ("shift-" if self.shift else "")
        return mods + (self.char if self.name == "char" else self.name)


_CSI_FINAL = {
    "A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "backtab",
}
_CSI_TILDE = {1: "home", 2: "insert", 3: "delete", 4: "end", 5: "pageup", 6: "pagedown", 7: "home", 8: "end",
              11: "f1", 12: "f2", 13: "f3", 14: "f4", 15: "f5", 17: "f6", 18: "f7", 19: "f8", 20: "f9",
              21: "f10", 23: "f11", 24: "f12"}
_SS3 = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "P": "f1", "Q": "f2",
        "R": "f3", "S": "f4"}
_MOUSE_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([mM])")


def _mods(n: int) -> tuple[bool, bool, bool]:
    n = max(1, n) - 1
    return bool(n & 2), bool(n & 4), bool(n & 1)  # alt, ctrl, shift


class KeyParser:
    """Incremental decoder for the byte stream of a terminal in raw mode."""

    def __init__(self):
        self.buf = ""

    def feed(self, data: bytes) -> list[Key]:
        self.buf += data.decode("utf-8", "replace")
        keys: list[Key] = []
        while self.buf:
            key, consumed = self._parse_one(self.buf)
            if consumed == 0:
                break  # incomplete sequence; wait for more bytes
            self.buf = self.buf[consumed:]
            if key is not None:
                keys.append(key)
        return keys

    def flush(self) -> list[Key]:
        """Called after a short idle: a lone ESC is really Escape."""
        keys: list[Key] = []
        if self.buf == ESC:
            keys.append(Key("escape"))
            self.buf = ""
        elif self.buf.startswith(ESC) and len(self.buf) == 2:
            keys.append(Key("char", char=self.buf[1], alt=True))
            self.buf = ""
        elif self.buf:
            # Unknown sequence: drop it rather than typing it into the composer.
            self.buf = ""
        return keys

    def _parse_one(self, s: str) -> tuple[Key | None, int]:
        ch = s[0]
        if ch != ESC:
            if ch == "\r" or ch == "\n":
                return Key("enter"), 1
            if ch == "\t":
                return Key("tab"), 1
            if ch == "\x7f" or ch == "\x08":
                return Key("backspace"), 1
            if ch == "\x00":
                return Key("char", char=" ", ctrl=True), 1
            if ord(ch) < 0x20:
                return Key("char", char=chr(ord(ch) + 96), ctrl=True), 1
            if "\ud800" <= ch <= "\udfff":
                return None, 1
            return Key("char", char=ch), 1
        if len(s) == 1:
            return None, 0
        nxt = s[1]
        if nxt == "[":
            if s.startswith("\x1b[200~"):
                # Bracketed paste: deliver the whole block as one key so a
                # multi-line paste never triggers "send" on its newlines.
                end = s.find("\x1b[201~")
                if end < 0:
                    return (None, len(s)) if len(s) > 1_000_000 else (None, 0)
                return Key("paste", char=s[6:end]), end + 6
            m = _MOUSE_RE.match(s)
            if m:
                b, x, y, kind = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
                return Key("mouse", x=x, y=y, button=b & ~0b11100, shift=bool(b & 4), alt=bool(b & 8),
                           ctrl=bool(b & 16), release=(kind == "m")), m.end()
            m = re.match(r"\x1b\[([0-9;]*)([A-Za-z~])", s)
            if not m:
                if len(s) > 32:
                    return None, len(s)
                return None, 0
            params, final = m.group(1), m.group(2)
            nums = [int(p) for p in params.split(";") if p.isdigit()] if params else []
            if final == "~":
                code = nums[0] if nums else 0
                name = _CSI_TILDE.get(code)
                alt, ctrl, shift = _mods(nums[1] if len(nums) > 1 else 1)
                return (Key(name, alt=alt, ctrl=ctrl, shift=shift) if name else None), m.end()
            if final == "u" and nums:
                # kitty keyboard protocol basic form: CSI codepoint;mods u
                alt, ctrl, shift = _mods(nums[1] if len(nums) > 1 else 1)
                cp = nums[0]
                if cp == 27:
                    return Key("escape"), m.end()
                if cp == 13:
                    return Key("enter", alt=alt, ctrl=ctrl, shift=shift), m.end()
                if cp == 9:
                    return Key("tab", shift=shift), m.end()
                if cp == 127:
                    return Key("backspace", alt=alt, ctrl=ctrl), m.end()
                if 0 < cp < 0x110000:
                    return Key("char", char=chr(cp), alt=alt, ctrl=ctrl, shift=shift), m.end()
                return None, m.end()
            name = _CSI_FINAL.get(final)
            if name is None:
                return None, m.end()
            alt, ctrl, shift = _mods(nums[1] if len(nums) > 1 else 1)
            return Key(name, alt=alt, ctrl=ctrl, shift=shift), m.end()
        if nxt == "O":
            if len(s) < 3:
                return None, 0
            name = _SS3.get(s[2])
            return (Key(name) if name else None), 3
        if nxt == "_" or nxt == "]" or nxt == "P":
            # APC/OSC/DCS response (e.g. graphics query answer): swallow to ST.
            end = s.find("\x1b\\")
            bel = s.find("\x07")
            ends = [e for e in (end, bel) if e >= 0]
            if not ends:
                return (None, len(s)) if len(s) > 4096 else (None, 0)
            e = min(ends)
            return None, e + (2 if e == end else 1)
        if nxt == ESC:
            return Key("escape"), 1
        # Alt+key
        if ord(nxt) < 0x20:
            return Key("char", char=chr(ord(nxt) + 96), ctrl=True, alt=True), 2
        return Key("char", char=nxt, alt=True), 2


# --- terminal ----------------------------------------------------------------

class Terminal:
    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self.fd_in = sys.stdin.fileno()
        self._saved = None
        self.cols = 80
        self.rows = 24
        self.cell_w = 10
        self.cell_h = 20
        self._buf: list[str] = []

    def __enter__(self):
        self._saved = termios.tcgetattr(self.fd_in)
        raw = termios.tcgetattr(self.fd_in)
        raw[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
        raw[1] &= ~termios.OPOST
        raw[2] |= termios.CS8
        raw[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        raw[6][termios.VMIN] = 0
        raw[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, raw)
        self.write(CSI + "?1049h" + CSI + "?25l" + CSI + "?1000h" + CSI + "?1006h" + CSI + "?2004h")
        self.write(CSI + ">1u")  # kitty keyboard protocol: disambiguate escape codes (ignored elsewhere)
        self.flush()
        self.measure()
        return self

    def __exit__(self, *exc):
        self.write(CSI + "<u" + CSI + "?2004l" + CSI + "?1006l" + CSI + "?1000l" + CSI + "0 q" + CSI + "?25h" + CSI + "?1049l")
        self.flush()
        if self._saved is not None:
            termios.tcsetattr(self.fd_in, termios.TCSAFLUSH, self._saved)

    def measure(self) -> None:
        try:
            packed = fcntl.ioctl(self.out.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
            rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
        except OSError:
            rows, cols, xpix, ypix = 24, 80, 0, 0
        self.rows = rows or 24
        self.cols = cols or 80
        if xpix and ypix:
            self.cell_w = max(1, xpix // self.cols)
            self.cell_h = max(1, ypix // self.rows)

    def write(self, s: str) -> None:
        self._buf.append(s)

    def flush(self) -> None:
        if not self._buf:
            return
        data = "".join(self._buf)
        self._buf.clear()
        try:
            self.out.write(data)
            self.out.flush()
        except (BrokenPipeError, OSError):
            pass

    def read(self, timeout: float = 0.0) -> bytes:
        r, _, _ = select.select([self.fd_in], [], [], timeout)
        if not r:
            return b""
        try:
            return os.read(self.fd_in, 65536)
        except OSError:
            return b""

    # sequences ----------------------------------------------------------------

    @staticmethod
    def move(row: int, col: int) -> str:
        return f"{CSI}{max(1, row)};{max(1, col)}H"

    @staticmethod
    def fg(rgb) -> str:
        return f"{CSI}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    @staticmethod
    def bg(rgb) -> str:
        return f"{CSI}48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"

    RESET = CSI + "0m"
    BOLD = CSI + "1m"
    DIM = CSI + "2m"
    ITALIC = CSI + "3m"
    UNDERLINE = CSI + "4m"
    REVERSE = CSI + "7m"
    CLEAR = CSI + "2J"
    CLEAR_LINE = CSI + "2K"
    SHOW_CURSOR = CSI + "?25h"
    HIDE_CURSOR = CSI + "?25l"

    @staticmethod
    def title(text: str) -> str:
        safe = "".join(ch for ch in text if ch.isprintable() and ch not in "\x1b\x07\x9c")[:80]
        return f"\x1b]0;{safe}\x07"

    @staticmethod
    def on_resize(callback) -> None:
        signal.signal(signal.SIGWINCH, lambda *_: callback())
