# Status — 2026-09-08 (overnight build)

## Done and verified on this machine

- Bridge daemon runs as the `omarchy-signal` systemd user unit against the real
  `signal-cli` 0.14.6 (`signal-cli-native-bin` from the AUR, installed tonight).
  It reports "connected, not linked" because no account has been linked yet.
- Terminal client starts in a real pty, draws the boot screen, header, panes,
  overlays (contacts, search, attach, react, help, quit) and the device-link QR
  overlay, and exits cleanly. Themed from the current Omarchy theme (Lumon).
- Quickshell plugin `iamteedoh.signal` is installed (copy mode), validated by
  `omarchy plugin validate`, enabled in the bar's right section, and answers
  IPC (`omarchy-shell iamteedoh.signal state|toasts|close|dismiss|demo`).
  `omarchy-signal demo` produced a toast layer surface (380×89, top right) and
  `omarchy-shell iamteedoh.signal reply number:+15550000000` produced the
  centered reply surface, both confirmed via `hyprctl layers`.
- Keybinding `SUPER+SHIFT+G` → terminal client (block in
  `~/.config/hypr/bindings.lua` between `-- BEGIN/END omarchy-signal`), and a
  "Signal (terminal)" row in `~/.config/omarchy/extensions/omarchy-menu.jsonc`.
- Tests: `make test` → 91 Python tests (unit + integration with a fake
  signal-cli), 8 node tests, shell static checks, manifest validation. All green.

## Not verified (needs a real account)

- Linking (`omarchy-signal link`), receiving, sending, attachments, receipts,
  typing, reactions and groups against the live Signal network. The JSON-RPC
  shapes follow the signal-cli 0.14 manual and were probed locally, but field
  names in real envelopes may need small adjustments (`envelope.py` is the one
  place to fix them).
- The visual look of the toast/reply window was not screenshotted: the display
  was off (DPMS) so `grim` blocked. First thing in the morning:
  `omarchy-signal demo` and click the popup.

## Known issues

- The shell's hot-reload recreates bar widgets and panels only; `Service.qml`
  (toasts, reply window, QR popup) is a `keepLoaded` service and only reloads
  on `omarchy restart shell`. `install.sh` now restarts the shell.

- **Omarchy shell crashes on plugin hot-reload (upstream).** Every write under
  `~/.config/omarchy/plugins/` reloads all user plugins; on this machine the
  cloned lock service (`tito.lock`) then hits Quickshell's fatal "Tried to show
  lockscreen surfaces without active lock" (coredumps 01:24:08, 01:25:53,
  01:29:40). Quickshell restarts itself within a second. Not caused by this
  plugin's QML; it happens on `omarchy-shell shell rescanPlugins` too. Worth
  reporting to omacom/omarchy with the log sequence in
  `~/.cache/quickshell/crashes/2gmo85u0lt/log.qslog.log`. Until then: prefer
  copy-mode installs and avoid `./install.sh --link`.
- `signal-cli` prints a harmless "Fatal error: java.lang.InterruptedException"
  on exit (GraalVM shutdown hook); the bridge filters it from the log.

## Next steps

1. `omarchy-signal link` → scan with the phone → `omarchy-signal tui`.
2. Send yourself a message from the phone: expect a toast, click → reply.
3. Adjust `envelope.py` if any real envelope field differs; add a captured
   (redacted) envelope to `tests/python/test_envelope.py`.
4. Screenshot the toast and reply window for the README.
5. Decide whether to publish (plugins.omarchy.org needs a public repo, README,
   license and the `omarchy-plugin` topic).
