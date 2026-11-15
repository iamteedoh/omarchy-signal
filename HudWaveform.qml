// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal waveform
// Date Created: 2026-11-14
// Description: One side of a call as a burst of thin bars that swells and shrinks in place with the voice.
import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Calls.js" as Calls

// One side of a call as a burst of thin bars around a centre line. It does
// not scroll: the bars stay put, and the whole burst grows taller and reaches
// further out to the sides the louder that side is, then falls back to a row
// of dots in a pause. The heights come from Calls.burst, so the terminal
// client draws the same shape. The bars exist once and only their heights
// change, eased so the burst moves instead of flickering.
Item {
  id: wave
  property var levels: []                       // 0 to 1, oldest first; the newest drive the burst
  property color bar: Commons.Color.accent
  property color foreground: Commons.Color.popups.text
  property bool live: true                      // false dims it: muted, or not connected yet

  implicitHeight: Style.space(54)
  implicitWidth: Style.space(220)

  readonly property real column: Math.max(1, Style.space(2))
  readonly property real gap: Math.max(1, Style.space(2))
  readonly property int slots: Math.max(12, Math.floor((wave.width + wave.gap) / (wave.column + wave.gap)))
  readonly property real inset: (wave.width - (wave.slots * (wave.column + wave.gap) - wave.gap)) / 2
  readonly property var heights: Calls.burst(wave.levels, wave.slots)

  Repeater {
    model: wave.slots
    delegate: Rectangle {
      required property int index
      readonly property real v: Number(wave.heights[index]) || 0
      x: wave.inset + index * (wave.column + wave.gap)
      width: wave.column
      height: Math.max(wave.column, v * wave.height)
      y: (wave.height - height) / 2
      radius: width / 2
      antialiasing: true
      color: v <= 0.02 ? Util.alpha(wave.foreground, 0.22) : Util.alpha(wave.bar, wave.live ? 0.95 : 0.4)
      Behavior on height { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
    }
  }
}
