// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import qs.Commons
import qs.Commons as Commons

// A key, drawn as a key: the legend under the composer and the shortcut list
// name keys with these rather than in a run of text.
Rectangle {
  id: cap
  property string label: ""
  property color foreground: Commons.Color.popups.text

  implicitWidth: capText.implicitWidth + Style.space(10)
  implicitHeight: capText.implicitHeight + Style.space(4)
  radius: Math.min(Style.cornerRadius, Style.space(4))
  color: Style.normalFillFor(cap.foreground, Commons.Color.accent)
  border.width: 1
  border.color: Style.normalBorderFor(cap.foreground, Commons.Color.accent)

  Text {
    id: capText
    anchors.centerIn: parent
    text: cap.label
    textFormat: Text.PlainText
    color: Qt.darker(cap.foreground, 1.15)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }
}
