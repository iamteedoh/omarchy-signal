// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal telemetry figures
// Date Created: 2026-11-13
// Description: Works out the chat window telemetry (traffic, delivery, reply times, activity) from history rows.
// The numbers behind the chat window's telemetry: who is sending how much,
// how fast each side answers, how far your own messages got, when the
// conversation is busiest. Pure functions over what the view already holds
// (the bridge's history rows and its status), so nothing here asks the
// network for anything and the same file runs under node for the unit tests
// (tests/qml/telemetry.test.js). No Qt imports and no .pragma library, for the
// same reason as Model.js.

var HOUR_MS = 3600000
var DAY_MS = 86400000

// A reply that takes longer than this is a new conversation, not an answer,
// and would drag the "typical reply" figure into hours.
var REPLY_WINDOW_MS = 12 * HOUR_MS

function pad2(n) { return (n < 10 ? "0" : "") + n }

function median(values) {
  var list = (Array.isArray(values) ? values : []).filter(function(v) { return typeof v === "number" && isFinite(v) })
  if (list.length === 0) return -1
  list.sort(function(a, b) { return a - b })
  var mid = Math.floor(list.length / 2)
  return list.length % 2 ? list[mid] : Math.round((list[mid - 1] + list[mid]) / 2)
}

// "42s", "3m", "2h 05m", "3d 4h", "45d", "3.0y"; "" for something that is not a duration.
function shortDuration(ms) {
  var n = Number(ms)
  if (!isFinite(n) || n < 0) return ""
  if (n < 1000) return "<1s"
  var s = Math.floor(n / 1000)
  if (s < 60) return s + "s"
  var m = Math.floor(s / 60)
  if (m < 60) return m + "m"
  var h = Math.floor(m / 60)
  if (h < 24) return h + "h " + pad2(m % 60) + "m"
  var d = Math.floor(h / 24)
  if (d < 14) return d + "d " + (h % 24) + "h"
  if (d < 365) return d + "d"
  return (d / 365).toFixed(1) + "y"
}

// A running counter since something happened: "T+00:03:12", and "T+4d 02h"
// once the hours no longer fit. "" when it has not happened.
function elapsedClock(sinceMs, nowMs) {
  var since = Number(sinceMs) || 0
  if (since <= 0) return ""
  var diff = Math.max(0, (Number(nowMs) || Date.now()) - since)
  var s = Math.floor(diff / 1000)
  var h = Math.floor(s / 3600)
  if (h >= 100) return "T+" + Math.floor(h / 24) + "d " + pad2(h % 24) + "h"
  return "T+" + pad2(h) + ":" + pad2(Math.floor(s / 60) % 60) + ":" + pad2(s % 60)
}

// A round trip to the bridge: "3 ms", "1.2 s"; "" when there is no sample.
function latencyLabel(ms) {
  var n = Number(ms)
  if (!isFinite(n) || n < 0) return ""
  if (n < 1) return "<1 ms"
  if (n < 1000) return Math.round(n) + " ms"
  return (n / 1000).toFixed(1) + " s"
}

// "ok" while a round trip is quick enough not to be felt, "slow" past that,
// "down" with no answer.
function latencyLevel(ms) {
  var n = Number(ms)
  if (!isFinite(n) || n < 0) return "down"
  return n < 250 ? "ok" : "slow"
}

// Keep the last `max` samples, newest last. Returns a new array, so a QML
// property holding it changes and its bindings re-run.
function pushSample(samples, value, max) {
  var list = Array.isArray(samples) ? samples.slice() : []
  var n = Number(value)
  if (!isFinite(n) || n < 0) return list
  list.push(n)
  var keep = Math.max(1, max || 24)
  return list.length > keep ? list.slice(list.length - keep) : list
}

function linkState(connected, linked) {
  return !connected ? "OFFLINE" : !linked ? "UNLINKED" : "ONLINE"
}

// How much the contact's key can be trusted, out of the bridge's "identities"
// answer (newest first). `level` is good | neutral | bad | unknown; `addedMs`
// is when this computer first saw the key (0 = unknown).
function trustSummary(identities, nowMs) {
  var list = Array.isArray(identities) ? identities.filter(function(i) { return i && typeof i === "object" }) : []
  if (list.length === 0) return { label: "", level: "unknown", ageMs: -1, addedMs: 0, changes: 0 }
  var newest = list[0]
  var raw = String(newest.trustLevel || "").toUpperCase()
  var label = raw === "TRUSTED_VERIFIED" ? "VERIFIED" : raw === "TRUSTED_UNVERIFIED" ? "UNVERIFIED" : raw === "UNTRUSTED" ? "UNTRUSTED" : ""
  var level = raw === "TRUSTED_VERIFIED" ? "good" : raw === "TRUSTED_UNVERIFIED" ? "neutral" : raw === "UNTRUSTED" ? "bad" : "unknown"
  var added = Number(newest.addedDate) || 0
  var now = Number(nowMs) || Date.now()
  var known = added > 0 && added <= now
  return { label: label, level: level, ageMs: known ? now - added : -1, addedMs: known ? added : 0, changes: list.length - 1 }
}

// Whether the contact's key changed while you were talking to them. signal-cli
// keeps only the current key of a contact, so a change shows as history that
// is older than the key: messages were exchanged before this key was first
// seen. An older stored key (`changes`) says the same where one is listed.
// Only ever a false "no": history beyond the window looked at is not seen.
function keyChanged(trust, firstTs) {
  if (!trust || typeof trust !== "object") return false
  if (trust.changes > 0) return true
  var first = Number(firstTs) || 0
  return trust.addedMs > 0 && first > 0 && first < trust.addedMs
}

// What is waiting in the composer.
function payloadStats(text) {
  var s = typeof text === "string" ? text : ""
  var trimmed = s.trim()
  return {
    chars: Array.from(s).length,
    words: trimmed ? trimmed.split(/\s+/).length : 0,
    lines: s.length ? s.split("\n").length : 0
  }
}

function emptyStats() {
  var hours = []
  for (var i = 0; i < 24; i++) hours.push(0)
  return {
    total: 0, tx: 0, rx: 0, today: 0,
    delivery: { sending: 0, sent: 0, delivered: 0, read: 0, failed: 0 },
    lastRx: 0, lastTx: 0, lastTxStatus: "",
    media: 0, links: 0, reactions: 0, edited: 0, expiring: 0,
    replyTheirs: -1, replyYours: -1, transit: -1,
    hours: hours, hoursMax: 0, peakHour: -1,
    senders: [], firstTs: 0
  }
}

// Everything the telemetry rail draws about one conversation, out of the
// bridge's history rows (oldest first, as "history" returns them). `nowMs` is
// passed in so the 24 hour window and "today" are testable.
function threadStats(rows, nowMs) {
  var s = emptyStats()
  var list = Array.isArray(rows) ? rows.filter(function(m) { return m && typeof m.ts === "number" && m.ts > 0 }) : []
  if (list.length === 0) return s
  list.sort(function(a, b) { return a.ts - b.ts })
  var now = Number(nowMs) || Date.now()
  var today = new Date(now)
  var dayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  // The 24 buckets end with the hour that is running now.
  var windowEnd = new Date(today.getFullYear(), today.getMonth(), today.getDate(), today.getHours() + 1).getTime()
  var windowStart = windowEnd - 24 * HOUR_MS
  var byHour = []
  for (var h = 0; h < 24; h++) byHour.push(0)
  var theirs = [], yours = [], transit = []
  var senders = {}, order = []
  var prev = null

  for (var i = 0; i < list.length; i++) {
    var m = list[i]
    var outgoing = m.outgoing === true
    s.total += 1
    if (outgoing) {
      s.tx += 1
      s.lastTx = m.ts
      s.lastTxStatus = typeof m.status === "string" ? m.status : ""
      if (Object.prototype.hasOwnProperty.call(s.delivery, m.status)) s.delivery[m.status] += 1
      else if (m.status === "viewed") s.delivery.read += 1
    } else {
      s.rx += 1
      s.lastRx = m.ts
      var got = Number(m.receivedAt) || 0
      // Sender's clock to ours: only believable when it is small and positive.
      if (got >= m.ts && got - m.ts < 5 * 60000) transit.push(got - m.ts)
    }
    if (m.ts >= dayStart) s.today += 1
    if (m.edited === true) s.edited += 1
    if (Number(m.expiresIn) > 0) s.expiring += 1
    if (m.deleted !== true) {
      if (Array.isArray(m.attachments)) s.media += m.attachments.length
      var found = typeof m.body === "string" ? m.body.match(/https?:\/\/[^\s<>"']+/g) : null
      if (found) s.links += found.length
    }
    if (m.reactions && typeof m.reactions === "object") for (var r in m.reactions) s.reactions += 1

    if (m.ts >= windowStart && m.ts < windowEnd) s.hours[Math.floor((m.ts - windowStart) / HOUR_MS)] += 1
    byHour[new Date(m.ts).getHours()] += 1

    // The first message after the other side spoke is the reply to it.
    if (prev && (prev.outgoing === true) !== outgoing) {
      var gap = m.ts - prev.ts
      if (gap >= 0 && gap <= REPLY_WINDOW_MS) (outgoing ? yours : theirs).push(gap)
    }
    prev = m

    var who = outgoing ? "You" : String(m.senderName || m.sender || "?")
    if (!Object.prototype.hasOwnProperty.call(senders, who)) { senders[who] = 0; order.push(who) }
    senders[who] += 1
  }

  s.firstTs = list[0].ts
  s.replyTheirs = median(theirs)
  s.replyYours = median(yours)
  s.transit = median(transit)
  for (var k = 0; k < 24; k++) if (s.hours[k] > s.hoursMax) s.hoursMax = s.hours[k]
  var best = 0
  for (var p = 0; p < 24; p++) if (byHour[p] > best) { best = byHour[p]; s.peakHour = p }
  s.senders = order.map(function(name) { return { name: name, count: senders[name] } })
    .sort(function(a, b) { return b.count - a.count || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) })
    .slice(0, 4)
  return s
}

// "21:00" for an hour of the day, "" for none.
function hourLabel(hour) {
  var h = Number(hour)
  return isFinite(h) && h >= 0 && h < 24 ? pad2(Math.floor(h)) + ":00" : ""
}

// The delivery mark of your newest message, in words.
function deliveryWord(status) {
  var words = { sending: "SENDING", sent: "SENT", delivered: "DELIVERED", read: "READ", viewed: "READ", failed: "FAILED" }
  return Object.prototype.hasOwnProperty.call(words, status) ? words[status] : ""
}

// How many of `cells` a share of `total` lights up; never 0 for something
// that happened at all, never more than there are.
function cellsFor(value, total, cells) {
  var v = Number(value) || 0, t = Number(total) || 0, n = Math.max(1, cells || 1)
  if (v <= 0 || t <= 0) return 0
  return Math.max(1, Math.min(n, Math.round(v / t * n)))
}

if (typeof module !== "undefined") {
  module.exports = {
    REPLY_WINDOW_MS: REPLY_WINDOW_MS,
    median: median,
    shortDuration: shortDuration,
    elapsedClock: elapsedClock,
    latencyLabel: latencyLabel,
    latencyLevel: latencyLevel,
    pushSample: pushSample,
    linkState: linkState,
    trustSummary: trustSummary,
    keyChanged: keyChanged,
    payloadStats: payloadStats,
    emptyStats: emptyStats,
    threadStats: threadStats,
    hourLabel: hourLabel,
    deliveryWord: deliveryWord,
    cellsFor: cellsFor
  }
}
