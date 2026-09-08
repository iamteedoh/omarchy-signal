#!/bin/bash
# Load the plugin's QML in a throwaway Quickshell instance (with the Omarchy
# shell's qs.Commons / qs.Ui modules) and fail if Service.qml does not load.
# install.sh runs this before copying anything into the live shell.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHELL_SRC="${OMARCHY_PATH:-/usr/share/omarchy}/shell"
command -v qs >/dev/null || { echo "qml-check: qs not found, skipping"; exit 0; }
[[ -d $SHELL_SRC/Commons && -d $SHELL_SRC/Ui ]] || { echo "qml-check: Omarchy shell modules not found, skipping"; exit 0; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { echo "qml-check: no Wayland display, skipping"; exit 0; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
ln -s "$SHELL_SRC/Commons" "$tmp/Commons"
ln -s "$SHELL_SRC/Ui" "$tmp/Ui"
cp "$HERE"/*.qml "$HERE"/*.js "$tmp/"
mkdir -p "$tmp/bin"; cp "$HERE/bin/omarchy-signal" "$tmp/bin/"; cp -r "$HERE/lib" "$tmp/lib"
cat > "$tmp/shell.qml" <<'QML'
import QtQuick
import Quickshell
ShellRoot {
  Loader {
    id: l
    source: "Service.qml"
    onStatusChanged: {
      if (status === Loader.Error) { console.log("QMLCHECK ERROR"); Qt.quit() }
      if (status === Loader.Ready) { console.log("QMLCHECK OK"); Qt.quit() }
    }
  }
}
QML
out=$(cd "$tmp" && timeout 15 qs -p "$tmp/shell.qml" 2>&1 || true)
if grep -q "QMLCHECK OK" <<<"$out" && ! grep -q -E "QMLCHECK ERROR|Cannot assign|is not a type|Unexpected token|Expected token" <<<"$out"; then
  echo "qml-check: Service.qml loads"
  exit 0
fi
echo "qml-check: Service.qml FAILED to load:" >&2
grep -i -E "error|warn|QMLCHECK" <<<"$out" | grep -v portal | head -10 >&2
exit 1
