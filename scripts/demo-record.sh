#!/bin/bash
# Record a showcase video and screenshots of omarchy-signal on an empty
# workspace, using fictional contacts (the test suite's fake signal-cli).
#
# It stops the real bridge for the duration so the shell's popups and chat
# window also show the demo data, and restarts it afterwards.
#
#   scripts/demo-record.sh [output-dir]      (default ~/Videos/omarchy-signal-demo)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$HOME/Videos/omarchy-signal-demo}"
WS=9
mkdir -p "$OUT"
MON=$(hyprctl monitors -j | python3 -c "import sys,json; print(next(m for m in json.load(sys.stdin) if m['focused'])['name'])")
say() { printf '\033[1;36m▸\033[0m %s\n' "$*" >&2; }
shot() { sleep 0.4; grim -o "$MON" "$OUT/$1.png"; say "screenshot $1"; }
type_slow() { wtype -d 45 "$1"; }
inject() { printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceUuid":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"viewOnce":false,"attachments":[]}}}\n' "$1" "$1" "$2" "$3" "$3" "$4" >>"$FAKE_SIGNAL_EVENTS"; }
typing() { printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","timestamp":%s,"typingMessage":{"action":"STARTED","timestamp":%s}}}\n' "$1" "$1" "$2" "$3" "$3" >>"$FAKE_SIGNAL_EVENTS"; }
focus_class() { for _ in $(seq 1 40); do a=$(hyprctl clients -j | python3 -c "import sys,json; print(next((c['address'] for c in json.load(sys.stdin) if c['class']=='$1'), ''))"); [[ -n $a ]] && { hyprctl dispatch "hl.dsp.focus({ window = \"address:$a\" })" >/dev/null; return 0; }; sleep 0.1; done; return 1; }
focus_title() { for _ in $(seq 1 40); do a=$(hyprctl clients -j | python3 -c "import sys,json; print(next((c['address'] for c in json.load(sys.stdin) if '$1' in c['title']), ''))"); [[ -n $a ]] && { hyprctl dispatch "hl.dsp.focus({ window = \"address:$a\" })" >/dev/null; return 0; }; sleep 0.1; done; return 1; }

cleanup() {
  say "cleaning up"
  [[ -n ${REC_PID:-} ]] && kill -INT "$REC_PID" 2>/dev/null && wait "$REC_PID" 2>/dev/null
  omarchy-shell iamteedoh.signal closeChatWindow >/dev/null 2>&1
  omarchy-shell iamteedoh.signal close >/dev/null 2>&1
  omarchy-shell iamteedoh.signal dismiss >/dev/null 2>&1
  pkill -f "org.omarchy.signal-demo" 2>/dev/null
  [[ -f $DEMO/bridge.pid ]] && kill "$(cat "$DEMO/bridge.pid")" 2>/dev/null
  sleep 1
  systemctl --user start omarchy-signal.service
  hyprctl dispatch "hl.dsp.workspace({ workspace = \"$PREV_WS\" })" >/dev/null 2>&1
  say "real bridge restarted; output in $OUT"
}
trap cleanup EXIT

PREV_WS=$(hyprctl activeworkspace -j | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
DEMO="/tmp/omarchy-signal-demo-$USER"
say "stopping the real bridge for the demo"
systemctl --user stop omarchy-signal.service
# demo bridge on the REAL runtime dir (so the shell plugin talks to it), demo data elsewhere
export OMARCHY_SIGNAL_DEMO_DIR="$DEMO"
eval "$("$HERE/scripts/demo-env.sh" 2>/dev/null)"
export XDG_RUNTIME_DIR_REAL="${XDG_RUNTIME_DIR_REAL:-/run/user/$UID}"
# demo-env started a bridge on the demo runtime dir; restart it on the real socket path instead
kill "$(cat "$DEMO/bridge.pid")" 2>/dev/null; sleep 0.5
( XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_REAL" "$HERE/bin/omarchy-signal" bridge --stderr >"$DEMO/bridge2.log" 2>&1 & echo $! >"$DEMO/bridge.pid" )
export XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR_REAL"
for _ in $(seq 1 50); do "$HERE/bin/omarchy-signal" status >/dev/null 2>&1 && break; sleep 0.2; done
sleep 4   # let the shell's event streams reconnect to the demo bridge
"$HERE/bin/omarchy-signal" mark-read Trinity >/dev/null 2>&1

hyprctl dispatch "hl.dsp.workspace({ workspace = \"$WS\" })" >/dev/null; sleep 1
say "recording"
gpu-screen-recorder -w "$MON" -f 30 -q high -c mp4 -o "$OUT/omarchy-signal-demo.mp4" >"$DEMO/rec.log" 2>&1 &
REC_PID=$!
sleep 1.5

# 1. terminal client boots
setsid ghostty --class=org.omarchy.signal-demo --title="Signal" -e env XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_DATA_HOME="$XDG_DATA_HOME" XDG_STATE_HOME="$XDG_STATE_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" "$HERE/bin/omarchy-signal" tui Trinity >/dev/null 2>&1 &
focus_class org.omarchy.signal-demo || say "no client window"
sleep 4.5
shot 01-terminal-client

# 2. Trinity types, then a message arrives (list, thread and a popup)
now=$(( $(date +%s) * 1000 ))
typing "+15550002222" "Trinity" "$now"; sleep 2.5
inject "+15550002222" "Trinity" "$((now + 2500))" "Bring a terminal. Ghostty preferred 😉"
sleep 3.5
shot 02-incoming-message-and-popup

# 3. reply with an emoji shortcode (picker shows, space converts)
type_slow "On my way :roc"; sleep 1.6
wtype -k Tab; sleep 0.6
type_slow " see you at the bridge"; sleep 0.8
wtype -k Return; sleep 3
shot 03-reply-sent

# 4. attach a picture (path completion with thumbnail), send it
wtype -M ctrl a -m ctrl; sleep 1.2
wtype -M ctrl u -m ctrl; sleep 0.3          # clear the prompt to ~/
type_slow "$OMARCHY_SIGNAL_DEMO_PICS/zi"; sleep 2
shot 04-attach-with-thumbnail
wtype -k Tab; sleep 0.8
wtype -k Return; sleep 1
type_slow "Here is the map"; sleep 0.5
wtype -k Return; sleep 4
shot 05-image-inline

# 5. settings screen, then contacts picker
wtype -M ctrl s -m ctrl; sleep 2.5
shot 06-settings
wtype -k Escape; sleep 0.8
wtype -M ctrl u -m ctrl; sleep 1.2
type_slow "morp"; sleep 1.5
shot 07-contact-picker
wtype -k Escape; sleep 0.8

# 6. detach into the chat window, reply there
wtype -M alt d -m alt; sleep 4.5
focus_title "Signal ·" || say "no chat window"
sleep 1
shot 08-chat-window
type_slow "Got it 👍 heading out now"; sleep 0.6
wtype -k Return; sleep 3
shot 09-chat-window-reply

# 7. Morpheus writes: popup, click-to-reply dialog
now=$(( $(date +%s) * 1000 ))
inject "+15550003333" "Morpheus" "$now" "The Oracle will see you now. 🔮"
sleep 3
shot 10-popup
omarchy-shell iamteedoh.signal reply "number:+15550003333" >/dev/null; sleep 3
shot 11-reply-dialog
type_slow "See you in Zion."; sleep 0.5
wtype -k Return; sleep 3
shot 12-reply-dialog-sent
wtype -k Escape; sleep 1.5
say "done"
