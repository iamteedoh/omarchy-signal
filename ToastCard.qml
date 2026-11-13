// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// One popup card. Drawn like Omarchy's own notifications: the same surface
// colours and border, a 40px mark for the sender, the title in bold over a
// dimmer body, and the things the card can do as a bar along its bottom edge.
// It only draws and reports; Service.qml decides what a click means.
Item {
  id: card

  property string kind: "message"        // message | setup | updated | update
  property string title: ""
  property string body: ""
  property string convName: ""
  property int attachments: 0
  // Show the button bar as if the pointer were over the card (screenshots, tests).
  property bool forceHover: false

  signal deed(string id)                 // act | window | terminal
  signal dismissed()

  // Animation length in ms. Omarchy 4.5 added a duration helper that returns 0
  // while `omarchy toggle animations` has them off; older releases do not have
  // it, so it is looked up by name rather than relied on.
  function ms(d) {
    var f = Style["duration"]
    return typeof f === "function" ? f(d) : d
  }

  readonly property bool isMessage: card.kind === "message"
  readonly property bool hovered: cardHover.hovered || card.forceHover
  readonly property color text: Commons.Color.notifications.text
  readonly property color dim: Util.alpha(Commons.Color.notifications.text, 0.6)
  // The buttons open under the pointer; a notice about the plugin always shows its action.
  readonly property bool deedsShown: card.hovered || !card.isMessage
  readonly property var deeds: card.kind === "setup" ? [{ id: "act", label: "Finish setup" }]
                             : card.kind === "updated" ? [{ id: "act", label: "Restart shell" }, { id: "dismiss", label: "Later" }]
                             : card.kind === "update" ? [{ id: "act", label: "Update" }, { id: "dismiss", label: "Later" }]
                             : [{ id: "act", label: "Reply" }, { id: "window", label: "Open chat" }, { id: "terminal", label: "Terminal" }]

  implicitWidth: Style.space(380)
  implicitHeight: plate.height
  height: plate.height

  HoverHandler { id: cardHover }

  BorderSurface {
    id: plate
    width: parent.width
    height: cardBody.implicitHeight + Style.space(20) + plate.borderTop + plate.borderBottom + deedBar.height
    radius: Style.cornerRadius
    color: Commons.Color.notifications.background
    borderSpec: Border.surfaceSpec("notifications", "border", Commons.Color.notifications.border, Math.max(1, Style.space(2)))
    clip: true

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
      cursorShape: Qt.PointingHandCursor
      onClicked: function(mouse) {
        if (mouse.button === Qt.RightButton) card.dismissed()
        else if (mouse.button === Qt.MiddleButton && card.isMessage) card.deed("terminal")
        else card.deed("act")
      }
    }

    RowLayout {
      id: cardBody
      anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
      anchors.leftMargin: plate.borderLeft + Style.space(12)
      anchors.rightMargin: plate.borderRight + Style.space(12)
      anchors.topMargin: plate.borderTop + Style.space(10)
      spacing: Style.space(12)

      Avatar {
        Layout.alignment: Qt.AlignTop
        visible: card.isMessage
        name: card.convName || card.title
        foreground: card.text
      }
      Rectangle {
        Layout.alignment: Qt.AlignTop
        visible: !card.isMessage
        implicitWidth: Style.space(40); implicitHeight: Style.space(40)
        radius: Style.cornerRadius
        color: Util.alpha(Commons.Color.accent, 0.18)
        Text {
          anchors.centerIn: parent
          text: "󰭹"
          textFormat: Text.PlainText
          color: Commons.Color.accent
          font.family: Style.font.family
          font.pixelSize: Style.font.iconLarge
        }
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(3)
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(8)
          Text {
            Layout.fillWidth: true
            text: card.title
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: card.text
            font.family: "Liberation Sans"
            font.pixelSize: Style.font.title
            font.bold: true
          }
          // The source and the close mark share one spot and crossfade, so nothing in the headline moves.
          Item {
            implicitWidth: Math.max(sourceText.implicitWidth, closeMark.implicitWidth + Style.space(6))
            implicitHeight: Math.max(sourceText.implicitHeight, closeMark.implicitHeight)
            Text {
              id: sourceText
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Signal"
              textFormat: Text.PlainText
              color: card.dim
              opacity: card.hovered ? 0 : 1
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              Behavior on opacity { NumberAnimation { duration: card.ms(150) } }
            }
            Text {
              id: closeMark
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "✕"
              textFormat: Text.PlainText
              color: closeArea.containsMouse ? card.text : card.dim
              opacity: card.hovered ? 1 : 0
              font.family: Style.font.family
              font.pixelSize: Style.font.subtitle
              Behavior on opacity { NumberAnimation { duration: card.ms(150) } }
              MouseArea {
                id: closeArea
                anchors.fill: parent; anchors.margins: -Style.space(5)
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: card.dismissed()
              }
            }
          }
        }
        Text {
          Layout.fillWidth: true
          text: card.body + (card.attachments > 0 ? "  󰁦 " + card.attachments : "")
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          maximumLineCount: card.hovered || !card.isMessage ? 8 : 2
          elide: Text.ElideRight
          color: Util.alpha(Commons.Color.notifications.text, 0.85)
          font.family: "Liberation Sans"
          font.pixelSize: Style.font.title
        }
      }
    }

    // What the card can do: equal segments along the bottom edge, split
    // by hairlines, the way Omarchy's notification buttons are drawn.
    Item {
      id: deedBar
      readonly property real full: Style.spacing.controlHeight + Style.space(6) + 1
      anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
      anchors.leftMargin: plate.borderLeft; anchors.rightMargin: plate.borderRight; anchors.bottomMargin: plate.borderBottom
      height: card.deedsShown ? full : 0
      clip: true
      Behavior on height { NumberAnimation { duration: card.ms(150); easing.type: Easing.OutCubic } }

      Row {
        anchors.bottom: parent.bottom
        width: parent.width
        height: deedBar.full
        Repeater {
          model: card.deeds
          delegate: Item {
            id: segment
            required property var modelData
            required property int index
            width: Math.round(deedBar.width / card.deeds.length)
            height: deedBar.full
            Rectangle {
              anchors.fill: parent
              anchors.topMargin: 1
              anchors.leftMargin: segment.index > 0 ? 1 : 0
              color: deedArea.containsMouse ? Style.hoverFillFor(Commons.Color.notifications.text, Commons.Color.accent) : "transparent"
              Behavior on color { ColorAnimation { duration: card.ms(120) } }
            }
            Rectangle { width: parent.width; height: 1; color: Util.alpha(Commons.Color.notifications.text, 0.18) }
            Rectangle { visible: segment.index > 0; width: 1; height: parent.height; color: Util.alpha(Commons.Color.notifications.text, 0.18) }
            Text {
              anchors.centerIn: parent
              text: segment.modelData.label
              textFormat: Text.PlainText
              color: deedArea.containsMouse ? card.text : Util.alpha(Commons.Color.notifications.text, 0.85)
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            MouseArea {
              id: deedArea
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: segment.modelData.id === "dismiss" ? card.dismissed() : card.deed(segment.modelData.id)
            }
          }
        }
      }
    }
  }
}
