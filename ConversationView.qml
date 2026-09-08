import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "Emoji.js" as Emoji

// One conversation: thread, composer, attachments, quote, reactions. Used by
// the click-to-reply popup and, unchanged, by the detached windows, so both
// look and behave the same. Everything shown is PlainText; every action goes
// through the CLI as an argv array.
Item {
  id: view

  required property string cliPath
  property string conversationKey: ""
  property string conversationName: ""
  property bool connected: false
  property bool linked: false
  property bool detached: false          // true inside a FloatingWindow
  property string account: ""            // our own number, for reacting to our own messages
  property bool emojiAutoconvert: true   // :smile: / :D become emoji after the space (setting)
  property bool stickToBottom: true      // follow new messages unless the user scrolled up
  property string typingName: ""

  signal requestClose()
  signal requestDetach()
  signal requestTerminal()

  property var thread: []                // Model.threadRows output
  property var quote: null               // {ts, author, text, who}
  property var attachments: []           // absolute paths
  property bool sending: false
  property string error: ""
  property int selectedTs: 0             // message with the action row open
  property bool emojiRowOpen: false
  readonly property var quickEmojis: ["👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "🎉"]

  implicitWidth: Style.space(640)
  implicitHeight: Style.space(560)

  function load(key, name) {
    view.stickToBottom = true
    view.conversationKey = key
    view.conversationName = Model.singleLine(name || key, 80)
    view.thread = []
    view.quote = null
    view.attachments = []
    view.error = ""
    view.selectedTs = 0
    view.emojiRowOpen = false
    historyProc.running = false
    historyProc.command = [view.cliPath, "history", "--json", "-n", "60", "--", key]
    historyProc.running = true
    markReadProc.command = [view.cliPath, "mark-read", "--", key]
    markReadProc.running = true
    Qt.callLater(function() { composer.forceActiveFocus() })
  }

  function reload() {
    if (!view.conversationKey) return
    historyProc.running = false
    historyProc.command = [view.cliPath, "history", "--json", "-n", "60", "--", view.conversationKey]
    historyProc.running = true
  }

  // Service.qml feeds bridge events here; anything about this conversation refreshes the thread.
  function onEvent(name, data) {
    if (!data || data.conversation !== view.conversationKey) return
    if (name === "typing") { view.typingName = data.typing ? Model.singleLine(data.senderName || "", 40) : ""; return }
    if (name === "message" || name === "sent" || name === "edited" || name === "deleted" || name === "reaction" || name === "receipt") {
      view.reload()
      if (name === "message") { markReadProc.command = [view.cliPath, "mark-read", "--", view.conversationKey]; markReadProc.running = true }
    }
  }

  function send() {
    var argv = Model.sendArgvFull(view.conversationKey, composer.text, view.attachments, view.quote)
    if (!argv || view.sending) return
    argv[0] = view.cliPath
    view.sending = true
    view.error = ""
    sendProc.command = argv
    sendProc.running = true
  }

  function react(row, emojiText) {
    var author = row.outgoing ? ("number:" + view.account) : row.sender
    var argv = Model.reactArgv(view.conversationKey, row.ts, author, emojiText)
    if (!argv) { view.error = "cannot react to that message"; return }
    argv[0] = view.cliPath
    actionProc.command = argv
    actionProc.running = true
    view.emojiRowOpen = false
    view.selectedTs = 0
  }

  function quoteRow(row) {
    view.quote = { ts: row.ts, author: row.sender, text: row.body, who: row.who }
    view.selectedTs = 0
    composer.forceActiveFocus()
  }

  // ---- attachment picker (thumbnails)
  property bool pickerOpen: false
  property string pickerDir: ""
  property string pickerParent: ""
  property var pickerRows: []
  property string pickerFilter: ""
  readonly property string home: Quickshell.env("HOME") || ""
  readonly property var quickDirs: ["Pictures", "Screenshots", "Downloads", "Documents", "Desktop"]

  function pickAttachment() {
    view.pickerOpen = true
    view.pickerFilter = ""
    view.listDir(view.pickerDir || (view.home + "/Pictures"))
  }

  function listDir(dir) {
    lsProc.running = false
    lsProc.command = [view.cliPath, "ls-files", "--", dir]
    lsProc.running = true
  }

  function chooseRow(row) {
    if (row.isDir) { view.listDir(row.path); return }
    if (view.attachments.indexOf(row.path) < 0 && view.attachments.length < 8) view.attachments = view.attachments.concat([row.path])
    view.pickerOpen = false
    composer.forceActiveFocus()
  }

  readonly property var pickerVisible: {
    var q = Model.singleLine(view.pickerFilter, 80).toLowerCase()
    var rows = Array.isArray(view.pickerRows) ? view.pickerRows : []
    return q ? rows.filter(function(r) { return r.name.toLowerCase().indexOf(q) >= 0 }) : rows
  }

  Process {
    id: lsProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var obj = null
        try { obj = JSON.parse(text) } catch (e) { obj = null }
        if (!obj || !Array.isArray(obj.rows)) { view.pickerRows = []; return }
        view.pickerDir = String(obj.dir || "")
        view.pickerParent = String(obj.parent || "")
        view.pickerRows = obj.rows.filter(function(r) { return r && typeof r.path === "string" && r.path.charAt(0) === "/" })
      }
    }
  }

  Process {
    id: historyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var rows = []
        try { rows = JSON.parse(text) } catch (e) { rows = [] }
        if (Array.isArray(rows)) { view.thread = Model.threadRows(rows, 60); if (view.stickToBottom) Qt.callLater(list.positionViewAtEnd) }
      }
    }
  }
  Process { id: markReadProc }
  Process { id: actionProc; onExited: function(code) { if (code !== 0) view.error = "action failed"; view.reload() } }
  Process {
    id: sendProc
    stderr: StdioCollector { id: sendErr; waitForEnd: true }
    onExited: function(code) {
      view.sending = false
      if (code === 0) { composer.text = ""; view.quote = null; view.attachments = []; view.reload() }
      else view.error = Model.singleLine(sendErr.text || ("send failed (" + code + ")"), 160)
    }
  }

  ColumnLayout {
    anchors.fill: parent
    spacing: Style.space(10)

    // Header
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(10)
      Text { text: "◢"; textFormat: Text.PlainText; color: Color.accent; font.family: Style.font.family; font.pixelSize: Style.font.title }
      Text {
        Layout.fillWidth: true
        text: view.conversationName
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.title
        font.bold: true
      }
      Text {
        text: view.connected ? "◉ SECURE CHANNEL" : "◌ OFFLINE"
        textFormat: Text.PlainText
        color: view.connected ? Color.accent : Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1.5
      }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Util.alpha(Color.popups.border, 0.6) }

    // Thread
    ListView {
      id: list
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      spacing: Style.space(6)
      model: view.thread
      boundsBehavior: Flickable.StopAtBounds
      // Follow the conversation: stay pinned to the newest message while the
      // user has not scrolled up, including when images finish loading.
      onContentHeightChanged: if (view.stickToBottom) positionViewAtEnd()
      onCountChanged: if (view.stickToBottom) Qt.callLater(positionViewAtEnd)
      onMovementEnded: view.stickToBottom = atYEnd
      onFlickEnded: view.stickToBottom = atYEnd
      delegate: Item {
        id: row
        required property var modelData
        width: list.width
        implicitHeight: bubble.implicitHeight + (actions.visible ? actions.implicitHeight + Style.space(4) : 0)

        Rectangle {
          id: bubble
          anchors.left: modelData.outgoing ? undefined : parent.left
          anchors.right: modelData.outgoing ? parent.right : undefined
          width: Math.min(parent.width * 0.85, Math.max(content.implicitWidth + Style.space(24), Style.space(120)))
          implicitHeight: content.implicitHeight + Style.space(16)
          radius: Style.cornerRadius
          color: modelData.outgoing ? Util.alpha(Color.accent, 0.18) : Util.alpha(Color.popups.text, 0.08)
          border.width: view.selectedTs === modelData.ts ? 2 : 1
          border.color: view.selectedTs === modelData.ts ? Color.accent : (modelData.outgoing ? Util.alpha(Color.accent, 0.45) : Util.alpha(Color.popups.border, 0.5))

          MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onClicked: function(mouse) {
              if (mouse.button === Qt.MiddleButton) { view.quoteRow(row.modelData); return }
              view.emojiRowOpen = false
              view.selectedTs = view.selectedTs === row.modelData.ts ? 0 : row.modelData.ts
            }
          }

          ColumnLayout {
            id: content
            anchors.fill: parent
            anchors.margins: Style.space(8)
            anchors.leftMargin: Style.space(12)
            spacing: Style.space(4)
            Text {
              visible: !row.modelData.outgoing
              text: row.modelData.who
              textFormat: Text.PlainText
              color: Color.accent
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            Text {
              visible: row.modelData.quote.length > 0
              Layout.fillWidth: true
              text: "↩ " + row.modelData.quote
              textFormat: Text.PlainText
              elide: Text.ElideRight
              color: Util.alpha(Color.popups.text, 0.6)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.italic: true
            }
            Image {
              visible: row.modelData.image.length > 0
              Layout.preferredWidth: Math.min(Style.space(360), list.width * 0.7)
              Layout.preferredHeight: visible ? Math.min(Style.space(300), Layout.preferredWidth * Math.max(0.3, implicitHeight / Math.max(1, implicitWidth))) : 0
              source: row.modelData.image ? "file://" + row.modelData.image : ""
              asynchronous: true
              cache: false
              fillMode: Image.PreserveAspectFit
              sourceSize.width: 720
              MouseArea { anchors.fill: parent; onClicked: Util.execArgv(["xdg-open", row.modelData.image]) }
            }
            Text {
              visible: row.modelData.body.length > 0
              Layout.fillWidth: true
              text: row.modelData.body
              textFormat: Text.PlainText
              wrapMode: Text.Wrap
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            Text {
              visible: row.modelData.files.length > 0 && !row.modelData.image
              text: "󰁦 " + row.modelData.files.join(", ")
              textFormat: Text.PlainText
              elide: Text.ElideRight
              Layout.fillWidth: true
              color: Util.alpha(Color.popups.text, 0.7)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            RowLayout {
              Layout.fillWidth: true
              spacing: Style.space(8)
              Text {
                visible: row.modelData.reactions.length > 0
                text: row.modelData.reactions
                textFormat: Text.PlainText
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
              Item { Layout.fillWidth: true }
              Text {
                text: Model.relativeTime(row.modelData.ts) + (row.modelData.edited ? " · edited" : "")
                      + (row.modelData.outgoing ? (row.modelData.status === "read" ? " ✓✓" : row.modelData.status === "delivered" ? " ✓✓" : row.modelData.status === "failed" ? " ✗" : " ✓") : "")
                textFormat: Text.PlainText
                color: row.modelData.status === "read" ? Color.accent : Util.alpha(Color.popups.text, 0.5)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
              }
            }
          }
        }

        // Action row under the selected message: reply, react, copy.
        RowLayout {
          id: actions
          visible: view.selectedTs === row.modelData.ts
          anchors.top: bubble.bottom
          anchors.topMargin: Style.space(4)
          anchors.left: row.modelData.outgoing ? undefined : parent.left
          anchors.right: row.modelData.outgoing ? parent.right : undefined
          spacing: Style.space(6)
          Button { text: "↩ Reply"; onClicked: view.quoteRow(row.modelData) }
          Button { text: "☺ React"; onClicked: view.emojiRowOpen = !view.emojiRowOpen }
          Repeater {
            model: view.emojiRowOpen ? view.quickEmojis : []
            delegate: Button { required property string modelData; text: modelData; onClicked: view.react(row.modelData, modelData) }
          }
          Button { text: "Copy"; onClicked: Util.execArgv(["wl-copy", "--", row.modelData.body]) }
        }
      }
    }

    // Thumbnail picker, drawn over the thread while open.
    Rectangle {
      id: picker
      visible: view.pickerOpen
      Layout.fillWidth: true
      Layout.preferredHeight: visible ? Math.min(Style.space(420), Math.max(Style.space(220), grid.contentHeight + pickerHead.implicitHeight + Style.space(40))) : 0
      color: Util.alpha(Color.popups.background, 1.0)
      border.color: Util.alpha(Color.accent, 0.5)
      border.width: 1
      radius: Style.cornerRadius

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.space(10)
        spacing: Style.space(8)

        RowLayout {
          id: pickerHead
          Layout.fillWidth: true
          spacing: Style.space(6)
          Button { text: "↑"; onClicked: if (view.pickerParent) view.listDir(view.pickerParent) }
          Repeater {
            model: view.quickDirs
            delegate: Button {
              required property string modelData
              text: modelData
              onClicked: view.listDir(view.home + "/" + modelData)
            }
          }
          TextField {
            id: pickerFilterField
            Layout.fillWidth: true
            placeholderText: "filter " + view.pickerDir.replace(view.home, "~")
            onTextChanged: view.pickerFilter = text
            Keys.onEscapePressed: function(e) { view.pickerOpen = false; composer.forceActiveFocus(); e.accepted = true }
            Keys.onReturnPressed: if (view.pickerVisible.length) view.chooseRow(view.pickerVisible[0])
          }
          Button { text: "✕"; onClicked: { view.pickerOpen = false; composer.forceActiveFocus() } }
        }

        GridView {
          id: grid
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          cellWidth: Style.space(132)
          cellHeight: Style.space(132)
          model: view.pickerVisible
          boundsBehavior: Flickable.StopAtBounds
          delegate: Item {
            required property var modelData
            width: grid.cellWidth
            height: grid.cellHeight
            Rectangle {
              id: tile
              anchors.fill: parent
              anchors.margins: Style.space(4)
              radius: Style.cornerRadius / 2
              color: tileHover.containsMouse ? Util.alpha(Color.accent, 0.18) : Util.alpha(Color.popups.text, 0.05)
              border.color: tileHover.containsMouse ? Color.accent : Util.alpha(Color.popups.border, 0.4)
              border.width: 1
              ColumnLayout {
                anchors.fill: parent
                anchors.margins: Style.space(6)
                spacing: Style.space(4)
                Item {
                  Layout.fillWidth: true
                  Layout.fillHeight: true
                  Image {
                    anchors.fill: parent
                    visible: modelData.kind === "image"
                    source: modelData.kind === "image" ? "file://" + modelData.path : ""
                    asynchronous: true
                    cache: true
                    fillMode: Image.PreserveAspectCrop
                    sourceSize.width: 160
                    sourceSize.height: 160
                  }
                  Text {
                    anchors.centerIn: parent
                    visible: modelData.kind !== "image"
                    text: modelData.isDir ? "" : (modelData.kind === "video" ? "󰕧" : modelData.kind === "audio" ? "󰎈" : modelData.kind === "doc" ? "󰈙" : "󰈔")
                    textFormat: Text.PlainText
                    color: modelData.isDir ? Color.accent : Util.alpha(Color.popups.text, 0.8)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.displayLarge
                  }
                }
                Text {
                  Layout.fillWidth: true
                  text: modelData.name + (modelData.isDir ? "/" : "")
                  textFormat: Text.PlainText
                  elide: Text.ElideMiddle
                  horizontalAlignment: Text.AlignHCenter
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
              MouseArea { id: tileHover; anchors.fill: parent; hoverEnabled: true; onClicked: view.chooseRow(modelData) }
            }
          }
        }
      }
    }

    Text {
      visible: view.typingName.length > 0
      text: view.typingName + " is typing…"
      textFormat: Text.PlainText
      color: Color.accent
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }

    // Quote preview
    RowLayout {
      visible: view.quote !== null
      Layout.fillWidth: true
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: view.quote ? "↩ " + view.quote.who + ": " + Model.singleLine(view.quote.text, 120) : ""
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Util.alpha(Color.popups.text, 0.7)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        font.italic: true
      }
      Button { text: "✕"; onClicked: view.quote = null }
    }

    // Attachment chips
    Flow {
      visible: view.attachments.length > 0
      Layout.fillWidth: true
      spacing: Style.space(6)
      Repeater {
        model: view.attachments
        delegate: Rectangle {
          required property string modelData
          required property int index
          width: chip.implicitWidth + Style.space(16)
          height: chip.implicitHeight + Style.space(8)
          radius: height / 2
          color: Util.alpha(Color.accent, 0.15)
          border.color: Util.alpha(Color.accent, 0.5)
          Text {
            id: chip
            anchors.centerIn: parent
            text: "󰁦 " + modelData.split("/").pop() + "  ✕"
            textFormat: Text.PlainText
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          MouseArea { anchors.fill: parent; onClicked: { var a = view.attachments.slice(); a.splice(index, 1); view.attachments = a } }
        }
      }
    }

    // Composer
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)
      Button { text: "󰁦"; onClicked: view.pickAttachment() }
      TextField {
        id: composer
        Layout.fillWidth: true
        placeholderText: view.linked ? "Message… (Enter sends, :smile: works)" : "No account linked: omarchy-signal link"
        enabled: !view.sending && view.linked
        onAccepted: view.send()
        onTextEdited: {
          if (!view.emojiAutoconvert) return
          var r = Emoji.convertBeforeCursor(text, cursorPosition)
          if (r) { text = r.text; cursorPosition = r.cursor }
        }
        Keys.onEscapePressed: function(event) { if (view.quote) view.quote = null; else view.requestClose(); event.accepted = true }
      }
      Button { text: "Send"; enabled: !view.sending && view.linked; onClicked: view.send() }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: view.error ? view.error : (view.sending ? "Encrypting…" : "click a message for reply / react · middle-click replies · Esc closes")
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: view.error ? Color.urgent : Util.alpha(Color.popups.text, 0.5)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      Button { visible: !view.detached; text: "Detach ⧉"; onClicked: view.requestDetach() }
      Button { text: "Terminal"; onClicked: view.requestTerminal() }
    }
  }
}
