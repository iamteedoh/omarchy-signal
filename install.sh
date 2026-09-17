#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
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

# --- progress ------------------------------------------------------------------
# Every step prints a numbered header, and anything that can take more than a
# moment runs under a spinner with the elapsed time, so the installer never
# sits silent. Without a terminal (a log, CI) the spinner becomes plain lines.
TOTAL_STEPS=9
STEP=0
STARTED=$SECONDS
TTY=0; [[ -t 1 ]] && TTY=1
if (( TTY )); then
  C_STEP=$'\033[1;36m' C_BOLD=$'\033[1m' C_OK=$'\033[1;32m' C_WARN=$'\033[1;33m' C_ERR=$'\033[1;31m' C_DIM=$'\033[2m' C_SPIN=$'\033[36m' C_OFF=$'\033[0m'
else
  C_STEP="" C_BOLD="" C_OK="" C_WARN="" C_ERR="" C_DIM="" C_SPIN="" C_OFF=""
fi

step() {
  STEP=$((STEP + 1))
  printf '\n%s[%d/%d]%s %s%s%s\n' "$C_STEP" "$STEP" "$TOTAL_STEPS" "$C_OFF" "$C_BOLD" "$*" "$C_OFF"
}
say() { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
note() { printf '  %s- %s%s\n' "$C_DIM" "$*" "$C_OFF"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }

# run LABEL CMD...: run a non-interactive command behind a spinner. Its output
# is shown only if it fails; the exit status is passed through.
run() {
  local label=$1; shift
  local log; log=$(mktemp)
  local t0=$SECONDS status=0
  if (( TTY )); then
    "$@" >"$log" 2>&1 &
    local pid=$! i=0 frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
    # Keep one frame on one row: "  x label (NNNs)" is the label plus about 14
    # columns of chrome. A wrapped line makes \r jump to the wrapped row and
    # \033[K clear only that row, which smears frames across the screen.
    local cols=${COLUMNS:-0} spin_label=$label
    (( cols > 0 )) || cols=$(tput cols 2>/dev/null || echo 80)
    (( ${#spin_label} > cols - 14 )) && spin_label="${spin_label:0:cols-17}..."
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s%s%s %s %s(%ds)%s\033[K' "$C_SPIN" "${frames[i++ % ${#frames[@]}]}" "$C_OFF" "$spin_label" "$C_DIM" $((SECONDS - t0)) "$C_OFF"
      sleep 0.1
    done
    wait "$pid" || status=$?
    printf '\r\033[K'
  else
    printf '  - %s...\n' "$label"
    "$@" >"$log" 2>&1 || status=$?
  fi
  if (( status == 0 )); then
    say "$label ($((SECONDS - t0))s)"
    # The command's own last word, e.g. "qml-check: no Wayland display, skipping".
    local last; last=$(grep -v '^[[:space:]]*$' "$log" | tail -n 1 || true)
    [[ -n $last ]] && note "$last"
  else
    printf '  %s✗%s %s (exit %d)\n' "$C_ERR" "$C_OFF" "$label" "$status" >&2
    sed 's/^/    /' "$log" >&2
  fi
  rm -f "$log"
  return "$status"
}

# --- prerequisites -----------------------------------------------------------
step "Checking prerequisites"
missing=()
command -v python3 >/dev/null || missing+=(python)
command -v qrencode >/dev/null || missing+=(qrencode)
command -v magick >/dev/null || command -v convert >/dev/null || missing+=(imagemagick)
command -v wl-copy >/dev/null && command -v wl-paste >/dev/null || missing+=(wl-clipboard)
if ((${#missing[@]})); then
  # Not behind the spinner: the package manager may ask for a password.
  note "Installing packages: ${missing[*]} (the package manager shows its own progress)"
  if command -v omarchy-pkg-add >/dev/null; then omarchy-pkg-add "${missing[@]}"; else sudo pacman -S --needed --noconfirm "${missing[@]}"; fi
  say "Packages installed: ${missing[*]}"
else
  say "python3, qrencode, ImageMagick and wl-clipboard are present"
fi
if command -v signal-cli >/dev/null; then
  say "signal-cli is present"
else
  warn "signal-cli is not installed. It is the component that speaks the Signal protocol."
  echo "  Install one of (AUR):"
  echo "    omarchy pkg aur add signal-cli-native-bin   # prebuilt native binary, fastest to install"
  echo "    omarchy pkg aur add signal-cli              # Java build (needs a JRE)"
  echo "  The native build is a ~340 MB download and usually takes a few minutes."
  install_signal_cli() {
    # omarchy-pkg-aur-add wraps `yay -S --noconfirm --needed` and re-checks with
    # pacman afterwards, because yay can exit 0 having installed nothing. Going
    # through it also spares the user yay's cleanBuild / diff / PGP-import /
    # proceed prompts, which have nothing to do with this plugin.
    if command -v omarchy-pkg-aur-add >/dev/null; then
      omarchy-pkg-aur-add signal-cli-native-bin
    else
      yay -S --noconfirm --needed signal-cli-native-bin
    fi
  }
  if [[ -t 0 && -t 1 ]] && { command -v omarchy-pkg-aur-add >/dev/null || command -v yay >/dev/null; }; then
    read -r -p "  Install signal-cli-native-bin now? [y/N] " ans
    if [[ ${ans,,} == y ]]; then
      install_signal_cli || warn "The package manager reported a failure."
      # Never advance into the rest of the install on a half-finished package:
      # every later step assumes signal-cli exists.
      if command -v signal-cli >/dev/null; then
        say "signal-cli installed ($(signal-cli --version 2>/dev/null || echo present))"
      else
        warn "signal-cli is still not on PATH; nothing further was installed."
        echo "  Install it, then run this script again:"
        echo "    omarchy pkg aur add signal-cli-native-bin"
        exit 1
      fi
    else
      warn "Continuing without signal-cli; the bridge cannot start until it is installed."
    fi
  else
    # No TTY (or no AUR helper): say so plainly instead of continuing as though
    # the requirement were met.
    warn "Cannot install it here (no terminal to ask in, or no AUR helper)."
    echo "  Install it, then run this script again:"
    echo "    omarchy pkg aur add signal-cli-native-bin"
    exit 1
  fi
fi

# --- preflight -----------------------------------------------------------------
# Refuse to deploy QML the shell cannot load: a broken Service.qml means no
# popups and no chat window until the next install.
step "Checking that the shell can load the plugin"
run "Loading the plugin's QML in a test shell" "$HERE/scripts/qml-check.sh" || { warn "QML check failed; nothing was installed"; exit 1; }

# --- plugin --------------------------------------------------------------------
step "Installing the plugin files"
mkdir -p "$PLUGINS_DIR" "$BIN_DIR" "$UNIT_DIR" "$CONFIG_DIR"
# Directories the hardened service unit may write to; the unit tolerates their
# absence but the bridge wants them owner-only from the first run.
for d in "$HOME/.local/share/omarchy-signal" "$HOME/.local/state/omarchy-signal" "$HOME/.local/share/signal-cli"; do
  mkdir -p "$d" && chmod 700 "$d"
done
if [[ -e $PLUGIN_DIR && $(realpath -m "$PLUGIN_DIR") == $(realpath -m "$HERE") ]]; then
  # Running from the installed plugin folder itself (e.g. after
  # `omarchy plugin add`): nothing to copy, and certainly nothing to delete.
  say "Installing in place from $PLUGIN_DIR"
elif (( LINK_MODE )); then
  if [[ -L $PLUGIN_DIR ]]; then rm -f "$PLUGIN_DIR"; elif [[ -d $PLUGIN_DIR ]]; then rm -rf "$PLUGIN_DIR"; fi
  ln -s "$HERE" "$PLUGIN_DIR"
  say "Linked $PLUGIN_DIR → $HERE (dev mode)"
else
  if [[ -L $PLUGIN_DIR ]]; then rm -f "$PLUGIN_DIR"; elif [[ -d $PLUGIN_DIR ]]; then rm -rf "$PLUGIN_DIR"; fi
  mkdir -p "$PLUGIN_DIR"
  # Everything the shell, the CLI and a later ./install.sh or ./uninstall.sh from
  # inside the plugin folder need; no tests, no .git.
  cp -a "$HERE"/manifest.json "$HERE"/*.qml "$HERE"/*.js "$HERE/bin" "$HERE/lib" "$HERE/scripts" "$HERE/systemd" "$HERE/docs" \
        "$HERE/install.sh" "$HERE/uninstall.sh" "$HERE/README.md" "$HERE/SECURITY.md" "$HERE/LICENSE" "$PLUGIN_DIR/"
  find "$PLUGIN_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  say "Installed plugin to $PLUGIN_DIR"
fi
if command -v omarchy-plugin-validate >/dev/null; then
  run "Validating the plugin manifest" omarchy-plugin-validate "$HERE" || { warn "plugin validation failed"; exit 1; }
fi

# --- CLI ---------------------------------------------------------------------------
step "Linking the command and writing the config"
ln -sf "$PLUGIN_DIR/bin/omarchy-signal" "$BIN_DIR/omarchy-signal"
say "Linked $BIN_DIR/omarchy-signal"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on PATH in this shell (Omarchy adds it at login)";; esac

# --- config ------------------------------------------------------------------------
if [[ ! -f $CONFIG_DIR/config.toml ]]; then
  cp "$HERE/docs/config.example.toml" "$CONFIG_DIR/config.toml"
  chmod 600 "$CONFIG_DIR/config.toml"
  say "Wrote default config to $CONFIG_DIR/config.toml"
else
  say "Keeping your config at $CONFIG_DIR/config.toml"
fi

# --- systemd -------------------------------------------------------------------------
step "Setting up the background service"
install -m 644 "$HERE/systemd/omarchy-signal.service" "$UNIT_DIR/omarchy-signal.service"
run "Reloading systemd" systemctl --user daemon-reload
if command -v signal-cli >/dev/null; then
  if systemctl --user is-active --quiet omarchy-signal.service; then
    # A running bridge keeps executing the code it started with; hand it the new one.
    run "Restarting the bridge service with the new code" systemctl --user restart omarchy-signal.service
  else
    run "Enabling and starting the bridge service" systemctl --user enable --now omarchy-signal.service
  fi
else
  run "Enabling the bridge service" systemctl --user enable omarchy-signal.service
  warn "Bridge service enabled but not started (install signal-cli first, then: systemctl --user start omarchy-signal)"
fi

# --- keybinding ----------------------------------------------------------------------
step "Keybindings"
if (( DO_BIND )); then
  # The block is rewritten on every install, so an update also updates the
  # bindings. Everything between the markers belongs to this plugin.
  body=$(cat <<'LUA'
-- SUPER+SHIFT+G is Omarchy's Signal key; point it at the terminal client.
hl.unbind("SUPER + SHIFT + G")
o.bind("SUPER + SHIFT + G", "Signal", "omarchy-signal open")
-- New conversation from anywhere: the bar widget's searchable contact list.
o.bind("SUPER + CTRL + G", "Signal: new conversation", "omarchy-shell iamteedoh.signal.bar toggle")
-- Detached conversation windows float themselves (720x640, centred) when they
-- open; SUPER+ALT+S moves one to the scratchpad, SUPER+S brings it back.
-- The popup conversation and the linking QR code are layer surfaces, not
-- windows, so SUPER+W closes them itself while one is on screen, and closes
-- the active window as usual otherwise.
local signal_layers = { ["omarchy-signal-reply"] = { open = 0, ipc = "close" }, ["omarchy-signal-qr"] = { open = 0, ipc = "hideQr" } }
hl.on("layer.opened", function(layer)
  local l = signal_layers[layer.namespace]
  if l then l.open = l.open + 1 end
end)
hl.on("layer.closed", function(layer)
  local l = signal_layers[layer.namespace]
  if l and l.open > 0 then l.open = l.open - 1 end
end)
hl.unbind("SUPER + W")
o.bind("SUPER + W", "Close window", function()
  for _, l in pairs(signal_layers) do
    if l.open > 0 then
      hl.dispatch(hl.dsp.exec_cmd("omarchy-shell iamteedoh.signal " .. l.ipc))
      return
    end
  end
  hl.dispatch(hl.dsp.window.close())
end)
LUA
)
  # Markers come from MARK_BEGIN/MARK_END so the block that is written and the
  # ranges that match it can never drift apart.
  block=$(printf '%s\n%s\n%s' "$MARK_BEGIN" "$body" "$MARK_END")
  mkdir -p "$(dirname "$BINDINGS")"
  touch "$BINDINGS"
  before=$(sed -n "/^$MARK_BEGIN\$/,/^$MARK_END\$/p" "$BINDINGS")
  if [[ $before == "$block" ]]; then
    say "Keybinding block up to date in $BINDINGS"
  else
    sed -i "/^$MARK_BEGIN\$/,/^$MARK_END\$/d" "$BINDINGS"
    printf '%s\n' "$block" >> "$BINDINGS"
    say "Wrote keybindings to $BINDINGS (SUPER+SHIFT+G, SUPER+CTRL+G, SUPER+W closes the popup)"
    run "Reloading Hyprland" hyprctl reload || true
  fi
else
  note "Skipped (--no-bind)"
fi

# --- menu entry ----------------------------------------------------------------------
step "Omarchy menu entry"
if (( ! DO_MENU )); then
  note "Skipped (--no-menu)"
elif [[ ! -f $MENU ]]; then
  note "Skipped: no menu extensions file at $MENU"
else
  if grep -q '"signal-tui"' "$MENU"; then
    say "Menu entry already present"
  else
    python3 - "$MENU" <<'PY'
import re, sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
entry = '  "signal-tui": {"icon":"󰭹","label":"Signal (terminal)","action":"omarchy-signal open"},\n'
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

# --- post-update hook -------------------------------------------------------------------
# After `omarchy update`, check that the plugin still loads and the service is
# enabled, and notify if not.
step "Post-update check"
HOOK_DIR="$HOME/.config/omarchy/hooks/post-update.d"
mkdir -p "$HOOK_DIR"
install -m 755 "$HERE/scripts/post-update-hook.sh" "$HOOK_DIR/omarchy-signal"
say "Post-update check installed ($HOOK_DIR/omarchy-signal)"

# --- shell ---------------------------------------------------------------------------
step "Loading the plugin into the Omarchy shell"
enable_plugin() {
  omarchy-plugin-enable "$PLUGIN_ID" --section right || omarchy-plugin-enable "$PLUGIN_ID"
}
if command -v omarchy-shell >/dev/null; then
  # The shell hot-reloads bar widgets and panels on file changes but keeps a
  # running service (Service.qml) as it is, so a restart is the only way to
  # pick up service changes. It comes back within a second or two.
  run "Enabling the plugin" enable_plugin || warn "Could not enable the plugin; enable it from the shell's plugin settings"
  if command -v omarchy-restart-shell >/dev/null; then
    run "Restarting the shell so the notification service picks up the new code" omarchy-restart-shell \
      || warn "Shell restart failed; run: omarchy restart shell"
  else
    run "Rescanning plugins" omarchy-shell -q shell rescanPlugins || true
    note "Run 'omarchy restart shell' to reload the notification service"
  fi
else
  note "Skipped: omarchy-shell not found"
fi

# Say "Done" only about a service that is actually running.
if systemctl --user is-active --quiet omarchy-signal.service; then
  say "Bridge service is running"
else
  warn "The bridge service is not running. Check: journalctl --user -u omarchy-signal -n 50"
fi

printf '\n%sDone%s in %ds.' "$C_OK" "$C_OFF" $((SECONDS - STARTED))
cat <<DONE
 Next steps:
  1. Link this computer to your Signal account:   omarchy-signal link
  2. Open the client:                             omarchy-signal tui   (or SUPER+SHIFT+G)
  3. Check everything:                            omarchy-signal doctor

Config: $CONFIG_DIR/config.toml   Logs: journalctl --user -u omarchy-signal
DONE

# Offer the remaining step here rather than leaving it as homework: the QR code
# appears in this same window, which is already open and already trusted.
if [[ -t 0 && -t 1 ]] && command -v omarchy-signal >/dev/null; then
  # Anchored and case-sensitive on purpose: the unlinked line reads
  # "account     : NOT LINKED, run `omarchy-signal link`", which a loose
  # match for "linked" would read as success.
  if omarchy-signal status 2>/dev/null | grep -qE '^account[[:space:]]*:[[:space:]]*linked[[:space:]]*$'; then
    say "This computer is already linked"
  else
    printf '\n'
    read -r -p "  Link this computer to your Signal account now? [Y/n] " link_ans
    if [[ -z $link_ans || ${link_ans,,} == y ]]; then
      omarchy-signal link || warn "Linking did not complete; run it again with: omarchy-signal link"
    else
      note "Link later with: omarchy-signal link"
    fi
  fi
fi
