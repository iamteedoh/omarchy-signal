#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Set up omarchy-signal on this machine.
#
# The usual way in is Omarchy's own:  omarchy plugin add https://github.com/iamteedoh/omarchy-signal --enable
# The shell then offers to run this script from the plugin folder, where it
# finishes the job in place: the command, the bridge service, the keybindings.
#
#   ./install.sh            # from the plugin folder: set up in place. From another checkout: copy it in first
#   ./install.sh --link     # developer mode: symlink the plugin dir to this checkout (every save reloads ALL
#                           # user plugins in the shell; see docs/STATUS.md before using it)
#   ./install.sh --no-bind  # skip the SUPER+SHIFT+G keybinding
#   ./install.sh --no-menu  # skip the Omarchy menu entry
#   ./install.sh --update   # what `omarchy-signal update` runs: the same steps, without the questions at the end
#
# Everything it touches is under $HOME. Re-running is safe. Update later with: omarchy-signal update

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
# bindings.lua and omarchy-menu.jsonc belong to the user, so both are edited
# through one helper that models the format instead of matching raw text. It
# owns the marker strings and the menu entry too, so nothing has to be kept in
# step by hand. See lib/omarchy_signal/confedit.py.
confedit() { PYTHONPATH="$HERE/lib" python3 -m omarchy_signal.confedit "$@"; }

LINK_MODE=0; DO_BIND=1; DO_MENU=1; UPDATE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --link) LINK_MODE=1 ;;
    --no-bind) DO_BIND=0 ;;
    --no-menu) DO_MENU=0 ;;
    --update) UPDATE_MODE=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
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
  # Only through Omarchy's own package command. This script never calls sudo
  # or a package manager itself: without that command it says what is missing
  # and stops, rather than reach for root on a system it does not recognise.
  if ! command -v omarchy-pkg-add >/dev/null; then
    warn "Missing packages: ${missing[*]}"
    echo "  Install them, then run this script again:"
    echo "    omarchy pkg add ${missing[*]}"
    exit 1
  fi
  note "Installing packages: ${missing[*]} (the package manager shows its own progress)"
  omarchy-pkg-add "${missing[@]}"
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
  # Through Omarchy's AUR command only, and only after a yes: it re-checks with
  # pacman afterwards, because an AUR helper can exit 0 having installed nothing.
  install_signal_cli() { omarchy-pkg-aur-add signal-cli-native-bin; }
  if [[ -t 0 && -t 1 ]] && command -v omarchy-pkg-aur-add >/dev/null; then
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
    warn "Cannot install it here (no terminal to ask in, or no omarchy-pkg-aur-add)."
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
  cp -a "$HERE"/manifest.json "$HERE"/*.qml "$HERE"/*.js "$HERE/bin" "$HERE/lib" "$HERE/scripts" "$HERE/systemd" "$HERE/config" "$HERE/docs" \
        "$HERE/install.sh" "$HERE/uninstall.sh" "$HERE/README.md" "$HERE/SECURITY.md" "$HERE/LICENSE" "$PLUGIN_DIR/"
  find "$PLUGIN_DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
  say "Installed plugin to $PLUGIN_DIR"
  # A copy has no .git, so remember where it came from: `omarchy-signal update`
  # then fetches releases from the same remote (a fork stays on the fork).
  origin=$(git -C "$HERE" remote get-url origin 2>/dev/null || true)
  if [[ -n $origin ]]; then
    ORIGIN="$origin" python3 -c 'import json, os; print(json.dumps({"origin": os.environ["ORIGIN"]}))' \
      >"$HOME/.local/state/omarchy-signal/install.json" || true
  fi
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
  # The file's own header ends at its first blank line; the rest is the block.
  body=$(sed '1,/^$/d' "$HERE/config/bindings.lua")
  mkdir -p "$(dirname "$BINDINGS")"
  touch "$BINDINGS"
  # An unbalanced marker pair means the file is in a shape this plugin did not
  # write. The helper then leaves it alone and says so, where the sed range it
  # replaces deleted everything from the opening marker to end of file.
  if ! bind_result=$(printf '%s\n' "$body" | confedit block-write --file "$BINDINGS"); then
    warn "Left $BINDINGS unchanged. Fix the omarchy-signal markers there and re-run."
  elif [[ $bind_result == unchanged ]]; then
    say "Keybinding block up to date in $BINDINGS"
  else
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
  # The entry goes after the last real member, not after whatever text happens to
  # precede the closing brace: Omarchy ships this file full of commented-out
  # examples that end in commas, and reading one of those as "a comma is already
  # there" is what used to leave the file unparseable.
  if [[ $(confedit menu-add --file "$MENU") == unchanged ]]; then
    say "Menu entry already present"
  else
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
  Update later with:                              omarchy-signal update

Config: $CONFIG_DIR/config.toml   Logs: journalctl --user -u omarchy-signal
DONE

# Offer the remaining step here rather than leaving it as homework: the QR code
# appears in this same window, which is already open and already trusted.
#
# The service was restarted a moment ago, and for the first second or so the
# bridge answers "not linked" simply because signal-cli has not connected and
# read the account yet. Asking then would offer to link a computer that is
# already linked, so the answer is only believed once signal-cli is up: linked,
# unlinked, or (if it never comes up) neither, and then nothing is offered.
link_state() {          # linked | unlinked | unknown
  local deadline=$((SECONDS + 20)) json
  while (( SECONDS < deadline )); do
    json=$("$BIN_DIR/omarchy-signal" status --json 2>/dev/null) || json=""
    if [[ -n $json ]]; then
      case "$(printf '%s' "$json" | python3 -c 'import json,sys
try:
    s = json.load(sys.stdin)
except ValueError:
    print("unknown"); raise SystemExit
print("linked" if s.get("linked") else ("unlinked" if s.get("accountsKnown") else "unknown"))' 2>/dev/null)" in
        linked) echo linked; return ;;
        unlinked) echo unlinked; return ;;
      esac
    fi
    sleep 1
  done
  echo unknown
}
if (( ! UPDATE_MODE )) && [[ -t 0 && -t 1 ]] && [[ -x $BIN_DIR/omarchy-signal ]] \
   && systemctl --user is-active --quiet omarchy-signal.service; then
  case "$(link_state)" in
    linked)
      say "This computer is already linked" ;;
    unlinked)
      printf '\n'
      read -r -p "  Link this computer to your Signal account now? [Y/n] " link_ans
      if [[ -z $link_ans || ${link_ans,,} == y ]]; then
        "$BIN_DIR/omarchy-signal" link || warn "Linking did not complete; run it again with: omarchy-signal link"
      else
        note "Link later with: omarchy-signal link"
      fi ;;
    *)
      note "signal-cli has not said which accounts exist yet, so whether this computer is linked is unknown; check with: omarchy-signal doctor" ;;
  esac
fi
