#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# End-to-end install -> uninstall against a sandbox HOME, with everything that
# would touch the real machine (systemd, hyprctl, the omarchy-* helpers) stubbed
# on PATH. Nothing outside the temporary directory is written.
#
# This exists because the installer edits two files it does not own. The unit
# tests cover the editing helper; this covers the installer actually calling it,
# which is where OMSIG-3 and OMSIG-4 would have been caught.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0
check() { if "$@" >/dev/null 2>&1; then echo "  ok   $*"; else echo "  FAIL $*"; fail=1; fi; }
note_fail() { echo "  FAIL $*"; fail=1; }

sandbox=$(mktemp -d)
trap 'rm -rf "$sandbox"' EXIT
stubs="$sandbox/stubs"
mkdir -p "$stubs" "$sandbox/home"

# The prerequisite tools are stubbed too, so the installer never reaches its
# package-manager branch. Without that this cannot run on a CI image.
for cmd in systemctl hyprctl omarchy-plugin-enable omarchy-plugin-disable \
           omarchy-plugin-validate omarchy-shell omarchy-restart-shell signal-cli \
           qrencode magick convert wl-copy wl-paste; do
  printf '#!/bin/sh\nexit 0\n' >"$stubs/$cmd"
  chmod +x "$stubs/$cmd"
done
# The installer asks whether the service is already running; say no, so it takes
# the enable-and-start path rather than restart.
printf '#!/bin/sh\ncase " $* " in *" is-active "*) exit 1 ;; esac\nexit 0\n' >"$stubs/systemctl"
chmod +x "$stubs/systemctl"

export HOME="$sandbox/home"
# No Omarchy shell sources here, so qml-check.sh reports NOT CHECKED and exits 0
# instead of starting Quickshell against the live Wayland session.
export OMARCHY_PATH="$sandbox/no-omarchy"
export PATH="$stubs:$PATH"

# A user's files, in the shapes that used to break.
mkdir -p "$HOME/.config/hypr" "$HOME/.config/omarchy/extensions"
BINDINGS="$HOME/.config/hypr/bindings.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
cat >"$BINDINGS" <<'LUA'
o.bind("SUPER + T", "Terminal", "alacritty")
o.bind("SUPER + B", "Browser", "chromium")
LUA
cat >"$MENU" <<'JSONC'
{
  // "personal": {"icon":"","label":"Personal"},
  "mine": {"icon":"","label":"Mine","action":"true"}
  // "about": {"icon":"","label":"About","action":"fastfetch"},
}
JSONC
cp "$BINDINGS" "$sandbox/bindings.orig"
cp "$MENU" "$sandbox/menu.orig"

echo "install"
if ! "$ROOT/install.sh" >"$sandbox/install.log" 2>&1; then
  note_fail "install.sh exited non-zero"
  sed 's/^/    /' "$sandbox/install.log" | tail -20
fi
check test -d "$HOME/.config/omarchy/plugins/iamteedoh.signal"
check test -L "$HOME/.local/bin/omarchy-signal"
check test -f "$HOME/.config/omarchy-signal/config.toml"

echo "the user's own bindings survive"
check grep -q 'SUPER + T' "$BINDINGS"
check grep -q 'SUPER + B' "$BINDINGS"
check grep -q 'BEGIN omarchy-signal' "$BINDINGS"
check grep -q 'END omarchy-signal' "$BINDINGS"

echo "the menu still parses"
check env PYTHONPATH="$ROOT/lib" python3 "$ROOT/tests/bash/parse_menu.py" "$MENU" signal-tui mine

echo "installing twice changes nothing"
cp "$BINDINGS" "$sandbox/bindings.after1"
cp "$MENU" "$sandbox/menu.after1"
"$ROOT/install.sh" >>"$sandbox/install.log" 2>&1
check cmp -s "$BINDINGS" "$sandbox/bindings.after1"
check cmp -s "$MENU" "$sandbox/menu.after1"

echo "uninstall restores both files exactly"
if ! "$HOME/.config/omarchy/plugins/iamteedoh.signal/uninstall.sh" >"$sandbox/uninstall.log" 2>&1; then
  note_fail "uninstall.sh exited non-zero"
  sed 's/^/    /' "$sandbox/uninstall.log" | tail -20
fi
check cmp -s "$BINDINGS" "$sandbox/bindings.orig"
check cmp -s "$MENU" "$sandbox/menu.orig"
check test ! -e "$HOME/.config/omarchy/plugins/iamteedoh.signal"

echo "a bindings.lua with a lost END marker is never truncated"
# OMSIG-3: the sed range this replaces deleted from BEGIN to end of file.
cat >"$BINDINGS" <<'LUA'
o.bind("SUPER + T", "Terminal", "alacritty")
-- BEGIN omarchy-signal
hl.unbind("SUPER + SHIFT + G")
o.bind("SUPER + B", "Browser", "chromium")
LUA
cp "$BINDINGS" "$sandbox/bindings.broken"
"$ROOT/install.sh" >"$sandbox/install2.log" 2>&1
check cmp -s "$BINDINGS" "$sandbox/bindings.broken"
check grep -q 'SUPER + B' "$BINDINGS"
if grep -q "Fix the omarchy-signal markers" "$sandbox/install2.log"; then
  echo "  ok   the installer says why it left the file alone"
else
  note_fail "the installer did not report the unbalanced markers"
fi

exit $fail
