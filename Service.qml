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
  property var windows: []               // conversation keys open as tabs of the chat window
  property string activeTab: ""          // which tab the chat window shows
  property var tabNames: ({})            // key -> resolved name
  property var tabUnread: ({})           // key -> true when a message arrived while another tab was active
  property var windowTabs: []            // [{key, name, unread}] for the tab strip and the bar panel
  property bool chatFloated: false

  onWindowsChanged: { root.refreshTabs(); if (root.windows.length) namesProc.running = true }

  function refreshTabs() {
    var tabs = []
    for (var i = 0; i < root.windows.length; i++) {
      var k = root.windows[i]
      tabs.push({ key: k, name: root.tabNames[k] || k.split(":").slice(1).join(":"), unread: root.tabUnread[k] === true })
    }
    root.windowTabs = tabs
  }

  function showTab(key) {
    if (root.windows.indexOf(key) < 0) return false
    root.activeTab = key
    var u = root.tabUnread; delete u[key]; root.tabUnread = u
    chatView.load(key, root.tabNames[key] || "")
    root.refreshTabs()
    return true
  }

  function raiseWindow(key) {
    if (!root.showTab(key)) return false
    if (!chatWindow.visible) chatWindow.visible = true
    Util.execArgv([root.cliPath, "raise-window", "--", chatWindow.title])
    return true
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
    // Conversation views (popup and the chat window) follow their own thread.
    if (root.replyOpen) replyView.onEvent(ev.event, d)
    if (chatWindow.visible) chatView.onEvent(ev.event, d)
    if (ev.event === "message" && d && typeof d.conversation === "string" && d.outgoing !== true
        && root.windows.indexOf(d.conversation) >= 0 && d.conversation !== root.activeTab) {
      var u = root.tabUnread; u[d.conversation] = true; root.tabUnread = u
      root.refreshTabs()
    }
    if (ev.event === "message") {
      var toast = Model.toastFromMessage(d, root.previewEnabled)
      if (!toast) return
      if (root.replyOpen && root.replyKey === toast.key) return
      if (chatWindow.visible && toast.key === root.activeTab) return   // the chat window is showing it
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
    if (name) { var n = root.tabNames; n[key] = Model.singleLine(name, 80); root.tabNames = n }
    if (root.windows.indexOf(key) < 0) root.windows = root.windows.concat([key])
    root.showTab(key)
    if (!chatWindow.visible) {
      chatWindow.visible = true
      if (!root.chatFloated) {
        root.chatFloated = true
        Qt.callLater(function() {
          floatProc.command = [root.cliPath, "float-window", "--", chatWindow.title]
          floatProc.running = true
        })
      }
    } else {
      Util.execArgv([root.cliPath, "raise-window", "--", chatWindow.title])
    }
    root.dismissKey(key)
    return true
  }

  function closeWindow(key) {
    var idx = root.windows.indexOf(key)
    if (idx < 0) return
    var next = root.windows.filter(function(k) { return k !== key })
    root.windows = next
    if (next.length === 0) {
      root.activeTab = ""
      chatWindow.visible = false
      root.chatFloated = false
    } else if (root.activeTab === key) {
      root.showTab(next[Math.min(idx, next.length - 1)])
    }
    root.refreshTabs()
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
    function closeChatWindow(): string { root.windows = []; root.activeTab = ""; chatWindow.visible = false; root.chatFloated = false; root.refreshTabs(); return "ok" }
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
      Keys.onEscapePressed: replyView.handleEscape()

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

  // ---------------------------------------------------------------- the chat window (tabs)
  //
  // One real toplevel window (class org.quickshell, title "Signal · <chat>")
  // holding every detached conversation as a tab. Hyprland can float, tile or
  // park it in the scratchpad like any app, and there is only ever one of it.

  FloatingWindow {
    id: chatWindow
    title: "Signal · " + (chatView.conversationName || "chats")
    color: Util.alpha(Color.popups.background, 1.0)
    implicitWidth: Style.space(640)
    implicitHeight: Style.space(600)
    minimumSize: Qt.size(Style.space(380), Style.space(320))
    visible: false
    onVisibleChanged: {
      // Closed by the window manager: drop every tab.
      if (!visible && root.windows.length > 0) { root.windows = []; root.activeTab = ""; root.chatFloated = false; root.refreshTabs() }
    }
    Process { id: floatProc }

    ConversationView {
      id: chatView
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
      onConversationNameChanged: {
        // Keep resolved names only; a bare number is just the placeholder.
        var bare = root.activeTab.split(":").slice(1).join(":")
        if (root.activeTab && conversationName && conversationName !== bare && conversationName !== root.activeTab) {
          var n = root.tabNames; n[root.activeTab] = conversationName; root.tabNames = n; root.refreshTabs()
        }
      }
      onRequestClose: root.closeWindow(root.activeTab)
      onRequestTerminal: root.openTerminal(root.activeTab)
      onRequestRaise: function(key) { root.showTab(key) }
      onRequestCloseTab: function(key) { root.closeWindow(key) }
    }

    // Titles come from the conversation list, then the directory (which is
    // where "Note to Self" and never-messaged contacts live).
    function applyNames(rows) {
      var n = root.tabNames
      var changed = false
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i]
        var name = r && (r.name || r.displayName)
        if (!r || !r.key || !name) continue
        var bare = String(r.key).split(":").slice(1).join(":")
        if (!n[r.key] || n[r.key] === bare || n[r.key] === r.key) { n[r.key] = Model.singleLine(name, 80); changed = true }
      }
      if (changed) {
        root.tabNames = n
        if (root.activeTab && n[root.activeTab] && chatView.conversationName !== n[root.activeTab]) chatView.conversationName = n[root.activeTab]
        root.refreshTabs()
      }
    }
    Process {
      id: namesProc
      command: [root.cliPath, "conversations", "--json", "--all"]
      stdout: StdioCollector {
        waitForEnd: true
        onStreamFinished: {
          var rows = []
          try { rows = JSON.parse(text) } catch (e) { rows = [] }
          if (Array.isArray(rows)) chatWindow.applyNames(rows)
          contactsProc.running = true
        }
      }
    }
    Process {
      id: contactsProc
      command: [root.cliPath, "contacts", "--json"]
      stdout: StdioCollector {
        waitForEnd: true
        onStreamFinished: {
          var obj = null
          try { obj = JSON.parse(text) } catch (e) { obj = null }
          if (!obj) return
          chatWindow.applyNames([].concat(Array.isArray(obj.contacts) ? obj.contacts : [], Array.isArray(obj.groups) ? obj.groups : []))
        }
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
