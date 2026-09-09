# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem completion for the attach prompt.

Given whatever the user has typed so far, return the directory being browsed
and the entries that match, directories first. Pure and cheap: one
``os.scandir`` per keystroke, no recursion, no symlink following beyond what
``stat`` needs to tell a directory from a file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".avif"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".ogg", ".m4a", ".aac", ".flac", ".wav", ".opus"}
DOC_EXT = {".pdf", ".txt", ".md", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".csv", ".zip"}


@dataclass
class Candidate:
    name: str          # display name, directories end with "/"
    path: str          # absolute path
    is_dir: bool
    size: int = 0
    kind: str = "file"  # dir | image | video | audio | doc | file

    @property
    def icon(self) -> str:
        return {"dir": "", "image": "󰋩", "video": "󰕧", "audio": "󰎈", "doc": "󰈙"}.get(self.kind, "󰈔")


def kind_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOC_EXT:
        return "doc"
    return "file"


def split_query(query: str) -> tuple[Path, str]:
    """``~/Pic`` -> (``/home/u``, ``Pic``); ``~/Pictures/`` -> (``.../Pictures``, ``""``)."""
    raw = query or "~/"
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(os.getcwd(), expanded)
    if raw.endswith("/"):
        return Path(expanded), ""
    head, tail = os.path.split(expanded)
    return Path(head or "/"), tail


def complete(query: str, *, limit: int = 200) -> tuple[Path, list[Candidate]]:
    """Entries in the typed directory whose names match the typed prefix.

    Prefix matches come first (case-insensitive), then substring matches;
    within each group directories precede files and names sort naturally.
    Hidden entries are listed only when the prefix itself starts with a dot.
    """
    base, prefix = split_query(query)
    try:
        entries = list(os.scandir(base))
    except OSError:
        return base, []
    want_hidden = prefix.startswith(".")
    low = prefix.lower()
    prefixed: list[tuple] = []
    contained: list[tuple] = []
    for entry in entries:
        name = entry.name
        if name.startswith(".") and not want_hidden:
            continue
        lname = name.lower()
        if low and not (lname.startswith(low) or low in lname):
            continue
        try:
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            continue
        cand = Candidate(name=name + ("/" if is_dir else ""), path=os.path.join(str(base), name),
                         is_dir=is_dir, size=size, kind="dir" if is_dir else kind_of(name))
        key = (0 if is_dir else 1, lname)
        (prefixed if (not low or lname.startswith(low)) else contained).append((key, cand))
    prefixed.sort(key=lambda t: t[0])
    contained.sort(key=lambda t: t[0])
    out = [c for _, c in prefixed] + [c for _, c in contained]
    return base, out[:limit]


def accept(query: str, cand: Candidate) -> str:
    """The query after choosing ``cand``: keeps the user's ``~`` spelling."""
    base, _ = split_query(query)
    raw = query or "~/"
    if raw.startswith("~"):
        home = str(Path.home())
        shown_base = "~" + str(base)[len(home):] if str(base).startswith(home) else str(base)
    else:
        shown_base = str(base)
    if not shown_base.endswith("/"):
        shown_base += "/"
    return shown_base + cand.name
