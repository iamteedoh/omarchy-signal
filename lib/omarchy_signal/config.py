"""Paths and user configuration.

Configuration is a small TOML file at ``$XDG_CONFIG_HOME/omarchy-signal/config.toml``.
Every key has a default so the file may be absent. Nothing secret lives here.
"""

from __future__ import annotations

import os
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


@dataclass
class Config:
    account: str = ""                      # E.164 of the linked account; "" = first account signal-cli knows
    signal_cli: str = "signal-cli"         # binary name or absolute path
    send_read_receipts: bool = True
    download_attachments: bool = True
    history_enabled: bool = True
    history_retain_days: int = 0           # 0 = keep forever
    notifications: str = "popup"           # popup | system | off
    notification_preview: bool = True      # show message text in popup (False = "New message")
    notification_timeout_ms: int = 8000
    typing_indicators: bool = True
    terminal_images: str = "auto"          # auto | on | off
    image_max_rows: int = 14
    qr_rows: int = 9                       # height of the linking QR code in terminal rows (image mode)
    device_name: str = "omarchy-signal"
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
        if cfg.terminal_images not in ("auto", "on", "off"):
            cfg.terminal_images = "auto"
        if cfg.trust_new_identities not in ("always", "on-first-use", "never"):
            cfg.trust_new_identities = "on-first-use"
        cfg.image_max_rows = max(2, min(60, cfg.image_max_rows))
        cfg.qr_rows = max(5, min(40, cfg.qr_rows))
        cfg.notification_timeout_ms = max(1000, min(120000, cfg.notification_timeout_ms))
        cfg.history_retain_days = max(0, cfg.history_retain_days)
        return cfg
