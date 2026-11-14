// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal telemetry tests
// Date Created: 2026-11-13
// Description: Unit tests for Telemetry.js, run under node.
// Run with: node --test tests/qml
const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("path")
const T = require(path.join(__dirname, "..", "..", "Telemetry.js"))

// Local time on purpose: "today" and the hour of the day are the reader's.
const at = (h, m, s = 0, day = 13) => new Date(2026, 10, day, h, m, s).getTime()
const NOW = at(21, 30)

const msg = (ts, outgoing, extra = {}) => ({ ts, outgoing, sender: outgoing ? "" : "number:+15550002222",
  senderName: outgoing ? "" : "Trinity", body: "", status: outgoing ? "sent" : "", ...extra })

test("median, and what it says about nothing", () => {
  assert.equal(T.median([]), -1)
  assert.equal(T.median(null), -1)
  assert.equal(T.median([5]), 5)
  assert.equal(T.median([9, 1, 5]), 5)
  assert.equal(T.median([1, 2, 3, 10]), 3)          // even count: the mean of the middle two, rounded
  assert.equal(T.median([4, NaN, "7", Infinity]), 4)
})

test("durations read at a glance", () => {
  assert.equal(T.shortDuration(400), "<1s")
  assert.equal(T.shortDuration(42000), "42s")
  assert.equal(T.shortDuration(3 * 60000 + 5000), "3m")
  assert.equal(T.shortDuration(2 * 3600000 + 5 * 60000), "2h 05m")
  assert.equal(T.shortDuration(3 * 86400000 + 4 * 3600000), "3d 4h")
  assert.equal(T.shortDuration(45 * 86400000 + 4 * 3600000), "45d")
  assert.equal(T.shortDuration(1095 * 86400000), "3.0y")
  assert.equal(T.shortDuration(-1), "")
  assert.equal(T.shortDuration("soon"), "")
})

test("elapsedClock counts up from an event and never runs backwards", () => {
  assert.equal(T.elapsedClock(NOW - (3 * 60 + 12) * 1000, NOW), "T+00:03:12")
  assert.equal(T.elapsedClock(NOW - (26 * 3600 + 61) * 1000, NOW), "T+26:01:01")
  assert.equal(T.elapsedClock(NOW - 99 * 3600 * 1000 - 5000, NOW), "T+99:00:05")
  assert.equal(T.elapsedClock(NOW - (5 * 24 + 2) * 3600 * 1000 - 5000, NOW), "T+5d 02h")
  assert.equal(T.elapsedClock(NOW + 5000, NOW), "T+00:00:00")     // a clock that is ahead of ours
  assert.equal(T.elapsedClock(0, NOW), "")
})

test("latency: a label and whether it is worth worrying about", () => {
  assert.equal(T.latencyLabel(3.4), "3 ms")
  assert.equal(T.latencyLabel(0), "<1 ms")                       // a local socket can answer inside a millisecond
  assert.equal(T.latencyLabel(0.6), "<1 ms")
  assert.equal(T.latencyLabel(1240), "1.2 s")
  assert.equal(T.latencyLabel(-1), "")
  assert.equal(T.latencyLevel(3), "ok")
  assert.equal(T.latencyLevel(900), "slow")
  assert.equal(T.latencyLevel(-1), "down")
  assert.equal(T.latencyLevel(undefined), "down")
})

test("pushSample keeps the newest and hands back a new array", () => {
  const first = [1, 2, 3]
  const next = T.pushSample(first, 4, 3)
  assert.deepEqual(next, [2, 3, 4])
  assert.deepEqual(first, [1, 2, 3])
  assert.deepEqual(T.pushSample(null, 7, 3), [7])
  assert.deepEqual(T.pushSample([1], -5, 3), [1])              // no answer is not a sample
  assert.deepEqual(T.pushSample([1], "x", 3), [1])
})

test("linkState names the three states the header can be in", () => {
  assert.equal(T.linkState(false, false), "OFFLINE")
  assert.equal(T.linkState(false, true), "OFFLINE")
  assert.equal(T.linkState(true, false), "UNLINKED")
  assert.equal(T.linkState(true, true), "ONLINE")
})

test("trustSummary reads the newest identity and counts key changes", () => {
  const ids = [{ trustLevel: "TRUSTED_UNVERIFIED", addedDate: NOW - 3 * 86400000 }, { trustLevel: "UNTRUSTED", addedDate: 1 }]
  assert.deepEqual(T.trustSummary(ids, NOW), { label: "UNVERIFIED", level: "neutral", ageMs: 3 * 86400000, addedMs: NOW - 3 * 86400000, changes: 1 })
  assert.equal(T.trustSummary([{ trustLevel: "TRUSTED_VERIFIED" }], NOW).level, "good")
  assert.equal(T.trustSummary([{ trustLevel: "UNTRUSTED" }], NOW).level, "bad")
  // Nothing known is "unknown", never "good".
  assert.deepEqual(T.trustSummary([], NOW), { label: "", level: "unknown", ageMs: -1, addedMs: 0, changes: 0 })
  assert.equal(T.trustSummary("nonsense", NOW).level, "unknown")
  assert.equal(T.trustSummary([{ trustLevel: "SOMETHING_NEW" }], NOW).level, "unknown")
  assert.equal(T.trustSummary([{ trustLevel: "TRUSTED_VERIFIED", addedDate: NOW + 60000 }], NOW).ageMs, -1)
})

test("keyChanged: history older than the key means the key changed", () => {
  const key = (daysAgo) => T.trustSummary([{ trustLevel: "TRUSTED_UNVERIFIED", addedDate: NOW - daysAgo * 86400000 }], NOW)
  // signal-cli lists one key a contact: a change shows as messages from before it.
  assert.equal(T.keyChanged(key(2), NOW - 30 * 86400000), true)
  assert.equal(T.keyChanged(key(30), NOW - 2 * 86400000), false)     // the key predates everything here
  assert.equal(T.keyChanged(key(2), 0), false)                       // no history: nothing to compare
  assert.equal(T.keyChanged(T.trustSummary([{ trustLevel: "TRUSTED_UNVERIFIED" }], NOW), NOW - 86400000), false)   // undated key
  // Where an older key is listed, that alone says so.
  assert.equal(T.keyChanged(T.trustSummary([{ trustLevel: "TRUSTED_UNVERIFIED" }, { trustLevel: "UNTRUSTED" }], NOW), 0), true)
  assert.equal(T.keyChanged(null, NOW), false)
})

test("payloadStats counts what is in the composer", () => {
  assert.deepEqual(T.payloadStats(""), { chars: 0, words: 0, lines: 0 })
  assert.deepEqual(T.payloadStats("On my way.\nSee you in Zion."), { chars: 27, words: 7, lines: 2 })
  assert.equal(T.payloadStats("👍🏽").chars, 2)                  // code points, not UTF-16 units
  assert.deepEqual(T.payloadStats(null), { chars: 0, words: 0, lines: 0 })
})

test("threadStats on nothing is all zeroes, with 24 empty hours", () => {
  for (const rows of [[], null, [{ ts: 0 }, null, { body: "no timestamp" }]]) {
    const s = T.threadStats(rows, NOW)
    assert.equal(s.total, 0)
    assert.equal(s.hours.length, 24)
    assert.equal(s.hoursMax, 0)
    assert.equal(s.peakHour, -1)
    assert.equal(s.replyTheirs, -1)
    assert.deepEqual(s.senders, [])
  }
})

test("threadStats counts traffic, delivery and what the messages carry", () => {
  const rows = [
    msg(at(20, 0), true, { status: "read", body: "see https://example.org/a and http://example.org/b" }),
    msg(at(20, 2), false, { attachments: [{ path: "/x.png" }, { path: "/y.png" }], reactions: { "number:+1555": "👍", "number:+1556": "🔥" } }),
    msg(at(20, 3), true, { status: "delivered", edited: true }),
    msg(at(20, 4), true, { status: "failed" }),
    msg(at(20, 5), true, { status: "viewed", expiresIn: 3600 }),
    msg(at(23, 0, 0, 12), false, { deleted: true, attachments: [{ path: "/z" }], body: "https://example.org/gone" })
  ]
  const s = T.threadStats(rows, NOW)
  assert.equal(s.total, 6)
  assert.equal(s.tx, 4)
  assert.equal(s.rx, 2)
  assert.equal(s.today, 5)                                       // the sixth was yesterday
  assert.deepEqual(s.delivery, { sending: 0, sent: 0, delivered: 1, read: 2, failed: 1 })
  assert.equal(s.lastTx, at(20, 5))
  assert.equal(s.lastTxStatus, "viewed")
  assert.equal(s.lastRx, at(20, 2))
  assert.equal(s.media, 2)                                       // a deleted message carries nothing
  assert.equal(s.links, 2)
  assert.equal(s.reactions, 2)
  assert.equal(s.edited, 1)
  assert.equal(s.expiring, 1)
  assert.equal(s.firstTs, at(23, 0, 0, 12))
})

test("threadStats: reply times are medians of first answers, and ignore a fresh start", () => {
  const rows = [
    msg(at(9, 0), true),
    msg(at(9, 2), false),                    // they took 2 m
    msg(at(9, 3), false),                    // the same run: not a second reply
    msg(at(9, 3, 30), true),                 // you took 30 s
    msg(at(9, 13, 30), false),               // they took 10 m
    msg(at(9, 14), true),                    // you took 30 s
    msg(at(9, 18), false),                   // they took 4 m
    msg(at(9, 18, 0, 14), true)              // a day later: a new conversation, not a reply
  ]
  const s = T.threadStats(rows, at(10, 0, 0, 14))
  assert.equal(s.replyTheirs, 4 * 60000)
  assert.equal(s.replyYours, 30000)
  // Rows in any order give the same answer.
  assert.equal(T.threadStats(rows.slice().reverse(), at(10, 0, 0, 14)).replyTheirs, 4 * 60000)
  // One side only: nobody answered anybody.
  const mono = T.threadStats([msg(at(9, 0), true), msg(at(9, 5), true)], NOW)
  assert.equal(mono.replyTheirs, -1)
  assert.equal(mono.replyYours, -1)
})

test("threadStats: the 24 hour graph ends with the hour running now", () => {
  const rows = [
    msg(at(21, 5), false), msg(at(21, 20), true),               // this hour: the last bucket
    msg(at(20, 59), false),                                      // the hour before
    msg(at(22, 0, 0, 12), false),                                // 23 hours back: the first bucket
    msg(at(21, 59, 0, 12), false),                               // just outside the window
    msg(at(21, 10, 0, 10), false), msg(at(21, 15, 0, 9), false)  // days ago: only the peak hour sees them
  ]
  const s = T.threadStats(rows, NOW)
  assert.equal(s.hours[23], 2)
  assert.equal(s.hours[22], 1)
  assert.equal(s.hours[0], 1)
  assert.equal(s.hours.reduce((a, b) => a + b, 0), 4)
  assert.equal(s.hoursMax, 2)
  assert.equal(s.peakHour, 21)
  assert.equal(T.hourLabel(s.peakHour), "21:00")
  assert.equal(T.hourLabel(-1), "")
})

test("threadStats: transit believes only a small positive gap between the two clocks", () => {
  const rows = [
    msg(at(9, 0), false, { receivedAt: at(9, 0) + 400 }),
    msg(at(9, 1), false, { receivedAt: at(9, 1) + 800 }),
    msg(at(9, 2), false, { receivedAt: at(9, 2) + 600 }),
    msg(at(9, 3), false, { receivedAt: at(9, 3) - 5000 }),       // their clock is ahead of ours
    msg(at(9, 4), false, { receivedAt: at(9, 4) + 3600000 }),    // fetched an hour later: says nothing about the link
    msg(at(9, 5), false),                                        // older history has none
    msg(at(9, 6), true, { receivedAt: at(9, 6) + 1 })            // your own are not in transit to you
  ]
  assert.equal(T.threadStats(rows, NOW).transit, 600)
  assert.equal(T.threadStats([msg(at(9, 5), false)], NOW).transit, -1)
})

test("threadStats: who is talking, busiest first, four at most", () => {
  const from = (name, ts) => msg(ts, false, { senderName: name, sender: "number:" + name })
  const rows = [from("Tank", at(9, 0)), from("Trinity", at(9, 1)), from("Tank", at(9, 2)), msg(at(9, 3), true),
                from("Morpheus", at(9, 4)), from("Switch", at(9, 5)), from("Niobe", at(9, 6)), from("Tank", at(9, 7)),
                msg(at(9, 8), true)]
  const s = T.threadStats(rows, NOW)
  assert.deepEqual(s.senders.map(x => x.name + ":" + x.count), ["Tank:3", "You:2", "Morpheus:1", "Niobe:1"])
})

test("deliveryWord and cellsFor", () => {
  assert.equal(T.deliveryWord("read"), "READ")
  assert.equal(T.deliveryWord("viewed"), "READ")
  assert.equal(T.deliveryWord("failed"), "FAILED")
  assert.equal(T.deliveryWord(""), "")
  assert.equal(T.deliveryWord("constructor"), "")
  assert.equal(T.cellsFor(0, 10, 20), 0)
  assert.equal(T.cellsFor(1, 1000, 20), 1)                       // it happened: it shows
  assert.equal(T.cellsFor(5, 10, 20), 10)
  assert.equal(T.cellsFor(50, 10, 20), 20)
  assert.equal(T.cellsFor(3, 0, 20), 0)
})

test("shared vectors (tests/vectors/telemetry.json) hold", () => {
  const V = require(path.join(__dirname, "..", "vectors", "telemetry.json"))
  assert.ok(V.threadStats.length >= 5)
  for (const c of V.threadStats) {
    const s = T.threadStats(c.rows, c.now)
    for (const key of Object.keys(c.expect)) assert.deepEqual(s[key], c.expect[key], c.name + ": " + key)
  }
  for (const c of V.shortDuration) assert.equal(T.shortDuration(c.ms), c.out, "shortDuration(" + c.ms + ")")
  for (const c of V.median) assert.equal(T.median(c.in), c.out, "median(" + JSON.stringify(c.in) + ")")
})
