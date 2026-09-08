import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Headless service: keeps one `omarchy-signal events` stream open, turns
// incoming messages into a popup you can answer without leaving what you are
// doing, and owns the centered reply window that popup opens.
//
// Everything shown comes from the bridge already sanitised; Model.cleanText
// runs on it again here, and every Text is PlainText. No string from the
// network is ever passed to a shell: replies go through Util.execArgv.
Item {
  id: root

  property var shell: null
  property var manifest: null
  property string omarchyPath: Quickshell.env("OMARCHY_PATH")

  // Bridge state mirrored from the event stream.
  property bool connected: false
  property bool linked: false
  property int unread: 0
  property bool previewEnabled: true
  property int toastTimeoutMs: 8000
  property string notificationMode: "popup"
  property string lastError: ""

  // Toast queue: newest last. Each entry is the object Model.toastFromMessage builds.
  property var toasts: []
  readonly property int maxToasts: 4

  // Reply window state.
  property bool replyOpen: false
  property string replyKey: ""
  property string replyName: ""
  property var replyThread: []          // [{who, body, outgoing, ts}]
  property bool sending: false
  property string sendError: ""

  readonly property string cliPath: Qt.resolvedUrl("bin/omarchy-signal").toString().replace("file://", "")

  signal unreadChangedExternally(int total)

  // ---------------------------------------------------------------- event stream

  Process {
    id: events
    command: [root.cliPath, "events"]
    running: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.handleLine(line) }
    }
    onExited: eventsRestart.restart()
  }
  Timer { id: eventsRestart; interval: 2000; onTriggered: events.running = true }

  function handleLine(line) {
    var ev = Model.parseEventLine(line)
    if (!ev) return
    var d = ev.data
    if (ev.event === "hello" || ev.event === "status") {
      if ("connected" in d) root.connected = d.connected === true
      if ("linked" in d) root.linked = d.linked === true
      if (typeof d.unread === "number") root.setUnread(d.unread)
      if ("notificationPreview" in d) root.previewEnabled = d.notificationPreview !== false
      if (typeof d.notificationTimeoutMs === "number") root.toastTimeoutMs = Math.max(1000, Math.min(120000, d.notificationTimeoutMs))
      if (typeof d.notifications === "string") root.notificationMode = d.notifications
      root.lastError = Model.singleLine(d.error || "", 200)
      return
    }
    if (ev.event === "unread") {
      if (typeof d.total === "number") root.setUnread(d.total)
      // Read elsewhere (the TUI, another device): drop that conversation's toasts.
      if (typeof d.conversation === "string") root.dismissKey(d.conversation)
      return
    }
    if (ev.event === "message") {
      var toast = Model.toastFromMessage(d, root.previewEnabled)
      if (!toast) return
      if (root.replyOpen && root.replyKey === toast.key) {
        root.replyThread = root.replyThread.concat([{ who: toast.title, body: Model.cleanText(d.text || toast.body, 4000), outgoing: false, ts: toast.ts }])
        return
      }
      if (root.notificationMode === "system") {
        Util.execArgv(["omarchy-notification-send", "--app-name", "Signal", "-g", "󰭹", toast.title, toast.body,
                       "--exec", "omarchy-launch-or-focus-tui", "--app-id=org.omarchy.signal", "omarchy-signal", "tui", toast.key])
        return
      }
      if (root.notificationMode !== "popup") return
      root.pushToast(toast)
      return
    }
    if (ev.event === "sent" && root.replyOpen && d.conversation === root.replyKey) {
      root.replyThread = root.replyThread.concat([{ who: "You", body: Model.cleanText(d.text || "[attachment]", 4000), outgoing: true, ts: d.ts || 0 }])
    }
  }

  function setUnread(n) {
    root.unread = Math.max(0, n | 0)
    root.unreadChangedExternally(root.unread)
  }

  // ---------------------------------------------------------------- toasts

  function pushToast(toast) {
    var list = root.toasts.filter(function(t) { return t.key !== toast.key })
    toast.id = Date.now() + "-" + Math.floor(Math.random() * 1e6)
    toast.expires = Date.now() + root.toastTimeoutMs
    list.push(toast)
    while (list.length > root.maxToasts) list.shift()
    root.toasts = list
    toastTick.start()
  }

  function dismissToast(id) {
    root.toasts = root.toasts.filter(function(t) { return t.id !== id })
  }

  function dismissKey(key) {
    root.toasts = root.toasts.filter(function(t) { return t.key !== key })
  }

  function dismissAll() { root.toasts = [] }

  Timer {
    id: toastTick
    interval: 250
    repeat: true
    running: root.toasts.length > 0
    onTriggered: {
      var now = Date.now()
      var keep = root.toasts.filter(function(t) { return t.expires > now || toastHover.hovered })
      if (keep.length !== root.toasts.length) root.toasts = keep
    }
  }
  QtObject { id: toastHover; property bool hovered: false }

  // ---------------------------------------------------------------- reply window

  function openReply(key, name) {
    if (!Model.isConversationKey(key)) return
    root.dismissKey(key)
    root.replyKey = key
    root.replyName = Model.singleLine(name || key, 80)
    root.replyThread = []
    root.sendError = ""
    root.replyOpen = true
    historyProc.running = false
    historyProc.command = [root.cliPath, "history", "--json", "-n", "12", "--", key]
    historyProc.running = true
    markReadProc.command = [root.cliPath, "mark-read", "--", key]
    markReadProc.running = true
    Qt.callLater(function() { if (root.replyOpen) replyField.forceActiveFocus() })
  }

  function closeReply() {
    root.replyOpen = false
    replyField.text = ""
    root.sendError = ""
  }

  function openTerminal(key) {
    Util.execArgv(Model.tuiArgv(key))
    root.closeReply()
    if (key) root.dismissKey(key)
  }

  function sendReply() {
    var argv = Model.sendArgv(root.replyKey, replyField.text)
    if (!argv || root.sending) return
    argv[0] = root.cliPath
    root.sending = true
    root.sendError = ""
    sendProc.command = argv
    sendProc.running = true
  }

  Process {
    id: historyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var rows = []
        try { rows = JSON.parse(text) } catch (e) { rows = [] }
        if (!Array.isArray(rows)) return
        root.replyThread = rows.slice(-12).map(function(m) {
          var body = Model.cleanText(m.body || "", 4000)
          if (!body && Array.isArray(m.attachments) && m.attachments.length) body = "[" + m.attachments.length + " attachment" + (m.attachments.length > 1 ? "s" : "") + "]"
          if (m.deleted) body = "(message deleted)"
          return { who: m.outgoing ? "You" : Model.singleLine(m.senderName || "?", 80), body: body, outgoing: m.outgoing === true, ts: m.ts || 0 }
        })
      }
    }
  }
  Process { id: markReadProc }
  Process {
    id: sendProc
    stdout: StdioCollector { id: sendOut; waitForEnd: true }
    stderr: StdioCollector { id: sendErr; waitForEnd: true }
    onExited: function(code) {
      root.sending = false
      if (code === 0) {
        replyField.text = ""
      } else {
        root.sendError = Model.singleLine(sendErr.text || ("send failed (" + code + ")"), 160)
      }
    }
  }

  IpcHandler {
    target: "iamteedoh.signal"
    function open(): string { Util.execArgv(Model.tuiArgv("")); return "ok" }
    function reply(key: string): string { root.openReply(key, ""); return "ok" }
    function dismiss(): string { root.dismissAll(); return "ok" }
    function close(): string { root.closeReply(); return "ok" }
    function demo(): string { Util.execArgv([root.cliPath, "demo"]); return "ok" }
    function toasts(): string { return String(root.toasts.length) }
    function unread(): string { return String(root.unread) }
    function state(): string { return JSON.stringify({ connected: root.connected, linked: root.linked, unread: root.unread }) }
  }

  // ---------------------------------------------------------------- toast surface

  PanelWindow {
    id: toastWindow
    visible: root.toasts.length > 0
    anchors { top: true; right: true }
    margins { top: Style.gapsOut + Style.bar.sizeHorizontal; right: Style.gapsOut }
    implicitWidth: Style.space(380)
    implicitHeight: toastColumn.implicitHeight + Style.space(8)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-toast"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    Column {
      id: toastColumn
      anchors.top: parent.top
      anchors.right: parent.right
      width: parent.width
      spacing: Style.space(8)

      Repeater {
        model: root.toasts
        delegate: BorderSurface {
          id: card
          required property var modelData
          width: toastColumn.width
          implicitHeight: cardLayout.implicitHeight + Style.space(24)
          color: Util.alpha(Color.notifications.background, 0.96)
          borderSpec: Border.surfaceSpec("notifications", "border", Color.notifications.border, Math.max(1, Style.space(2)))
          radius: Style.cornerRadius

          // Slide in from the right, like a message decrypting into place.
          transform: Translate { id: slide; x: Style.space(40) }
          opacity: 0
          Component.onCompleted: { enter.start() }
          ParallelAnimation {
            id: enter
            NumberAnimation { target: slide; property: "x"; to: 0; duration: 220; easing.type: Easing.OutCubic }
            NumberAnimation { target: card; property: "opacity"; to: 1; duration: 220 }
          }

          // Accent stripe on the left edge.
          Rectangle {
            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
            anchors.margins: Math.max(1, Style.space(2))
            width: Style.space(3)
            radius: width / 2
            color: Color.accent
          }

          MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onEntered: toastHover.hovered = true
            onExited: toastHover.hovered = false
            onClicked: function(mouse) {
              if (mouse.button === Qt.RightButton) root.dismissToast(card.modelData.id)
              else if (mouse.button === Qt.MiddleButton) root.openTerminal(card.modelData.key)
              else root.openReply(card.modelData.key, card.modelData.convName)
            }
          }

          ColumnLayout {
            id: cardLayout
            anchors.fill: parent
            anchors.margins: Style.space(12)
            anchors.leftMargin: Style.space(16)
            spacing: Style.space(4)

            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(8)
              Text {
                text: "󰭹"
                textFormat: Text.PlainText
                color: Color.accent
                font.family: Style.font.family
                font.pixelSize: Style.font.icon
              }
              Text {
                Layout.fillWidth: true
                text: card.modelData.title
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Color.notifications.text
                font.family: Style.font.family
                font.pixelSize: Style.font.subtitle
                font.bold: true
              }
              Text {
                text: "SIGNAL"
                textFormat: Text.PlainText
                color: Util.alpha(Color.notifications.text, 0.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                font.letterSpacing: 2
              }
            }
            Text {
              Layout.fillWidth: true
              text: card.modelData.body + (card.modelData.attachments > 0 ? "  󰁦" + card.modelData.attachments : "")
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              maximumLineCount: 4
              elide: Text.ElideRight
              color: Color.notifications.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            Text {
              Layout.fillWidth: true
              text: "click to reply  ·  middle-click for terminal  ·  right-click to dismiss"
              textFormat: Text.PlainText
              color: Util.alpha(Color.notifications.text, 0.45)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          // Countdown bar along the bottom edge.
          Rectangle {
            anchors.left: parent.left; anchors.bottom: parent.bottom
            anchors.margins: Math.max(1, Style.space(2))
            height: Math.max(1, Style.space(2))
            radius: height / 2
            color: Color.notifications.countdown
            width: {
              var total = root.toastTimeoutMs
              var left = Math.max(0, card.modelData.expires - Date.now())
              return toastHover.hovered ? card.width * 0.98 : card.width * 0.98 * (left / total)
            }
            Behavior on width { NumberAnimation { duration: 240 } }
            Connections { target: toastTick; function onTriggered() { } }
          }
        }
      }
    }
  }

  // ---------------------------------------------------------------- reply window

  PanelWindow {
    id: replyWindow
    visible: root.replyOpen
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-reply"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.replyOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: Color.menu.scrim
      MouseArea { anchors.fill: parent; onClicked: root.closeReply() }
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: root.closeReply()

      BorderSurface {
        id: dialog
        anchors.centerIn: parent
        width: Math.min(parent.width - Style.space(48), Style.space(640))
        height: Math.min(parent.height - Style.space(48), dialogLayout.implicitHeight + Style.space(40))
        color: Util.alpha(Color.popups.background, 0.98)
        borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
        radius: Style.cornerRadius
        scale: root.replyOpen ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }

        MouseArea { anchors.fill: parent; onClicked: {} }

        ColumnLayout {
          id: dialogLayout
          anchors.fill: parent
          anchors.margins: Style.space(20)
          spacing: Style.space(12)

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(10)
            Text {
              text: "◢"
              textFormat: Text.PlainText
              color: Color.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.title
            }
            Text {
              Layout.fillWidth: true
              text: root.replyName
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              text: root.connected ? "◉ SECURE CHANNEL" : "◌ OFFLINE"
              textFormat: Text.PlainText
              color: root.connected ? Color.accent : Color.urgent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.5
            }
          }

          Rectangle { Layout.fillWidth: true; height: 1; color: Util.alpha(Color.popups.border, 0.6) }

          // Recent thread.
          ListView {
            id: thread
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(Style.space(300), Math.max(Style.space(60), contentHeight))
            clip: true
            spacing: Style.space(6)
            model: root.replyThread
            onCountChanged: positionViewAtEnd()
            delegate: Item {
              required property var modelData
              width: thread.width
              implicitHeight: bubble.implicitHeight
              Rectangle {
                id: bubble
                anchors.left: modelData.outgoing ? undefined : parent.left
                anchors.right: modelData.outgoing ? parent.right : undefined
                width: Math.min(parent.width * 0.85, bubbleText.implicitWidth + Style.space(24))
                implicitHeight: bubbleText.implicitHeight + Style.space(16)
                radius: Style.cornerRadius
                color: modelData.outgoing ? Util.alpha(Color.accent, 0.18) : Util.alpha(Color.popups.text, 0.08)
                border.width: 1
                border.color: modelData.outgoing ? Util.alpha(Color.accent, 0.45) : Util.alpha(Color.popups.border, 0.5)
                Text {
                  id: bubbleText
                  anchors.fill: parent
                  anchors.margins: Style.space(8)
                  anchors.leftMargin: Style.space(12)
                  text: (modelData.outgoing ? "" : modelData.who + "\n") + modelData.body
                  textFormat: Text.PlainText
                  wrapMode: Text.Wrap
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
              }
            }
          }

          TextField {
            id: replyField
            Layout.fillWidth: true
            placeholderText: "Reply… (Enter sends, Esc closes)"
            onAccepted: root.sendReply()
            enabled: !root.sending && root.linked
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)
            Text {
              Layout.fillWidth: true
              text: root.sendError ? root.sendError : (root.sending ? "Encrypting…" : (root.linked ? "" : "No account linked. Run: omarchy-signal link"))
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: root.sendError ? Color.urgent : Util.alpha(Color.popups.text, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Button { text: "Open in terminal"; onClicked: root.openTerminal(root.replyKey) }
            Button { text: "Send"; enabled: !root.sending && root.linked; onClicked: root.sendReply() }
          }
        }
      }
    }
  }
}
