# Status (2026-11-12)

## Verified on this machine (real account)

Linking (QR popup), receiving, sending, attachments both ways, read receipts
(they also clear the phone), typing indicators, reactions, quotes, edits,
delete-for-everyone, Note to Self, the popup and the tabbed chat window
(float, scratchpad round trip, WM close and reopen), the bar picker, the
terminal client with inline images in Ghostty, settings live-reload, and the
post-update hook. `make test` runs 120+ Python tests (fake signal-cli), the
node tests for the shared JS, shell static checks and a QML load check.

Visual behaviour in the chat window (send flicker, scroll pinning) was
verified with burst screenshot captures of real sends, not by inspection.

## Checked only against the demo bridge

- **The instrument-panel look and the telemetry** (OMSIG-2026-1113-0004): the
  chat window, the popups, the bar panel and the terminal client's telemetry
  line were rendered offscreen against the fake `signal-cli` in three themes
  and at several widths, and their figures are unit tested. They have not yet
  been run against a real account in the live shell.

- **Voice calls** (OMSIG-2026-1113-0005): the whole flow (placing, answering,
  declining, a call answered on another device, a second caller while you
  talk, muting, switching devices, signal-cli dying mid-call) is tested
  against the fake `signal-cli` with a fake sound server. The audio routing
  and both level meters were run against the real PipeWire and the real
  `signal-call-tunnel` devices on this machine, with no call behind them. The
  call panel was rendered offscreen with drawn levels. **No real call has been
  placed or answered yet**: that needs a second person, and it is the first
  thing to check, together with whether the systemd unit's sandbox lets the
  tunnel connect (`AF_NETLINK` was added for it; `RestrictRealtime=yes` is
  still on and may cost audio thread priority).

## Known issues and gotchas

- **Omarchy seeds the Quake console with the default agent** every time the
  console is created, including when a window is moved onto an empty console
  (checked on Hyprland 0.56; Omarchy's comment says it should not). Sending the
  chat window there therefore brings the agent along. Not ours to fix; the
  README's top warning has a `hyprland.lua` wrap that skips that one launch.
- **The chat window floats, and the Quake console only lays out tiled
  windows.** `config/bindings.lua` tiles it when it lands on
  `special:qconsole` and floats it again when it leaves. A Hyprland reload
  forgets which window it tiled, so one moved off after a reload stays tiled
  (`SUPER+T`).
- **Calls are voice only, one person at a time.** `signal-cli` sends every
  offer as an audio call and `signal-call-tunnel` has no video path, so video,
  group calls, screen sharing and call links are not possible without a
  different media engine. An incoming video call is answered as a voice call.
- **A call answered on your phone keeps signal-cli's side alive for up to a
  minute.** signal-cli ignores the "answered elsewhere" message and waits for
  its own ring timeout. The bridge stops ringing at once and does not hang up,
  because a hangup from here would go to the caller. A new call cannot be
  placed from this computer until that timeout has passed.
- **Call telemetry is what can be measured outside the tunnel**: state, times,
  drops, and levels read from the call's two audio devices. Packet loss,
  jitter, bitrate and round trip are inside RingRTC and the tunnel does not
  report them.
- **The level meters and the waveforms come from `parec`** at 8 kHz in 50 ms
  frames; the clients are sent fifteen readings a second.
- **A device name is shown as PipeWire's node name** in the call panel's
  footer; the picker shows the readable description.

- **Telemetry looks at the newest 240 messages of a conversation.** Reply times,
  delivery counts and the busiest hour describe that window, not the whole
  history. A reply that takes more than 12 hours counts as a new conversation
  and is left out of the reply time.
- **signal-cli keeps only a contact's current key.** `listIdentities` returns one
  entry a contact (checked on 0.14.10), dated by `addedTimestamp`. The rail
  therefore says "Key changed" when the conversation's history is older than
  the key, not by counting stored keys. Until this branch the bridge read a
  field called `addedDate`, which signal-cli never sends, so every key looked
  undated and the key age never showed.
- **"Transit" is the gap between the sender's clock and this computer's.** It
  is shown only when that gap is under five minutes; a phone with a wrong clock
  shows none.

- **Omarchy shell may abort on plugin hot-reload (upstream).** Writing under
  `~/.config/omarchy/plugins/` reloads every user plugin; a cloned copy of the
  lock service (as some users keep) then sometimes trips Quickshell's fatal
  "Tried to show lockscreen surfaces without active lock". Quickshell restarts
  itself. `install.sh` therefore copies (never symlinks) and restarts the shell
  once; avoid `--link`.
- **Services do not hot-reload.** `Service.qml` (popups, chat window) only
  reloads on a shell restart; `install.sh` does that.
- **Hyprland 0.56 dispatchers are Lua** (`hl.dsp.window.float({...})`); the
  classic `setfloating address:…` form is rejected, and an `o.window` rule did
  not float the Quickshell window, so the chat window floats itself after
  mapping via `omarchy-signal float-window`.
- **Qt list views scroll by raw trackpad deltas**, which Omarchy scales to 0.4×;
  the chat window applies its own multiplier (`scroll_speed`) with inertia.
- **`positionViewAtEnd()` blanks the list for a few frames**; the chat window
  scrolls by setting `contentY`.
- `signal-cli` prints a harmless GraalVM "InterruptedException" on exit; the
  bridge filters it.

- **Qt 6.12 has a `Color` type of its own.** A bare `Color.accent` in a plugin
  can resolve to it instead of Omarchy's palette, and then the file loads with
  every colour undefined. Write `Commons.Color` (as Omarchy's own QML does);
  `make test` refuses a bare one and `scripts/qml-check.sh` fails on undefined
  colours.
- **`omarchy plugin update` does not run a plugin's installer.** The service
  notices the plugin's version changing on disk, runs `omarchy-signal sync`
  and offers a shell restart. The plugin itself only checks for releases;
  `omarchy-signal update` starts `omarchy plugin update iamteedoh.signal`.
- **Qt 6.12.0 reads a clipboard offer whole whenever the clipboard changes**,
  for any editable text field. Fields are read-only unless focused and on
  screen, and paste goes through `omarchy-signal paste` (`PasteGuard.qml`).

## Not done

- Video calls, group calls, screen sharing, stories, link previews, voice-note recording, stickers sending.
