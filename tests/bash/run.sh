#!/bin/bash
# Static checks for the shell pieces: syntax, systemd unit sanity, manifest.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0
check() { if "$@" >/dev/null 2>&1; then echo "  ok   $*"; else echo "  FAIL $*"; fail=1; fi; }

echo "bash syntax"
check bash -n "$ROOT/install.sh"
check bash -n "$ROOT/uninstall.sh"
check bash -n "$ROOT/tests/bash/run.sh"

echo "launcher"
check test -x "$ROOT/bin/omarchy-signal"
check "$ROOT/bin/omarchy-signal" --version
check "$ROOT/bin/omarchy-signal" send --help

echo "manifest"
check python3 -c "import json,sys; m=json.load(open('$ROOT/manifest.json')); assert m['schemaVersion']==1 and not m['id'].startswith('omarchy.'); [open('$ROOT/'+v).close() for v in m['entryPoints'].values()]"
if command -v omarchy-plugin-validate >/dev/null; then check omarchy-plugin-validate "$ROOT"; fi

echo "installer copies every QML/JS file"
for f in "$ROOT"/*.qml "$ROOT"/*.js; do
  if grep -q '"$HERE"/\*.qml' "$ROOT/install.sh" && grep -q '"$HERE"/\*.js' "$ROOT/install.sh"; then echo "  ok   $(basename "$f")"; else echo "  FAIL install.sh does not copy $(basename "$f")"; fail=1; fi
done

echo "no symlinks inside the plugin"
if [[ -z $(find "$ROOT" -name .git -prune -o -type l -print) ]]; then echo "  ok   none"; else echo "  FAIL symlinks present"; fail=1; fi

echo "systemd unit"
if command -v systemd-analyze >/dev/null; then
  out=$(systemd-analyze --user verify "$ROOT/systemd/omarchy-signal.service" 2>&1 | grep -v "is not executable: No such file" || true)
  if [[ -z $out ]]; then echo "  ok   systemd-analyze verify"; else echo "  FAIL systemd-analyze verify: $out"; fail=1; fi
fi
check grep -q "ExecStart=%h/.local/bin/omarchy-signal bridge" "$ROOT/systemd/omarchy-signal.service"
check grep -q "NoNewPrivileges=yes" "$ROOT/systemd/omarchy-signal.service"

echo "no shell-string execution in QML"
if grep -n "execDetached\|bash -c\|sh -c" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" "$ROOT/Model.js"; then echo "  FAIL shell strings found"; fail=1; else echo "  ok   argv only"; fi
echo "no 32-bit int holds a Signal timestamp"
if grep -n -E "property int (\w*[a-z0-9_]Ts|ts|\w*Timestamp)\b" "$ROOT"/*.qml; then echo "  FAIL use real/var for timestamps"; fail=1; else echo "  ok"; fi
echo "every Text is PlainText"
n_text=$(grep -c "^\s*Text {" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
n_plain=$(grep -c "textFormat: Text.PlainText" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
if [[ $n_text -eq $n_plain ]]; then echo "  ok   $n_text/$n_plain"; else echo "  FAIL $n_text Text blocks, $n_plain PlainText"; fail=1; fi

exit $fail
