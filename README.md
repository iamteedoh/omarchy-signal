# omarchy-signal

Signal messenger for the [Omarchy](https://omarchy.org) desktop: a terminal
client that renders images and links inline, popup notifications you can
answer in place, a tabbed conversation window you can park in the scratchpad,
and a bar widget with your unread count. Everything follows the active
Omarchy theme.

```
 ◢ SIGNAL // OMARCHY   ◉ SECURE                          2 unread ▸ lumon ▸ IMG
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ▶ ● Trinity           2 │ Trinity  +15550002222
     wake up, neo…       │ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
   ● Morpheus            │              ┈┈ Tuesday, 08 September 2026 ┈┈
     the red pill        │ Trinity · 00:41
   ● Nebuchadnezzar crew │   wake up, neo…
     Tank: ready         │   🖼 matrix.png · 213.0KB
                         │ You · 00:42 ✓✓
                         │   I know kung fu. 🥋
                         │ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌
                         │ ▌ Message… (Enter to send, Alt-Enter for a new line)
 Tab list  Ctrl-U contacts  / search  Ctrl-A attach  Ctrl-G message  Ctrl-T chat
```

## What you get

**Terminal client** (`omarchy-signal tui`, `SUPER+SHIFT+G`)
- Conversations, contacts and groups, history with day separators, delivery and
  read ticks, typing indicators, disappearing-message marks, `@mentions`.
- Images inline through the kitty graphics protocol (Ghostty, kitty, WezTerm);
  URLs and attachments are clickable OSC 8 hyperlinks; text-only elsewhere.
- Emoji shortcodes (`:smile:`) with a picker as you type, and emoticons
  (`:D`, `:)`, `<3`, …); both convert after the space, or send raw (setting).
- Message actions on any message: react, quote/reply, edit, delete for
  everyone, forward, copy, info. Attachment menu: open larger, save, save as,
  copy path, show/hide inline.
- Conversation menu: disappearing timer, mute, archive, block, message
  requests, safety number with verification, group members, rename, leave,
  new group, Note to Self.
- Attach files with a path prompt that completes as you type, lists the folder,
  and previews the highlighted image.
- Settings screen (`Ctrl-S`) grouped like Signal's own client.

**Popups and the chat window** (Quickshell)
- An incoming message slides in top right. Click it: a conversation view opens
  front and centre with the thread, images, reply-to-message, reactions,
  attachments (thumbnail picker), emoji, typing indicator.
- **Detach** it, or pick a chat from the bar, and it lives in the **chat
  window**: one real window with a tab per conversation, so Hyprland can float
  it, tile it, or park it in the scratchpad (`SUPER+ALT+S`, back with
  `SUPER+S`). Tabs show unread dots; `Ctrl+Tab` cycles them.
- Kinetic trackpad and wheel scrolling with a speed setting.
- Notification content levels (name and message / name only / nothing),
  Do Not Disturb awareness, optional sound.

**Bar widget**: Signal glyph with a pulsing unread dot and count. Click for a
searchable list of conversations and contacts (also `SUPER+CTRL+G`): `Enter`
opens the chat window, `Ctrl+Enter` or right-click the terminal client.

**Command line**: send, react, list, history, status, doctor, settings.

## How it works

```
 phone ──(Signal protocol, E2EE)──► signal-cli ◄──stdio JSON-RPC──► bridge ◄──unix socket──┬── tui / cli
                                    (keys live here)                 (history, sanitising)   └── Quickshell plugin
```

- [`signal-cli`](https://github.com/AsamK/signal-cli) implements the Signal
  protocol with Signal's own `libsignal` and is the only process that holds
  keys or does cryptography. This computer is linked as a secondary device,
  exactly like Signal Desktop.
- The **bridge** (`omarchy-signal bridge`, a systemd user service) runs
  `signal-cli` as a child in JSON-RPC mode, records messages in an owner-only
  SQLite database, and serves clients on a private Unix socket in
  `$XDG_RUNTIME_DIR`. Every string from the network is sanitised before it is
  stored or forwarded; every request from a client is schema-validated.
- The **terminal client**, the **CLI** and the **Quickshell plugin** are thin
  clients of the bridge. The plugin drives it through `omarchy-signal events`
  (a JSON line stream) and the CLI, always as argv arrays, never shell strings.

## Install

Requirements: Omarchy 4.x (Quattro), Python ≥ 3.11 (ships with Omarchy),
`signal-cli` from the AUR, `qrencode` (linking) and ImageMagick (image
conversion). The installer offers to install the last three. Inline images in
the terminal need Ghostty, kitty or WezTerm as Omarchy's default terminal
(`omarchy default terminal ghostty`) or `terminal = "ghostty"` in the config.

```bash
git clone https://github.com/iamteedoh/omarchy-signal ~/git/omarchy-signal
cd ~/git/omarchy-signal
./install.sh          # also fine from ~/.config/omarchy/plugins/iamteedoh.signal after `omarchy plugin add`
                      # (--link symlinks the checkout instead; see docs/STATUS.md before using it)
omarchy-signal link   # a QR code pops up on screen; scan it with Signal on your phone
omarchy-signal tui
```

`install.sh` load-checks the QML against your shell, copies the plugin to
`~/.config/omarchy/plugins/iamteedoh.signal`, links `~/.local/bin/omarchy-signal`,
installs and (re)starts the `omarchy-signal` user service, adds the
keybindings and the menu row, enables the bar widget, installs a post-update
check, and restarts the shell so the notification service picks up new code.
`./uninstall.sh` reverses all of it (`--purge` also deletes history). Once the
repo is public, `omarchy plugin add <git-url>` works too; then run
`install.sh` from the plugin directory for the CLI, service and keybindings.

## Keyboard shortcuts

### Desktop (Hyprland)

| Key | Action |
|-----|--------|
| `SUPER+SHIFT+G` | open or focus the terminal client |
| `SUPER+CTRL+G` | new conversation: the bar's searchable contact picker |
| `SUPER+ALT+S` / `SUPER+S` | park the chat window in the scratchpad / bring it back (Omarchy defaults) |

### Terminal client

| Key | Action |
|-----|--------|
| `Enter` / `Alt-Enter` | send / new line |
| `Tab` | focus the conversation list (`j`/`k`, `Enter`; `w` detaches; `m` mutes) |
| `Alt-↑/↓`, `Ctrl-N/P`, `Alt-1…9` | switch conversation |
| `PgUp`/`PgDn`, mouse wheel | scroll, loads older history |
| `Ctrl-U` | contacts and groups (start a conversation; "me" is Note to Self) |
| `/` | search history |
| `Ctrl-A` | attach: path prompt with Tab completion, folder listing and image preview |
| `Ctrl-G` | pick any message: react, quote, edit, delete for everyone, forward, copy, open, info |
| `Ctrl-R` / `Ctrl-Q` | react to / quote the last message |
| `Ctrl-O`, or click an attachment or picture | open larger, save to `~/Downloads`, save as…, copy path, show/hide inline |
| `Ctrl-T` | conversation: detach into the chat window, disappearing messages, mute, archive, block, message request, safety number (`v` verifies), group members/rename/leave, new group, archived chats |
| `Alt-D` | detach this chat into the chat window |
| `Ctrl-E` | mute conversation |
| `:smile:` / `:D` | emoji shortcodes and emoticons convert after the space (setting); a picker opens as you type |
| `Ctrl-S` / `F2` | settings |
| `Ctrl-Z` | put a failed message back in the composer |
| `Ctrl-X` / `Ctrl-L` | clear composer / redraw |
| `?` | help · `Ctrl-C` quit |

### Popup and chat window

| Key or mouse | Action |
|--------------|--------|
| click a message | action row: Reply · React (quick emojis) · Copy |
| right-click a message | reactions |
| middle-click a message | reply to it |
| click a picture | open it larger |
| `Enter` | send (shortcodes and emoticons convert after the space) |
| `Ctrl+O` / `Ctrl+Shift+A` / 󰁦 | attachment picker: arrows move, `Enter` attaches, `Backspace` on an empty filter goes up a folder |
| `Ctrl+Tab` / `Ctrl+Shift+Tab`, click a tab | switch conversation tab; ✕ closes a tab |
| `Esc` | closes one layer at a time: picker → message actions → quote → window |
| Detach / Terminal buttons | move the popup into the chat window / open the terminal client |

### Bar panel

| Key or mouse | Action |
|--------------|--------|
| type | filter conversations or contacts |
| `↑`/`↓`, `PgUp`/`PgDn` | move |
| `Enter`, click | open in the chat window |
| `Ctrl+Enter`, right-click | open in the terminal client |
| `Tab` | switch between conversations and contacts |
| `Esc` | clear the filter, then close |

## Settings

`Ctrl-S` in the client, or `omarchy-signal settings [key [value]]`. Live keys
apply immediately to the client, the bridge and the popups; the few marked
(restart) need `systemctl --user restart omarchy-signal` (the settings screen
offers `r`). File: `~/.config/omarchy-signal/config.toml`
([`docs/config.example.toml`](docs/config.example.toml)).

| Section | Key | Values / meaning |
|---------|-----|------------------|
| Notifications | `notifications` | `popup` (click-to-reply toast) · `system` · `off` |
| | `notification_content` | `name-and-message` · `name-only` · `none` (like Signal) |
| | `notification_timeout_ms` | how long a popup stays |
| | `respect_dnd` | no popups while Omarchy's Do Not Disturb is on |
| | `notification_sound` | audio file played with a popup (empty = silent) |
| Privacy | `send_read_receipts` | also what clears the notification on your phone |
| | `typing_indicators` | send and show |
| | `trust_new_identities` | `on-first-use` · `always` · `never` (restart) |
| Chats & media | `inline_images` | `always` · `click` · `never` |
| | `download_attachments` | auto-fetch attachments (restart) |
| | `attachment_thumbnails` | tiles or a plain list in the picker |
| | `save_dir` | where the attachment menu saves |
| | `emoji_autoconvert` | `:smile:` and `:D` → emoji; off sends them raw |
| | `message_layout` | `left` · `bubbles` |
| Appearance | `scroll_speed` | chat window trackpad/wheel multiplier (0 = Qt default) |
| | `terminal` | `auto` (Omarchy default) · `ghostty` · `kitty` · `wezterm` · `foot` · `alacritty` |
| | `terminal_images`, `image_max_rows` | inline image support and height |
| | `qr_style` | linking QR: `auto` (shell popup) · `shell` · `image` · `half` · `quad` · `braille` |
| Data | `history_enabled`, `history_retain_days` | local history (restart), retention |
| | `device_name` | shown in Signal's Linked devices (restart) |

## Command line

```bash
omarchy-signal tui [chat] [--new]          # terminal client (--new: contact picker)
omarchy-signal open [chat] [--new]         # open or focus the client in a terminal window
omarchy-signal window [chat]               # open a chat in the tabbed chat window
omarchy-signal send "Trinity" -m "on my way" [-a photo.jpg] [--quote-ts …]
omarchy-signal react <chat> <ts> <author> 🔥
omarchy-signal conversations | contacts [--refresh] | history <chat> -n 30
omarchy-signal mark-read <chat>            # also clears the phone's notification
omarchy-signal link [--force] [--qr-style …]
omarchy-signal settings [key [value]]
omarchy-signal status | doctor | demo
```

Recipients accept a contact name, `+number`, `username.NN`, `group:ID`, a
conversation key, or `me` for Note to Self. Add `--json` for machine output.

## Surviving Omarchy updates

Everything the installer places lives under your home directory, which
`omarchy update` does not rewrite. The installer also drops a post-update hook
(`~/.config/omarchy/hooks/post-update.d/omarchy-signal`) that re-checks the
plugin still loads against the new shell and that the bridge service is
enabled, and raises a critical notification if anything needs attention;
`omarchy-signal doctor` gives the details. A manual `omarchy refresh shell`
resets `shell.json` (dropping the bar widget); re-run `./install.sh` after it.

## Security

See [SECURITY.md](SECURITY.md) for the threat model, what is and is not
protected, and how to report a problem. Short version: end-to-end encryption
is `libsignal`'s, untouched; this project's job is to never let a message
from a stranger do anything to your terminal, your shell or your files.

## Tests

```bash
make test          # python unit + integration tests (fake signal-cli), node tests, shell checks, QML load check
make security      # the adversarial subset: terminal injection, path traversal, protocol abuse
```

## Status

Works against a real account on this machine (linking, sending, receiving,
attachments, receipts). Private for now; not on plugins.omarchy.org yet.
See [`docs/STATUS.md`](docs/STATUS.md) for known issues.

## License

MIT.
