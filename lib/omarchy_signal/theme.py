"""Omarchy theme integration for the terminal client.

Omarchy writes the active theme to ``~/.local/state/omarchy/current/theme/``.
``colors.toml`` is the canonical palette (the same file the Quickshell shell's
``Color`` singleton reads), so the TUI and the popups always agree.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

RGB = tuple[int, int, int]

_FALLBACK = {
    "mode": "dark",
    "accent": "#8bc9eb",
    "selection": "#243d56",
    "muted": "#304860",
    "background": "#16242d",
    "dark_background": "#101b21",
    "darker_background": "#0b1216",
    "lighter_background": "#1b2d40",
    "foreground": "#d6e2ee",
    "dark_foreground": "#4d86b0",
    "light_foreground": "#d6e2ee",
    "bright_foreground": "#f2fcff",
    "red": "#4d86b0",
    "yellow": "#6fa4c9",
    "orange": "#8bc9eb",
    "green": "#5e95bc",
    "cyan": "#b4e4f6",
    "blue": "#6fb8e3",
    "magenta": "#8bc9eb",
    "brown": "#456475",
    "bright_red": "#73a6cb",
    "bright_yellow": "#9dcae5",
    "bright_green": "#86b7d8",
    "bright_cyan": "#d1eef8",
    "bright_blue": "#f2fcff",
    "bright_magenta": "#b1d8ee",
}


def parse_hex(value: object, default: RGB = (200, 200, 200)) -> RGB:
    if not isinstance(value, str):
        return default
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6:
        return default
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return default


def mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def luminance(c: RGB) -> float:
    r, g, b = (x / 255 for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@dataclass
class Theme:
    name: str = "unknown"
    mode: str = "dark"
    accent: RGB = (139, 201, 235)
    selection: RGB = (36, 61, 86)
    muted: RGB = (48, 72, 96)
    background: RGB = (22, 36, 45)
    dark_background: RGB = (16, 27, 33)
    darker_background: RGB = (11, 18, 22)
    lighter_background: RGB = (27, 45, 64)
    foreground: RGB = (214, 226, 238)
    dark_foreground: RGB = (77, 134, 176)
    light_foreground: RGB = (214, 226, 238)
    bright_foreground: RGB = (242, 252, 255)
    red: RGB = (77, 134, 176)
    yellow: RGB = (111, 164, 201)
    orange: RGB = (139, 201, 235)
    green: RGB = (94, 149, 188)
    cyan: RGB = (180, 228, 246)
    blue: RGB = (111, 184, 227)
    magenta: RGB = (139, 201, 235)
    brown: RGB = (69, 100, 117)
    bright_red: RGB = (115, 166, 203)
    bright_yellow: RGB = (157, 202, 229)
    bright_green: RGB = (134, 183, 216)
    bright_cyan: RGB = (209, 238, 248)
    bright_blue: RGB = (242, 252, 255)
    bright_magenta: RGB = (177, 216, 238)
    source_mtime: float = field(default=0.0, compare=False)

    @property
    def is_dark(self) -> bool:
        return self.mode != "light"

    # Derived tones used by the TUI chrome.
    @property
    def panel(self) -> RGB:
        return self.dark_background if self.is_dark else self.lighter_background

    @property
    def panel_alt(self) -> RGB:
        return self.lighter_background if self.is_dark else self.dark_background

    @property
    def border(self) -> RGB:
        return mix(self.muted, self.accent, 0.35)

    @property
    def dim(self) -> RGB:
        return mix(self.foreground, self.background, 0.45)

    @property
    def glow(self) -> RGB:
        return mix(self.accent, self.bright_foreground, 0.4)

    @classmethod
    def from_mapping(cls, data: dict, *, name: str = "unknown", mtime: float = 0.0) -> "Theme":
        merged = dict(_FALLBACK)
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, str):
                merged[k] = v
        theme = cls(name=name, source_mtime=mtime)
        theme.mode = "light" if merged.get("mode") == "light" else "dark"
        for f in fields(cls):
            if f.name in ("name", "mode", "source_mtime"):
                continue
            default = getattr(theme, f.name)
            setattr(theme, f.name, parse_hex(merged.get(f.name), default))
        return theme

    @classmethod
    def load(cls, theme_dir: Path | None = None) -> "Theme":
        theme_dir = theme_dir or (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
                                  / "omarchy/current/theme")
        colors = theme_dir / "colors.toml"
        name = "unknown"
        try:
            name = (theme_dir.parent / "theme.name").read_text(encoding="utf-8").strip() or name
        except OSError:
            pass
        try:
            raw = colors.read_bytes()
            mtime = colors.stat().st_mtime
        except OSError:
            return cls.from_mapping({}, name=name)
        try:
            data = tomllib.loads(raw.decode("utf-8", "replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        return cls.from_mapping(data, name=name, mtime=mtime)

    def changed_on_disk(self, theme_dir: Path | None = None) -> bool:
        theme_dir = theme_dir or (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
                                  / "omarchy/current/theme")
        try:
            return (theme_dir / "colors.toml").stat().st_mtime != self.source_mtime
        except OSError:
            return False
