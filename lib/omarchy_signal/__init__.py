# SPDX-License-Identifier: GPL-3.0-or-later
"""omarchy-signal: a Signal messenger client for the Omarchy desktop.

The package is split into three layers that never share process state:

* ``bridge``   – a per-user daemon that supervises ``signal-cli`` (which owns
                 the Signal protocol, keys and end-to-end encryption), keeps a
                 local message history, and exposes a small newline-JSON API on
                 a private Unix socket.
* ``tui``/``cli`` – the terminal client that speaks to the bridge.
* the Quickshell plugin (QML, in the repository root) that also speaks to the
  bridge and draws the notification popup, reply window and bar widget.

Nothing in this package ever touches Signal key material: it lives in
``signal-cli``'s data directory and only ``signal-cli`` reads it.
"""

__version__ = "0.1.0"
