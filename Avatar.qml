// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// The tile that stands for a conversation: its first letter on the theme's
// quiet fill, the way Omarchy's own notifications mark a source without an
// icon. An unread chat gets the accent instead. Two corner ticks mark it as a
// target; `ticks: false` where something else already frames it.
Rectangle {
  id: tile
  property string name: ""
  property bool highlighted: false
  property color foreground: Commons.Color.popups.text
  property real size: Style.space(40)
  property bool ticks: true

  implicitWidth: size
  implicitHeight: size
  width: size
  height: size
  radius: Style.cornerRadius
  color: tile.highlighted ? Util.alpha(Commons.Color.accent, 0.22) : Style.normalFillFor(tile.foreground, Commons.Color.accent)

  Text {
    anchors.centerIn: parent
    text: Model.initial(tile.name)
    textFormat: Text.PlainText
    color: tile.highlighted ? tile.foreground : Qt.darker(tile.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Math.max(Style.font.caption, Math.round(tile.size * 0.36))
    font.weight: Font.DemiBold
  }

  readonly property real tick: Math.max(3, Math.round(tile.size * 0.2))
  readonly property color tickColor: Util.alpha(Commons.Color.accent, tile.highlighted ? 1 : 0.6)
  Rectangle { visible: tile.ticks; width: tile.tick; height: 1; color: tile.tickColor }
  Rectangle { visible: tile.ticks; width: 1; height: tile.tick; color: tile.tickColor }
  Rectangle { visible: tile.ticks; x: tile.width - tile.tick; y: tile.height - 1; width: tile.tick; height: 1; color: tile.tickColor }
  Rectangle { visible: tile.ticks; x: tile.width - 1; y: tile.height - tile.tick; width: 1; height: tile.tick; color: tile.tickColor }
}
