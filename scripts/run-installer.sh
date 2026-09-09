#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run install.sh from the plugin folder inside a terminal and keep the window
# open until the user has read the result. The shell's "finish setup" popup
# launches this after `omarchy plugin add`.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$HERE/install.sh"
status=$?
echo
if (( status == 0 )); then
  echo "Setup finished. Next: omarchy-signal link   (then SUPER+SHIFT+G opens the client)"
else
  echo "install.sh exited with status $status; see the messages above."
fi
read -rp "Press Enter to close this window"
exit "$status"
