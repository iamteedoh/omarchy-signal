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

## Known issues and gotchas

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

- Calls, stories, link previews, voice-note recording, stickers sending.
