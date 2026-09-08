#!/bin/bash
# Start a self-contained DEMO of omarchy-signal: a bridge backed by the test
# suite's fake signal-cli, with fictional contacts and a seeded conversation,
# in private XDG directories. Nothing here touches the real account, history
# or shell service. Prints the env exports to use for the client.
#
#   source <(scripts/demo-env.sh)        # exports OMARCHY_SIGNAL_DEMO_* and XDG_* for this shell
#   omarchy-signal tui                    # the client now talks to the demo bridge
#   scripts/demo-env.sh --stop
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO="${OMARCHY_SIGNAL_DEMO_DIR:-/tmp/omarchy-signal-demo-$USER}"
if [[ ${1:-} == --stop ]]; then
  [[ -f $DEMO/bridge.pid ]] && kill "$(cat "$DEMO/bridge.pid")" 2>/dev/null || true
  rm -rf "$DEMO"
  echo "demo stopped" >&2
  exit 0
fi
rm -rf "$DEMO"; mkdir -p "$DEMO"/{config/omarchy-signal,data,state,run,signal-cli,theme}
chmod 700 "$DEMO"/run
# the demo uses the real Omarchy theme so it looks like the user's desktop
ln -s "$HOME/.local/state/omarchy/current/theme" "$DEMO/theme/theme" 2>/dev/null || true
cat > "$DEMO/config/omarchy-signal/config.toml" <<TOML
signal_cli = "$HERE/tests/python/fake_signal_cli.py"
notifications = "popup"
TOML
export XDG_CONFIG_HOME="$DEMO/config" XDG_DATA_HOME="$DEMO/data" XDG_STATE_HOME="$DEMO/state" XDG_RUNTIME_DIR="$DEMO/run"
export FAKE_SIGNAL_ACCOUNT="+15550001111" FAKE_SIGNAL_EVENTS="$DEMO/events.jsonl" FAKE_SIGNAL_SENT_LOG="$DEMO/sent.jsonl"
# The demo bridge reads the user's real theme through XDG_STATE_HOME/omarchy/current/theme
mkdir -p "$DEMO/state/omarchy/current"; ln -sfn "$HOME/.local/state/omarchy/current/theme" "$DEMO/state/omarchy/current/theme"
cp "$HOME/.local/state/omarchy/current/theme.name" "$DEMO/state/omarchy/current/theme.name" 2>/dev/null || true
( "$HERE/bin/omarchy-signal" bridge --stderr >"$DEMO/bridge.log" 2>&1 & echo $! >"$DEMO/bridge.pid" )
for _ in $(seq 1 60); do [[ -S $DEMO/run/omarchy-signal/bridge.sock ]] && break; sleep 0.1; done
sleep 1.5
# ---- seed a conversation (incoming messages via the fake's event file, outgoing via the bridge)
inject() { # number name ts text
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceUuid":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"viewOnce":false,"attachments":[]}}}\n' \
    "$1" "$1" "$2" "$3" "$3" "$4" >>"$FAKE_SIGNAL_EVENTS"
}
sent() { # number ts text   (a message we sent from another device, synced here)
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceDevice":2,"timestamp":%s,"syncMessage":{"sentMessage":{"destination":"%s","destinationNumber":"%s","timestamp":%s,"message":"%s","expiresInSeconds":0}}}}\n' \
    "$FAKE_SIGNAL_ACCOUNT" "$FAKE_SIGNAL_ACCOUNT" "$2" "$1" "$1" "$2" "$3" >>"$FAKE_SIGNAL_EVENTS"
}
typing() { printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","timestamp":%s,"typingMessage":{"action":"STARTED","timestamp":%s}}}\n' "$1" "$1" "$2" "$3" "$3" >>"$FAKE_SIGNAL_EVENTS"; }
now=$(( $(date +%s) * 1000 ))
inject "+15550002222" "Trinity"  $((now - 3600000)) "Wake up, Neo… 🐇"
inject "+15550002222" "Trinity"  $((now - 3500000)) "The Matrix has you. Follow the white rabbit."
sent   "+15550002222"            $((now - 3400000)) "Who is this? 🤔"
inject "+15550002222" "Trinity"  $((now - 3300000)) "Someone who knows what you are looking for. Meet me at the Adams Street bridge."
inject "+15550003333" "Morpheus" $((now - 1800000)) "This is your last chance. After this, there is no turning back."
sent   "+15550003333"            $((now - 1750000)) "Red pill. Obviously. 💊"
inject "+15550003333" "Morpheus" $((now - 1700000)) "Then let me show you how deep the rabbit hole goes. 🕳️"
inject "+15550002222" "Trinity"  $((now - 600000))  "Sending you the map. Don't open it on the phone."
inject "+15550004444" "Tank"     $((now - 300000))  "Operator. Need an exit? 📟"
sleep 1
"$HERE/bin/omarchy-signal" mark-read "+15550003333" >/dev/null 2>&1 || true
"$HERE/bin/omarchy-signal" mark-read "+15550004444" >/dev/null 2>&1 || true
# a few demo pictures to attach
PICS="$DEMO/pictures"; mkdir -p "$PICS"
if command -v magick >/dev/null; then
  magick -size 640x400 gradient:'#0b1216-#8bc9eb' -fill white -gravity center -pointsize 36 -annotate 0 "ZION MAINFRAME MAP" "$PICS/zion-map.png" 2>/dev/null || true
  magick -size 640x400 plasma:fractal -blur 0x2 "$PICS/nebuchadnezzar.jpg" 2>/dev/null || true
  magick -size 400x400 xc:'#16242d' -fill '#8bc9eb' -draw "circle 200,200 200,60" -fill '#0b1216' -draw "circle 200,200 200,120" "$PICS/red-pill.png" 2>/dev/null || true
fi
echo "export OMARCHY_SIGNAL_DEMO_PICS='$PICS'"
echo "export XDG_CONFIG_HOME='$DEMO/config' XDG_DATA_HOME='$DEMO/data' XDG_STATE_HOME='$DEMO/state' XDG_RUNTIME_DIR='$DEMO/run' FAKE_SIGNAL_EVENTS='$FAKE_SIGNAL_EVENTS' FAKE_SIGNAL_ACCOUNT='$FAKE_SIGNAL_ACCOUNT' OMARCHY_SIGNAL_DEMO_DIR='$DEMO'"
echo "demo bridge up: $DEMO" >&2
