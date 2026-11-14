// SPDX-License-Identifier: GPL-3.0-or-later
// Author: Tito Valentin
// Name of Program: omarchy-signal telemetry rail
// Date Created: 2026-11-13
// Description: Draws the telemetry modules beside a conversation in the chat window.
import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model
import "Telemetry.js" as Telemetry

// The telemetry beside a conversation: the link to the bridge, the channel's
// security, the traffic in both directions, how far your messages got, how
// quickly each side answers, and what is waiting in the composer.
//
// It only draws. `hud` is the ConversationView, which owns every number shown
// here; nothing in this file asks the bridge for anything. Readings come from
// data the view already holds, so the rail costs one pass over the thread per
// change and a clock tick a second for the running counters. Every string is
// PlainText, and names are cleaned by Model.js before they get here.
Flickable {
  id: rail
  required property var hud

  readonly property color fg: hud.fg
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: Commons.Color.urgent
  readonly property color quiet: Util.alpha(hud.fg, 0.5)
  readonly property var stats: hud.stats
  readonly property bool online: hud.connected && hud.linked

  implicitWidth: Style.space(212)
  contentWidth: width
  contentHeight: modules.implicitHeight
  clip: true
  boundsBehavior: Flickable.StopAtBounds
  interactive: contentHeight > height

  MouseArea {
    anchors.fill: parent
    z: 10
    acceptedButtons: Qt.NoButton
    onWheel: function(ev) { if (rail.interactive) rail.hud.scrollBy(rail, ev); else ev.accepted = false }
  }

  Column {
    id: modules
    width: rail.width
    spacing: Style.space(11)

    // ---- the connection to the bridge
    HudModule {
      width: parent.width
      title: "Link"
      foreground: rail.fg
      aside: Telemetry.linkState(rail.hud.connected, rail.hud.linked)
      asideColor: rail.online ? rail.accent : rail.urgent
      HudSparkline {
        width: parent.width
        implicitHeight: Style.space(18)
        visible: rail.hud.rttSamples.length > 1
        values: rail.hud.rttSamples
        slots: 24                       // ConversationView keeps the last 24
        bar: Telemetry.latencyLevel(rail.hud.rttMs) === "slow" ? rail.urgent : rail.accent
        floor: Util.alpha(rail.fg, 0.14)
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Round trip"
        value: rail.hud.connected ? Telemetry.latencyLabel(rail.hud.rttMs) : ""
        valueColor: Telemetry.latencyLevel(rail.hud.rttMs) === "slow" ? rail.urgent : rail.fg
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Bridge " + rail.hud.bridgeVersion
        value: rail.hud.connected && rail.hud.bridgeUpSince > 0 ? "up " + Telemetry.shortDuration(rail.hud.now - rail.hud.bridgeUpSince) : ""
      }
    }

    // ---- who this is and how it is protected
    HudModule {
      width: parent.width
      title: "Channel"
      foreground: rail.fg
      aside: rail.hud.noteToSelf ? "Note" : rail.hud.isGroup ? "Group" : "Direct"
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Cipher"
        value: "Signal E2E"
        visible: rail.hud.isGroup || rail.hud.noteToSelf
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: !rail.hud.isGroup && !rail.hud.noteToSelf
        label: "Identity"
        value: rail.hud.trust.label
        valueColor: rail.hud.trust.level === "good" ? rail.accent : rail.hud.trust.level === "bad" ? rail.urgent : rail.fg
        strong: rail.hud.trust.level !== "neutral"
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: !rail.hud.isGroup && !rail.hud.noteToSelf && rail.hud.trust.ageMs >= 0
        // The key changed while you were talking: say when. A change this week
        // is worth a second look; an old one is history.
        readonly property bool changed: Telemetry.keyChanged(rail.hud.trust, rail.stats.firstTs)
        readonly property bool recent: changed && rail.hud.trust.ageMs < 7 * 86400000
        label: changed ? "Key changed" : "Key age"
        value: Telemetry.shortDuration(rail.hud.trust.ageMs) + (changed ? " ago" : "")
        valueColor: recent ? rail.urgent : rail.fg
        strong: recent
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.hud.isGroup
        label: "Members"
        value: rail.hud.members > 0 ? String(rail.hud.members) : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Disappear"
        value: rail.hud.expiration > 0 ? Model.timerLabel(rail.hud.expiration) : "Off"
        valueColor: rail.hud.expiration > 0 ? rail.accent : rail.fg
      }
    }

    // ---- messages in each direction, and when
    HudModule {
      width: parent.width
      title: "Traffic"
      foreground: rail.fg
      aside: rail.stats.total > 0 ? rail.stats.total + " msg" : ""
      Item {
        width: parent.width
        implicitHeight: Math.max(txText.implicitHeight, rxText.implicitHeight)
        HudLabel { id: txText; anchors.left: parent.left; text: "Tx " + rail.stats.tx; foreground: rail.accent; strength: 1; font.bold: true }
        HudLabel { id: rxText; anchors.right: parent.right; text: rail.stats.rx + " Rx"; foreground: rail.fg; strength: 0.85; font.bold: true }
      }
      HudMeter {
        width: parent.width
        parts: [{ value: rail.stats.tx, color: rail.accent }, { value: rail.stats.rx, color: Util.alpha(rail.fg, 0.5) }]
        track: Util.alpha(rail.fg, 0.1)
      }
      HudSparkline {
        width: parent.width
        values: rail.stats.hours
        floor: Util.alpha(rail.fg, 0.14)
      }
      Item {
        width: parent.width
        implicitHeight: agoText.implicitHeight
        HudLabel { id: agoText; anchors.left: parent.left; text: "-24h"; foreground: rail.fg; strength: 0.38 }
        HudLabel { anchors.right: parent.right; text: "Now"; foreground: rail.fg; strength: 0.38 }
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Today"
        value: rail.stats.today + (rail.stats.peakHour >= 0 ? " · peak " + Telemetry.hourLabel(rail.stats.peakHour) : "")
      }
      // In a group: who is carrying the conversation.
      Repeater {
        model: rail.hud.isGroup ? rail.stats.senders : []
        delegate: HudReadout {
          required property var modelData
          width: modules.width
          foreground: rail.fg
          label: Model.singleLine(modelData.name, 18)
          value: String(modelData.count)
        }
      }
    }

    // ---- how far your own messages got
    HudModule {
      width: parent.width
      title: "Delivery"
      foreground: rail.fg
      aside: Telemetry.deliveryWord(rail.stats.lastTxStatus)
      asideColor: rail.stats.lastTxStatus === "failed" ? rail.urgent : rail.accent
      HudMeter {
        width: parent.width
        total: rail.stats.tx
        parts: [{ value: rail.stats.delivery.read, color: rail.accent },
                { value: rail.stats.delivery.delivered, color: Util.alpha(rail.accent, 0.5) },
                { value: rail.stats.delivery.sent + rail.stats.delivery.sending, color: Util.alpha(rail.fg, 0.4) },
                { value: rail.stats.delivery.failed, color: rail.urgent }]
        track: Util.alpha(rail.fg, 0.1)
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Read"
        value: rail.stats.tx > 0 ? rail.stats.delivery.read + " / " + rail.stats.tx : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Delivered"
        value: rail.stats.tx > 0 ? String(rail.stats.delivery.delivered) : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.stats.delivery.sent + rail.stats.delivery.sending > 0
        label: "In flight"
        value: String(rail.stats.delivery.sent + rail.stats.delivery.sending)
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.stats.delivery.failed > 0
        label: "Failed"
        value: String(rail.stats.delivery.failed)
        valueColor: rail.urgent
        strong: true
      }
    }

    // ---- how quickly each side answers, and how long it has been
    HudModule {
      width: parent.width
      title: "Response"
      foreground: rail.fg
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: !rail.hud.noteToSelf
        label: rail.hud.isGroup ? "They answer in" : "Answers in"
        value: rail.stats.replyTheirs >= 0 ? "~" + Telemetry.shortDuration(rail.stats.replyTheirs) : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: !rail.hud.noteToSelf
        label: "You answer in"
        value: rail.stats.replyYours >= 0 ? "~" + Telemetry.shortDuration(rail.stats.replyYours) : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: !rail.hud.noteToSelf
        label: "Last in"
        value: Telemetry.elapsedClock(rail.stats.lastRx, rail.hud.now)
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Last out"
        value: Telemetry.elapsedClock(rail.stats.lastTx, rail.hud.now)
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.stats.transit >= 0
        label: "Transit"
        value: Telemetry.latencyLabel(rail.stats.transit)
      }
    }

    // ---- what is waiting to go out
    HudModule {
      width: parent.width
      title: "Payload"
      foreground: rail.fg
      aside: rail.hud.sending ? "Sending" : (rail.hud.payload.chars > 0 || rail.hud.attachments.length > 0) ? "Armed" : "Idle"
      asideColor: rail.hud.sending || rail.hud.payload.chars > 0 || rail.hud.attachments.length > 0 ? rail.accent : rail.quiet
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Draft"
        value: rail.hud.payload.chars > 0
               ? rail.hud.payload.chars + " ch · " + rail.hud.payload.words + " w" + (rail.hud.payload.lines > 1 ? " · " + rail.hud.payload.lines + " ln" : "")
               : ""
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Attached"
        value: rail.hud.attachments.length + " / 8"
        valueColor: rail.hud.attachments.length > 0 ? rail.accent : rail.fg
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.hud.quote !== null
        label: "Replying to"
        value: rail.hud.quote ? Model.singleLine(rail.hud.quote.who, 16) : ""
      }
    }

    // ---- everything else that is waiting, here and in the other chats
    HudModule {
      width: parent.width
      title: "Queue"
      foreground: rail.fg
      aside: rail.hud.totalUnread > 0 ? rail.hud.totalUnread + " unread" : ""
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Unread, all chats"
        value: String(rail.hud.totalUnread)
        valueColor: rail.hud.totalUnread > 0 ? rail.accent : rail.fg
        strong: rail.hud.totalUnread > 0
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        visible: rail.hud.detached
        label: "Tabs open"
        value: rail.hud.tabCount + (rail.hud.tabsUnread > 0 ? " · " + rail.hud.tabsUnread + " new" : "")
      }
      HudReadout {
        width: parent.width; foreground: rail.fg
        label: "Files · links"
        value: rail.stats.media + " · " + rail.stats.links
      }
    }
  }
}
