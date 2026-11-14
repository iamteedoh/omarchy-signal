// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD frame
// Date Created: 2026-11-13
// Description: Corner brackets drawn around a window, a popup or a picture.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// Four corner brackets around whatever it fills: the targeting-reticle frame
// the chat window, the popups and the pictures share. Drawn with eight plain
// rectangles (no canvas, no shader), so it costs nothing while it sits still,
// and it takes no input: clicks go to what is underneath.
Item {
  id: frame
  property color color: Commons.Color.accent
  property real arm: Style.space(12)                    // length of each bracket leg
  property real weight: Math.max(1, Style.space(2))     // its thickness
  // 0 hides the brackets, 1 shows them: animate it to make a frame "lock on".
  property real lock: 1

  enabled: false
  opacity: frame.lock
  // Unlocked brackets sit a little outside and close in.
  readonly property real slack: (1 - frame.lock) * Style.space(8)

  Rectangle { x: -frame.slack; y: -frame.slack; width: frame.arm; height: frame.weight; color: frame.color }
  Rectangle { x: -frame.slack; y: -frame.slack; width: frame.weight; height: frame.arm; color: frame.color }
  Rectangle { x: frame.width - frame.arm + frame.slack; y: -frame.slack; width: frame.arm; height: frame.weight; color: frame.color }
  Rectangle { x: frame.width - frame.weight + frame.slack; y: -frame.slack; width: frame.weight; height: frame.arm; color: frame.color }
  Rectangle { x: -frame.slack; y: frame.height - frame.weight + frame.slack; width: frame.arm; height: frame.weight; color: frame.color }
  Rectangle { x: -frame.slack; y: frame.height - frame.arm + frame.slack; width: frame.weight; height: frame.arm; color: frame.color }
  Rectangle { x: frame.width - frame.arm + frame.slack; y: frame.height - frame.weight + frame.slack; width: frame.arm; height: frame.weight; color: frame.color }
  Rectangle { x: frame.width - frame.weight + frame.slack; y: frame.height - frame.arm + frame.slack; width: frame.weight; height: frame.arm; color: frame.color }
}
