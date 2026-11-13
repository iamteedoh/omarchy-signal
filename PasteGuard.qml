// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

// Keeps one native text field (TextField, TextArea) from reading the clipboard
// itself, and pastes into it through `omarchy-signal paste` instead.
//
// Why: Qt reads a clipboard offer whole, on the GUI thread, with no size or
// time limit. An editable field does it on its own every time the clipboard
// changes (to update canPaste), and again for Ctrl+V, Shift+Insert, the
// middle click and the context menu's Paste. Whoever owns the clipboard
// decides how big the offer is and how slowly it arrives, so a hostile one
// could freeze the shell or fill its memory.
//
// So, for its target field:
// * it is read-only unless it has keyboard focus and is on screen. A
//   read-only field never reads the clipboard; focus is only active while the
//   field's window is the active one;
// * there is no context menu: the right click pastes the clipboard and the
//   middle click the selection, through here;
// * handleKey() takes the paste keys; the field calls it from Keys.onPressed;
// * the CLI reads the clipboard through wl-paste with a size and time limit,
//   and its output goes through BoundedProcess (a ceiling and a deadline).
//   A result that arrives after the field was edited, lost focus or was hidden
//   is dropped.
Item {
  id: guard
  visible: false

  required property Item target
  property string cliPath: ""
  // Copied files and pictures come back as paths for attached() rather than text.
  property bool files: false
  property bool singleLine: false
  property int maxChars: singleLine ? 200 : 60000

  signal attached(var paths)
  // No text on the clipboard and nothing attached: callers may say so.
  signal nothing()

  readonly property bool editable: target.activeFocus && target.visible
  readonly property bool busy: reader.busy

  Binding { target: guard.target; property: "readOnly"; value: !guard.editable }
  // The paste-free reading of the field also drops any paste still on its way.
  onEditableChanged: if (!editable) reader.cancel()
  Connections {
    target: guard.target
    function onTextChanged() { if (!guard.inserting) reader.cancel() }
  }

  property bool inserting: false
  property bool primary: false

  function isPasteKey(event) {
    var shift = (event.modifiers & Qt.ShiftModifier) !== 0
    var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
    return event.matches(StandardKey.Paste) || event.key === Qt.Key_Paste
        || (event.key === Qt.Key_Insert && shift)
        || (event.key === Qt.Key_V && ctrl)
  }

  // Call from the field's Keys.onPressed. True when the key was a paste (taken here).
  function handleKey(event) {
    if (!isPasteKey(event)) return false
    event.accepted = true
    paste(false)
    return true
  }

  function paste(fromSelection) {
    if (!guard.cliPath) return
    guard.primary = fromSelection === true
    var argv = [guard.cliPath, "paste", "--max-chars", String(guard.maxChars)]
    if (guard.primary) argv.push("--primary")
    else if (guard.files) argv.push("--files")
    reader.run(argv)
  }

  function insertText(text) {
    var t = guard.target
    var clean = String(text).replace(/\u0000/g, "")
    if (guard.singleLine) clean = clean.replace(/[\r\n\t]+/g, " ")
    clean = clean.slice(0, guard.maxChars)
    if (!clean) return
    guard.inserting = true
    if (t.selectedText && t.selectedText.length > 0) t.remove(t.selectionStart, t.selectionEnd)
    t.insert(t.cursorPosition, clean)
    guard.inserting = false
  }

  BoundedProcess {
    id: reader
    // Text is at most maxChars characters, sent as JSON: room for escapes.
    maxChars: guard.maxChars * 6 + 4096
    timeoutMs: 12000
    onFinished: function(ok, text) {
      if (!ok || !guard.editable) return
      var obj = null
      try { obj = JSON.parse(text) } catch (e) { obj = null }
      if (!obj || typeof obj !== "object") return
      if (Array.isArray(obj.paths) && obj.paths.length) {
        guard.attached(obj.paths.filter(function(p) { return typeof p === "string" && p.charAt(0) === "/" }))
        return
      }
      if (typeof obj.text === "string" && obj.text.length) { guard.insertText(obj.text); return }
      guard.nothing()
    }
  }

  // Both paste through here, like the terminal client: the middle click pastes
  // the selection, the right click the clipboard (no native menu, whose Paste
  // would read the offer itself). The left button is left to the field.
  MouseArea {
    parent: guard.target
    anchors.fill: parent
    acceptedButtons: Qt.MiddleButton | Qt.RightButton
    cursorShape: Qt.IBeamCursor
    onPressed: function(mouse) {
      mouse.accepted = true
      guard.target.forceActiveFocus()
      if (mouse.button === Qt.MiddleButton) guard.target.cursorPosition = guard.target.positionAt(mouse.x, mouse.y)
      guard.paste(mouse.button === Qt.MiddleButton)
    }
  }
}
