// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD module
// Date Created: 2026-11-13
// Description: A titled block of the telemetry rail.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// One block of the telemetry rail: a titled header with a lit marker and a
// hairline running to an optional reading on the right, then its rows.
Column {
  id: module
  property string title: ""
  property string aside: ""                     // a reading shown in the header itself
  property color foreground: Commons.Color.popups.text
  property color accent: Commons.Color.accent
  property color asideColor: module.accent
  default property alias rows: body.data

  spacing: Style.space(5)

  Item {
    width: parent.width
    implicitHeight: Math.max(titleText.implicitHeight, asideText.implicitHeight)
    Rectangle {
      id: marker
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(2, Style.space(3)); height: Style.space(9)
      color: module.accent
    }
    HudLabel {
      id: titleText
      anchors.left: marker.right
      anchors.leftMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
      text: module.title
      foreground: module.foreground
      strength: 0.9
      font.bold: true
    }
    Rectangle {
      anchors.left: titleText.right
      anchors.right: asideText.left
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: asideText.text.length > 0 ? Style.space(6) : 0
      anchors.verticalCenter: parent.verticalCenter
      height: 1
      color: Util.alpha(module.foreground, 0.14)
    }
    HudLabel {
      id: asideText
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: module.aside
      foreground: module.asideColor
      strength: 1
    }
  }

  Column {
    id: body
    width: parent.width
    spacing: Style.space(4)
  }
}
