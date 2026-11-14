// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal telemetry strip
// Date Created: 2026-11-13
// Description: Draws the telemetry as a row of chips when the window is too narrow for the rail.
import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model
import "Telemetry.js" as Telemetry

// The telemetry of a conversation in one wrapping row of chips, for a window
// too narrow to give the rail (TelemetryRail.qml) a column of its own. The same
// readings, the ones worth keeping when there is room for six.
//
// `hud` is the ConversationView; this draws what it holds and asks for nothing.
Flow {
  id: strip
  required property var hud

  readonly property var stats: hud.stats
  readonly property bool online: hud.connected && hud.linked

  spacing: Style.space(6)

  HudChip {
    foreground: strip.hud.fg
    label: "Link"
    value: strip.online && strip.hud.rttMs >= 0 ? Telemetry.latencyLabel(strip.hud.rttMs) : Telemetry.linkState(strip.hud.connected, strip.hud.linked)
    tone: strip.online && Telemetry.latencyLevel(strip.hud.rttMs) !== "slow" ? Commons.Color.accent : Commons.Color.urgent
    toned: !strip.online
  }
  HudChip {
    foreground: strip.hud.fg
    label: "E2E"
    value: strip.hud.isGroup || strip.hud.noteToSelf || strip.hud.trust.label === "" ? "Signal" : strip.hud.trust.label
    tone: strip.hud.trust.level === "bad" ? Commons.Color.urgent : Commons.Color.accent
    toned: strip.hud.trust.level === "bad"
  }
  HudChip {
    foreground: strip.hud.fg
    visible: strip.hud.expiration > 0
    label: "Disappear"
    value: Model.timerLabel(strip.hud.expiration)
  }
  HudChip {
    foreground: strip.hud.fg
    label: "Tx / Rx"
    value: strip.stats.tx + " / " + strip.stats.rx
  }
  HudChip {
    foreground: strip.hud.fg
    visible: strip.stats.tx > 0
    label: strip.stats.delivery.failed > 0 ? "Failed" : "Read"
    value: strip.stats.delivery.failed > 0 ? String(strip.stats.delivery.failed) : strip.stats.delivery.read + "/" + strip.stats.tx
    tone: strip.stats.delivery.failed > 0 ? Commons.Color.urgent : Commons.Color.accent
    toned: strip.stats.delivery.failed > 0
  }
  HudChip {
    foreground: strip.hud.fg
    visible: strip.stats.lastRx > 0 && !strip.hud.noteToSelf
    label: "Last in"
    value: Telemetry.shortDuration(Math.max(0, strip.hud.now - strip.stats.lastRx))
  }
  HudChip {
    foreground: strip.hud.fg
    visible: strip.stats.replyTheirs >= 0 && !strip.hud.noteToSelf
    label: "Answers in"
    value: "~" + Telemetry.shortDuration(strip.stats.replyTheirs)
  }
  HudChip {
    foreground: strip.hud.fg
    visible: strip.hud.totalUnread > 0
    label: "Unread"
    value: String(strip.hud.totalUnread)
    toned: true
  }
}
