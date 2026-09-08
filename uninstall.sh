#!/bin/bash
# Remove omarchy-signal. Keeps signal-cli's account data and your message
# history unless --purge is given.
set -euo pipefail
PLUGIN_ID="iamteedoh.signal"
PURGE=0
[[ ${1:-} == --purge ]] && PURGE=1

systemctl --user disable --now omarchy-signal.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/omarchy-signal.service"
systemctl --user daemon-reload || true
command -v omarchy-plugin-disable >/dev/null && omarchy-plugin-disable "$PLUGIN_ID" 2>/dev/null || true
rm -rf "$HOME/.config/omarchy/plugins/$PLUGIN_ID"
rm -f "$HOME/.local/bin/omarchy-signal"
BINDINGS="$HOME/.config/hypr/bindings.lua"
if [[ -f $BINDINGS ]]; then
  sed -i '/^-- BEGIN omarchy-signal$/,/^-- END omarchy-signal$/d' "$BINDINGS"
  hyprctl reload >/dev/null 2>&1 || true
fi
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
[[ -f $MENU ]] && sed -i '/"signal-tui"/d' "$MENU"
command -v omarchy-shell >/dev/null && omarchy-shell -q shell rescanPlugins >/dev/null 2>&1 || true
if (( PURGE )); then
  rm -rf "$HOME/.local/share/omarchy-signal" "$HOME/.local/state/omarchy-signal" "$HOME/.config/omarchy-signal"
  echo "Removed omarchy-signal and its message history. signal-cli's account data in ~/.local/share/signal-cli was left alone;"
  echo "unlink the device from your phone (Settings → Linked devices) and delete that directory if you no longer want it."
else
  echo "Removed omarchy-signal. History kept in ~/.local/share/omarchy-signal (pass --purge to delete)."
fi
