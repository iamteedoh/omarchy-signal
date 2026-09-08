#!/bin/bash
# Install omarchy-signal on this machine.
#
#   ./install.sh            # copy the plugin into ~/.config/omarchy/plugins, link the CLI, enable the service
#   ./install.sh --link     # developer mode: symlink the plugin dir to this checkout (every save reloads ALL
#                           # user plugins in the shell; see docs/STATUS.md before using it)
#   ./install.sh --no-bind  # skip the SUPER+SHIFT+G keybinding
#   ./install.sh --no-menu  # skip the Omarchy menu entry
#
# Everything it touches is under $HOME. Re-running is safe.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ID="iamteedoh.signal"
PLUGINS_DIR="$HOME/.config/omarchy/plugins"
PLUGIN_DIR="$PLUGINS_DIR/$PLUGIN_ID"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
BINDINGS="$HOME/.config/hypr/bindings.lua"
MENU="$HOME/.config/omarchy/extensions/omarchy-menu.jsonc"
CONFIG_DIR="$HOME/.config/omarchy-signal"
MARK_BEGIN="-- BEGIN omarchy-signal"
MARK_END="-- END omarchy-signal"

LINK_MODE=0; DO_BIND=1; DO_MENU=1
for arg in "$@"; do
  case "$arg" in
    --link) LINK_MODE=1 ;;
    --no-bind) DO_BIND=0 ;;
    --no-menu) DO_MENU=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }

# --- prerequisites -----------------------------------------------------------
missing=()
command -v python3 >/dev/null || missing+=(python)
command -v qrencode >/dev/null || missing+=(qrencode)
command -v magick >/dev/null || command -v convert >/dev/null || missing+=(imagemagick)
if ((${#missing[@]})); then
  say "Installing packages: ${missing[*]}"
  if command -v omarchy-pkg-add >/dev/null; then omarchy-pkg-add "${missing[@]}"; else sudo pacman -S --needed --noconfirm "${missing[@]}"; fi
fi
if ! command -v signal-cli >/dev/null; then
  warn "signal-cli is not installed. It is the component that speaks the Signal protocol."
  echo "  Install one of (AUR):"
  echo "    omarchy pkg aur add signal-cli-native-bin   # prebuilt native binary, fastest to install"
  echo "    omarchy pkg aur add signal-cli              # Java build (needs a JRE)"
  if [[ -t 0 && -t 1 ]] && command -v yay >/dev/null; then
    read -r -p "  Install signal-cli-native-bin now with yay? [y/N] " ans
    if [[ ${ans,,} == y ]]; then yay -S --needed signal-cli-native-bin; fi
  fi
fi

# --- plugin --------------------------------------------------------------------
mkdir -p "$PLUGINS_DIR" "$BIN_DIR" "$UNIT_DIR" "$CONFIG_DIR"
if [[ -L $PLUGIN_DIR || -d $PLUGIN_DIR ]]; then
  if [[ -L $PLUGIN_DIR ]]; then rm -f "$PLUGIN_DIR"; else rm -rf "$PLUGIN_DIR"; fi
fi
if (( LINK_MODE )); then
  ln -s "$HERE" "$PLUGIN_DIR"
  say "Linked $PLUGIN_DIR → $HERE (dev mode)"
else
  mkdir -p "$PLUGIN_DIR"
  # Only what the shell and the CLI need; no tests, no .git.
  cp -a "$HERE/manifest.json" "$HERE/Model.js" "$HERE/Service.qml" "$HERE/BarWidget.qml" "$HERE/bin" "$HERE/lib" "$HERE/README.md" "$HERE/LICENSE" "$PLUGIN_DIR/"
  find "$PLUGIN_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  say "Installed plugin to $PLUGIN_DIR"
fi
if command -v omarchy-plugin-validate >/dev/null; then
  omarchy-plugin-validate "$HERE" || { warn "plugin validation failed"; exit 1; }
fi

# --- CLI ---------------------------------------------------------------------------
ln -sf "$PLUGIN_DIR/bin/omarchy-signal" "$BIN_DIR/omarchy-signal"
say "Linked $BIN_DIR/omarchy-signal"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on PATH in this shell (Omarchy adds it at login)";; esac

# --- config ------------------------------------------------------------------------
if [[ ! -f $CONFIG_DIR/config.toml ]]; then
  cp "$HERE/docs/config.example.toml" "$CONFIG_DIR/config.toml"
  chmod 600 "$CONFIG_DIR/config.toml"
  say "Wrote default config to $CONFIG_DIR/config.toml"
fi

# --- systemd -------------------------------------------------------------------------
install -m 644 "$HERE/systemd/omarchy-signal.service" "$UNIT_DIR/omarchy-signal.service"
systemctl --user daemon-reload
if command -v signal-cli >/dev/null; then
  systemctl --user enable --now omarchy-signal.service
  say "Bridge service enabled and started"
else
  systemctl --user enable omarchy-signal.service
  warn "Bridge service enabled but not started (install signal-cli first, then: systemctl --user start omarchy-signal)"
fi

# --- keybinding ----------------------------------------------------------------------
if (( DO_BIND )); then
  if [[ -f $BINDINGS ]] && grep -qF -- "$MARK_BEGIN" "$BINDINGS"; then
    say "Keybinding block already present in $BINDINGS"
  else
    mkdir -p "$(dirname "$BINDINGS")"
    cat >> "$BINDINGS" <<'LUA'
-- BEGIN omarchy-signal
-- SUPER+SHIFT+G is Omarchy's Signal key; point it at the terminal client.
hl.unbind("SUPER + SHIFT + G")
o.bind("SUPER + SHIFT + G", "Signal", "omarchy-launch-or-focus-tui --app-id=org.omarchy.signal omarchy-signal tui")
-- END omarchy-signal
LUA
    say "Added SUPER+SHIFT+G → omarchy-signal tui to $BINDINGS"
    hyprctl reload >/dev/null 2>&1 || true
  fi
fi

# --- menu entry ----------------------------------------------------------------------
if (( DO_MENU )) && [[ -f $MENU ]]; then
  if grep -q '"signal-tui"' "$MENU"; then
    say "Menu entry already present"
  else
    python3 - "$MENU" <<'PY'
import re, sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
entry = '  "signal-tui": {"icon":"󰭹","label":"Signal (terminal)","action":"omarchy-launch-or-focus-tui --app-id=org.omarchy.signal omarchy-signal tui"},\n'
# Insert before the final closing brace of the top-level object.
idx = src.rstrip().rfind("}")
if idx < 0:
    src = "{\n" + entry + "}\n"
else:
    head = src[:idx].rstrip()
    if not head.endswith("{") and not head.endswith(","):
        head += ","
    src = head + "\n" + entry + "}\n"
open(path, "w", encoding="utf-8").write(src)
PY
    say "Added 'Signal (terminal)' to the Omarchy menu"
  fi
fi

# --- shell ---------------------------------------------------------------------------
if command -v omarchy-shell >/dev/null; then
  # The shell hot-reloads bar widgets and panels on file changes but keeps a
  # running service (Service.qml) as it is, so a restart is the only way to
  # pick up service changes. It comes back within a second or two.
  omarchy-plugin-enable "$PLUGIN_ID" --section right >/dev/null 2>&1 || omarchy-plugin-enable "$PLUGIN_ID" >/dev/null 2>&1 || true
  if command -v omarchy-restart-shell >/dev/null; then
    omarchy-restart-shell >/dev/null 2>&1 || true
    say "Plugin enabled; shell restarted so the notification service picks up the new code"
  else
    omarchy-shell -q shell rescanPlugins >/dev/null 2>&1 || true
    say "Plugin enabled in the shell (run 'omarchy restart shell' to reload the notification service)"
  fi
fi

cat <<DONE

Done. Next steps:
  1. Link this computer to your Signal account:   omarchy-signal link
  2. Open the client:                             omarchy-signal tui   (or SUPER+SHIFT+G)
  3. Check everything:                            omarchy-signal doctor

Config: $CONFIG_DIR/config.toml   Logs: journalctl --user -u omarchy-signal
DONE
