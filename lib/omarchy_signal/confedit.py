# SPDX-License-Identifier: GPL-3.0-or-later
"""Edits to config files this plugin does not own.

``bindings.lua`` and ``omarchy-menu.jsonc`` belong to the user. Every edit here
has to be reversible and has to leave everything it did not write byte for byte
as it found it -- including comments, indentation and trailing punctuation.

Two bugs are the reason this module exists instead of the regexes that used to
live in ``install.sh`` and ``uninstall.sh``:

* ``sed "/^BEGIN$/,/^END$/d"`` deletes to end of file when the closing marker is
  missing, so a hand-edited ``bindings.lua`` lost every binding after the block
  (OMSIG-3). A range needs a *matched* pair or it must not fire at all.
* The menu entry was placed by testing whether the text before the closing brace
  ended in a comma. Omarchy's shipped menu file is mostly commented-out examples
  and those examples end in commas, so a real entry that needed a separator did
  not get one and the file stopped parsing (OMSIG-4).

Both formats are therefore edited through a scanner that knows what is code and
what is a comment, and never by matching raw text.
"""

from __future__ import annotations

import argparse
import json
import sys

# The single definition of what this plugin writes into the user's files.
# install.sh and uninstall.sh used to keep their own copies of the markers, with
# a comment asking the next person to keep them in step by hand.
BINDINGS_BEGIN = "-- BEGIN omarchy-signal"
BINDINGS_END = "-- END omarchy-signal"
MENU_KEY = "signal-tui"
MENU_ENTRY = (
    '"signal-tui": {"icon":"\U000f0b69","label":"Signal (terminal)",'
    '"action":"omarchy-signal open"}'
)

# --- marked blocks (bindings.lua) --------------------------------------------


class UnmatchedMarker(Exception):
    """A BEGIN marker with no matching END (or the reverse).

    Raised rather than guessing: the file is in a shape this plugin did not
    write, and the previous code's guess was to delete the rest of it.
    """


def find_blocks(text: str, begin: str, end: str) -> list[tuple[int, int]]:
    """Line index spans ``[start, stop)`` of every complete ``begin``/``end`` block.

    Markers match a whole line, so a marker quoted inside a Lua string is not one.
    Raises :class:`UnmatchedMarker` if a marker is left open or closes nothing.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip("\r") == begin:
            for j in range(i + 1, len(lines)):
                if lines[j].strip("\r") == end:
                    blocks.append((i, j + 1))
                    i = j + 1
                    break
            else:
                raise UnmatchedMarker(f"{begin!r} at line {i + 1} has no matching {end!r}")
        elif lines[i].strip("\r") == end:
            raise UnmatchedMarker(f"{end!r} at line {i + 1} closes nothing")
        else:
            i += 1
    return blocks


def _join(lines: list[str], trailing_newline: bool) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if trailing_newline else "")


def remove_block(text: str, begin: str, end: str) -> str:
    """Drop every complete block. Text outside the markers is untouched."""
    blocks = find_blocks(text, begin, end)
    if not blocks:
        return text
    lines = text.splitlines()
    drop = {i for start, stop in blocks for i in range(start, stop)}
    kept = [ln for i, ln in enumerate(lines) if i not in drop]
    return _join(kept, text.endswith("\n"))


def write_block(text: str, begin: str, end: str, body: str) -> str:
    """Replace the block with ``body``, or append it when there is none."""
    block = [begin, *body.splitlines(), end]
    blocks = find_blocks(text, begin, end)
    lines = text.splitlines()
    if blocks:
        # Replace the first block in place and drop any duplicates, so the
        # bindings keep their position in the user's file across updates.
        first, *rest = blocks
        drop = {i for start, stop in rest for i in range(start, stop)}
        out: list[str] = []
        for i, ln in enumerate(lines):
            if i in drop:
                continue
            if i == first[0]:
                out.extend(block)
            if first[0] <= i < first[1]:
                continue
            out.append(ln)
        return _join(out, True)
    while lines and not lines[-1].strip():
        lines.pop()
    return _join([*lines, *block], True)


# --- JSONC (omarchy-menu.jsonc) ----------------------------------------------

CODE, STRING, COMMENT = "code", "string", "comment"


def scan(text: str):
    """Yield ``(index, char, kind)`` with ``kind`` one of code/string/comment.

    Knowing which is which is the whole point: every defect this replaces came
    from treating a comment, or a brace inside one, as structure.
    """
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            yield i, ch, STRING
            i += 1
            while i < n:
                if text[i] == "\\":
                    yield i, text[i], STRING
                    if i + 1 < n:
                        yield i + 1, text[i + 1], STRING
                    i += 2
                    continue
                yield i, text[i], STRING
                i += 1
                if text[i - 1] == '"':
                    break
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                yield i, text[i], COMMENT
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            while i < end:
                yield i, text[i], COMMENT
                i += 1
            continue
        yield i, ch, CODE
        i += 1


def _meaningful_positions(text: str) -> list[tuple[int, str]]:
    """Every non-whitespace character that is not inside a comment.

    String characters count: a member whose value is a string ends in ``"``, and
    looking only at ``code`` characters would report the ``:`` before it as the
    end of the member and insert the separating comma there.
    """
    return [(i, c) for i, c, kind in scan(text) if kind != COMMENT and not c.isspace()]


def top_level_close(text: str) -> int | None:
    """Index of the ``}`` that closes the outermost object, ignoring comments."""
    depth = 0
    opened = False
    for i, c, kind in scan(text):
        if kind != CODE:
            continue
        if c == "{":
            depth += 1
            opened = True
        elif c == "}":
            depth -= 1
            if opened and depth == 0:
                return i
    return None


def entry_span(text: str, key: str) -> tuple[int, int] | None:
    """Span of a top-level ``"key": <value>`` member, or ``None``.

    Only a real member counts -- a commented-out copy of the same key is left
    alone, which the line-oriented regex could not do.
    """
    target = json.dumps(key)
    depth = 0
    i = 0
    chars = list(scan(text))
    while i < len(chars):
        idx, c, kind = chars[i]
        if kind == CODE:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
        if depth == 1 and kind == STRING and c == '"' and text.startswith(target, idx):
            start = idx
            j = idx + len(target)
            # Skip to the value, then run to the end of it.
            while j < len(text) and text[j] != ":":
                if not text[j].isspace():
                    break
                j += 1
            if j < len(text) and text[j] == ":":
                return start, _value_end(text, j + 1)
        i += 1
    return None


def _value_end(text: str, start: int) -> int:
    """Index just past the value beginning at or after ``start``."""
    depth = 0
    seen = False
    for i, c, kind in scan(text[start:]):
        pos = start + i
        if kind == STRING:
            if c == '"':
                seen = not seen if depth == 0 else seen
                if depth == 0 and not seen:
                    return pos + 1
            continue
        if kind == COMMENT:
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            if depth == 0:
                return pos
            depth -= 1
            if depth == 0:
                return pos + 1
        elif depth == 0 and c == ",":
            return pos
    return len(text)


def _last_meaningful_before(text: str, limit: int) -> tuple[int, str] | None:
    last = None
    for i, c in _meaningful_positions(text):
        if i >= limit:
            break
        last = (i, c)
    return last


def _indent_of_line(text: str, idx: int) -> str:
    start = text.rfind("\n", 0, idx) + 1
    line = text[start:idx]
    return line[: len(line) - len(line.lstrip())]


def menu_add_entry(text: str, key: str, entry: str) -> str:
    """Add ``entry`` as a top-level member. Idempotent.

    The entry goes immediately after the last existing member's value -- before
    any trailing comment -- so removing it later restores the file exactly.
    """
    if entry_span(text, key) is not None:
        return text
    close = top_level_close(text)
    if close is None:
        return "{\n  " + entry + "\n}\n"
    last = _last_meaningful_before(text, close)
    if last is None or last[1] == "{":
        # No members yet: put it on its own line above the closing brace.
        line_start = text.rfind("\n", 0, close) + 1
        indent = _indent_of_line(text, close) + "  "
        return text[:line_start] + indent + entry + "\n" + text[line_start:]
    idx, ch = last
    if ch == ",":
        prev = _last_meaningful_before(text, idx)
        idx = prev[0] if prev else idx
    indent = _indent_of_line(text, idx) or "  "
    return text[: idx + 1] + ",\n" + indent + entry + text[idx + 1 :]


def menu_remove_entry(text: str, key: str) -> str:
    """Remove the member added by :func:`menu_add_entry`, and nothing else."""
    span = entry_span(text, key)
    if span is None:
        return text
    start, end = span
    prev = _last_meaningful_before(text, start)
    if prev and prev[1] == ",":
        # Exactly what menu_add_entry inserted: the separator plus the entry.
        return text[: prev[0]] + text[end:]
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end + 1
    return text[:line_start] + text[line_end:]


# --- CLI ---------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="omarchy-signal-confedit", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("block-write", "block-remove"):
        sp = sub.add_parser(name)
        sp.add_argument("--file", required=True)
        sp.add_argument("--begin", default=BINDINGS_BEGIN)
        sp.add_argument("--end", default=BINDINGS_END)

    for name in ("menu-add", "menu-remove"):
        sp = sub.add_parser(name)
        sp.add_argument("--file", required=True)
        sp.add_argument("--key", default=MENU_KEY)
        if name == "menu-add":
            sp.add_argument("--entry", default=MENU_ENTRY)

    args = p.parse_args(argv)
    text = _read(args.file)
    try:
        if args.cmd == "block-write":
            out = write_block(text, args.begin, args.end, sys.stdin.read().rstrip("\n"))
        elif args.cmd == "block-remove":
            out = remove_block(text, args.begin, args.end)
        elif args.cmd == "menu-add":
            out = menu_add_entry(text, args.key, args.entry)
        else:
            out = menu_remove_entry(text, args.key)
    except UnmatchedMarker as exc:
        # Deliberately not "best effort": the previous best effort deleted the
        # rest of the user's file.
        print(f"omarchy-signal: {args.file}: {exc}; left unchanged", file=sys.stderr)
        return 3
    if out != text:
        _write(args.file, out)
        print("changed")
    else:
        print("unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
