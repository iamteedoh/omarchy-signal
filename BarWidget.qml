import QtQuick
import Quickshell
import Quickshell.Io
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Bar widget: Signal glyph with an unread badge. Click opens a keyboard-driven
// list of conversations and contacts; Enter opens the chosen one in the
// terminal client, right-click opens the client directly.
Panel {
  id: root
  moduleName: "iamteedoh.signal"
  ipcTarget: "iamteedoh.signal.bar"
  readonly property bool vertical: bar ? bar.vertical : false

  property int unread: 0
  property bool connected: false
  property bool linked: false
  property var conversations: []
  property var contacts: []
  property var openWindows: []           // [{key, name}] detached conversation windows
  property string query: ""
  property int cursor: 0
  property bool showContacts: false
  readonly property bool hideWhenZero: setting("hideWhenZero", false) === true
  readonly property string cliPath: Qt.resolvedUrl("bin/omarchy-signal").toString().replace("file://", "")

  readonly property var rows: {
    var list = root.showContacts
      ? root.contacts.map(function(c) { return { key: c.key, name: Model.singleLine(c.displayName || c.key, 60), sub: Model.singleLine(c.number || c.username || "", 40), unread: 0, ts: 0 } })
      : Model.sortConversations(root.conversations).map(function(c) {
          var open = root.openWindows.some(function(w) { return w.key === c.key })
          return { key: c.key, name: (open ? "⧉ " : "") + Model.singleLine(c.name || c.key, 60), sub: Model.singleLine(c.preview || "", 80), unread: c.unread || 0, ts: c.lastTs || 0, typing: c.typing === true }
        })
    return Model.filterRows(list, root.query).slice(0, 500)
  }

  onOpenedChanged: {
    if (opened) {
      searchField.text = ""
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

  function refresh() {
    if (!convProc.running) convProc.running = true
    if (!contactsProc.running) contactsProc.running = true
    if (!windowsProc.running) windowsProc.running = true
  }

  Process {
    id: windowsProc
    command: ["omarchy-shell", "iamteedoh.signal", "windows"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var rows = []
        try { rows = JSON.parse(text) } catch (e) { rows = [] }
        root.openWindows = Array.isArray(rows) ? rows : []
      }
    }
  }

  // Enter opens the conversation in its own window (the popup look, detached);
  // Ctrl+Enter or right-click on a row opens the full terminal client instead.
  function activate(inTerminal) {
    var argv
    if (root.rows.length === 0) {
      if (!root.query.trim()) return
      argv = Model.tuiArgv("")
    } else {
      var row = root.rows[Math.max(0, Math.min(root.rows.length - 1, root.cursor))]
      argv = inTerminal ? Model.tuiArgv(row.key) : [root.cliPath, "window", "--", row.key]
    }
    argv[0] = root.cliPath
    Util.execArgv(argv)
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

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰭹" + (root.unread > 0 && !vertical ? " " + Model.unreadLabel(root.unread) : "")
    slotSize: Style.bar.iconSlot * (root.unread > 0 && !vertical ? 1.6 : 1)
    tooltipText: root.linked ? (root.unread > 0 ? root.unread + " unread Signal message" + (root.unread === 1 ? "" : "s") : "Signal")
                             : (root.connected ? "Signal: not linked (omarchy-signal link)" : "Signal: bridge offline")
    onPressed: function(b) {
      if (b === Qt.RightButton) { var argv = Model.tuiArgv(""); argv[0] = root.cliPath; Util.execArgv(argv) }
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
    open: root.opened
    focusTarget: searchField
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // The search field owns the keyboard while it has focus (the catcher
      // would otherwise eat j/k/h/l/x/space as navigation); it forwards the
      // navigation keys itself below.
      blocked: searchField.activeFocus
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveCursor(dy); else if (dx !== 0) root.toggleMode() }
      onActivateRequested: root.activate(false)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

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
            text: root.showContacts ? "CONTACTS" : "CONVERSATIONS"
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
            text: (root.linked ? "◉ " + (root.unread > 0 ? root.unread + " unread" : "secure") : (root.connected ? "◌ not linked" : "◌ offline"))
                  + (root.openWindows.length > 0 ? "  ⧉ " + root.openWindows.length + (root.openWindows.length === 1 ? " window" : " windows") : "")
            textFormat: Text.PlainText
            color: root.linked ? Color.accent : Color.urgent
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }

        TextField {
          id: searchField
          width: parent.width
          foreground: root.bar.foreground
          placeholderText: root.showContacts ? "Search contacts and groups…" : "Search conversations…"
          verticalPadding: Style.space(4)
          onTextChanged: { root.query = text; root.cursor = 0 }
          Keys.onPressed: function(event) {
            var ctrl = event.modifiers & Qt.ControlModifier
            if (event.key === Qt.Key_Down || (ctrl && event.key === Qt.Key_N)) { root.moveCursor(1); event.accepted = true }
            else if (event.key === Qt.Key_Up || (ctrl && event.key === Qt.Key_P)) { root.moveCursor(-1); event.accepted = true }
            else if (event.key === Qt.Key_PageDown) { root.moveCursor(8); event.accepted = true }
            else if (event.key === Qt.Key_PageUp) { root.moveCursor(-8); event.accepted = true }
            else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.activate(!!(event.modifiers & Qt.ControlModifier)); event.accepted = true }
            else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) { root.toggleMode(); event.accepted = true }
            else if (event.key === Qt.Key_Escape) {
              if (searchField.text.length > 0) searchField.text = ""; else root.close()
              event.accepted = true
            }
          }
        }

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

        // Scrollable list: capped height so a long address book never pushes
        // the popup off screen; the cursor row is kept in view.
        ListView {
          id: list
          width: parent.width
          height: Math.min(contentHeight, Style.space(420))
          spacing: Style.space(2)
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
          model: root.rows
          currentIndex: root.cursor
          onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

          delegate: Rectangle {
            id: rowItem
            required property var modelData
            required property int index
            width: ListView.view.width
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
              acceptedButtons: Qt.LeftButton | Qt.RightButton
              onEntered: root.cursor = rowItem.index
              onClicked: function(mouse) { root.cursor = rowItem.index; root.activate(mouse.button === Qt.RightButton) }
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

        // Footer: hints on the left, position in the list on the right.
        Item {
          width: parent.width
          implicitHeight: Math.max(footerHints.implicitHeight, footerPos.implicitHeight)
          Text {
            id: footerHints
            anchors.left: parent.left
            width: parent.width - footerPos.implicitWidth - Style.space(8)
            text: "↑↓ move · Enter window · Ctrl+Enter terminal · Tab " + (root.showContacts ? "conversations" : "contacts") + " · Esc " + (root.query ? "clear" : "close")
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Util.alpha(root.bar.foreground, 0.5)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
          Text {
            id: footerPos
            anchors.right: parent.right
            text: root.rows.length > 0 ? (root.cursor + 1) + " / " + root.rows.length : ""
            textFormat: Text.PlainText
            color: Util.alpha(root.bar.foreground, 0.7)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
