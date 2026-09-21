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
check bash -n "$ROOT/tests/bash/install-e2e.sh"

if command -v shellcheck >/dev/null; then
  echo "shellcheck"
  for f in "$ROOT/install.sh" "$ROOT/uninstall.sh" "$ROOT"/scripts/*.sh "$ROOT"/tests/bash/*.sh "$ROOT"/tests/qml/*.sh; do
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
# Run install.sh's own copy command into a temp directory and compare what
# landed. The previous version of this check ran the same two greps for every
# file and printed "ok <name>" whatever the copy list actually said, so it could
# not fail per-file at all (OMSIG-5).
copy_cmd=$(awk '/^ *cp -a "\$HERE"/{p=1} p{print} p&&/PLUGIN_DIR\/"$/{exit}' "$ROOT/install.sh")
if [[ -z $copy_cmd ]]; then
  echo "  FAIL could not find install.sh's cp command; this check is not testing anything"; fail=1
else
  copy_dst=$(mktemp -d)
  if ! HERE="$ROOT" PLUGIN_DIR="$copy_dst" bash -c "set -euo pipefail; $copy_cmd" 2>/dev/null; then
    echo "  FAIL install.sh's copy command failed to run"; fail=1
  else
    copied=0
    for f in "$ROOT"/*.qml "$ROOT"/*.js; do
      if [[ -e "$copy_dst/$(basename "$f")" ]]; then
        copied=$((copied + 1))
      else
        echo "  FAIL install.sh does not copy $(basename "$f")"; fail=1
      fi
    done
    # A copy list that silently produced nothing would otherwise pass above.
    if (( copied == 0 )); then echo "  FAIL install.sh copied no QML/JS at all"; fail=1
    else echo "  ok   $copied QML/JS file(s), verified by running the copy"; fi
  fi
  rm -rf "$copy_dst"
fi

echo "config edits install.sh and uninstall.sh make"
# Drives the same CLI both scripts call, so the module path, the default marker
# strings and the default menu entry are all covered -- the Python tests call
# the functions directly and would not catch a broken invocation here.
ce() { PYTHONPATH="$ROOT/lib" python3 -m omarchy_signal.confedit "$@"; }
cfg=$(mktemp -d)
printf 'o.bind("SUPER + T", "Terminal", "alacritty")\no.bind("SUPER + B", "Browser", "chromium")\n' >"$cfg/bindings.lua"
cp "$cfg/bindings.lua" "$cfg/bindings.orig"
printf '{\n  // "personal": {"icon":"","label":"Personal"},\n  "mine": {"label":"Mine"}\n  // "about": {"label":"About"},\n}\n' >"$cfg/menu.jsonc"
cp "$cfg/menu.jsonc" "$cfg/menu.orig"

printf 'hl.unbind("SUPER + SHIFT + G")\n' | ce block-write --file "$cfg/bindings.lua" >/dev/null
check grep -q "BEGIN omarchy-signal" "$cfg/bindings.lua"
ce menu-add --file "$cfg/menu.jsonc" >/dev/null
check grep -q "signal-tui" "$cfg/menu.jsonc"
# The menu must still parse with the entry in it -- this is the OMSIG-4 case.
cat >"$cfg/parse.py" <<'PY'
import json, re, sys
from omarchy_signal import confedit
src = open(sys.argv[1], encoding="utf-8").read()
kept = {confedit.CODE, confedit.STRING}
bare = "".join(c if k in kept else (" " if c != "\n" else c) for _i, c, k in confedit.scan(src))
assert "signal-tui" in json.loads(re.sub(r",(\s*[}\]])", r"\1", bare)), "menu entry missing"
PY
check env PYTHONPATH="$ROOT/lib" python3 "$cfg/parse.py" "$cfg/menu.jsonc"
ce block-remove --file "$cfg/bindings.lua" >/dev/null
ce menu-remove --file "$cfg/menu.jsonc" >/dev/null
check cmp -s "$cfg/bindings.lua" "$cfg/bindings.orig"
check cmp -s "$cfg/menu.jsonc" "$cfg/menu.orig"

# OMSIG-3: an unbalanced marker pair must leave the file byte-identical and fail
# loudly, where the sed range it replaces deleted everything below the marker.
printf 'keep_me()\n-- BEGIN omarchy-signal\norphan()\no.bind("SUPER + B", "Browser", "chromium")\n' >"$cfg/broken.lua"
cp "$cfg/broken.lua" "$cfg/broken.orig"
if ce block-remove --file "$cfg/broken.lua" >/dev/null 2>&1; then
  echo "  FAIL an unmatched marker was accepted"; fail=1
else
  echo "  ok   an unmatched marker is refused"
fi
check cmp -s "$cfg/broken.lua" "$cfg/broken.orig"
rm -rf "$cfg"

echo "qml-check verdict"
# The flake this guards (OMSIG-2): the poll loop stopped at the first line
# matching QMLCHECK, which is the FIRST component's "LOADED" line, and killed
# Quickshell before the rest reported. A healthy plugin was then reported as a
# failed load -- naming the component that had just succeeded. Run the verdict
# in its own shell so qml-check.sh's `set -e` does not leak into this suite.
verdict() { QMLCHECK_LIB_ONLY=1 bash -c 'source "$0"; qmlcheck_verdict "$1"' "$ROOT/scripts/qml-check.sh" "$1"; }
vlog=$(mktemp)
printf 'QMLCHECK LOADED Service.qml\nQMLCHECK LOADED BarWidget.qml\nQMLCHECK LOADED ConversationView.qml\nQMLCHECK OK\n' >"$vlog"
check verdict "$vlog"
if [[ $(verdict "$vlog" 2>/dev/null) == *"3 component(s) load"* ]]; then echo "  ok   counts every component"; else echo "  FAIL wrong component count"; fail=1; fi

printf 'QMLCHECK LOADED Service.qml\n' >"$vlog"
if verdict "$vlog" >/dev/null 2>&1; then echo "  FAIL a truncated run was reported as success"; fail=1; else echo "  ok   a truncated run is not success"; fi
msg=$(verdict "$vlog" 2>&1 >/dev/null || true)
case $msg in
  *"did not finish"*) echo "  ok   a truncated run says so" ;;
  *) echo "  FAIL truncated run reported as: $msg"; fail=1 ;;
esac
# It must not present the component that loaded as the one that broke: the old
# output was "a component FAILED to load:" followed by "QMLCHECK LOADED Service.qml".
if [[ $msg == *"FAILED to load"* || $msg != *"last loaded: Service.qml"* ]]; then
  echo "  FAIL blames the component that succeeded: $msg"; fail=1
else
  echo "  ok   names Service.qml as the last one loaded, not as the failure"
fi

printf 'QMLCHECK LOADED Service.qml\nQMLCHECK ERROR BarWidget.qml: boom\n' >"$vlog"
if verdict "$vlog" >/dev/null 2>&1; then echo "  FAIL a real error was reported as success"; fail=1; else echo "  ok   a real error fails"; fi
msg=$(verdict "$vlog" 2>&1 >/dev/null || true)
case $msg in
  *"BarWidget.qml"*) echo "  ok   names the component that failed" ;;
  *) echo "  FAIL does not name the failing component: $msg"; fail=1 ;;
esac
rm -f "$vlog"

# The poll condition itself: waiting on any QMLCHECK line reintroduces the race.
if grep -q 'grep -qE "QMLCHECK (OK|ERROR)"' "$ROOT/scripts/qml-check.sh"; then
  echo "  ok   polls for a terminal line only"
else
  echo "  FAIL qml-check.sh no longer waits for a terminal QMLCHECK line"; fail=1
fi

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
echo "the bottom legend wraps instead of eliding"
# OMSIG-6: the legend was a single elided line, so the only way to read the end
# of it ("Ctrl+O attach · Esc closes") was to widen the window. All three
# properties below are load-bearing -- in particular, without preferredWidth 0
# the RowLayout honours the Text's full single-line implicitWidth and never asks
# it to be narrower, so it silently goes back to one line.
legend=$(awk '/objectName: "legendText"/{p=1} p{print} p&&/^      }$/{exit}' "$ROOT/ConversationView.qml")
if [[ -z $legend ]]; then
  echo "  FAIL could not find the legendText block in ConversationView.qml"; fail=1
else
  for prop in 'wrapMode: Text.WordWrap' 'Layout.preferredWidth: 0' 'Layout.fillWidth: true'; do
    if grep -qF "$prop" <<<"$legend"; then echo "  ok   legend sets $prop"
    else echo "  FAIL legend is missing $prop"; fail=1; fi
  done
  if grep -q 'elide:' <<<"$legend"; then
    echo "  FAIL legend elides again; it cannot both elide and wrap"; fail=1
  else
    echo "  ok   legend does not elide"
  fi
fi

echo "every GitHub Action is pinned to a commit SHA"
# OMSIG-10: a major-version tag like @v7 is mutable. If upstream moves it, or the
# action's repo is compromised, the changed code runs here -- and
# release-please.yml holds contents/issues/pull-requests write, which is enough to
# rewrite this repo's release path. The marketplace review blocked the submission
# on exactly this. Every uses: must name a full 40-hex commit SHA.
unpinned=0
while IFS= read -r line; do
  [[ -z $line ]] && continue
  ref=${line##*@}
  ref=${ref%%[[:space:]]*}
  if [[ ! $ref =~ ^[0-9a-f]{40}$ ]]; then
    echo "  FAIL not pinned to a commit SHA: $line"; fail=1; unpinned=$((unpinned + 1))
  fi
done < <(grep -rhoE "uses: [^[:space:]]+@[^[:space:]]+" "$ROOT"/.github/workflows/ 2>/dev/null || true)
n_uses=$(grep -rhcE "uses: [^[:space:]]+@" "$ROOT"/.github/workflows/ 2>/dev/null | awk '{s+=$1} END {print s+0}')
if (( n_uses == 0 )); then
  echo "  FAIL found no uses: lines to check; this guard is not testing anything"; fail=1
elif (( unpinned == 0 )); then
  echo "  ok   $n_uses action(s), all pinned to a 40-char SHA"
fi

echo "no 32-bit int holds a Signal timestamp"
if grep -n -E "property int (\w*[a-z0-9_]Ts|ts|\w*Timestamp)\b" "$ROOT"/*.qml; then echo "  FAIL use real/var for timestamps"; fail=1; else echo "  ok"; fi
echo "every Text, TextEdit and TextArea is PlainText"
n_text=$(grep -c -E "^\s*(Text|TextEdit|QQC\.TextArea) \{" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
n_plain=$(grep -c -E "textFormat: (Text|TextEdit).PlainText" "$ROOT/Service.qml" "$ROOT/BarWidget.qml" "$ROOT/ConversationView.qml" | awk -F: '{s+=$2} END {print s}')
if [[ $n_text -eq $n_plain ]]; then echo "  ok   $n_text/$n_plain"; else echo "  FAIL $n_text Text blocks, $n_plain PlainText"; fail=1; fi

exit $fail
