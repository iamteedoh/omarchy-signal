// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model
import "Hub.js" as Hub

// Headless service: holds the plugin's connection to the bridge, turns
// incoming messages into a popup you can answer without leaving what you are
// doing, and owns the reply dialog and the tabbed chat window.
//
// Everything shown comes from the bridge already sanitised; Model.cleanText
// runs on it again here, and every Text is PlainText. No string from the
// network is ever passed to a shell: requests go to the bridge's socket as
// JSON (Bridge.qml), and the few programs started here get an argv array.
Item {
  id: root

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

  property var shell: null
  property var manifest: null
  property string omarchyPath: Quickshell.env("OMARCHY_PATH")

  // Bridge state mirrored from its hello and status events.
  property bool connected: false
  property bool linked: false
  property bool accountsKnown: false     // signal-cli has said which accounts exist; before that "not linked" means "not known yet"
  readonly property bool unlinked: root.connected && root.accountsKnown && !root.linked
  property int unread: 0
  property bool previewEnabled: true
  property int toastTimeoutMs: 8000
  property string notificationMode: "popup"
  property bool respectDnd: true
  property string notificationSound: ""
  property string account: ""
  property bool emojiAutoconvert: true
  property bool attachmentThumbnails: true
  property int scrollSpeed: 9
  property int windowOpacity: 0          // percent; 0 = what the theme uses
  property bool updateCheck: true
  property string bridgeVersion: ""
  property var windows: []               // conversation keys open as tabs of the chat window
  property string activeTab: ""          // which tab the chat window shows
  property var tabNames: ({})            // key -> resolved name
  property var tabUnread: ({})           // key -> true when a message arrived while another tab was active
  property var windowTabs: []            // [{key, name, unread}] for the tab strip and the bar panel

  // How solid the popup conversation and the chat window are.
  function surfaceAlpha(themeDefault) {
    return root.windowOpacity > 0 ? Math.max(0.5, Math.min(1, root.windowOpacity / 100)) : themeDefault
  }

  onWindowsChanged: { root.refreshTabs(); if (root.windows.length) root.refreshNames() }

  function refreshTabs() {
    var tabs = []
    for (var i = 0; i < root.windows.length; i++) {
      var k = root.windows[i]
      tabs.push({ key: k, name: root.tabNames[k] || k.split(":").slice(1).join(":"), unread: root.tabUnread[k] === true })
    }
    root.windowTabs = tabs
  }

  readonly property var chatWindow: chatLoader.item      // null while no tab is open
  readonly property var chatView: chatLoader.item ? chatLoader.item.view : null

  function showTab(key) {
    if (root.windows.indexOf(key) < 0) return false
    root.activeTab = key
    var u = root.tabUnread; delete u[key]; root.tabUnread = u
    if (root.chatView) root.chatView.load(key, root.tabNames[key] || "")
    root.refreshTabs()
    return true
  }

  function raiseWindow(key) {
    if (!root.showTab(key)) return false
    if (root.chatWindow) root.execArgv([root.cliPath, "raise-window", "--", root.chatWindow.title])
    return true
  }
  property bool dnd: false
  property string lastError: ""
  // `omarchy plugin add` only clones the folder. The command line, the bridge
  // service and the keybindings come from install.sh; until it has run the
  // bridge is offline and we say so once, with a button that runs it.
  property bool serviceInstalled: true
  property bool setupPrompted: false
  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "").replace(/\/$/, "")
  readonly property string cliPath: Qt.resolvedUrl("bin/omarchy-signal").toString().replace("file://", "")

  Component.onCompleted: Hub.register(root)
  Component.onDestruction: Hub.unregister(root)

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

  FileView {
    id: unitFile
    path: (Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")) + "/systemd/user/omarchy-signal.service"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { root.serviceInstalled = true; root.dismissKey("setup:install") }
    onLoadFailed: { root.serviceInstalled = false; root.maybePromptSetup() }
  }

  function maybePromptSetup() {
    if (root.connected || root.serviceInstalled || root.setupPrompted) return
    root.setupPrompted = true
    root.pushToast({ key: "setup:install", kind: "setup", sticky: true, attachments: 0, convName: "",
                     title: "Finish setting up Signal",
                     body: "The plugin is in place. Its command, the background service and the keybindings still need setting up, which takes a minute in a terminal." })
  }

  function runInstaller() {
    // Launch before dismissing: if the exec fails the popup stays up rather
    // than vanishing and leaving the user with nothing to click.
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-installer.sh"])
    root.dismissKey("setup:install")
  }

  function runLink() {
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-link.sh"])
  }

  // ---------------------------------------------------------------- updates
  //
  // `omarchy plugin update` moves the plugin folder to new code and stops
  // there. This service is still the old code (services do not hot-reload), and
  // so is the running bridge. Notice the version on disk changing, bring the
  // unit, keybindings and bridge in line without asking anything (`sync`), and
  // offer the one step left: restarting the shell.
  property string loadedVersion: ""      // the plugin version this service was loaded from
  property string diskVersion: ""        // the version in the plugin folder right now
  property string syncedFor: ""          // sync runs once per version per shell session
  property string latestVersion: ""      // a newer release found by the daily check
  property string announcedVersion: ""

  FileView {
    id: manifestFile
    path: root.pluginDir + "/manifest.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var version = ""
      try { version = Model.singleLine(JSON.parse(text()).version || "", 20) } catch (e) { version = "" }
      if (!/^\d{1,4}\.\d{1,4}\.\d{1,5}$/.test(version)) return     // mid-write, or not a release: wait for the next change
      if (root.loadedVersion === "") root.loadedVersion = version
      root.diskVersion = version
      root.maybeSync()
    }
  }

  function maybeSync() {
    if (root.diskVersion === "" || root.syncedFor === root.diskVersion || syncProc.busy) return
    var filesMoved = root.diskVersion !== root.loadedVersion
    var bridgeBehind = root.bridgeVersion !== "" && root.bridgeVersion !== root.diskVersion
    if (!filesMoved && !bridgeBehind) return
    root.syncedFor = root.diskVersion
    syncProc.run([root.cliPath, "sync", "--json"])
  }

  BoundedProcess {
    id: syncProc
    maxChars: 64 * 1024
    timeoutMs: 60000
    onFinished: function(ok, text) {
      if (root.diskVersion === root.loadedVersion) return
      root.pushToast({ key: "setup:updated", kind: "updated", sticky: true, attachments: 0, convName: "",
                       title: "Signal updated to " + root.diskVersion,
                       body: "The background service is already on the new version. Restart the shell to load the new popups and chat window." })
    }
  }

  function restartShell() {
    root.dismissKey("setup:updated")
    root.execArgv(["omarchy-restart-shell"])
  }

  // Once a day at most (the CLI keeps the clock): ask the plugin's git remote
  // for release tags. Nothing else is sent, and the setting turns it off.
  Timer {
    interval: 120000
    running: root.updateCheck && root.connected
    repeat: false
    onTriggered: if (!checkProc.busy) checkProc.run([root.cliPath, "update", "--check", "--if-due", "--json"])
  }
  Timer {
    interval: 6 * 3600 * 1000
    running: root.updateCheck
    repeat: true
    onTriggered: if (!checkProc.busy) checkProc.run([root.cliPath, "update", "--check", "--if-due", "--json"])
  }
  BoundedProcess {
    id: checkProc
    maxChars: 16 * 1024
    timeoutMs: 60000
    onFinished: function(ok, text) {
      if (!ok) { root.latestVersion = ""; return }
      var found = null
      try { found = JSON.parse(text) } catch (e) { found = null }
      if (!found || found.available !== true) { root.latestVersion = ""; return }
      var latest = Model.singleLine(found.latest || "", 20)
      if (!Model.versionNewer(latest, root.diskVersion || root.loadedVersion)) { root.latestVersion = ""; return }
      root.latestVersion = latest
      if (root.announcedVersion === latest) return
      root.announcedVersion = latest
      root.pushToast({ key: "setup:update", kind: "update", sticky: false, attachments: 0, convName: "",
                       title: "Signal " + latest + " is available",
                       body: "You have " + (root.diskVersion || root.loadedVersion) + ". Updating takes a minute and keeps your account and history." })
    }
  }

  function runUpdate() {
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-update.sh"])
    root.dismissKey("setup:update")
  }

  // Toast queue: newest last.
  ListModel { id: toastModel }
  readonly property int toastCount: toastModel.count
  readonly property int maxToasts: 4
  property bool toastsHovered: false

  // Reply window state.
  property bool replyOpen: false
  property string replyKey: ""
  property string replyName: ""

  // Device-linking QR code, shown for terminals that cannot draw images.
  property bool qrOpen: false
  property string qrPath: ""
  readonly property string runDir: (Quickshell.env("OMARCHY_SIGNAL_RUNTIME_DIR") || Quickshell.env("XDG_RUNTIME_DIR") || "") + "/omarchy-signal/"

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
  }

  signal unreadChangedExternally(int total)
  // Everything the bridge announces, for the bar widget when it shares this connection.
  signal bridgeEvent(string name, var data)

  // ---------------------------------------------------------------- bridge

  readonly property alias bridge: link

  Bridge {
    id: link
    onEvent: function(name, data) { root.handleEvent(name, data) }
  }

  function applyStatus(d) {
    if ("connected" in d) root.connected = d.connected === true
    if ("linked" in d) root.linked = d.linked === true
    // A bridge from before 0.5 does not say; it then means what it always meant.
    root.accountsKnown = "accountsKnown" in d ? d.accountsKnown === true : root.connected
    if (typeof d.unread === "number") root.setUnread(d.unread)
    if ("notificationPreview" in d) root.previewEnabled = d.notificationPreview !== false
    if (typeof d.notificationTimeoutMs === "number") root.toastTimeoutMs = Math.max(1000, Math.min(120000, d.notificationTimeoutMs))
    if (typeof d.notifications === "string") root.notificationMode = d.notifications
    if ("respectDnd" in d) root.respectDnd = d.respectDnd !== false
    if (typeof d.notificationSound === "string") root.notificationSound = d.notificationSound
    if (typeof d.account === "string") root.account = d.account
    if ("emojiAutoconvert" in d) root.emojiAutoconvert = d.emojiAutoconvert !== false
    if ("attachmentThumbnails" in d) root.attachmentThumbnails = d.attachmentThumbnails !== false
    if (typeof d.scrollSpeed === "number") root.scrollSpeed = Math.max(0, Math.min(20, d.scrollSpeed))
    if (typeof d.windowOpacity === "number") root.windowOpacity = Math.max(0, Math.min(100, d.windowOpacity))
    if ("updateCheck" in d) root.updateCheck = d.updateCheck !== false
    if (typeof d.version === "string") root.bridgeVersion = Model.singleLine(d.version, 20)
    root.lastError = Model.singleLine(d.error || "", 200)
    if (root.connected) { root.dismissKey("setup:install"); root.maybeSync() }
    else { unitFile.reload(); root.maybePromptSetup() }
  }

  function handleEvent(name, d) {
    root.bridgeEvent(name, d)
    if (name === "hello" || name === "status") { root.applyStatus(d); return }
    if (name === "unread") {
      if (typeof d.total === "number") root.setUnread(d.total)
      // Read elsewhere (the terminal client, another device): drop that conversation's toasts.
      if (typeof d.conversation === "string") root.dismissKey(d.conversation)
      return
    }
    if (name === "conversationRemoved" && typeof d.conversation === "string") {
      root.dismissKey(d.conversation)
      if (root.replyOpen && root.replyKey === d.conversation) root.closeReply()
      root.closeWindow(d.conversation)
      return
    }
    // Conversation views (popup and the chat window) follow their own thread.
    if (root.replyOpen) replyView.onEvent(name, d)
    if (root.chatView) root.chatView.onEvent(name, d)
    if (name !== "message") return
    if (typeof d.conversation === "string" && d.outgoing !== true
        && root.windows.indexOf(d.conversation) >= 0 && d.conversation !== root.activeTab) {
      var u = root.tabUnread; u[d.conversation] = true; root.tabUnread = u
      root.refreshTabs()
    }
    var toast = Model.toastFromMessage(d, root.previewEnabled)
    if (!toast) return
    if (root.replyOpen && root.replyKey === toast.key) return
    if (root.chatView && toast.key === root.activeTab) return   // the chat window is showing it
    if (root.respectDnd && root.dnd) return
    if (root.notificationMode === "system") {
      root.execArgv(["omarchy-notification-send", "--app-name", "Signal", "-g", "󰭹", toast.title, toast.body,
                     "--exec", "omarchy-signal", "open", toast.key])
      return
    }
    if (root.notificationMode !== "popup") return
    toast.kind = "message"
    toast.sticky = false
    root.pushToast(toast)
    if (root.notificationSound && /^\/[^\0]+\.(wav|ogg|oga|mp3|flac)$/i.test(root.notificationSound))
      root.execArgv(["pw-play", root.notificationSound])
  }

  function setUnread(n) {
    root.unread = Math.max(0, n | 0)
    root.unreadChangedExternally(root.unread)
  }

  // ---------------------------------------------------------------- toasts

  function toastIndex(key) {
    for (var i = 0; i < toastModel.count; i++) if (toastModel.get(i).key === key) return i
    return -1
  }

  function pushToast(toast) {
    var row = {
      tid: Date.now() + "-" + Math.floor(Math.random() * 1e6),
      key: String(toast.key || ""),
      kind: String(toast.kind || "message"),
      title: Model.singleLine(toast.title || "", 120),
      body: Model.cleanText(toast.body || "", 400),
      convName: Model.singleLine(toast.convName || "", 80),
      attachments: Number(toast.attachments) || 0,
      sticky: toast.sticky === true,
      // A notice about the plugin itself stays twice as long as a message.
      expires: Date.now() + (toast.kind === "message" ? root.toastTimeoutMs : root.toastTimeoutMs * 2)
    }
    // A second message from the same chat replaces its card in place instead of stacking another.
    var at = root.toastIndex(row.key)
    if (at >= 0) { toastModel.set(at, row); return }
    toastModel.append(row)
    while (toastModel.count > root.maxToasts) toastModel.remove(0)
  }

  function dismissToast(tid) {
    for (var i = 0; i < toastModel.count; i++) if (toastModel.get(i).tid === tid) { toastModel.remove(i); return }
  }

  function dismissKey(key) {
    var at = root.toastIndex(key)
    if (at >= 0) toastModel.remove(at)
  }

  function dismissAll() { toastModel.clear() }

  // Coarse on purpose: nothing draws the time left, only the expiry needs it.
  Timer {
    interval: 500
    repeat: true
    running: toastModel.count > 0
    onTriggered: {
      if (root.toastsHovered) return
      var now = Date.now()
      for (var i = toastModel.count - 1; i >= 0; i--) {
        var t = toastModel.get(i)
        if (!t.sticky && t.expires <= now) toastModel.remove(i)
      }
    }
  }

  function activateToast(row) {
    if (row.kind === "setup") root.runInstaller()
    else if (row.kind === "updated") root.restartShell()
    else if (row.kind === "update") root.runUpdate()
    else root.openReply(row.key, row.convName)
  }

  function openReply(key, name) {
    if (!Model.isConversationKey(key)) return
    root.dismissKey(key)
    root.replyKey = key
    if (!name && root.tabNames[key]) name = root.tabNames[key]
    root.replyName = Model.singleLine(name || key, 80)
    root.replyOpen = true
    replyView.load(key, root.replyName)
    if (!name) root.refreshNames()               // opened by key alone (IPC / CLI): look the title up
  }

  function closeReply() {
    root.replyOpen = false
  }

  function openWindow(key, name) {
    if (!Model.isConversationKey(key)) return false
    if (name) { var n = root.tabNames; n[key] = Model.singleLine(name, 80); root.tabNames = n }
    var hadWindow = root.chatWindow !== null
    if (root.windows.indexOf(key) < 0) root.windows = root.windows.concat([key])   // creates the window when it is the first tab
    root.showTab(key)
    if (hadWindow && root.chatWindow) root.execArgv([root.cliPath, "raise-window", "--", root.chatWindow.title])
    root.dismissKey(key)
    return true
  }

  function closeWindow(key) {
    var idx = root.windows.indexOf(key)
    if (idx < 0) return
    var next = root.windows.filter(function(k) { return k !== key })
    if (next.length === 0) {
      root.activeTab = ""
      root.windows = []          // the loader tears the window down
    } else {
      root.windows = next
      if (root.activeTab === key) root.showTab(next[Math.min(idx, next.length - 1)])
    }
    root.refreshTabs()
  }

  function openTerminal(key) {
    var argv = Model.tuiArgv(key)
    argv[0] = root.cliPath
    root.execArgv(argv)
    root.closeReply()
    if (key) root.dismissKey(key)
  }

  IpcHandler {
    target: "iamteedoh.signal"
    function open(): string { root.openTerminal(""); return "ok" }
    function reply(key: string): string { root.openReply(key, ""); return "ok" }
    function window(key: string): string {
      if (!key) { root.openTerminal(""); return "ok" }
      return root.openWindow(key, "") ? "ok" : "refused"
    }
    function closeWindow(key: string): string { root.closeWindow(key); return "ok" }
    function windows(): string { return JSON.stringify(root.windowTabs) }
    function raise(key: string): string { return root.raiseWindow(key) ? "ok" : "unknown" }
    function closeChatWindow(): string { root.windows = []; root.activeTab = ""; root.refreshTabs(); return "ok" }
    function dismiss(): string { root.dismissAll(); return "ok" }
    function close(): string { root.closeReply(); return "ok" }
    function demo(): string { root.execArgv([root.cliPath, "demo"]); return "ok" }
    function toasts(): string { return String(toastModel.count) }
    function showQr(path: string): string { return root.showQr(path) ? "ok" : "refused" }
    function hideQr(): string { root.hideQr(); return "ok" }
    function unread(): string { return String(root.unread) }
    function state(): string {
      return JSON.stringify({ connected: root.connected, linked: root.linked, accountsKnown: root.accountsKnown, unread: root.unread,
                              mode: root.notificationMode, dnd: root.dnd, respectDnd: root.respectDnd,
                              version: root.loadedVersion, diskVersion: root.diskVersion, bridgeVersion: root.bridgeVersion,
                              latestVersion: root.latestVersion })
    }
  }

  // ---------------------------------------------------------------- toast surface
  //
  // Drawn like Omarchy's own notifications: the same surface colours and
  // border, a 40px mark for the sender, the title in bold over a dimmer body,
  // and the things a card can do as a bar along its bottom edge.

  PanelWindow {
    id: toastWindow
    // Kept on screen until the last card has finished leaving.
    visible: toastModel.count > 0 || toastLinger.running
    anchors { top: true; right: true }
    margins { top: Style.gapsOut + Style.bar.sizeHorizontal; right: Style.gapsOut }
    implicitWidth: Style.space(380)
    implicitHeight: Math.max(1, toastList.contentHeight)
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-toast"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    Timer { id: toastLinger; interval: 320 }
    Connections { target: toastModel; function onCountChanged() { if (toastModel.count === 0) toastLinger.restart() } }

    ListView {
      id: toastList
      anchors.fill: parent
      interactive: false
      // The pointer over any card holds every card's clock.
      HoverHandler { onHoveredChanged: root.toastsHovered = hovered }
      spacing: Style.space(8)
      model: toastModel

      // A card slides in from the screen edge, leaves the same way, and the
      // ones below close the gap instead of jumping.
      add: Transition {
        ParallelAnimation {
          NumberAnimation { property: "opacity"; from: 0; to: 1; duration: root.ms(200); easing.type: Easing.OutCubic }
          NumberAnimation { property: "x"; from: Style.space(48); to: 0; duration: root.ms(260); easing.type: Easing.OutCubic }
        }
      }
      remove: Transition {
        ParallelAnimation {
          NumberAnimation { property: "opacity"; to: 0; duration: root.ms(160); easing.type: Easing.InCubic }
          NumberAnimation { property: "x"; to: Style.space(48); duration: root.ms(200); easing.type: Easing.InCubic }
        }
      }
      displaced: Transition {
        NumberAnimation { properties: "y"; duration: root.ms(220); easing.type: Easing.OutCubic }
      }

      delegate: ToastCard {
        id: card
        required property string tid
        required property string key
        required kind
        required title
        required body
        required convName
        required attachments
        width: ListView.view.width
        onDismissed: root.dismissToast(card.tid)
        onDeed: function(id) {
          if (id === "window") root.openWindow(card.key, card.convName)
          else if (id === "terminal") root.openTerminal(card.key)
          else root.activateToast({ key: card.key, kind: card.kind, convName: card.convName })
        }
      }
    }
  }

  // ---------------------------------------------------------------- reply dialog (click a toast)

  PanelWindow {
    id: replyWindow
    // 0 closed, 1 open: the scrim fades and the dialog eases in and out with it.
    property real reveal: root.replyOpen ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: root.ms(180); easing.type: Easing.OutCubic } }
    visible: reveal > 0.001
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-reply"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.replyOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: Commons.Color.menu.scrim
      opacity: replyWindow.reveal
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
        anchors.verticalCenterOffset: Style.space(12) * (1 - replyWindow.reveal)
        width: Math.min(parent.width - Style.space(48), Style.space(680))
        height: Math.min(parent.height - Style.space(48), Style.space(640))
        color: Util.alpha(Commons.Color.popups.background, root.surfaceAlpha(0.98))
        borderSpec: Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, Style.space(2)))
        radius: Style.cornerRadius
        opacity: replyWindow.reveal
        scale: 0.97 + 0.03 * replyWindow.reveal
        MouseArea { anchors.fill: parent; onClicked: {} }

        ConversationView {
          id: replyView
          anchors.fill: parent
          anchors.margins: Style.spacing.panelPadding
          cliPath: root.cliPath
          bridge: link
          connected: root.connected
          linked: root.linked
          account: root.account
          emojiAutoconvert: root.emojiAutoconvert
          thumbnails: root.attachmentThumbnails
          scrollSpeed: root.scrollSpeed
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

  BoundedProcess { id: floatProc; maxChars: 16 * 1024; timeoutMs: 15000 }

  Loader {
    id: chatLoader
    active: root.windows.length > 0
    sourceComponent: chatWindowComponent
    onLoaded: {
      item.visible = true
      if (root.activeTab) item.view.load(root.activeTab, root.tabNames[root.activeTab] || "")
      Qt.callLater(function() {
        if (!chatLoader.item) return
        floatProc.run([root.cliPath, "float-window", "--", chatLoader.item.title])
      })
      root.refreshNames()
    }
  }

  Component {
    id: chatWindowComponent
    FloatingWindow {
      id: win
      property alias view: chatView
      title: "Signal · " + (chatView.conversationName || "chats")
      color: Util.alpha(Commons.Color.popups.background, root.surfaceAlpha(1.0))
      implicitWidth: Style.space(680)
      implicitHeight: Style.space(640)
      minimumSize: Qt.size(Style.space(380), Style.space(320))
      visible: false
      onVisibleChanged: {
        // Closed by the window manager: drop every tab (which destroys this window).
        if (!visible && root.windows.length > 0) { root.windows = []; root.activeTab = ""; root.refreshTabs() }
      }

      ConversationView {
        id: chatView
        anchors.fill: parent
        anchors.margins: Style.spacing.panelPadding
        cliPath: root.cliPath
        bridge: link
        connected: root.connected
        linked: root.linked
        account: root.account
        emojiAutoconvert: root.emojiAutoconvert
        thumbnails: root.attachmentThumbnails
        scrollSpeed: root.scrollSpeed
        detached: true
        siblings: root.windowTabs
        // The window's content eases in once, when the window first maps.
        opacity: 0
        Component.onCompleted: opacity = 1
        Behavior on opacity { NumberAnimation { duration: root.ms(160); easing.type: Easing.OutCubic } }
        onConversationNameChanged: {
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
    }
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
      if (root.activeTab && n[root.activeTab] && root.chatView && root.chatView.conversationName !== n[root.activeTab]) root.chatView.conversationName = n[root.activeTab]
      if (root.replyOpen && n[root.replyKey] && root.replyName !== n[root.replyKey]) {
        root.replyName = n[root.replyKey]
        replyView.conversationName = root.replyName
      }
      root.refreshTabs()
    }
  }

  // Asked over the socket that is already open; only when a title is missing.
  property bool namesBusy: false
  function refreshNames() {
    if (root.namesBusy) return
    var missing = root.replyOpen && !root.tabNames[root.replyKey]
    for (var i = 0; i < root.windows.length && !missing; i++) if (!root.tabNames[root.windows[i]]) missing = true
    if (!missing) return
    root.namesBusy = link.request("conversations", { includeArchived: true }, function(ok, rows) {
      if (ok && Array.isArray(rows)) root.applyNames(rows)
      link.request("contacts", {}, function(ok2, contacts) {
        if (ok2 && Array.isArray(contacts)) root.applyNames(contacts)
        link.request("groups", {}, function(ok3, groups) {
          root.namesBusy = false
          if (ok3 && Array.isArray(groups)) root.applyNames(groups.map(function(g) { return { key: g.key, name: g.name } }))
        })
      })
    })
  }

  // ---------------------------------------------------------------- linking QR popup

  PanelWindow {
    id: qrWindow
    property real reveal: root.qrOpen ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: root.ms(180); easing.type: Easing.OutCubic } }
    visible: reveal > 0.001
    onVisibleChanged: if (!visible) root.qrPath = ""
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "omarchy-signal-qr"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.qrOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      color: Commons.Color.menu.scrim
      opacity: qrWindow.reveal
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
        color: Util.alpha(Commons.Color.popups.background, 0.98)
        borderSpec: Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, Style.space(2)))
        radius: Style.cornerRadius
        opacity: qrWindow.reveal
        scale: 0.97 + 0.03 * qrWindow.reveal
        MouseArea { anchors.fill: parent; onClicked: {} }

        ColumnLayout {
          id: qrLayout
          anchors.centerIn: parent
          spacing: Style.space(14)

          Text {
            Layout.alignment: Qt.AlignHCenter
            text: "Link this computer to Signal"
            textFormat: Text.PlainText
            color: Commons.Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.heading
            font.bold: true
          }
          Rectangle {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: Style.space(300)
            implicitHeight: implicitWidth
            radius: Style.cornerRadius
            color: "white"              // a scanner needs dark modules on white, whatever the theme
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
            text: "On your phone: Signal → Settings → Linked devices → Link new device.\nAfter the scan, nothing shows on the phone until the link finishes (10 to 60 seconds)."
            textFormat: Text.PlainText
            color: Commons.Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          Row {
            Layout.alignment: Qt.AlignHCenter
            spacing: Style.space(6)
            KeyCap { label: "Esc" }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "hides this; the terminal keeps waiting"
              textFormat: Text.PlainText
              color: Util.alpha(Commons.Color.popups.text, 0.55)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }
  }
}
