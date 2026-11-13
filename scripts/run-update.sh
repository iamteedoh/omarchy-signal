#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal update runner
## Date Created: 2026-11-12
## Description: Runs `omarchy-signal update` in a terminal and keeps the window open until the result has been read.
# The shell's "update available" popup and the bar panel launch this.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$HERE/bin/omarchy-signal" update
status=$?
echo
if (( status != 0 )); then
  echo "The update did not finish (status $status); see the messages above."
fi
read -rp "Press Enter to close this window"
exit "$status"
