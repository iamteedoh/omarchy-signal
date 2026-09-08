# omarchy-signal

Signal messenger for the [Omarchy](https://omarchy.org) desktop: a terminal
client that renders images and links inline, popup notifications you can
answer without leaving what you are doing, and a bar widget with your unread
count. It follows whichever Omarchy theme is active.

```
 ◢ SIGNAL // OMARCHY   ◉ SECURE                          2 unread ▸ lumon ▸ IMG
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ▶ ● Trinity           2 │ Trinity  +15550002222
     wake up, neo…       │ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
   ● Morpheus            │              ┈┈ Tuesday, 08 September 2026 ┈┈
     the red pill        │ Trinity · 00:41
   ● Nebuchadnezzar crew │ ▏ wake up, neo…
     Tank: ready         │ ▏ 🖼 matrix.png · 213.0KB
                         │                                          You · 00:42 ✓✓
                         │                                     ▏ I know kung fu.
                         │ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
                         │ ▌ Message… (Enter to send, Alt-Enter for a new line)
 Tab list  Ctrl-U contacts  / search  Ctrl-A attach  Ctrl-R react  ?  help
```

## What you get

- **Terminal client** (`omarchy-signal tui`): conversations, contacts and
  groups, history, quotes, reactions on any message, edit and delete-for-
  everyone, forward, message info, attachments with an action menu,
  emoji shortcodes (`:smile:` with a picker), typing indicators, read receipts
  that also clear the phone, disappearing-message timers, mute, archive,
  block, message requests, safety numbers with verification, group creation,
  renaming and leaving, Note to Self, `@mentions`, and a Signal-style
  settings screen. Images render inline through the kitty graphics protocol in
  Ghostty, kitty, WezTerm and Konsole; URLs and attachments are clickable
  OSC 8 hyperlinks. Falls back to text on other terminals, so make one of
  those Omarchy's default (`omarchy default terminal ghostty`) or set
  `terminal = "ghostty"` in the config; `omarchy-signal doctor` checks this.
- **Popup notifications** (Quickshell): an incoming message slides in at the
  top right. Click it and a conversation window opens front and centre: the
  thread with images, reply-to-message (click a bubble, or middle-click it),
  reactions, attachments via Omarchy's file menu, emoji shortcodes.
  "Detach" turns it into a real window (`omarchy-signal window <chat>` does
  too) that Hyprland floats, tiles or parks in the scratchpad
  (`SUPER+ALT+S` / `SUPER+S`) like any app, so a chat can stay open without
  the whole client. Middle-click on the toast opens the terminal; right-click
  dismisses.
- **Bar widget**: Signal glyph with a pulsing unread dot and count. Click for a
  keyboard-driven list of conversations and contacts: type to filter, Enter
  opens the chat in its own window, Ctrl+Enter (or right-click) in the
  terminal client.
- **Command line**: `omarchy-signal send "Trinity" -m "on my way"`,
  `omarchy-signal send +15550002222 -a photo.jpg`, `conversations`, `contacts`,
  `history`, `status`, `doctor`.
- **Keybindings**: `SUPER+SHIFT+G` (Omarchy's Signal key) opens the client,
  `SUPER+CTRL+G` opens the contact picker for a new conversation from
  anywhere, and there is a "Signal (terminal)" row in the Omarchy menu.
  Inside the client, `Ctrl-U` starts a new conversation; "Note to Self" is
  the first entry.
- **Theme**: colours come from `~/.local/state/omarchy/current/theme/colors.toml`
  in the terminal and from the shell's `Color`/`Style` singletons in Quickshell,
  so `omarchy theme set …` restyles everything live.

## How it works

```
 phone ──(Signal protocol, E2EE)──► signal-cli ◄──stdio JSON-RPC──► bridge ◄──unix socket──┬── tui / cli
                                    (keys live here)                 (history, sanitising)   └── Quickshell plugin
```

- [`signal-cli`](https://github.com/AsamK/signal-cli) implements the Signal
  protocol with Signal's own `libsignal`. It is the only process that holds
  keys or does cryptography. This project links your computer as a secondary
  device, exactly like Signal Desktop.
- The **bridge** (`omarchy-signal bridge`, a systemd user service) runs
  `signal-cli` as a child in JSON-RPC mode, records messages in an owner-only
  SQLite database, and serves clients on a private Unix socket in
  `$XDG_RUNTIME_DIR`. Every string from the network is sanitised before it is
  stored or forwarded; every request from a client is schema-validated.
- The **TUI**, the **CLI** and the **Quickshell plugin** are thin clients of the
  bridge. The plugin drives it through `omarchy-signal events` (a JSON line
  stream) and `omarchy-signal send`, always as argv arrays, never shell strings.

## Install

Requirements: Omarchy 4.x (Quattro), Python ≥ 3.11 (ships with Omarchy),
`signal-cli` from the AUR, `qrencode` (linking) and ImageMagick (image
conversion). The installer offers to install the last three.

```bash
git clone <this repo> ~/git/omarchy-signal
cd ~/git/omarchy-signal
./install.sh          # (--link symlinks the checkout instead; see docs/STATUS.md before using it)
omarchy-signal link   # a QR code pops up on screen; scan it with Signal on your phone
omarchy-signal tui
```

`install.sh` copies the plugin to `~/.config/omarchy/plugins/iamteedoh.signal`,
links `~/.local/bin/omarchy-signal`, installs and starts the
`omarchy-signal` user service, rebinds `SUPER+SHIFT+G`, adds the menu entry
and enables the bar widget. `./uninstall.sh` reverses all of it
(`--purge` also deletes history). Once the repo is public,
`omarchy plugin add <git-url>` works too; then run `install.sh` from the
plugin directory for the CLI, service and keybinding.

## Keys (terminal client)

| Key | Action |
|-----|--------|
| `Enter` / `Alt-Enter` | send / new line |
| `Tab` | focus the conversation list (`j`/`k`, `Enter`) |
| `Alt-↑/↓`, `Ctrl-N/P`, `Alt-1…9` | switch conversation |
| `PgUp`/`PgDn`, mouse wheel | scroll, loads older history |
| `Ctrl-U` | contacts and groups (start a conversation) |
| `/` | search history |
| `Ctrl-A` | attach a file (path prompt with Tab completion and a file list) |
| `Ctrl-R` / `Ctrl-Q` | react to / quote the last message |
| `Ctrl-G` | pick any message: react, quote, edit, delete for everyone, forward, copy, info |
| `Ctrl-T` | conversation: disappearing messages, mute, archive, block, safety number, group members/rename/leave, new group |
| `:smile:` / `:D` | emoji shortcodes and emoticons convert after the space (setting: "Convert :smile: and :D"); a picker opens as you type |
| `Ctrl-O`, or click an attachment | open it larger, save to `~/Downloads`, save as…, or copy its path |
| `Ctrl-E` | mute conversation |
| `?` | help · `Ctrl-C` quit |

Clicking a link or attachment name also opens it in terminals with mouse support.

## Settings

Press `Ctrl-S` (or `F2`) in the client for a settings screen grouped like
Signal's own: **Notifications** (popup / system / off, and notification
content: name and message, name only, or nothing; honour Omarchy's Do Not Disturb), **Privacy** (read
receipts, typing indicators, safety-number policy), **Chats & media** (show
images always / on click / never, auto-download, save folder, layout),
**Appearance** (terminal, image height, QR style) and **Data** (history,
retention). Changes apply immediately to the client, the bridge and the shell
popups; the few that need a bridge restart say so and `r` restarts it.

The same settings from the command line:

```bash
omarchy-signal settings                              # list
omarchy-signal settings notification_content none    # like Signal's "No name or content"
omarchy-signal settings inline_images click
```

They live in `~/.config/omarchy-signal/config.toml`
([`docs/config.example.toml`](docs/config.example.toml) documents every key).

## Security

See [SECURITY.md](SECURITY.md) for the threat model, what is and is not
protected, and how to report a problem. Short version: end-to-end encryption
is `libsignal`'s, untouched; this project's job is to never let a message
from a stranger do anything to your terminal, your shell or your files.

## Tests

```bash
make test          # python unit + integration tests (fake signal-cli), node tests for Model.js, shell checks
make security      # the adversarial subset: terminal injection, path traversal, protocol abuse
```

## Status

Early. Works end-to-end against a fake `signal-cli` in the test suite; the
real-account path (linking, receiving, sending, attachments) needs a live
device to exercise. Not on plugins.omarchy.org yet.

## License

MIT.
