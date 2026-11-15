// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal call panel
// Date Created: 2026-11-14
// Description: The in-call panel of the chat window: state, controls, telemetry, devices and a waveform for each side.
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Calls.js" as Calls
import "Model.js" as Model

// A voice call, drawn where the conversation is. Top: who and what state, and
// the controls. Middle: the call's telemetry as chips. Bottom: two waveforms
// side by side, what you send on the left and what arrives on the right.
//
// It only shows what the bridge reports ("call" and "callLevels" events) and
// asks for things through signals; the owner talks to the bridge.
Item {
  id: panel
  property var call: null                       // the bridge's call record
  property var tx: []                           // levels being sent, oldest first
  property var rx: []                           // levels arriving
  property real now: 0                          // ms; the owner's clock, so every reading ticks together
  property var devices: null                    // callDevices result, once asked for
  property bool devicesOpen: false
  property color fg: Commons.Color.popups.text
  property bool telemetry: true

  signal accept()
  signal decline()
  signal hangup()
  signal mute(bool muted)
  signal pickDevice(string kind, string name)
  signal wantDevices()

  readonly property bool live: Calls.isLive(panel.call)
  readonly property bool ringingIn: Calls.isRingingIn(panel.call)
  readonly property bool talking: !!panel.call && (panel.call.state === "connected" || panel.call.state === "reconnecting")
  readonly property bool alarm: !!panel.call && (panel.call.state === "reconnecting" || (panel.call.state === "ended" && !panel.call.connectedAt))
  readonly property color tone: panel.alarm ? Commons.Color.urgent : Commons.Color.accent
  readonly property var rows: panel.call ? Calls.readings(panel.call, panel.now) : []
  readonly property var levels: (panel.call && panel.call.levels) ? panel.call.levels : ({})

  implicitHeight: body.implicitHeight + Style.space(20)

  Rectangle {
    anchors.fill: parent
    radius: Math.min(Style.cornerRadius, Style.space(4))
    color: Util.alpha(panel.fg, 0.04)
    border.width: 1
    border.color: Util.alpha(panel.tone, 0.45)
  }
  HudFrame { anchors.fill: parent; arm: Style.space(12); color: panel.tone }

  ColumnLayout {
    id: body
    anchors.fill: parent
    anchors.margins: Style.space(10)
    spacing: Style.space(8)

    // Who, what state, and the controls.
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(10)
      Rectangle {
        implicitWidth: Math.max(6, Style.space(9)); implicitHeight: implicitWidth
        radius: Math.min(Style.cornerRadius, implicitWidth / 2)
        color: panel.tone
        // Breathes while it rings or reconnects; still once connected.
        SequentialAnimation on opacity {
          running: panel.live && !!panel.call && panel.call.state !== "connected"
          loops: Animation.Infinite
          alwaysRunToEnd: true
          NumberAnimation { to: 0.25; duration: 520 }
          NumberAnimation { to: 1; duration: 520 }
        }
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(2)
        Text {
          objectName: "callState"
          Layout.fillWidth: true
          text: {
            if (!panel.call) return ""
            var label = Calls.stateLabel(panel.call).toUpperCase()
            var time = panel.call.connectedAt ? Calls.clock(Calls.durationMs(panel.call, panel.now)) : ""
            return time ? label + "  " + time : label
          }
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: panel.tone
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
          font.letterSpacing: Math.max(0.5, Style.font.title * 0.08)
        }
        HudLabel {
          Layout.fillWidth: true
          elide: Text.ElideRight
          foreground: panel.fg
          strength: 0.6
          text: {
            if (!panel.call) return ""
            var who = Model.singleLine(panel.call.name || "", 60)
            var kind = panel.call.media === "video" ? "Video call, voice only on this computer" : "Voice call"
            return kind + (who ? " · " + who : "") + " · End-to-end encrypted"
          }
        }
      }
      Button {
        objectName: "callAccept"
        visible: panel.ringingIn
        iconText: "\u{F03F2}"
        text: "Answer"
        tooltipText: "Answer (Ctrl+Shift+A)"
        onClicked: panel.accept()
      }
      Button {
        objectName: "callDecline"
        visible: panel.ringingIn
        iconText: "\u{F03F5}"
        text: "Decline"
        tooltipText: "Decline (Ctrl+Shift+H)"
        onClicked: panel.decline()
      }
      Button {
        objectName: "callMute"
        visible: panel.live && !panel.ringingIn
        iconText: panel.call && panel.call.muted ? "\u{F036D}" : "\u{F036C}"
        selected: !!panel.call && panel.call.muted === true
        tooltipText: panel.call && panel.call.muted ? "Unmute your microphone (Ctrl+Shift+M)" : "Mute your microphone (Ctrl+Shift+M)"
        onClicked: panel.mute(!(panel.call && panel.call.muted))
      }
      Button {
        objectName: "callDevices"
        visible: panel.live
        iconText: "\u{F02CB}"
        selected: panel.devicesOpen
        tooltipText: "Microphone and speaker"
        onClicked: { panel.devicesOpen = !panel.devicesOpen; if (panel.devicesOpen) panel.wantDevices() }
      }
      Button {
        objectName: "callHangup"
        visible: panel.live && !panel.ringingIn
        iconText: "\u{F03F5}"
        text: panel.talking ? "Hang up" : "Cancel"
        tooltipText: "Hang up (Ctrl+Shift+H)"
        onClicked: panel.hangup()
      }
    }

    // Telemetry: everything the call is known to be doing, as chips.
    Flow {
      Layout.fillWidth: true
      visible: panel.telemetry && panel.rows.length > 0
      spacing: Style.space(5)
      Repeater {
        model: panel.telemetry ? panel.rows : []
        delegate: HudChip {
          required property var modelData
          label: modelData[0]
          value: modelData[1]
          foreground: panel.fg
          tone: modelData[2] === "alarm" ? Commons.Color.urgent : Commons.Color.accent
          toned: modelData[2] !== ""
        }
      }
    }

    // Microphone and speaker: what the system uses, and a different choice for calls.
    ColumnLayout {
      Layout.fillWidth: true
      visible: panel.devicesOpen && panel.live
      spacing: Style.space(6)
      Repeater {
        model: [{ kind: "input", title: "MICROPHONE", list: "inputs", chosen: "input", missing: "inputMissing" },
                { kind: "output", title: "SPEAKER", list: "outputs", chosen: "output", missing: "outputMissing" }]
        delegate: ColumnLayout {
          id: group
          required property var modelData
          readonly property var items: panel.devices && Array.isArray(panel.devices[modelData.list]) ? panel.devices[modelData.list] : []
          readonly property string chosen: panel.devices ? String(panel.devices[modelData.chosen] || "") : ""
          Layout.fillWidth: true
          spacing: Style.space(3)
          HudLabel {
            foreground: panel.fg
            strength: 0.9
            font.bold: true
            text: group.modelData.title + (panel.devices && panel.devices[group.modelData.missing] === true ? "  ·  your choice is unplugged, using the system default" : "")
          }
          HudLabel {
            visible: !panel.devices
            foreground: panel.fg
            strength: 0.5
            text: "Looking for devices…"
          }
          Flow {
            Layout.fillWidth: true
            spacing: Style.space(5)
            Repeater {
              // "" is the system default, followed at the moment a call connects.
              model: panel.devices ? [{ name: "", description: "System default", default: false }].concat(group.items) : []
              delegate: Rectangle {
                id: pick
                required property var modelData
                readonly property bool on: group.chosen === modelData.name
                implicitWidth: pickText.implicitWidth + Style.space(16)
                implicitHeight: pickText.implicitHeight + Style.space(8)
                radius: Math.min(Style.cornerRadius, Style.space(3))
                color: pick.on ? Util.alpha(Commons.Color.accent, 0.22) : pickArea.containsMouse ? Util.alpha(panel.fg, 0.1) : Util.alpha(panel.fg, 0.05)
                border.width: 1
                border.color: pick.on ? Commons.Color.accent : Util.alpha(panel.fg, 0.14)
                Text {
                  id: pickText
                  anchors.centerIn: parent
                  text: Model.singleLine(pick.modelData.description || pick.modelData.name, 48) + (pick.modelData.default === true ? "  (default)" : "")
                  textFormat: Text.PlainText
                  color: panel.fg
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: pick.on
                }
                MouseArea {
                  id: pickArea
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: panel.pickDevice(group.modelData.kind, pick.modelData.name)
                }
              }
            }
          }
        }
      }
    }

    // The two sides, side by side: you on the left, them on the right.
    RowLayout {
      Layout.fillWidth: true
      visible: panel.live
      spacing: Style.space(14)
      Repeater {
        model: [{ side: "tx", title: "YOU · TX" }, { side: "rx", title: "THEM · RX" }]
        delegate: ColumnLayout {
          id: lane
          required property var modelData
          readonly property bool mine: modelData.side === "tx"
          readonly property real level: Number(panel.levels[modelData.side]) || 0
          readonly property real peak: Number(panel.levels[modelData.side + "Peak"]) || 0
          Layout.fillWidth: true
          Layout.preferredWidth: 1
          spacing: Style.space(4)
          RowLayout {
            Layout.fillWidth: true
            HudLabel {
              text: lane.modelData.title + (lane.mine && panel.call && panel.call.muted ? "  ·  MUTED" : "")
              foreground: lane.mine && panel.call && panel.call.muted ? Commons.Color.urgent : panel.fg
              strength: 0.9
              font.bold: true
            }
            Item { Layout.fillWidth: true }
            HudLabel {
              visible: panel.talking
              foreground: panel.fg
              strength: 0.6
              text: "LVL " + Math.round(lane.level * 100) + "  PK " + Math.round(lane.peak * 100)
            }
          }
          Item {
            Layout.fillWidth: true
            implicitHeight: Style.space(58)
            HudWaveform {
              objectName: lane.mine ? "waveTx" : "waveRx"
              anchors.fill: parent
              anchors.margins: Style.space(4)
              levels: lane.mine ? panel.tx : panel.rx
              foreground: panel.fg
              bar: lane.mine ? Commons.Color.accent : Commons.Color.popups.text
              live: panel.talking && !(lane.mine && panel.call && panel.call.muted)
            }
            HudFrame { anchors.fill: parent; arm: Style.space(7); weight: 1; color: Util.alpha(panel.fg, 0.4) }
          }
          HudLabel {
            Layout.fillWidth: true
            elide: Text.ElideMiddle
            foreground: panel.fg
            strength: 0.45
            text: {
              if (!panel.call) return ""
              var device = lane.mine ? panel.call.mic : panel.call.speaker
              if (panel.call.audioError) return "AUDIO: " + Model.singleLine(panel.call.audioError, 80)
              return device ? (lane.mine ? "MIC " : "OUT ") + device : (panel.talking ? "" : "audio connects when the call does")
            }
          }
        }
      }
    }
  }
}
