import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Bar widget: Signal glyph with an unread badge. Click opens a keyboard-driven
// list of conversations and contacts; Enter opens the chosen one in the
// terminal client, right-click opens the client directly.
BarWidget {
  id: root
  moduleName: "iamteedoh.signal"

  property int unread: 0
  property bool connected: false
  property bool linked: false
  property var conversations: []
  property var contacts: []
  property string query: ""
  property int cursor: 0
  property bool showContacts: false
  readonly property bool hideWhenZero: setting("hideWhenZero", false) === true
  readonly property string cliPath: Qt.resolvedUrl("bin/omarchy-signal").toString().replace("file://", "")

  readonly property var rows: {
    var list = root.showContacts
      ? root.contacts.map(function(c) { return { key: c.key, name: Model.singleLine(c.displayName || c.key, 60), sub: Model.singleLine(c.number || c.username || "", 40), unread: 0, ts: 0 } })
      : Model.sortConversations(root.conversations).map(function(c) { return { key: c.key, name: Model.singleLine(c.name || c.key, 60), sub: Model.singleLine(c.preview || "", 80), unread: c.unread || 0, ts: c.lastTs || 0, typing: c.typing === true } })
    return Model.filterRows(list, root.query).slice(0, 40)
  }

  readonly property bool opened: panel.open
  function open() { panel.open = true }
  function close() { panel.open = false }
  function toggle() { panel.open = !panel.open }

  onOpenedChanged: {
    if (opened) {
      root.query = ""
      root.cursor = 0
      root.showContacts = false
      refresh()
    }
  }

  function refresh() {
    if (!convProc.running) convProc.running = true
    if (!contactsProc.running) contactsProc.running = true
  }

  function activate() {
    if (root.rows.length === 0) {
      if (root.query.trim()) Util.execArgv(Model.tuiArgv(""))
      return
    }
    var row = root.rows[Math.max(0, Math.min(root.rows.length - 1, root.cursor))]
    Util.execArgv(Model.tuiArgv(row.key))
    root.close()
  }

  function moveCursor(delta) {
    if (root.rows.length === 0) return
    root.cursor = Math.max(0, Math.min(root.rows.length - 1, root.cursor + delta))
  }

  visible: !(hideWhenZero && unread === 0 && !opened)
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  // Live unread count from the event stream (one process per bar instance is
  // cheap: it is a thin socket client).
  Process {
    id: events
    command: [root.cliPath, "events"]
    running: true
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) {
        var ev = Model.parseEventLine(line)
        if (!ev) return
        var d = ev.data
        if (ev.event === "hello" || ev.event === "status") {
          if ("connected" in d) root.connected = d.connected === true
          if ("linked" in d) root.linked = d.linked === true
          if (typeof d.unread === "number") root.unread = d.unread
        } else if (ev.event === "unread" && typeof d.total === "number") {
          root.unread = d.total
          if (root.opened) root.refresh()
        } else if ((ev.event === "message" || ev.event === "typing" || ev.event === "sent") && root.opened) {
          root.refresh()
        }
      }
    }
    onExited: eventsRestart.restart()
  }
  Timer { id: eventsRestart; interval: 3000; onTriggered: events.running = true }

  Process {
    id: convProc
    command: [root.cliPath, "conversations", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var rows = []
        try { rows = JSON.parse(text) } catch (e) { rows = [] }
        if (Array.isArray(rows)) root.conversations = rows
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
        if (!obj || typeof obj !== "object") return
        var list = Array.isArray(obj.contacts) ? obj.contacts.slice() : []
        var groups = Array.isArray(obj.groups) ? obj.groups : []
        for (var i = 0; i < groups.length; i++) list.push({ key: groups[i].key, displayName: groups[i].name, number: "group" })
        root.contacts = list
      }
    }
  }

  IpcHandler {
    target: "iamteedoh.signal.bar"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰭹" + (root.unread > 0 && !vertical ? " " + Model.unreadLabel(root.unread) : "")
    slotSize: Style.bar.iconSlot * (root.unread > 0 && !vertical ? 1.6 : 1)
    tooltipText: root.linked ? (root.unread > 0 ? root.unread + " unread Signal message" + (root.unread === 1 ? "" : "s") : "Signal")
                             : (root.connected ? "Signal: not linked (omarchy-signal link)" : "Signal: bridge offline")
    onPressed: function(b) {
      if (b === Qt.RightButton) Util.execArgv(Model.tuiArgv(""))
      else if (b === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }

    // Unread dot in the corner of the glyph.
    Rectangle {
      visible: root.unread > 0
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: Math.max(1, Style.space(3))
      width: Math.max(4, Style.space(6)); height: width; radius: width / 2
      color: root.bar ? root.bar.foreground : Color.accent
      SequentialAnimation on opacity {
        running: root.unread > 0
        loops: Animation.Infinite
        NumberAnimation { to: 0.35; duration: 900; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1.0; duration: 900; easing.type: Easing.InOutSine }
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy); else if (dx !== 0) root.showContacts = !root.showContacts }
      onActivateRequested: root.activate()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Backspace) { root.query = root.query.slice(0, -1); root.cursor = 0; event.accepted = true; return }
        if (event.key === Qt.Key_Tab) { root.showContacts = !root.showContacts; root.cursor = 0; event.accepted = true; return }
        if (event.text && event.text.length === 1 && event.text >= " " && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier))) {
          root.query += event.text; root.cursor = 0; event.accepted = true
        }
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(8)

        // Header
        Item {
          width: parent.width
          implicitHeight: Math.max(headerTitle.implicitHeight, headerState.implicitHeight)
          Text {
            id: headerTitle
            anchors.left: parent.left
            text: (root.showContacts ? "CONTACTS" : "CONVERSATIONS") + (root.query ? "  ▸ " + root.query : "")
            textFormat: Text.PlainText
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
            font.letterSpacing: 1.5
            elide: Text.ElideRight
            width: parent.width - headerState.implicitWidth - Style.space(8)
          }
          Text {
            id: headerState
            anchors.right: parent.right
            text: root.linked ? "◉ " + (root.unread > 0 ? root.unread + " unread" : "secure") : (root.connected ? "◌ not linked" : "◌ offline")
            textFormat: Text.PlainText
            color: root.linked ? Color.accent : Color.urgent
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
        PanelSeparator { width: parent.width }

        Text {
          visible: root.rows.length === 0
          width: parent.width
          text: root.linked ? (root.query ? "No match. Enter opens the client." : (root.showContacts ? "No contacts yet." : "No conversations yet. Tab for contacts.")) : "Link this computer first: omarchy-signal link"
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: Util.alpha(root.bar.foreground, 0.6)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
        }

        Repeater {
          model: root.rows
          delegate: Rectangle {
            id: rowItem
            required property var modelData
            required property int index
            width: column.width
            implicitHeight: rowLayout.implicitHeight + Style.space(10)
            radius: Style.cornerRadius / 2
            readonly property bool current: index === root.cursor
            color: current ? Style.selectedFill : (rowHover.containsMouse ? Style.hoverFill : "transparent")
            border.width: current ? 1 : 0
            border.color: Style.selectedBorderColor

            MouseArea {
              id: rowHover
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.cursor = rowItem.index
              onClicked: { root.cursor = rowItem.index; root.activate() }
            }

            Row {
              id: rowLayout
              anchors.left: parent.left; anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(8)
              spacing: Style.space(8)
              Text {
                text: rowItem.modelData.unread > 0 ? "●" : "○"
                textFormat: Text.PlainText
                color: rowItem.modelData.unread > 0 ? Color.accent : Util.alpha(root.bar.foreground, 0.5)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                anchors.verticalCenter: parent.verticalCenter
              }
              Column {
                width: parent.width - Style.space(60)
                spacing: Style.space(2)
                Text {
                  width: parent.width
                  text: rowItem.modelData.name
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: rowItem.modelData.unread > 0
                }
                Text {
                  width: parent.width
                  visible: text.length > 0
                  text: rowItem.modelData.typing ? "typing…" : rowItem.modelData.sub
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  color: rowItem.modelData.typing ? Color.accent : Util.alpha(root.bar.foreground, 0.55)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: rowItem.modelData.unread > 0 ? Model.unreadLabel(rowItem.modelData.unread) : Model.relativeTime(rowItem.modelData.ts)
                textFormat: Text.PlainText
                color: rowItem.modelData.unread > 0 ? Color.accent : Util.alpha(root.bar.foreground, 0.5)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: rowItem.modelData.unread > 0
              }
            }
          }
        }

        PanelSeparator { width: parent.width }
        Text {
          width: parent.width
          text: "type to filter · ↑↓ move · Enter open · Tab " + (root.showContacts ? "conversations" : "contacts") + " · right-click icon opens client"
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          color: Util.alpha(root.bar.foreground, 0.5)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
