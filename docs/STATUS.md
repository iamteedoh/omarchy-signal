# Status (2026-09-08)

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

## Known issues and gotchas

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

## Not done

- Calls, stories, link previews, voice-note recording, stickers sending.
- Publishing to plugins.omarchy.org (needs a public repo and the
  `omarchy-plugin` topic).
