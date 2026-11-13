// SPDX-License-Identifier: GPL-3.0-or-later
// Pure helpers shared by Service.qml and BarWidget.qml. No Qt imports and no
// .pragma library (node would choke on it), so the
// same file runs under node for the unit tests (see tests/qml).


// Characters that must never reach a Text element even in PlainText mode:
// C0/C1 controls, bidi overrides/isolates, BOM and the private-use plane a
// remote sender could use to draw fake UI glyphs. Mirrors
// lib/omarchy_signal/sanitize.py.
var CONTROL_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u00ad\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff\ue000-\uf8ff\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/gu

function cleanText(value, maxLength) {
  if (value === null || value === undefined) return ""
  var s = String(value).replace(/\r\n?/g, "\n").replace(CONTROL_RE, "")
  var limit = maxLength || 4000
  if (s.length > limit) s = s.substring(0, limit)
  return s
}

function singleLine(value, maxLength) {
  return cleanText(value, maxLength).replace(/\s*\n\s*/g, " ").trim()
}

// Parse one line of `omarchy-signal events` output. Returns null for anything
// that is not an {event, data} object.
function parseEventLine(line) {
  var s = String(line || "").trim()
  if (!s) return null
  if (s.length > 8 * 1024 * 1024) return null
  var obj
  try { obj = JSON.parse(s) } catch (e) { return null }
  if (!obj || typeof obj !== "object" || typeof obj.event !== "string") return null
  return { event: obj.event, data: (obj.data && typeof obj.data === "object" && !Array.isArray(obj.data)) ? obj.data : {} }
}

// Build the popup model for an incoming message event. Returns null when the
// bridge says not to notify (outgoing, muted, notifications off).
function toastFromMessage(data, previewEnabled) {
  if (!data || data.notify !== true) return null
  var key = typeof data.conversation === "string" ? data.conversation : ""
  if (!key) return null
  var sender = singleLine(data.senderName || data.sender || "Signal", 80)
  var convName = singleLine(data.conversationName || sender, 80)
  var title = data.isGroup ? (sender + " · " + convName) : sender
  var body = previewEnabled === false ? "New message" : singleLine(data.preview || data.text || "New message", 400)
  var attachments = Array.isArray(data.attachments) ? data.attachments.length : 0
  return {
    key: key,
    ts: typeof data.ts === "number" ? data.ts : 0,
    title: title,
    body: body,
    convName: convName,
    isGroup: data.isGroup === true,
    attachments: attachments
  }
}

// Sort conversations newest-first, unread first when the timestamps tie.
function sortConversations(list) {
  var arr = Array.isArray(list) ? list.slice() : []
  arr.sort(function(a, b) {
    var ta = a.lastTs || 0, tb = b.lastTs || 0
    if (tb !== ta) return tb - ta
    return (b.unread || 0) - (a.unread || 0)
  })
  return arr
}

function filterRows(rows, query) {
  var q = singleLine(query, 100).toLowerCase()
  if (!q) return rows
  return rows.filter(function(r) {
    return (r.name || "").toLowerCase().indexOf(q) >= 0 || (r.sub || "").toLowerCase().indexOf(q) >= 0
  })
}

function unreadLabel(total) {
  var n = Number(total) || 0
  if (n <= 0) return ""
  if (n > 99) return "99+"
  return String(n)
}

function relativeTime(tsMs, nowMs) {
  var ts = Number(tsMs) || 0
  if (!ts) return ""
  var now = Number(nowMs) || Date.now()
  var diff = Math.max(0, now - ts)
  var m = Math.floor(diff / 60000)
  if (m < 1) return "now"
  if (m < 60) return m + "m"
  var h = Math.floor(m / 60)
  if (h < 24) return h + "h"
  var d = Math.floor(h / 24)
  if (d < 7) return d + "d"
  return new Date(ts).toISOString().substring(0, 10)
}

// Omarchy's Do Not Disturb state file (~/.local/state/omarchy/notifications.json).
function parseDnd(text) {
  var obj
  try { obj = JSON.parse(String(text || "")) } catch (e) { return false }
  return !!(obj && typeof obj === "object" && obj.dnd === true)
}

var KEY_RE = /^(number|uuid|group|username):[A-Za-z0-9+/=_.\-]{1,120}$/

function isConversationKey(key) {
  return typeof key === "string" && KEY_RE.test(key)
}

// The argv used to launch the terminal client for a conversation. Always an
// array: it goes through execArgv, never through a shell string.
function tuiArgv(conversationKey) {
  var argv = ["omarchy-signal", "open"]
  if (isConversationKey(conversationKey)) argv.push(conversationKey)
  return argv
}

// ---------------------------------------------------------------- bridge wire
//
// The shell talks to the bridge the way the terminal client does: newline-
// delimited JSON on its private Unix socket (lib/omarchy_signal/protocol.py).
// Nothing here builds a command line; a request is an object the bridge
// validates against its own schema before any code runs on it.

var OP_RE = /^[A-Za-z]{1,32}$/

// One request line, or null when it could not be a valid one.
function requestLine(id, op, params) {
  if (typeof id !== "number" || !isFinite(id) || id <= 0) return null
  if (typeof op !== "string" || !OP_RE.test(op)) return null
  var msg = {}
  if (params && typeof params === "object") for (var k in params) if (k !== "id" && k !== "op") msg[k] = params[k]
  msg.id = id
  msg.op = op
  return JSON.stringify(msg) + "\n"
}

// One line from the bridge: {kind: "event", name, data}, {kind: "reply", id,
// ok, result, error, code}, or null for anything else.
function parseBridgeLine(line) {
  var s = String(line || "").trim()
  if (!s || s.charAt(0) !== "{") return null
  if (s.length > 8 * 1024 * 1024) return null
  var obj
  try { obj = JSON.parse(s) } catch (e) { return null }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null
  if (typeof obj.event === "string") {
    return { kind: "event", name: singleLine(obj.event, 40),
             data: (obj.data && typeof obj.data === "object" && !Array.isArray(obj.data)) ? obj.data : {} }
  }
  if (typeof obj.id === "number" && typeof obj.ok === "boolean") {
    return { kind: "reply", id: obj.id, ok: obj.ok, result: obj.result,
             error: singleLine(obj.error || "", 200), code: singleLine(obj.code || "", 40) }
  }
  return null
}

// Parameters for "send": a reply with an optional quote and attachments.
// Attachments must be absolute paths; the bridge checks them again.
function sendParams(conversationKey, text, attachments, quote) {
  if (!isConversationKey(conversationKey)) return null
  var body = cleanText(text, 60000)
  var files = Array.isArray(attachments) ? attachments.filter(function(a) { return typeof a === "string" && a.charAt(0) === "/" && a.length < 4096 }).slice(0, 8) : []
  if (!body.trim() && files.length === 0) return null
  var p = { conversation: conversationKey, text: body, attachments: files }
  if (quote && typeof quote.ts === "number" && quote.ts > 0 && isConversationKey(quote.author)) {
    p.quoteTs = quote.ts
    p.quoteAuthor = quote.author
    p.quoteText = singleLine(quote.text || "", 200)
  }
  return p
}

function reactParams(conversationKey, ts, author, emojiText, remove) {
  if (!isConversationKey(conversationKey) || !isConversationKey(author)) return null
  if (typeof ts !== "number" || ts <= 0) return null
  var e = singleLine(emojiText, 16)
  if (!e) return null
  return { conversation: conversationKey, ts: ts, author: author, emoji: e, remove: remove === true }
}

// {op, params} for deleting one message. "For me" (deleteLocal) is local and
// sends nothing; "for everyone" (delete) is Signal's remote delete.
function deleteRequest(conversationKey, ts, everyone, sender) {
  if (!isConversationKey(conversationKey)) return null
  if (typeof ts !== "number" || ts <= 0) return null
  if (everyone) return { op: "delete", params: { conversation: conversationKey, ts: ts } }
  var p = { conversation: conversationKey, ts: ts }
  if (sender && isConversationKey(sender)) p.sender = sender
  return { op: "deleteLocal", params: p }
}

// Why a message cannot be deleted for everyone, or "" when it can: your own,
// not already deleted, and within Signal's one-day window unless it is in Note
// to Self. The bridge enforces the same rule (protocol.delete_for_everyone_refusal
// in lib/omarchy_signal/protocol.py); both are held to tests/vectors/model.json.
var DELETE_FOR_EVERYONE_MS = 24 * 60 * 60 * 1000

function deleteForEveryoneRefusal(row, nowMs, noteToSelf) {
  if (!row || row.outgoing !== true) return "you can only delete your own messages for everyone"
  if (row.deleted === true) return "that message is already deleted"
  if (noteToSelf !== true && nowMs - row.ts >= DELETE_FOR_EVERYONE_MS)
    return "Signal only allows delete for everyone within 24 hours of sending; delete it for me instead"
  return ""
}

// ---------------------------------------------------------------- presentation

// The letter drawn in a conversation's tile, the way Omarchy's notifications
// mark a source that has no icon.
function initial(name) {
  var s = singleLine(name, 80).replace(/^[\s+@#~\-_.()\[\]]+/, "")
  if (!s) return "#"
  var first = Array.from(s)[0]
  return first.toUpperCase()
}

function pad2(n) { return (n < 10 ? "0" : "") + n }

// Local calendar day, as a sortable key: the thread draws a separator where it changes.
function dayKey(tsMs) {
  var ts = Number(tsMs) || 0
  if (!ts) return ""
  var d = new Date(ts)
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

var WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

function dayLabel(key, nowMs) {
  if (!key) return ""
  var now = new Date(Number(nowMs) || Date.now())
  if (key === dayKey(now.getTime())) return "Today"
  var y = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (key === dayKey(y.getTime())) return "Yesterday"
  var parts = key.split("-")
  var then = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
  var days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - then.getTime()) / 86400000)
  if (days > 0 && days < 7) return WEEKDAYS[then.getDay()]
  return key
}

function clockTime(tsMs) {
  var ts = Number(tsMs) || 0
  if (!ts) return ""
  var d = new Date(ts)
  return pad2(d.getHours()) + ":" + pad2(d.getMinutes())
}

// "👍 2  ❤️" from the bridge's {sender: emoji} map.
function reactionSummary(reactions) {
  if (!reactions || typeof reactions !== "object") return ""
  var order = [], count = {}
  for (var k in reactions) {
    var e = singleLine(reactions[k], 8)
    if (!e) continue
    if (!(e in count)) { count[e] = 0; order.push(e) }
    count[e] += 1
  }
  return order.map(function(e) { return count[e] > 1 ? e + " " + count[e] : e }).join("  ")
}

// True when version a is a release newer than b ("0.10.0" > "0.9.3").
function versionNewer(a, b) {
  var pa = /^(\d{1,4})\.(\d{1,4})\.(\d{1,5})$/.exec(String(a || "")), pb = /^(\d{1,4})\.(\d{1,4})\.(\d{1,5})$/.exec(String(b || ""))
  if (!pa || !pb) return false
  for (var i = 1; i <= 3; i++) {
    if (Number(pa[i]) !== Number(pb[i])) return Number(pa[i]) > Number(pb[i])
  }
  return false
}

// Disappearing-message timers, the ones Signal's own clients offer (seconds).
var TIMERS = [
  { seconds: 0, label: "Off" }, { seconds: 30, label: "30s" }, { seconds: 300, label: "5m" },
  { seconds: 3600, label: "1h" }, { seconds: 8 * 3600, label: "8h" }, { seconds: 86400, label: "1d" },
  { seconds: 7 * 86400, label: "1w" }, { seconds: 28 * 86400, label: "4w" }
]

// "30s", "5m", "2h", "1d", "4w" for a timer in seconds; "" when it is off or not a sane number.
function timerLabel(seconds) {
  var n = Number(seconds)
  if (!isFinite(n) || n <= 0 || n > 366 * 86400) return ""
  n = Math.floor(n)
  var units = [["w", 604800], ["d", 86400], ["h", 3600], ["m", 60]]
  for (var i = 0; i < units.length; i++) {
    if (n >= units[i][1] && n % units[i][1] === 0) return (n / units[i][1]) + units[i][0]
  }
  return n + "s"
}

// The timer of one conversation, out of the bridge's "conversations" answer.
function timerOf(conversations, key) {
  var list = Array.isArray(conversations) ? conversations : []
  for (var i = 0; i < list.length; i++) {
    if (list[i] && list[i].key === key) {
      var n = Number(list[i].expiration)
      return isFinite(n) && n > 0 ? Math.floor(n) : 0
    }
  }
  return 0
}

// Rows for the message list of a conversation window, oldest first. Every
// field is a string, number or boolean: the rows go into a ListModel.
function threadRows(rows, limit) {
  var list = Array.isArray(rows) ? rows.slice(-(limit || 60)) : []
  var prevSender = null, prevDay = ""
  return list.map(function(m) {
    var body = cleanText(m.body || "", 4000)
    var atts = Array.isArray(m.attachments) ? m.attachments : []
    if (m.deleted) { body = "This message was deleted"; atts = [] }
    var image = "", imageW = 0, imageH = 0
    for (var i = 0; i < atts.length; i++) {
      if (atts[i] && typeof atts[i].path === "string" && atts[i].path.charAt(0) === "/" && /^image\//.test(atts[i].contentType || "")) {
        image = atts[i].path
        imageW = Number(atts[i].width) || 0
        imageH = Number(atts[i].height) || 0
        break
      }
    }
    var filesText = atts.map(function(a) { return singleLine((a && a.filename) || "attachment", 80) }).join(", ")
    var ts = typeof m.ts === "number" ? m.ts : 0
    var outgoing = m.outgoing === true
    var sender = typeof m.sender === "string" ? m.sender : ""
    var day = dayKey(ts)
    // A run of messages from one person on one day shows the name once.
    var runKey = (outgoing ? "me" : sender) + "|" + day
    var firstOfRun = runKey !== prevSender
    prevSender = runKey
    var newDay = day !== prevDay
    prevDay = day
    return {
      ts: ts,
      who: outgoing ? "You" : singleLine(m.senderName || "?", 80),
      sender: sender,
      outgoing: outgoing,
      body: body,
      filesText: filesText,
      image: image,
      imageW: imageW,
      imageH: imageH,
      status: singleLine(m.status || "", 20),
      edited: m.edited === true,
      deleted: m.deleted === true,
      quote: singleLine(m.quoteText || "", 200),
      reactions: reactionSummary(m.reactions),
      day: day,
      newDay: newDay,
      firstOfRun: firstOfRun,
      time: clockTime(ts)
    }
  })
}

if (typeof module !== "undefined") {
  module.exports = {
    cleanText: cleanText,
    singleLine: singleLine,
    parseEventLine: parseEventLine,
    toastFromMessage: toastFromMessage,
    sortConversations: sortConversations,
    filterRows: filterRows,
    unreadLabel: unreadLabel,
    relativeTime: relativeTime,
    isConversationKey: isConversationKey,
    parseDnd: parseDnd,
    tuiArgv: tuiArgv,
    requestLine: requestLine,
    parseBridgeLine: parseBridgeLine,
    sendParams: sendParams,
    reactParams: reactParams,
    deleteRequest: deleteRequest,
    deleteForEveryoneRefusal: deleteForEveryoneRefusal,
    initial: initial,
    dayKey: dayKey,
    dayLabel: dayLabel,
    clockTime: clockTime,
    reactionSummary: reactionSummary,
    versionNewer: versionNewer,
    TIMERS: TIMERS,
    timerLabel: timerLabel,
    timerOf: timerOf,
    threadRows: threadRows
  }
}
