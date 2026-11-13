// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// What the bar panel shows: the connection state, the one thing left to do,
// the search field, the list of conversations or contacts, and the keys.
//
// It holds no state of its own. `owner` is the bar widget (BarWidget.qml),
// which keeps the lists, the cursor and the actions; this draws them. Keeping
// the drawing apart from the panel window is also what lets `make screenshots`
// render it without a compositor.
Column {
  id: column
  required property var owner
  readonly property alias field: searchField
  spacing: Style.spacing.lg

  // Header: what this is, and how the connection is doing.
  Item {
    width: parent.width
    implicitHeight: Math.max(headerTitle.implicitHeight, headerState.implicitHeight)
    Text {
      id: headerTitle
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      text: "Signal"
      textFormat: Text.PlainText
      color: owner.fg
      font.family: owner.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }
    Text {
      id: headerState
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: owner.linked ? (owner.unread > 0 ? owner.unread + " unread" : "Connected")
            : owner.unlinked ? "Not linked" : owner.connected ? "Connecting" : "Offline"
      textFormat: Text.PlainText
      color: owner.linked ? (owner.unread > 0 ? Commons.Color.accent : owner.dim) : (owner.connected && !owner.unlinked ? owner.dim : Commons.Color.urgent)
      font.family: owner.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // The one thing left to do, when there is one: finish setup, link, or update.
  Rectangle {
    id: notice
    readonly property string kind: owner.needsSetup ? "setup" : owner.unlinked ? "link" : owner.latestVersion !== "" ? "update" : ""
    visible: kind !== ""
    width: parent.width
    implicitHeight: noticeRow.implicitHeight + Style.space(16)
    radius: Style.cornerRadius
    color: Style.normalFillFor(owner.fg, Commons.Color.accent)
    border.width: 1
    border.color: Style.normalBorderFor(owner.fg, Commons.Color.accent)
    Row {
      id: noticeRow
      anchors.left: parent.left; anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.margins: Style.space(10)
      spacing: Style.space(10)
      Text {
        width: parent.width - noticeButton.width - parent.spacing
        anchors.verticalCenter: parent.verticalCenter
        text: notice.kind === "setup" ? "Setup is not finished: the command, the background service and the keybindings are still missing."
              : notice.kind === "link" ? "This computer is not linked to your Signal account yet."
              : "Signal " + owner.latestVersion + " is available."
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: owner.fg
        font.family: owner.fontFamily
        font.pixelSize: Style.font.body
      }
      Button {
        id: noticeButton
        anchors.verticalCenter: parent.verticalCenter
        bordered: true
        foreground: owner.fg
        text: notice.kind === "setup" ? "Finish setup" : notice.kind === "link" ? "Link" : "Update"
        onClicked: notice.kind === "setup" ? owner.runSetup() : notice.kind === "link" ? owner.runLink() : owner.runUpdate()
      }
    }
  }

  TextField {
    id: searchField
    width: parent.width
    foreground: owner.fg
    placeholderText: owner.showContacts ? "Search contacts and groups" : "Search conversations"
    verticalPadding: Style.space(5)
    onTextChanged: { owner.query = text; owner.cursor = 0 }
    ContextMenu.menu: null
    PasteGuard { id: searchPaste; target: searchField; cliPath: owner.cliPath || ""; singleLine: true }
    Keys.onPressed: function(event) {
      if (searchPaste.handleKey(event)) return
      var ctrl = event.modifiers & Qt.ControlModifier
      if (event.key === Qt.Key_Down || (ctrl && event.key === Qt.Key_N)) { owner.moveCursor(1); event.accepted = true }
      else if (event.key === Qt.Key_Up || (ctrl && event.key === Qt.Key_P)) { owner.moveCursor(-1); event.accepted = true }
      else if (event.key === Qt.Key_PageDown) { owner.moveCursor(8); event.accepted = true }
      else if (event.key === Qt.Key_PageUp) { owner.moveCursor(-8); event.accepted = true }
      else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { owner.activate(!!(event.modifiers & Qt.ControlModifier)); event.accepted = true }
      else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) { owner.toggleMode(); event.accepted = true }
      else if (event.key === Qt.Key_Escape) {
        if (searchField.text.length > 0) searchField.text = ""; else owner.close()
        event.accepted = true
      }
    }
  }

  // Section header with the switch between the two lists on its right.
  Item {
    width: parent.width
    implicitHeight: Math.max(sectionTitle.implicitHeight, modeRow.implicitHeight)
    PanelSectionHeader {
      id: sectionTitle
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      foreground: owner.fg
      fontFamily: owner.fontFamily
      text: (owner.showContacts ? "Contacts" : "Conversations") + (owner.rows.length > 0 ? "  " + (owner.cursor + 1) + " / " + owner.rows.length : "")
    }
    Row {
      id: modeRow
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)
      Button {
        text: "Chats"; foreground: owner.fg; fontSize: Style.font.caption
        verticalPadding: Style.space(3)
        selected: !owner.showContacts
        onClicked: if (owner.showContacts) owner.toggleMode()
      }
      Button {
        text: "Contacts"; foreground: owner.fg; fontSize: Style.font.caption
        verticalPadding: Style.space(3)
        selected: owner.showContacts
        onClicked: if (!owner.showContacts) owner.toggleMode()
      }
    }
  }

  Text {
    visible: owner.rows.length === 0
    width: parent.width
    topPadding: Style.space(6)
    bottomPadding: Style.space(6)
    text: owner.linked ? (owner.query ? "Nothing matches. Enter opens the terminal client."
                         : (owner.showContacts ? "No contacts yet." : "No conversations yet. Tab shows your contacts."))
          : owner.unlinked ? "Link this computer to see your conversations."
          : owner.connected ? "Connecting to Signal…" : "Signal is not running."
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: owner.dim
    font.family: owner.fontFamily
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
    model: owner.rows
    currentIndex: owner.cursor
    onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

    delegate: Rectangle {
      id: rowItem
      required property var modelData
      required property int index
      width: ListView.view.width
      implicitHeight: Math.max(rowAvatar.height, rowLabels.implicitHeight) + Style.space(12)
      radius: Style.cornerRadius
      readonly property bool current: index === owner.cursor
      color: current ? Style.selectedFillFor(owner.fg, Commons.Color.accent)
                     : (rowHover.containsMouse ? Style.hoverFillFor(owner.fg, Commons.Color.accent) : "transparent")
      Behavior on color { ColorAnimation { duration: owner.ms(90) } }

      MouseArea {
        id: rowHover
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPositionChanged: owner.cursor = rowItem.index
        onClicked: function(mouse) { owner.cursor = rowItem.index; owner.activate(mouse.button === Qt.RightButton) }
      }

      Avatar {
        id: rowAvatar
        anchors.left: parent.left
        anchors.leftMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        size: Style.space(30)
        name: rowItem.modelData.name
        foreground: owner.fg
        highlighted: rowItem.modelData.unread > 0
      }
      Column {
        id: rowLabels
        anchors.left: rowAvatar.right
        anchors.leftMargin: Style.space(10)
        anchors.right: rowMeta.left
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(2)
        Text {
          width: parent.width
          text: rowItem.modelData.name
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: owner.fg
          font.family: owner.fontFamily
          font.pixelSize: Style.font.body
          font.bold: rowItem.modelData.unread > 0
        }
        Text {
          width: parent.width
          visible: text.length > 0
          text: rowItem.modelData.typing ? "typing…" : rowItem.modelData.sub
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: rowItem.modelData.typing ? Commons.Color.accent : owner.dim
          font.family: owner.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
      Column {
        id: rowMeta
        anchors.right: parent.right
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(3)
        Text {
          anchors.right: parent.right
          visible: text.length > 0
          text: rowItem.modelData.open ? "open" : Model.relativeTime(rowItem.modelData.ts)
          textFormat: Text.PlainText
          color: rowItem.modelData.open ? Commons.Color.accent : owner.dim
          font.family: owner.fontFamily
          font.pixelSize: Style.font.caption
        }
        Rectangle {
          anchors.right: parent.right
          visible: rowItem.modelData.unread > 0
          width: Math.max(height, badgeText.implicitWidth + Style.space(8))
          height: badgeText.implicitHeight + Style.space(2)
          radius: height / 2
          color: Commons.Color.accent
          Text {
            id: badgeText
            anchors.centerIn: parent
            text: Model.unreadLabel(rowItem.modelData.unread)
            textFormat: Text.PlainText
            color: Commons.Color.background
            font.family: owner.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
        }
      }
    }
  }

  PanelSeparator { width: parent.width; foreground: owner.fg }

  // Footer: the keys worth knowing, as keycaps.
  Flow {
    width: parent.width
    spacing: Style.space(10)
    Repeater {
      model: [{ key: "↑↓", does: "move" }, { key: "Enter", does: "open" }, { key: "Ctrl+Enter", does: "terminal" },
              { key: "Tab", does: owner.showContacts ? "chats" : "contacts" }, { key: "Esc", does: owner.query ? "clear" : "close" }]
      delegate: Row {
        required property var modelData
        spacing: Style.space(5)
        KeyCap { label: modelData.key; foreground: owner.fg }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: modelData.does
          textFormat: Text.PlainText
          color: owner.dim
          font.family: owner.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
