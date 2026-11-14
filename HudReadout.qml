// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD readout
// Date Created: 2026-11-13
// Description: One label and value line of a telemetry module.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// One line of a telemetry module: what is measured on the left, the reading
// on the right. A reading that is not known yet shows as two hyphens, never as a
// made-up number.
Item {
  id: readout
  property string label: ""
  property string value: ""
  property color foreground: Commons.Color.popups.text
  property color valueColor: readout.foreground
  property bool strong: false

  implicitHeight: Math.max(key.implicitHeight, reading.implicitHeight)
  implicitWidth: key.implicitWidth + reading.implicitWidth + Style.space(12)

  HudLabel {
    id: key
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    text: readout.label
    foreground: readout.foreground
  }
  Text {
    id: reading
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(implicitWidth, Math.max(0, readout.width - key.width - Style.space(8)))
    horizontalAlignment: Text.AlignRight
    text: readout.value.length > 0 ? readout.value : "--"
    textFormat: Text.PlainText
    elide: Text.ElideRight
    color: readout.value.length > 0 ? readout.valueColor : Util.alpha(readout.foreground, 0.38)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    font.bold: readout.strong
  }
}
