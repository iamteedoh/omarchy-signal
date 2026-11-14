// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal HUD label
// Date Created: 2026-11-13
// Description: The small spaced capitals that label a reading.
import QtQuick
import qs.Commons
import qs.Commons as Commons

// The small spaced capitals every readout is labelled with. Still the theme's
// own font and colours; only the casing and the tracking are ours.
Text {
  id: label
  property color foreground: Commons.Color.popups.text
  property real strength: 0.55                 // how much of the foreground it takes
  textFormat: Text.PlainText
  color: Util.alpha(label.foreground, label.strength)
  font.family: Style.font.family
  font.pixelSize: Style.font.caption
  font.capitalization: Font.AllUppercase
  font.letterSpacing: Math.max(0.5, Style.font.caption * 0.09)
}
