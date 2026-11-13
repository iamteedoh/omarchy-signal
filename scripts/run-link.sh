#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal link runner
## Date Created: 2026-11-12
## Description: Runs `omarchy-signal link` in a terminal and keeps the window open until the result has been read.
# The bar panel's "Link this computer" button launches this.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$HERE/bin/omarchy-signal" link
status=$?
echo
read -rp "Press Enter to close this window"
exit "$status"
