# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal updater
## Date Created: 2026-11-12
## Description: Checks for and installs new releases of omarchy-signal through git, from the same remote the plugin was added from.
"""Updating from inside the program.

On Omarchy a plugin is a folder in ``~/.config/omarchy/plugins``. Added with
``omarchy plugin add`` it is a git checkout, and updating it is the same thing
done again with a newer release: fetch, move the checkout to the newest release
tag, run *that* version's ``install.sh`` in place. So updating trusts exactly
what adding the plugin trusted (the remote, over the transport it was cloned
with) and nothing new.

An older install made by ``./install.sh`` from a separate checkout is a plain
copy with no ``.git``, which ``omarchy plugin update`` cannot see. Updating one
replaces it with a git checkout of the release (the old folder is kept as a
backup next to it), so from then on both commands work.

Rules that keep it from doing damage:

* Only release tags (``vX.Y.Z``) are candidates, never the tip of a branch.
* A checkout moves by fast-forward only. History that does not contain the
  current commit is refused, as is a checkout with local changes or one that is
  not on its default branch: that is somebody's work in progress.
* A developer's ``--link`` install is never touched.
* git is always run with argv arrays, tag names must match a strict pattern,
  and a remote or path can never be read as an option.
* The new release has to pass ``omarchy plugin validate`` before it is used;
  a checkout that fails is put back where it was.
* Nothing runs as root. ``install.sh`` asks before it installs packages.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

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


def _git(args: list[str], cwd: Path | None = None, *, run: Runner = subprocess.run, timeout: int = GIT_TIMEOUT) -> str:
    exe = shutil.which("git")
    if not exe:
        raise UpdateError("git is not installed (omarchy pkg add git)")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")      # never sit waiting for a password
    try:
        res = run([exe] + args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("git timed out (no network?)") from exc
    except OSError as exc:
        raise UpdateError(str(exc)) from exc
    if res.returncode != 0:
        last = (res.stderr or "").strip().splitlines()[-1:] or ["git failed"]
        raise UpdateError(last[0][:200])
    return res.stdout


@dataclass
class InstallInfo:
    """Where the plugin is installed and how: ``git`` (added by ``omarchy
    plugin add``), ``copy`` (copied by ``./install.sh`` from another checkout),
    ``link`` (a developer's symlink) or ``none`` (not installed at all)."""
    plugin_dir: Path
    kind: str
    origin: str

    @classmethod
    def detect(cls, plugin_dir: Path | None = None, state_dir: Path | None = None, *, run: Runner = subprocess.run) -> "InstallInfo":
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
        return cls(directory, "copy", _recorded_origin(state_dir) or DEFAULT_ORIGIN)


def _recorded_origin(state_dir: Path | None) -> str:
    """The remote ``install.sh`` copied the plugin from, when it was a fork."""
    if state_dir is None:
        return ""
    try:
        data = json.loads((state_dir / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    origin = data.get("origin") if isinstance(data, dict) else None
    return origin if isinstance(origin, str) and safe_remote(origin) else ""


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


def check(info: InstallInfo, *, current: str = __version__, run: Runner = subprocess.run) -> CheckResult:
    """Ask the remote which releases exist. Touches nothing on disk."""
    now = time.time()
    if info.kind == "link":
        return CheckResult(current, None, False, now, "this is a developer link to a checkout; update that checkout with git")
    if info.kind == "none":
        return CheckResult(current, None, False, now, "the plugin is not installed; add it with: omarchy plugin add " + DEFAULT_ORIGIN)
    if not safe_remote(info.origin):
        return CheckResult(current, None, False, now, "the plugin folder has no usable git remote")
    try:
        listing = _git(["ls-remote", "--tags", "--refs", "--", info.origin], run=run, timeout=30)
    except UpdateError as exc:
        return CheckResult(current, None, False, now, str(exc))
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


def check_if_due(info: InstallInfo, state_dir: Path, *, enabled: bool = True, run: Runner = subprocess.run,
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

def plan(checkout: Path, *, current: str = __version__, run: Runner = subprocess.run) -> str | None:
    """Fetch, and return the release tag to move a git-managed plugin folder to,
    or None when it is up to date. Raises UpdateError when the checkout is not
    one we may move."""
    if _git(["status", "--porcelain", "--untracked-files=no"], checkout, run=run).strip():
        raise UpdateError(f"{checkout} has local changes; commit or stash them, or update it yourself with git")
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], checkout, run=run).strip()
    default = _default_branch(checkout, run=run)
    if branch != default:
        raise UpdateError(f"{checkout} is on '{branch}', not '{default}': that looks like work in progress, so it was left alone")
    _git(["fetch", "--quiet", "--tags", "--prune", "origin"], checkout, run=run)
    tag = newest_tag(_git(["tag", "--list", "v*"], checkout, run=run))
    if tag is None:
        return None
    have, want = parse_version(current), parse_version(tag)
    if have is None or want is None or want <= have:
        return None
    # Fast-forward only: the release must contain what is checked out now.
    try:
        _git(["merge-base", "--is-ancestor", "HEAD", f"refs/tags/{tag}"], checkout, run=run)
    except UpdateError as exc:
        raise UpdateError(f"release {tag} does not continue from this checkout's history; not touching it") from exc
    return tag


def _default_branch(checkout: Path, *, run: Runner = subprocess.run) -> str:
    try:
        ref = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], checkout, run=run).strip()
        return ref.split("/", 1)[1] if "/" in ref else ref
    except UpdateError:
        return "main"


def validate(directory: Path, *, run: Runner = subprocess.run) -> None:
    """The same gate ``omarchy plugin add`` and ``omarchy plugin update`` apply,
    plus the one thing only we know: it must still be this plugin."""
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("the release has no readable manifest.json") from exc
    if not isinstance(manifest, dict) or manifest.get("id") != PLUGIN_ID:
        raise UpdateError("the release is not this plugin (its manifest id differs)")
    if not (directory / "install.sh").is_file():
        raise UpdateError("the release has no install.sh")
    exe = shutil.which("omarchy-plugin-validate")
    if exe:
        res = run([exe, str(directory)], capture_output=True, text=True, timeout=60, check=False)
        if res.returncode != 0:
            last = (res.stderr or res.stdout or "").strip().splitlines()[-1:] or ["validation failed"]
            raise UpdateError("the release failed omarchy plugin validate: " + last[0][:200])


def fast_forward(checkout: Path, tag: str, *, run: Runner = subprocess.run) -> None:
    """Move a git-managed plugin folder to ``tag``; put it back if the release does not validate."""
    if not TAG_RE.match(tag):
        raise UpdateError("not a release tag")
    before = _git(["rev-parse", "HEAD"], checkout, run=run).strip()
    _git(["merge", "--ff-only", "--quiet", f"refs/tags/{tag}"], checkout, run=run)
    try:
        validate(checkout, run=run)
    except UpdateError:
        if re.fullmatch(r"[0-9a-f]{40,64}", before):
            _git(["reset", "--hard", "--quiet", before], checkout, run=run)
        raise


def adopt(info: InstallInfo, tag: str, *, run: Runner = subprocess.run, now: float | None = None) -> Path:
    """Replace a plain-copy install with a git checkout of ``tag``. The clone is
    staged and validated beside the plugin first; the old folder is kept as a
    hidden backup, the way ``omarchy plugin remove`` keeps one. Returns the backup."""
    if not TAG_RE.match(tag):
        raise UpdateError("not a release tag")
    if not safe_remote(info.origin):
        raise UpdateError("no usable git remote to install the release from")
    parent = info.plugin_dir.parent
    stage = parent / f".{PLUGIN_ID}.update.{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    try:
        _git(["clone", "--quiet", "--", info.origin, str(stage)], run=run)
        _git(["merge-base", "--is-ancestor", f"refs/tags/{tag}", "HEAD"], stage, run=run)
        _git(["reset", "--hard", "--quiet", f"refs/tags/{tag}"], stage, run=run)
        validate(stage, run=run)
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(time.time() if now is None else now))
        backup = parent / f".{PLUGIN_ID}.bak.{stamp}"
        n = 1
        while backup.exists():
            backup = parent / f".{PLUGIN_ID}.bak.{stamp}-{n}"
            n += 1
        os.rename(info.plugin_dir, backup)
        try:
            os.rename(stage, info.plugin_dir)
        except OSError:
            os.rename(backup, info.plugin_dir)       # put the working install back
            raise
    except OSError as exc:
        shutil.rmtree(stage, ignore_errors=True)
        raise UpdateError(f"could not replace the plugin folder: {exc}") from exc
    except UpdateError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return backup


def run_installer(plugin_dir: Path, *, install: Callable[[Path], int] | None = None) -> int:
    """Run the new release's own installer in place: it refreshes the service,
    the keybindings and the hooks, and restarts the shell."""
    if install is not None:
        return install(plugin_dir)
    installer = plugin_dir / "install.sh"
    # Inherit the terminal: the installer shows its progress and may ask about packages.
    return subprocess.run(["bash", str(installer), "--update"], cwd=str(plugin_dir), check=False).returncode
