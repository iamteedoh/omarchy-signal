#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Render ConversationView at a range of widths and assert the bottom legend
# wraps instead of eliding (OMSIG-6).
#
# Not part of `make test`: it needs Quickshell, the Omarchy shell modules and a
# Wayland display, and it briefly opens a real window -- layout only runs for a
# window that is actually visible. A hidden window reports the first width's
# numbers for every subsequent resize, which looks like a pass and is not one.
#
# Run it with `make legend-wrap` after touching that part of ConversationView.
# tests/bash/run.sh carries the property-level guard that runs everywhere.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SKIP_STATUS=0
[[ ${1:-} == --strict ]] && SKIP_STATUS=1
SHELL_SRC="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
command -v qs >/dev/null || { echo "legend-wrap: NOT CHECKED (Quickshell (qs) not found)" >&2; exit "$SKIP_STATUS"; }
[[ -d $SHELL_SRC/Commons && -d $SHELL_SRC/Ui ]] || { echo "legend-wrap: NOT CHECKED (Omarchy shell modules not found)" >&2; exit "$SKIP_STATUS"; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "legend-wrap: NOT CHECKED (no Wayland display)" >&2; exit "$SKIP_STATUS"; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
ln -s "$SHELL_SRC/Commons" "$tmp/Commons"
ln -s "$SHELL_SRC/Ui" "$tmp/Ui"
cp "$HERE"/*.qml "$HERE"/*.js "$tmp/"
mkdir -p "$tmp/bin"; cp "$HERE/bin/omarchy-signal" "$tmp/bin/"; cp -r "$HERE/lib" "$tmp/lib"
cat > "$tmp/shell.qml" <<'QML'
import QtQuick
import Quickshell
// The window manager owns the window's size -- setting win.width is ignored
// under a tiling compositor -- so the view's width is driven directly instead.
ShellRoot {
  id: root
  property var widths: [1780, 1180, 900, 700, 560, 420]
  property int idx: -1
  property int testWidth: 1780
  property var legend: null
  function find(item, name) {
    if (!item) return null
    if (item.objectName === name) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) { var r = find(kids[i], name); if (r) return r }
    return null
  }
  FloatingWindow {
    id: win
    implicitWidth: 1800
    implicitHeight: 700
    visible: true
    ConversationView {
      id: chatView
      width: root.testWidth
      height: parent.height - 32
      x: 16; y: 16
      cliPath: "/bin/true"
      detached: true
    }
  }
  Component.onCompleted: {
    legend = find(chatView, "legendText")
    if (!legend) { console.log("LEGEND ERROR legendText not found"); Qt.quit(); return }
    step.start()
  }
  Timer {
    id: step; interval: 250; repeat: true
    onTriggered: {
      if (root.idx >= 0) {
        var l = root.legend
        console.log("LEGEND viewW=" + root.widths[root.idx] + " legendW=" + Math.round(l.width)
                    + " lines=" + l.lineCount + " truncated=" + l.truncated)
      }
      root.idx++
      if (root.idx >= root.widths.length) { console.log("LEGEND DONE"); step.stop(); Qt.quit(); return }
      root.testWidth = root.widths[root.idx]
    }
  }
}
QML
(cd "$tmp" && exec timeout 40 qs -p "$tmp/shell.qml") >"$tmp/out.log" 2>&1 &
qs_pid=$!
for _ in $(seq 200); do
  grep -qE "LEGEND (DONE|ERROR)" "$tmp/out.log" 2>/dev/null && break
  kill -0 "$qs_pid" 2>/dev/null || break
  sleep 0.2
done
kill "$qs_pid" 2>/dev/null || true
wait "$qs_pid" 2>/dev/null || true

out=$(cat "$tmp/out.log")
if grep -q "LEGEND ERROR" <<<"$out"; then
  grep -o "LEGEND ERROR.*" <<<"$out" >&2; exit 1
fi
grep -q "LEGEND DONE" <<<"$out" || { echo "legend-wrap: run did not finish" >&2; sed 's/^/  /' <<<"$out" | tail -10 >&2; exit 1; }

fail=0
rows=$(grep -o "LEGEND viewW=.*" <<<"$out")
[[ $(grep -c . <<<"$rows") -eq 6 ]] || { echo "legend-wrap: expected 6 measurements" >&2; fail=1; }
while read -r row; do
  [[ -z $row ]] && continue
  view_w=${row#LEGEND viewW=}; view_w=${view_w%% *}
  legend_w=$(sed 's/.*legendW=\([0-9]*\).*/\1/' <<<"$row")
  lines=$(sed 's/.*lines=\([0-9]*\).*/\1/' <<<"$row")
  truncated=$(sed 's/.*truncated=\([a-z]*\).*/\1/' <<<"$row")
  why=""
  # The whole point: no width may cut the legend off.
  [[ $truncated != false ]] && why="${why} truncated;"
  # It must track the view, not sit at some fixed width.
  (( legend_w > view_w )) && why="${why} legend ${legend_w}px wider than the view;"
  # Narrow enough that one line cannot possibly hold it.
  (( view_w <= 700 && lines < 2 )) && why="${why} still one line;"
  if [[ -n $why ]]; then
    echo "  FAIL ${view_w}px ->${why} (${lines} line(s), legend ${legend_w}px, truncated=${truncated})" >&2
    fail=1
  else
    echo "  ok   ${view_w}px -> ${lines} line(s), legend ${legend_w}px, truncated=${truncated}"
  fi
done <<<"$rows"

(( fail == 0 )) && echo "legend-wrap: wraps at every width, never truncated"
exit $fail
