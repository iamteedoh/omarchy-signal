// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// The shell's connection to the bridge: one Unix socket, newline-delimited
// JSON, the same protocol the terminal client speaks
// (lib/omarchy_signal/protocol.py).
//
// It replaces a Python process per action. Opening a conversation used to
// start five or six interpreters (history, mark-read, names, contacts, the
// window helpers); it is now a few lines written to a socket that is already
// open, which is what removed the CPU spike on every open.
//
// Trust: the socket lives in $XDG_RUNTIME_DIR/omarchy-signal (0700) with mode
// 0600 and the bridge checks every peer with SO_PEERCRED. Requests are built
// with JSON.stringify (Model.requestLine) and validated by the bridge against
// its schema; nothing here ever reaches a shell. What comes back was sanitised
// by the bridge and is cleaned again by Model.js before it is shown.
Item {
  id: bridge
  visible: false

  readonly property string socketPath: (Quickshell.env("OMARCHY_SIGNAL_RUNTIME_DIR") || Quickshell.env("XDG_RUNTIME_DIR") || "") + "/omarchy-signal/bridge.sock"
  // The socket is open and the bridge has said hello.
  property bool online: false
  // False for a consumer that only wants to ask (the bar widget, when the
  // service already holds the event stream).
  property bool subscribe: true
  property bool active: true

  // An unsolicited event, or "hello"/"status" describing the bridge itself.
  // Going offline is announced as a status event, so a consumer has one path.
  signal event(string name, var data)

  property int nextId: 1
  property var pending: ({})               // id -> { cb, deadline }

  // Send a request. `cb(ok, result, error)` is called exactly once: with the
  // result, with the bridge's error, or with "offline"/"timed out".
  function request(op, params, cb, timeoutMs) {
    var done = typeof cb === "function" ? cb : null
    if (!bridge.online) { if (done) done(false, null, "the Signal bridge is offline"); return false }
    var id = bridge.nextId
    bridge.nextId = id >= 2000000000 ? 1 : id + 1
    var line = Model.requestLine(id, op, params)
    if (line === null || line.length > 900000) { if (done) done(false, null, "invalid request"); return false }
    bridge.pending[id] = { cb: done, deadline: Date.now() + (timeoutMs > 0 ? timeoutMs : 30000) }
    sock.write(line)
    sock.flush()
    sweep.start()
    return true
  }

  function failAll(reason) {
    var old = bridge.pending
    bridge.pending = ({})
    for (var id in old) if (old[id].cb) old[id].cb(false, null, reason)
  }

  function handleLine(line) {
    var msg = Model.parseBridgeLine(line)
    if (!msg) return
    if (msg.kind === "reply") {
      var waiting = bridge.pending[msg.id]
      if (!waiting) return
      delete bridge.pending[msg.id]
      if (waiting.cb) waiting.cb(msg.ok, msg.ok ? msg.result : null, msg.ok ? "" : (msg.error || "request failed"))
      return
    }
    if (msg.name === "hello") {
      bridge.online = true
      if (bridge.subscribe) bridge.request("subscribe", {}, null)
    }
    bridge.event(msg.name, msg.data)
  }

  Socket {
    id: sock
    path: bridge.socketPath
    connected: bridge.active && bridge.socketPath.charAt(0) === "/"
    parser: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { bridge.handleLine(line) }
    }
    onConnectionStateChanged: {
      if (sock.connected) return
      var was = bridge.online
      bridge.online = false
      bridge.failAll("the Signal bridge went away")
      if (was) bridge.event("status", { connected: false, linked: false, error: "bridge connection lost" })
    }
    onError: function(error) {
      if (!bridge.online) bridge.event("status", { connected: false, linked: false, error: "bridge offline" })
    }
  }

  // The bridge may not be up yet (login, an update restarting it): keep asking.
  Timer {
    id: retry
    interval: 3000
    repeat: true
    running: bridge.active && !sock.connected
    onTriggered: sock.connected = true
  }

  // A request the bridge never answers must not hold its callback forever.
  Timer {
    id: sweep
    interval: 1000
    repeat: true
    onTriggered: {
      var now = Date.now()
      var left = 0
      for (var id in bridge.pending) {
        var p = bridge.pending[id]
        if (p.deadline > now) { left++; continue }
        delete bridge.pending[id]
        if (p.cb) p.cb(false, null, "the Signal bridge did not answer in time")
      }
      if (left === 0) stop()
    }
  }

  Component.onDestruction: bridge.failAll("closing")
}
