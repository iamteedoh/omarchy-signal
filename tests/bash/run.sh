#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Static checks for the shell pieces: syntax, systemd unit sanity, manifest.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail=0
check() { if "$@" >/dev/null 2>&1; then echo "  ok   $*"; else echo "  FAIL $*"; fail=1; fi; }

echo "bash syntax"
check bash -n "$ROOT/install.sh"
check bash -n "$ROOT/uninstall.sh"
check bash -n "$ROOT/tests/bash/run.sh"

if command -v shellcheck >/dev/null; then
  echo "shellcheck"
  for f in "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT"/scripts/*.sh "$ROOT/tests/bash/run.sh"; do
    check shellcheck -S warning "$f"
  done
else
  echo "shellcheck: not installed, skipping"
fi

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
# One vetted exception: the vendored execArgv helper. It hands argv to bash as
# positional parameters via `exec "$@"` and never builds a shell string, which
# is exactly what Omarchy's own Util.execArgv does. Matched as a fixed string,
# so any other use of execDetached / bash -c / sh -c still fails.
EXEC_HELPER='Quickshell.execDetached(["bash", "-lc", '"'"'exec "$@"'"'"', "bash"].concat(argv))'
shell_hits=$(grep -n "execDetached\|bash -c\|sh -c" \
  "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" "$ROOT/Model.js" \
  | grep -vF "$EXEC_HELPER" || true)
n_helper=$(grep -cF "$EXEC_HELPER" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
if [[ -n $shell_hits ]]; then echo "$shell_hits"; echo "  FAIL shell strings found"; fail=1
elif [[ $n_helper -ne 3 ]]; then echo "  FAIL expected the execArgv helper in all 3 components, found $n_helper"; fail=1
else echo "  ok   argv only (execArgv helper x$n_helper)"; fi

echo "no Omarchy API outside the supported baseline"
# The bug this catches: a qs.Commons member that exists on the development
# machine but not on the oldest supported Omarchy. QML resolves it at call
# time, so the file loads, the plugin enables, and the action silently does
# nothing. Runs anywhere -- no Omarchy or Quickshell needed.
BASELINE="$ROOT/tests/omarchy-api-baseline.txt"
if [[ -f $BASELINE ]]; then
  min_omarchy=$(grep -E '^MIN_OMARCHY=' "$BASELINE" | cut -d= -f2)
  allowed=$(grep -vE '^#|^MIN_OMARCHY=|^$' "$BASELINE" | sort -u)
  actual=$(sed 's|//.*||' "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" \
    | grep -ohE '\b(Util|Style|Color)\.[A-Za-z_][A-Za-z0-9_.]*' | sort -u)
  extra=$(comm -13 <(printf '%s\n' "$allowed") <(printf '%s\n' "$actual"))
  if [[ -n $extra ]]; then
    echo "  FAIL these Omarchy APIs are not in the baseline (verified against ${min_omarchy}):"
    printf '         %s\n' $extra
    echo "         Confirm each exists in ${min_omarchy} and add it to tests/omarchy-api-baseline.txt,"
    echo "         or vendor it the way execArgv is vendored in the QML components."
    fail=1
  else
    echo "  ok   $(printf '%s\n' "$actual" | grep -c .) APIs, all in the ${min_omarchy} baseline"
  fi
else
  echo "  FAIL tests/omarchy-api-baseline.txt is missing"; fail=1
fi
echo "no 32-bit int holds a Signal timestamp"
if grep -n -E "property int (\w*[a-z0-9_]Ts|ts|\w*Timestamp)\b" "$ROOT"/*.qml; then echo "  FAIL use real/var for timestamps"; fail=1; else echo "  ok"; fi
echo "every Text, TextEdit and TextArea is PlainText"
n_text=$(grep -c -E "^\s*(Text|TextEdit|QQC\.TextArea) \{" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
n_plain=$(grep -c -E "textFormat: (Text|TextEdit).PlainText" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
if [[ $n_text -eq $n_plain ]]; then echo "  ok   $n_text/$n_plain"; else echo "  FAIL $n_text Text blocks, $n_plain PlainText"; fail=1; fi

exit $fail
