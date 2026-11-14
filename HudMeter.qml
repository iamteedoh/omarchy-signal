// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD meter
// Date Created: 2026-11-13
// Description: A segmented bar lit by one or more shares of a whole.
import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Telemetry.js" as Telemetry

// A segmented bar: a row of cells, lit by one or more shares of a whole
// (sent against received, read against delivered). `parts` is
// [{ value, color }], drawn left to right; what is left stays dim.
Item {
  id: meter
  property var parts: []
  property real total: 0                        // 0 = the sum of the parts
  property int cells: 20
  property color track: Util.alpha(Commons.Color.popups.text, 0.1)

  implicitHeight: Math.max(4, Style.space(6))
  implicitWidth: Style.space(160)

  // Which colour each cell takes, worked out once per change, not per cell.
  readonly property var lit: {
    var list = Array.isArray(meter.parts) ? meter.parts : []
    var sum = 0
    for (var i = 0; i < list.length; i++) sum += Math.max(0, Number(list[i].value) || 0)
    var whole = meter.total > 0 ? meter.total : sum
    var out = []
    for (var p = 0; p < list.length && out.length < meter.cells; p++) {
      var n = Telemetry.cellsFor(list[p].value, whole, meter.cells)
      for (var c = 0; c < n && out.length < meter.cells; c++) out.push(list[p].color)
    }
    return out
  }

  Row {
    id: cellRow
    anchors.fill: parent
    readonly property real gap: Math.max(1, Style.space(2))
    spacing: gap
    Repeater {
      model: meter.cells
      delegate: Rectangle {
        required property int index
        width: Math.max(1, (cellRow.width - cellRow.gap * (meter.cells - 1)) / meter.cells)
        height: cellRow.height
        color: index < meter.lit.length ? meter.lit[index] : meter.track
      }
    }
  }
}
