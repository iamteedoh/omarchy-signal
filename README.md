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
  groups, history, quotes, reactions, attachments, typing indicators, read
  receipts. Images render inline through the kitty graphics protocol in
  Ghostty, kitty, WezTerm and Konsole; URLs and attachments are clickable
  OSC 8 hyperlinks. Falls back to text on other terminals.
- **Popup notifications** (Quickshell): an incoming message slides in at the
  top right. Click it and a reply window opens front and centre with the
  recent thread and a text field. Middle-click opens the conversation in the
  terminal; right-click dismisses.
- **Bar widget**: Signal glyph with a pulsing unread dot and count. Click for a
  keyboard-driven list of conversations and contacts (type to filter, Enter
  opens it in the terminal).
- **Command line**: `omarchy-signal send "Trinity" -m "on my way"`,
  `omarchy-signal send +15550002222 -a photo.jpg`, `conversations`, `contacts`,
  `history`, `status`, `doctor`.
- **Keybinding**: `SUPER+SHIFT+G` (Omarchy's Signal key) opens the client, and
  there is a "Signal (terminal)" row in the Omarchy menu.
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
./install.sh          # or ./install.sh --link for a hot-reloading dev checkout
omarchy-signal link   # scan the QR code with Signal on your phone
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
| `Ctrl-A` | attach a file |
| `Ctrl-R` / `Ctrl-Q` | react to / quote the last message |
| `Ctrl-O` | open the last link or attachment (`xdg-open`) |
| `Ctrl-E` | mute conversation |
| `?` | help · `Ctrl-C` quit |

Clicking a link or attachment name also opens it in terminals with mouse support.

## Configuration

`~/.config/omarchy-signal/config.toml` — see [`docs/config.example.toml`](docs/config.example.toml)
for every option (read receipts, history retention, notification style and
preview, inline images, device name).

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
