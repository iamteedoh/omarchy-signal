// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal call figures
// Date Created: 2026-11-14
// Description: Labels, clock, waveform and telemetry rows for a voice call, from the bridge's call record.
// What the chat window says about a call: what it is doing, how long it has
// run, how it ended, and the readings beside it. Pure functions over the
// call record the bridge sends ("call" events), so the same file runs under
// node for the unit tests (tests/qml/calls.test.js). The terminal client's
// copy is lib/omarchy_signal/calls.py; both are held to
// tests/vectors/calls.json. No Qt imports and no .pragma library, for the same
// reason as Model.js.

var BARS = "▁▂▃▄▅▆▇█"

function pad2(n) { return (n < 10 ? "0" : "") + n }

// "0:07", "3:12", "1:02:03"; "" for something that is not a duration.
function clock(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms < 0) return ""
  var s = Math.floor(ms / 1000)
  if (s >= 3600) return Math.floor(s / 3600) + ":" + pad2(Math.floor(s % 3600 / 60)) + ":" + pad2(s % 60)
  return Math.floor(s / 60) + ":" + pad2(s % 60)
}

function isLive(call) { return !!call && call.state !== "ended" }

function isRingingIn(call) { return !!call && call.state === "ringing" && !call.outgoing }

// How a call ended, in words. `reason` is signal-cli's, plus the bridge's own
// "elsewhere" (answered or declined on another device) and "busy_here".
function endLabel(call) {
  var c = call || {}
  var reason = String(c.reason || "")
  var outgoing = !!c.outgoing
  var answered = !!c.connectedAt
  if (reason === "elsewhere") return "Answered on another device"
  if (reason === "busy_here") return "Missed call"
  if (answered) return (reason === "tunnel_exit" || reason === "tunnel_error" || reason === "shutdown") ? "Call dropped" : "Call ended"
  if (reason === "remote_busy") return "Busy"
  if (reason === "rejected") return "Declined"
  if (reason === "tunnel_spawn_error" || reason === "tunnel_exit" || reason === "tunnel_error" || reason === "shutdown") return "Call failed"
  if (reason === "local_hangup") return outgoing ? "Cancelled" : "Declined"
  if (reason === "remote_hangup") return outgoing ? "Declined" : "Missed call"
  return outgoing ? "No answer" : "Missed call"
}

function stateLabel(call) {
  var c = call || {}
  if (c.state === "ringing") return c.outgoing ? "Calling" : "Incoming call"
  if (c.state === "connecting") return "Connecting"
  if (c.state === "connected") return "Connected"
  if (c.state === "reconnecting") return "Reconnecting"
  if (c.state === "ended") return endLabel(c)
  return ""
}

// The line a finished call leaves in the conversation.
function historyLine(call) {
  var c = call || {}
  var kind = c.media === "video" ? "video call (voice only here)" : "voice call"
  var way = c.outgoing ? "Outgoing" : "Incoming"
  if (c.connectedAt) return "📞 " + way + " " + kind + " · " + clock(Math.max(0, (c.endedAt || 0) - c.connectedAt))
  var label = endLabel(c)
  if (label === "Missed call") return "📞 Missed " + kind
  return "📞 " + way + " " + kind + " · " + label.toLowerCase()
}

// How long the two sides have been talking; 0 before the call connects.
function durationMs(call, nowMs) {
  var c = call || {}
  if (!c.connectedAt) return 0
  return Math.max(0, (c.endedAt || nowMs) - c.connectedAt)
}

// `part` of `whole` as a whole percentage; 0 when there is nothing to divide.
function share(part, whole) {
  var p = Number(part), w = Number(whole)
  if (!(w > 0) || !(p > 0)) return 0
  return Math.max(0, Math.min(100, Math.floor(p * 100 / w + 0.5)))
}

// One side of a call as a burst of `slots` bars, each 0 to 1, that stays where
// it is: the louder the side, the taller the bars and the further out from the
// centre they reach. Nothing scrolls. Every bar gets its own share of the
// level, re-dealt whenever the level changes, so the burst moves the way
// speech does. `levels` is the rolling history, oldest first; only the newest
// three count, so a word dies away instead of cutting off.
// Whole-number arithmetic for the dealing, so calls.py gives the same bars.
function burst(levels, slots) {
  var n = Math.floor(Number(slots) || 0)
  if (!(n > 0)) return []
  var vals = (Array.isArray(levels) ? levels : []).filter(function(v) { return typeof v === "number" && isFinite(v) })
  function at(back) {
    var i = vals.length - back
    return i >= 0 ? Math.max(0, Math.min(1, vals[i])) : 0
  }
  var a = at(1), b = at(2), c = at(3)
  var energy = Math.max(a, b * 0.7, c * 0.45)
  var out = []
  if (energy <= 0.02) {
    for (var z = 0; z < n; z++) out.push(0)
    return out
  }
  var gain = Math.min(1, energy * 1.7)
  var spread = 0.3 + 0.7 * gain
  var seed = Math.floor(a * 997) + Math.floor(b * 991) * 13 + vals.length * 7
  var mid = (n - 1) / 2, half = Math.max(1, n / 2)
  for (var i = 0; i < n; i++) {
    var d = Math.abs(i - mid) / half / spread
    var shape = d >= 1 ? 0 : (1 - d * d) * (1 - d * d)
    var x = (i * 374761 + seed * 668265 + 1) % 1000003
    x = (x * x) % 1000003
    x = (x * 48271 + 12345) % 1000003
    out.push(Math.max(0, Math.min(1, gain * shape * (0.4 + 0.6 * x / 1000003))))
  }
  return out
}

// The same burst as `width` block characters, for the terminal client.
function waveform(levels, width) {
  var heights = burst(levels, width)
  var bars = ""
  for (var i = 0; i < heights.length; i++) bars += BARS[Math.max(0, Math.min(BARS.length - 1, Math.floor(heights[i] * BARS.length)))]
  return bars
}

// Appends a level to a rolling history of at most `max`, returning a new array
// (QML only notices a property that is assigned, not one that is mutated).
function pushLevel(history, level, max) {
  var v = Number(level)
  if (!isFinite(v)) v = 0
  var out = (Array.isArray(history) ? history : []).concat([Math.max(0, Math.min(1, v))])
  var limit = Math.max(1, max || 1)
  return out.length > limit ? out.slice(out.length - limit) : out
}

// The telemetry of a call as [label, value, tone] rows, most important first.
// tone is "" | "accent" | "alarm".
function readings(call, nowMs) {
  var c = call || {}
  var rows = []
  rows.push(["STATE", stateLabel(c).toUpperCase(), c.state === "connected" ? "accent" : (c.state === "reconnecting" ? "alarm" : "")])
  if (c.connectedAt) rows.push(["TIME", clock(durationMs(c, nowMs)), ""])
  else if (c.state !== "ended") rows.push(["RINGING", clock(Math.max(0, nowMs - (c.startedAt || nowMs))), ""])
  rows.push(["LINK", c.encrypted ? "E2E SRTP" : "", "accent"])
  if (c.setupMs) rows.push(["SETUP", (Math.floor(c.setupMs) / 1000).toFixed(1) + "s", ""])
  var lv = (c.levels && typeof c.levels === "object") ? c.levels : {}
  var talked = durationMs(c, nowMs)
  if (talked) {
    rows.push(["YOU", share(lv.txTalkMs || 0, talked) + "% talking", ""])
    rows.push(["THEM", share(lv.rxTalkMs || 0, talked) + "% talking", ""])
  }
  if (c.muted) rows.push(["MIC", "MUTED", "alarm"])
  if (c.reconnects) rows.push(["DROPS", String(c.reconnects), "alarm"])
  rows.push(["CODEC", "OPUS 48K", ""])
  if (c.audioError) rows.push(["AUDIO", "NOT CONNECTED", "alarm"])
  return rows.filter(function(r) { return r[1] })
}

// The one live call a conversation has, or null. `calls` is id -> record.
function forConversation(calls, key) {
  var map = calls || {}
  for (var id in map) if (map[id] && map[id].conversation === key && isLive(map[id])) return map[id]
  return null
}

// Any live call at all (there is only ever one), or null.
function live(calls) {
  var map = calls || {}
  for (var id in map) if (isLive(map[id])) return map[id]
  return null
}

if (typeof module !== "undefined") {
  module.exports = {
    clock: clock, isLive: isLive, isRingingIn: isRingingIn, endLabel: endLabel, stateLabel: stateLabel,
    historyLine: historyLine, durationMs: durationMs, share: share, burst: burst, waveform: waveform, pushLevel: pushLevel,
    readings: readings, forConversation: forConversation, live: live
  }
}
