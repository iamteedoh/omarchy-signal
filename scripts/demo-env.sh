#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal demo environment
## Date Created: 2026-09-08
## Description: Starts a self-contained demo bridge with fictional contacts in a throwaway HOME, for screenshots and manual testing.
# Start a self-contained DEMO of omarchy-signal: a bridge backed by the test
# suite's fake signal-cli, with fictional contacts and a seeded conversation.
#
# Everything runs in a throwaway HOME. That is the point, not a detail: a file
# picker, a path prompt and an image preview all read the home directory, so a
# demo run with the real one puts real file names and real pictures on screen.
# Nothing here reads or writes the real account, history, config or pictures.
# The only things taken from the machine are fonts and the colours of an
# Omarchy theme.
#
#   source <(scripts/demo-env.sh)        # exports HOME, XDG_* and OMARCHY_SIGNAL_* for this shell
#   omarchy-signal tui                    # the client now talks to the demo bridge
#   scripts/demo-env.sh --stop
#
#   DEMO_THEME=catppuccin-latte scripts/demo-env.sh    # any theme under $OMARCHY_PATH/themes (default tokyo-night)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_HOME="$HOME"
DEMO="${OMARCHY_SIGNAL_DEMO_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/omarchy-signal-demo}"
if [[ ${1:-} == --stop ]]; then
  [[ -f $DEMO/bridge.pid ]] && kill "$(cat "$DEMO/bridge.pid")" 2>/dev/null || true
  rm -rf "$DEMO"
  echo "demo stopped" >&2
  exit 0
fi
[[ -f $DEMO/bridge.pid ]] && kill "$(cat "$DEMO/bridge.pid")" 2>/dev/null || true
rm -rf "$DEMO"
FAKE_HOME="$DEMO/home"
mkdir -p "$FAKE_HOME"/{.config/omarchy-signal,.local/share,.local/state/omarchy/current/theme,.cache,Pictures,Downloads,Documents} "$DEMO/run"
chmod 700 "$DEMO/run"

# Fonts are not personal data; without them the demo draws in a fallback face
# with no icon glyphs. Linked, not copied: they are large.
[[ -d $REAL_HOME/.local/share/fonts ]] && ln -s "$REAL_HOME/.local/share/fonts" "$FAKE_HOME/.local/share/fonts"
if [[ -f $REAL_HOME/.config/fontconfig/fonts.conf ]]; then
  mkdir -p "$FAKE_HOME/.config/fontconfig"
  cp "$REAL_HOME/.config/fontconfig/fonts.conf" "$FAKE_HOME/.config/fontconfig/fonts.conf"
fi

# The colours of one Omarchy theme: a stock one, or one installed for this user
# (~/.config/omarchy/themes). Only its colour files are copied; a theme's
# wallpapers and anything else in the real home stay out of the demo.
THEME_NAME="${DEMO_THEME:-tokyo-night}"
THEME_SRC="${OMARCHY_PATH:-/usr/share/omarchy}/themes/$THEME_NAME"
[[ -f $THEME_SRC/colors.toml ]] || THEME_SRC="$REAL_HOME/.config/omarchy/themes/$THEME_NAME"
if [[ -f $THEME_SRC/colors.toml ]]; then
  cp "$THEME_SRC/colors.toml" "$FAKE_HOME/.local/state/omarchy/current/theme/colors.toml"
  [[ -f $THEME_SRC/shell.toml ]] && cp "$THEME_SRC/shell.toml" "$FAKE_HOME/.local/state/omarchy/current/theme/shell.toml"
  printf '%s\n' "$THEME_NAME" >"$FAKE_HOME/.local/state/omarchy/current/theme.name"
fi

cat > "$FAKE_HOME/.config/omarchy-signal/config.toml" <<TOML
signal_cli = "$HERE/tests/python/fake_signal_cli.py"
notifications = "popup"
respect_dnd = false
update_check = false     # a demo never goes to the network
TOML

export HOME="$FAKE_HOME"
export XDG_CONFIG_HOME="$FAKE_HOME/.config" XDG_DATA_HOME="$FAKE_HOME/.local/share" XDG_STATE_HOME="$FAKE_HOME/.local/state" XDG_CACHE_HOME="$FAKE_HOME/.cache"
# The bridge's socket moves; the Wayland and Hyprland sockets in XDG_RUNTIME_DIR stay reachable.
export OMARCHY_SIGNAL_RUNTIME_DIR="$DEMO/run"
export FAKE_SIGNAL_ACCOUNT="+15550001111" FAKE_SIGNAL_EVENTS="$DEMO/events.jsonl" FAKE_SIGNAL_SENT_LOG="$DEMO/sent.jsonl"
CLI="$HERE/bin/omarchy-signal"
( "$CLI" bridge --stderr >"$DEMO/bridge.log" 2>&1 & echo $! >"$DEMO/bridge.pid" )
for _ in $(seq 1 80); do [[ -S $DEMO/run/omarchy-signal/bridge.sock ]] && break; sleep 0.1; done
for _ in $(seq 1 80); do "$CLI" status --json 2>/dev/null | grep -q '"linked": true' && break; sleep 0.1; done

# ---- demo pictures, generated here (scripts/demo/*.py, fixed seed) so each shows what its name says
PICS="$FAKE_HOME/Pictures"
svg_to_png() { # reads SVG on stdin
  if command -v rsvg-convert >/dev/null; then rsvg-convert -o "$1"; else magick svg:- "$1"; fi
}
python3 "$HERE/scripts/demo/tactical-map.py" | svg_to_png "$PICS/tunnel-grid-07.png" 2>/dev/null || true
python3 "$HERE/scripts/demo/demo-art.py" red-pill | svg_to_png "$PICS/red-pill.png" 2>/dev/null || true
python3 "$HERE/scripts/demo/demo-art.py" blue-pill | svg_to_png "$PICS/blue-pill.png" 2>/dev/null || true
python3 "$HERE/scripts/demo/demo-art.py" ship | svg_to_png "$PICS/nebuchadnezzar.png" 2>/dev/null || true
if command -v magick >/dev/null; then
  magick -size 500x700 xc:white -fill '#333333' -gravity north -pointsize 28 -annotate +0+60 "Operator checklist" "$FAKE_HOME/Documents/checklist.png" 2>/dev/null || true
fi
printf 'Exit route notes.\n' >"$FAKE_HOME/Documents/notes.txt"

# ---- seed conversations (incoming through the fake's event file, outgoing through the bridge)
GROUP_ID="Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="
inject() { # number name ts text
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"viewOnce":false,"attachments":[]}}}\n' \
    "$1" "$1" "$2" "$3" "$3" "$4" >>"$FAKE_SIGNAL_EVENTS"
}
inject_group() { # number name ts text
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"viewOnce":false,"attachments":[],"groupInfo":{"groupId":"%s","type":"DELIVER"}}}}\n' \
    "$1" "$1" "$2" "$3" "$3" "$4" "$GROUP_ID" >>"$FAKE_SIGNAL_EVENTS"
}
sent() { # number ts text   (a message we sent from another device, synced here)
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceDevice":2,"timestamp":%s,"syncMessage":{"sentMessage":{"destination":"%s","destinationNumber":"%s","timestamp":%s,"message":"%s","expiresInSeconds":0}}}}\n' \
    "$FAKE_SIGNAL_ACCOUNT" "$FAKE_SIGNAL_ACCOUNT" "$2" "$1" "$1" "$2" "$3" >>"$FAKE_SIGNAL_EVENTS"
}
react() { # number name ts emoji target-ts   (their reaction to a message of ours)
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":null,"reaction":{"emoji":"%s","targetAuthorNumber":"%s","targetSentTimestamp":%s,"isRemove":false}}}}\n' \
    "$1" "$1" "$2" "$3" "$3" "$4" "$FAKE_SIGNAL_ACCOUNT" "$5" >>"$FAKE_SIGNAL_EVENTS"
}
sent_group() { # ts text   (our own message to the group, synced here)
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceDevice":2,"timestamp":%s,"syncMessage":{"sentMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"groupInfo":{"groupId":"%s","type":"DELIVER"}}}}}\n' \
    "$FAKE_SIGNAL_ACCOUNT" "$FAKE_SIGNAL_ACCOUNT" "$1" "$1" "$2" "$GROUP_ID" >>"$FAKE_SIGNAL_EVENTS"
}
inject_picture() { # number name ts text file   (a picture they sent: signal-cli keeps it under its data dir by id)
  local id="demo-$3.png" store="$XDG_DATA_HOME/signal-cli/attachments"
  [[ -f $5 ]] || { inject "$1" "$2" "$3" "$4"; return; }
  mkdir -p "$store" && cp "$5" "$store/$id"
  printf '{"envelope":{"source":"%s","sourceNumber":"%s","sourceName":"%s","sourceDevice":1,"timestamp":%s,"dataMessage":{"timestamp":%s,"message":"%s","expiresInSeconds":0,"viewOnce":false,"attachments":[{"contentType":"image/png","filename":"%s","id":"%s","size":%s,"width":1440,"height":900}]}}}\n' \
    "$1" "$1" "$2" "$3" "$3" "$4" "$(basename "$5")" "$id" "$(stat -c %s "$5")" >>"$FAKE_SIGNAL_EVENTS"
}
# The script reads in order: nobody answers a message before it arrives.
now=$(( $(date +%s) * 1000 ))
min=60000
day=86400000
NEO_TRINITY="+15550002222"; MORPHEUS="+15550003333"; TANK="+15550004444"
NIOBE="+15550005555"; LINK="+15550006666"; ORACLE="+15550007777"; SWITCH="+15550008888"

inject "$ORACLE"   "Oracle"     $((now - 2*day - 50*min)) "I was wondering when you would write."
sent   "$ORACLE"                $((now - 2*day - 48*min)) "Do I get to know what happens next?"
inject "$ORACLE"   "Oracle"     $((now - 2*day - 45*min)) "You get a cookie. The rest you already know. 🍪"

inject "$NEO_TRINITY" "Trinity" $((now - day - 60*min)) "Wake up, Neo… 🐇"
inject "$NEO_TRINITY" "Trinity" $((now - day - 59*min)) "The Matrix has you. Follow the white rabbit."
sent   "$NEO_TRINITY"           $((now - day - 57*min)) "Who is this? 🤔"
inject "$NEO_TRINITY" "Trinity" $((now - day - 56*min)) "Someone who knows what you are looking for."

inject "$LINK"     "Link"     $((now - day - 20*min)) "New training programs are loaded. Want the kung fu one first?"
sent   "$LINK"                $((now - day - 18*min)) "All of them."
inject "$LINK"     "Link"     $((now - day - 17*min)) "Thought so. Uploading tonight."

inject "$NIOBE"    "Niobe"    $((now - 190*min)) "The Logos is docked. Need anything from the surface?"
sent   "$NIOBE"               $((now - 185*min)) "Just a clear channel. Thanks, Niobe."

inject "$MORPHEUS" "Morpheus" $((now - 90*min)) "This is your last chance. After this, there is no turning back."
sent   "$MORPHEUS"            $((now - 88*min)) "Red pill. Obviously. 💊"
inject "$MORPHEUS" "Morpheus" $((now - 87*min)) "Then let me show you how deep the rabbit hole goes."

inject "$SWITCH"   "Switch"   $((now - 70*min)) "Copy that. See you at the rendezvous."

inject "$NEO_TRINITY" "Trinity" $((now - 31*min)) "Agents are on your floor. You need to leave now."
sent   "$NEO_TRINITY"           $((now - 30*min)) "Which way out?"
inject_picture "$NEO_TRINITY" "Trinity" $((now - 28*min)) "Tunnel grid 07. Your route is in red, out through exit 04." "$PICS/tunnel-grid-07.png"
sent   "$NEO_TRINITY"           $((now - 26*min)) "Got it. Heading out now 👍"
react  "$NEO_TRINITY" "Trinity" $((now - 26*min + 20000)) "🔥" $((now - 26*min))
inject "$NEO_TRINITY" "Trinity" $((now - 25*min)) "Tank has the line open for you."

inject_group "$MORPHEUS"    "Morpheus" $((now - 22*min)) "Everyone to the hard line by nine."
inject_group "$NEO_TRINITY" "Trinity"  $((now - 21*min)) "Neo is on his way down. Four minutes."
inject_group "$TANK"        "Tank"     $((now - 20*min)) "Line is hot. I have all of you on the board. 📟"
sent_group                             $((now - 19*min)) "Two floors to go."
inject_group "$MORPHEUS"    "Morpheus" $((now - 18*min)) "I will hold the door."
inject_group "$SWITCH"      "Switch"   $((now - 17*min)) "Van is running. Back entrance."

inject "$TANK"     "Tank"     $((now - 12*min)) "Operator. Exit is ready at Wabash and Lake."
inject "$TANK"     "Tank"     $((now - 11*min)) "Pick up on the second ring. 📟"
sleep 1.4
for seen in "$NEO_TRINITY" "$MORPHEUS" "$NIOBE" "$LINK" "$ORACLE" "$SWITCH" "group:$GROUP_ID"; do
  "$CLI" mark-read "$seen" >/dev/null 2>&1 || true
done
# Tank stays unread, and one more arrives last, so the list has something new at the top.
inject "$MORPHEUS" "Morpheus" $(( $(date +%s) * 1000 )) "The Oracle will see you now. 🔮"
sleep 0.6

echo "export HOME='$FAKE_HOME' XDG_CONFIG_HOME='$XDG_CONFIG_HOME' XDG_DATA_HOME='$XDG_DATA_HOME' XDG_STATE_HOME='$XDG_STATE_HOME' XDG_CACHE_HOME='$XDG_CACHE_HOME'"
echo "export OMARCHY_SIGNAL_RUNTIME_DIR='$OMARCHY_SIGNAL_RUNTIME_DIR' OMARCHY_SIGNAL_DEMO_DIR='$DEMO' OMARCHY_SIGNAL_DEMO_PICS='$PICS' OMARCHY_SIGNAL_DEMO_REAL_HOME='$REAL_HOME'"
echo "export FAKE_SIGNAL_EVENTS='$FAKE_SIGNAL_EVENTS' FAKE_SIGNAL_ACCOUNT='$FAKE_SIGNAL_ACCOUNT' FAKE_SIGNAL_SENT_LOG='$FAKE_SIGNAL_SENT_LOG'"
echo "demo bridge up: $DEMO (theme $THEME_NAME)" >&2
