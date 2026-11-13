// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell.Io

// Runs one of this plugin's own commands with a deadline and a ceiling on
// what it may print, and reports exactly once.
//
// Output is counted as it arrives, not after the process ends: past maxChars
// the process is killed and the run fails, so a misbehaving producer cannot
// grow the shell's memory. Past timeoutMs it is killed too. A process that
// cannot start (no exec, bad path) also fails, through the same watchdog,
// since Quickshell reports no exit for it.
//
// run() while busy cancels the earlier run. cancel() drops a run's result:
// callers cancel when what they asked about has changed (the text was edited,
// the field lost focus, the window closed), so a late answer is never applied
// to a newer state.
Item {
  id: root
  visible: false

  property int maxChars: 256 * 1024
  property int timeoutMs: 15000
  readonly property bool busy: !_settled

  // ok: exited with status 0 within the limits. text: everything it printed (stdout).
  signal finished(bool ok, string text)

  property string _buf: ""
  property bool _settled: true
  property int _run: 0
  property var _proc: null

  function run(argv) {
    cancel()
    _buf = ""
    _settled = false
    _run += 1
    // A process per run: a killed one may still report its exit after the
    // next run has started, and must not be mistaken for it.
    _proc = procComponent.createObject(root, { runId: _run, command: argv })
    _proc.running = true
    watchdog.restart()
  }

  function cancel() {
    if (_settled) return
    _settled = true
    _run += 1
    watchdog.stop()
    _kill()
  }

  function _kill() {
    // A killed process destroys itself when it reports its exit; one that never
    // started reports nothing, so it goes now.
    if (_proc && _proc.running) _proc.signal(9)
    else if (_proc) _proc.destroy()
    _proc = null
  }

  function _settle(run, ok) {
    if (_settled || run !== _run) return
    _settled = true
    watchdog.stop()
    _kill()
    var text = ok ? _buf : ""
    _buf = ""
    root.finished(ok, text)
  }

  Component {
    id: procComponent
    Process {
      id: proc
      property int runId: 0
      stdout: SplitParser {
        splitMarker: ""
        onRead: function(data) {
          if (root._settled || proc.runId !== root._run) return
          if (root._buf.length + data.length > root.maxChars) { root._settle(proc.runId, false); return }
          root._buf += data
        }
      }
      onExited: function(code, status) {
        // Let the last chunk of output be delivered before reporting, then go.
        Qt.callLater(function() { root._settle(proc.runId, code === 0); proc.destroy() })
      }
    }
  }

  Timer {
    id: watchdog
    interval: root.timeoutMs
    onTriggered: root._settle(root._run, false)
  }
}
