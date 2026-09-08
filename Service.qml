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
  property bool respectDnd: true
  property string notificationSound: ""
  property string account: ""
  property bool emojiAutoconvert: true
  property bool attachmentThumbnails: true
  property var windows: []               // conversation keys open as detached windows
  property var windowTabs: []            // [{key, name}] for the tab strip in every detached window
  property int windowSeq: 0              // cascade index for newly opened windows

  function refreshTabs() {
    var tabs = []
    for (var i = 0; i < windowRepeater.count; i++) {
      var w = windowRepeater.objectAt(i)
      if (w && w.view) tabs.push({ key: w.modelData, name: w.view.conversationName })
    }
    root.windowTabs = tabs
  }

  function raiseWindow(key) {
    for (var i = 0; i < windowRepeater.count; i++) {
      var w = windowRepeater.objectAt(i)
      if (w && w.modelData === key) { Util.execArgv([root.cliPath, "raise-window", "--", w.title]); return true }
    }
    return false
  }
  property bool dnd: false
  property string lastError: ""

  // Omarchy's Do Not Disturb, so our popups stay quiet when the user asked
  // the desktop to be quiet. Unread counts keep flowing regardless.
  FileView {
    id: dndFile
    path: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/omarchy/notifications.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.dnd = Model.parseDnd(text())
    onLoadFailed: root.dnd = false
  }

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

  // Device-linking QR code, shown for terminals that cannot draw images.
  property bool qrOpen: false
  property string qrPath: ""
  readonly property string runDir: (Quickshell.env("XDG_RUNTIME_DIR") || "") + "/omarchy-signal/"

  function showQr(path) {
    var p = String(path || "")
    // Only a PNG the CLI wrote into our own runtime directory.
    if (!root.runDir.startsWith("/") || p.indexOf(root.runDir) !== 0 || p.indexOf("..") >= 0 || !/\.png$/.test(p)) return false
    root.qrPath = ""
    root.qrPath = p
    root.qrOpen = true
    Qt.callLater(function() { if (root.qrOpen) qrKeys.forceActiveFocus() })
    return true
  }

  function hideQr() {
    root.qrOpen = false
    root.qrPath = ""
  }

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
      if ("respectDnd" in d) root.respectDnd = d.respectDnd !== false
      if (typeof d.notificationSound === "string") root.notificationSound = d.notificationSound
      if (typeof d.account === "string") root.account = d.account
      if ("emojiAutoconvert" in d) root.emojiAutoconvert = d.emojiAutoconvert !== false
      if ("attachmentThumbnails" in d) root.attachmentThumbnails = d.attachmentThumbnails !== false
      root.lastError = Model.singleLine(d.error || "", 200)
      return
    }
    if (ev.event === "unread") {
      if (typeof d.total === "number") root.setUnread(d.total)
      // Read elsewhere (the TUI, another device): drop that conversation's toasts.
      if (typeof d.conversation === "string") root.dismissKey(d.conversation)
      return
    }
    // Conversation views (popup and detached windows) follow their own thread.
    if (root.replyOpen) replyView.onEvent(ev.event, d)
    for (var i = 0; i < windowRepeater.count; i++) {
      var w = windowRepeater.objectAt(i)
      if (w && w.view) w.view.onEvent(ev.event, d)
    }
    if (ev.event === "message") {
      var toast = Model.toastFromMessage(d, root.previewEnabled)
      if (!toast) return
      if (root.replyOpen && root.replyKey === toast.key) return
      if (root.windows.indexOf(toast.key) >= 0) return   // a detached window is showing it
      if (root.respectDnd && root.dnd) return
      if (root.notificationMode === "system") {
        Util.execArgv(["omarchy-notification-send", "--app-name", "Signal", "-g", "󰭹", toast.title, toast.body,
                       "--exec", "omarchy-signal", "open", toast.key])
        return
      }
      if (root.notificationMode !== "popup") return
      root.pushToast(toast)
      if (root.notificationSound && /^\/[^\0]+\.(wav|ogg|oga|mp3|flac)$/i.test(root.notificationSound))
        Util.execArgv(["pw-play", root.notificationSound])
      return
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

  function openReply(key, name) {
    if (!Model.isConversationKey(key)) return
    root.dismissKey(key)
    root.replyKey = key
    root.replyName = Model.singleLine(name || key, 80)
    root.replyOpen = true
    replyView.load(key, root.replyName)
  }

  function closeReply() {
    root.replyOpen = false
  }

  function openWindow(key, name) {
    if (!Model.isConversationKey(key)) return false
    if (root.windows.indexOf(key) < 0) { root.windowSeq += 1; root.windows = root.windows.concat([key]) }
    else root.raiseWindow(key)
    root.dismissKey(key)
    return true
  }

  function closeWindow(key) {
    root.windows = root.windows.filter(function(k) { return k !== key })
  }

  function openTerminal(key) {
    var argv = Model.tuiArgv(key)
    argv[0] = root.cliPath
    Util.execArgv(argv)
    root.closeReply()
    if (key) root.dismissKey(key)
  }

  IpcHandler {
    target: "iamteedoh.signal"
    function open(): string { Util.execArgv(Model.tuiArgv("")); return "ok" }
    function reply(key: string): string { root.openReply(key, ""); return "ok" }
    function window(key: string): string {
      if (!key) { Util.execArgv([root.cliPath, "open"]); return "ok" }
      return root.openWindow(key, "") ? "ok" : "refused"
    }
    function closeWindow(key: string): string { root.closeWindow(key); return "ok" }
    function windows(): string { return JSON.stringify(root.windowTabs) }
    function raise(key: string): string { return root.raiseWindow(key) ? "ok" : "unknown" }
    function dismiss(): string { root.dismissAll(); return "ok" }
    function close(): string { root.closeReply(); return "ok" }
    function demo(): string { Util.execArgv([root.cliPath, "demo"]); return "ok" }
    function toasts(): string { return String(root.toasts.length) }
    function showQr(path: string): string { return root.showQr(path) ? "ok" : "refused" }
    function hideQr(): string { root.hideQr(); return "ok" }
    function unread(): string { return String(root.unread) }
    function state(): string { return JSON.stringify({ connected: root.connected, linked: root.linked, unread: root.unread, mode: root.notificationMode, dnd: root.dnd, respectDnd: root.respectDnd }) }
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

  // ---------------------------------------------------------------- reply window (click a toast)

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
        width: Math.min(parent.width - Style.space(48), Style.space(680))
        height: Math.min(parent.height - Style.space(48), Style.space(620))
        color: Util.alpha(Color.popups.background, 0.98)
        borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
        radius: Style.cornerRadius
        scale: root.replyOpen ? 1 : 0.96
        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        MouseArea { anchors.fill: parent; onClicked: {} }

        ConversationView {
          id: replyView
          anchors.fill: parent
          anchors.margins: Style.space(20)
          cliPath: root.cliPath
          connected: root.connected
          linked: root.linked
          account: root.account
          emojiAutoconvert: root.emojiAutoconvert
          thumbnails: root.attachmentThumbnails
          onRequestClose: root.closeReply()
          onRequestTerminal: root.openTerminal(root.replyKey)
          onRequestDetach: { var k = root.replyKey; root.closeReply(); root.openWindow(k, root.replyName) }
        }
      }
    }
  }

  // ---------------------------------------------------------------- detached conversation windows
  //
  // Real toplevel windows (class org.quickshell, title "Signal · <name>"), so
  // Hyprland can tile, float, move them to the scratchpad or another
  // workspace like any app. Same view as the popup.

  Instantiator {
    id: windowRepeater
    model: root.windows
    onObjectRemoved: root.refreshTabs()
    delegate: FloatingWindow {
      id: win
      required property string modelData
      property alias view: winView
      property int cascade: 0
      title: "Signal · " + winView.conversationName
      color: Util.alpha(Color.popups.background, 1.0)
      implicitWidth: Style.space(640)
      implicitHeight: Style.space(600)
      minimumSize: Qt.size(Style.space(380), Style.space(320))
      // Map only once the title is final: Hyprland applies float/size/center
      // rules at map time, and the rule matches on "^Signal · ".
      visible: false
      onVisibleChanged: if (!visible && winView.conversationKey) root.closeWindow(modelData)
      Component.onCompleted: {
        win.cascade = Math.max(0, root.windows.indexOf(modelData))   // fixed at creation: its slot in the stack
        winView.load(modelData, modelData.split(":").slice(1).join(":"))
        Qt.callLater(function() {
          win.visible = true
          // Float, size and centre it once mapped; from then on it is an
          // ordinary window Hyprland can tile, move or park in the scratchpad.
          floatProc.command = [root.cliPath, "float-window", "--offset", String(win.cascade % 6), "--", win.title]
          floatProc.running = true
          root.refreshTabs()
        })
        nameProc.command = [root.cliPath, "conversations", "--json", "--all"]
        nameProc.running = true
        contactsProc.command = [root.cliPath, "contacts", "--json"]
        contactsProc.running = true
      }
      Process { id: floatProc }
      // The title comes from the conversation list, else the contact/group
      // directory (which is where "Note to Self" and never-messaged contacts live).
      Process {
        id: nameProc
        stdout: StdioCollector {
          waitForEnd: true
          onStreamFinished: {
            var rows = []
            try { rows = JSON.parse(text) } catch (e) { rows = [] }
            for (var i = 0; i < rows.length; i++) if (rows[i].key === win.modelData && rows[i].name) { winView.conversationName = Model.singleLine(rows[i].name, 80); return }
          }
        }
      }
      Process {
        id: contactsProc
        stdout: StdioCollector {
          waitForEnd: true
          onStreamFinished: {
            var obj = null
            try { obj = JSON.parse(text) } catch (e) { obj = null }
            if (!obj) return
            var lists = [].concat(Array.isArray(obj.contacts) ? obj.contacts : [], Array.isArray(obj.groups) ? obj.groups : [])
            for (var i = 0; i < lists.length; i++) {
              if (lists[i].key === win.modelData) {
                var n = lists[i].displayName || lists[i].name
                if (n && winView.conversationName === win.modelData.split(":").slice(1).join(":")) winView.conversationName = Model.singleLine(n, 80)
                return
              }
            }
          }
        }
      }
      ConversationView {
        id: winView
        anchors.fill: parent
        anchors.margins: Style.space(16)
        cliPath: root.cliPath
        connected: root.connected
        linked: root.linked
        account: root.account
        emojiAutoconvert: root.emojiAutoconvert
        thumbnails: root.attachmentThumbnails
        detached: true
        siblings: root.windowTabs
        onConversationNameChanged: root.refreshTabs()
        onRequestClose: root.closeWindow(win.modelData)
        onRequestTerminal: root.openTerminal(win.modelData)
        onRequestRaise: function(key) { root.raiseWindow(key) }
      }
    }
  }

  // ---------------------------------------------------------------- linking QR popup

  PanelWindow {
    id: qrWindow
    visible: root.qrOpen
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-qr"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.qrOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: Color.menu.scrim
      MouseArea { anchors.fill: parent; onClicked: root.hideQr() }
    }

    Item {
      id: qrKeys
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: root.hideQr()

      BorderSurface {
        anchors.centerIn: parent
        width: qrLayout.implicitWidth + Style.space(48)
        height: qrLayout.implicitHeight + Style.space(48)
        color: Util.alpha(Color.popups.background, 0.98)
        borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
        radius: Style.cornerRadius
        MouseArea { anchors.fill: parent; onClicked: {} }

        ColumnLayout {
          id: qrLayout
          anchors.centerIn: parent
          spacing: Style.space(14)

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "LINK THIS DEVICE"
            textFormat: Text.PlainText
            color: Color.accent
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            font.bold: true
            font.letterSpacing: 2
          }
          Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: Style.space(300)
            height: width
            radius: Style.cornerRadius / 2
            color: "white"
            Image {
              anchors.fill: parent
              anchors.margins: Style.space(12)
              source: root.qrPath ? "file://" + root.qrPath : ""
              cache: false
              smooth: false
              fillMode: Image.PreserveAspectFit
            }
          }
          Text {
            Layout.alignment: Qt.AlignHCenter
            Layout.maximumWidth: Style.space(360)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Signal on your phone → Settings → Linked devices → Link new device.\nAfter the scan, nothing shows on the phone until the link finishes (10–60 s)."
            textFormat: Text.PlainText
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "Esc hides this; the terminal keeps waiting"
            textFormat: Text.PlainText
            color: Util.alpha(Color.popups.text, 0.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
