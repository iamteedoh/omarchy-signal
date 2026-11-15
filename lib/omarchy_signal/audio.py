# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal call audio
## Date Created: 2026-11-14
## Description: Finds the microphones and speakers, wires a call to them and measures how loud each side is.
"""Audio for a call: devices, routing and levels.

``signal-cli`` hands every call to ``signal-call-tunnel``, which makes two
virtual PulseAudio devices for it and nothing more:

* ``sink_for_<input>``: whatever is played into this sink is what the other
  person hears.
* ``<output>.monitor``: what the other person says comes out here.

Nothing is connected to a real microphone or speaker until a client does it.
This module does that with two ``module-loopback`` instances (the same thing
``pactl load-module module-loopback`` does by hand), so the microphone is
only ever attached while a call is connected, and muting takes it off again
instead of muting the microphone for the whole desktop.

The levels come from ``parec`` reading the two virtual devices, so they show
what is really being sent and what really arrives, not what the microphone
picks up while muted.

Everything goes through ``pactl``/``parec`` (PipeWire's pulse server on
Omarchy). Nothing here keeps or writes any audio.
"""

from __future__ import annotations

import array
import asyncio
import contextlib
import json
import logging
import math
import re
import shutil
import sys
from typing import Callable

log = logging.getLogger("omarchy_signal.audio")

METER_RATE = 8000            # plenty for a level; keeps the reader cheap
METER_FRAME_MS = 50
METER_FRAME_BYTES = METER_RATE * 2 * METER_FRAME_MS // 1000
FLOOR_DB = -60.0             # quieter than this reads as silence
SPEECH_LEVEL = 0.42          # about -35 dBFS: above this somebody is talking
LOOPBACK_LATENCY_MS = 20

# Device names are handed to pactl as one argument each, never through a
# shell, but they are also written into a module argument string
# ("source=<name> sink=<name>"), where a space or a quote would start a new
# argument. PulseAudio and PipeWire node names never need either.
_DEVICE_NAME = re.compile(r"^[A-Za-z0-9_.:+\-]{1,256}$")
# The tunnel's own devices, which must never be offered as a microphone or a
# speaker (a call wired to itself).
_VIRTUAL = re.compile(r"^(sink_for_)?signal_(input|output)")


class AudioError(RuntimeError):
    pass


def valid_device(name: object) -> bool:
    return isinstance(name, str) and bool(_DEVICE_NAME.match(name))


def is_call_device(name: str) -> bool:
    return bool(_VIRTUAL.match(name))


def level_from_pcm(frame: bytes) -> float:
    """How loud a frame of signed 16-bit little-endian mono audio is, 0 to 1.

    The scale is decibels, not amplitude: 0 is :data:`FLOOR_DB` or quieter and
    1 is full scale, so ordinary speech sits around the middle instead of
    hugging the bottom the way a linear meter does."""
    usable = len(frame) - (len(frame) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frame[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    total = 0
    for s in samples:
        total += s * s
    rms = math.sqrt(total / len(samples))
    if rms < 1:
        return 0.0
    db = 20 * math.log10(rms / 32768.0)
    return max(0.0, min(1.0, (db - FLOOR_DB) / -FLOOR_DB))


def parse_devices(raw: str, *, kind: str, default: str = "") -> list[dict]:
    """Turn ``pactl -f json list sources|sinks`` into what a client can list.

    Monitors are left out of the microphones (a monitor is a speaker played
    back), and the tunnel's own virtual devices are left out of both."""
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not valid_device(name) or is_call_device(name):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        if kind == "input" and (name.endswith(".monitor") or props.get("device.class") == "monitor"):
            continue
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        description = " ".join(description.split())[:120] or name
        out.append({"name": name, "description": description, "default": name == default,
                    "muted": bool(item.get("mute"))})
    out.sort(key=lambda d: (not d["default"], d["description"].lower()))
    return out


class Pactl:
    """The few pactl calls a call needs. ``run`` can be swapped in a test."""

    def __init__(self, pactl: str = "pactl", parec: str = "parec"):
        self.pactl = pactl
        self.parec = parec

    def available(self) -> str:
        """"" when calls can have audio, otherwise what is missing."""
        for exe in (self.pactl, self.parec):
            if "/" not in exe and not shutil.which(exe):
                return f"{exe} is not installed"
        return ""

    async def run(self, *args: str, timeout: float = 5.0) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.pactl, *args, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise AudioError(f"cannot run pactl: {exc}") from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            raise AudioError("the sound server did not answer") from None
        if proc.returncode != 0:
            raise AudioError((err.decode("utf-8", "replace").strip() or "pactl failed")[:200])
        return out.decode("utf-8", "replace")

    async def default(self, kind: str) -> str:
        name = (await self.run("get-default-source" if kind == "input" else "get-default-sink")).strip()
        return name if valid_device(name) and not is_call_device(name) else ""

    async def devices(self) -> dict:
        default_in = await self.default("input")
        default_out = await self.default("output")
        sources = await self.run("-f", "json", "list", "sources")
        sinks = await self.run("-f", "json", "list", "sinks")
        return {"inputs": parse_devices(sources, kind="input", default=default_in),
                "outputs": parse_devices(sinks, kind="output", default=default_out),
                "defaultInput": default_in, "defaultOutput": default_out}

    async def loopback(self, source: str, sink: str) -> int:
        if not (valid_device(source) and valid_device(sink)):
            raise AudioError("invalid audio device name")
        out = await self.run("load-module", "module-loopback", f"source={source}", f"sink={sink}",
                             f"latency_msec={LOOPBACK_LATENCY_MS}", "source_dont_move=true", "sink_dont_move=true")
        try:
            return int(out.strip().split()[0])
        except (ValueError, IndexError):
            raise AudioError("the sound server did not create the connection") from None

    async def unload(self, module: int) -> None:
        with contextlib.suppress(AudioError):
            await self.run("unload-module", str(module))

    async def record(self, source: str) -> asyncio.subprocess.Process:
        """A parec process writing raw 16-bit mono at :data:`METER_RATE` to its stdout."""
        if not valid_device(source):
            raise AudioError("invalid audio device name")
        try:
            return await asyncio.create_subprocess_exec(
                self.parec, f"--device={source}", f"--rate={METER_RATE}", "--channels=1", "--format=s16le",
                "--raw", f"--latency-msec={METER_FRAME_MS}", "--client-name=omarchy-signal-meter",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            raise AudioError(f"cannot run parec: {exc}") from exc


class CallAudio:
    """One call's connections to the real microphone and speaker.

    ``input_device``/``output_device`` are the names signal-cli reports for
    the call. ``mic`` and ``speaker`` are real device names, or "" for the
    system default at the moment the call connects.

    ``on_level(side, level)`` is called for every measured frame with side
    "tx" (what is being sent) or "rx" (what arrives)."""

    def __init__(self, pactl: Pactl, input_device: str, output_device: str, *, mic: str = "", speaker: str = "",
                 on_level: Callable[[str, float], None] | None = None):
        if not (valid_device(input_device) and valid_device(output_device)):
            raise AudioError("signal-cli reported no usable audio device for the call")
        self.pactl = pactl
        self.tx_sink = f"sink_for_{input_device}"
        self.rx_source = f"{output_device}.monitor"
        self.mic = mic
        self.speaker = speaker
        self.on_level = on_level
        self.muted = False
        self.started = False
        self.mic_in_use = ""
        self.speaker_in_use = ""
        self._mic_module = 0
        self._speaker_module = 0
        self._meters: list[asyncio.Task] = []
        self._procs: list[asyncio.subprocess.Process] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.started:
                return
            self.started = True
            await self._connect_speaker()
            if not self.muted:
                await self._connect_mic()
            self._meters = [asyncio.create_task(self._meter("tx", f"{self.tx_sink}.monitor")),
                            asyncio.create_task(self._meter("rx", self.rx_source))]

    async def stop(self) -> None:
        async with self._lock:
            self.started = False
            for task in self._meters:
                task.cancel()
            for task in self._meters:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._meters = []
            for proc in self._procs:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
            self._procs = []
            await self._disconnect_mic()
            if self._speaker_module:
                await self.pactl.unload(self._speaker_module)
                self._speaker_module = 0

    async def set_muted(self, muted: bool) -> None:
        async with self._lock:
            self.muted = muted
            if not self.started:
                return
            if muted:
                await self._disconnect_mic()
            elif not self._mic_module:
                await self._connect_mic()

    async def set_mic(self, name: str) -> None:
        async with self._lock:
            self.mic = name
            if self.started and not self.muted:
                await self._disconnect_mic()
                await self._connect_mic()

    async def set_speaker(self, name: str) -> None:
        async with self._lock:
            self.speaker = name
            if self.started:
                if self._speaker_module:
                    await self.pactl.unload(self._speaker_module)
                    self._speaker_module = 0
                await self._connect_speaker()

    async def _connect_mic(self) -> None:
        source = self.mic or await self.pactl.default("input")
        if not source:
            raise AudioError("no microphone found")
        self._mic_module = await self.pactl.loopback(source, self.tx_sink)
        self.mic_in_use = source

    async def _disconnect_mic(self) -> None:
        if self._mic_module:
            await self.pactl.unload(self._mic_module)
            self._mic_module = 0

    async def _connect_speaker(self) -> None:
        sink = self.speaker or await self.pactl.default("output")
        if not sink:
            raise AudioError("no speaker found")
        self._speaker_module = await self.pactl.loopback(self.rx_source, sink)
        self.speaker_in_use = sink

    async def _meter(self, side: str, source: str) -> None:
        try:
            proc = await self.pactl.record(source)
        except AudioError as exc:
            log.warning("no %s level: %s", side, exc)
            return
        self._procs.append(proc)
        assert proc.stdout is not None
        try:
            while True:
                frame = await proc.stdout.readexactly(METER_FRAME_BYTES)
                if self.on_level is not None:
                    self.on_level(side, level_from_pcm(frame))
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
