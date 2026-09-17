#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Load the plugin's QML in a throwaway Quickshell instance (with the Omarchy
# shell's qs.Commons / qs.Ui modules) and fail if Service.qml does not load.
# install.sh runs this before copying anything into the live shell.
set -euo pipefail
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
# Qt.quit() does not end a Quickshell instance, so stop it as soon as the
# result line appears instead of waiting out the timeout.
(cd "$tmp" && exec timeout 15 qs -p "$tmp/shell.qml") >"$tmp/out.log" 2>&1 &
qs_pid=$!
for _ in $(seq 150); do
  grep -q "QMLCHECK" "$tmp/out.log" 2>/dev/null && break
  kill -0 "$qs_pid" 2>/dev/null || break
  sleep 0.1
done
kill "$qs_pid" 2>/dev/null || true
wait "$qs_pid" 2>/dev/null || true
out=$(cat "$tmp/out.log")
if grep -q "QMLCHECK OK" <<<"$out" && ! grep -q -E "QMLCHECK ERROR|Cannot assign|is not a type|Unexpected token|Expected token" <<<"$out"; then
  echo "qml-check: $(grep -c 'QMLCHECK LOADED' <<<"$out") component(s) load"
  exit 0
fi
echo "qml-check: a component FAILED to load:" >&2
grep -i -E "error|warn|QMLCHECK" <<<"$out" | grep -v portal | head -10 >&2
exit 1
