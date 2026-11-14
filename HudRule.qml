// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD rule
// Date Created: 2026-11-13
// Description: A divider with a lit leading segment, tick marks and a one-shot spark.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// A divider with a lit leading segment and a run of tick marks at its far
// end, in place of a bare hairline. `pulse` runs a spark along it once (a
// message arriving, a conversation loading); at rest nothing moves.
Item {
  id: rule
  property color foreground: Commons.Color.popups.text
  property color accent: Commons.Color.accent
  property real lead: Style.space(56)           // length of the lit segment
  property int ticks: 6
  property int pulseMs: 0                       // 0 = animations are off

  implicitHeight: Math.max(3, Style.space(5))

  function pulse() {
    if (rule.pulseMs > 0 && rule.width > 0) spark.restart()
  }

  Rectangle {
    anchors.left: parent.left; anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    height: 1
    color: Util.alpha(rule.foreground, 0.14)
  }
  Rectangle {
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(rule.lead, rule.width)
    height: Math.max(1, Style.space(2))
    color: rule.accent
  }
  Row {
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    spacing: Style.space(3)
    Repeater {
      model: rule.ticks
      delegate: Rectangle {
        required property int index
        width: Math.max(1, Style.space(2))
        height: rule.implicitHeight
        color: Util.alpha(rule.accent, 0.25 + 0.6 * (index + 1) / rule.ticks)
      }
    }
  }
  Rectangle {
    id: sparkDot
    anchors.verticalCenter: parent.verticalCenter
    width: Style.space(40)
    height: Math.max(1, Style.space(2))
    opacity: 0
    gradient: Gradient {
      orientation: Gradient.Horizontal
      GradientStop { position: 0.0; color: Util.alpha(rule.accent, 0) }
      GradientStop { position: 1.0; color: rule.accent }
    }
  }
  ParallelAnimation {
    id: spark
    NumberAnimation { target: sparkDot; property: "x"; from: 0; to: Math.max(0, rule.width - sparkDot.width); duration: rule.pulseMs; easing.type: Easing.InOutCubic }
    SequentialAnimation {
      NumberAnimation { target: sparkDot; property: "opacity"; from: 0; to: 1; duration: rule.pulseMs * 0.2 }
      PauseAnimation { duration: rule.pulseMs * 0.5 }
      NumberAnimation { target: sparkDot; property: "opacity"; to: 0; duration: rule.pulseMs * 0.3 }
    }
  }
}
