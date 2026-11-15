// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal call figure tests
// Date Created: 2026-11-14
// Description: Unit tests for Calls.js, run under node and held to the vectors the Python client shares.
// Run with: node --test tests/qml
const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("path")
const C = require(path.join(__dirname, "..", "..", "Calls.js"))
const V = require(path.join(__dirname, "..", "vectors", "calls.json"))

test("the clock, as the terminal client prints it", () => {
  for (const [ms, want] of V.clock) assert.equal(C.clock(ms), want, String(ms))
  assert.equal(C.clock("soon"), "")
  assert.equal(C.clock(NaN), "")
})

test("what a call is doing and how it ended", () => {
  for (const row of V.labels) {
    assert.equal(C.stateLabel(row.call), row.state, JSON.stringify(row.call))
    assert.equal(C.endLabel(row.call), row.end, JSON.stringify(row.call))
    assert.equal(C.historyLine(row.call), row.history, JSON.stringify(row.call))
  }
  assert.equal(C.stateLabel(null), "")
})

test("share of the call each side spent talking", () => {
  for (const [part, whole, want] of V.share) assert.equal(C.share(part, whole), want, part + "/" + whole)
})

test("waveform in block characters", () => {
  for (const [levels, width, want] of V.waveform) assert.equal(C.waveform(levels, width), want, JSON.stringify(levels))
  assert.equal(C.waveform(null, 3), "▁▁▁")
})

test("burst: bars that swell in place, the same as the terminal client's", () => {
  for (const [levels, slots, want] of V.burst) {
    const got = C.burst(levels, slots)
    assert.equal(got.length, want.length, JSON.stringify(levels))
    got.forEach((v, i) => assert.ok(Math.abs(v - want[i]) < 1e-9, JSON.stringify(levels) + " bar " + i))
  }
  const sum = a => a.reduce((x, y) => x + y, 0)
  const lit = a => a.filter(v => v > 0.02).length
  const quiet = C.burst([0.3, 0.3, 0.3], 48), loud = C.burst([0.8, 0.8, 0.8], 48)
  assert.ok(sum(loud) > sum(quiet) * 2, "louder is taller")
  assert.ok(lit(loud) > lit(quiet) + 8, "louder reaches further out")
  assert.ok(Math.max(...loud.slice(20, 28)) > Math.max(...loud.slice(0, 6)), "tallest in the middle")
  assert.equal(sum(C.burst([0.9, 0, 0, 0], 48)), 0, "a pause falls flat")
  assert.ok(sum(C.burst([0.9, 0], 48)) > 0, "but a word dies away first")
  assert.deepEqual(C.burst([0.5], 0), [])
})

test("telemetry rows match the terminal client's", () => {
  for (const row of V.readings) assert.deepEqual(C.readings(row.call, row.now), row.rows, JSON.stringify(row.call))
})

test("level history rolls and never grows past its window", () => {
  let h = []
  for (let i = 0; i < 10; i++) h = C.pushLevel(h, i / 10, 4)
  assert.deepEqual(h, [0.6, 0.7, 0.8, 0.9])
  assert.deepEqual(C.pushLevel([0.5], 7, 4), [0.5, 1])           // clamped
  assert.deepEqual(C.pushLevel(null, "x", 4), [0])
  const before = [0.1]
  C.pushLevel(before, 0.2, 4)
  assert.deepEqual(before, [0.1])                                // a new array every time
})

test("finding the live call", () => {
  const calls = {
    "1": { id: "1", conversation: "number:+15550002222", state: "ended" },
    "2": { id: "2", conversation: "number:+15550003333", state: "ringing", outgoing: false },
  }
  assert.equal(C.forConversation(calls, "number:+15550002222"), null)
  assert.equal(C.forConversation(calls, "number:+15550003333").id, "2")
  assert.equal(C.live(calls).id, "2")
  assert.equal(C.live({}), null)
  assert.equal(C.live(null), null)
  assert.equal(C.isRingingIn(calls["2"]), true)
  assert.equal(C.isRingingIn({ state: "ringing", outgoing: true }), false)
  assert.equal(C.isLive(null), false)
})
