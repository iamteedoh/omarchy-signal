#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Omarchy post-update hook: after `omarchy update`, make sure the Signal plugin
# still loads and its service is still installed; tell the user if not.
# Installed to ~/.config/omarchy/hooks/post-update.d/ by install.sh.
#
# This hook only reports. It never enables, starts or restarts the bridge: the
# unit lives in ~/.config/systemd/user, which `omarchy update` does not rewrite,
# so a disabled or masked service is the user's own choice and must stay off.
PLUGIN_DIR="$HOME/.config/omarchy/plugins/iamteedoh.signal"
problems=()
ok_msg="plugin and service OK after update"

[[ -f $PLUGIN_DIR/manifest.json ]] || problems+=("plugin folder missing")
if command -v omarchy-plugin-validate >/dev/null && [[ -d $PLUGIN_DIR ]]; then
  omarchy-plugin-validate "$PLUGIN_DIR" >/dev/null 2>&1 || problems+=("manifest no longer validates")
fi
if [[ -x $PLUGIN_DIR/scripts/qml-check.sh ]]; then
  "$PLUGIN_DIR/scripts/qml-check.sh" >/dev/null 2>&1 || problems+=("Service.qml no longer loads with this Omarchy shell")
fi
state=$(systemctl --user is-enabled omarchy-signal.service 2>/dev/null)
case $state in
  enabled | enabled-runtime) ;;
  disabled | masked | masked-runtime)
    echo "omarchy-signal: bridge service is $state; leaving it off (to turn it back on: systemctl --user enable --now omarchy-signal)"
    ok_msg="plugin OK after update; bridge service left $state"
    ;;
  "" | not-found) problems+=("bridge service is not installed (re-run ./install.sh)") ;;
  *)
    echo "omarchy-signal: bridge service is $state; leaving it as it is"
    ok_msg="plugin OK after update; bridge service left $state"
    ;;
esac
if command -v signal-cli >/dev/null; then
  ver=$(signal-cli --version 2>/dev/null | awk '{print $2}')
  [[ -n $ver ]] && echo "omarchy-signal: signal-cli $ver"
else
  problems+=("signal-cli is not installed")
fi
if command -v jq >/dev/null && [[ -f $HOME/.config/omarchy/shell.json ]]; then
  jq -e 'tostring | test("iamteedoh.signal")' "$HOME/.config/omarchy/shell.json" >/dev/null 2>&1 || problems+=("bar widget no longer enabled in shell.json")
fi

if ((${#problems[@]})); then
  msg=$(IFS='; '; echo "${problems[*]}")
  echo "omarchy-signal: $msg" >&2
  command -v omarchy-notification-send >/dev/null && omarchy-notification-send --app-name Signal -g "󰭹" -u critical \
    "Signal plugin needs attention after the update" "$msg. Run: omarchy-signal doctor"
  exit 1
fi
echo "omarchy-signal: $ok_msg"
exit 0
