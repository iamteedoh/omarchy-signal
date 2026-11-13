// SPDX-License-Identifier: GPL-3.0-or-later
// Run with: node --test tests/qml
const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("path")
const M = require(path.join(__dirname, "..", "..", "Model.js"))

test("cleanText strips controls, bidi, private use and caps length", () => {
  assert.equal(M.cleanText("a\x1b[31mb‮c⁦d﻿ef\x9bg"), "a[31mbcdefg")
  assert.equal(M.cleanText("x\r\ny\rz"), "x\ny\nz")
  assert.equal(M.cleanText(null), "")
  assert.equal(M.cleanText(12), "12")
  assert.equal(M.cleanText("x".repeat(10), 3), "xxx")
  assert.equal(M.cleanText("a\u{F0C9A}b\u00adc"), "abc")     // supplementary private use (Nerd Font glyphs) and soft hyphen
  assert.equal(M.cleanText("Ünïcödé ✓ 日本語 🎉"), "Ünïcödé ✓ 日本語 🎉")
})

test("singleLine collapses newlines", () => {
  assert.equal(M.singleLine("  a \n  b\n\nc  "), "a b c")
})

test("parseEventLine accepts only {event, data}", () => {
  assert.deepEqual(M.parseEventLine('{"event":"unread","data":{"total":3}}'), { event: "unread", data: { total: 3 } })
  assert.deepEqual(M.parseEventLine('{"event":"x","data":[1]}'), { event: "x", data: {} })
  assert.equal(M.parseEventLine(""), null)
  assert.equal(M.parseEventLine("not json"), null)
  assert.equal(M.parseEventLine("[1,2]"), null)
  assert.equal(M.parseEventLine('{"data":{}}'), null)
  assert.equal(M.parseEventLine('{"event":5}'), null)
  assert.equal(M.parseEventLine("x".repeat(9 * 1024 * 1024)), null)
})

test("toastFromMessage honours notify flag and sanitises", () => {
  const data = { notify: true, conversation: "number:+15550002222", senderName: "Trin\x1b[0mity", conversationName: "Trinity",
                 preview: "hello\nworld", ts: 5, isGroup: false, attachments: [{}, {}] }
  const t = M.toastFromMessage(data, true)
  assert.equal(t.title, "Trin[0mity")
  assert.equal(t.body, "hello world")
  assert.equal(t.attachments, 2)
  assert.equal(M.toastFromMessage({ ...data, notify: false }), null)
  assert.equal(M.toastFromMessage({ ...data, conversation: 5 }), null)
  assert.equal(M.toastFromMessage(null), null)
  assert.equal(M.toastFromMessage(data, false).body, "New message")
  const g = M.toastFromMessage({ ...data, isGroup: true, conversationName: "Crew" }, true)
  assert.equal(g.title, "Trin[0mity · Crew")
})

test("sortConversations newest first, unread breaks ties", () => {
  const out = M.sortConversations([{ key: "a", lastTs: 1 }, { key: "b", lastTs: 5 }, { key: "c", lastTs: 5, unread: 2 }])
  assert.deepEqual(out.map(c => c.key), ["c", "b", "a"])
  assert.deepEqual(M.sortConversations(null), [])
})

test("filterRows and unreadLabel", () => {
  const rows = [{ name: "Trinity", sub: "+1555" }, { name: "Neo", sub: "" }]
  assert.equal(M.filterRows(rows, "TRIN").length, 1)
  assert.equal(M.filterRows(rows, "1555").length, 1)
  assert.equal(M.filterRows(rows, "").length, 2)
  assert.equal(M.unreadLabel(0), "")
  assert.equal(M.unreadLabel(7), "7")
  assert.equal(M.unreadLabel(150), "99+")
  assert.equal(M.unreadLabel("x"), "")
})

test("relativeTime", () => {
  const now = 1_700_000_000_000
  assert.equal(M.relativeTime(now - 10_000, now), "now")
  assert.equal(M.relativeTime(now - 5 * 60_000, now), "5m")
  assert.equal(M.relativeTime(now - 3 * 3_600_000, now), "3h")
  assert.equal(M.relativeTime(now - 2 * 86_400_000, now), "2d")
  assert.equal(M.relativeTime(0, now), "")
})

test("tuiArgv never produces a shell string and validates keys", () => {
  assert.deepEqual(M.tuiArgv("number:+15550002222"), ["omarchy-signal", "open", "number:+15550002222"])
  assert.deepEqual(M.tuiArgv("number:+1; rm -rf ~"), ["omarchy-signal", "open"])
  assert.deepEqual(M.tuiArgv("$(evil)"), M.tuiArgv(""))
})

test("requestLine writes one JSON line and cannot be steered by its parameters", () => {
  assert.equal(M.requestLine(7, "history", { conversation: "number:+1", limit: 60 }),
    '{"conversation":"number:+1","limit":60,"id":7,"op":"history"}\n')
  // A parameter may not replace the id or the op, whatever a caller passes in.
  assert.deepEqual(JSON.parse(M.requestLine(7, "markRead", { id: 99, op: "shutdown", conversation: "number:+1" })),
    { conversation: "number:+1", id: 7, op: "markRead" })
  // Text with newlines stays one line: the bridge reads a request per line.
  const line = M.requestLine(1, "send", { text: "a\nb\r\n{\"op\":\"shutdown\"}" })
  assert.equal(line.split("\n").length, 2)
  assert.equal(line.endsWith("\n"), true)
  for (const op of ["", "shut down", "a;b", "x".repeat(40), null, 5]) assert.equal(M.requestLine(1, op, {}), null)
  for (const id of [0, -1, NaN, "1", null]) assert.equal(M.requestLine(id, "status", {}), null)
  assert.equal(M.requestLine(1, "status"), '{"id":1,"op":"status"}\n')
})

test("parseBridgeLine tells events from replies and rejects the rest", () => {
  assert.deepEqual(M.parseBridgeLine('{"event":"unread","data":{"total":3}}'), { kind: "event", name: "unread", data: { total: 3 } })
  assert.deepEqual(M.parseBridgeLine('{"event":"x","data":[1]}'), { kind: "event", name: "x", data: {} })
  assert.deepEqual(M.parseBridgeLine('{"event":"x"}'), { kind: "event", name: "x", data: {} })
  assert.deepEqual(M.parseBridgeLine('{"id":4,"ok":true,"result":[1,2]}'),
    { kind: "reply", id: 4, ok: true, result: [1, 2], error: "", code: "" })
  assert.deepEqual(M.parseBridgeLine('{"id":4,"ok":false,"error":"no\\u001b[31m such\\nmessage","code":"invalid"}'),
    { kind: "reply", id: 4, ok: false, result: undefined, error: "no[31m such message", code: "invalid" })
  for (const junk of ["", "not json", "[1]", "42", '"str"', "null", '{"id":"4","ok":true}', '{"id":4}', '{"ok":true}', '{"event":5}'])
    assert.equal(M.parseBridgeLine(junk), null, junk)
})

test("sendParams carries attachments and quotes safely", () => {
  assert.deepEqual(M.sendParams("number:+1", "hi", ["/home/u/a.png", "relative.png", 5], { ts: 12, author: "number:+2", text: "q\nx" }),
    { conversation: "number:+1", text: "hi", attachments: ["/home/u/a.png"], quoteTs: 12, quoteAuthor: "number:+2", quoteText: "q x" })
  assert.equal(M.sendParams("number:+1", "  ", []), null)
  assert.deepEqual(M.sendParams("number:+1", "", ["/x/y.jpg"]), { conversation: "number:+1", text: "", attachments: ["/x/y.jpg"] })
  assert.equal(M.sendParams("bad key", "hi", []), null)
  assert.equal(M.sendParams("number:+1", "hi\x00there", []).text, "hithere")
  assert.equal("quoteTs" in M.sendParams("number:+1", "hi", [], { ts: 0, author: "number:+2" }), false)
  assert.equal("quoteTs" in M.sendParams("number:+1", "hi", [], { ts: 5, author: "not a key" }), false)
  assert.equal(M.sendParams("number:+1", "", Array.from({ length: 20 }, (_, i) => "/f/" + i)).attachments.length, 8)
})

test("reactParams and deleteRequest validate what they are given", () => {
  assert.deepEqual(M.reactParams("number:+1", 5, "number:+2", "🔥"),
    { conversation: "number:+1", ts: 5, author: "number:+2", emoji: "🔥", remove: false })
  assert.equal(M.reactParams("number:+1", 5, "number:+2", "🔥", true).remove, true)
  assert.equal(M.reactParams("number:+1", 0, "number:+2", "🔥"), null)
  assert.equal(M.reactParams("number:+1", 5, "x", "🔥"), null)
  assert.equal(M.reactParams("number:+1", 5, "number:+2", "\x1b"), null)

  assert.deepEqual(M.deleteRequest("number:+1", 5, false, "number:+2"),
    { op: "deleteLocal", params: { conversation: "number:+1", ts: 5, sender: "number:+2" } })
  assert.deepEqual(M.deleteRequest("number:+1", 5, false, ""), { op: "deleteLocal", params: { conversation: "number:+1", ts: 5 } })
  assert.deepEqual(M.deleteRequest("number:+1", 5, false, "$(id)"), { op: "deleteLocal", params: { conversation: "number:+1", ts: 5 } })
  assert.deepEqual(M.deleteRequest("number:+1", 5, true, "number:+2"), { op: "delete", params: { conversation: "number:+1", ts: 5 } })
  assert.equal(M.deleteRequest("bad key", 5, false), null)
  assert.equal(M.deleteRequest("number:+1", 0, true), null)
  assert.equal(M.deleteRequest("number:+1", "5", true), null)
})

test("what the views draw: initials, days, clock time, reactions, versions", () => {
  assert.equal(M.initial("trinity"), "T")
  assert.equal(M.initial("  +15550002222"), "1")
  assert.equal(M.initial("🐇 Rabbit"), "🐇")
  assert.equal(M.initial(""), "#")
  assert.equal(M.initial("\x1b\u202e"), "#")

  const noon = new Date(2026, 10, 12, 12, 0, 0).getTime()
  assert.equal(M.dayKey(noon), "2026-11-12")
  assert.equal(M.dayKey(0), "")
  assert.equal(M.dayLabel("2026-11-12", noon), "Today")
  assert.equal(M.dayLabel("2026-11-11", noon), "Yesterday")
  assert.equal(M.dayLabel("2026-11-09", noon), "Monday")
  assert.equal(M.dayLabel("2026-11-05", noon), "2026-11-05")       // a week or more: the date
  assert.equal(M.dayLabel("2026-11-20", noon), "2026-11-20")       // a clock set wrong: never "in the future" words
  assert.equal(M.dayLabel("", noon), "")
  assert.equal(M.clockTime(new Date(2026, 10, 12, 9, 5).getTime()), "09:05")
  assert.equal(M.clockTime(0), "")

  assert.equal(M.reactionSummary({ a: "👍", b: "👍", c: "❤️" }), "👍 2  ❤️")
  assert.equal(M.reactionSummary({ a: "\x1b" }), "")
  assert.equal(M.reactionSummary(null), "")

  assert.equal(M.versionNewer("0.10.0", "0.9.9"), true)
  assert.equal(M.versionNewer("0.5.0", "0.5.0"), false)
  assert.equal(M.versionNewer("0.4.9", "0.5.0"), false)
  for (const junk of ["", "1.2", "v1.2.3", "1.2.3; rm", null]) assert.equal(M.versionNewer(junk, "0.0.1"), false)
})

test("parseDnd reads Omarchy's state file", () => {
  assert.equal(M.parseDnd('{"version":3,"dnd":true}'), true)
  assert.equal(M.parseDnd('{"version":3,"dnd":false}'), false)
  assert.equal(M.parseDnd(""), false)
  assert.equal(M.parseDnd("garbage"), false)
  assert.equal(M.parseDnd('{"dnd":"true"}'), false)
})

test("threadRows normalises history rows", () => {
  const day1 = new Date(2026, 10, 11, 23, 50).getTime(), day2 = new Date(2026, 10, 12, 0, 10).getTime()
  const rows = M.threadRows([
    { ts: day1, senderName: "Trin\x1bity", sender: "number:+2", body: "hi", attachments: [{ filename: "a.png", contentType: "image/png", path: "/p/a.png", width: 640, height: 400 }], reactions: { "number:+1": "👍" } },
    { ts: day1 + 1000, senderName: "Trinity", sender: "number:+2", body: "again" },
    { ts: day2, senderName: "Trinity", sender: "number:+2", body: "next day", attachments: [{ filename: "x.png", contentType: "image/png", path: "relative.png" }] },
    { ts: day2 + 1000, outgoing: true, body: "x", deleted: true, status: "read", edited: true, quoteText: "hi", attachments: [{ filename: "gone.png", contentType: "image/png", path: "/p/gone.png" }] },
    "junk"
  ].filter(x => typeof x === "object"), 10)
  assert.equal(rows[0].who, "Trinity"); assert.equal(rows[0].image, "/p/a.png"); assert.equal(rows[0].reactions, "👍"); assert.equal(rows[0].filesText, "a.png")
  assert.deepEqual([rows[0].imageW, rows[0].imageH], [640, 400])       // the bubble is sized before the picture decodes
  assert.equal(rows[2].image, "")                                      // only an absolute path is ever loaded
  assert.equal(rows[3].body, "This message was deleted"); assert.equal(rows[3].who, "You"); assert.equal(rows[3].edited, true)
  assert.equal(rows[3].deleted, true); assert.equal(rows[0].deleted, false)
  assert.equal(rows[3].image, ""); assert.equal(rows[3].filesText, "") // a deleted message shows nothing it carried
  // Day separators and sender runs.
  assert.deepEqual(rows.map(r => r.newDay), [true, false, true, false])
  assert.deepEqual(rows.map(r => r.firstOfRun), [true, false, true, true])
  assert.equal(rows[0].day, "2026-11-11"); assert.equal(rows[2].day, "2026-11-12"); assert.equal(rows[0].time, "23:50")
  // Every field must be a flat value: the rows are stored in a ListModel.
  for (const r of rows) for (const k in r) assert.ok(["string", "number", "boolean"].includes(typeof r[k]), k)
  assert.deepEqual(M.threadRows(null), [])
})

test("Emoji.js converts after a space", () => {
  const E = require(path.join(__dirname, "..", "..", "Emoji.js"))
  // cursor positions are UTF-16 units, which is what a QML TextField uses
  assert.deepEqual(E.convertBeforeCursor("hey :D ", 7), { text: "hey 😃 ", cursor: 7 })
  assert.deepEqual(E.convertBeforeCursor("a :smile: ", 10), { text: "a 😄 ", cursor: 5 })
  assert.equal(E.convertBeforeCursor("see http://x/ ", 14), null)
  assert.equal(E.convertBeforeCursor("a :nope: ", 9), null)
  assert.equal(E.lookup("SMILE"), "😄")
})

// The same cases the bridge's Python rule is tested against (tests/python/test_protocol_rpc.py),
// and the ones the sigdesk ports share.
test("shared vectors (tests/vectors/model.json) hold", () => {
  const V = require(path.join(__dirname, "..", "vectors", "model.json"))
  for (const c of V.cleanText) assert.equal(M.cleanText(c.in, c.max || undefined), c.out, JSON.stringify(c))
  for (const c of V.singleLine) assert.equal(M.singleLine(c.in, c.max || undefined), c.out, JSON.stringify(c))
  for (const c of V.isConversationKey) assert.equal(M.isConversationKey(c.in), c.out, JSON.stringify(c))
  for (const c of V.unreadLabel) assert.equal(M.unreadLabel(c.in), c.out, JSON.stringify(c))
  for (const c of V.relativeTime) assert.equal(M.relativeTime(c.ts, c.now), c.out, JSON.stringify(c))
  assert.deepEqual(M.sortConversations(V.sortConversations.in).map(c => c.key), V.sortConversations.out)
  assert.ok(V.deleteForEveryoneRefusal.length >= 5)
  for (const c of V.deleteForEveryoneRefusal)
    assert.equal(M.deleteForEveryoneRefusal({ ts: c.ts, outgoing: c.outgoing, deleted: c.deleted }, c.now, c.noteToSelf), c.out, c.name)
  for (const c of V.filterRows.cases) assert.deepEqual(M.filterRows(V.filterRows.rows, c.query).map(r => r.key), c.out, c.query)
})
