#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal terminal client screenshots
## Date Created: 2026-11-13
## Description: Photographs the terminal client running in Ghostty against the demo bridge, inside a private nested Hyprland, so nothing of the real desktop can be captured.
#
# The terminal client draws images through the kitty graphics protocol, so its
# pictures have to come from a real terminal. This starts a second Hyprland
# nested in the current session, with its own config: one output, a plain
# background in the theme's colours, no bar, no wallpaper, no notifications,
# and nothing running in it but one Ghostty. grim and wtype talk only to that
# compositor's socket, so a screenshot cannot contain anything else.
#
# Everything Ghostty and the client read comes from scripts/demo-env.sh's
# throwaway HOME. The nested compositor's window is visible on the real
# desktop for the few seconds this takes; nothing is read from it.
#
#   scripts/demo-tui-shots.sh [output-dir]        (default docs/media)
#   DEMO_THEME=gruvbox scripts/demo-tui-shots.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mkdir -p "${1:-$HERE/docs/media}" && cd "${1:-$HERE/docs/media}" && pwd)"
REAL_HOME="$HOME"
for tool in Hyprland ghostty grim wtype hyprctl; do
  command -v "$tool" >/dev/null || { echo "demo-tui-shots: $tool not found" >&2; exit 1; }
done
[[ -n ${WAYLAND_DISPLAY:-} && -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]] || { echo "demo-tui-shots: needs a Hyprland session to nest in" >&2; exit 1; }
PARENT="$HYPRLAND_INSTANCE_SIGNATURE"

THEME="${DEMO_THEME:-lumon-dark}"
[[ -f ${OMARCHY_PATH:-/usr/share/omarchy}/themes/$THEME/colors.toml || -f $HOME/.config/omarchy/themes/$THEME/colors.toml ]] || THEME=lumon
export OMARCHY_SIGNAL_DEMO_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-signal-tui-shots"
work="$(mktemp -d)"
hypr_pid=""
cleanup() {
  [[ -n $hypr_pid ]] && kill "$hypr_pid" 2>/dev/null
  HOME="$REAL_HOME" "$HERE/scripts/demo-env.sh" --stop >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

HOME="$REAL_HOME" DEMO_THEME="$THEME" "$HERE/scripts/demo-env.sh" >"$work/env" 2>"$work/env.log" \
  || { echo "demo-tui-shots: the demo environment did not start" >&2; sed 's/^/  /' "$work/env.log" >&2; exit 1; }
# shellcheck disable=SC1090,SC1091
source "$work/env"
if [[ $HOME == "$REAL_HOME" || $HOME != "$OMARCHY_SIGNAL_DEMO_DIR"/* ]]; then
  echo "demo-tui-shots: refusing to run with HOME=$HOME" >&2; exit 1
fi

# The theme's colours, read from the copy demo-env.sh put in the fake home.
colors="$HOME/.local/state/omarchy/current/theme/colors.toml"
pick() { sed -n "s/^$1 *= *\"#\{0,1\}\([0-9a-fA-F]\{6\}\)\".*/\1/p" "$colors" | head -1; }
BG=$(pick background); FG=$(pick foreground); DARKER=$(pick darker_background); BORDER=$(pick active_border_color)
ACCENT=$(pick accent); SEL=$(pick selection)
[[ -n $BORDER ]] || BORDER=$ACCENT
pal=""
i=0
for name in black red green yellow blue magenta cyan white bright_black bright_red bright_green bright_yellow bright_blue bright_magenta bright_cyan bright_white; do
  v=$(pick "$name"); [[ -n $v ]] && pal+="palette = $i=#$v"$'\n'
  i=$((i + 1))
done
mkdir -p "$HOME/.config/ghostty"
cat >"$HOME/.config/ghostty/config" <<CFG
font-family = "JetBrainsMono Nerd Font"
font-size = 11
background = #${BG}
foreground = #${FG}
cursor-color = #${FG}
selection-background = #${SEL:-$ACCENT}
${pal}
window-padding-x = 14
window-padding-y = 14
window-decoration = none
confirm-close-surface = false
resize-overlay = never
cursor-style-blink = false
shell-integration = none
CFG

# The nested compositor. Its one output takes the size of its window on the
# real desktop, so that window (and only that window) is floated and sized
# there: 2560x1600, drawn at scale 2 inside, a 1280x800 desktop at twice the
# pixels so the pictures stay sharp on a dense display.
cat >"$work/hyprland.lua" <<LUA
hl.monitor({ output = "", mode = "preferred", position = "0x0", scale = 2 })
hl.config({
  general = { gaps_in = 0, gaps_out = 36, border_size = 2,
              col = { active_border = "rgb(${BORDER})", inactive_border = "rgb(${BORDER})" } },
  decoration = { rounding = 0, shadow = { enabled = true, range = 18, render_power = 3, color = "rgba(${ACCENT}55)" },
                 blur = { enabled = false } },
  animations = { enabled = false },
  misc = { disable_hyprland_logo = true, force_default_wallpaper = 0, background_color = "rgb(${DARKER:-$BG})",
           disable_splash_rendering = true, disable_watchdog_warning = true, disable_scale_notification = true },
  cursor = { invisible = true },
  debug = { suppress_errors = true },
})
LUA

before_hypr=$(command ls "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | sort)
before_wl=$(command ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$' | sort)
Hyprland --config "$work/hyprland.lua" >"$work/hypr.log" 2>&1 &
hypr_pid=$!
sig="" nested=""
for _ in $(seq 1 100); do
  sleep 0.1
  sig=$(comm -13 <(printf '%s\n' "$before_hypr") <(command ls "$XDG_RUNTIME_DIR/hypr" | sort) | head -1)
  nested=$(comm -13 <(printf '%s\n' "$before_wl") <(command ls "$XDG_RUNTIME_DIR" | grep '^wayland-[0-9]*$' | sort) | head -1)
  [[ -n $sig && -n $nested && -S $XDG_RUNTIME_DIR/hypr/$sig/.socket.sock ]] && break
done
[[ -n $sig && -n $nested ]] || { echo "demo-tui-shots: the nested compositor did not start" >&2; tail -20 "$work/hypr.log" >&2; exit 1; }
hc() { hyprctl --instance "$sig" "$@"; }

# Its window on the real desktop, found by process id.
addr=""
for _ in $(seq 1 50); do
  addr=$(hyprctl --instance "$PARENT" clients -j 2>/dev/null \
    | python3 -c 'import json,sys; print(next((c["address"] for c in json.load(sys.stdin) if c.get("pid")==int(sys.argv[1])), ""))' "$hypr_pid")
  [[ -n $addr ]] && break
  sleep 0.1
done
[[ -n $addr ]] || { echo "demo-tui-shots: could not find the nested compositor's window" >&2; exit 1; }
hyprctl --instance "$PARENT" eval "local w = 'address:$addr'
hl.dispatch(hl.dsp.window.float({ action = 'on', window = w }))
hl.dispatch(hl.dsp.window.resize({ x = 2560, y = 1600, relative = false, window = w }))
hl.dispatch(hl.dsp.window.center({ window = w }))" >/dev/null
sleep 1
# Now that the output is 2560x1600, scale 2 divides it cleanly.
hc reload >/dev/null
sleep 1
size=$(hc -j monitors | python3 -c 'import json,sys; m=json.load(sys.stdin)[0]; print(m["width"], m["height"], "scale", m["scale"])')
echo "  nested output: $size" >&2
hc eval "hl.exec_cmd(\"ghostty --title=omarchy-signal -e $HERE/bin/omarchy-signal tui Trinity\")" >/dev/null
sleep 4   # Ghostty starts, the client connects and draws

key() { WAYLAND_DISPLAY="$nested" wtype "$@"; sleep "${PAUSE:-0.8}"; }
shot() {
  sleep 0.6
  WAYLAND_DISPLAY="$nested" grim "$OUT/$1.png" && echo "  SHOT saved $1.png"
}

# 1. The conversation with the picture Trinity sent, drawn inline.
sleep 1.5
shot tui-chat
# 2. Attach: the path prompt, its folder listing and the preview of the highlighted picture.
key -M ctrl a -m ctrl
key "Pictures/"                    # the prompt starts at ~/
PAUSE=1.6 key -k Down              # highlight a picture: its preview appears
shot tui-attach
key -k Escape
key -k Escape
# 3. Settings.
PAUSE=1.2 key -M ctrl s -m ctrl
shot tui-settings
key -k Escape
