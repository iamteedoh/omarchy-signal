# SPDX-License-Identifier: GPL-3.0-or-later
"""Clickable links (OSC 8) and URL detection for terminal output.

Only ``http``, ``https`` and ``mailto`` URLs found in message text become
hyperlinks, and only after every byte of the target has been checked: a URL
is emitted inside an OSC 8 sequence, so it must never contain the bytes that
terminate or alter that sequence. ``file://`` links are generated *only* by
us, for attachment paths we located on disk, never from remote text.
"""

from __future__ import annotations

import re
from urllib.parse import quote

URL_RE = re.compile(
    r"(?<![\w@])((?:https?://|mailto:)[^\s<>\"'`\x00-\x1f\x7f]{1,2048})",
    re.IGNORECASE)
# Characters commonly attached to a URL in prose but not part of it.
_TRAIL = ".,;:!?)]}'\"»”’"

_OSC8_SAFE = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$")


def find_urls(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, url)`` spans for every URL in ``text``."""
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(1)
        # Balance a trailing ")" only if there is a matching "(" inside.
        while url and url[-1] in _TRAIL:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if len(url) < 8:
            continue
        out.append((m.start(1), m.start(1) + len(url), url))
    return out


def safe_href(url: str) -> str | None:
    """Percent-encode anything outside the OSC 8 safe set; refuse the rest."""
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("mailto:")):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f or ch in "\x9c\x9d\x1b" for ch in url):
        return None
    encoded = quote(url, safe="-._~:/?#[]@!$&'()*+,;=%")
    if not _OSC8_SAFE.match(encoded) or len(encoded) > 2083:
        return None
    return encoded


def osc8(href: str, text: str, *, link_id: str = "") -> str:
    """Wrap ``text`` in an OSC 8 hyperlink. ``href`` must already be safe."""
    params = f"id={link_id}" if link_id and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", link_id) else ""
    return f"\x1b]8;{params};{href}\x1b\\{text}\x1b]8;;\x1b\\"


def file_href(path: str) -> str | None:
    if not path.startswith("/") or "\x00" in path:
        return None
    encoded = "file://" + quote(path, safe="/-._~")
    if not _OSC8_SAFE.match(encoded):
        return None
    return encoded
