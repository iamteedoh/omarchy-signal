// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD chip
// Date Created: 2026-11-13
// Description: A compact label and value reading for the telemetry strip and the bar panel.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// A reading small enough to sit in a row of them: the label dim, the value
// bright, a notch on the left in the colour of its state. The compact
// telemetry strip (a narrow window) and the bar panel are made of these.
Rectangle {
  id: chip
  property string label: ""
  property string value: ""
  property color foreground: Commons.Color.popups.text
  property color tone: Commons.Color.accent      // the notch and the value's colour
  property bool toned: false                     // colour the value too, not just the notch

  implicitWidth: chipRow.implicitWidth + Style.space(14)
  implicitHeight: chipRow.implicitHeight + Style.space(6)
  radius: Math.min(Style.cornerRadius, Style.space(3))
  color: Util.alpha(chip.foreground, 0.05)
  border.width: 1
  border.color: Util.alpha(chip.foreground, 0.14)

  Rectangle {
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: Math.max(2, Style.space(2)); height: parent.height - Style.space(6)
    color: chip.tone
  }
  Row {
    id: chipRow
    anchors.centerIn: parent
    anchors.horizontalCenterOffset: Style.space(1)
    spacing: Style.space(6)
    HudLabel {
      anchors.verticalCenter: parent.verticalCenter
      visible: chip.label.length > 0
      text: chip.label
      foreground: chip.foreground
    }
    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: chip.value.length > 0
      text: chip.value
      textFormat: Text.PlainText
      color: chip.toned ? chip.tone : chip.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }
}
