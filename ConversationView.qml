import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "Emoji.js" as Emoji

// One conversation: thread, composer, attachments, quote, reactions. Used by
// the click-to-reply popup and, unchanged, by the detached windows, so both
// look and behave the same. Everything shown is PlainText; every action goes
// through the CLI as an argv array.
Item {
  id: view

  required property string cliPath
  property string conversationKey: ""
  property string conversationName: ""
  property bool connected: false
  property bool linked: false
  property bool detached: false          // true inside a FloatingWindow
  property string account: ""            // our own number, for reacting to our own messages
  property bool emojiAutoconvert: true   // :smile: / :D become emoji after the space (setting)
  property bool thumbnails: true         // picture tiles in the attachment picker (setting); off = plain list
  property int scrollSpeed: 9            // trackpad/wheel multiplier (setting)

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

  property var siblings: []              // other detached windows: [{key, name}]

  // Esc peels one layer at a time: picker → message actions → quote → the window.
  function handleEscape() {
    if (view.pickerOpen) { view.pickerOpen = false; composer.forceActiveFocus(); return true }
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

  property var thread: []                // Model.threadRows output (last applied)
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
    for (var k = 0; k < rows.length; k++) {
      var row = rows[k]
      if (k < threadModel.count) {
        var have = threadModel.get(k)
        if (have.ts === row.ts && have.outgoing === row.outgoing) {
          if (have.body !== row.body || have.status !== row.status || have.reactions !== row.reactions || have.edited !== row.edited
              || have.image !== row.image || have.filesText !== row.filesText || have.quote !== row.quote) threadModel.set(k, row)
          continue
        }
      }
      threadModel.insert(k, row)
    }
    while (threadModel.count > rows.length) threadModel.remove(threadModel.count - 1)
    view.thread = rows
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

  function load(key, name) {
    view.stickToBottom = true
    view.conversationKey = key
    view.conversationName = Model.singleLine(name || key.split(":").slice(1).join(":"), 80)
    threadModel.clear()
    view.thread = []
    view.quote = null
    view.attachments = []
    view.error = ""
    view.selectedTs = 0
    view.emojiRowOpen = false
    historyProc.running = false
    historyProc.command = [view.cliPath, "history", "--json", "-n", "60", "--", key]
    historyProc.running = true
    markReadProc.command = [view.cliPath, "mark-read", "--", key]
    markReadProc.running = true
    Qt.callLater(function() { composer.forceActiveFocus() })
  }

  property real savedY: 0
  property bool pinning: false
  property bool userScrolling: false

  function markUserScrolling() {
    view.userScrolling = true
    userScrollTimer.restart()
  }
  Timer { id: userScrollTimer; interval: 250; onTriggered: view.userScrolling = false }

  function pinToBottom() {
    view.pinning = true
    list.positionViewAtEnd()
    view.pinning = false
    view.stickToBottom = true
    settle.restart()          // rows may still be measuring: pin once more when they are done
  }
  Timer { id: settle; interval: 120; onTriggered: if (view.stickToBottom && !view.userScrolling) { view.pinning = true; list.positionViewAtEnd(); view.pinning = false } }

  function reload() {
    if (!view.conversationKey) return
    view.savedY = list.contentY
    historyProc.running = false
    historyProc.command = [view.cliPath, "history", "--json", "-n", "60", "--", view.conversationKey]
    historyProc.running = true
  }

  // Service.qml feeds bridge events here; anything about this conversation refreshes the thread.
  function onEvent(name, data) {
    if (!data || data.conversation !== view.conversationKey) return
    if (name === "typing") { view.typingName = data.typing ? Model.singleLine(data.senderName || "", 40) : ""; return }
    if (name === "message" || name === "sent" || name === "edited" || name === "deleted" || name === "reaction" || name === "receipt") {
      view.reload()
      if (name === "message") { markReadProc.command = [view.cliPath, "mark-read", "--", view.conversationKey]; markReadProc.running = true }
    }
  }

  function send() {
    var argv = Model.sendArgvFull(view.conversationKey, composer.text, view.attachments, view.quote)
    if (!argv || view.sending) return
    argv[0] = view.cliPath
    view.sending = true
    view.error = ""
    sendProc.command = argv
    sendProc.running = true
  }

  function react(row, emojiText) {
    var author = row.outgoing ? ("number:" + view.account) : row.sender
    var argv = Model.reactArgv(view.conversationKey, row.ts, author, emojiText)
    if (!argv) { view.error = "cannot react to that message"; return }
    argv[0] = view.cliPath
    actionProc.command = argv
    actionProc.running = true
    view.emojiRowOpen = false
    view.selectedTs = 0
  }

  function quoteRow(row) {
    view.quote = { ts: row.ts, author: row.sender, text: row.body, who: row.who }
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
    view.pickerOpen = true
    view.pickerFilter = ""
    view.pickerIndex = 0
    view.listDir(view.pickerDir || (view.home + "/Pictures"))
    Qt.callLater(function() { pickerFilterField.forceActiveFocus() })
  }

  function listDir(dir) {
    view.pickerIndex = 0
    lsProc.running = false
    lsProc.command = [view.cliPath, "ls-files", "--", dir]
    lsProc.running = true
  }

  function chooseRow(row) {
    if (row.isDir) { view.listDir(row.path); return }
    if (view.attachments.indexOf(row.path) < 0 && view.attachments.length < 8) view.attachments = view.attachments.concat([row.path])
    view.pickerOpen = false
    composer.forceActiveFocus()
  }

  readonly property var pickerVisible: {
    var q = Model.singleLine(view.pickerFilter, 80).toLowerCase()
    var rows = Array.isArray(view.pickerRows) ? view.pickerRows : []
    return q ? rows.filter(function(r) { return r.name.toLowerCase().indexOf(q) >= 0 }) : rows
  }

  Process {
    id: lsProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var obj = null
        try { obj = JSON.parse(text) } catch (e) { obj = null }
        if (!obj || !Array.isArray(obj.rows)) { view.pickerRows = []; return }
        view.pickerDir = String(obj.dir || "")
        view.pickerParent = String(obj.parent || "")
        view.pickerRows = obj.rows.filter(function(r) { return r && typeof r.path === "string" && r.path.charAt(0) === "/" })
      }
    }
  }

  Process {
    id: historyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var rows = []
        try { rows = JSON.parse(text) } catch (e) { rows = [] }
        if (Array.isArray(rows)) {
          view.applyThread(Model.threadRows(rows, 60))
          // A model swap resets the list to the top; put it back where it was,
          // or at the end when following the conversation.
          var y = view.savedY
          Qt.callLater(function() { if (view.stickToBottom) view.pinToBottom(); else list.contentY = Math.min(y, Math.max(0, list.contentHeight - list.height)) })
        }
      }
    }
  }
  Process { id: markReadProc }
  Process { id: actionProc; onExited: function(code) { if (code !== 0) view.error = "action failed"; view.reload() } }
  Process {
    id: sendProc
    stderr: StdioCollector { id: sendErr; waitForEnd: true }
    onExited: function(code) {
      view.sending = false
      if (code === 0) { composer.text = ""; view.quote = null; view.attachments = []; view.stickToBottom = true; view.reload() }
      else view.error = Model.singleLine(sendErr.text || ("send failed (" + code + ")"), 160)
    }
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(10)

    // Header
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(10)
      Text { text: "◢"; textFormat: Text.PlainText; color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.title }
      Text {
        Layout.fillWidth: true
        text: view.conversationName
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }
      Text {
        text: view.connected ? "◉ SECURE CHANNEL" : "◌ OFFLINE"
        textFormat: Text.PlainText
        color: view.connected ? Color.accent : Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1.5
      }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Util.alpha(Color.popups.border, 0.6) }

    // Tab strip: every conversation open in this window. Click switches the
    // content in place; ✕ closes a tab; Ctrl+Tab cycles.
    Flow {
      visible: view.detached && Array.isArray(view.siblings) && view.siblings.length > 0
      Layout.fillWidth: true
      spacing: Style.space(6)
      Repeater {
        model: view.detached ? view.siblings : []
        delegate: Rectangle {
          required property var modelData
          readonly property bool current: modelData.key === view.conversationKey
          width: tabRow.implicitWidth + Style.space(20)
          height: tabRow.implicitHeight + Style.space(10)
          radius: height / 2
          color: current ? Util.alpha(Color.accent, 0.25) : Util.alpha(Color.popups.text, 0.06)
          border.width: 1
          border.color: current ? Color.accent : (modelData.unread ? Util.alpha(Color.accent, 0.8) : Util.alpha(Color.popups.border, 0.5))
          Row {
            id: tabRow
            anchors.centerIn: parent
            spacing: Style.space(6)
            Text {
              text: (modelData.unread ? "● " : "") + Model.singleLine(modelData.name || modelData.key, 28)
              textFormat: Text.PlainText
              color: current ? Color.popups.text : (modelData.unread ? Color.accent : Util.alpha(Color.popups.text, 0.8))
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: modelData.unread === true
              MouseArea { anchors.fill: parent; onClicked: if (!current) view.requestRaise(modelData.key) }
            }
            Text {
              text: "✕"
              textFormat: Text.PlainText
              color: Util.alpha(Color.popups.text, 0.5)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              MouseArea { anchors.fill: parent; anchors.margins: -Style.space(4); onClicked: view.requestCloseTab(modelData.key) }
            }
          }
        }
      }
      Text {
        visible: Array.isArray(view.siblings) && view.siblings.length > 1
        text: "  Ctrl+Tab cycles"
        textFormat: Text.PlainText
        color: Util.alpha(Color.popups.text, 0.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        height: Style.font.caption + Style.space(10)
        verticalAlignment: Text.AlignVCenter
      }
    }

    // Thread
    ListView {
      id: list
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      spacing: Style.space(6)
      model: threadModel
      boundsBehavior: Flickable.StopAtBounds
      cacheBuffer: Style.space(3000)     // keep delegates alive well beyond the viewport
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
      onDragStarted: view.markUserScrolling()
      onMovementStarted: view.markUserScrolling()

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
        required property var modelData
        width: list.width
        implicitHeight: bubble.implicitHeight + (actions.visible ? actions.implicitHeight + Style.space(4) : 0)

        Rectangle {
          id: bubble
          anchors.left: modelData.outgoing ? undefined : parent.left
          anchors.right: modelData.outgoing ? parent.right : undefined
          width: Math.min(parent.width * 0.85, Math.max(content.implicitWidth + Style.space(24), Style.space(120)))
          implicitHeight: content.implicitHeight + Style.space(16)
          radius: Style.cornerRadius
          color: modelData.outgoing ? Util.alpha(Color.accent, 0.18) : Util.alpha(Color.popups.text, 0.08)
          border.width: view.selectedTs === modelData.ts ? 2 : 1
          border.color: view.selectedTs === modelData.ts ? Color.accent : (modelData.outgoing ? Util.alpha(Color.accent, 0.45) : Util.alpha(Color.popups.border, 0.5))

          MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onClicked: function(mouse) {
              if (mouse.button === Qt.MiddleButton) { view.quoteRow(row.modelData); return }
              if (mouse.button === Qt.RightButton) {
                // Right-click: straight to the reaction row.
                view.selectedTs = row.modelData.ts
                view.emojiRowOpen = true
                return
              }
              view.emojiRowOpen = false
              view.selectedTs = view.selectedTs === row.modelData.ts ? 0 : row.modelData.ts
            }
          }

          ColumnLayout {
            id: content
            anchors.fill: parent
            anchors.margins: Style.space(8)
            anchors.leftMargin: Style.space(12)
            spacing: Style.space(4)
            Text {
              visible: !row.modelData.outgoing
              text: row.modelData.who
              textFormat: Text.PlainText
              color: Color.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            Text {
              visible: row.modelData.quote.length > 0
              Layout.fillWidth: true
              text: "↩ " + row.modelData.quote
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: Util.alpha(Color.popups.text, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.italic: true
            }
            // Photos are decoded once at thumbnail size and cached, and their
            // box is sized from Signal's metadata before the decode finishes,
            // so scrolling never re-decodes or re-lays out the thread.
            Item {
              visible: row.modelData.image.length > 0
              readonly property real boxW: Math.min(Style.space(360), list.width * 0.7)
              readonly property real ratio: (row.modelData.imageW > 0 && row.modelData.imageH > 0) ? row.modelData.imageH / row.modelData.imageW : 0.66
              Layout.preferredWidth: boxW
              Layout.preferredHeight: visible ? Math.min(Style.space(300), boxW * ratio) : 0
              Image {
                anchors.fill: parent
                source: row.modelData.image ? "file://" + row.modelData.image : ""
                asynchronous: true
                cache: true
                smooth: true
                fillMode: Image.PreserveAspectFit
                sourceSize.width: 480
                MouseArea { anchors.fill: parent; onClicked: Util.execArgv(["xdg-open", row.modelData.image]) }
              }
            }
            Text {
              visible: row.modelData.body.length > 0
              Layout.fillWidth: true
              text: row.modelData.body
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            Text {
              visible: row.modelData.filesText.length > 0 && !row.modelData.image
              text: "󰁦 " + row.modelData.filesText
              textFormat: Text.PlainText
              elide: Text.ElideRight
              Layout.fillWidth: true
              color: Util.alpha(Color.popups.text, 0.7)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(8)
              Text {
                visible: row.modelData.reactions.length > 0
                text: row.modelData.reactions
                textFormat: Text.PlainText
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Item { Layout.fillWidth: true }
              Text {
                text: Model.relativeTime(row.modelData.ts) + (row.modelData.edited ? " · edited" : "")
                      + (row.modelData.outgoing ? (row.modelData.status === "read" ? " ✓✓" : row.modelData.status === "delivered" ? " ✓✓" : row.modelData.status === "failed" ? " ✗" : " ✓") : "")
                textFormat: Text.PlainText
                color: row.modelData.status === "read" ? Color.accent : Util.alpha(Color.popups.text, 0.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }
        }

        // Action row under the selected message: reply, react, copy.
        RowLayout {
          id: actions
          visible: view.selectedTs === row.modelData.ts
          anchors.top: bubble.bottom
          anchors.topMargin: Style.space(4)
          anchors.left: row.modelData.outgoing ? undefined : parent.left
          anchors.right: row.modelData.outgoing ? parent.right : undefined
          spacing: Style.space(6)
          Button { text: "↩ Reply"; onClicked: view.quoteRow(row.modelData) }
          Button { text: "☺ React"; onClicked: view.emojiRowOpen = !view.emojiRowOpen }
          Repeater {
            model: view.emojiRowOpen ? view.quickEmojis : []
            delegate: Button { required property string modelData; text: modelData; onClicked: view.react(row.modelData, modelData) }
          }
          Button { text: "Copy"; onClicked: Util.execArgv(["wl-copy", "--", row.modelData.body]) }
        }
      }
    }

    // Thumbnail picker, drawn over the thread while open.
    Rectangle {
      id: picker
      visible: view.pickerOpen
      Layout.fillWidth: true
      Layout.preferredHeight: visible ? Math.min(Style.space(420), Math.max(Style.space(220), (view.thumbnails ? grid.contentHeight : plainList.contentHeight) + pickerHead.implicitHeight + Style.space(40))) : 0
      color: Util.alpha(Color.popups.background, 1.0)
      border.color: Util.alpha(Color.accent, 0.5)
      border.width: 1
      radius: Style.cornerRadius

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.space(10)
        spacing: Style.space(8)

        RowLayout {
          id: pickerHead
          Layout.fillWidth: true
          spacing: Style.space(6)
          Button { text: "↑"; onClicked: if (view.pickerParent) view.listDir(view.pickerParent) }
          Repeater {
            model: view.quickDirs
            delegate: Button {
              required property string modelData
              text: modelData
              onClicked: view.listDir(view.home + "/" + modelData)
            }
          }
          TextField {
            id: pickerFilterField
            Layout.fillWidth: true
            placeholderText: "filter " + view.pickerDir.replace(view.home, "~") + "   (arrows move · Enter attaches · Backspace on empty goes up)"
            onTextChanged: { view.pickerFilter = text; view.pickerIndex = 0 }
            Keys.onEscapePressed: function(e) { view.handleEscape(); e.accepted = true }
            Keys.onReturnPressed: view.pickerChoose()
            Keys.onPressed: function(e) {
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
          Button { text: "✕"; onClicked: { view.pickerOpen = false; composer.forceActiveFocus() } }
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
            radius: Style.cornerRadius / 2
            color: (rowHover.containsMouse || index === view.pickerIndex) ? Util.alpha(Color.accent, 0.18) : "transparent"
            RowLayout {
              anchors.fill: parent
              anchors.margins: Style.space(6)
              spacing: Style.space(8)
              Text {
                text: modelData.isDir ? "" : (modelData.kind === "image" ? "󰋩" : modelData.kind === "video" ? "󰕧" : modelData.kind === "audio" ? "󰎈" : modelData.kind === "doc" ? "󰈙" : "󰈔")
                textFormat: Text.PlainText
                color: modelData.isDir ? Color.accent : Util.alpha(Color.popups.text, 0.8)
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                id: rowText
                Layout.fillWidth: true
                text: modelData.name + (modelData.isDir ? "/" : "")
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                text: modelData.isDir ? "" : (modelData.size >= 1048576 ? (modelData.size / 1048576).toFixed(1) + " MB" : modelData.size >= 1024 ? Math.round(modelData.size / 1024) + " KB" : modelData.size + " B")
                textFormat: Text.PlainText
                color: Util.alpha(Color.popups.text, 0.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Text {
                text: modelData.mtime ? new Date(modelData.mtime * 1000).toISOString().substring(0, 10) : ""
                textFormat: Text.PlainText
                color: Util.alpha(Color.popups.text, 0.5)
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
          model: view.thumbnails ? view.pickerVisible : []
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
              radius: Style.cornerRadius / 2
              color: (tileHover.containsMouse || keyed) ? Util.alpha(Color.accent, 0.18) : Util.alpha(Color.popups.text, 0.05)
              border.color: (tileHover.containsMouse || keyed) ? Color.accent : Util.alpha(Color.popups.border, 0.4)
              border.width: keyed ? 2 : 1
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
                    text: modelData.isDir ? "" : (modelData.kind === "video" ? "󰕧" : modelData.kind === "audio" ? "󰎈" : modelData.kind === "doc" ? "󰈙" : "󰈔")
                    textFormat: Text.PlainText
                    color: modelData.isDir ? Color.accent : Util.alpha(Color.popups.text, 0.8)
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
                  color: Color.popups.text
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

    Text {
      visible: view.typingName.length > 0
      text: view.typingName + " is typing…"
      textFormat: Text.PlainText
      color: Color.accent
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }

    // Quote preview
    RowLayout {
      visible: view.quote !== null
      Layout.fillWidth: true
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: view.quote ? "↩ " + view.quote.who + ": " + Model.singleLine(view.quote.text, 120) : ""
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Util.alpha(Color.popups.text, 0.7)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.italic: true
      }
      Button { text: "✕"; onClicked: view.quote = null }
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
          width: chip.implicitWidth + Style.space(16)
          height: chip.implicitHeight + Style.space(8)
          radius: height / 2
          color: Util.alpha(Color.accent, 0.15)
          border.color: Util.alpha(Color.accent, 0.5)
          Text {
            id: chip
            anchors.centerIn: parent
            text: "󰁦 " + modelData.split("/").pop() + "  ✕"
            textFormat: Text.PlainText
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          MouseArea { anchors.fill: parent; onClicked: { var a = view.attachments.slice(); a.splice(index, 1); view.attachments = a } }
        }
      }
    }

    // Composer
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)
      Button { text: "󰁦"; onClicked: view.pickAttachment() }
      TextField {
        id: composer
        Layout.fillWidth: true
        placeholderText: view.linked ? "Message… (Enter sends, :smile: works)" : "No account linked: omarchy-signal link"
        enabled: !view.sending && view.linked
        onAccepted: view.send()
        onTextEdited: {
          if (!view.emojiAutoconvert) return
          var r = Emoji.convertBeforeCursor(text, cursorPosition)
          if (r) { text = r.text; cursorPosition = r.cursor }
        }
        Keys.onEscapePressed: function(event) { view.handleEscape(); event.accepted = true }
        Keys.onPressed: function(event) {
          if ((event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) && (event.modifiers & Qt.ControlModifier)) {
            view.cycleWindow((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1)
            event.accepted = true
          } else if ((event.key === Qt.Key_O && (event.modifiers & Qt.ControlModifier))
                     || (event.key === Qt.Key_A && (event.modifiers & Qt.ControlModifier) && (event.modifiers & Qt.ShiftModifier))) {
            view.pickAttachment()
            event.accepted = true
          }
        }
      }
      Button { text: "Send"; enabled: !view.sending && view.linked; onClicked: view.send() }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: view.error ? view.error : (view.sending ? "Encrypting…" : "click a message: Reply · React · Copy  ·  right-click: react  ·  middle-click: reply  ·  Ctrl+O attach  ·  Esc closes")
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: view.error ? Color.urgent : Util.alpha(Color.popups.text, 0.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      Button { visible: !view.detached; text: "Detach ⧉"; onClicked: view.requestDetach() }
      Button { text: "Terminal"; onClicked: view.requestTerminal() }
    }
  }
}
