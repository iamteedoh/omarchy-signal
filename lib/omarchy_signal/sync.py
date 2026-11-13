# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal sync
## Date Created: 2026-11-12
## Description: Brings the files install.sh placed outside the plugin folder in line with the plugin after its files changed.
"""Catching up after ``omarchy plugin update``.

``omarchy plugin update`` moves the plugin folder to newer code and stops
there: it does not know this plugin also has a systemd unit, a keybinding
block, a post-update hook and a running bridge that is still executing the old
code. The shell runs :func:`sync` when it sees the plugin's version change, so
that one Omarchy command is a complete update.

It only ever *refreshes what install.sh already put in place*:

* a unit, hook or keybinding block that is not installed stays not installed
  (the user passed ``--no-bind``, or never ran the installer);
* a bridge that is stopped, disabled or masked is left exactly as it is: that
  is the user's choice, and this must never turn the service back on;
* nothing here installs packages, asks a question or needs a terminal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import __version__, confedit

UNIT = "omarchy-signal.service"
Runner = Callable[..., subprocess.CompletedProcess]


def _config_home(home: Path) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base and base.startswith("/") else home / ".config"


def bindings_body(plugin_dir: Path) -> str:
    """config/bindings.lua without its header, which ends at the first blank line."""
    text = (plugin_dir / "config" / "bindings.lua").read_text(encoding="utf-8")
    head, sep, body = text.partition("\n\n")
    return (body if sep else head).rstrip("\n")


def _refresh_file(source: Path, target: Path, mode: int) -> str:
    """Copy ``source`` over ``target`` only when ``target`` is already there and differs."""
    if not target.is_file():
        return "absent"
    try:
        new = source.read_bytes()
        if target.read_bytes() == new:
            return "unchanged"
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(new)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except OSError as exc:
        return f"failed: {exc}"
    return "updated"


def _refresh_bindings(plugin_dir: Path, target: Path) -> str:
    if not target.is_file():
        return "absent"
    try:
        text = target.read_text(encoding="utf-8")
        if not confedit.find_blocks(text, confedit.BINDINGS_BEGIN, confedit.BINDINGS_END):
            return "absent"                         # installed with --no-bind, or removed by hand
        out = confedit.write_block(text, confedit.BINDINGS_BEGIN, confedit.BINDINGS_END, bindings_body(plugin_dir))
        if out == text:
            return "unchanged"
        tmp = target.with_name(target.name + ".omarchy-signal.tmp")
        tmp.write_text(out, encoding="utf-8")
        os.replace(tmp, target)
    except confedit.UnmatchedMarker as exc:
        return f"failed: {exc}"                     # a shape this plugin did not write: leave it alone
    except OSError as exc:
        return f"failed: {exc}"
    return "updated"


def _systemctl(args: list[str], run: Runner) -> subprocess.CompletedProcess | None:
    exe = shutil.which("systemctl")
    if not exe:
        return None
    try:
        return run([exe, "--user"] + args, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _bridge_version(run_dir: Path | None) -> str:
    """The version the running bridge announces, or "" when it cannot be asked."""
    import asyncio

    from .client import BridgeClient, BridgeError
    from .config import Paths

    async def ask() -> str:
        paths = Paths()
        if run_dir is not None:
            paths.run_dir = run_dir
        client = BridgeClient(paths)
        try:
            hello = await client.connect(timeout=3)
            return str(hello.get("version") or "")
        finally:
            await client.close()
    try:
        return asyncio.run(ask())
    except (BridgeError, OSError, asyncio.TimeoutError):
        return ""


def sync(plugin_dir: Path, *, home: Path | None = None, run: Runner = subprocess.run,
         bridge_version: Callable[[], str] | None = None) -> dict:
    """Refresh the unit, the hook and the keybindings, then hand the running
    bridge the new code. Returns what happened to each, for the shell to show."""
    home = home or Path.home()
    config = _config_home(home)
    result = {"version": __version__}
    result["unit"] = _refresh_file(plugin_dir / "systemd" / UNIT, config / "systemd" / "user" / UNIT, 0o644)
    result["hook"] = _refresh_file(plugin_dir / "scripts" / "post-update-hook.sh",
                                   config / "omarchy" / "hooks" / "post-update.d" / "omarchy-signal", 0o755)
    result["bindings"] = _refresh_bindings(plugin_dir, config / "hypr" / "bindings.lua")
    if result["bindings"] == "updated" and shutil.which("hyprctl"):
        try:
            run(["hyprctl", "reload"], capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    if result["unit"] == "updated":
        _systemctl(["daemon-reload"], run)

    # Only a bridge that is running gets restarted, and only when it is running
    # something else than the plugin folder now holds.
    active = _systemctl(["is-active", "--quiet", UNIT], run)
    if active is None or active.returncode != 0:
        result["bridge"] = "not running"
    else:
        running = bridge_version() if bridge_version is not None else _bridge_version(None)
        if result["unit"] != "updated" and running == __version__:
            result["bridge"] = "unchanged"
        else:
            restarted = _systemctl(["restart", UNIT], run)
            result["bridge"] = "restarted" if restarted is not None and restarted.returncode == 0 else "failed: restart"
    return result
