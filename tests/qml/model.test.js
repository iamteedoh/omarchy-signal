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

test("argv builders never produce shell strings and validate keys", () => {
  assert.deepEqual(M.tuiArgv("number:+15550002222"),
    ["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.signal", "omarchy-signal", "tui", "number:+15550002222"])
  assert.deepEqual(M.tuiArgv("number:+1; rm -rf ~"),
    ["omarchy-launch-or-focus-tui", "--app-id=org.omarchy.signal", "omarchy-signal", "tui"])
  assert.deepEqual(M.tuiArgv("$(evil)"), M.tuiArgv(""))
  assert.deepEqual(M.sendArgv("group:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==", "-m looks like a flag"),
    ["omarchy-signal", "send", "--json", "--message=-m looks like a flag", "--", "group:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="])
  assert.equal(M.sendArgv("number:+1", "   "), null)
  assert.equal(M.sendArgv("bad key", "hi"), null)
  assert.equal(M.sendArgv("number:+1", "hi\x00there")[3], "--message=hithere")
})
