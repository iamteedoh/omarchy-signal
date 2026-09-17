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
# install.sh prints its own closing message and next steps; repeating them here
# just made the end of the run read twice. Only speak up when it failed.
if (( status != 0 )); then
  echo "install.sh exited with status $status; see the messages above."
fi
read -rp "Press Enter to close this window"
exit "$status"
