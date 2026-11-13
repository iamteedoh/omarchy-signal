// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import qs.Commons as Commons
import qs.Ui as Ui
import "Model.js" as Model

// Renders the plugin's real components against the demo bridge and saves
// pictures of them, without a compositor: scripts/demo-shots.sh runs this
// under Qt's offscreen platform. Nothing is drawn on a screen, so nothing of
// the desktop can end up in a picture, and every path the components can list
// is inside the demo's throwaway HOME.
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

  Bridge {
    id: link
    onEvent: function(name, data) {
      if (name === "hello" || name === "status") {
        shots.up = data.connected === true && data.linked === true
        if (shots.up && !runner.running && runner.step === 0) runner.start()
      }
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
    { wait: 700, name: "notification", run: function() { chat.chatMenuOpen = false; chat.expiration = 0; toast.visible = true } },
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
  Timer { interval: 60000; running: true; onTriggered: { console.log("SHOTS TIMEOUT up=" + shots.up); Qt.quit() } }

  FloatingWindow {
    visible: true
    implicitWidth: 1240
    implicitHeight: 800
    color: Commons.Color.background

    Item {
      id: stage
      width: 1240
      height: 800

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
        width: shots.sp(700); height: shots.sp(700)
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
          detached: true
          siblings: [{ key: shots.trinity, name: "Trinity", unread: false }, { key: shots.morpheus, name: "Morpheus", unread: true },
                     { key: shots.crew, name: "Nebuchadnezzar crew", unread: false }]
        }
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
        y: shots.listShown ? listFrame.y + listFrame.height + 24 : 50
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
          width: shots.sp(680); height: shots.sp(640)
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
