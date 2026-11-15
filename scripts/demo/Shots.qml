// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import qs.Commons as Commons
import Quickshell.Io
import qs.Ui as Ui
import "Model.js" as Model
import "Calls.js" as Calls

// Renders the plugin's real components against the demo bridge and saves
// pictures of them, without a compositor: scripts/demo-shots.sh runs this
// under Qt's offscreen platform. Nothing is drawn on a screen, so nothing of
// the desktop can end up in a picture, and every path the components can list
// is inside the demo's throwaway HOME.
//
// With SHOTS_VIDEO=1 it plays a short scripted scene instead and saves every
// frame it can grab, with the time it was taken (scripts/demo-shots.sh --video
// turns them into a video). Messages arrive through the fake signal-cli's
// event file, the same way the demo's seeded conversations do.
ShellRoot {
  id: shots

  readonly property string outDir: Quickshell.env("SHOTS_OUT")
  readonly property string cliPath: Quickshell.env("SHOTS_CLI")
  readonly property string prefix: Quickshell.env("SHOTS_PREFIX") || ""
  readonly property string trinity: "number:+15550002222"
  readonly property string morpheus: "number:+15550003333"
  readonly property string crew: "group:Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="
  property bool up: false
  property var trinityRows: []
  property int saved: 0
  property var convs: []
  property bool listShown: false
  readonly property bool video: Quickshell.env("SHOTS_VIDEO") === "1"

  // Stands in for the bar widget behind the panel's list (ChatList.qml): the
  // same rows, built the same way, from the demo bridge's conversations.
  QtObject {
    id: listOwner
    readonly property color fg: Commons.Color.foreground
    readonly property color dim: Commons.Util.alpha(fg, 0.55)
    readonly property string fontFamily: Commons.Style.font.family
    readonly property bool connected: shots.up
    readonly property bool linked: shots.up
    readonly property bool unlinked: false
    readonly property bool needsSetup: false
    readonly property string latestVersion: ""
    readonly property bool telemetry: true
    readonly property string cliPath: shots.cliPath
    property bool showContacts: false
    property string query: ""
    property int cursor: 0
    readonly property var rows: Model.sortConversations(shots.convs).map(function(c) {
      return { key: c.key, name: Model.singleLine(c.name || c.key, 60), sub: Model.singleLine(c.preview || "", 80),
               unread: c.unread || 0, ts: c.lastTs || 0, open: c.key === shots.trinity, typing: false }
    })
    readonly property int unread: rows.reduce(function(n, r) { return n + r.unread }, 0)
    function ms(d) { return 0 }
    function activate(inTerminal) {}
    function moveCursor(delta) {}
    function toggleMode() {}
    function close() {}
    function runSetup() {}
    function runLink() {}
    function runUpdate() {}
  }

  function sp(px) { return Commons.Style.space(px) }

  // Levels shaped like speech: bursts with pauses, the two sides out of step.
  function speech(n, side) {
    var out = []
    for (var i = 0; i < n; i++) {
      var turn = Math.sin((i + side * 9) / 5.5) > (side ? 0.05 : -0.25)
      var syllable = 0.5 + 0.5 * Math.sin(i * 1.9 + side * 2.1) * Math.cos(i * 0.71 + side)
      out.push(turn ? Math.max(0.08, Math.min(1, 0.3 + 0.62 * syllable)) : 0.03 + 0.03 * Math.abs(Math.sin(i * 3.3)))
    }
    return out
  }

  Bridge {
    id: link
    onEvent: function(name, data) {
      if (name === "hello" || name === "status") {
        shots.up = data.connected === true && data.linked === true
        if (shots.up && shots.video && !scene.started) scene.begin()
        else if (shots.up && !shots.video && !runner.running && runner.step === 0) runner.start()
      }
      if (shots.video) chat.onEvent(name, data)
    }
  }

  // ---- video mode -------------------------------------------------------
  readonly property string trinityNumber: "+15550002222"
  readonly property string morpheusNumber: "+15550003333"

  Component { id: injectorComponent; Process { onExited: destroy() } }
  // One line into the fake signal-cli's event file, which it emits as a notification.
  function inject(params) {
    var p = injectorComponent.createObject(shots, {
      command: ["sh", "-c", 'printf "%s\\n" "$1" >>"$FAKE_SIGNAL_EVENTS"', "sh", JSON.stringify(params)] })
    p.running = true
  }
  function incoming(number, name, text) {
    var ts = Date.now()
    shots.inject({ envelope: { source: number, sourceNumber: number, sourceName: name, sourceDevice: 1, timestamp: ts,
                               dataMessage: { timestamp: ts, message: text, expiresInSeconds: 0, viewOnce: false, attachments: [] } } })
  }
  function typing(number, name) {
    var ts = Date.now()
    shots.inject({ envelope: { source: number, sourceNumber: number, sourceName: name, sourceDevice: 1, timestamp: ts,
                               typingMessage: { action: "STARTED", timestamp: ts } } })
  }
  function slideToast(show) {
    toastSlide.stop()
    toastSlide.from = show ? stage.width : toast.x
    toastSlide.to = show ? stage.width - toast.width - 70 : stage.width + 20
    if (show) { toast.x = toastSlide.from; toast.visible = true }
    toastSlide.start()
  }
  NumberAnimation { id: toastSlide; target: toast; property: "x"; duration: 420; easing.type: Easing.OutCubic }

  // A call as the bridge would report it, changed in place.
  function callState(changes) {
    var c = Object.assign({}, chat.activeCall || {}, changes)
    chat.activeCall = c
  }
  property real talkStart: 0
  // Two people taking turns: each side speaks in bursts of syllables, then listens.
  Timer {
    id: talk
    interval: 66; repeat: true
    onTriggered: {
      var t = (Date.now() - shots.talkStart) / 1000
      // Short turns that overlap a little, so both waveforms keep moving.
      var mine = Math.sin(t * 1.9) > -0.1
      var theirs = Math.sin(t * 1.9 + 2.6) > -0.2
      function voice(on, phase) {
        if (!on) return 0.02 + 0.03 * Math.random()
        var syllable = Math.abs(Math.sin(t * 7.3 + phase)) * (0.6 + 0.4 * Math.sin(t * 2.1 + phase))
        return Math.max(0.05, Math.min(1, 0.25 + 0.65 * syllable + 0.08 * Math.random()))
      }
      var muted = chat.activeCall && chat.activeCall.muted
      var tx = muted ? 0 : voice(mine, 0)
      var rx = voice(theirs, 1.7)
      chat.callTx = Calls.pushLevel(chat.callTx, tx, 48)
      chat.callRx = Calls.pushLevel(chat.callRx, rx, 48)
      var lv = chat.activeCall.levels || {}
      shots.callState({ levels: { tx: tx, rx: rx, txPeak: Math.max(lv.txPeak || 0, tx), rxPeak: Math.max(lv.rxPeak || 0, rx),
                                  txTalkMs: (lv.txTalkMs || 0) + (tx > 0.42 ? 66 : 0), rxTalkMs: (lv.rxTalkMs || 0) + (rx > 0.42 ? 66 : 0) } })
    }
  }

  // The scene, as [time in ms, what happens]. About 25 seconds.
  readonly property var script: [
    [300, function() { chat.load(shots.trinity, "Trinity") }],
    [2600, function() { shots.typing(shots.trinityNumber, "Trinity") }],
    [4300, function() { shots.incoming(shots.trinityNumber, "Trinity", "Exit 04 is clear. Move now.") }],
    [5600, function() { typer.text = "On my way. Two minutes."; typer.at = 0; typer.start() }],
    [7400, function() { chat.send() }],
    [8900, function() {
        shots.incoming(shots.morpheusNumber, "Morpheus", "The Oracle will see you now. 🔮")
        toast.kind = "message"; toast.title = "Morpheus"; toast.convName = "Morpheus"; toast.body = "The Oracle will see you now. 🔮"
        shots.slideToast(true)
      }],
    [10100, function() { toast.forceHover = true }],
    [11400, function() { toast.forceHover = false; shots.slideToast(false) }],
    [12000, function() {
        var now = Date.now()
        chat.callsAvailable = true
        chat.callTx = []; chat.callRx = []
        chat.activeCall = { id: "43", conversation: shots.trinity, name: "Trinity", outgoing: false, state: "ringing",
                            media: "audio", startedAt: now, connectedAt: 0, endedAt: 0, reason: "", muted: false,
                            reconnects: 0, encrypted: true, setupMs: 0, audioError: "", mic: "", speaker: "", levels: {} }
        toast.kind = "call"; toast.title = "Trinity"; toast.convName = "Trinity"; toast.body = "Incoming voice call"
        shots.slideToast(true)
      }],
    [14400, function() { shots.slideToast(false); shots.callState({ state: "connecting" }) }],
    [15100, function() {
        shots.callState({ state: "connected", connectedAt: Date.now(), setupMs: 700,
                          mic: "alsa_input.usb-desk-microphone", speaker: "alsa_output.usb-headphones" })
        shots.talkStart = Date.now(); talk.start()
      }],
    [19000, function() { shots.callState({ muted: true }) }],
    [20600, function() { shots.callState({ muted: false }) }],
    [22600, function() { talk.stop(); shots.callState({ state: "ended", endedAt: Date.now(), reason: "local_hangup" }) }],
    [25000, function() { scene.finish() }]
  ]

  Timer {
    id: typer
    property string text: ""
    property int at: 0
    interval: 60; repeat: true
    onTriggered: { at += 1; chat.draft = text.slice(0, at); if (at >= text.length) stop() }
  }

  QtObject {
    id: scene
    property bool started: false
    property real t0: 0
    property int next: 0
    property int frames: 0
    property bool busy: false
    property bool done: false
    function begin() {
      started = true
      link.request("conversations", {}, function(ok, rows) { shots.convs = ok ? rows : [] })
      t0 = Date.now()
      clock.start()
      grabber.start()
    }
    function finish() {
      done = true
      clock.stop()
      grabber.stop()
      Qt.callLater(function() { console.log("SHOTS DONE " + scene.frames); Qt.quit() })
    }
  }
  Timer {
    id: clock
    interval: 20; repeat: true
    onTriggered: {
      var t = Date.now() - scene.t0
      while (scene.next < shots.script.length && shots.script[scene.next][0] <= t) {
        shots.script[scene.next][1]()
        scene.next += 1
      }
    }
  }
  // Grabs as fast as the scene renders (aiming at 30 a second) and logs when
  // each frame was taken, so the video keeps real time even if a grab is slow.
  Timer {
    id: grabber
    interval: 33; repeat: true
    onTriggered: {
      if (scene.busy || scene.done) return
      scene.busy = true
      var n = scene.frames
      var t = Date.now() - scene.t0
      stage.grabToImage(function(result) {
        var name = "frame-" + ("00000" + n).slice(-5) + ".bmp"   // uncompressed: a PNG costs a third of the frame rate
        if (result.saveToFile(shots.outDir + "/" + name)) console.log("FRAME " + name + " " + t)
        scene.frames += 1
        scene.busy = false
      }, Qt.size(stage.width * 2, stage.height * 2))   // twice the logical size, like the pictures
    }
  }

  // The steps, in order. Each sets a scene up, waits for it to settle, and is photographed.
  readonly property var steps: [
    { wait: 1400, name: "", run: function() {
        link.request("history", { conversation: shots.trinity, limit: 60 }, function(ok, rows) { shots.trinityRows = ok ? rows : [] })
        link.request("conversations", {}, function(ok, rows) { shots.convs = ok ? rows : [] })
        chat.load(shots.trinity, "Trinity")
      } },
    { wait: 500, name: "chat-window", run: function() {} },
    { wait: 500, name: "message-actions", run: function() {
        var mine = shots.trinityRows.filter(function(m) { return m.outgoing && m.body.indexOf("Heading out") >= 0 })
        if (mine.length) { chat.selectedTs = mine[0].ts; chat.emojiRowOpen = true }
      } },
    { wait: 700, name: "shortcuts", run: function() { chat.selectedTs = 0; chat.emojiRowOpen = false; chat.helpOpen = true } },
    { wait: 1800, name: "attach-picker", run: function() { chat.helpOpen = false; chat.pickAttachment() } },
    { wait: 1400, name: "group-chat", run: function() { chat.pickerOpen = false; chat.load(shots.crew, "Nebuchadnezzar crew") } },
    { wait: 1400, name: "", run: function() { chat.load(shots.trinity, "Trinity") } },
    { wait: 900, name: "timer", run: function() { chat.expiration = 3600; chat.chatMenuOpen = true } },
    { wait: 900, name: "call", run: function() {
        chat.chatMenuOpen = false; chat.expiration = 0
        // A call in progress, as the bridge would report it. No audio device is
        // touched: the levels are drawn here, shaped like two people taking turns.
        var now = Date.now()
        chat.callsAvailable = true
        chat.callTx = shots.speech(48, 0).concat([0.58, 0.66])
        chat.callRx = shots.speech(48, 1).concat([0.44, 0.5])
        chat.activeCall = { id: "42", conversation: shots.trinity, name: "Trinity", outgoing: true, state: "connected",
                            media: "audio", startedAt: now - 194400, connectedAt: now - 192000, endedAt: 0, reason: "",
                            muted: false, reconnects: 0, encrypted: true, setupMs: 2400, audioError: "",
                            mic: "alsa_input.usb-desk-microphone", speaker: "alsa_output.usb-headphones",
                            levels: { tx: 0.58, rx: 0.22, txPeak: 0.91, rxPeak: 0.84, txTalkMs: 71000, rxTalkMs: 93000 } }
        chat.now = now
      } },
    { wait: 900, name: "incoming-call", run: function() {
        // Trinity calling: the panel rings in the chat and the popup offers Answer and Decline.
        var now = Date.now()
        chat.callTx = []; chat.callRx = []
        chat.activeCall = { id: "43", conversation: shots.trinity, name: "Trinity", outgoing: false, state: "ringing",
                            media: "audio", startedAt: now - 6000, connectedAt: 0, endedAt: 0, reason: "",
                            muted: false, reconnects: 0, encrypted: true, setupMs: 0, audioError: "",
                            mic: "", speaker: "", levels: {} }
        chat.now = now
        toast.kind = "call"; toast.title = "Trinity"; toast.convName = "Trinity"; toast.body = "Incoming voice call"
        toast.visible = true
      } },
    { wait: 700, name: "notification", run: function() {
        chat.activeCall = null; chat.callTx = []; chat.callRx = []
        toast.kind = "message"; toast.title = "Morpheus"; toast.convName = "Morpheus"; toast.body = "The Oracle will see you now. 🔮"
        toast.visible = true
      } },
    { wait: 700, name: "chat-list", run: function() { toast.visible = false; shots.listShown = true } },
    { wait: 700, name: "preview", run: function() { toast.visible = true; toast.forceHover = true } },
    { wait: 1400, name: "", run: function() {
        toast.visible = false
        shots.listShown = false
        dialogLayer.visible = true
        reply.load(shots.morpheus, "Morpheus")
      } },
    { wait: 700, name: "reply-dialog", run: function() {
        link.request("history", { conversation: shots.morpheus, limit: 60 }, function(ok, rows) {
          var theirs = ok ? rows.filter(function(m) { return !m.outgoing }) : []
          if (theirs.length) reply.quote = { ts: theirs[theirs.length - 1].ts, author: shots.morpheus, text: theirs[theirs.length - 1].body, who: "Morpheus" }
        })
        reply.draft = "On my way. See you in Zion."
      } }
  ]

  Timer {
    id: runner
    property int step: 0
    interval: 600
    onTriggered: {
      if (step > 0) {
        var name = shots.steps[step - 1].name
        if (name) {
          var path = shots.outDir + "/" + shots.prefix + name + ".png"
          // Twice the logical size, so the pictures stay sharp on a dense display.
          stage.grabToImage(function(result) {
            console.log("SHOT " + (result.saveToFile(path) ? "saved " : "FAILED ") + path)
            shots.saved += 1
            runner.advance()
          }, Qt.size(stage.width * 2, stage.height * 2))
          return
        }
      }
      advance()
    }
    function advance() {
      if (step >= shots.steps.length) { console.log("SHOTS DONE " + shots.saved); Qt.quit(); return }
      var s = shots.steps[step]
      step += 1
      s.run()
      interval = s.wait
      restart()
    }
  }

  // Give up rather than hang when the demo bridge never answers.
  Timer { interval: 90000; running: true; onTriggered: { console.log("SHOTS TIMEOUT up=" + shots.up); Qt.quit() } }

  FloatingWindow {
    visible: true
    implicitWidth: 1500
    implicitHeight: 860
    color: Commons.Color.background

    Item {
      id: stage
      // The video frames the chat window alone, and its popups land over the
      // window's top right corner, where Omarchy's notifications would be.
      width: shots.video ? frame.x + frame.width + 50 : 1500
      height: shots.video ? frame.y + frame.height + 50 : 860

      // A plain backdrop in the theme's own colours: no wallpaper, no desktop.
      Rectangle {
        anchors.fill: parent
        gradient: Gradient {
          GradientStop { position: 0.0; color: Qt.darker(Commons.Color.background, 1.18) }
          GradientStop { position: 1.0; color: Qt.lighter(Commons.Color.background, 1.3) }
        }
      }
      // The chat window, framed the way Hyprland frames a floating window.
      Ui.BorderSurface {
        id: frame
        x: 50; y: 50
        width: shots.sp(940); height: shots.sp(760)
        color: Commons.Color.popups.background
        borderSpec: Commons.Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, shots.sp(2)))
        radius: Commons.Style.cornerRadius
        ConversationView {
          id: chat
          anchors.fill: parent
          anchors.margins: Commons.Style.spacing.panelPadding
          cliPath: shots.cliPath
          bridge: link
          connected: shots.up
          linked: shots.up
          account: "+15550001111"
          totalUnread: listOwner.unread
          detached: true
          siblings: [{ key: shots.trinity, name: "Trinity", unread: false }, { key: shots.morpheus, name: "Morpheus", unread: true },
                     { key: shots.crew, name: "Nebuchadnezzar crew", unread: false }]
        }
        HudFrame { anchors.fill: parent; anchors.margins: shots.sp(4); arm: shots.sp(16) }
      }

      // The bar panel's list, in the surface the panel gives it.
      Ui.BorderSurface {
        id: listFrame
        visible: shots.listShown
        x: frame.x + frame.width + 40
        y: 50
        width: stage.width - x - 50
        height: chatList.implicitHeight + 2 * Commons.Style.spacing.panelPadding
        color: Commons.Color.popups.background
        borderSpec: Commons.Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, shots.sp(2)))
        radius: Commons.Style.cornerRadius
        ChatList {
          id: chatList
          owner: listOwner
          anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
          anchors.margins: Commons.Style.spacing.panelPadding
        }
      }

      ToastCard {
        id: toast
        visible: false
        x: stage.width - width - 50
        y: shots.listShown ? listFrame.y + listFrame.height + 24 : shots.video ? 70 : 50
        width: shots.listShown ? listFrame.width : shots.sp(380)
        kind: "message"
        title: "Morpheus"
        convName: "Morpheus"
        body: "The Oracle will see you now. 🔮"
      }

      Item {
        id: dialogLayer
        visible: false
        anchors.fill: parent
        Rectangle { anchors.fill: parent; color: Commons.Color.menu.scrim }
        Ui.BorderSurface {
          anchors.centerIn: parent
          width: shots.sp(940); height: shots.sp(760)
          color: Commons.Util.alpha(Commons.Color.popups.background, 0.98)
          borderSpec: Commons.Border.surfaceSpec("popups", "border", Commons.Color.popups.border, Math.max(1, shots.sp(2)))
          radius: Commons.Style.cornerRadius
          ConversationView {
            id: reply
            anchors.fill: parent
            anchors.margins: Commons.Style.spacing.panelPadding
            cliPath: shots.cliPath
            bridge: link
            connected: shots.up
            linked: shots.up
            account: "+15550001111"
          }
        }
      }
    }
  }
}
