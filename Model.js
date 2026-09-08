// Pure helpers shared by Service.qml and BarWidget.qml. No Qt imports and no
// .pragma library (node would choke on it), so the
// same file runs under node for the unit tests (see tests/qml).


// Characters that must never reach a Text element even in PlainText mode:
// C0/C1 controls, bidi overrides/isolates, BOM and the private-use plane a
// remote sender could use to draw fake UI glyphs. Mirrors
// lib/omarchy_signal/sanitize.py.
var CONTROL_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f؜‎‏‪-‮⁦-⁩﻿-]/g

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
// array: it goes through Util.execArgv, never through a shell string.
function tuiArgv(conversationKey) {
  var argv = ["omarchy-signal", "open"]
  if (isConversationKey(conversationKey)) argv.push(conversationKey)
  return argv
}

// argv for a reply typed into the popup. The text travels glued to its flag
// ("--message=...") so argparse never mistakes a message that starts with "-"
// for an option, and the key comes after "--" for the same reason.
function sendArgv(conversationKey, text) {
  if (!isConversationKey(conversationKey)) return null
  var body = cleanText(text, 60000)
  if (!body.trim()) return null
  return ["omarchy-signal", "send", "--json", "--message=" + body, "--", conversationKey]
}

// argv for a reply with an optional quote and attachments.
function sendArgvFull(conversationKey, text, attachments, quote) {
  if (!isConversationKey(conversationKey)) return null
  var body = cleanText(text, 60000)
  var files = Array.isArray(attachments) ? attachments.filter(function(a) { return typeof a === "string" && a.charAt(0) === "/" }) : []
  if (!body.trim() && files.length === 0) return null
  var argv = ["omarchy-signal", "send", "--json", "--message=" + body]
  for (var i = 0; i < files.length; i++) argv.push("-a", files[i])
  if (quote && typeof quote.ts === "number" && quote.ts > 0 && isConversationKey(quote.author))
    argv.push("--quote-ts", String(quote.ts), "--quote-author", quote.author, "--quote-text", singleLine(quote.text || "", 200))
  argv.push("--", conversationKey)
  return argv
}

function reactArgv(conversationKey, ts, author, emojiText, remove) {
  if (!isConversationKey(conversationKey) || !isConversationKey(author)) return null
  if (typeof ts !== "number" || ts <= 0) return null
  var e = singleLine(emojiText, 16)
  if (!e) return null
  var argv = ["omarchy-signal", "react", "--", conversationKey, String(ts), author, e]
  if (remove) argv.splice(2, 0, "--remove")
  return argv
}

// Rows for the message list of a conversation window, oldest first.
function threadRows(rows, limit) {
  var list = Array.isArray(rows) ? rows.slice(-(limit || 60)) : []
  return list.map(function(m) {
    var body = cleanText(m.body || "", 4000)
    var atts = Array.isArray(m.attachments) ? m.attachments : []
    if (m.deleted) body = "(message deleted)"
    var image = "", imageW = 0, imageH = 0
    for (var i = 0; i < atts.length; i++) {
      if (atts[i] && typeof atts[i].path === "string" && /^image\//.test(atts[i].contentType || "")) {
        image = atts[i].path
        imageW = Number(atts[i].width) || 0
        imageH = Number(atts[i].height) || 0
        break
      }
    }
    var filesText = atts.map(function(a) { return singleLine(a.filename || "attachment", 80) }).join(", ")
    var reactions = []
    if (m.reactions && typeof m.reactions === "object") for (var k in m.reactions) reactions.push(singleLine(m.reactions[k], 8))
    return {
      ts: typeof m.ts === "number" ? m.ts : 0,
      who: m.outgoing ? "You" : singleLine(m.senderName || "?", 80),
      sender: typeof m.sender === "string" ? m.sender : "",
      outgoing: m.outgoing === true,
      body: body,
      filesText: filesText,
      image: image,
      status: singleLine(m.status || "", 20),
      edited: m.edited === true,
      quote: singleLine(m.quoteText || "", 200),
      reactions: reactions.join(" ")
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
    sendArgv: sendArgv,
    sendArgvFull: sendArgvFull,
    reactArgv: reactArgv,
    threadRows: threadRows
  }
}
