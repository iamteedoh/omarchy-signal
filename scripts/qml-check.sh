#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Load the plugin's QML in a throwaway Quickshell instance (with the Omarchy
# shell's qs.Commons / qs.Ui modules) and fail if a component does not load.
# install.sh runs this before copying anything into the live shell.
#
# Sourcing it with QMLCHECK_LIB_ONLY=1 defines qmlcheck_verdict and returns,
# which is how tests/bash/run.sh exercises the verdict against recorded logs.
set -euo pipefail

# qmlcheck_verdict LOGFILE: decide from a finished (or truncated) run log.
# Prints the human-readable result and returns 0 only on a complete success.
#
# The poll loop below used to stop at the first line matching QMLCHECK, which
# matches "QMLCHECK LOADED Service.qml" -- the FIRST component. It killed
# Quickshell before the other two reported, so the terminal QMLCHECK OK never
# arrived and a healthy plugin was reported as a failed load, intermittently,
# depending only on whether all three finished inside one 100ms tick (OMSIG-2).
# Only OK and ERROR are terminal; LOADED means work is still in progress.
qmlcheck_verdict() {
  local log=$1 out
  out=$(cat "$log" 2>/dev/null || true)
  local failed
  failed=$(grep -o 'QMLCHECK ERROR .*' <<<"$out" | head -1 || true)
  if [[ -n $failed ]]; then
    echo "qml-check: ${failed#QMLCHECK }" >&2
    return 1
  fi
  # Errors Quickshell reports without reaching our own error branch.
  local qml_errors
  qml_errors=$(grep -E "Cannot assign|is not a type|Unexpected token|Expected token" <<<"$out" | head -5 || true)
  if [[ -n $qml_errors ]]; then
    echo "qml-check: QML errors:" >&2
    sed 's/^/  /' <<<"$qml_errors" >&2
    return 1
  fi
  if grep -q "QMLCHECK OK" <<<"$out"; then
    echo "qml-check: $(grep -c 'QMLCHECK LOADED' <<<"$out") component(s) load"
    return 0
  fi
  # No verdict line at all: the run was cut short. Say so, and name the last
  # component that did report, rather than presenting it as the one that broke.
  local last
  last=$(grep -o 'QMLCHECK LOADED .*' <<<"$out" | tail -1 || true)
  if [[ -n $last ]]; then
    echo "qml-check: run did not finish (last loaded: ${last#QMLCHECK LOADED }); no component reported an error" >&2
  else
    echo "qml-check: run did not finish; Quickshell produced no QMLCHECK output" >&2
    grep -i -E "error|warn" <<<"$out" | grep -v portal | head -5 >&2 || true
  fi
  return 1
}

[[ ${QMLCHECK_LIB_ONLY:-0} == 1 ]] && return 0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# --strict turns "cannot check here" into a failure, for callers that know the
# environment should support it. Default stays lenient so `make test` on a
# non-Omarchy box, or a CI runner, is not blocked by it.
SKIP_STATUS=0
[[ ${1:-} == --strict ]] && SKIP_STATUS=1
SHELL_SRC="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
command -v qs >/dev/null || { echo "qml-check: NOT CHECKED (Quickshell (qs) not found)" >&2; exit "$SKIP_STATUS"; }
[[ -d $SHELL_SRC/Commons && -d $SHELL_SRC/Ui ]] || { echo "qml-check: NOT CHECKED (Omarchy shell modules not found)" >&2; exit "$SKIP_STATUS"; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "qml-check: NOT CHECKED (no Wayland display)" >&2; exit "$SKIP_STATUS"; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
ln -s "$SHELL_SRC/Commons" "$tmp/Commons"
ln -s "$SHELL_SRC/Ui" "$tmp/Ui"
cp "$HERE"/*.qml "$HERE"/*.js "$tmp/"
mkdir -p "$tmp/bin"; cp "$HERE/bin/omarchy-signal" "$tmp/bin/"; cp -r "$HERE/lib" "$tmp/lib"
cat > "$tmp/shell.qml" <<'QML'
import QtQuick
import Quickshell
// Instantiate every component the plugin ships, so a break in the bar widget or
// the conversation view is caught too -- not just Service.qml. Loader is no use
// here: ConversationView declares a required property, which only an explicit
// createObject can supply, and reusing one Loader for several sources hangs.
ShellRoot {
  id: check
  Component.onCompleted: {
    var specs = [
      { file: "Service.qml", props: ({}) },
      { file: "BarWidget.qml", props: ({}) },
      { file: "ConversationView.qml", props: ({ cliPath: "/bin/true" }) }
    ]
    for (var i = 0; i < specs.length; i++) {
      var c = Qt.createComponent(specs[i].file)
      if (c.status === Component.Error) {
        console.log("QMLCHECK ERROR " + specs[i].file + ": " + c.errorString()); Qt.quit(); return
      }
      if (c.createObject(null, specs[i].props) === null) {
        console.log("QMLCHECK ERROR " + specs[i].file + ": createObject returned null"); Qt.quit(); return
      }
      console.log("QMLCHECK LOADED " + specs[i].file)
    }
    console.log("QMLCHECK OK"); Qt.quit()
  }
}
QML
# Qt.quit() does not end a Quickshell instance, so stop it as soon as a verdict
# appears instead of waiting out the timeout. Wait for a TERMINAL line only --
# a LOADED line means there is still work in flight.
(cd "$tmp" && exec timeout 15 qs -p "$tmp/shell.qml") >"$tmp/out.log" 2>&1 &
qs_pid=$!
for _ in $(seq 150); do
  grep -qE "QMLCHECK (OK|ERROR)" "$tmp/out.log" 2>/dev/null && break
  kill -0 "$qs_pid" 2>/dev/null || break
  sleep 0.1
done
kill "$qs_pid" 2>/dev/null || true
wait "$qs_pid" 2>/dev/null || true
qmlcheck_verdict "$tmp/out.log"
