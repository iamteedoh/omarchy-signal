// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Controls as QQC
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model
import "Emoji.js" as Emoji

// One conversation: thread, composer, attachments, quote, reactions. Used by
// the click-to-reply popup and, unchanged, by the tabs of the chat window, so
// both look and behave the same. Everything shown is PlainText. Messages,
// reactions and deletes go to the bridge as requests on its socket (Bridge.qml);
// the two things that still start a program (the file listing for the
// attachment picker and reading the clipboard) run the CLI as an argv array.
//
// Colours, spacing, rounding and fonts all come from Omarchy's Style and Color
// singletons, so the view follows whatever theme is active.
Item {
  id: view

  // Omarchy only grew Util.execArgv during the 4.0.x series (absent in 4.0.0,
  // present by 4.0.4), so calling it throws on an Omarchy that predates it and
  // every action here silently does nothing. Vendor it instead of depending on
  // the version. Same form as upstream: argv is passed as positional
  // parameters and never interpolated into a shell string, and `bash -lc`
  // keeps the login PATH that resolves omarchy-* helpers.
  function execArgv(argv) {
    Quickshell.execDetached(["bash", "-lc", 'exec "$@"', "bash"].concat(argv))
  }

  // Animation length in ms. Omarchy 4.5 added a duration helper that returns 0
  // while `omarchy toggle animations` has them off; older releases do not have
  // it, so it is looked up by name rather than relied on.
  function ms(d) {
    var f = Style["duration"]
    return typeof f === "function" ? f(d) : d
  }

  required property string cliPath
  required property var bridge             // Bridge.qml
  property string conversationKey: ""
  property string conversationName: ""
  property bool connected: false
  property bool linked: false
  property bool detached: false          // true inside the chat window
  property string account: ""            // our own number, for reacting to our own messages
  property bool emojiAutoconvert: true   // :smile: / :D become emoji after the space (setting)
  property bool thumbnails: true         // picture tiles in the attachment picker (setting); off = plain list
  property int scrollSpeed: 9            // trackpad/wheel multiplier (setting)

  readonly property color fg: Commons.Color.popups.text
  readonly property color dim: Util.alpha(Commons.Color.popups.text, 0.55)
  readonly property color faint: Util.alpha(Commons.Color.popups.text, 0.38)
  readonly property color hairline: Util.alpha(Commons.Color.popups.text, 0.12)
  readonly property bool isGroup: view.conversationKey.indexOf("group:") === 0
  readonly property bool noteToSelf: view.account !== "" && view.conversationKey === "number:" + view.account

  // Qt Quick moves a Flickable by the raw trackpad pixel delta, which on a
  // Wayland touchpad is a few pixels per event; terminals and GTK apps scale
  // it. Take over wheel events and apply the multiplier ourselves.
  // Inertia: a swipe keeps gliding and eases out, a wheel notch glides
  // instead of stepping.
  property var kineticTarget: null
  property real velocity: 0          // px per second, positive = scrolling down
  property real lastWheelAt: 0

  // A ListView's content can start above y=0 (originY moves as rows get
  // measured), so the real bounds are relative to originY.
  function clampY(flick, y) {
    var minY = flick.originY !== undefined ? flick.originY : 0
    var maxY = Math.max(minY, minY + flick.contentHeight - flick.height)
    return Math.max(minY, Math.min(maxY, y))
  }

  function scrollBy(flick, ev) {
    if (view.scrollSpeed <= 0) { ev.accepted = false; return }   // 0 = leave scrolling to Qt
    var dy = 0
    var discrete = false
    if (ev.pixelDelta && ev.pixelDelta.y !== 0) dy = ev.pixelDelta.y * view.scrollSpeed
    else if (ev.angleDelta && ev.angleDelta.y !== 0) { dy = (ev.angleDelta.y / 120) * 60 * view.scrollSpeed; discrete = true }
    if (dy === 0) { ev.accepted = false; return }
    if (flick.moving) flick.cancelFlick()
    if (flick === list) view.markUserScrolling()
    var now = Date.now()
    var dt = Math.max(8, Math.min(120, now - view.lastWheelAt))
    view.lastWheelAt = now
    if (view.kineticTarget !== flick) { view.velocity = 0; view.kineticTarget = flick }
    if (discrete) {
      // Mouse wheel: each notch is a kick that the inertia loop plays out smoothly.
      view.velocity = view.velocity * 0.6 - dy * 14
      inertia.start()
    } else {
      // Trackpad: follow the finger now, remember its speed for the glide after release.
      flick.contentY = view.clampY(flick, flick.contentY - dy)
      var instant = -dy / (dt / 1000)
      view.velocity = view.velocity * 0.5 + instant * 0.5
      inertia.stop()
      gestureEnd.restart()
    }
    ev.accepted = true
  }

  Timer {
    id: gestureEnd
    interval: 70
    onTriggered: if (Math.abs(view.velocity) > 60) inertia.start(); else view.velocity = 0
  }

  // The glide runs on the display's frame clock, so it advances exactly once
  // per rendered frame whatever the refresh rate (no timer/vsync beating).
  FrameAnimation {
    id: inertia
    running: false
    onTriggered: {
      var flick = view.kineticTarget
      if (!flick) { stop(); return }
      var dt = Math.min(0.05, Math.max(0.001, frameTime))
      if (flick === list) view.markUserScrolling()
      var before = flick.contentY
      flick.contentY = view.clampY(flick, before + view.velocity * dt)
      view.velocity *= Math.pow(0.012, dt)          // friction: ~1.2% of the speed left after a second
      if (Math.abs(view.velocity) < 25 || flick.contentY === before) { view.velocity = 0; stop() }
    }
  }
  property bool stickToBottom: true      // follow new messages unless the user scrolled up
  property string typingName: ""

  signal requestClose()
  signal requestDetach()
  signal requestTerminal()
  signal requestRaise(string key)
  signal requestCloseTab(string key)

  property var siblings: []              // every tab of the chat window: [{key, name, unread}]

  // Esc peels one layer at a time: help → picker → menus and confirmations →
  // text selection → message actions → quote → the window.
  function handleEscape() {
    if (view.helpOpen) { view.helpOpen = false; composer.forceActiveFocus(); return true }
    if (view.pickerOpen) { view.pickerOpen = false; composer.forceActiveFocus(); return true }
    if (view.chatMenuOpen) { view.chatMenuOpen = false; view.confirmDeleteChat = false; composer.forceActiveFocus(); return true }
    if (view.confirmDeleteTs) { view.confirmDeleteTs = 0; composer.forceActiveFocus(); return true }
    if (view.selectionTs) { view.selectionTs = 0; composer.forceActiveFocus(); return true }
    if (view.emojiRowOpen || view.selectedTs) { view.emojiRowOpen = false; view.selectedTs = 0; return true }
    if (view.quote) { view.quote = null; return true }
    view.requestClose()
    return true
  }

  function cycleWindow(delta) {
    var tabs = Array.isArray(view.siblings) ? view.siblings : []
    if (tabs.length < 2) return
    var idx = -1
    for (var i = 0; i < tabs.length; i++) if (tabs[i].key === view.conversationKey) idx = i
    var next = tabs[(idx + delta + tabs.length) % tabs.length]
    if (next) view.requestRaise(next.key)
  }

  property bool helpOpen: false
  readonly property var legendKeys: [
    { key: "Enter", does: "send" }, { key: "Shift+Enter", does: "new line" }, { key: "Ctrl+V", does: "paste, pictures and files too" },
    { key: "Ctrl+O", does: "attach" }, { key: "Esc", does: "close" }
  ]
  readonly property var helpSections: [
    { title: "Messages", rows: [
      { key: "click", does: "Reply · React · Copy · Delete" }, { key: "right-click", does: "react" }, { key: "middle-click", does: "reply to it" },
      { key: "drag", does: "select text; Ctrl+C or right-click copies" }, { key: "click picture", does: "open it larger" } ] },
    { title: "Writing", rows: [
      { key: "Enter", does: "send" }, { key: "Shift+Enter", does: "new line" }, { key: ":smile:  :D", does: "become emoji after a space" },
      { key: "Ctrl+V", does: "paste text, a picture, or copied files" }, { key: "Ctrl+O", does: "attachment picker" } ] },
    { title: "Window", rows: [
      { key: "Ctrl+Tab", does: "next chat (Shift: previous)" }, { key: "Esc", does: "close one layer, then the chat" },
      { key: "Super+Alt+S", does: "park the chat window in the scratchpad" }, { key: "Super+Ctrl+G", does: "new conversation" },
      { key: "Super+Shift+G", does: "the terminal client" } ] }
  ]

  property int rowCount: 0
  property bool threadReady: false       // the first history of this conversation has been laid out
  property bool animateAdds: false       // new rows fade in; a whole thread loading does not
  ListModel { id: threadModel }

  // Sync the list in place: untouched rows keep their delegates, so a send
  // or an incoming message does not rebuild (and flicker) the whole thread.
  function applyThread(rows) {
    var wanted = {}
    for (var i = 0; i < rows.length; i++) wanted[rows[i].ts + ":" + (rows[i].outgoing ? 1 : 0)] = true
    for (var j = threadModel.count - 1; j >= 0; j--) {
      var cur = threadModel.get(j)
      if (!wanted[cur.ts + ":" + (cur.outgoing ? 1 : 0)]) threadModel.remove(j)
    }
    // Only a message or two arriving in a thread already on screen is animated.
    view.animateAdds = view.threadReady && threadModel.count > 0 && rows.length - threadModel.count <= 3
    for (var k = 0; k < rows.length; k++) {
      var row = rows[k]
      if (k < threadModel.count) {
        var have = threadModel.get(k)
        if (Number(have.ts) === Number(row.ts) && !!have.outgoing === !!row.outgoing) {
          if (have.body !== row.body || have.status !== row.status || have.reactions !== row.reactions || have.edited !== row.edited
              || have.deleted !== row.deleted || have.image !== row.image || have.filesText !== row.filesText || have.quote !== row.quote
              || have.newDay !== row.newDay || have.firstOfRun !== row.firstOfRun || have.who !== row.who) threadModel.set(k, row)
          continue
        }
      }
      threadModel.insert(k, row)
    }
    while (threadModel.count > rows.length) threadModel.remove(threadModel.count - 1)
    view.rowCount = rows.length
    view.animateAdds = false
  }
  property var quote: null               // {ts, author, text, who}
  property var attachments: []           // absolute paths
  property bool sending: false
  property string error: ""
  property real selectedTs: 0            // message with the action row open (Signal timestamps overflow a QML int)
  property bool emojiRowOpen: false
  readonly property var quickEmojis: ["👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "🎉"]

  implicitWidth: Style.space(640)
  implicitHeight: Style.space(560)

  // What was being written in each conversation, so switching tabs neither
  // loses a half-typed reply nor carries it into another chat.
  property var drafts: ({})
  property alias draft: composer.text

  function load(key, name) {
    if (view.conversationKey && view.conversationKey !== key) {
      if (composer.text.length > 0) view.drafts[view.conversationKey] = composer.text
      else delete view.drafts[view.conversationKey]
      composer.text = view.drafts[key] || ""
      composer.cursorPosition = composer.text.length
    }
    view.stickToBottom = true
    view.conversationKey = key
    view.conversationName = Model.singleLine(name || key.split(":").slice(1).join(":"), 80)
    view.threadReady = false
    threadModel.clear()
    view.rowCount = 0
    view.quote = null
    view.attachments = []
    view.error = ""
    view.notice = ""
    view.typingName = ""
    view.selectedTs = 0
    view.selectionTs = 0
    view.confirmDeleteTs = 0
    view.confirmDeleteChat = false
    view.chatMenuOpen = false
    view.emojiRowOpen = false
    view.pickerOpen = false
    view.reloadPending = false
    view.expiration = 0
    view.fetch()
    view.fetchTimer()
    view.markRead()
    Qt.callLater(function() { composer.forceActiveFocus() })
  }

  // Disappearing messages: the conversation's timer in seconds, 0 = off. It is
  // shown in the header and set from the header's menu.
  property int expiration: 0
  property bool settingTimer: false
  function fetchTimer() {
    var key = view.conversationKey
    if (!key || !view.bridge) return
    view.bridge.request("conversations", { includeArchived: true }, function(ok, rows) {
      if (ok && key === view.conversationKey) view.expiration = Model.timerOf(rows, key)
    })
  }
  function setTimer(seconds) {
    var key = view.conversationKey
    if (!key || view.settingTimer || seconds === view.expiration) return
    view.settingTimer = true
    view.bridge.request("setExpiration", { conversation: key, seconds: seconds }, function(ok, result, err) {
      view.settingTimer = false
      if (key !== view.conversationKey) return
      if (ok) {
        view.expiration = result && typeof result.expiration === "number" ? result.expiration : seconds
        view.notice = view.expiration > 0 ? "messages now disappear after " + Model.timerLabel(view.expiration) : "disappearing messages are off"
        noticeTimer.restart()
      } else {
        view.error = Model.singleLine(err || "could not change the timer", 160)
      }
    })
  }

  function markRead() {
    if (view.conversationKey) view.bridge.request("markRead", { conversation: view.conversationKey }, null)
  }

  property real savedY: 0
  property bool pinning: false
  property bool userScrolling: false

  function markUserScrolling() {
    view.userScrolling = true
    userScrollTimer.restart()
  }
  Timer { id: userScrollTimer; interval: 250; onTriggered: view.userScrolling = false }

  // Scroll to the end by setting contentY. Asking the ListView to position
  // itself at the end makes it re-lay out from the end and drop its visible
  // delegates for a few frames, which showed as a blank flash on every send.
  function scrollToEnd() {
    view.pinning = true
    list.contentY = view.clampY(list, list.originY + list.contentHeight - list.height)
    view.pinning = false
  }

  function pinToBottom() {
    view.scrollToEnd()
    view.stickToBottom = true
    settle.restart()          // rows may still be measuring: pin once more when they are done
  }
  Timer { id: settle; interval: 120; onTriggered: if (view.stickToBottom && !view.userScrolling) view.scrollToEnd() }

  property bool fetching: false
  property bool reloadPending: false

  // One history request at a time. A second one asked for while the first is
  // out waits for it, and a burst of events (a read receipt for every message
  // in a thread) becomes one request, not one each.
  function fetch() {
    if (!view.conversationKey) return
    if (view.fetching) { view.reloadPending = true; return }
    var key = view.conversationKey
    view.savedY = list.contentY
    view.fetching = view.bridge.request("history", { conversation: key, limit: 60 }, function(ok, rows, err) {
      view.fetching = false
      if (key === view.conversationKey) {
        if (ok && Array.isArray(rows)) {
          view.applyThread(Model.threadRows(rows, 60))
          var y = view.savedY
          Qt.callLater(function() {
            if (view.stickToBottom) view.pinToBottom(); else list.contentY = view.clampY(list, y)
            view.threadReady = true
          })
        } else if (!ok && view.connected) {
          view.error = Model.singleLine(err, 160)
        }
      }
      if (view.reloadPending) { view.reloadPending = false; view.fetch() }
    })
  }

  function reload() { coalesce.restart() }
  Timer { id: coalesce; interval: 40; onTriggered: view.fetch() }

  // The bridge came (back) up while this conversation is on screen.
  onConnectedChanged: if (view.connected && view.conversationKey) view.reload()

  // Service.qml feeds bridge events here; anything about this conversation refreshes the thread.
  function onEvent(name, data) {
    if (!data || data.conversation !== view.conversationKey) return
    if (name === "typing") {
      view.typingName = data.typing ? Model.singleLine(data.senderName || "", 40) : ""
      if (view.typingName) typingTimeout.restart()
      return
    }
    if (name === "conversationRemoved") { view.requestClose(); return }
    // The timer was changed here, in the terminal client, on the phone or by the other person.
    if (name === "conversation") {
      if (typeof data.expiration === "number") view.expiration = Math.max(0, Math.floor(data.expiration))
      return
    }
    // The message someone was typing has arrived: Signal sends no separate "stopped".
    if (name === "message" && data.outgoing !== true) view.typingName = ""
    if (name === "message" || name === "sent" || name === "edited" || name === "deleted" || name === "removed"
        || name === "reaction" || name === "receipt") {
      view.reload()
      if (name === "message") view.markRead()
    }
  }

  // Signal clients stop showing "typing" after 15 s without a refresh.
  Timer { id: typingTimeout; interval: 15000; onTriggered: view.typingName = "" }

  function send() {
    var params = Model.sendParams(view.conversationKey, composer.text, view.attachments, view.quote)
    if (!params || view.sending) return
    view.error = ""
    view.sending = view.bridge.request("send", params, function(ok, result, err) {
      view.sending = false
      if (ok && result && result.status !== "failed") {
        composer.text = ""
        delete view.drafts[view.conversationKey]
        view.quote = null
        view.attachments = []
        view.stickToBottom = true
        view.reload()
      } else {
        view.error = ok ? "Signal could not deliver that message" : Model.singleLine(err || "send failed", 160)
        view.reload()
      }
    }, 120000)
    if (!view.sending) view.error = "the Signal bridge is offline"
  }

  // Run one action against the bridge; say `done` when it worked, the bridge's reason when not.
  function act(op, params, done) {
    view.error = ""
    view.bridge.request(op, params, function(ok, result, err) {
      if (ok) { if (done) { view.notice = done; noticeTimer.restart() } }
      else view.error = Model.singleLine(err || "that did not work", 160)
      view.reload()
    }, 70000)
  }

  function react(row, emojiText) {
    var author = row.outgoing ? ("number:" + view.account) : row.sender
    var params = Model.reactParams(view.conversationKey, row.ts, author, emojiText)
    if (!params) { view.error = "cannot react to that message"; return }
    view.act("react", params, "")
    view.emojiRowOpen = false
    view.selectedTs = 0
  }

  property real confirmDeleteTs: 0       // the message whose Delete was clicked: the row asks before it does anything
  property bool confirmDeleteChat: false // the same for the whole conversation
  property bool chatMenuOpen: false      // the header's menu

  // Deleting. "For me" is local and sends nothing: the message stays on your
  // phone and for whoever sent it. "For everyone" is Signal's remote delete and
  // only works on your own messages.
  function deleteMessage(row, everyone) {
    var req = Model.deleteRequest(view.conversationKey, row.ts, everyone === true, row.outgoing ? "" : row.sender)
    if (!req) { view.error = "cannot delete that message"; return }
    view.act(req.op, req.params, everyone ? "deleted for everyone" : "deleted here")
    view.confirmDeleteTs = 0
    view.selectedTs = 0
  }

  // Why this message cannot be deleted for everyone, or "" when it can; the
  // bridge refuses the same cases (see deleteForEveryoneRefusal in Model.js).
  function everyoneRefusal(row) {
    return Model.deleteForEveryoneRefusal(row, Date.now(), view.noteToSelf)
  }

  function deleteChat() {
    if (!Model.isConversationKey(view.conversationKey)) return
    view.confirmDeleteChat = false
    view.chatMenuOpen = false
    // The bridge announces the removal as an event, which closes this view.
    view.bridge.request("deleteConversation", { conversation: view.conversationKey }, function(ok, result, err) {
      if (!ok) view.error = Model.singleLine(err || "could not delete the conversation", 160)
    })
  }

  // ---- clipboard
  property real selectionTs: 0           // the message whose text is selected; starting a selection elsewhere clears it
  property string notice: ""

  function copyText(t) {
    if (!t) return
    view.execArgv(["wl-copy", "--", t])
    view.notice = t.indexOf("\n") >= 0 ? "copied " + t.split("\n").length + " lines" : "copied"
    noticeTimer.restart()
  }
  Timer { id: noticeTimer; interval: 2500; onTriggered: view.notice = "" }

  function addAttachments(paths) {
    var added = []
    var list = view.attachments.slice()
    for (var i = 0; i < paths.length; i++) {
      var p = String(paths[i])
      if (p.charAt(0) !== "/" || list.indexOf(p) >= 0 || list.length >= 8) continue
      list.push(p)
      added.push(p)
    }
    if (added.length === 0) return 0
    view.attachments = list
    view.notice = added.length === 1 ? "attached " + added[0].split("/").pop() : "attached " + added.length + " files"
    noticeTimer.restart()
    return added.length
  }

  function quoteRow(row) {
    view.quote = { ts: row.ts, author: row.outgoing ? ("number:" + view.account) : row.sender, text: row.body, who: row.who }
    view.selectedTs = 0
    composer.forceActiveFocus()
  }

  // ---- attachment picker (thumbnails)
  property bool pickerOpen: false
  property string pickerDir: ""
  property string pickerParent: ""
  property var pickerRows: []
  property string pickerFilter: ""
  property int pickerIndex: 0
  readonly property string home: Quickshell.env("HOME") || ""

  function pickerMove(delta) {
    var n = view.pickerVisible.length
    if (n === 0) return
    view.pickerIndex = Math.max(0, Math.min(n - 1, view.pickerIndex + delta))
    if (view.thumbnails) grid.positionViewAtIndex(view.pickerIndex, GridView.Contain)
    else plainList.positionViewAtIndex(view.pickerIndex, ListView.Contain)
  }

  function pickerChoose() {
    var rows = view.pickerVisible
    if (rows.length) view.chooseRow(rows[Math.max(0, Math.min(rows.length - 1, view.pickerIndex))])
  }
  readonly property var quickDirs: ["Pictures", "Screenshots", "Downloads", "Documents", "Desktop"]

  function pickAttachment() {
    view.helpOpen = false
    view.pickerOpen = true
    view.pickerFilter = ""
    view.pickerIndex = 0
    view.listDir(view.pickerDir || (view.home + "/Pictures"))
    Qt.callLater(function() { pickerFilterField.forceActiveFocus() })
  }

  function listDir(dir) {
    view.pickerIndex = 0
    lsProc.run([view.cliPath, "ls-files", "--", dir])
  }

  function chooseRow(row) {
    if (row.isDir) { view.listDir(row.path); return }
    view.addAttachments([row.path])
    view.pickerOpen = false
    composer.forceActiveFocus()
  }

  readonly property var pickerVisible: {
    var q = Model.singleLine(view.pickerFilter, 80).toLowerCase()
    var rows = Array.isArray(view.pickerRows) ? view.pickerRows : []
    return q ? rows.filter(function(r) { return r.name.toLowerCase().indexOf(q) >= 0 }) : rows
  }

  // At most 500 rows of JSON; a ceiling and a deadline all the same (BoundedProcess.qml).
  BoundedProcess {
    id: lsProc
    maxChars: 2 * 1024 * 1024
    timeoutMs: 10000
    onFinished: function(ok, text) {
      if (!ok) { view.pickerRows = []; return }
      var obj = null
      try { obj = JSON.parse(text) } catch (e) { obj = null }
      if (!obj || !Array.isArray(obj.rows)) { view.pickerRows = []; return }
      view.pickerDir = String(obj.dir || "")
      view.pickerParent = String(obj.parent || "")
      view.pickerRows = obj.rows.filter(function(r) { return r && typeof r.path === "string" && r.path.charAt(0) === "/" })
    }
  }

  function fileGlyph(kind, isDir) {
    if (isDir) return "󰉋"
    return kind === "image" ? "󰋩" : kind === "video" ? "󰕧" : kind === "audio" ? "󰎈" : kind === "doc" ? "󰈙" : "󰈔"
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(10)

    // Header: who this is, how the channel is doing, and the window's own actions.
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(12)

      Avatar {
        name: view.conversationName
        size: Style.space(36)
        foreground: view.fg
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          Layout.fillWidth: true
          text: view.noteToSelf ? "Note to Self" : view.conversationName
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: view.fg
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          Layout.fillWidth: true
          readonly property bool alarm: !view.connected || !view.linked
          text: !view.connected ? "Bridge offline" : !view.linked ? "No account linked"
                : view.typingName.length > 0 ? (view.isGroup ? view.typingName + " is typing…" : "typing…")
                : view.expiration > 0 ? "End-to-end encrypted · 󰔛 messages disappear after " + Model.timerLabel(view.expiration)
                : "End-to-end encrypted"
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: alarm ? Commons.Color.urgent : view.typingName.length > 0 ? Commons.Color.accent : view.dim
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          Behavior on color { ColorAnimation { duration: view.ms(120) } }
        }
      }
      Button {
        iconText: "󰇘"
        tooltipText: "Conversation: disappearing messages, delete chat"
        visible: view.conversationKey.length > 0
        selected: view.chatMenuOpen
        onClicked: { view.chatMenuOpen = !view.chatMenuOpen; view.confirmDeleteChat = false }
      }
      Button { iconText: "󰆍"; tooltipText: "Open in the terminal client"; onClicked: view.requestTerminal() }
      Button { visible: !view.detached; iconText: "󰏌"; tooltipText: "Move to the chat window"; onClicked: view.requestDetach() }
      Button { iconText: "󰅖"; tooltipText: view.detached ? "Close this chat" : "Close"; onClicked: view.requestClose() }
    }

    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: view.hairline }

    // The conversation's own actions, behind the menu button so none of them is one stray click away.
    Item {
      id: chatMenu
      readonly property real shown: view.chatMenuOpen ? 1 : 0
      property real reveal: shown
      Behavior on reveal { NumberAnimation { duration: view.ms(140); easing.type: Easing.OutCubic } }
      Layout.fillWidth: true
      Layout.preferredHeight: chatMenuBody.implicitHeight * reveal
      visible: reveal > 0.01
      opacity: reveal
      clip: true
      ColumnLayout {
        id: chatMenuBody
        width: parent.width
        spacing: Style.space(8)
        // Disappearing messages: one click on a duration sets it for everyone in the chat.
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(4)
          Text {
            text: "Disappearing messages"
            textFormat: Text.PlainText
            color: view.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            rightPadding: Style.space(8)
          }
          Repeater {
            model: Model.TIMERS
            delegate: Button {
              required property var modelData
              objectName: "timer-" + modelData.seconds
              text: modelData.label
              bordered: true
              selected: view.expiration === modelData.seconds
              enabled: !view.settingTimer && view.linked
              tooltipText: modelData.seconds === 0 ? "Keep messages" : "New messages disappear " + modelData.label + " after they are seen"
              onClicked: view.setTimer(modelData.seconds)
            }
          }
          // A timer set elsewhere to a duration that is not one of the buttons.
          Text {
            visible: view.expiration > 0 && !Model.TIMERS.some(function(t) { return t.seconds === view.expiration })
            text: "now " + Model.timerLabel(view.expiration)
            textFormat: Text.PlainText
            color: view.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            leftPadding: Style.space(6)
          }
          Item { Layout.fillWidth: true }
        }
        RowLayout {
          id: chatMenuRow
          Layout.fillWidth: true
          spacing: Style.space(8)
          Button {
            bordered: true
            text: "Delete chat on this computer"
            visible: !view.confirmDeleteChat
            onClicked: view.confirmDeleteChat = true
          }
          Text {
            visible: view.confirmDeleteChat
            text: "Delete this chat here? It stays on your phone."
            textFormat: Text.PlainText
            color: view.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          Button { bordered: true; text: "Delete"; foreground: Commons.Color.urgent; visible: view.confirmDeleteChat; onClicked: view.deleteChat() }
          Button { bordered: true; text: "Cancel"; visible: view.confirmDeleteChat; onClicked: { view.confirmDeleteChat = false; view.chatMenuOpen = false } }
          Item { Layout.fillWidth: true }
        }
      }
    }

    // Tab strip: every conversation open in this window. Click switches the
    // content in place; the close mark closes a tab; Ctrl+Tab cycles.
    Flow {
      visible: view.detached && Array.isArray(view.siblings) && view.siblings.length > 1
      Layout.fillWidth: true
      spacing: Style.space(4)
      Repeater {
        model: view.detached ? view.siblings : []
        delegate: Rectangle {
          id: tab
          required property var modelData
          readonly property bool current: modelData.key === view.conversationKey
          width: tabRow.implicitWidth + Style.space(20)
          height: tabRow.implicitHeight + Style.space(10)
          radius: Style.cornerRadius
          color: current ? Style.selectedFillFor(view.fg, Commons.Color.accent) : (tabHover.containsMouse ? Style.hoverFillFor(view.fg, Commons.Color.accent) : "transparent")
          Behavior on color { ColorAnimation { duration: view.ms(120) } }
          MouseArea { id: tabHover; anchors.fill: parent; hoverEnabled: true; onClicked: if (!tab.current) view.requestRaise(tab.modelData.key) }
          Row {
            id: tabRow
            anchors.centerIn: parent
            spacing: Style.space(8)
            Rectangle {
              visible: tab.modelData.unread === true
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(6); height: width; radius: width / 2
              color: Commons.Color.accent
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: Model.singleLine(tab.modelData.name || tab.modelData.key, 28)
              textFormat: Text.PlainText
              color: tab.current ? view.fg : (tab.modelData.unread ? view.fg : view.dim)
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              font.bold: tab.modelData.unread === true
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "✕"
              textFormat: Text.PlainText
              color: tabClose.containsMouse ? view.fg : view.faint
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              MouseArea {
                id: tabClose
                anchors.fill: parent; anchors.margins: -Style.space(5)
                hoverEnabled: true
                onClicked: view.requestCloseTab(tab.modelData.key)
              }
            }
          }
        }
      }
    }

    // Thread
    ListView {
      id: list
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      spacing: Style.space(3)
      model: threadModel
      boundsBehavior: Flickable.StopAtBounds
      cacheBuffer: Style.space(1600)     // keep delegates alive beyond the viewport, so measuring never moves the pin
      // A conversation fades in once its history is laid out, instead of
      // flashing the rows into place as they are measured.
      opacity: view.threadReady ? 1 : 0
      Behavior on opacity { NumberAnimation { duration: view.ms(140); easing.type: Easing.OutCubic } }
      // Follow the conversation only while the user is at the bottom. The
      // pin is derived from the actual scroll position on every change, so
      // any way of scrolling up (wheel, trackpad, drag, scrollbar) releases
      // it, and nothing re-pins except a new message arriving while pinned.
      // Only movement the user caused (wheel, trackpad, drag, glide) may
      // release the pin; movement caused by rows being measured may not, and
      // while pinned the view follows every height change.
      onContentYChanged: if (view.userScrolling && !view.pinning) view.stickToBottom = (contentY >= originY + contentHeight - height - 4)
      onContentHeightChanged: if (view.stickToBottom && !view.userScrolling) view.pinToBottom()
      onCountChanged: if (view.stickToBottom) view.pinToBottom()
      // The picker and the shortcut list take room from the thread: keep the newest message in view.
      onHeightChanged: if (view.stickToBottom && !view.userScrolling) view.pinToBottom()
      onDragStarted: view.markUserScrolling()
      onMovementStarted: view.markUserScrolling()

      add: Transition {
        enabled: view.animateAdds
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: view.ms(180); easing.type: Easing.OutCubic }
      }

      // A transparent layer above the delegates owns wheel events, so the
      // Flickable's own wheel animation never fights the position we set.
      MouseArea {
        anchors.fill: parent
        z: 10
        acceptedButtons: Qt.NoButton      // wheel only; clicks fall through to the messages
        onWheel: function(ev) { view.scrollBy(list, ev) }
      }

      delegate: Item {
        id: row
        required property real ts
        required property string who
        required property string sender
        required property bool outgoing
        required property string body
        required property string filesText
        required property string image
        required property real imageW
        required property real imageH
        required property string status
        required property bool edited
        required property bool deleted
        required property string quote
        required property string reactions
        required property string day
        required property bool newDay
        required property bool firstOfRun
        required property string time
        // What the view's functions take: a plain copy of this row.
        readonly property var message: ({ ts: row.ts, who: row.who, sender: row.sender, outgoing: row.outgoing,
                                          body: row.body, deleted: row.deleted })
        readonly property bool selected: view.selectedTs === row.ts

        width: list.width
        implicitHeight: column.implicitHeight

        Column {
          id: column
          width: parent.width
          spacing: 0

          // Day separator, drawn above the first message of each day.
          Item {
            visible: row.newDay
            width: parent.width
            height: visible ? dayText.implicitHeight + Style.space(18) : 0
            Rectangle { anchors.left: parent.left; anchors.right: dayText.left; anchors.rightMargin: Style.space(10); anchors.verticalCenter: parent.verticalCenter; height: 1; color: view.hairline }
            Rectangle { anchors.right: parent.right; anchors.left: dayText.right; anchors.leftMargin: Style.space(10); anchors.verticalCenter: parent.verticalCenter; height: 1; color: view.hairline }
            Text {
              id: dayText
              anchors.centerIn: parent
              text: Model.dayLabel(row.day)
              textFormat: Text.PlainText
              color: view.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }

          // A little air between one person's run of messages and the next.
          Item { width: 1; height: row.firstOfRun && !row.newDay ? Style.space(8) : 0 }

          Item {
            width: parent.width
            height: bubble.height

            Rectangle {
              id: bubble
              anchors.left: row.outgoing ? undefined : parent.left
              anchors.right: row.outgoing ? parent.right : undefined
              width: Math.min(parent.width * 0.8, Math.max(content.implicitWidth + Style.space(24), Style.space(96)))
              height: content.implicitHeight + Style.space(14)
              radius: Style.cornerRadius
              color: row.outgoing ? Util.alpha(Commons.Color.accent, row.selected ? 0.26 : 0.16)
                                  : Util.alpha(Commons.Color.popups.text, row.selected ? 0.13 : 0.07)
              border.width: row.selected ? 1 : 0
              border.color: Util.alpha(Commons.Color.accent, 0.8)
              Behavior on color { ColorAnimation { duration: view.ms(120) } }

              MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                onClicked: function(mouse) {
                  if (mouse.button === Qt.MiddleButton) { view.quoteRow(row.message); return }
                  if (mouse.button === Qt.RightButton) {
                    // Right-click on selected text copies it; anywhere else it opens the reaction row.
                    if (bodyText.selectedText.length > 0) { view.copyText(bodyText.selectedText); return }
                    view.confirmDeleteTs = 0
                    view.selectedTs = row.ts
                    view.emojiRowOpen = true
                    return
                  }
                  view.emojiRowOpen = false
                  view.confirmDeleteTs = 0
                  view.selectedTs = row.selected ? 0 : row.ts
                }
              }

              ColumnLayout {
                id: content
                anchors.fill: parent
                anchors.topMargin: Style.space(7)
                anchors.bottomMargin: Style.space(7)
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(12)
                spacing: Style.space(4)

                // In a group, the name once per run; in a 1:1 chat the header already says who.
                Text {
                  visible: view.isGroup && !row.outgoing && row.firstOfRun
                  text: row.who
                  textFormat: Text.PlainText
                  color: Commons.Color.accent
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                // The message this one answers.
                RowLayout {
                  visible: row.quote.length > 0
                  Layout.fillWidth: true
                  Layout.fillHeight: false
                  Layout.maximumHeight: implicitHeight
                  spacing: Style.space(8)
                  Rectangle { Layout.fillHeight: true; implicitWidth: Math.max(2, Style.space(2)); radius: 1; color: Util.alpha(Commons.Color.accent, 0.7) }
                  Text {
                    Layout.fillWidth: true
                    text: row.quote
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    maximumLineCount: 2
                    wrapMode: Text.Wrap
                    color: view.dim
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
                // Photos are decoded once at thumbnail size and cached, and their
                // box is sized from Signal's metadata before the decode finishes,
                // so scrolling never re-decodes or re-lays out the thread.
                Item {
                  visible: row.image.length > 0
                  readonly property real boxW: Math.min(Style.space(360), list.width * 0.66)
                  readonly property real ratio: (row.imageW > 0 && row.imageH > 0) ? row.imageH / row.imageW : 0.66
                  Layout.preferredWidth: boxW
                  Layout.preferredHeight: visible ? Math.min(Style.space(300), boxW * ratio) : 0
                  Loader {
                    anchors.fill: parent
                    active: row.image.length > 0
                    sourceComponent: Image {
                      source: "file://" + row.image
                      asynchronous: true
                      cache: true
                      smooth: true
                      fillMode: Image.PreserveAspectFit
                      horizontalAlignment: row.outgoing ? Image.AlignRight : Image.AlignLeft
                      sourceSize.width: 480
                      opacity: status === Image.Ready ? 1 : 0
                      Behavior on opacity { NumberAnimation { duration: view.ms(160) } }
                      MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: view.execArgv(["xdg-open", row.image]) }
                    }
                  }
                }
                // Selectable: drag to select, Ctrl+C (or Super+C, or a right-click) copies.
                TextEdit {
                  id: bodyText
                  visible: row.body.length > 0
                  Layout.fillWidth: true
                  text: row.body
                  textFormat: TextEdit.PlainText
                  wrapMode: TextEdit.Wrap
                  readOnly: true
                  selectByMouse: true
                  selectByKeyboard: true
                  persistentSelection: true      // survives clicking Copy or moving to the composer
                  color: row.deleted ? view.dim : view.fg
                  selectionColor: Style.selectionFillFor(Commons.Color.popups.text, Commons.Color.accent)
                  selectedTextColor: view.fg
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.italic: row.deleted
                  readonly property bool ownsSelection: view.selectionTs === row.ts
                  onOwnsSelectionChanged: if (!ownsSelection) deselect()
                  onSelectedTextChanged: {
                    if (selectedText.length > 0) view.selectionTs = row.ts
                    else if (ownsSelection) view.selectionTs = 0
                  }
                  // A plain click still opens the message actions; a drag does not.
                  TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onTapped: {
                      view.emojiRowOpen = false
                      view.confirmDeleteTs = 0
                      view.selectedTs = row.selected ? 0 : row.ts
                    }
                  }
                  Keys.onPressed: function(event) {
                    var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
                    if (event.matches(StandardKey.Copy) || (ctrl && event.key === Qt.Key_Insert)) {
                      view.copyText(selectedText.length > 0 ? selectedText : row.body)
                      event.accepted = true
                    } else if (event.key === Qt.Key_Escape) {
                      view.handleEscape()
                      event.accepted = true
                    } else if (event.matches(StandardKey.SelectAll) || event.key === Qt.Key_Shift || event.key === Qt.Key_Control
                               || event.key === Qt.Key_Alt || event.key === Qt.Key_Meta || event.key === Qt.Key_Super_L) {
                      // selection keys and lone modifiers stay here
                    } else {
                      // Anything else is meant for the composer: go back to it and deliver the key.
                      composer.forceActiveFocus()
                      if (composerPaste.isPasteKey(event)) {
                        composerPaste.paste(false)
                      } else if (!ctrl && !(event.modifiers & Qt.AltModifier) && event.text.length > 0 && event.text.charCodeAt(0) >= 32) {
                        composer.insert(composer.cursorPosition, event.text)
                      }
                      event.accepted = true
                    }
                  }
                }
                Text {
                  visible: row.filesText.length > 0 && row.image.length === 0
                  text: "󰁦 " + row.filesText
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  Layout.fillWidth: true
                  color: view.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
                RowLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(8)
                  Rectangle {
                    visible: row.reactions.length > 0
                    implicitWidth: reactionText.implicitWidth + Style.space(12)
                    implicitHeight: reactionText.implicitHeight + Style.space(4)
                    radius: Math.min(Style.cornerRadius, height / 2)
                    color: Util.alpha(Commons.Color.popups.text, 0.08)
                    Text {
                      id: reactionText
                      anchors.centerIn: parent
                      text: row.reactions
                      textFormat: Text.PlainText
                      color: view.fg
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }
                  Item { Layout.fillWidth: true }
                  Text {
                    text: row.time + (row.edited ? " · edited" : "")
                          + (row.outgoing && !row.deleted ? (row.status === "read" || row.status === "delivered" ? "  ✓✓" : row.status === "failed" ? "  ✗" : "  ✓") : "")
                    textFormat: Text.PlainText
                    color: !row.outgoing ? view.faint : row.status === "failed" ? Commons.Color.urgent : row.status === "read" ? Commons.Color.accent : view.faint
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
          }

          // Action row under the selected message: reply, react, copy, delete.
          // Built only for the selected message, so a thread of sixty rows is
          // not also sixty rows of buttons.
          Loader {
            active: row.selected
            visible: active
            anchors.left: row.outgoing ? undefined : parent.left
            anchors.right: row.outgoing ? parent.right : undefined
            sourceComponent: Item {
              id: actionsRoot
              implicitWidth: actions.implicitWidth
              implicitHeight: actions.implicitHeight + Style.space(8)
              opacity: 0
              Component.onCompleted: opacity = 1
              Behavior on opacity { NumberAnimation { duration: view.ms(120) } }
              readonly property bool confirming: view.confirmDeleteTs === row.ts
              RowLayout {
                id: actions
                y: Style.space(4)
                spacing: Style.space(4)
                Button { bordered: true; visible: !actionsRoot.confirming; iconText: "󰑚"; text: "Reply"; onClicked: view.quoteRow(row.message) }
                Button { bordered: true; visible: !actionsRoot.confirming && !row.deleted; text: "React"; selected: view.emojiRowOpen; onClicked: view.emojiRowOpen = !view.emojiRowOpen }
                Repeater {
                  model: view.emojiRowOpen && !actionsRoot.confirming ? view.quickEmojis : []
                  delegate: Button { required property string modelData; text: modelData; onClicked: view.react(row.message, modelData) }
                }
                Button {
                  bordered: true
                  visible: !actionsRoot.confirming && !row.deleted
                  iconText: "󰆏"; text: "Copy"
                  onClicked: view.copyText(bodyText.selectedText.length > 0 ? bodyText.selectedText : row.body)
                }
                Button {
                  bordered: true
                  visible: !actionsRoot.confirming
                  iconText: "󰆴"; text: "Delete"
                  onClicked: { view.confirmDeleteTs = row.ts; view.emojiRowOpen = false }
                }
                // Asked before anything is deleted, and says which of the two it is.
                Text {
                  visible: actionsRoot.confirming
                  text: "Delete"
                  textFormat: Text.PlainText
                  color: view.dim
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                Button { bordered: true; visible: actionsRoot.confirming; text: "for me"; onClicked: view.deleteMessage(row.message, false) }
                Button {
                  // Your own messages only; past Signal's one-day window it says so
                  // rather than disappearing, so "for me" is not the only thing left.
                  readonly property string refusal: actionsRoot.confirming ? view.everyoneRefusal(row.message) : ""
                  bordered: true
                  text: refusal === "" ? "for everyone" : "for everyone: only within 24 h"
                  enabled: refusal === ""
                  opacity: enabled ? 1 : 0.5
                  visible: actionsRoot.confirming && row.outgoing && !row.deleted
                  onClicked: view.deleteMessage(row.message, true)
                }
                Button { bordered: true; visible: actionsRoot.confirming; text: "cancel"; onClicked: view.confirmDeleteTs = 0 }
              }
            }
          }
        }
      }

      // Nothing here yet.
      Text {
        anchors.centerIn: parent
        visible: view.threadReady && view.rowCount === 0 && view.conversationKey.length > 0
        text: view.linked ? "No messages here yet" : "Link this computer to see your messages"
        textFormat: Text.PlainText
        color: view.dim
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      // Back to the newest message, offered only while scrolled away from it.
      Button {
        z: 11
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(8)
        bordered: true
        background: Util.alpha(Commons.Color.popups.background, 0.96)
        text: "↓ Latest"
        opacity: view.stickToBottom || view.rowCount === 0 ? 0 : 1
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: view.ms(140) } }
        onClicked: view.pinToBottom()
      }
    }

    // Thumbnail picker, taking room from the thread while open.
    Rectangle {
      id: picker
      readonly property real wanted: Math.min(Style.space(420), Math.max(Style.space(220), (view.thumbnails ? grid.contentHeight : plainList.contentHeight) + pickerHead.implicitHeight + Style.space(40)))
      property real reveal: view.pickerOpen ? 1 : 0
      Behavior on reveal { NumberAnimation { duration: view.ms(160); easing.type: Easing.OutCubic } }
      visible: reveal > 0.01
      opacity: reveal
      Layout.fillWidth: true
      Layout.preferredHeight: wanted * reveal
      clip: true
      color: Style.normalFillFor(Commons.Color.popups.text, Commons.Color.accent)
      border.color: Style.normalBorderFor(Commons.Color.popups.text, Commons.Color.accent)
      border.width: 1
      radius: Style.cornerRadius

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.space(10)
        spacing: Style.space(8)

        RowLayout {
          id: pickerHead
          Layout.fillWidth: true
          spacing: Style.space(4)
          Button { bordered: true; iconText: "󰁝"; tooltipText: "Up one folder"; onClicked: if (view.pickerParent) view.listDir(view.pickerParent) }
          Repeater {
            model: view.quickDirs
            delegate: Button {
              required property string modelData
              text: modelData
              selected: view.pickerDir === view.home + "/" + modelData
              onClicked: view.listDir(view.home + "/" + modelData)
            }
          }
          TextField {
            id: pickerFilterField
            Layout.fillWidth: true
            foreground: view.fg
            verticalPadding: Style.space(4)
            placeholderText: "Filter " + view.pickerDir.replace(view.home, "~")
            onTextChanged: { view.pickerFilter = text; view.pickerIndex = 0 }
            QQC.ContextMenu.menu: null
            PasteGuard { id: filterPaste; target: pickerFilterField; cliPath: view.cliPath; singleLine: true }
            Keys.onEscapePressed: function(e) { view.handleEscape(); e.accepted = true }
            Keys.onReturnPressed: view.pickerChoose()
            Keys.onPressed: function(e) {
              if (filterPaste.handleKey(e)) return
              var perRow = view.thumbnails ? Math.max(1, Math.floor(grid.width / grid.cellWidth)) : 1
              if (e.key === Qt.Key_Down) { view.pickerMove(perRow); e.accepted = true }
              else if (e.key === Qt.Key_Up) { view.pickerMove(-perRow); e.accepted = true }
              else if (e.key === Qt.Key_Right && view.thumbnails && cursorPosition === text.length) { view.pickerMove(1); e.accepted = true }
              else if (e.key === Qt.Key_Left && view.thumbnails && cursorPosition === 0) { view.pickerMove(-1); e.accepted = true }
              else if (e.key === Qt.Key_PageDown) { view.pickerMove(perRow * 3); e.accepted = true }
              else if (e.key === Qt.Key_PageUp) { view.pickerMove(-perRow * 3); e.accepted = true }
              else if (e.key === Qt.Key_Backspace && text.length === 0 && view.pickerParent) { view.listDir(view.pickerParent); e.accepted = true }
            }
          }
          Button { iconText: "󰅖"; tooltipText: "Close"; onClicked: { view.pickerOpen = false; composer.forceActiveFocus() } }
        }

        // Plain list (setting "Thumbnails when attaching" off)
        ListView {
          id: plainList
          visible: !view.thumbnails
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          spacing: Style.space(2)
          model: view.thumbnails ? [] : view.pickerVisible
          boundsBehavior: Flickable.StopAtBounds
          MouseArea { anchors.fill: parent; z: 10; acceptedButtons: Qt.NoButton; onWheel: function(ev) { view.scrollBy(plainList, ev) } }
          delegate: Rectangle {
            required property var modelData
            required property int index
            width: plainList.width
            implicitHeight: rowText.implicitHeight + Style.space(10)
            radius: Style.cornerRadius
            color: index === view.pickerIndex ? Style.selectedFillFor(Commons.Color.popups.text, Commons.Color.accent)
                   : rowHover.containsMouse ? Style.hoverFillFor(Commons.Color.popups.text, Commons.Color.accent) : "transparent"
            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(6)
              spacing: Style.space(8)
              Text {
                text: view.fileGlyph(modelData.kind, modelData.isDir)
                textFormat: Text.PlainText
                color: modelData.isDir ? Commons.Color.accent : view.dim
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                id: rowText
                Layout.fillWidth: true
                text: modelData.name + (modelData.isDir ? "/" : "")
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: view.fg
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                text: modelData.isDir ? "" : (modelData.size >= 1048576 ? (modelData.size / 1048576).toFixed(1) + " MB" : modelData.size >= 1024 ? Math.round(modelData.size / 1024) + " KB" : modelData.size + " B")
                textFormat: Text.PlainText
                color: view.dim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Text {
                text: modelData.mtime ? new Date(modelData.mtime * 1000).toISOString().substring(0, 10) : ""
                textFormat: Text.PlainText
                color: view.dim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
            MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true; onClicked: view.chooseRow(modelData) }
          }
        }

        GridView {
          id: grid
          visible: view.thumbnails
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          cellWidth: Style.space(132)
          cellHeight: Style.space(132)
          model: view.thumbnails && view.pickerOpen ? view.pickerVisible : []
          boundsBehavior: Flickable.StopAtBounds
          MouseArea { anchors.fill: parent; z: 10; acceptedButtons: Qt.NoButton; onWheel: function(ev) { view.scrollBy(grid, ev) } }
          delegate: Item {
            required property var modelData
            required property int index
            readonly property bool keyed: index === view.pickerIndex
            width: grid.cellWidth
            height: grid.cellHeight
            Rectangle {
              id: tile
              anchors.fill: parent
              anchors.margins: Style.space(4)
              radius: Style.cornerRadius
              color: keyed ? Style.selectedFillFor(Commons.Color.popups.text, Commons.Color.accent)
                     : tileHover.containsMouse ? Style.hoverFillFor(Commons.Color.popups.text, Commons.Color.accent) : "transparent"
              border.color: Util.alpha(Commons.Color.accent, 0.8)
              border.width: keyed ? 1 : 0
              ColumnLayout {
                anchors.fill: parent
                anchors.margins: Style.space(6)
                spacing: Style.space(4)
                Item {
                  Layout.fillWidth: true
                  Layout.fillHeight: true
                  Image {
                    anchors.fill: parent
                    visible: modelData.kind === "image"
                    source: modelData.kind === "image" ? "file://" + modelData.path : ""
                    asynchronous: true
                    cache: true
                    fillMode: Image.PreserveAspectCrop
                    sourceSize.width: 160
                    sourceSize.height: 160
                  }
                  Text {
                    anchors.centerIn: parent
                    visible: modelData.kind !== "image"
                    text: view.fileGlyph(modelData.kind, modelData.isDir)
                    textFormat: Text.PlainText
                    color: modelData.isDir ? Commons.Color.accent : view.dim
                    font.family: Style.font.family
                    font.pixelSize: Style.font.displayLarge
                  }
                }
                Text {
                  Layout.fillWidth: true
                  text: modelData.name + (modelData.isDir ? "/" : "")
                  textFormat: Text.PlainText
                  elide: Text.ElideMiddle
                  horizontalAlignment: Text.AlignHCenter
                  color: view.fg
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
              MouseArea { id: tileHover; anchors.fill: parent; hoverEnabled: true; onClicked: view.chooseRow(modelData) }
            }
          }
        }
      }
    }

    // Every shortcut, in three short columns. "?" or Esc closes it.
    Rectangle {
      property real reveal: view.helpOpen ? 1 : 0
      Behavior on reveal { NumberAnimation { duration: view.ms(160); easing.type: Easing.OutCubic } }
      visible: reveal > 0.01
      opacity: reveal
      Layout.fillWidth: true
      Layout.preferredHeight: (helpGrid.implicitHeight + Style.space(28)) * reveal
      clip: true
      color: Style.normalFillFor(Commons.Color.popups.text, Commons.Color.accent)
      border.color: Style.normalBorderFor(Commons.Color.popups.text, Commons.Color.accent)
      border.width: 1
      radius: Style.cornerRadius
      GridLayout {
        id: helpGrid
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: Style.space(14)
        columns: width > Style.space(560) ? 3 : 1
        columnSpacing: Style.space(24)
        rowSpacing: Style.space(14)
        Repeater {
          model: view.helpSections
          delegate: ColumnLayout {
            required property var modelData
            Layout.alignment: Qt.AlignTop
            Layout.fillWidth: true
            spacing: Style.space(6)
            Text {
              text: modelData.title
              textFormat: Text.PlainText
              color: view.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            Repeater {
              model: modelData.rows
              delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: Style.space(8)
                KeyCap { label: modelData.key; foreground: view.fg }
                Text {
                  Layout.fillWidth: true
                  text: modelData.does
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  color: Util.alpha(Commons.Color.popups.text, 0.75)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }
      }
    }

    // Quote preview
    RowLayout {
      visible: view.quote !== null
      Layout.fillWidth: true
      // The bar beside the quote stretches to the row's height; without this
      // the row itself asks for all the height the thread has.
      Layout.fillHeight: false
      Layout.maximumHeight: implicitHeight
      spacing: Style.space(8)
      Rectangle { Layout.fillHeight: true; implicitWidth: Math.max(2, Style.space(2)); radius: 1; color: Commons.Color.accent }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          text: view.quote ? "Replying to " + view.quote.who : ""
          textFormat: Text.PlainText
          color: Commons.Color.accent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
        }
        Text {
          Layout.fillWidth: true
          text: view.quote ? Model.singleLine(view.quote.text, 160) : ""
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: view.dim
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
      Button { iconText: "󰅖"; tooltipText: "Do not quote"; onClicked: view.quote = null }
    }

    // Attachment chips
    Flow {
      visible: view.attachments.length > 0
      Layout.fillWidth: true
      spacing: Style.space(6)
      Repeater {
        model: view.attachments
        delegate: Rectangle {
          required property string modelData
          required property int index
          width: chip.implicitWidth + Style.space(18)
          height: chip.implicitHeight + Style.space(8)
          radius: Style.cornerRadius
          color: chipHover.containsMouse ? Style.hoverFillFor(Commons.Color.popups.text, Commons.Color.accent) : Style.normalFillFor(Commons.Color.popups.text, Commons.Color.accent)
          border.width: 1
          border.color: Style.normalBorderFor(Commons.Color.popups.text, Commons.Color.accent)
          Text {
            id: chip
            anchors.centerIn: parent
            text: "󰁦 " + Model.singleLine(modelData.split("/").pop(), 40) + "  ✕"
            textFormat: Text.PlainText
            color: view.fg
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          MouseArea { id: chipHover; anchors.fill: parent; hoverEnabled: true; onClicked: { var a = view.attachments.slice(); a.splice(index, 1); view.attachments = a } }
        }
      }
    }

    // Composer: a multi-line box that grows with the message (2 to 10 lines,
    // then scrolls), so a long reply can be read over before it is sent.
    // Enter sends; Shift+Enter or Alt+Enter starts a new line.
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(6)
      Button { Layout.alignment: Qt.AlignBottom; bordered: true; iconText: "󰁦"; tooltipText: "Attach a file (Ctrl+O)"; onClicked: view.pickerOpen ? (view.pickerOpen = false) : view.pickAttachment() }
      QQC.ScrollView {
        id: composerScroll
        Layout.fillWidth: true
        readonly property real chrome: composer.topPadding + composer.bottomPadding
        Layout.preferredHeight: Math.min(composerMetrics.lineSpacing * 10, Math.max(composerMetrics.lineSpacing * 2, composer.contentHeight)) + chrome
        QQC.ScrollBar.horizontal.policy: QQC.ScrollBar.AlwaysOff
        readonly property var borderSpec: Border.controlSpec(composer.activeFocus ? "focus" : (composer.hovered ? "hover-cursor" : "normal"), Commons.Color.foreground, Commons.Color.accent)
        background: BorderSurface {
          color: Style.controlFill(composer.activeFocus, composer.hovered, Commons.Color.foreground, Commons.Color.accent)
          borderSpec: composerScroll.borderSpec
          radius: Style.cornerRadius
        }
        FontMetrics { id: composerMetrics; font: composer.font }

        QQC.TextArea {
          id: composer
          objectName: "composer"
          property bool converting: false
          wrapMode: TextEdit.Wrap
          textFormat: TextEdit.PlainText
          placeholderText: view.linked ? "Message" : "No account linked: omarchy-signal link"
          enabled: !view.sending && view.linked
          background: null
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          color: Commons.Color.foreground
          selectionColor: Style.selectionFillFor(Commons.Color.foreground, Commons.Color.accent)
          selectedTextColor: Commons.Color.foreground
          placeholderTextColor: Qt.darker(Commons.Color.foreground, 1.6)
          leftPadding: Style.spacing.controlPaddingX + Border.left(composerScroll.borderSpec)
          rightPadding: Style.spacing.controlPaddingX + Border.right(composerScroll.borderSpec)
          topPadding: Style.spacing.inputPaddingY + Border.top(composerScroll.borderSpec)
          bottomPadding: Style.spacing.inputPaddingY + Border.bottom(composerScroll.borderSpec)
          onTextChanged: {
            // The space after :smile: or :D turns it into the emoji, as you type.
            if (converting || !activeFocus || !view.emojiAutoconvert) return
            var r = Emoji.convertBeforeCursor(text, cursorPosition)
            if (!r) return
            converting = true
            text = r.text
            cursorPosition = r.cursor
            converting = false
          }
          QQC.ContextMenu.menu: null
          PasteGuard {
            id: composerPaste
            target: composer
            cliPath: view.cliPath
            files: true
            onAttached: function(paths) { view.addAttachments(paths) }
          }
          Keys.onEscapePressed: function(event) { view.handleEscape(); event.accepted = true }
          Keys.onPressed: function(event) {
            var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
              if (event.modifiers & (Qt.ShiftModifier | Qt.AltModifier)) insert(cursorPosition, "\n")
              else view.send()
              event.accepted = true
            } else if ((event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) && ctrl) {
              view.cycleWindow((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1)
              event.accepted = true
            } else if (composerPaste.handleKey(event)) {
              // Ctrl+V and friends: files and pictures are attached, text is typed in (PasteGuard.qml).
            } else if ((event.key === Qt.Key_O && ctrl)
                       || (event.key === Qt.Key_A && ctrl && (event.modifiers & Qt.ShiftModifier))) {
              view.pickAttachment()
              event.accepted = true
            }
          }
        }
      }
      Button {
        Layout.alignment: Qt.AlignBottom
        bordered: true
        iconText: "󰒊"
        text: "Send"
        readonly property bool ready: !view.sending && view.linked && (composer.text.trim().length > 0 || view.attachments.length > 0)
        enabled: ready
        opacity: ready ? 1 : 0.5
        foreground: ready ? Commons.Color.accent : Commons.Color.foreground
        Behavior on opacity { NumberAnimation { duration: view.ms(120) } }
        onClicked: view.send()
      }
    }

    // Footer. One quiet line: a status when there is something to say (an error,
    // "Encrypting…", "copied"), otherwise the handful of keys worth remembering,
    // drawn as keycaps. Everything else lives behind "?".
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)
      readonly property bool hasStatus: view.error.length > 0 || view.sending || view.notice.length > 0
      Text {
        objectName: "statusText"
        visible: parent.hasStatus
        Layout.fillWidth: true
        Layout.preferredWidth: 0
        Layout.alignment: Qt.AlignVCenter
        text: view.error ? view.error : (view.sending ? "Encrypting…" : view.notice)
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        color: view.error ? Commons.Color.urgent : Commons.Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      // The legend wraps instead of eliding, so its end can be read without
      // widening the window. preferredWidth 0 is what makes that work inside a
      // RowLayout: the row then never reserves the legend's full single-line
      // width, the button takes what it needs and the keycaps wrap into the rest.
      Flow {
        objectName: "legendFlow"
        visible: !parent.hasStatus
        Layout.fillWidth: true
        Layout.preferredWidth: 0
        Layout.alignment: Qt.AlignVCenter
        spacing: Style.space(12)
        Repeater {
          model: view.legendKeys
          delegate: Row {
            required property var modelData
            spacing: Style.space(6)
            KeyCap { label: modelData.key; foreground: view.fg }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: modelData.does
              textFormat: Text.PlainText
              color: view.dim
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
      Button {
        Layout.alignment: Qt.AlignBottom
        text: "?"
        tooltipText: "Every shortcut"
        selected: view.helpOpen
        onClicked: { view.helpOpen = !view.helpOpen; view.pickerOpen = false }
      }
    }
  }
}
