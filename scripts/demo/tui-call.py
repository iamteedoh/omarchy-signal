#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal terminal client call demo
## Date Created: 2026-11-14
## Description: Runs the real terminal client and, on a signal file, plays a made-up voice call into it for a screenshot.
"""The terminal client with a call in progress, for scripts/demo-tui-shots.sh.

A demo cannot place a real call: there is nobody to answer and no audio device
that may be touched. So this runs the real client against the demo bridge and,
once the file named in DEMO_CALL_TRIGGER appears, hands it the same "call" and
"callLevels" events the bridge sends during a call, with levels shaped like
two people talking. Nothing is sent anywhere and no sound device is
opened. Removing the file ends the call.
"""
import asyncio
import math
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "lib"))

from omarchy_signal.config import Config, Paths  # noqa: E402
from omarchy_signal.tui import App  # noqa: E402

TRINITY = "number:+15550002222"
TRIGGER = os.environ.get("DEMO_CALL_TRIGGER", "")


def voice(on: bool, t: float, phase: float) -> float:
    if not on:
        return 0.03
    syllable = abs(math.sin(t * 7.3 + phase)) * (0.6 + 0.4 * math.sin(t * 2.1 + phase))
    return max(0.45, min(1.0, 0.45 + 0.5 * syllable))


async def play(app: App) -> None:
    while not (TRIGGER and os.path.exists(TRIGGER)):
        await asyncio.sleep(0.1)
    now = int(time.time() * 1000)
    call = {"id": "42", "conversation": TRINITY, "name": "Trinity", "outgoing": True, "state": "connected",
            "media": "audio", "startedAt": now - 194400, "connectedAt": now - 192000, "endedAt": 0, "reason": "",
            "muted": False, "reconnects": 0, "encrypted": True, "setupMs": 2400, "audioError": "", "levels": {}}
    await app._on_event("call", dict(call))
    start = time.monotonic()
    tx_talk = rx_talk = 0
    while os.path.exists(TRIGGER):
        t = time.monotonic() - start
        # Both sides audible at once: one picture has to show both waveforms.
        tx = voice(True, t, 0.0)
        rx = voice(True, t, 1.7)
        tx_talk += 66 if tx > 0.42 else 0
        rx_talk += 66 if rx > 0.42 else 0
        await app._on_event("callLevels", {"id": "42", "tx": tx, "rx": rx, "txPeak": 0.9, "rxPeak": 0.85,
                                           "txTalkMs": tx_talk, "rxTalkMs": rx_talk})
        await asyncio.sleep(0.066)
    await app._on_event("call", dict(call, state="ended", endedAt=int(time.time() * 1000), reason="local_hangup"))


async def main() -> int:
    paths = Paths()
    app = App(Config.load(paths), paths, initial=sys.argv[1] if len(sys.argv) > 1 else "")
    feeder = asyncio.create_task(play(app))
    try:
        return await app.run()
    finally:
        feeder.cancel()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
