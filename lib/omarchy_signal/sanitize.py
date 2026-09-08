"""Input hardening for everything that crosses a trust boundary.

Two boundaries matter:

1. Text that arrives from the Signal network (message bodies, contact names,
   group titles, attachment file names) is attacker controlled. When it is
   written to a terminal it must not be able to inject escape sequences
   (cursor moves, OSC hyperlinks, kitty graphics commands, title changes,
   clipboard writes) or bidi overrides that make text read differently from
   what was sent. :func:`clean_text` removes every such code point.

2. Strings the user (or a plugin) hands to the bridge and that end up as
   ``signal-cli`` parameters are validated with allow-lists, never
   block-lists: :func:`classify_recipient`, :func:`safe_attachment_path`.

The rules here are deliberately conservative. A message that loses an exotic
control character is a small cost; a terminal that executes one is not.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Terminal-safe text
# ---------------------------------------------------------------------------

# Bidirectional overrides and isolates. They are the "Trojan Source" family:
# text that renders in a different order than it is stored.
_BIDI = frozenset(chr(c) for c in (
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,   # LRE RLE PDF LRO RLO
    0x2066, 0x2067, 0x2068, 0x2069,           # LRI RLI FSI PDI
    0x061C, 0x200E, 0x200F,                   # ALM LRM RLM
))

# Kept even though they are format characters: joiners make emoji sequences
# and Indic scripts work, and are harmless on a terminal.
_ALLOWED_FORMAT = frozenset("‍‌️︎")

MAX_TEXT_LENGTH = 64 * 1024


def _keep(ch: str) -> bool:
    if ch in ("\n", "\t"):
        return True
    if ch in _BIDI:
        return False
    if ch in _ALLOWED_FORMAT:
        return True
    cat = unicodedata.category(ch)
    # Cc: C0/C1 controls (ESC, CSI, OSC, DCS, APC ... all live here).
    # Cf: format chars (bidi, soft hyphen, word joiner, BOM ...).
    # Cs: lone surrogates. Co: private use (Nerd Font glyphs live here but a
    # remote sender has no business drawing our UI icons). Cn: unassigned.
    return cat not in ("Cc", "Cf", "Cs", "Co", "Cn")


def clean_text(value: object, *, max_length: int = MAX_TEXT_LENGTH, single_line: bool = False) -> str:
    """Return ``value`` as a string that is safe to write to a terminal.

    * Every control, format and surrogate code point is dropped, except newline
      and tab (tab becomes four spaces so column maths stays honest).
    * Bidi overrides are dropped even though some are technically printable.
    * Text is NFC-normalised so width calculations and comparisons are stable.
    * The result is truncated to ``max_length`` code points.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        if not _keep(ch):
            continue
        if ch == "\t":
            out.append("    ")
        elif ch == "\n" and single_line:
            out.append(" ")
        else:
            out.append(ch)
    text = "".join(out)
    if len(text) > max_length:
        text = text[:max_length]
    return text


def clean_name(value: object, *, max_length: int = 120) -> str:
    """Contact / group names: single line, no leading/trailing space."""
    return clean_text(value, max_length=max_length, single_line=True).strip()


_ESCAPE_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def contains_control(value: str) -> bool:
    """True if ``value`` still contains a C0/C1 control (used by tests and
    as a last-line assertion before bytes hit the terminal)."""
    return bool(_ESCAPE_PATTERN.search(value))


# ---------------------------------------------------------------------------
# Recipient validation
# ---------------------------------------------------------------------------

E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# signal-cli group ids are base64 of a 32-byte id (44 chars) but older v1
# groups used 16 bytes (24 chars). Allow standard base64 with padding only.
GROUP_ID = re.compile(r"^[A-Za-z0-9+/]{22,86}={0,2}$")
# Signal usernames: 3-32 chars, letters/digits/underscore, then "." and 2+ digits.
USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,31}\.[0-9]{2,9}$")


@dataclass(frozen=True)
class Recipient:
    kind: str   # "number" | "uuid" | "group" | "username"
    value: str

    @property
    def key(self) -> str:
        """Conversation key used by the store: ``kind:value``."""
        return f"{self.kind}:{self.value}"


class InvalidRecipient(ValueError):
    pass


def classify_recipient(raw: object) -> Recipient:
    """Turn user input into a typed recipient or raise :class:`InvalidRecipient`.

    Accepts an E.164 number (``+15551234567``), an ACI/PNI UUID, a group id
    (base64, optionally prefixed ``group:``), or a username (``alice.42``,
    optionally prefixed ``u:`` / ``username:``). Everything else is rejected;
    in particular nothing that could be mistaken for a command-line option
    (leading ``-``) or a path is ever accepted.
    """
    if not isinstance(raw, str):
        raise InvalidRecipient("recipient must be a string")
    value = raw.strip()
    if not value or len(value) > 128:
        raise InvalidRecipient("recipient is empty or too long")
    if contains_control(value) or any(ch.isspace() for ch in value):
        raise InvalidRecipient("recipient contains whitespace or control characters")

    lowered = value.lower()
    if lowered.startswith("group:"):
        gid = value[6:]
        if GROUP_ID.match(gid):
            return Recipient("group", gid)
        raise InvalidRecipient("malformed group id")
    for prefix in ("username:", "u:"):
        if lowered.startswith(prefix):
            name = value[len(prefix):]
            if USERNAME.match(name):
                return Recipient("username", name)
            raise InvalidRecipient("malformed username")
    for prefix in ("number:", "uuid:"):
        if lowered.startswith(prefix):
            value = value[len(prefix):]
            break

    if E164.match(value):
        return Recipient("number", value)
    if UUID.match(value):
        return Recipient("uuid", value.lower())
    if USERNAME.match(value):
        return Recipient("username", value)
    if GROUP_ID.match(value) and len(value) >= 24:
        return Recipient("group", value)
    raise InvalidRecipient(f"unrecognised recipient: {value[:40]!r}")


def parse_conversation_key(key: object) -> Recipient:
    """Inverse of :attr:`Recipient.key`; validates both halves."""
    if not isinstance(key, str) or ":" not in key:
        raise InvalidRecipient("malformed conversation key")
    kind, _, value = key.partition(":")
    if kind not in ("number", "uuid", "group", "username"):
        raise InvalidRecipient("unknown conversation kind")
    rec = classify_recipient(f"{kind}:{value}" if kind in ("group", "username") else value)
    if rec.kind != kind:
        raise InvalidRecipient("conversation kind does not match value")
    return rec


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # Signal's own ceiling is ~100 MiB.


class InvalidAttachment(ValueError):
    pass


def safe_attachment_path(raw: object, *, allowed_roots: list[Path] | None = None) -> Path:
    """Validate a file the user wants to send.

    The path is resolved (symlinks followed) and must be a regular, readable
    file under one of ``allowed_roots`` (default: the user's home directory;
    the bridge runs with a private ``/tmp``, so files there would be invisible
    to it anyway). Device nodes, FIFOs, sockets and anything under a root the
    user did not opt into are refused so a crafted request from another local
    process can not exfiltrate ``/etc/shadow`` or hang the bridge on a FIFO.
    """
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise InvalidAttachment("attachment path must be a non-empty string")
    if raw.startswith("data:"):
        raise InvalidAttachment("data: URIs are not accepted; pass a file path")
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidAttachment(f"attachment not found: {raw}") from exc

    roots = allowed_roots or [Path.home().resolve()]
    if not any(path == root or root in path.parents for root in roots):
        raise InvalidAttachment("attachment must be a file under your home directory")
    if not path.is_file():
        raise InvalidAttachment("attachment is not a regular file")
    if not os.access(path, os.R_OK):
        raise InvalidAttachment("attachment is not readable")
    size = path.stat().st_size
    if size == 0:
        raise InvalidAttachment("attachment is empty")
    if size > MAX_ATTACHMENT_BYTES:
        raise InvalidAttachment("attachment is larger than 100 MiB")
    return path


_FILENAME_BAD = re.compile(r"[\\/\x00-\x1f\x7f]")


def safe_filename(raw: object, *, fallback: str = "attachment") -> str:
    """Reduce a remote-supplied file name to a single safe path component."""
    name = clean_name(raw, max_length=160)
    # Keep only the last path component, then neutralise anything a file
    # system could interpret. Leading dots go too: no hidden files.
    name = re.split(r"[\\/]", name)[-1]
    name = _FILENAME_BAD.sub("_", name).strip(". ")
    if not name or name in (".", ".."):
        return fallback
    return name
