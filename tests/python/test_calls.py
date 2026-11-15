# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal call tests
## Date Created: 2026-11-14
## Description: Tests for voice calls: labels and telemetry, audio device parsing, and the bridge's call flow against the fake signal-cli.
"""Calls: the pure figures (held to tests/vectors/calls.json together with
Calls.js), the audio helpers, and the whole flow through a real bridge with a
fake signal-cli and a fake sound server. No real audio device is touched."""

import array
import asyncio
import json
import math
import os
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import audio, calls
from omarchy_signal.client import BridgeClient, BridgeError
from omarchy_signal.term import str_width
from test_bridge_integration import BridgeHarness, incoming

VECTORS = json.loads((Path(__file__).parents[1] / "vectors" / "calls.json").read_text(encoding="utf-8"))
TRINITY = "number:+15550002222"


class FigureTests(unittest.TestCase):
    def test_vectors(self):
        for value, want in VECTORS["clock"]:
            self.assertEqual(calls.clock(value), want, value)
        for row in VECTORS["labels"]:
            self.assertEqual(calls.state_label(row["call"]), row["state"], row["call"])
            self.assertEqual(calls.end_label(row["call"]), row["end"], row["call"])
            self.assertEqual(calls.history_line(row["call"]), row["history"], row["call"])
        for part, whole, want in VECTORS["share"]:
            self.assertEqual(calls.share(part, whole), want, (part, whole))
        for levels, width, want in VECTORS["waveform"]:
            self.assertEqual(calls.waveform(levels, width), want, (levels, width))
        for levels, slots, want in VECTORS["burst"]:
            got = calls.burst(levels, slots)
            self.assertEqual(len(got), len(want), (levels, slots))
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=9)
        quiet, loud = calls.burst([0.3] * 3, 48), calls.burst([0.8] * 3, 48)
        self.assertGreater(sum(loud), sum(quiet) * 2)                       # louder is taller
        self.assertGreater(len([v for v in loud if v > 0.02]), len([v for v in quiet if v > 0.02]) + 8)  # and wider
        self.assertEqual(sum(calls.burst([0.9, 0, 0, 0], 48)), 0)           # a pause falls flat
        for row in VECTORS["readings"]:
            self.assertEqual(calls.readings(row["call"], row["now"]), row["rows"], row["call"])

    def test_call_ids_survive_every_spelling(self):
        # signal-cli: signed in callEvent, unsigned inside a received call message.
        self.assertEqual(calls.call_id_text(-42), str(2**64 - 42))
        self.assertEqual(calls.call_id_text(str(2**64 - 42)), str(2**64 - 42))
        self.assertEqual(calls.call_id_signed(str(2**64 - 42)), -42)
        self.assertEqual(calls.call_id_signed(calls.call_id_text(2**63 - 1)), 2**63 - 1)
        self.assertEqual(calls.call_id_signed(calls.call_id_text(-(2**63))), -(2**63))
        self.assertEqual(calls.call_id_text(7), "7")
        for bad in (True, None, 1.5, "", "abc", "1-2", "9" * 30, 2**64, -(2**63) - 1, [1]):
            self.assertEqual(calls.call_id_text(bad), "", bad)

    def test_call_message_parsing(self):
        env = {"envelope": {"sourceNumber": "+15550002222", "sourceUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "callMessage": {"offerMessage": {"id": 2**64 - 42, "type": "VIDEO_CALL", "opaque": "x"}}}}
        info = calls.parse_call_message(env)
        self.assertEqual((info["id"], info["sender"].key, info["offer"], info["hangup"]),
                         (str(2**64 - 42), TRINITY, "video", ""))
        env["envelope"]["callMessage"] = {"hangupMessage": {"id": 5, "type": "ACCEPTED", "deviceId": 1}}
        self.assertEqual(calls.parse_call_message(env)["hangup"], "ACCEPTED")
        for junk in (None, [], {}, {"envelope": 3}, {"envelope": {"callMessage": "x"}},
                     {"envelope": {"sourceNumber": "nope", "callMessage": {"offerMessage": {"id": 1}}}},
                     {"envelope": {"sourceNumber": "+15550002222", "callMessage": {"offerMessage": {"id": "x"}}}},
                     {"envelope": {"sourceNumber": "+15550002222", "callMessage": {"iceUpdateMessages": []}}}):
            self.assertIsNone(calls.parse_call_message(junk), junk)

    def test_signal_cli_version_gate(self):
        self.assertEqual(calls.version_tuple("0.14.10"), (0, 14, 10))
        self.assertEqual(calls.version_tuple("0.14.6-fake"), (0, 14, 6))
        self.assertLess(calls.version_tuple("0.14.2"), calls.MIN_SIGNAL_CLI)
        self.assertEqual(calls.version_tuple("garbage"), ())


class AudioTests(unittest.TestCase):
    def test_level_is_decibels_not_amplitude(self):
        def tone(amplitude, n=400):
            return array.array("h", [int(amplitude * math.sin(i / 3)) for i in range(n)]).tobytes()
        self.assertEqual(audio.level_from_pcm(b""), 0.0)
        self.assertEqual(audio.level_from_pcm(b"\x00" * 800), 0.0)
        self.assertEqual(audio.level_from_pcm(b"\x01"), 0.0)            # half a sample
        quiet, speech, loud = (audio.level_from_pcm(tone(a)) for a in (100, 1500, 30000))
        self.assertTrue(0 < quiet < audio.SPEECH_LEVEL < speech < loud <= 1.0, (quiet, speech, loud))
        self.assertGreater(loud, 0.9)

    def test_devices_leave_out_monitors_and_the_calls_own(self):
        sources = json.dumps([
            {"name": "alsa_input.usb-mic", "description": "Desk   Microphone", "mute": False, "properties": {}},
            {"name": "alsa_output.speakers.monitor", "description": "Monitor of Speakers", "properties": {"device.class": "monitor"}},
            {"name": "signal_input_4242", "description": "signal_input_4242"},
            {"name": "sink_for_signal_input_4242.monitor", "description": "x"},
            {"name": "bluez_input.headset", "description": "Headset", "mute": True},
            {"name": "bad name; rm -rf", "description": "nope"},
            "junk", {"description": "no name"}])
        got = audio.parse_devices(sources, kind="input", default="bluez_input.headset")
        self.assertEqual([(d["name"], d["description"], d["default"], d["muted"]) for d in got],
                         [("bluez_input.headset", "Headset", True, True),
                          ("alsa_input.usb-mic", "Desk Microphone", False, False)])
        sinks = json.dumps([{"name": "alsa_output.speakers", "description": "Speakers"},
                            {"name": "signal_output_4242", "description": "x"},
                            {"name": "sink_for_signal_input_4242", "description": "x"}])
        self.assertEqual([d["name"] for d in audio.parse_devices(sinks, kind="output")], ["alsa_output.speakers"])
        self.assertEqual(audio.parse_devices("not json", kind="input"), [])
        self.assertEqual(audio.parse_devices("{}", kind="input"), [])

    def test_device_names_cannot_smuggle_module_arguments(self):
        for bad in ("a b", "x sink=other", 'a"b', "", None, 5, "a/b", "x" * 300, "a\nb"):
            self.assertFalse(audio.valid_device(bad), bad)
        self.assertTrue(audio.valid_device("alsa_input.usb-Samson_G-Track-00.analog-stereo"))
        self.assertTrue(audio.valid_device("bluez_output.AA_BB_CC:1.monitor"))


class FakePactl(audio.Pactl):
    """Records what would be asked of the sound server."""

    def __init__(self):
        super().__init__()
        self.loaded: dict[int, tuple[str, str]] = {}
        self.unloaded: list[int] = []
        self.recording: list[str] = []
        self.next = 100
        self.inputs = [{"name": "mic.desk", "description": "Desk mic", "default": True, "muted": False},
                       {"name": "mic.headset", "description": "Headset", "default": False, "muted": False}]
        self.outputs = [{"name": "out.speakers", "description": "Speakers", "default": True, "muted": False},
                        {"name": "out.headset", "description": "Headset", "default": False, "muted": False}]

    def available(self):
        return ""

    async def default(self, kind):
        return "mic.desk" if kind == "input" else "out.speakers"

    async def devices(self):
        return {"inputs": [dict(d) for d in self.inputs], "outputs": [dict(d) for d in self.outputs],
                "defaultInput": "mic.desk", "defaultOutput": "out.speakers"}

    async def loopback(self, source, sink):
        if not (audio.valid_device(source) and audio.valid_device(sink)):
            raise audio.AudioError("invalid audio device name")
        self.next += 1
        self.loaded[self.next] = (source, sink)
        return self.next

    async def unload(self, module):
        self.unloaded.append(module)
        self.loaded.pop(module, None)

    async def record(self, source):
        self.recording.append(source)
        raise audio.AudioError("no meter in tests")


def call_event(state, call_id=-42, outgoing=False, number="+15550002222", reason=None, devices=True):
    result = {"callId": call_id, "state": state, "number": number, "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
              "isOutgoing": outgoing}
    if devices:
        result.update(inputDeviceName="signal_input_1", outputDeviceName="signal_output_1")
    if reason:
        result["reason"] = reason
    return {"__method": "callEvent", "subscription": 0, "result": result}


def call_message(kind, call_id, number="+15550002222", **fields):
    return {"envelope": {"source": number, "sourceNumber": number, "sourceUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                         "sourceDevice": 1, "timestamp": 1,
                         "callMessage": {kind: {"id": call_id, **fields}}}}


ID = str(2**64 - 42)          # what -42 is called on the wire to clients


class CallHarness(BridgeHarness):
    """A bridge that believes the call tunnel is installed and has a sound server."""

    def __init__(self, **cfg_over):
        super().__init__(**cfg_over)
        tunnel = Path(self.tmp.name) / "signal-call-tunnel"
        tunnel.write_text("#!/bin/sh\nexit 0\n")
        tunnel.chmod(tunnel.stat().st_mode | stat.S_IXUSR)
        self.bridge.cfg.call_tunnel = str(tunnel)
        self.pactl = FakePactl()
        self.bridge.calls.pactl = self.pactl

    async def __aenter__(self):
        await super().__aenter__()
        for _ in range(100):
            if self.bridge.calls.subscribed:
                break
            await asyncio.sleep(0.05)
        return self


class CallFlowTests(unittest.IsolatedAsyncioTestCase):
    async def _client(self, h):
        events = []
        c = BridgeClient(h.paths, on_event=lambda name, data: events.append((name, data)))
        await c.connect()
        await c.request("subscribe")
        return c, events

    async def _until(self, events, pred, what="event"):
        for _ in range(200):
            for name, data in events:
                if pred(name, data):
                    return data
            await asyncio.sleep(0.02)
        self.fail(f"no {what}; saw {[(n, (d or {}).get('state')) for n, d in events if n == 'call']}")

    async def test_status_says_whether_calls_work_and_why_not(self):
        async with CallHarness() as h:
            status = h.bridge.status()["calls"]
            self.assertEqual((status["available"], status["reason"], status["video"]), (True, "", False))
            self.assertEqual(h.bridge.supervisor.environment()["SIGNAL_CALL_TUNNEL_BIN"], h.bridge.cfg.call_tunnel)
            self.assertIn({"method": "subscribeCallEvents", "params": {"account": "+15550001111"}}, h.sent())
        # No tunnel anywhere: not on PATH, and not in a home that has one installed.
        empty = tempfile.TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        nowhere = mock.patch.object(calls, "find_tunnel", wraps=lambda name: _find_in(empty.name, name))
        with nowhere:
            await self._status_without_a_tunnel()

    async def _status_without_a_tunnel(self):
        async with BridgeHarness() as h:
            status = h.bridge.status()["calls"]
            self.assertFalse(status["available"])
            self.assertIn("signal-call-tunnel", status["reason"])
            self.assertNotIn("SIGNAL_CALL_TUNNEL_BIN", h.bridge.supervisor.environment())
            self.assertFalse([s for s in h.sent() if s["method"] == "subscribeCallEvents"])
            c = BridgeClient(h.paths)
            await c.connect()
            with self.assertRaises(BridgeError) as ctx:
                await c.request("callStart", conversation=TRINITY)
            self.assertIn("not available", str(ctx.exception))
            await c.close()
        async with CallHarness(calls=False) as h:
            self.assertIn("switched off", h.bridge.status()["calls"]["reason"])

    async def test_outgoing_call_connects_audio_mutes_and_ends(self):
        async with CallHarness() as h:
            c, events = await self._client(h)
            started = await c.request("callStart", conversation=TRINITY)
            self.assertEqual((started["id"], started["state"], started["outgoing"], started["name"], started["label"]),
                             (ID, "ringing", True, "Trinity", "Calling"))
            self.assertIn({"method": "startCall", "params": {"account": "+15550001111", "recipient": ["+15550002222"]}}, h.sent())
            # One call at a time.
            with self.assertRaises(BridgeError):
                await c.request("callStart", conversation="number:+15550003333")
            self.assertFalse(h.pactl.loaded)                 # no microphone while it only rings

            h.inject(call_event("CONNECTED", outgoing=True))
            connected = await self._until(events, lambda n, d: n == "call" and d["state"] == "connected", "connected")
            self.assertEqual(sorted(h.pactl.loaded.values()),
                             [("mic.desk", "sink_for_signal_input_1"), ("signal_output_1.monitor", "out.speakers")])
            self.assertEqual((connected["mic"], connected["speaker"], connected["encrypted"]), ("mic.desk", "out.speakers", True))
            self.assertGreater(connected["connectedAt"], 0)
            self.assertEqual(h.bridge.status()["calls"]["active"][0]["id"], ID)

            muted = await c.request("callMute", call=ID, muted=True)
            self.assertTrue(muted["muted"])
            self.assertEqual(list(h.pactl.loaded.values()), [("signal_output_1.monitor", "out.speakers")])   # mic taken off
            await c.request("callMute", call=ID, muted=False)
            self.assertIn(("mic.desk", "sink_for_signal_input_1"), h.pactl.loaded.values())

            h.inject(call_event("RECONNECTING", outgoing=True))
            await self._until(events, lambda n, d: n == "call" and d["state"] == "reconnecting", "reconnecting")
            h.inject(call_event("CONNECTED", outgoing=True))
            back = await self._until(events, lambda n, d: n == "call" and d["state"] == "connected" and d["reconnects"] == 1, "reconnect")
            self.assertEqual(len(h.pactl.loaded), 2)         # audio was not wired twice

            ended = await c.request("callHangup", call=ID)
            self.assertEqual((ended["state"], ended["label"]), ("ended", "Call ended"))
            self.assertFalse(h.pactl.loaded)                 # microphone and speaker released
            self.assertIn({"method": "hangupCall", "params": {"account": "+15550001111", "callId": -42}}, h.sent())
            history = await c.request("history", conversation=TRINITY)
            self.assertTrue(history[-1]["body"].startswith("📞 Outgoing voice call · 0:0"), history[-1]["body"])
            self.assertTrue(history[-1]["outgoing"])
            with self.assertRaises(BridgeError):
                await c.request("callHangup", call=ID)         # already over
            self.assertEqual(back["id"], ID)
            await c.close()

    async def test_incoming_call_accept_and_remote_hangup(self):
        async with CallHarness() as h:
            c, events = await self._client(h)
            h.inject(call_message("offerMessage", 2**64 - 42, type="VIDEO_CALL", opaque="AAAA"))
            h.inject(call_event("RINGING_INCOMING"))
            ringing = await self._until(events, lambda n, d: n == "call" and d["state"] == "ringing", "ringing")
            self.assertEqual((ringing["id"], ringing["outgoing"], ringing["conversation"], ringing["name"], ringing["media"], ringing["label"]),
                             (ID, False, TRINITY, "Trinity", "video", "Incoming call"))
            with self.assertRaises(BridgeError):
                await c.request("callStart", conversation="number:+15550003333")
            accepted = await c.request("callAccept", call=ID)
            self.assertEqual(accepted["state"], "connecting")
            self.assertIn({"method": "acceptCall", "params": {"account": "+15550001111", "callId": -42}}, h.sent())
            with self.assertRaises(BridgeError):
                await c.request("callReject", call=ID)         # no longer ringing
            h.inject(call_event("CONNECTING"))
            h.inject(call_event("CONNECTED"))
            await self._until(events, lambda n, d: n == "call" and d["state"] == "connected", "connected")
            self.assertEqual(len(h.pactl.loaded), 2)
            h.inject(call_event("ENDED", reason="remote_hangup"))
            ended = await self._until(events, lambda n, d: n == "call" and d["state"] == "ended", "ended")
            self.assertEqual(ended["label"], "Call ended")
            self.assertFalse(h.pactl.loaded)
            history = await c.request("history", conversation=TRINITY)
            self.assertIn("Incoming video call (voice only here) · 0:0", history[-1]["body"])
            convs = {x["key"]: x for x in await c.request("conversations")}
            self.assertEqual(convs[TRINITY]["unread"], 0)    # an answered call is not unread
            await c.close()

    async def test_missed_declined_and_answered_elsewhere(self):
        async with CallHarness() as h:
            c, events = await self._client(h)
            # Missed: the caller gives up.
            h.inject(call_event("RINGING_INCOMING", call_id=1))
            await self._until(events, lambda n, d: n == "call" and d["id"] == "1" and d["state"] == "ringing")
            h.inject(call_event("ENDED", call_id=1, reason="remote_hangup"))
            missed = await self._until(events, lambda n, d: n == "call" and d["id"] == "1" and d["state"] == "ended")
            self.assertEqual(missed["label"], "Missed call")
            convs = {x["key"]: x for x in await c.request("conversations")}
            self.assertEqual((convs[TRINITY]["unread"], convs[TRINITY]["preview"]), (1, "📞 Missed voice call"))
            # Declined here.
            h.inject(call_event("RINGING_INCOMING", call_id=2))
            await self._until(events, lambda n, d: n == "call" and d["id"] == "2" and d["state"] == "ringing")
            declined = await c.request("callReject", call="2")
            self.assertEqual(declined["label"], "Declined")
            self.assertIn({"method": "rejectCall", "params": {"account": "+15550001111", "callId": 2}}, h.sent())
            # Answered on the phone: stop ringing here, and never hang up on the caller.
            h.inject(call_event("RINGING_INCOMING", call_id=3))
            await self._until(events, lambda n, d: n == "call" and d["id"] == "3" and d["state"] == "ringing")
            before = len(h.sent())
            h.inject(call_message("hangupMessage", 3, type="ACCEPTED", deviceId=1))
            elsewhere = await self._until(events, lambda n, d: n == "call" and d["id"] == "3" and d["state"] == "ended")
            self.assertEqual(elsewhere["label"], "Answered on another device")
            h.inject(call_event("ENDED", call_id=3, reason="ring_timeout"))      # signal-cli's own late timeout
            await asyncio.sleep(0.3)
            self.assertEqual([s["method"] for s in h.sent()[before:]], [])
            history = await c.request("history", conversation=TRINITY)
            self.assertEqual([m["body"] for m in history[-3:]],
                             ["📞 Missed voice call", "📞 Incoming voice call · declined",
                              "📞 Incoming voice call · answered on another device"])
            convs = {x["key"]: x for x in await c.request("conversations")}
            self.assertEqual(convs[TRINITY]["unread"], 1)    # only the missed one
            await c.close()

    async def test_second_caller_gets_busy_while_you_talk(self):
        async with CallHarness() as h:
            c, events = await self._client(h)
            h.inject(call_event("RINGING_INCOMING", call_id=1))
            await self._until(events, lambda n, d: n == "call" and d["id"] == "1")
            h.inject(call_event("RINGING_INCOMING", call_id=9, number="+15550003333"))
            other = await self._until(events, lambda n, d: n == "call" and d["id"] == "9" and d["state"] == "ended")
            self.assertEqual(other["label"], "Missed call")
            self.assertIn({"method": "rejectCall", "params": {"account": "+15550001111", "callId": 9}}, h.sent())
            self.assertEqual([x["id"] for x in h.bridge.status()["calls"]["active"]], ["1"])
            await c.close()

    async def test_devices_can_be_listed_and_changed_mid_call(self):
        async with CallHarness() as h:
            c, events = await self._client(h)
            devices = await c.request("callDevices")
            self.assertEqual((devices["defaultInput"], devices["defaultOutput"], devices["input"], devices["output"]),
                             ("mic.desk", "out.speakers", "", ""))
            self.assertEqual([d["name"] for d in devices["inputs"]], ["mic.desk", "mic.headset"])
            for bad in ({"kind": "camera", "name": "x"}, {"kind": "input", "name": "not.there"},
                        {"kind": "input", "name": "a b sink=evil"}):
                with self.assertRaises(BridgeError):
                    await c.request("callSetDevice", **bad)
            await c.request("callStart", conversation=TRINITY)
            h.inject(call_event("CONNECTED", outgoing=True))
            await self._until(events, lambda n, d: n == "call" and d["state"] == "connected")
            after = await c.request("callSetDevice", kind="input", name="mic.headset")
            self.assertEqual(after["input"], "mic.headset")
            await c.request("callSetDevice", kind="output", name="out.headset")
            self.assertEqual(sorted(h.pactl.loaded.values()),
                             [("mic.headset", "sink_for_signal_input_1"), ("signal_output_1.monitor", "out.headset")])
            self.assertEqual(json.loads((h.paths.data_dir / "calls.json").read_text()), {"mic": "mic.headset", "speaker": "out.headset"})
            self.assertEqual(stat.S_IMODE((h.paths.data_dir / "calls.json").stat().st_mode), 0o600)
            # Unplugged: the choice is kept, reported missing, and the default is used.
            h.pactl.inputs = h.pactl.inputs[:1]
            gone = await c.request("callDevices")
            self.assertEqual((gone["input"], gone["inputMissing"]), ("", True))
            # Back to the system default.
            await c.request("callSetDevice", kind="input", name="")
            self.assertIn(("mic.desk", "sink_for_signal_input_1"), h.pactl.loaded.values())
            await c.request("callHangup", call=ID)
            await c.close()

    async def test_what_cannot_be_called(self):
        async with CallHarness() as h:
            c, _ = await self._client(h)
            for conv, why in ((f"group:{'Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=='}", "group"),
                              ("number:+15550001111", "yourself"), ("nonsense", "")):
                with self.assertRaises(BridgeError) as ctx:
                    await c.request("callStart", conversation=conv)
                self.assertIn(why, str(ctx.exception))
            for op in ("callAccept", "callReject", "callHangup"):
                with self.assertRaises(BridgeError):
                    await c.request(op, call="12345")
            with self.assertRaises(BridgeError):
                await c.request("callHangup", call="not a number")
            self.assertFalse([s for s in h.sent() if s["method"] in ("startCall", "acceptCall", "rejectCall", "hangupCall")])
            # Hostile or broken events are dropped without a record.
            for junk in ({"__method": "callEvent"}, {"__method": "callEvent", "result": "x"},
                         call_event("WARP_SPEED"), call_event("RINGING_INCOMING", call_id="x"),
                         call_event("RINGING_INCOMING", number="nope") | {"result": {"callId": 5, "state": "RINGING_INCOMING", "number": "nope"}}):
                h.inject(junk)
            await asyncio.sleep(0.4)
            self.assertEqual(await c.request("calls"), [])
            await c.close()

    async def test_a_call_dies_with_signal_cli(self):
        os.environ["FAKE_SIGNAL_CRASH_ON_EOF"] = "1"
        try:
            async with CallHarness() as h:
                c, events = await self._client(h)
                await c.request("callStart", conversation=TRINITY)
                h.inject(call_event("CONNECTED", outgoing=True))
                await self._until(events, lambda n, d: n == "call" and d["state"] == "connected")
                h.bridge.supervisor.proc.kill()
                ended = await self._until(events, lambda n, d: n == "call" and d["state"] == "ended", "ended after crash")
                self.assertEqual(ended["label"], "Call dropped")
                self.assertFalse(h.pactl.loaded)             # the microphone is never left attached
                # signal-cli comes back within a second or two: calls must work again.
                before = len([x for x in h.sent() if x["method"] == "subscribeCallEvents"])
                for _ in range(200):
                    if len([x for x in h.sent() if x["method"] == "subscribeCallEvents"]) > before and h.bridge.calls.subscribed:
                        break
                    await asyncio.sleep(0.05)
                self.assertTrue(h.bridge.calls.subscribed)
                self.assertTrue(h.bridge.status()["calls"]["available"])
                await c.close()
        finally:
            os.environ.pop("FAKE_SIGNAL_CRASH_ON_EOF", None)


if __name__ == "__main__":
    unittest.main()


class TerminalClientCallTests(unittest.IsolatedAsyncioTestCase):
    """The terminal client's call line and keys, with no bridge behind it."""

    def _app(self, cols=120):
        from test_tui_render import make_app, seed
        app = make_app(cols=cols)
        seed(app)
        app.status["calls"] = {"available": True, "reason": "", "active": []}
        app.sent = []

        class Client:
            async def request(_self, op, timeout=0, **params):
                app.sent.append((op, params))
                return {}
        app.client = Client()
        return app

    @staticmethod
    def _call(**kw):
        base = {"id": ID, "conversation": TRINITY, "name": "Trin\x1bity", "outgoing": False, "state": "ringing",
                "media": "audio", "startedAt": 1, "connectedAt": 0, "endedAt": 0, "reason": "", "muted": False}
        base.update(kw)
        return base

    @staticmethod
    def _plain(text):
        import re
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)

    async def _key(self, app, char):
        from omarchy_signal.term import Key
        await app._handle_keys([Key("char", char=char, alt=True)])
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_ringing_answer_talk_mute_hangup(self):
        app = self._app()
        await app._on_event("call", self._call())
        self.assertIn("is calling", app.toast)
        line = self._plain(app._call_line(100))
        self.assertIn("INCOMING CALL", line)
        self.assertIn("Alt-A answer", line)
        self.assertNotIn("\x1b", line)                      # the caller's name cannot carry an escape
        self.assertEqual(str_width(line), 100)                    # exactly one row, whatever is on it
        await self._key(app, "a")
        self.assertEqual(app.sent[-1], ("callAccept", {"call": ID}))

        import time as _time
        now = int(_time.time() * 1000)
        await app._on_event("call", self._call(state="connected", connectedAt=now - 192000))
        for i in range(80):
            await app._on_event("callLevels", {"id": ID, "tx": (i % 8) / 8, "rx": 0.5, "txPeak": 0.9, "rxPeak": 0.5,
                                               "txTalkMs": 1000, "rxTalkMs": 2000})
        self.assertEqual(len(app.call_tx), tui_wave_keep())
        line = self._plain(app._call_line(110))
        self.assertIn("CONNECTED 3:1", line)
        self.assertIn("YOU ", line)
        self.assertIn("THEM ", line)
        self.assertRegex(line.split("THEM ")[1], "[▃▄▅▆▇█]")   # their steady level, as a burst
        self.assertIn("Alt-H hang up", line)
        self.assertEqual(str_width(line), 110)
        # The whole screen still draws, with the call line in the telemetry row.
        app.draw()
        self.assertIn("CONNECTED", self._plain(app.term.text()))
        # A narrow pane drops the waveforms before it overflows.
        narrow = self._plain(app._call_line(40))
        self.assertEqual(str_width(narrow), 40)
        self.assertNotIn("THEM", narrow)

        await self._key(app, "m")
        self.assertEqual(app.sent[-1], ("callMute", {"call": ID, "muted": True}))
        await app._on_event("call", self._call(state="connected", connectedAt=now - 192000, muted=True))
        self.assertIn("MUTED", self._plain(app._call_line(110)))
        await self._key(app, "h")
        self.assertEqual(app.sent[-1], ("callHangup", {"call": ID}))
        await app._on_event("call", self._call(state="ended", connectedAt=now - 192000, endedAt=now, reason="local_hangup"))
        self.assertIn("Call ended", app.toast)
        self.assertFalse(app._call_live())
        self.assertIn("CALL ENDED", self._plain(app._call_line(100)))

    async def test_keys_say_why_when_there_is_nothing_to_do(self):
        app = self._app()
        for char, why in (("a", "no call is ringing"), ("h", "not in a call"), ("m", "not in a call")):
            await self._key(app, char)
            self.assertIn(why, app.toast)
        self.assertEqual(app.sent, [])
        await self._key(app, "c")
        self.assertEqual(app.sent[-1], ("callStart", {"conversation": TRINITY}))
        app.status["calls"] = {"available": False, "reason": "signal-call-tunnel is not installed"}
        app.sent.clear()
        await self._key(app, "c")
        self.assertIn("signal-call-tunnel is not installed", app.toast)
        self.assertEqual(app.sent, [])
        # Declining a ringing call is a reject, not a hangup.
        await app._on_event("call", self._call())
        await self._key(app, "h")
        self.assertEqual(app.sent[-1], ("callReject", {"call": ID}))
        # A second caller while a call is live only leaves a note.
        await app._on_event("call", self._call(id="9", name="Morpheus", state="ended", reason="busy_here"))
        self.assertEqual(app.call["id"], ID)
        self.assertIn("missed a call from Morpheus", app.toast)

    async def test_a_call_already_going_is_picked_up_from_status(self):
        app = self._app()
        app.status["calls"]["active"] = [self._call(state="connected", connectedAt=5)]
        app._adopt_call()
        self.assertTrue(app._call_live())
        self.assertEqual(app.call["id"], ID)


def _find_in(home, name):
    """calls.find_tunnel as it would answer for somebody whose home is `home`."""
    with mock.patch.object(Path, "home", return_value=Path(home)), mock.patch.dict(os.environ, {"PATH": home}):
        return _real_find_tunnel(name)


_real_find_tunnel = calls.find_tunnel


def tui_wave_keep():
    from omarchy_signal import tui
    return tui.CALL_WAVE_KEEP
