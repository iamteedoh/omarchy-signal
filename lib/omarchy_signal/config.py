# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths and user configuration.

Configuration is a small TOML file at ``$XDG_CONFIG_HOME/omarchy-signal/config.toml``.
Every key has a default so the file may be absent. Nothing secret lives here.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def runtime_dir() -> Path:
    """Per-user tmpfs directory. Falls back to a private dir under /tmp when
    XDG_RUNTIME_DIR is unset (e.g. some cron/ssh sessions)."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value:
        return Path(value)
    return Path(f"/tmp/omarchy-signal-{os.getuid()}")


@dataclass
class Paths:
    config_dir: Path = field(default_factory=lambda: xdg("XDG_CONFIG_HOME", ".config") / "omarchy-signal")
    data_dir: Path = field(default_factory=lambda: xdg("XDG_DATA_HOME", ".local/share") / "omarchy-signal")
    state_dir: Path = field(default_factory=lambda: xdg("XDG_STATE_HOME", ".local/state") / "omarchy-signal")
    run_dir: Path = field(default_factory=lambda: runtime_dir() / "omarchy-signal")
    signal_cli_data: Path = field(default_factory=lambda: xdg("XDG_DATA_HOME", ".local/share") / "signal-cli")
    omarchy_theme_dir: Path = field(default_factory=lambda: xdg("XDG_STATE_HOME", ".local/state") / "omarchy/current/theme")

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def socket(self) -> Path:
        return self.run_dir / "bridge.sock"

    @property
    def database(self) -> Path:
        return self.data_dir / "history.sqlite3"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "bridge.log"

    def ensure(self) -> None:
        """Create the directories the bridge writes to, owner-only.

        The config directory is deliberately left alone: under the systemd
        unit's ``ProtectHome=read-only`` it is not writable, and the bridge
        only ever reads it."""
        for d in (self.data_dir, self.state_dir, self.run_dir, self.attachments_dir):
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        # The runtime dir holds the socket. With XDG_RUNTIME_DIR unset it falls
        # back to a path under /tmp that another local user could have created
        # first, so insist that it is a private directory of ours (no symlink).
        st = self.run_dir.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or (st.st_mode & 0o077):
            raise PermissionError(f"refusing to use {self.run_dir}: not a private directory owned by this user")


@dataclass
class Config:
    account: str = ""                      # E.164 of the linked account; "" = first account signal-cli knows
    signal_cli: str = "signal-cli"         # binary name or absolute path
    send_read_receipts: bool = True
    download_attachments: bool = True
    history_enabled: bool = True
    history_retain_days: int = 0           # 0 = keep forever
    notifications: str = "popup"           # popup | system | off
    notification_content: str = "name-and-message"   # name-and-message | name-only | none  (like Signal's "Notification content")
    notification_preview: bool = True      # legacy alias: False == "name-only"
    notification_timeout_ms: int = 8000
    respect_dnd: bool = True               # no popups while Omarchy's Do Not Disturb is on
    notification_sound: str = ""           # sound file played with a popup (empty = silent)
    typing_indicators: bool = True
    terminal_images: str = "auto"          # auto | on | off   (can the terminal draw images at all)
    inline_images: str = "always"          # always | click | never   (show received images in the thread)
    message_layout: str = "left"           # left (everything left-aligned) | bubbles (yours on the right)
    emoji_autoconvert: bool = True         # turn :smile: and :D into emoji as you type (and on send); off sends them raw
    attachment_thumbnails: bool = True     # picture tiles in the attachment picker (off = plain list)
    scroll_speed: int = 9                  # chat window: trackpad/wheel scroll multiplier (0 = Qt default)
    terminal: str = "auto"                 # terminal for `omarchy-signal open`: auto (system default) | ghostty | kitty | wezterm | foot | alacritty
    image_max_rows: int = 14
    qr_rows: int = 9                       # height of the linking QR code in terminal rows (image mode)
    qr_style: str = "auto"                 # auto (shell popup, else image, else text) | shell | image | half | quad | braille
    device_name: str = "omarchy-signal"
    save_dir: str = "~/Downloads"          # where "save" puts received attachments
    log_level: str = "info"
    trust_new_identities: str = "on-first-use"  # passed to signal-cli verbatim (allow-listed)

    @classmethod
    def load(cls, paths: Paths | None = None) -> "Config":
        paths = paths or Paths()
        cfg = cls()
        try:
            raw = paths.config_file.read_bytes()
        except FileNotFoundError:
            return cfg
        data = tomllib.loads(raw.decode("utf-8", "replace"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        flat: dict = {}
        for key, value in data.items():
            if isinstance(value, dict):
                for sub, subval in value.items():
                    flat[f"{key}_{sub}"] = subval
                    flat[sub] = subval if sub not in flat else flat[sub]
            else:
                flat[key] = value
        for name in cfg.__dataclass_fields__:
            if name in flat:
                current = getattr(cfg, name)
                value = flat[name]
                if isinstance(current, bool):
                    setattr(cfg, name, bool(value))
                elif isinstance(current, int):
                    try:
                        setattr(cfg, name, int(value))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(value, (str, int, float)):
                    setattr(cfg, name, str(value))
        if cfg.notifications not in ("popup", "system", "off"):
            cfg.notifications = "popup"
        if cfg.notification_content not in ("name-and-message", "name-only", "none"):
            cfg.notification_content = "name-and-message"
        if "notification_content" not in flat and not cfg.notification_preview:
            cfg.notification_content = "name-only"
        cfg.notification_preview = cfg.notification_content == "name-and-message"
        if cfg.terminal_images not in ("auto", "on", "off"):
            cfg.terminal_images = "auto"
        if cfg.inline_images not in ("always", "click", "never"):
            cfg.inline_images = "always"
        if cfg.message_layout not in ("left", "bubbles"):
            cfg.message_layout = "left"
        if cfg.terminal not in ("auto", "ghostty", "kitty", "wezterm", "foot", "alacritty"):
            cfg.terminal = "auto"
        if cfg.trust_new_identities not in ("always", "on-first-use", "never"):
            cfg.trust_new_identities = "on-first-use"
        cfg.image_max_rows = max(2, min(60, cfg.image_max_rows))
        cfg.qr_rows = max(5, min(40, cfg.qr_rows))
        if cfg.qr_style not in ("auto", "image", "shell", "half", "quad", "braille"):
            cfg.qr_style = "auto"
        cfg.notification_timeout_ms = max(1000, min(120000, cfg.notification_timeout_ms))
        cfg.history_retain_days = max(0, cfg.history_retain_days)
        return cfg


# --------------------------------------------------------------------------
# Settings schema: what the settings screen and `omarchy-signal settings`
# expose, grouped the way Signal's own client groups them.
# --------------------------------------------------------------------------

SETTINGS: list[dict] = [
    {"section": "Notifications", "key": "notifications", "label": "Notifications", "type": "choice",
     "choices": ["popup", "system", "off"], "help": "popup: click-to-reply toast · system: plain Omarchy notification · off"},
    {"section": "Notifications", "key": "notification_content", "label": "Notification content", "type": "choice",
     "choices": ["name-and-message", "name-only", "none"], "help": "What a popup reveals: sender and text, sender only, or neither"},
    {"section": "Notifications", "key": "notification_timeout_ms", "label": "Popup stays for (ms)", "type": "int",
     "min": 1000, "max": 120000, "step": 1000, "help": "How long a popup stays on screen"},
    {"section": "Notifications", "key": "respect_dnd", "label": "Honor Do Not Disturb", "type": "bool",
     "help": "No popups while Omarchy's Do Not Disturb is on (unread counts still update)"},
    {"section": "Notifications", "key": "notification_sound", "label": "Notification sound", "type": "path",
     "help": "A .wav/.ogg/.oga file played with each popup (pw-play); empty for silence"},
    {"section": "Privacy", "key": "send_read_receipts", "label": "Read receipts", "type": "bool",
     "help": "Tell senders you read their messages; the same receipt clears the notification on your phone"},
    {"section": "Privacy", "key": "typing_indicators", "label": "Typing indicators", "type": "bool",
     "help": "Send and show typing indicators"},
    {"section": "Privacy", "key": "trust_new_identities", "label": "New safety numbers", "type": "choice",
     "choices": ["on-first-use", "always", "never"], "restart": True,
     "help": "on-first-use matches Signal's clients; never refuses changed safety numbers"},
    {"section": "Chats & media", "key": "inline_images", "label": "Show images in chat", "type": "choice",
     "choices": ["always", "click", "never"], "help": "always: as they arrive · click: only after you click · never"},
    {"section": "Chats & media", "key": "download_attachments", "label": "Auto-download attachments", "type": "bool",
     "restart": True, "help": "Fetch attachments as messages arrive"},
    {"section": "Chats & media", "key": "attachment_thumbnails", "label": "Thumbnails when attaching", "type": "bool",
     "help": "Picture tiles in the attachment picker; off shows a plain file list"},
    {"section": "Chats & media", "key": "save_dir", "label": "Save attachments to", "type": "path",
     "help": "Folder used by the attachment menu's save action"},
    {"section": "Chats & media", "key": "emoji_autoconvert", "label": "Convert :smile: and :D to emoji", "type": "bool",
     "help": "As you type (after the space) and on send; off sends them exactly as written"},
    {"section": "Chats & media", "key": "message_layout", "label": "Message layout", "type": "choice",
     "choices": ["left", "bubbles"], "help": "left: everything left-aligned · bubbles: yours on the right"},
    {"section": "Appearance", "key": "scroll_speed", "label": "Chat window scroll speed", "type": "int",
     "min": 0, "max": 20, "step": 1, "help": "Multiplier for trackpad and mouse-wheel scrolling in the popup and chat window; 0 = Qt's default"},
    {"section": "Appearance", "key": "terminal", "label": "Terminal for the client", "type": "choice",
     "choices": ["auto", "ghostty", "kitty", "wezterm", "foot", "alacritty"], "help": "auto follows omarchy default terminal; images need ghostty, kitty or wezterm"},
    {"section": "Appearance", "key": "terminal_images", "label": "Terminal image support", "type": "choice",
     "choices": ["auto", "on", "off"], "help": "auto probes the terminal"},
    {"section": "Appearance", "key": "image_max_rows", "label": "Inline image height (rows)", "type": "int",
     "min": 2, "max": 60, "step": 1},
    {"section": "Appearance", "key": "qr_style", "label": "Linking QR code", "type": "choice",
     "choices": ["auto", "shell", "image", "half", "quad", "braille"], "help": "auto: shell popup, else image, else text"},
    {"section": "Data", "key": "history_enabled", "label": "Keep message history", "type": "bool", "restart": True,
     "help": "Store messages on disk (owner-only SQLite); off = nothing survives a restart"},
    {"section": "Data", "key": "history_retain_days", "label": "Delete history after (days)", "type": "int",
     "min": 0, "max": 3650, "step": 7, "help": "0 keeps everything"},
    {"section": "Data", "key": "device_name", "label": "Device name", "type": "text", "restart": True,
     "help": "Shown in Signal's Linked devices"},
]

RESTART_REQUIRED = {s["key"] for s in SETTINGS if s.get("restart")} | {"account", "signal_cli", "log_level"}
LIVE_KEYS = {s["key"] for s in SETTINGS if not s.get("restart")}


def setting_spec(key: str) -> dict | None:
    for spec in SETTINGS:
        if spec["key"] == key:
            return spec
    return None


def coerce_setting(key: str, raw) -> object:
    """Validate a value for ``key`` against the schema; raises ValueError."""
    spec = setting_spec(key)
    if spec is None:
        raise ValueError(f"unknown setting: {key}")
    kind = spec["type"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{key} must be true or false")
    if kind == "int":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        return max(spec["min"], min(spec["max"], value))
    if kind == "choice":
        text = str(raw).strip()
        if text not in spec["choices"]:
            raise ValueError(f"{key} must be one of: {', '.join(spec['choices'])}")
        return text
    text = str(raw).strip()
    if "\n" in text or "\x00" in text or len(text) > 500:
        raise ValueError(f"{key} is not a valid value")
    return text


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save_config(cfg: Config, paths: Paths | None = None) -> Path:
    """Write ``config.toml`` from ``cfg``: every key, one comment each, 0600.

    The file is regenerated rather than patched, so it is always complete and
    valid; hand edits to values survive because they were loaded into ``cfg``
    first, hand-written comments do not."""
    import os
    paths = paths or Paths()
    lines = ["# omarchy-signal configuration, edited by `omarchy-signal settings` and the client's settings screen.",
             "# Live keys apply immediately; keys marked (restart) need: systemctl --user restart omarchy-signal", ""]
    lines += ["account = " + _toml_value(cfg.account) + "   # E.164 of the account to use; empty = first linked (restart)",
              "signal_cli = " + _toml_value(cfg.signal_cli) + "   # (restart)",
              "log_level = " + _toml_value(cfg.log_level) + "   # debug | info | warning | error (restart)", ""]
    section = None
    for spec in SETTINGS:
        if spec["section"] != section:
            section = spec["section"]
            lines.append(f"# ── {section} ──")
        note = spec.get("help", "")
        if spec.get("restart"):
            note = (note + " " if note else "") + "(restart)"
        lines.append(f"{spec['key']} = {_toml_value(getattr(cfg, spec['key']))}" + (f"   # {note}" if note else ""))
    lines.append("")
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(paths.config_dir, 0o700)
    tmp = paths.config_file.with_suffix(".toml.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    os.replace(tmp, paths.config_file)
    os.chmod(paths.config_file, 0o600)
    return paths.config_file
