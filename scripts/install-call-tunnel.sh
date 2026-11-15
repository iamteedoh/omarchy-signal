#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal call tunnel installer
## Date Created: 2026-11-14
## Description: Builds signal-call-tunnel (the helper signal-cli needs for voice calls) from source at a pinned commit and installs it for this user.
# Voice calls need signal-call-tunnel: a small Rust program around RingRTC,
# Signal's own calling library, that signal-cli starts once per call. Its
# author publishes no binaries and no package exists, so it is built here.
#
#   scripts/install-call-tunnel.sh            build and install
#   scripts/install-call-tunnel.sh --check    say whether it is installed, change nothing
#
# What it does: clones the source at the commit pinned below (never a moving
# branch), builds it with cargo (the build downloads Signal's prebuilt WebRTC,
# about 100 MB), and copies the one binary to
# ~/.local/share/omarchy-signal/bin, where the bridge looks for it. Nothing is
# installed system-wide and nothing needs root. Restart the bridge afterwards:
#   systemctl --user restart omarchy-signal
set -euo pipefail

REPO=https://github.com/visigoth/signal-call-tunnel.git
# The commit this plugin was tested against (2026-11-14). Move it on purpose, after reading what changed.
COMMIT=02c84e9bfbb45e6872956ac2168adcc0b1e7a8ba
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-signal/bin"
BIN="$DEST/signal-call-tunnel"

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

if [[ ${1:-} == --check ]]; then
  if [[ -x $BIN ]]; then echo "installed: $BIN"; exit 0; fi
  found=$(command -v signal-call-tunnel || true)
  if [[ -n $found ]]; then echo "installed: $found"; exit 0; fi
  echo "not installed"; exit 1
fi

command -v git >/dev/null || die "git is needed"
command -v pactl >/dev/null || die "pactl is needed (Omarchy ships it with PipeWire)"
# cargo from PATH, or through mise, which Omarchy ships. A mise shim can be on
# PATH with no Rust version behind it, so cargo has to actually answer.
if cargo --version >/dev/null 2>&1; then
  cargo=(cargo)
elif command -v mise >/dev/null; then
  cargo=(mise exec rust@stable -- cargo)
else
  die "Rust is needed to build it: install it with 'mise use -g rust' or 'omarchy pkg add rustup'"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
say "Fetching signal-call-tunnel at $COMMIT"
git clone --quiet "$REPO" "$work/src"
git -C "$work/src" checkout --quiet "$COMMIT"
# Only RingRTC is needed; the repository's other submodule is a signal-cli fork for its own tests.
git -C "$work/src" submodule update --init --depth 1 third-party/ringrtc

say "Building (the first build downloads Signal's WebRTC library, about 100 MB)"
(cd "$work/src/signal-call-tunnel" && "${cargo[@]}" build --release --locked)

built="$work/src/signal-call-tunnel/target/release/signal-call-tunnel"
[[ -x $built ]] || die "the build finished without producing a binary"
mkdir -p "$DEST"
install -m 0755 "$built" "$BIN"
say "Installed $BIN"
echo "    Restart the bridge to use it:  systemctl --user restart omarchy-signal"
