#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal demo screenshots
## Date Created: 2026-11-12
## Description: Renders the plugin's popups and chat window offscreen against the demo bridge and saves them as PNGs.
# Screenshots of the plugin's own windows, made without a screen.
#
# The components are rendered by Quickshell under Qt's offscreen platform and
# grabbed straight from the scene graph, so the desktop is never captured: no
# wallpaper, no bar, no other window can be in a picture. Everything they can
# read comes from scripts/demo-env.sh's throwaway HOME (fictional contacts, the
# test suite's fake signal-cli, pictures drawn on the spot).
#
#   scripts/demo-shots.sh [output-dir]                 (default docs/media)
#   DEMO_THEMES="tokyo-night catppuccin-latte" scripts/demo-shots.sh
#
# The first theme's pictures are saved under their plain names; the others get
# the theme name as a prefix, to show the plugin following the active theme.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mkdir -p "${1:-$HERE/docs/media}" && cd "${1:-$HERE/docs/media}" && pwd)"
# The first theme is the one the README leads with. lumon-dark is a user theme;
# where it is not installed the stock lumon stands in.
LEAD=lumon-dark
[[ -f ${OMARCHY_PATH:-/usr/share/omarchy}/themes/$LEAD/colors.toml || -f $HOME/.config/omarchy/themes/$LEAD/colors.toml ]] || LEAD=lumon
THEMES=${DEMO_THEMES:-$LEAD catppuccin-latte gruvbox}
SHELL_SRC="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
REAL_HOME="$HOME"
command -v qs >/dev/null || { echo "demo-shots: Quickshell (qs) not found" >&2; exit 1; }
[[ -d $SHELL_SRC/Commons && -d $SHELL_SRC/Ui ]] || { echo "demo-shots: Omarchy shell modules not found" >&2; exit 1; }

work=$(mktemp -d)
export OMARCHY_SIGNAL_DEMO_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-signal-shots"
cleanup() {
  HOME="$REAL_HOME" "$HERE/scripts/demo-env.sh" --stop >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# The plugin as the shell would load it, next to the Omarchy modules it imports.
ln -s "$SHELL_SRC/Commons" "$work/Commons"
ln -s "$SHELL_SRC/Ui" "$work/Ui"
cp "$HERE"/*.qml "$HERE"/*.js "$work/"
cp "$HERE/scripts/demo/Shots.qml" "$work/shell.qml"

first=1
status=0
for theme in $THEMES; do
  # Each theme gets a fresh demo: the bridge, the HOME and the colours.
  env_file="$work/env.$theme"
  if ! HOME="$REAL_HOME" DEMO_THEME="$theme" "$HERE/scripts/demo-env.sh" >"$env_file" 2>"$work/env.log"; then
    echo "demo-shots: the demo environment did not start for $theme" >&2; sed 's/^/  /' "$work/env.log" >&2; status=1; continue
  fi
  prefix=""; (( first )) || prefix="$theme-"
  (
    # shellcheck disable=SC1090
    source "$env_file"
    # The guard this whole script exists for: never render with the real home.
    if [[ $HOME == "$REAL_HOME" || $HOME != "$OMARCHY_SIGNAL_DEMO_DIR"/* ]]; then
      echo "demo-shots: refusing to render with HOME=$HOME" >&2; exit 1
    fi
    export SHOTS_OUT="$OUT" SHOTS_CLI="$HERE/bin/omarchy-signal" SHOTS_PREFIX="$prefix"
    export QT_QPA_PLATFORM=offscreen
    cd "$work" && timeout 90 qs -p "$work/shell.qml" >"$work/qs.$theme.log" 2>&1
  )
  if grep -q "SHOTS DONE" "$work/qs.$theme.log"; then
    grep -o "SHOT saved .*" "$work/qs.$theme.log" | sed "s|$OUT/||; s/^/  /"
    # Anything the components complained about while drawing is worth seeing.
    grep -E "WARN.*(qml|scene)|ReferenceError|TypeError|Unable to assign|is not defined" "$work/qs.$theme.log" \
      | grep -v -E "portal|window masks|toplevel-mapping|QT_QPA_PLATFORM" | sed 's/^/  ! /' | sort -u | head -20
  else
    echo "demo-shots: rendering failed for $theme" >&2
    grep -v portal "$work/qs.$theme.log" | tail -15 | sed 's/^/  /' >&2
    status=1
  fi
  first=0
done
# When writing into the repository: the hero picture lives at the root (the
# marketplace reads preview.png there), and a theme's pictures are kept only
# where the README shows them.
if [[ $OUT == "$HERE/docs/media" && $status == 0 ]]; then
  mv "$OUT/preview.png" "$HERE/preview.png"
  for f in "$OUT"/*.png; do
    grep -qF "docs/media/$(basename "$f")" "$HERE/README.md" || rm -f "$f"
  done
fi
exit $status
