// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD sparkline
// Date Created: 2026-11-13
// Description: A column graph of a short series of readings.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// A column graph of a short series (messages an hour, bridge round trips).
// One rectangle a value: it is redrawn only when the series itself changes.
Item {
  id: spark
  property var values: []
  property real peak: 0                         // 0 = the largest value in the series
  property color bar: Commons.Color.accent
  property color floor: Util.alpha(Commons.Color.popups.text, 0.14)
  property bool emphasiseLast: true             // the newest column at full strength
  // Room for this many columns, filled from the right. A series that is still
  // short (the first samples after the bridge starts) then draws as a few
  // narrow columns, not two half-width blocks. 0 = as many as there are values.
  property int slots: 0

  implicitHeight: Style.space(26)
  implicitWidth: Style.space(160)

  readonly property var series: Array.isArray(spark.values) ? spark.values : []
  readonly property real ceiling: {
    if (spark.peak > 0) return spark.peak
    var m = 0
    for (var i = 0; i < spark.series.length; i++) m = Math.max(m, Number(spark.series[i]) || 0)
    return m
  }

  Row {
    id: columns
    readonly property real gap: Math.max(1, Style.space(1))
    readonly property int count: Math.max(1, spark.slots, spark.series.length)
    readonly property real column: Math.max(1, (spark.width - gap * (count - 1)) / count)
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    spacing: gap
    Repeater {
      model: spark.series.length
      delegate: Item {
        required property int index
        readonly property real v: Math.max(0, Number(spark.series[index]) || 0)
        width: columns.column
        height: columns.height
        Rectangle {
          anchors.bottom: parent.bottom
          width: parent.width
          height: parent.v > 0 && spark.ceiling > 0 ? Math.max(2, Math.round(parent.v / spark.ceiling * parent.height)) : 1
          color: parent.v <= 0 ? spark.floor
               : spark.emphasiseLast && parent.index === spark.series.length - 1 ? spark.bar : Util.alpha(spark.bar, 0.62)
        }
      }
    }
  }
}
