#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Remove omarchy-signal. Keeps signal-cli's account data and your message
# history unless --purge is given.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="iamteedoh.signal"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
BINDINGS="$HOME/.config/hypr/bindings.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
PURGE=0
[[ ${1:-} == --purge ]] && PURGE=1

# Same helper the installer writes with, so what is removed is exactly what was
# added -- markers and menu entry included. It has to run before the plugin
# directory goes, because that is where it lives.
confedit() { PYTHONPATH="$HERE/lib" python3 -m omarchy_signal.confedit "$@"; }

if [[ -f $BINDINGS ]]; then
  # Refuses to act on an unbalanced marker pair instead of deleting from the
  # opening marker to end of file, which is what the old sed range did to a
  # hand-edited bindings.lua.
  if confedit block-remove --file "$BINDINGS" >/dev/null; then
    hyprctl reload >/dev/null 2>&1 || true
  else
    echo "Left $BINDINGS alone; remove the omarchy-signal block by hand." >&2
  fi
fi
if [[ -f $MENU ]]; then confedit menu-remove --file "$MENU" >/dev/null; fi

systemctl --user disable --now omarchy-signal.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/omarchy-signal.service"
systemctl --user daemon-reload || true
command -v omarchy-plugin-disable >/dev/null && omarchy-plugin-disable "$PLUGIN_ID" 2>/dev/null || true
rm -rf "$PLUGIN_DIR"
rm -f "$HOME/.local/bin/omarchy-signal"
rm -f "$HOME/.config/omarchy/hooks/post-update.d/omarchy-signal"
command -v omarchy-shell >/dev/null && omarchy-shell -q shell rescanPlugins >/dev/null 2>&1 || true
if (( PURGE )); then
  rm -rf "$HOME/.local/share/omarchy-signal" "$HOME/.local/state/omarchy-signal" "$HOME/.config/omarchy-signal"
  echo "Removed omarchy-signal and its message history. signal-cli's account data in ~/.local/share/signal-cli was left alone;"
  echo "unlink the device from your phone (Settings → Linked devices) and delete that directory if you no longer want it."
else
  echo "Removed omarchy-signal. History kept in ~/.local/share/omarchy-signal (pass --purge to delete)."
fi
