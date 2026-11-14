// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model
import "Hub.js" as Hub

// Bar widget: Signal glyph with the unread count. Click opens a keyboard-driven
// list of conversations and contacts, built from Omarchy's own panel kit so it
// sits next to the audio, network and bluetooth panels without standing out.
// Enter opens the chosen chat in the chat window, Ctrl+Enter or a right-click
// in the terminal client.
Panel {
  id: root
  moduleName: "iamteedoh.signal"
  ipcTarget: "iamteedoh.signal.bar"

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

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color dim: Util.alpha(root.fg, 0.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property int unread: 0
  property bool connected: false
  property bool linked: false
  property bool accountsKnown: false
  readonly property bool unlinked: root.connected && root.accountsKnown && !root.linked
  property var conversations: []
  property var contacts: []
  property string query: ""
  property int cursor: 0
  property bool showContacts: false
  readonly property bool hideWhenZero: setting("hideWhenZero", false) === true
  readonly property string cliPath: Qt.resolvedUrl("bin/omarchy-signal").toString().replace("file://", "")
  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace("file://", "").replace(/\/$/, "")

  // The running Service.qml, when this widget shares an engine with it. Asking
  // it directly opens a chat without starting a process; without it the
  // widget falls back to the shell's IPC.
  property var service: null
  readonly property var openWindows: root.service ? root.service.windowTabs : []
  readonly property string latestVersion: root.service ? root.service.latestVersion : ""
  // The readings in the panel follow the same setting as the chat window's.
  readonly property bool telemetry: root.service ? root.service.telemetry !== false : true
  readonly property bool needsSetup: !root.connected && root.service !== null && root.service.serviceInstalled === false
  Timer {
    interval: 2000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: if (Hub.service !== root.service) root.service = Hub.service
  }

  readonly property var rows: {
    var list = root.showContacts
      ? root.contacts.map(function(c) { return { key: c.key, name: Model.singleLine(c.displayName || c.key, 60), sub: Model.singleLine(c.number || c.username || "", 40), unread: 0, ts: 0, open: false, typing: false } })
      : Model.sortConversations(root.conversations).map(function(c) {
          var open = root.openWindows.some(function(w) { return w.key === c.key })
          return { key: c.key, name: Model.singleLine(c.name || c.key, 60), sub: Model.singleLine(c.preview || "", 80), unread: c.unread || 0, ts: c.lastTs || 0, open: open, typing: c.typing === true }
        })
    return Model.filterRows(list, root.query).slice(0, 500)
  }

  onOpenedChanged: {
    if (opened) {
      column.field.text = ""
      root.query = ""
      root.cursor = 0
      root.showContacts = false
      refresh()
    }
  }

  function toggleMode() {
    root.showContacts = !root.showContacts
    root.cursor = 0
  }

  // The lists come over the socket that is already open: no process is started
  // to open the panel, and a burst of events becomes one refresh.
  property bool refreshing: false
  function refresh() {
    if (root.refreshing || !link.online) return
    root.refreshing = link.request("conversations", {}, function(ok, rows) {
      if (ok && Array.isArray(rows)) root.conversations = rows
      link.request("contacts", {}, function(ok2, contacts) {
        var list = ok2 && Array.isArray(contacts) ? contacts.slice() : null
        link.request("groups", {}, function(ok3, groups) {
          root.refreshing = false
          if (list === null) return
          if (ok3 && Array.isArray(groups))
            for (var i = 0; i < groups.length; i++) list.push({ key: groups[i].key, displayName: groups[i].name, number: "group" })
          root.contacts = list
        })
      })
    })
  }
  Timer { id: refreshSoon; interval: 150; onTriggered: root.refresh() }

  function openTerminal(key) {
    var argv = Model.tuiArgv(key)
    argv[0] = root.cliPath
    root.execArgv(argv)
  }

  // Enter opens the conversation in the chat window; Ctrl+Enter or a
  // right-click on a row opens the terminal client instead.
  function activate(inTerminal) {
    if (root.rows.length === 0) {
      if (!root.query.trim()) return
      root.openTerminal("")
    } else {
      var row = root.rows[Math.max(0, Math.min(root.rows.length - 1, root.cursor))]
      if (inTerminal) root.openTerminal(row.key)
      else if (root.service) root.service.openWindow(row.key, row.name)
      else root.execArgv(["omarchy-shell", "iamteedoh.signal", "window", row.key])
    }
    root.close()
  }

  function moveCursor(delta) {
    if (root.rows.length === 0) return
    root.cursor = Math.max(0, Math.min(root.rows.length - 1, root.cursor + delta))
  }

  function runSetup() {
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-installer.sh"])
    root.close()
  }
  function runLink() {
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-link.sh"])
    root.close()
  }
  function runUpdate() {
    root.execArgv(["omarchy-launch-terminal", root.pluginDir + "/scripts/run-update.sh"])
    root.close()
  }

  visible: !(hideWhenZero && unread === 0 && !opened)
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  // Live state from the bridge's socket: a connection, not a process per bar.
  Bridge {
    id: link
    onEvent: function(name, d) {
      if (name === "hello" || name === "status") {
        if ("connected" in d) root.connected = d.connected === true
        if ("linked" in d) root.linked = d.linked === true
        root.accountsKnown = "accountsKnown" in d ? d.accountsKnown === true : root.connected
        if (typeof d.unread === "number") root.unread = d.unread
        if (root.opened) refreshSoon.restart()
      } else if (name === "unread" && typeof d.total === "number") {
        root.unread = d.total
        if (root.opened) refreshSoon.restart()
      } else if ((name === "message" || name === "typing" || name === "sent" || name === "conversationRemoved") && root.opened) {
        refreshSoon.restart()
      }
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰭹" + (root.unread > 0 && !vertical ? " " + Model.unreadLabel(root.unread) : "")
    slotSize: Style.bar.iconSlot * (root.unread > 0 && !vertical ? 1.6 : 1)
    tooltipText: root.linked ? (root.unread > 0 ? root.unread + " unread Signal message" + (root.unread === 1 ? "" : "s") : "Signal")
                 : root.unlinked ? "Signal: this computer is not linked yet"
                 : root.connected ? "Signal: connecting"
                 : root.needsSetup ? "Signal: setup is not finished" : "Signal: the background service is not running"
    onPressed: function(b) {
      if (b === Qt.RightButton) root.openTerminal("")
      else if (b === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }

    // Unread dot in the corner of the glyph. It draws attention once, when the
    // count goes up, and then sits still: a mark that pulses for as long as
    // anything is unread keeps the bar repainting sixty times a second.
    Rectangle {
      id: dot
      visible: root.unread > 0
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: Math.max(1, Style.space(3))
      width: Math.max(4, Style.space(6)); height: width; radius: width / 2
      color: root.bar ? root.bar.foreground : Commons.Color.accent
      SequentialAnimation {
        id: nudge
        NumberAnimation { target: dot; property: "scale"; to: 1.7; duration: root.ms(140); easing.type: Easing.OutCubic }
        NumberAnimation { target: dot; property: "scale"; to: 1.0; duration: root.ms(260); easing.type: Easing.OutCubic }
      }
    }
  }
  property int unreadSeen: 0
  onUnreadChanged: {
    if (root.unread > root.unreadSeen) nudge.restart()
    root.unreadSeen = root.unread
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: column.field
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // The search field owns the keyboard while it has focus (the catcher
      // would otherwise eat j/k/h/l/x/space as navigation); it forwards the
      // navigation keys itself below.
      blocked: column.field.activeFocus
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy); else if (dx !== 0) root.toggleMode() }
      onActivateRequested: root.activate(false)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ChatList {
        id: column
        owner: root
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
      }
    }
  }
}
