# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal calls
## Date Created: 2026-11-14
## Description: Voice calls: follows signal-cli's call events, wires the audio and reports state, levels and telemetry.
"""Voice calls.

``signal-cli`` (0.14.2 and newer) places and answers one-to-one voice calls
through a helper, ``signal-call-tunnel``, which runs Signal's own calling
library (RingRTC). The media is end-to-end encrypted by that library with keys
derived from both sides' identity keys, exactly as on a phone; this module
never sees it.

What happens here:

* :class:`CallManager` subscribes to signal-cli's ``callEvent`` notifications,
  keeps one record per call and tells the clients about every change.
* When a call connects it hands the call's two virtual audio devices to
  :class:`omarchy_signal.audio.CallAudio`, which attaches the microphone and
  the speaker and measures both directions.
* When a call ends it writes one line into the conversation's history
  ("Missed voice call", "Outgoing voice call · 3:12").

The pure functions at the top (labels, clock, waveform, readings) are shared
with the chat window's ``Calls.js``; both are held to
``tests/vectors/calls.json`` so the two clients describe a call the same way.

Video is not here: neither signal-cli nor the tunnel carries it. An incoming
video call is answered as a voice call and says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import protocol
from .audio import AudioDisconnectError, METER_FRAME_MS, SPEECH_LEVEL, AudioError, CallAudio, Pactl, valid_device
from .envelope import Event
from .rpc import RpcClosed, RpcError
from .sanitize import E164, UUID, InvalidRecipient, Recipient, parse_conversation_key

log = logging.getLogger("omarchy_signal.calls")

U64 = 1 << 64
LEVELS_INTERVAL_S = 0.066         # how often levels are pushed to the clients (15 a second)
ENDED_LINGER_S = 8                # an ended call stays listed this long, so a late client still sees how it ended
MIN_SIGNAL_CLI = (0, 14, 3)       # 0.14.2 answered "busy" for every linked device; 0.14.3 fixed that
TUNNEL_NAME = "signal-call-tunnel"

RINGING, CONNECTING, CONNECTED, RECONNECTING, ENDED = "ringing", "connecting", "connected", "reconnecting", "ended"
_STATES = {"RINGING_INCOMING": RINGING, "RINGING_OUTGOING": RINGING, "CONNECTING": CONNECTING,
           "CONNECTED": CONNECTED, "RECONNECTING": RECONNECTING, "ENDED": ENDED}
_BARS = "▁▂▃▄▅▆▇█"


# --------------------------------------------------------------------------
# Pure functions, mirrored in Calls.js
# --------------------------------------------------------------------------

def call_id_text(value: Any) -> str:
    """A call id as the unsigned decimal string the clients use.

    signal-cli prints the id as a signed 64-bit number in ``callEvent`` and as
    an unsigned one inside a received call message, and a JavaScript client
    cannot hold either exactly. A string of the unsigned value is the one
    spelling all three agree on. "" for anything that is not a 64-bit integer."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > 21 or not text.lstrip("-").isdigit() or "-" in text[1:]:
            return ""
        value = int(text)
    if not isinstance(value, int) or not (-(U64 // 2) <= value < U64):
        return ""
    return str(value % U64)


def call_id_signed(text: str) -> int:
    """The same id as the signed number signal-cli's commands take."""
    value = int(text)
    return value - U64 if value >= U64 // 2 else value


def clock(ms: Any) -> str:
    """``0:07``, ``3:12``, ``1:02:03``; "" for no duration."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms != ms or ms < 0:
        return ""
    s = int(ms // 1000)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def state_label(call: dict) -> str:
    """What a call is doing, in words."""
    state = call.get("state")
    if state == RINGING:
        return "Calling" if call.get("outgoing") else "Incoming call"
    if state == CONNECTING:
        return "Connecting"
    if state == CONNECTED:
        return "Connected"
    if state == RECONNECTING:
        return "Reconnecting"
    if state == ENDED:
        return end_label(call)
    return ""


def end_label(call: dict) -> str:
    """How a call ended, in words. ``reason`` is signal-cli's, plus the
    bridge's own "elsewhere" (answered or declined on another device)."""
    reason = str(call.get("reason") or "")
    outgoing = bool(call.get("outgoing"))
    answered = bool(call.get("connectedAt"))
    if reason == "elsewhere":
        return "Answered on another device"
    if reason == "busy_here":                 # it rang while you were on another call
        return "Missed call"
    if answered:
        return "Call dropped" if reason in ("tunnel_exit", "tunnel_error", "shutdown", "audio_error") else "Call ended"
    if reason == "remote_busy":
        return "Busy"
    if reason == "rejected":
        return "Declined"
    if reason in ("tunnel_spawn_error", "tunnel_exit", "tunnel_error", "shutdown"):
        return "Call failed"
    if reason == "local_hangup":
        return "Cancelled" if outgoing else "Declined"
    if reason == "remote_hangup":
        return "Declined" if outgoing else "Missed call"
    return "No answer" if outgoing else "Missed call"


def history_line(call: dict) -> str:
    """The line a finished call leaves in the conversation."""
    kind = "video call (voice only here)" if call.get("media") == "video" else "voice call"
    if call.get("connectedAt"):
        length = clock(max(0, int(call.get("endedAt") or 0) - int(call["connectedAt"])))
        return f"📞 {'Outgoing' if call.get('outgoing') else 'Incoming'} {kind} · {length}"
    label = end_label(call)
    if label == "Missed call":
        return f"📞 Missed {kind}"
    return f"📞 {'Outgoing' if call.get('outgoing') else 'Incoming'} {kind} · {label.lower()}"


def duration_ms(call: dict, now_ms: int) -> int:
    """How long the two sides have been talking; 0 before the call connects."""
    connected = int(call.get("connectedAt") or 0)
    if not connected:
        return 0
    end = int(call.get("endedAt") or 0) or now_ms
    return max(0, end - connected)


def share(part: Any, whole: Any) -> int:
    """``part`` of ``whole`` as a whole percentage; 0 when there is nothing to divide."""
    try:
        if whole <= 0 or part <= 0:
            return 0
        return max(0, min(100, int(part * 100 / whole + 0.5)))
    except TypeError:
        return 0


def burst(levels: list, slots: int) -> list[float]:
    """One side of a call as a burst of ``slots`` bars, each 0 to 1, that
    stays where it is: the louder the side, the taller the bars and the
    further out from the centre they reach. Nothing scrolls. Every bar gets
    its own share of the level, re-dealt whenever the level changes. Only the
    newest three levels count, so a word dies away instead of cutting off.

    The same arithmetic as ``burst`` in Calls.js, held to the shared vectors."""
    n = int(slots) if isinstance(slots, (int, float)) and not isinstance(slots, bool) else 0
    if n <= 0:
        return []
    vals = [v for v in levels if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]

    def at(back: int) -> float:
        i = len(vals) - back
        return max(0.0, min(1.0, vals[i])) if i >= 0 else 0.0

    a, b, c = at(1), at(2), at(3)
    energy = max(a, b * 0.7, c * 0.45)
    if energy <= 0.02:
        return [0.0] * n
    gain = min(1.0, energy * 1.7)
    spread = 0.3 + 0.7 * gain
    seed = math.floor(a * 997) + math.floor(b * 991) * 13 + len(vals) * 7
    mid, half = (n - 1) / 2, max(1.0, n / 2)
    out = []
    for i in range(n):
        d = abs(i - mid) / half / spread
        shape = 0.0 if d >= 1 else (1 - d * d) * (1 - d * d)
        x = (i * 374761 + seed * 668265 + 1) % 1000003
        x = (x * x) % 1000003
        x = (x * 48271 + 12345) % 1000003
        out.append(max(0.0, min(1.0, gain * shape * (0.4 + 0.6 * x / 1000003))))
    return out


def waveform(levels: list, width: int) -> str:
    """The same burst as ``width`` block characters, for the terminal client."""
    return "".join(_BARS[max(0, min(len(_BARS) - 1, int(v * len(_BARS))))] for v in burst(levels, width))


def readings(call: dict, now_ms: int) -> list[list[str]]:
    """The telemetry of a call as ``[label, value, tone]`` rows, most
    important first. ``tone`` is "" | "accent" | "alarm"."""
    rows: list[list[str]] = []
    state = call.get("state")
    tone = "accent" if state == CONNECTED else ("alarm" if state == RECONNECTING else "")
    rows.append(["STATE", state_label(call).upper(), tone])
    if call.get("connectedAt"):
        rows.append(["TIME", clock(duration_ms(call, now_ms)), ""])
    elif state != ENDED:
        rows.append(["RINGING", clock(max(0, now_ms - int(call.get("startedAt") or now_ms))), ""])
    rows.append(["LINK", "E2E SRTP" if call.get("encrypted") else "", "accent"])
    if call.get("setupMs"):
        rows.append(["SETUP", f"{int(call['setupMs']) / 1000:.1f}s", ""])
    lv = call.get("levels") if isinstance(call.get("levels"), dict) else {}
    talked = duration_ms(call, now_ms)
    if talked:
        rows.append(["YOU", f"{share(lv.get('txTalkMs', 0), talked)}% talking", ""])
        rows.append(["THEM", f"{share(lv.get('rxTalkMs', 0), talked)}% talking", ""])
    if call.get("muted"):
        rows.append(["MIC", "MUTED", "alarm"])
    if call.get("reconnects"):
        rows.append(["DROPS", str(call["reconnects"]), "alarm"])
    rows.append(["CODEC", "OPUS 48K", ""])
    if call.get("audioError"):
        rows.append(["AUDIO", "NOT CONNECTED", "alarm"])
    return [r for r in rows if r[1]]


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Side:
    level: float = 0.0
    peak: float = 0.0
    talk_ms: int = 0

    def add(self, level: float) -> None:
        self.level = level
        if level > self.peak:
            self.peak = level
        if level >= SPEECH_LEVEL:
            self.talk_ms += METER_FRAME_MS


@dataclass
class Call:
    id: str
    conversation: str
    name: str
    outgoing: bool
    state: str = RINGING
    media: str = "audio"              # what the caller asked for; only audio is carried
    started_at: int = 0
    connected_at: int = 0
    ended_at: int = 0
    reason: str = ""
    muted: bool = False
    reconnects: int = 0
    input_device: str = ""
    output_device: str = ""
    audio_error: str = ""
    tx: Side = field(default_factory=Side)
    rx: Side = field(default_factory=Side)
    audio: CallAudio | None = None
    recorded: bool = False

    def levels(self) -> dict:
        return {"tx": round(self.tx.level, 3), "rx": round(self.rx.level, 3),
                "txPeak": round(self.tx.peak, 3), "rxPeak": round(self.rx.peak, 3),
                "txTalkMs": self.tx.talk_ms, "rxTalkMs": self.rx.talk_ms}

    def to_json(self) -> dict:
        data = {
            "id": self.id, "conversation": self.conversation, "name": self.name, "outgoing": self.outgoing,
            "state": self.state, "media": self.media, "startedAt": self.started_at,
            "connectedAt": self.connected_at, "endedAt": self.ended_at, "reason": self.reason,
            "muted": self.muted, "reconnects": self.reconnects, "encrypted": True,
            "setupMs": max(0, self.connected_at - self.started_at) if self.connected_at else 0,
            "mic": self.audio.mic_in_use if self.audio else "", "speaker": self.audio.speaker_in_use if self.audio else "",
            "audioError": self.audio_error, "levels": self.levels(),
        }
        data["label"] = state_label(data)
        return data


def _now_ms() -> int:
    return int(time.time() * 1000)


def find_tunnel(configured: str) -> str:
    """The call tunnel's path, or "" when it is not installed.

    A systemd user unit's PATH rarely includes the places a user installs
    into, so those are searched as well."""
    name = configured or TUNNEL_NAME
    if "/" in name:
        path = Path(os.path.expanduser(name))
        return str(path) if path.is_file() and os.access(path, os.X_OK) else ""
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    for folder in (home / ".local/bin", home / ".cargo/bin", home / ".local/share/omarchy-signal/bin"):
        path = folder / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return ""


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in str(text).split("-")[0].split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def parse_call_message(params: Any) -> dict | None:
    """What a received envelope says about a call, or None.

    Returns ``{"id", "sender": Recipient, "offer": "audio"|"video"|"",
    "hangup": "NORMAL"|"ACCEPTED"|"DECLINED"|"BUSY"|""}``.
    """
    env = params.get("envelope") if isinstance(params, dict) else None
    msg = env.get("callMessage") if isinstance(env, dict) else None
    if not isinstance(msg, dict):
        return None
    number, uuid = env.get("sourceNumber"), env.get("sourceUuid")
    if isinstance(number, str) and E164.match(number):
        sender = Recipient("number", number)
    elif isinstance(uuid, str) and UUID.match(uuid):
        sender = Recipient("uuid", uuid.lower())
    else:
        return None
    offer = msg.get("offerMessage") if isinstance(msg.get("offerMessage"), dict) else None
    hangup = msg.get("hangupMessage") if isinstance(msg.get("hangupMessage"), dict) else None
    source = offer or hangup
    if source is None:
        return None
    call_id = call_id_text(source.get("id"))
    if not call_id:
        return None
    return {"id": call_id, "sender": sender,
            "offer": ("video" if str(offer.get("type", "")).upper().startswith("VIDEO") else "audio") if offer else "",
            "hangup": str(hangup.get("type", "NORMAL")).upper() if hangup else ""}


# --------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------

class CallManager:
    """Owned by the bridge. Every method runs on the bridge's event loop."""

    def __init__(self, bridge: Any, *, pactl: Pactl | None = None):
        self.bridge = bridge
        self.pactl = pactl or Pactl()
        self.calls: dict[str, Call] = {}
        self.tunnel = ""
        self.subscribed = False
        self.unavailable = "starting"
        self._media: dict[str, str] = {}            # call id -> media the caller offered
        self._pending_outgoing = ""                 # conversation key a startCall is in flight for
        self._levels_task: asyncio.Task | None = None
        self._prefs_path: Path = bridge.paths.data_dir / "calls.json"
        self.mic, self.speaker = self._load_prefs()

    # -- availability ---------------------------------------------------------

    def refresh_tunnel(self) -> str:
        self.tunnel = find_tunnel(self.bridge.cfg.call_tunnel) if self.bridge.cfg.calls else ""
        return self.tunnel

    def status(self) -> dict:
        return {"available": not self.unavailable, "reason": self.unavailable, "video": False,
                "active": [c.to_json() for c in self.calls.values() if c.state != ENDED]}

    async def on_ready(self, signal_cli_version: str) -> None:
        """signal-cli (re)started and an account is known: ask for call events."""
        self.subscribed = False
        await self._drop_all("shutdown")
        cfg = self.bridge.cfg
        if not cfg.calls:
            self.unavailable = "calls are switched off in the settings"
        elif not self.bridge.account:
            self.unavailable = "no Signal account is linked"
        elif version_tuple(signal_cli_version) and version_tuple(signal_cli_version) < MIN_SIGNAL_CLI:
            self.unavailable = "signal-cli is too old for calls (0.14.3 or newer is needed)"
        elif not self.tunnel:
            self.unavailable = "signal-call-tunnel is not installed (see the README, Calls)"
        elif self.pactl.available():
            self.unavailable = self.pactl.available()
        else:
            try:
                await self.bridge.supervisor.call("subscribeCallEvents", {"account": self.bridge.account}, timeout=30)
                self.subscribed = True
                self.unavailable = ""
            except (RpcError, RpcClosed) as exc:
                self.unavailable = "signal-cli does not support calls"
                log.warning("subscribeCallEvents failed: %s", getattr(exc, "message", exc))
        log.info("calls: %s", "ready" if not self.unavailable else self.unavailable)

    async def on_stopped(self) -> None:
        """signal-cli went away: every call it carried is gone with it."""
        self.subscribed = False
        self.unavailable = "signal-cli is not running"
        await self._drop_all("shutdown")

    async def shutdown(self) -> None:
        for call in list(self.calls.values()):
            if call.state != ENDED and self.subscribed:
                with contextlib.suppress(RpcError, RpcClosed, asyncio.TimeoutError):
                    await self._rpc("hangupCall", call, timeout=5)
        await self._drop_all("shutdown")

    async def _drop_all(self, reason: str) -> None:
        for call in list(self.calls.values()):
            if call.state != ENDED:
                await self._end(call, reason)

    # -- preferences ----------------------------------------------------------

    def _load_prefs(self) -> tuple[str, str]:
        try:
            data = json.loads(self._prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", ""
        if not isinstance(data, dict):
            return "", ""
        mic, speaker = data.get("mic", ""), data.get("speaker", "")
        return (mic if valid_device(mic) else "", speaker if valid_device(speaker) else "")

    def _save_prefs(self) -> None:
        try:
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._prefs_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mic": self.mic, "speaker": self.speaker}), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._prefs_path)
        except OSError as exc:
            log.warning("could not save the call devices: %s", exc)

    async def devices(self) -> dict:
        """Microphones and speakers, the system's defaults and what calls use.

        A saved choice that is unplugged is reported as not present and the
        default is used until it comes back."""
        found = await self.pactl.devices()
        mics = {d["name"] for d in found["inputs"]}
        speakers = {d["name"] for d in found["outputs"]}
        found["input"] = self.mic if self.mic in mics else ""
        found["output"] = self.speaker if self.speaker in speakers else ""
        found["inputMissing"] = bool(self.mic) and self.mic not in mics
        found["outputMissing"] = bool(self.speaker) and self.speaker not in speakers
        found["cameras"] = []
        return found

    async def set_device(self, kind: str, name: str) -> dict:
        if kind not in ("input", "output"):
            raise ValueError("kind must be input or output")
        if name and not valid_device(name):
            raise ValueError("unknown audio device")
        if name:
            found = await self.pactl.devices()
            if name not in {d["name"] for d in found["inputs" if kind == "input" else "outputs"]}:
                raise ValueError("unknown audio device")
        for call in self.calls.values():
            if call.audio is not None and call.state != ENDED:
                try:
                    await (call.audio.set_mic(name) if kind == "input" else call.audio.set_speaker(name))
                    call.audio_error = ""
                except AudioError as exc:
                    call.audio_error = str(exc)
                    if kind == "input" and isinstance(exc, AudioDisconnectError):
                        await self._fail_closed_audio(call)
                    else:
                        await self._announce(call)
                    raise
                await self._announce(call)
        if kind == "input":
            self.mic = name
        else:
            self.speaker = name
        self._save_prefs()
        return await self.devices()

    # -- requests from clients --------------------------------------------------

    def _active(self) -> Call | None:
        for call in self.calls.values():
            if call.state != ENDED:
                return call
        return None

    def _get(self, call_id: str) -> Call:
        call = self.calls.get(call_id_text(call_id))
        if call is None or call.state == ENDED:
            raise ValueError("that call is over")
        return call

    def _require(self) -> None:
        if self.unavailable:
            raise protocol.ProtocolError(f"calls are not available: {self.unavailable}")

    async def _rpc(self, method: str, call: Call, *, timeout: float = 30) -> Any:
        return await self.bridge.supervisor.call(
            method, {"account": self.bridge.account, "callId": call_id_signed(call.id)}, timeout=timeout)

    async def start(self, conversation: str) -> dict:
        self._require()
        rec = parse_conversation_key(conversation)
        if rec.kind not in ("number", "uuid"):
            raise ValueError("group calls are not supported; call one person")
        if rec.kind == "number" and rec.value == self.bridge.account:
            raise ValueError("you cannot call yourself")
        if self._active() is not None:
            raise ValueError("you are already in a call")
        self._pending_outgoing = rec.key
        try:
            result = await self.bridge.supervisor.call(
                "startCall", {"account": self.bridge.account, "recipient": [rec.value]}, timeout=45)
        finally:
            self._pending_outgoing = ""
        if not isinstance(result, dict) or not call_id_text(result.get("callId")):
            raise ValueError("signal-cli did not start the call")
        # The RINGING_OUTGOING event usually arrives before this answer; when
        # it has not, the answer creates the record.
        call = self._upsert(call_id_text(result.get("callId")), rec.key, outgoing=True)
        self._devices_from(call, result)
        await self._announce(call)
        return call.to_json()

    async def accept(self, call_id: str) -> dict:
        self._require()
        call = self._get(call_id)
        if call.outgoing or call.state != RINGING:
            raise ValueError("that call is not ringing")
        result = await self._rpc("acceptCall", call)
        if isinstance(result, dict):
            self._devices_from(call, result)
        if call.state == RINGING:
            call.state = CONNECTING
        await self._announce(call)
        return call.to_json()

    async def reject(self, call_id: str) -> dict:
        self._require()
        call = self._get(call_id)
        if call.outgoing or call.state != RINGING:
            raise ValueError("that call is not ringing")
        await self._rpc("rejectCall", call)
        await self._end(call, "rejected")
        return call.to_json()

    async def hangup(self, call_id: str) -> dict:
        call = self._get(call_id)
        try:
            await self._rpc("hangupCall", call)
        except RpcError as exc:
            # signal-cli has already forgotten the call: it is over either way.
            log.info("hangupCall: %s", exc.message)
        await self._end(call, "local_hangup")
        return call.to_json()

    async def mute(self, call_id: str, muted: bool) -> dict:
        call = self._get(call_id)
        if call.audio is not None:
            try:
                await call.audio.set_muted(muted)
                call.audio_error = ""
            except AudioError as exc:
                call.audio_error = str(exc)
                if isinstance(exc, AudioDisconnectError):
                    await self._fail_closed_audio(call)
                else:
                    await self._announce(call)
                raise
        call.muted = muted
        await self._announce(call)
        return call.to_json()

    def list(self) -> list[dict]:
        return [c.to_json() for c in self.calls.values()]

    # -- events from signal-cli ----------------------------------------------------

    def _conversation_for(self, event: dict, outgoing: bool) -> str:
        if outgoing and self._pending_outgoing:
            return self._pending_outgoing
        number, uuid = event.get("number"), event.get("uuid")
        if isinstance(number, str) and E164.match(number):
            return f"number:{number}"
        if isinstance(uuid, str) and UUID.match(uuid):
            return f"uuid:{uuid.lower()}"
        return ""

    def _upsert(self, call_id: str, conversation: str, *, outgoing: bool) -> Call:
        call = self.calls.get(call_id)
        if call is None:
            call = Call(id=call_id, conversation=conversation, name=self.bridge.store.display_name(conversation),
                        outgoing=outgoing, started_at=_now_ms(), media=self._media.pop(call_id, "audio"))
            self.calls[call_id] = call
        return call

    @staticmethod
    def _devices_from(call: Call, data: dict) -> None:
        for attr, key in (("input_device", "inputDeviceName"), ("output_device", "outputDeviceName")):
            value = data.get(key)
            if valid_device(value):
                setattr(call, attr, value)

    async def on_call_event(self, params: Any) -> None:
        event = params.get("result") if isinstance(params, dict) else None
        if not isinstance(event, dict):
            return
        call_id = call_id_text(event.get("callId"))
        state = _STATES.get(str(event.get("state")))
        if not call_id or state is None:
            return
        outgoing = bool(event.get("isOutgoing"))
        call = self.calls.get(call_id)
        if call is None:
            if state == ENDED:
                return
            conversation = self._conversation_for(event, outgoing)
            if not conversation:
                return
            busy = self._active()
            call = self._upsert(call_id, conversation, outgoing=outgoing)
            if busy is not None and not outgoing:
                # Already talking: tell the caller so (signal-cli sends "busy")
                # and leave a missed call behind.
                with contextlib.suppress(RpcError, RpcClosed):
                    await self._rpc("rejectCall", call)
                await self._end(call, "busy_here")
                return
        if call.state == ENDED:
            return
        self._devices_from(call, event)
        if state == ENDED:
            await self._end(call, str(event.get("reason") or ""))
            return
        if state == RECONNECTING and call.state != RECONNECTING:
            call.reconnects += 1
        if state == CONNECTED and not call.connected_at:
            call.connected_at = _now_ms()
        # signal-cli reports "connecting" for an accepted call that the bridge
        # may already have marked; never step back from connected to ringing.
        if not (state == RINGING and call.state != RINGING):
            call.state = state
        if state == CONNECTED:
            await self._start_audio(call)
        await self._announce(call)

    async def on_receive(self, params: Any) -> None:
        """A received envelope: note what kind of call is being offered, and
        stop ringing when another of your devices took or refused the call."""
        info = parse_call_message(params)
        if info is None:
            return
        if info["offer"]:
            call = self.calls.get(info["id"])
            if call is not None:
                if call.media != info["offer"]:
                    call.media = info["offer"]
                    await self._announce(call)
            else:
                self._media[info["id"]] = info["offer"]
                while len(self._media) > 16:
                    self._media.pop(next(iter(self._media)))
        elif info["hangup"] in ("ACCEPTED", "DECLINED", "BUSY"):
            call = self.calls.get(info["id"])
            if call is not None and not call.outgoing and call.state == RINGING:
                # signal-cli keeps its side until the ring times out. Hanging it
                # up here would tell the caller to end a call your phone just
                # answered, so only this computer stops ringing.
                await self._end(call, "elsewhere")

    # -- audio and levels -----------------------------------------------------------

    async def _start_audio(self, call: Call) -> None:
        if call.audio is not None:
            return

        def on_level(side: str, level: float) -> None:
            (call.tx if side == "tx" else call.rx).add(level)

        try:
            call.audio = CallAudio(self.pactl, call.input_device, call.output_device, mic=self.mic,
                                   speaker=self.speaker, on_level=on_level)
            call.audio.muted = call.muted
            await call.audio.start()
            call.audio_error = ""
        except AudioError as exc:
            call.audio_error = str(exc)
            log.warning("call audio: %s", exc)
        if self._levels_task is None or self._levels_task.done():
            self._levels_task = asyncio.create_task(self._levels_loop(), name="call-levels")

    async def _levels_loop(self) -> None:
        while any(c.audio is not None and c.state != ENDED for c in self.calls.values()):
            for call in list(self.calls.values()):
                if call.audio is not None and call.state != ENDED:
                    await self.bridge._broadcast("callLevels", {"id": call.id, **call.levels()})
            await asyncio.sleep(LEVELS_INTERVAL_S)

    async def _fail_closed_audio(self, call: Call) -> None:
        """End a call whose live microphone loopback could not be removed."""
        try:
            await self._rpc("hangupCall", call, timeout=5)
        except (RpcError, RpcClosed, asyncio.TimeoutError):
            # Losing signal-cli also stops the media session it carries.
            await self.bridge.supervisor.restart()
        await self._end(call, "audio_error")

    # -- ending -----------------------------------------------------------------------

    async def _end(self, call: Call, reason: str) -> None:
        if call.state == ENDED:
            return
        call.state = ENDED
        call.reason = reason
        call.ended_at = _now_ms()
        if call.audio is not None:
            with contextlib.suppress(Exception):
                await call.audio.stop()
            call.audio = None
        data = call.to_json()
        await self._record(call, data)
        await self.bridge._broadcast("call", data)
        loop = asyncio.get_running_loop()
        loop.call_later(ENDED_LINGER_S, self.calls.pop, call.id, None)

    async def _record(self, call: Call, data: dict) -> None:
        """Leave one line in the conversation, as Signal's own clients do."""
        if call.recorded:
            return
        call.recorded = True
        bridge = self.bridge
        try:
            rec = parse_conversation_key(call.conversation)
        except InvalidRecipient:
            return
        text = history_line(data)
        conv = bridge.store.conversation(rec.key)
        expires = conv.expiration if conv else 0
        ts = call.ended_at
        missed = (not call.outgoing) and not call.connected_at and data["label"] == "Missed call"
        if call.outgoing:
            bridge.store.add_outgoing(rec, ts, f"number:{bridge.account}", text, status="", expires_in=expires)
        else:
            ev = Event(kind="message", conversation=rec, timestamp=ts, sender=rec, text=text, expires_in=expires)
            bridge.store.add_event_message(ev, count_unread=missed)
        message = bridge.store.message(rec.key, ts)
        if message is not None:
            payload = message.to_json()
            await bridge._broadcast("message", {
                "conversation": rec.key, "conversationName": call.name, "isGroup": False,
                "sender": payload["sender"], "senderName": call.name if not call.outgoing else "", "ts": ts,
                "text": text, "preview": text, "attachments": [], "outgoing": call.outgoing, "muted": False,
                "quoteText": "", "expiresIn": expires, "viewOnce": False, "notify": False, "call": True})
            await bridge._broadcast("unread", {"total": bridge.store.total_unread()})

    async def _announce(self, call: Call) -> None:
        await self.bridge._broadcast("call", call.to_json())
