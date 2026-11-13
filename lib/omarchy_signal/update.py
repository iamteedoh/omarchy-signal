# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal updater
## Date Created: 2026-11-12
## Description: Checks the remote the plugin was added from for newer release tags; installing is left to omarchy plugin update.
"""Knowing about new releases.

The plugin only *notices* a new release: once a day at most it lists the
release tags (``vX.Y.Z``) on the git remote it was added from, and says so in
a popup, the bar panel and the terminal client. It never downloads, checks out
or runs code itself. Installing is left to Omarchy's own updater,
``omarchy plugin update iamteedoh.signal``, which shows the changes and asks
first; afterwards the plugin's sync step (``sync.py``) refreshes the service
and keybindings from the files Omarchy put in place.

git is always run with argv arrays, a remote can never be read as an option,
and nothing runs as root.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, bounded

PLUGIN_ID = "iamteedoh.signal"
DEFAULT_ORIGIN = "https://github.com/iamteedoh/omarchy-signal.git"
TAG_RE = re.compile(r"\Av(\d{1,4})\.(\d{1,4})\.(\d{1,5})\Z")     # \Z, not $: "$" also matches before a trailing newline
CHECK_INTERVAL = 24 * 3600
GIT_TIMEOUT = 120
Runner = Callable[..., subprocess.CompletedProcess]


class UpdateError(Exception):
    pass


def plugins_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base and base.startswith("/") else Path.home() / ".config"
    return root / "omarchy" / "plugins"


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = TAG_RE.match(text if text.startswith("v") else "v" + text)
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]


def safe_remote(url: str) -> bool:
    """A remote we are willing to hand to git: https, ssh or scp-style, or a local
    path; never something git would read as an option or a helper command."""
    if not url or url.startswith("-") or any(c in url for c in "\x00\n\r ") or "::" in url:
        return False
    return bool(re.match(r"^(https://|ssh://|git@[\w.-]+:|/)", url))


GIT_OUTPUT_LIMIT = 1024 * 1024    # a tag listing from the remote; ours is a few KiB


def _bounded_run(argv, *, cwd=None, timeout, env=None, **_kw) -> subprocess.CompletedProcess:
    """subprocess.run's shape over bounded.run: the remote decides how much
    ``git ls-remote`` prints, so it is read with a ceiling and one deadline."""
    res = bounded.run(argv, limit=GIT_OUTPUT_LIMIT, timeout=timeout, cwd=cwd, env=env)
    if res.timed_out:
        raise subprocess.TimeoutExpired(argv, timeout)
    if res.overflow:
        return subprocess.CompletedProcess(argv, 1, "", "the remote's answer is too long")
    return subprocess.CompletedProcess(argv, res.returncode, res.stdout.decode("utf-8", "replace"),
                                       res.stderr.decode("utf-8", "replace"))


def _git(args: list[str], cwd: Path | None = None, *, run: Runner = _bounded_run, timeout: int = GIT_TIMEOUT) -> str:
    exe = shutil.which("git")
    if not exe:
        raise UpdateError("git is not installed (omarchy pkg add git)")
    # Never sit waiting for a password or a key's passphrase.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    try:
        res = run([exe] + args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("git timed out (no network?)") from exc
    except OSError as exc:
        raise UpdateError(str(exc)) from exc
    if res.returncode != 0:
        raise UpdateError(git_error(res.stderr or ""))
    return res.stdout


def git_error(stderr: str) -> str:
    """The line of git's complaint that says what went wrong: its ``fatal:`` or
    ``error:`` line, not whatever advice it printed last."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    for prefix in ("fatal:", "error:"):
        for ln in lines:
            if ln.lower().startswith(prefix):
                return ln[len(prefix):].strip()[:200] or "git failed"
    return (lines[-1] if lines else "git failed")[:200]


def anonymous_url(url: str) -> str:
    """The https address of an ssh remote (``git@host:owner/repo.git`` or
    ``ssh://git@host/owner/repo.git``), or "" when it is not one. Listing the
    release tags of a public repository needs no key, so the check does not
    depend on an ssh agent being reachable from wherever it runs."""
    if url.startswith("ssh://"):
        m = re.match(r"\Assh://(?:[\w.-]+@)?([A-Za-z0-9][\w.-]*)/([\w.-]+(?:/[\w.-]+)+)/?\Z", url)    # with a port: no guess
    else:
        m = re.match(r"\A[\w.-]+@([A-Za-z0-9][\w.-]*):([\w.-]+(?:/[\w.-]+)+)/?\Z", url)
    if not m:
        return ""
    path = m.group(2)[:-4] if m.group(2).endswith(".git") else m.group(2)
    return f"https://{m.group(1)}/{path}.git"


@dataclass
class InstallInfo:
    """Where the plugin is installed and how: ``git`` (added by ``omarchy
    plugin add``), ``copy`` (copied by ``./install.sh`` from another checkout),
    ``link`` (a developer's symlink) or ``none`` (not installed at all)."""
    plugin_dir: Path
    kind: str
    origin: str
    source: str = ""          # for a copy: the checkout ``./install.sh`` was run from, while it still exists

    @classmethod
    def detect(cls, plugin_dir: Path | None = None, state_dir: Path | None = None, *, run: Runner = _bounded_run) -> "InstallInfo":
        directory = plugin_dir or plugins_dir() / PLUGIN_ID
        if directory.is_symlink():
            return cls(directory, "link", "")
        if not (directory / "manifest.json").is_file():
            return cls(directory, "none", "")
        if (directory / ".git").exists():
            try:
                origin = _git(["remote", "get-url", "origin"], directory, run=run, timeout=10).strip()
            except UpdateError:
                origin = ""
            return cls(directory, "git", origin)
        record = _install_record(state_dir)
        return cls(directory, "copy", _recorded_origin(record) or DEFAULT_ORIGIN, _recorded_source(record))


def _install_record(state_dir: Path | None) -> dict:
    """What ``install.sh`` wrote down when it copied the plugin from a checkout."""
    if state_dir is None:
        return {}
    try:
        data = json.loads((state_dir / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _recorded_origin(record: dict) -> str:
    """The remote the checkout came from, when it was a fork."""
    origin = record.get("origin")
    return origin if isinstance(origin, str) and safe_remote(origin) else ""


def _recorded_source(record: dict) -> str:
    """The checkout itself, when it is still there."""
    source = record.get("source")
    if not isinstance(source, str) or not source.startswith("/") or any(ord(c) < 32 for c in source) or len(source) > 1024:
        return ""
    return source if (Path(source) / "install.sh").is_file() and (Path(source) / ".git").exists() else ""


def newest_tag(listing: str) -> str | None:
    """The highest ``vX.Y.Z`` in ``git ls-remote --tags`` or ``git tag`` output.
    Anything else (pre-releases, odd names) is not a release."""
    best: tuple[tuple[int, int, int], str] | None = None
    for line in listing.splitlines():
        name = line.split("refs/tags/")[-1].strip()
        if name.endswith("^{}"):
            name = name[:-3]
        version = parse_version(name) if TAG_RE.match(name) else None
        if version and (best is None or version > best[0]):
            best = (version, name)
    return best[1] if best else None


@dataclass
class CheckResult:
    current: str
    latest: str | None          # newest release tag, without the "v"
    available: bool
    checked_at: float
    error: str = ""

    def to_json(self) -> dict:
        return {"current": self.current, "latest": self.latest, "available": self.available,
                "checkedAt": int(self.checked_at), "error": self.error}


def check(info: InstallInfo, *, current: str = __version__, run: Runner = _bounded_run) -> CheckResult:
    """Ask the remote which releases exist. Touches nothing on disk."""
    now = time.time()
    if info.kind == "link":
        return CheckResult(current, None, False, now, "this is a developer link to a checkout; update that checkout with git")
    if info.kind == "none":
        return CheckResult(current, None, False, now, "the plugin is not installed; add it with: omarchy plugin add " + DEFAULT_ORIGIN)
    if not safe_remote(info.origin):
        return CheckResult(current, None, False, now, "the plugin folder has no usable git remote")
    listing = None
    public = anonymous_url(info.origin)
    if public:
        # No credentials are offered: this either works for anyone or not at all.
        with contextlib.suppress(UpdateError):
            listing = _git(["-c", "credential.helper=", "ls-remote", "--tags", "--refs", "--", public], run=run, timeout=30)
    if listing is None:
        try:
            listing = _git(["ls-remote", "--tags", "--refs", "--", info.origin], run=run, timeout=30)
        except UpdateError as exc:
            return CheckResult(current, None, False, now, f"could not list the releases at {info.origin}: {exc}")
    tag = newest_tag(listing)
    if tag is None:
        return CheckResult(current, None, False, now)
    have, want = parse_version(current), parse_version(tag)
    return CheckResult(current, tag[1:], bool(have is not None and want is not None and want > have), now)


# -- the once-a-day check --------------------------------------------------------------

def cache_file(state_dir: Path) -> Path:
    return state_dir / "update-check.json"


def cached(state_dir: Path, *, current: str = __version__) -> CheckResult | None:
    try:
        data = json.loads(cache_file(state_dir).read_text(encoding="utf-8"))
        latest = data.get("latest")
        if latest is not None and parse_version(str(latest)) is None:
            return None
        result = CheckResult(current, latest, False, float(data.get("checkedAt", 0)), str(data.get("error", ""))[:200])
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    # Recomputed against the running version: after an update the old answer must not linger.
    have, want = parse_version(current), parse_version(latest) if latest else None
    result.available = bool(have and want and want > have)
    return result


def check_if_due(info: InstallInfo, state_dir: Path, *, enabled: bool = True, run: Runner = _bounded_run,
                 now: float | None = None) -> CheckResult | None:
    """The background check: at most once a day, never when switched off."""
    if not enabled:
        return None
    now = time.time() if now is None else now
    last = cached(state_dir)
    if last is not None and now - last.checked_at < CHECK_INTERVAL:
        return last
    result = check(info, run=run)
    store(state_dir, result)
    return result


def store(state_dir: Path, result: CheckResult) -> None:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_file(state_dir).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(result.to_json(), fh)
        os.replace(tmp, cache_file(state_dir))
    except OSError:
        pass


# -- applying an update --------------------------------------------------------------------

def host_update_argv(*, yes: bool = False) -> list[str]:
    """Omarchy's own plugin updater for this plugin. The plugin never fetches or
    installs code itself: updating is the host's job, through the same command
    every plugin uses, which shows the changes and asks first."""
    exe = shutil.which("omarchy")
    if not exe:
        raise UpdateError("the omarchy command is not available; update with: omarchy plugin update " + PLUGIN_ID)
    return [exe, "plugin", "update", PLUGIN_ID] + (["--yes"] if yes else [])


def advice(info: InstallInfo) -> str:
    """How to update this kind of install, in one line."""
    if info.kind == "git":
        return f"omarchy plugin update {PLUGIN_ID}"
    if info.kind == "link":
        return "this is a developer link to a checkout: update that checkout with git and rerun ./install.sh"
    if info.kind == "copy":
        if info.source:
            return f"this install was copied from {info.source}: update that checkout (git pull) and run ./install.sh there"
        return (f"this install is a plain copy that Omarchy cannot update; replace it with "
                f"`omarchy plugin remove {PLUGIN_ID}` then `omarchy plugin add {DEFAULT_ORIGIN} --enable` "
                f"(your account and history stay)")
    return f"add it with: omarchy plugin add {DEFAULT_ORIGIN} --enable"
