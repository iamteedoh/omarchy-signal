"""``omarchy-signal`` command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import re
import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .client import BridgeClient, BridgeError, BridgeUnavailable
from .config import Config, Paths
from .sanitize import clean_text


def _paths() -> Paths:
    return Paths()


async def _with_client(fn, *, subscribe: bool = False):
    client = BridgeClient(_paths())
    try:
        hello = await client.connect()
        if subscribe:
            await client.request("subscribe")
        return await fn(client, hello)
    finally:
        await client.close()


def _print_json(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _safe_print(text: str) -> None:
    """Everything printed to a terminal from remote data goes through here."""
    sys.stdout.write(clean_text(text) + "\n")


# ----------------------------------------------------------------------------- commands

def cmd_tui(args) -> int:
    from .tui import run_tui
    return run_tui(Config.load(_paths()), _paths(), initial=args.conversation or "", new=bool(args.new))


def cmd_send(args) -> int:
    text = args.message
    if text is None and not sys.stdin.isatty():
        text = sys.stdin.read()
    if text is None and not args.attachment:
        print("omarchy-signal send: give a message with -m, on stdin, or an attachment with -a", file=sys.stderr)
        return 2

    async def go(client, hello):
        res = await client.request("resolve", query=args.recipient)
        if "key" not in res:
            names = ", ".join(c["name"] for c in res.get("candidates", []))
            raise BridgeError(f"ambiguous recipient; candidates: {names}", "invalid")
        params = dict(conversation=res["key"], text=text or "",
                      attachments=[str(Path(a).expanduser()) for a in (args.attachment or [])])
        if args.quote_ts:
            params.update(quoteTs=args.quote_ts, quoteAuthor=args.quote_author or "", quoteText=args.quote_text or "")
        result = await client.request("send", **params)
        if args.json:
            _print_json({"conversation": res["key"], **result})
        else:
            print(f"sent to {clean_text(res['name'], single_line=True)} (ts {result['ts']}, {result['status']})")
        return 0 if result.get("status") == "sent" else 1
    return _run(go)


def cmd_react(args) -> int:
    async def go(client, hello):
        res = await client.request("resolve", query=args.conversation)
        if "key" not in res:
            raise BridgeError("ambiguous conversation", "invalid")
        await client.request("react", conversation=res["key"], ts=args.ts, author=args.author, emoji=args.emoji,
                             remove=bool(args.remove))
        if args.json:
            _print_json({"ok": True})
        return 0
    return _run(go)


def cmd_pick_file(args) -> int:
    """Print one file chosen with Omarchy's file menu (used by the shell windows)."""
    dirs = [d for d in (args.dirs or []) if os.path.isdir(d)] or [os.path.expanduser("~")]
    exe = shutil.which("omarchy-menu-file")
    if not exe:
        print("omarchy-menu-file not found", file=sys.stderr)
        return 1
    formats = "png jpg jpeg gif webp heic pdf txt md mp4 mov mp3 m4a ogg opus zip"
    res = subprocess.run([exe, "Attach", ":".join(dirs), formats], capture_output=True, text=True, timeout=300, check=False)
    path = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    if not path:
        return 1
    print(path)
    return 0


def cmd_float_window(args) -> int:
    """Float, size and centre the Hyprland window with the given title (used by
    the shell for detached conversation windows; window rules proved flaky)."""
    import json as _json
    import time as _time
    hyprctl = shutil.which("hyprctl")
    if not hyprctl:
        return 1
    wanted = args.title
    address = ""
    for _ in range(40):
        try:
            clients = _json.loads(subprocess.run([hyprctl, "clients", "-j"], capture_output=True, text=True, timeout=5, check=False).stdout or "[]")
        except ValueError:
            clients = []
        for c in clients:
            if c.get("class") == "org.quickshell" and (c.get("title") == wanted or c.get("initialTitle") == wanted):
                address = c.get("address", "")
                floating = c.get("floating", False)
                break
        if address:
            break
        _time.sleep(0.05)
    if not address:
        return 1
    w, h = max(320, args.width), max(240, args.height)
    if not re.fullmatch(r"0x[0-9a-f]+", address):
        return 1
    win = f'address:{address}'

    def dispatch(lua: str) -> None:
        # Hyprland ≥ 0.55 takes Lua dispatchers; the classic "setfloating address:…" form is refused.
        subprocess.run([hyprctl, "dispatch", lua], capture_output=True, timeout=5, check=False)

    if not floating:
        dispatch(f'hl.dsp.window.float({{ window = "{win}", action = "toggle" }})')
    dispatch(f'hl.dsp.window.resize({{ window = "{win}", x = {w}, y = {h} }})')
    dispatch(f'hl.dsp.window.center({{ window = "{win}" }})')
    dispatch(f'hl.dsp.focus({{ window = "{win}" }})')
    return 0


def cmd_window(args) -> int:
    """Open a conversation in its own window (Quickshell), detached from the client."""
    key = ""
    if args.conversation:
        async def go(client, hello):
            res = await client.request("resolve", query=args.conversation)
            if "key" not in res:
                raise BridgeError("ambiguous conversation", "invalid")
            return res["key"]
        try:
            key = asyncio.run(_with_client(go))
        except BridgeError as exc:
            print(f"omarchy-signal: {exc}", file=sys.stderr)
            return 1
    exe = shutil.which("omarchy-shell")
    if not exe:
        print("omarchy-shell not found", file=sys.stderr)
        return 1
    res = subprocess.run([exe, "iamteedoh.signal", "window", key], capture_output=True, text=True, timeout=5, check=False)
    if res.returncode != 0 or "ok" not in res.stdout:
        print("the shell did not open a window (is the Signal plugin enabled?)", file=sys.stderr)
        return 1
    return 0


def cmd_conversations(args) -> int:
    async def go(client, hello):
        convs = await client.request("conversations", includeArchived=bool(args.all))
        if args.json:
            _print_json(convs)
            return 0
        for c in convs:
            badge = f"[{c['unread']}]" if c.get("unread") else "   "
            _safe_print(f"{badge:>5} {c['name'][:32]:<32} {c['key']}")
        return 0
    return _run(go)


def cmd_contacts(args) -> int:
    async def go(client, hello):
        if args.refresh:
            await client.request("refresh")
        contacts = await client.request("contacts")
        groups = await client.request("groups")
        if args.json:
            _print_json({"contacts": contacts, "groups": groups})
            return 0
        for c in contacts:
            _safe_print(f"  {c['displayName'][:32]:<32} {c.get('number') or c.get('username') or c.get('uuid')}")
        for g in groups:
            _safe_print(f"  {g['name'][:32]:<32} group:{g['groupId']}")
        return 0
    return _run(go)


def cmd_history(args) -> int:
    async def go(client, hello):
        res = await client.request("resolve", query=args.conversation)
        if "key" not in res:
            raise BridgeError("ambiguous conversation", "invalid")
        msgs = await client.request("history", conversation=res["key"], limit=args.limit)
        if args.json:
            _print_json(msgs)
            return 0
        from datetime import datetime
        for m in msgs:
            when = datetime.fromtimestamp(m["ts"] / 1000).strftime("%Y-%m-%d %H:%M")
            who = "you" if m["outgoing"] else m["senderName"]
            body = m["body"] or ("[" + ", ".join(a.get("filename", "attachment") for a in m.get("attachments", [])) + "]" if m.get("attachments") else "")
            _safe_print(f"{when}  {who:>16}  {body}")
        return 0
    return _run(go)


def cmd_status(args) -> int:
    async def go(client, hello):
        status = await client.request("status")
        if args.json:
            _print_json(status)
            return 0
        print(f"bridge      : running (v{status['version']}, up {status['uptime']}s)")
        print(f"signal-cli  : {'connected' if status['connected'] else 'not running'}")
        print(f"account     : {'linked' if status['linked'] else 'NOT LINKED — run `omarchy-signal link`'}")
        print(f"unread      : {status['unread']}")
        print(f"history     : {'on' if status['historyEnabled'] else 'off'}")
        print(f"notifications: {status['notifications']}")
        if status.get("error"):
            print(f"last error  : {clean_text(status['error'], single_line=True)}")
        return 0 if status["connected"] else 1
    return _run(go)


def cmd_events(args) -> int:
    """Stream bridge events as JSON lines. The Quickshell plugin runs this."""
    async def go():
        stop = asyncio.Event()

        def on_event(name, data):
            _print_json({"event": name, "data": data})

        async def watch_parent():
            # The shell (or whoever started us) is our reason to exist. If it
            # dies, or crashes and restarts, we would linger holding a socket;
            # notice being reparented and leave.
            parent = os.getppid()
            while not stop.is_set():
                await asyncio.sleep(2)
                if os.getppid() != parent:
                    stop.set()
                    return

        watcher = asyncio.create_task(watch_parent())

        while not stop.is_set():
            client = BridgeClient(_paths(), on_event=on_event)
            try:
                hello = await client.connect()
                await client.request("subscribe")
                _print_json({"event": "unread", "data": {"total": hello.get("unread", 0)}})
                await asyncio.wait([asyncio.ensure_future(client.closed.wait()), asyncio.ensure_future(stop.wait())],
                                   return_when=asyncio.FIRST_COMPLETED)
            except BridgeUnavailable as exc:
                _print_json({"event": "status", "data": {"connected": False, "linked": False, "error": str(exc).split(";")[0]}})
                if args.once:
                    return 1
                await asyncio.sleep(3)
            finally:
                await client.close()
            if args.once or stop.is_set():
                break
            _print_json({"event": "status", "data": {"connected": False, "linked": False, "error": "bridge connection lost"}})
            await asyncio.sleep(1)
        watcher.cancel()
        return 0
    try:
        return asyncio.run(go())
    except (KeyboardInterrupt, BrokenPipeError):
        return 0


def cmd_mark_read(args) -> int:
    async def go(client, hello):
        res = await client.request("resolve", query=args.conversation)
        if "key" not in res:
            raise BridgeError("ambiguous conversation", "invalid")
        result = await client.request("markRead", conversation=res["key"])
        if args.json:
            _print_json(result)
        return 0
    return _run(go)


def cmd_link(args) -> int:
    from . import kitty, qr

    async def go(client, hello):
        if hello.get("linked") and not args.force:
            print("an account is already linked; pass --force to link another")
            return 1
        cfg = Config.load(_paths())
        res = await client.request("link", deviceName=args.name or cfg.device_name)
        uri = res["uri"]
        qr_rows = max(5, min(40, args.qr_size)) if args.qr_size else cfg.qr_rows
        print("Open Signal on your phone → Settings → Linked devices → Link new device, then scan:\n")
        style = args.qr_style or cfg.qr_style
        if args.text:
            style = "half"
        shown = ""
        if style in ("auto", "shell"):
            try:
                png_path = qr.qr_png_file(uri, _paths().run_dir)
                if qr.shell_show_qr(png_path):
                    shown = "shell"
                    print("  The QR code is on your screen (Omarchy shell). Esc hides it; the link keeps waiting.")
                    print("  Prefer it in the terminal? Ctrl-C and rerun with --qr-style image (or half).")
            except qr.QrUnavailable:
                shown = ""
        if not shown and style in ("auto", "image") and sys.stdout.isatty() and kitty.terminal_supports_graphics():
            try:
                import fcntl, struct, termios
                packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
                rows_, cols_, xpix, ypix = struct.unpack("HHHH", packed)
                cell_w = max(1, xpix // max(1, cols_)) if xpix else 10
                cell_h = max(1, ypix // max(1, rows_)) if ypix else 20
                seq, cols, rows = qr.qr_image_sequence(uri, kitty.image_id_for(uri), cell_w=cell_w, cell_h=cell_h, rows=qr_rows)
                sys.stdout.write("  " + seq + "\n" * (rows + 1))
                sys.stdout.flush()
                shown = "image"
            except (qr.QrUnavailable, OSError):
                shown = ""
        if not shown:
            text_style = style if style in qr.TEXT_STYLES else "half"
            try:
                # Dark modules on a light background, as a scanner expects.
                for line in qr.qr_text_lines(uri, text_style):
                    print("  \x1b[48;2;255;255;255m\x1b[38;2;0;0;0m" + line + "\x1b[0m")
            except qr.QrUnavailable as exc:
                print(f"({exc}; paste this into another QR tool)\n{uri}")
        print()
        print("After the scan the phone shows nothing until the link completes; that usually takes")
        print("10–60 seconds while keys are exchanged and contacts sync. Pull down to refresh")
        print("Linked devices afterwards if the new device is not listed yet.\n")

        started = asyncio.get_running_loop().time()
        task = asyncio.ensure_future(client.request("linkFinish", timeout=620))
        frames = "◐◓◑◒"
        i = 0
        import atexit
        if shown == "shell":
            atexit.register(qr.shell_hide_qr)   # killed or crashed: never leave the popup on screen
        try:
            while not task.done():
                elapsed = int(asyncio.get_running_loop().time() - started)
                sys.stdout.write(f"\r  {frames[i % 4]} waiting for the phone… {elapsed:>3}s  (Ctrl-C cancels)")
                sys.stdout.flush()
                i += 1
                await asyncio.wait({task}, timeout=0.25)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Ctrl-C: tidy up quietly. The pending link simply expires; nothing
            # on the account has changed.
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait({task}, timeout=1)
            if not task.cancelled() and task.done():
                task.exception()
            if shown == "shell":
                qr.shell_hide_qr()
            sys.stdout.write("\r" + " " * 60 + "\r")
            print("  Linking cancelled. Nothing was changed; run `omarchy-signal link` again any time.")
            raise
        sys.stdout.write("\r" + " " * 60 + "\r")
        if shown == "shell":
            qr.shell_hide_qr()
        fin = task.result()
        if not fin.get("linked"):
            print("linking did not complete")
            return 1
        number = clean_text(fin.get("number", ""), single_line=True)
        print(f"  ✓ linked as {number or 'your account'}")
        # The bridge restarts signal-cli for the new account and pulls the
        # directory; show that instead of returning to a silent prompt.
        sys.stdout.write("  ◌ syncing contacts and groups…")
        sys.stdout.flush()
        contacts = groups = 0
        for tick in range(120):
            await asyncio.sleep(1)
            try:
                status = await client.request("status")
                if status.get("linked") and status.get("connected"):
                    contacts = len(await client.request("contacts"))
                    groups = len(await client.request("groups"))
                    if contacts or groups or tick > 30:
                        break
            except BridgeError:
                pass
            sys.stdout.write(".")
            sys.stdout.flush()
        print(f"\r  ✓ {contacts} contacts, {groups} groups synced" + " " * 20)
        print("\nAll set. Open the client with `omarchy-signal tui` or SUPER+SHIFT+G.")
        return 0
    return _run(go)


def cmd_demo(args) -> int:
    """Show a sample popup (nothing is sent or stored)."""
    async def go(client, hello):
        await client.request("demo", text=args.text or "")
        print("demo popup sent to the shell")
        return 0
    return _run(go)


TERMINAL_ARGV = {
    # terminal -> argv prefix that sets the Wayland app id and runs a command
    "ghostty": ["ghostty", "--class=org.omarchy.signal", "-e"],
    "kitty": ["kitty", "--class", "org.omarchy.signal", "-e"],
    "wezterm": ["wezterm", "start", "--class", "org.omarchy.signal", "--"],
    "foot": ["foot", "--app-id=org.omarchy.signal", "--"],
    "alacritty": ["alacritty", "--class", "org.omarchy.signal", "-e"],
}


def cmd_open(args) -> int:
    """Open (or focus) the client in a terminal window. Used by the keybinding,
    the menu, the bar widget and the popup's "Open in terminal" button."""
    import shlex
    cfg = Config.load(_paths())
    me = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else "omarchy-signal"
    tui = [me, "tui"] + (["--new"] if args.new else []) + ([args.conversation] if args.conversation else [])
    if cfg.terminal != "auto" and shutil.which(cfg.terminal):
        launch = TERMINAL_ARGV[cfg.terminal] + tui
        if shutil.which("uwsm-app"):
            launch = ["uwsm-app", "--"] + launch   # same session scoping Omarchy uses for every app
    else:
        launch = ["omarchy-launch-tui", "--app-id=org.omarchy.signal"] + tui
    focus = shutil.which("omarchy-launch-or-focus")
    if focus:
        # omarchy-launch-or-focus <app-id> <command string>: focuses a window
        # with that app id, else runs the command.
        os.execv(focus, [focus, "org.omarchy.signal", " ".join(shlex.quote(a) for a in launch)])
    os.execvp(launch[0], launch)
    return 0


def cmd_settings(args) -> int:
    """List, read or change settings. Live keys reach the running bridge and
    shell at once; the rest are flagged as needing a bridge restart."""
    from .config import RESTART_REQUIRED, SETTINGS, coerce_setting, save_config, setting_spec
    paths = _paths()
    cfg = Config.load(paths)
    if args.key is None:
        section = None
        for spec in SETTINGS:
            if spec["section"] != section:
                section = spec["section"]
                print(f"\n{section}")
            value = getattr(cfg, spec["key"])
            flag = "  (restart)" if spec.get("restart") else ""
            print(f"  {spec['key']:<24} {str(value).lower() if isinstance(value, bool) else value}{flag}")
        print(f"\nchange one: omarchy-signal settings <key> <value>    file: {paths.config_file}")
        return 0
    spec = setting_spec(args.key)
    if spec is None:
        print(f"unknown setting: {args.key}", file=sys.stderr)
        return 2
    if args.value is None:
        value = getattr(cfg, args.key)
        print(str(value).lower() if isinstance(value, bool) else value)
        return 0
    try:
        new = coerce_setting(args.key, args.value)
    except ValueError as exc:
        print(f"omarchy-signal: {exc}", file=sys.stderr)
        return 2
    setattr(cfg, args.key, new)
    cfg = Config.from_dict({k: getattr(cfg, k) for k in cfg.__dataclass_fields__})   # re-run the clamps
    save_config(cfg, paths)
    print(f"{args.key} = {str(new).lower() if isinstance(new, bool) else new}")
    if args.key in RESTART_REQUIRED:
        print("takes effect after: systemctl --user restart omarchy-signal")
        return 0

    async def go(client, hello):
        res = await client.request("reloadConfig")
        if res.get("restartRequired"):
            print("pending a bridge restart: " + ", ".join(res["restartRequired"]))
        return 0
    try:
        return asyncio.run(_with_client(go))
    except BridgeUnavailable:
        print("(bridge not running; it will read the new value when it starts)")
        return 0


def cmd_bridge(args) -> int:
    from .bridge import main as bridge_main
    return bridge_main(["--stderr"] if args.stderr else [])


def cmd_doctor(args) -> int:
    paths = _paths()
    cfg = Config.load(paths)
    ok = True

    def check(label, good, hint=""):
        nonlocal ok
        ok = ok and bool(good)
        print(f"  [{'ok' if good else '!!'}] {label}" + (f"  → {hint}" if not good and hint else ""))

    print("omarchy-signal doctor")
    check("python ≥ 3.11", sys.version_info >= (3, 11), "install python")
    check(f"signal-cli on PATH ({cfg.signal_cli})", shutil.which(cfg.signal_cli) or Path(cfg.signal_cli).exists(),
          "omarchy pkg aur add signal-cli  (or signal-cli-native-bin)")
    check("qrencode (for linking)", shutil.which("qrencode"), "omarchy pkg add qrencode")
    check("ImageMagick (for image previews)", shutil.which("magick") or shutil.which("convert"), "omarchy pkg add imagemagick")
    check("XDG_RUNTIME_DIR set", bool(os.environ.get("XDG_RUNTIME_DIR")))
    check(f"bridge socket {paths.socket}", paths.socket.exists(), "systemctl --user enable --now omarchy-signal")
    theme = paths.omarchy_theme_dir / "colors.toml"
    check(f"omarchy theme {theme}", theme.exists())
    unit = subprocess.run(["systemctl", "--user", "is-active", "omarchy-signal"], capture_output=True, text=True, check=False)
    check("systemd user unit active", unit.stdout.strip() == "active", "systemctl --user enable --now omarchy-signal")
    plugin = Path.home() / ".config/omarchy/plugins/iamteedoh.signal/manifest.json"
    check("Quickshell plugin installed", plugin.exists(), "./install.sh")
    try:
        async def go(client, hello):
            return hello
        hello = asyncio.run(_with_client(go))
        check("bridge answers", True)
        check("signal-cli connected", hello.get("connected"), "journalctl --user -u omarchy-signal")
        check("account linked", hello.get("linked"), "omarchy-signal link")
    except BridgeUnavailable as exc:
        check("bridge answers", False, str(exc).split(";")[0])
    from .kitty import terminal_supports_graphics
    print(f"  [..] this terminal draws images: {'yes' if terminal_supports_graphics(probe=False) else 'no'}")
    default_term = ""
    try:
        default_term = subprocess.run(["omarchy-default-terminal"], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    chosen = cfg.terminal if cfg.terminal != "auto" else (default_term or "unknown")
    good = any(t in chosen for t in ("ghostty", "kitty", "wezterm"))
    check(f"client terminal can draw images ({chosen})", good,
          "omarchy default terminal ghostty   (or terminal = \"ghostty\" in config.toml)")
    return 0 if ok else 1


def _run(fn) -> int:
    try:
        return asyncio.run(_with_client(fn))
    except BridgeUnavailable as exc:
        print(f"omarchy-signal: {exc}", file=sys.stderr)
        return 3
    except BridgeError as exc:
        print(f"omarchy-signal: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


# ----------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omarchy-signal", description="Signal messenger for the Omarchy terminal and shell.")
    p.add_argument("--version", action="version", version=f"omarchy-signal {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("tui", help="open the terminal client")
    s.add_argument("conversation", nargs="?", help="contact name, number, group or conversation key to open")
    s.add_argument("--new", action="store_true", help="start with the contact picker open")
    s.set_defaults(fn=cmd_tui)

    s = sub.add_parser("open", help="open or focus the client in a terminal window")
    s.add_argument("conversation", nargs="?")
    s.add_argument("--new", action="store_true", help="start with the contact picker open (new conversation)")
    s.set_defaults(fn=cmd_open)

    s = sub.add_parser("send", help="send a message from the command line")
    s.add_argument("recipient", help="contact name, +number, username.NN or group:ID")
    s.add_argument("-m", "--message", help="message text (default: stdin)")
    s.add_argument("-a", "--attachment", action="append", help="file to attach (repeatable)")
    s.add_argument("--quote-ts", type=int, help="reply to the message with this timestamp")
    s.add_argument("--quote-author", help="conversation key of that message's author (number:+1…)")
    s.add_argument("--quote-text", help="its text, shown in the quote")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("react", help="react to a message")
    s.add_argument("conversation")
    s.add_argument("ts", type=int, help="message timestamp")
    s.add_argument("author", help="author key (number:+1…)")
    s.add_argument("emoji")
    s.add_argument("--remove", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_react)

    s = sub.add_parser("float-window", help=argparse.SUPPRESS)
    s.add_argument("title")
    s.add_argument("--width", type=int, default=720)
    s.add_argument("--height", type=int, default=640)
    s.set_defaults(fn=cmd_float_window)

    s = sub.add_parser("pick-file", help=argparse.SUPPRESS)
    s.add_argument("dirs", nargs="*")
    s.set_defaults(fn=cmd_pick_file)

    s = sub.add_parser("window", help="open a conversation in its own window, detached from the client")
    s.add_argument("conversation", nargs="?")
    s.set_defaults(fn=cmd_window)

    s = sub.add_parser("conversations", help="list conversations", aliases=["ls"])
    s.add_argument("--all", action="store_true", help="include archived")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_conversations)

    s = sub.add_parser("contacts", help="list contacts and groups")
    s.add_argument("--refresh", action="store_true", help="re-sync from Signal first")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_contacts)

    s = sub.add_parser("history", help="print a conversation")
    s.add_argument("conversation")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_history)

    s = sub.add_parser("mark-read", help="mark a conversation read (sends read receipts)")
    s.add_argument("conversation")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_mark_read)

    s = sub.add_parser("status", help="bridge and account status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("events", help="stream events as JSON lines (used by the shell plugin)")
    s.add_argument("--once", action="store_true", help="exit when the bridge disconnects")
    s.set_defaults(fn=cmd_events)

    s = sub.add_parser("link", help="link this computer to your Signal account (QR code)")
    s.add_argument("-n", "--name", help="device name shown in Signal")
    s.add_argument("--force", action="store_true")
    s.add_argument("--text", action="store_true", help="draw the QR code with block characters even if the terminal can show images")
    s.add_argument("--qr-size", type=int, metavar="ROWS", help="height of the image QR code in rows (default from config, 9)")
    s.add_argument("--qr-style", choices=["auto", "image", "shell", "half", "quad", "braille"],
                   help="auto: Omarchy shell popup, else an image in a graphics terminal, else half-block text")
    s.set_defaults(fn=cmd_link)

    s = sub.add_parser("settings", help="list or change settings (notifications, privacy, media, appearance)")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_settings)

    s = sub.add_parser("demo", help="show a sample notification popup (nothing is sent)")
    s.add_argument("text", nargs="?")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("bridge", help="run the bridge daemon in the foreground")
    s.add_argument("--stderr", action="store_true", help="log to stderr instead of the log file")
    s.set_defaults(fn=cmd_bridge)

    s = sub.add_parser("doctor", help="check the installation")
    s.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
