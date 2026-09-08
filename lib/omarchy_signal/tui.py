"""The terminal client.

A single-screen, keyboard-first messenger drawn with raw escape sequences on
top of :mod:`term`. Colours come from the active Omarchy theme; images come
through the kitty graphics protocol when the terminal supports it; links are
OSC 8 hyperlinks. Every string that reaches the screen has been through
:func:`sanitize.clean_text` twice: once in the bridge, once here.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__, emoji, kitty, links, pathcomplete, qr
from .bridge import _guess_mime
from .client import BridgeClient, BridgeError, BridgeUnavailable
from .config import RESTART_REQUIRED, SETTINGS, Config, Paths, coerce_setting, save_config
from .sanitize import InvalidAttachment, clean_name, clean_text, safe_attachment_path, safe_filename
from .term import Key, KeyParser, Terminal, pad, str_width, truncate, wrap
from .theme import Theme, mix

T = Terminal

LIST_WIDTH = 30
MIN_COLS = 70
MIN_ROWS = 14


@dataclass
class Line:
    """One rendered screen line of the message pane."""
    text: str                 # with escape codes
    width: int                # visible width
    image: tuple[int, int, int] | None = None   # (image_id, cols, rows) anchored at this line
    right: bool = False


@dataclass
class ImageSlot:
    path: str
    image_id: int
    cols: int
    rows: int
    transmitted: bool = False
    failed: bool = False


@dataclass
class Composer:
    text: str = ""
    cursor: int = 0
    attachments: list[str] = field(default_factory=list)
    quote: dict | None = None

    def insert(self, s: str) -> None:
        self.text = self.text[:self.cursor] + s + self.text[self.cursor:]
        self.cursor += len(s)

    def backspace(self) -> None:
        if self.cursor > 0:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    def delete(self) -> None:
        self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]

    def delete_word(self) -> None:
        i = self.cursor
        while i > 0 and self.text[i - 1].isspace():
            i -= 1
        while i > 0 and not self.text[i - 1].isspace():
            i -= 1
        self.text = self.text[:i] + self.text[self.cursor:]
        self.cursor = i

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0
        self.attachments.clear()
        self.quote = None


class App:
    def __init__(self, cfg: Config, paths: Paths, *, initial: str = ""):
        self.cfg = cfg
        self.paths = paths
        self.initial = initial
        self.theme = Theme.load(paths.omarchy_theme_dir)
        self.term: Terminal | None = None
        self.client: BridgeClient | None = None
        self.status: dict = {}
        self.conversations: list[dict] = []
        self.messages: dict[str, list[dict]] = {}
        self.contacts: list[dict] = []
        self.groups: list[dict] = []
        self.selected = 0
        self.active_key = ""
        self.focus = "composer"          # composer | list | overlay
        self.overlay = ""                # "" | contacts | search | attach | react | help | link | quit
        self.overlay_query = ""
        self.overlay_index = 0
        self.overlay_items: list[dict] = []
        self.overlay_results: list[dict] = []
        self.overlay_message = ""
        self.att_items: list[dict] = []      # attachments shown in the "attachment" overlay
        self.att_index = 0
        self._pending_seq = 0
        self.emoji_suggestions: list[tuple[str, str]] = []
        self.emoji_index = 0
        self.pick_items: list[dict] = []          # message picker
        self.pick_index = 0
        self.pick_action = ""                     # pending action awaiting a target/prompt
        self.pick_target: dict | None = None
        self.menu_items: list[tuple[str, str]] = []   # conversation menu (key, label)
        self.menu_index = 0
        self.group_members: set[str] = set()      # new-group member selection
        self.info_lines: list[str] = []           # generic info overlay content
        self.forward_payload: dict | None = None
        self._last_failed: dict | None = None
        self.settings_index = 0
        self.settings_pending: set[str] = set()   # changed keys that need a bridge restart
        self.settings_edit_key = ""               # text/path setting being edited in a prompt
        self.revealed: set[str] = set()      # attachment paths shown inline on request
        self.hidden: set[str] = set()        # attachment paths hidden on request
        self.link_uri = ""
        self.link_started = 0.0
        self.link_png_id = 0
        self.link_shell_shown = False
        self.link_shell_ok = False
        self.scroll = 0                  # lines scrolled up from the bottom
        self.composer = Composer()
        self.toast = ""
        self.toast_until = 0.0
        self.typing: dict[str, tuple[str, float]] = {}
        self.images: dict[str, ImageSlot] = {}
        self.placed: dict[int, tuple[int, int, int, int]] = {}   # image id -> (row, col, cols, rows) currently on screen
        self.reset_images = False
        self.graphics = False
        self.dirty = True
        self.running = True
        self.boot_progress = 0.0
        self.last_typing_sent = 0.0
        self.pending_history: set[str] = set()
        self.connected = False
        self.parser = KeyParser()
        self.frame = 0
        self.hover_link: str = ""
        self.link_map: dict[int, list[tuple[int, int, str]]] = {}   # screen row -> [(col_start, col_end, href)]

    # ------------------------------------------------------------------ lifecycle

    async def run(self) -> int:
        with Terminal() as term:
            self.term = term
            term.write(T.title("Signal"))
            self.graphics = self.cfg.terminal_images == "on" or (
                self.cfg.terminal_images == "auto" and kitty.terminal_supports_graphics())
            term.on_resize(self._resized)
            loop = asyncio.get_running_loop()
            loop.add_reader(term.fd_in, self._on_input)
            try:
                await self._boot()
                await self._main_loop()
            finally:
                loop.remove_reader(term.fd_in)
                if self.graphics:
                    term.write(kitty.encode_delete_all())
                if self.client:
                    await self.client.close()
        return 0

    async def _boot(self) -> None:
        steps = ["Loading theme", "Contacting bridge", "Decrypting channel", "Synchronising directory", "Online"]
        for i, step in enumerate(steps):
            self.boot_progress = (i + 1) / len(steps)
            self._draw_boot(step)
            if i == 1:
                await self._connect()
            else:
                await asyncio.sleep(0.12)
        if self.client:
            await self._load_initial()
        if not self.graphics and self.cfg.terminal_images != "off":
            self.show_toast("This terminal cannot draw images. For inline photos: omarchy default terminal ghostty", 8)

    async def _connect(self) -> None:
        self.client = BridgeClient(self.paths, on_event=self._on_event)
        try:
            self.status = await self.client.connect()
            self.connected = True
            await self.client.request("subscribe")
        except BridgeUnavailable as exc:
            self.client = None
            self.connected = False
            self.show_toast(str(exc).split(";")[0])

    async def _load_initial(self) -> None:
        assert self.client
        try:
            self.conversations = await self.client.request("conversations")
            self.contacts = await self.client.request("contacts")
            self.groups = await self.client.request("groups")
        except BridgeError as exc:
            self.show_toast(f"bridge: {exc}")
            return
        if self.initial:
            try:
                res = await self.client.request("resolve", query=self.initial)
                if "key" in res:
                    self._ensure_conversation(res["key"], res.get("name", ""), res.get("kind", ""))
                    self._select_key(res["key"])
                else:
                    self.show_toast("ambiguous recipient; pick from the list")
            except BridgeError as exc:
                self.show_toast(f"{exc}")
        if not self.active_key and self.conversations:
            self._select_index(0)
        if not self.status.get("linked"):
            self.open_overlay("link")

    async def _main_loop(self) -> None:
        assert self.term
        last_theme_check = 0.0
        while self.running:
            now = time.monotonic()
            if now - last_theme_check > 2:
                last_theme_check = now
                if self.theme.changed_on_disk(self.paths.omarchy_theme_dir):
                    self.theme = Theme.load(self.paths.omarchy_theme_dir)
                    self.dirty = True
            if self.toast and now > self.toast_until:
                self.toast = ""
                self.dirty = True
            if self.typing:
                expired = [k for k, (_, until) in self.typing.items() if until < now]
                for k in expired:
                    del self.typing[k]
                if expired:
                    self.dirty = True
            if self.overlay == "link" and self.link_uri and self.frame % 1 == 0:
                self.dirty = True
            if self.dirty:
                self.draw()
                self.dirty = False
            await asyncio.sleep(0.03)
            flushed = self.parser.flush() if self.parser.buf else []
            for key in flushed:
                await self.handle_key(key)

    def _resized(self) -> None:
        if self.term:
            self.term.measure()
        # Cell geometry changed: recompute every image box and re-place.
        self.images.clear()
        self.reset_images = True
        self.dirty = True

    def _on_input(self) -> None:
        assert self.term
        data = self.term.read()
        if not data:
            return
        keys = self.parser.feed(data)
        if keys:
            asyncio.ensure_future(self._handle_keys(keys))

    async def _handle_keys(self, keys: list[Key]) -> None:
        for key in keys:
            if not self.running:
                break
            try:
                await self.handle_key(key)
            except BridgeUnavailable as exc:
                self.connected = False
                self.show_toast(str(exc).split(";")[0])
            except BridgeError as exc:
                self.show_toast(f"{exc}")
            self.dirty = True

    # ------------------------------------------------------------------ events

    async def _on_event(self, name: str, data) -> None:
        if not isinstance(data, dict):
            data = {}
        if name == "message":
            key = data.get("conversation", "")
            masked = data.get("senderName") == "Signal" and data.get("conversationName") == "New message"
            self._ensure_conversation(key, "" if masked else data.get("conversationName", ""), "group" if data.get("isGroup") else "")
            self._touch_conversation(key, data.get("ts", 0), data.get("preview", ""),
                                     unread_delta=0 if (data.get("outgoing") or key == self.active_key) else 1)
            if key in self.messages:
                self.messages[key].append(self._event_to_message(data))
            if key == self.active_key and not data.get("outgoing") and self.client:
                asyncio.ensure_future(self._mark_read(key, force=True))
                self.scroll = 0
            elif not data.get("outgoing") and data.get("notify"):
                self.show_toast(f"{clean_name(data.get('senderName', '?'))}: {data.get('preview', '')}", 4)
            self.typing.pop(key, None)
        elif name == "sent":
            key = data.get("conversation", "")
            self._touch_conversation(key, data.get("ts", 0), data.get("text") or "[attachment]")
            if key in self.messages:
                if not any(m.get("ts") == data.get("ts") and m.get("outgoing") for m in self.messages[key]):
                    self.messages[key].append({
                        "conversation": key, "ts": data.get("ts", 0), "sender": "me", "senderName": "You",
                        "outgoing": True, "body": data.get("text", ""), "attachments": data.get("attachments", []),
                        "status": data.get("status", "sent"), "reactions": {}, "quoteText": "", "quoteAuthor": ""})
        elif name == "receipt":
            for msgs in self.messages.values():
                for m in msgs:
                    if m.get("outgoing") and m.get("ts") in data.get("timestamps", []):
                        m["status"] = {"delivery": "delivered", "read": "read", "viewed": "viewed"}.get(data.get("type"), m.get("status"))
        elif name == "typing":
            key = data.get("conversation", "")
            if data.get("typing"):
                self.typing[key] = (data.get("senderName", ""), time.monotonic() + 6)
            else:
                self.typing.pop(key, None)
        elif name == "reaction":
            key = data.get("conversation", "")
            for m in self.messages.get(key, []):
                if m.get("ts") == data.get("ts"):
                    reactions = m.setdefault("reactions", {})
                    if data.get("removed"):
                        reactions.pop(data.get("sender", ""), None)
                    else:
                        reactions[data.get("sender", "")] = data.get("emoji", "")
        elif name == "deleted":
            for m in self.messages.get(data.get("conversation", ""), []):
                if m.get("ts") == data.get("ts"):
                    m["deleted"] = True
                    m["body"] = ""
                    m["attachments"] = []
        elif name == "edited":
            for m in self.messages.get(data.get("conversation", ""), []):
                if m.get("ts") == data.get("ts"):
                    m["body"] = clean_text(data.get("text", ""))
                    m["edited"] = True
        elif name == "conversation":
            for c in self.conversations:
                if c["key"] == data.get("conversation"):
                    for k in ("expiration", "blocked", "muted", "archived"):
                        if k in data:
                            c[k] = data[k]
        elif name == "unread":
            key = data.get("conversation")
            if key:
                for c in self.conversations:
                    if c["key"] == key:
                        c["unread"] = 0
        elif name == "status":
            self.status.update(data)
            self.connected = bool(data.get("connected"))
            if self.overlay == "link" and data.get("linked"):
                self.close_overlay()
                self.show_toast("Linked. Welcome aboard.")
                if self.client:
                    with contextlib.suppress(BridgeError):
                        self.contacts = await self.client.request("contacts")
                        self.groups = await self.client.request("groups")
        elif name == "directory" and self.client:
            with contextlib.suppress(BridgeError):
                self.contacts = await self.client.request("contacts")
                self.groups = await self.client.request("groups")
                self.conversations = await self.client.request("conversations")
                self._select_key(self.active_key)
        self.dirty = True

    @staticmethod
    def _event_to_message(data: dict) -> dict:
        sender_name = data.get("senderName", "")
        if sender_name == "Signal" and data.get("sender"):
            sender_name = ""   # masked for the popup ("none" content); the thread shows the real name
        return {"conversation": data.get("conversation", ""), "ts": data.get("ts", 0), "sender": data.get("sender", ""),
                "senderName": sender_name, "outgoing": bool(data.get("outgoing")),
                "body": data.get("text", ""), "attachments": data.get("attachments", []), "status": "",
                "reactions": {}, "quoteText": data.get("quoteText", ""), "quoteAuthor": "",
                "expiresIn": data.get("expiresIn", 0)}

    # ------------------------------------------------------------------ state helpers

    def _ensure_conversation(self, key: str, name: str, kind: str) -> None:
        if not key:
            return
        for c in self.conversations:
            if c["key"] == key:
                if name and not c.get("name"):
                    c["name"] = name
                return
        self.conversations.insert(0, {"key": key, "kind": kind or key.split(":")[0], "name": name or key.split(":", 1)[-1],
                                      "lastTs": 0, "preview": "", "unread": 0, "muted": False, "typing": False})

    def _touch_conversation(self, key: str, ts: int, preview: str, *, unread_delta: int = 0) -> None:
        for c in self.conversations:
            if c["key"] == key:
                c["lastTs"] = max(c.get("lastTs", 0), ts)
                c["preview"] = preview
                c["unread"] = max(0, c.get("unread", 0) + unread_delta)
                break
        self.conversations.sort(key=lambda c: -c.get("lastTs", 0))
        self._select_key(self.active_key)

    def _select_key(self, key: str) -> None:
        for i, c in enumerate(self.conversations):
            if c["key"] == key:
                self.selected = i
                self.active_key = key
                asyncio.ensure_future(self._load_history(key))
                return

    def _select_index(self, index: int) -> None:
        if not self.conversations:
            self.active_key = ""
            return
        self.selected = max(0, min(len(self.conversations) - 1, index))
        key = self.conversations[self.selected]["key"]
        if key != self.active_key:
            self.active_key = key
            self.scroll = 0
            self.composer.quote = None
        asyncio.ensure_future(self._load_history(key))
        asyncio.ensure_future(self._mark_read(key))

    async def _mark_read(self, key: str, *, force: bool = False) -> None:
        if not self.client:
            return
        for c in self.conversations:
            if c["key"] == key and (c.get("unread") or force):
                c["unread"] = 0
                self.dirty = True
                with contextlib.suppress(BridgeError, BridgeUnavailable):
                    await self.client.request("markRead", conversation=key)

    async def _load_history(self, key: str, *, older: bool = False) -> None:
        if not self.client or not key or key in self.pending_history:
            return
        if key in self.messages and not older:
            return
        self.pending_history.add(key)
        try:
            before = self.messages[key][0]["ts"] if older and self.messages.get(key) else 0
            msgs = await self.client.request("history", conversation=key, before=before, limit=60)
            if older:
                self.messages[key] = msgs + self.messages.get(key, [])
                if not msgs:
                    self.show_toast("beginning of history")
            else:
                self.messages[key] = msgs
        except BridgeError as exc:
            self.show_toast(f"history: {exc}")
        finally:
            self.pending_history.discard(key)
            self.dirty = True

    def show_toast(self, text: str, seconds: float = 3.0) -> None:
        self.toast = clean_text(text, max_length=200, single_line=True)
        self.toast_until = time.monotonic() + seconds
        self.dirty = True

    def active(self) -> dict | None:
        for c in self.conversations:
            if c["key"] == self.active_key:
                return c
        return None

    # ------------------------------------------------------------------ overlays

    def open_overlay(self, name: str, *, message: str = "") -> None:
        self.overlay = name
        self.overlay_query = ""
        self.overlay_index = 0
        self.overlay_message = message
        self.focus = "overlay"
        if name in ("contacts", "forward"):
            self.overlay_items = [{"key": c["key"], "name": c["displayName"], "sub": c.get("number") or c.get("username") or "", "kind": "contact"}
                                  for c in self.contacts]
            self.overlay_items += [{"key": g["key"], "name": g["name"], "sub": f"{len(g.get('members', []))} members", "kind": "group"}
                                   for g in self.groups]
            self.overlay_items.sort(key=lambda x: x["name"].lower())
        elif name == "members":
            self.overlay_items = [{"key": c["key"], "name": c["displayName"], "sub": c.get("number") or c.get("username") or "", "kind": "contact"}
                                  for c in self.contacts]
            self.overlay_items.sort(key=lambda x: x["name"].lower())
        elif name == "link":
            asyncio.ensure_future(self._start_link())
        elif name in ("attach", "saveas"):
            self.overlay_query = message or "~/"
            self._path_candidates()
        self._filter_overlay()
        self.dirty = True

    # ------------------------------------------------------------------ message picker (Ctrl-G)

    def open_message_picker(self) -> None:
        msgs = [m for m in self.messages.get(self.active_key, []) if not m.get("_pending")]
        if not msgs:
            self.show_toast("no messages here yet")
            return
        self.pick_items = msgs[-30:]
        self.pick_index = len(self.pick_items) - 1
        self.pick_action = ""
        self.open_overlay("pick")

    def _pick_current(self) -> dict | None:
        if not self.pick_items:
            return None
        return self.pick_items[max(0, min(len(self.pick_items) - 1, self.pick_index))]

    async def _pick_key(self, key: Key) -> None:
        m = self._pick_current()
        n = len(self.pick_items)
        if key.name == "down" or (key.name == "char" and key.char == "j"):
            self.pick_index = min(n - 1, self.pick_index + 1)
        elif key.name == "up" or (key.name == "char" and key.char == "k"):
            self.pick_index = max(0, self.pick_index - 1)
        elif key.name == "pagedown":
            self.pick_index = min(n - 1, self.pick_index + 8)
        elif key.name == "pageup":
            self.pick_index = max(0, self.pick_index - 8)
        elif m is None:
            return
        elif key.name == "char" and key.char == "r":
            self.pick_target = m
            self.pick_action = "react"
            self.overlay = "prompt"
            self.overlay_query = ""
            self.overlay_message = "emoji to react with (type :code: or paste one)"
        elif key.name == "char" and key.char == "q":
            self.composer.quote = m
            self.close_overlay()
            self.show_toast("quoting; Enter sends your reply")
        elif key.name == "char" and key.char == "e":
            if not m.get("outgoing"):
                self.show_toast("you can only edit your own messages")
                return
            self.pick_target = m
            self.pick_action = "edit"
            self.overlay = "prompt"
            self.overlay_query = m.get("body", "")
            self.overlay_message = "edit the message; Enter sends the new text"
        elif key.name == "char" and key.char == "d":
            if not m.get("outgoing"):
                self.show_toast("you can only delete your own messages")
                return
            self.pick_target = m
            self.pick_action = "delete"
            self.overlay = "prompt"
            self.overlay_query = ""
            self.overlay_message = "delete for everyone? type yes"
        elif key.name == "char" and key.char == "f":
            self.forward_payload = m
            self.open_overlay("forward")
        elif key.name == "char" and key.char == "c":
            self._copy_to_clipboard(m.get("body", ""))
            self.close_overlay()
        elif key.name == "char" and key.char == "o":
            atts = [a for a in m.get("attachments", []) if a.get("path")]
            if atts:
                self.open_attachment_menu(atts, 0)
            else:
                for _, _, url in links.find_urls(m.get("body", "")):
                    self._open_href(url)
                    break
                else:
                    self.show_toast("nothing to open in that message")
        elif key.name == "char" and key.char == "i":
            self.info_lines = self._message_info(m)
            self.pick_action = "info"
            self.overlay = "info"

    def _message_info(self, m: dict) -> list[str]:
        when = datetime.fromtimestamp(m.get("ts", 0) / 1000).strftime("%Y-%m-%d %H:%M:%S") if m.get("ts") else "?"
        lines = [f"From      {'You' if m.get('outgoing') else clean_name(m.get('senderName') or m.get('sender', '?'))}",
                 f"Sent      {when}",
                 f"Status    {m.get('status') or ('received' if not m.get('outgoing') else 'sent')}"]
        if m.get("edited"):
            lines.append("Edited    yes")
        if m.get("expiresIn"):
            lines.append(f"Expires   after {self._fmt_duration(int(m['expiresIn']))}")
        if m.get("reactions"):
            lines.append("Reactions " + "  ".join(f"{clean_name(e, max_length=8)} {self._who(k)}" for k, e in m["reactions"].items()))
        for a in m.get("attachments", []):
            lines.append(f"File      {self._attachment_filename(a)} · {a.get('contentType', '')} · {self._fmt_size(int(a.get('size', 0) or 0))}")
        return lines

    def _who(self, key: str) -> str:
        for c in self.contacts:
            if c["key"] == key:
                return c["displayName"]
        return "you" if key.startswith("number:") and self.status.get("account") and key.endswith(self.status["account"]) else key.split(":", 1)[-1]

    async def _prompt_submit(self) -> None:
        action, target = self.pick_action, self.pick_target
        q = self.overlay_query.strip()
        if not self.client:
            self.close_overlay()
            return
        try:
            if action == "react" and target:
                glyph = emoji.replace_shortcodes(q) if q else ""
                glyph = clean_name(glyph, max_length=8)
                if not glyph:
                    self.close_overlay()
                    return
                await self.client.request("react", conversation=self.active_key, ts=target["ts"],
                                          author=target["sender"] if not target.get("outgoing") else f"number:{self.status.get('account', '')}",
                                          emoji=glyph)
            elif action == "edit" and target:
                if q and q != target.get("body", ""):
                    await self.client.request("edit", conversation=self.active_key, ts=target["ts"], text=q)
            elif action == "delete" and target:
                if q.lower() == "yes":
                    await self.client.request("delete", conversation=self.active_key, ts=target["ts"])
            elif action == "expiration":
                pass
            elif action == "rename":
                if q:
                    await self.client.request("renameGroup", conversation=self.active_key, name=q)
                    self._ensure_conversation(self.active_key, q, "group")
                    for c in self.conversations:
                        if c["key"] == self.active_key:
                            c["name"] = q
            elif action == "newgroup":
                if q:
                    self.pick_action = "newgroup-members"
                    self.overlay_message = q          # group name travels here
                    self.group_members = set()
                    self.open_overlay("members")
                    return
            elif action == "verify":
                await self.client.request("trust", conversation=self.active_key, safetyNumber=q)
                self.show_toast("safety number verified")
        except BridgeError as exc:
            self.show_toast(f"{exc}", 6)
        self.close_overlay()

    # ------------------------------------------------------------------ conversation menu (Ctrl-T)

    EXPIRATIONS = [(0, "off"), (30, "30 seconds"), (300, "5 minutes"), (3600, "1 hour"), (8 * 3600, "8 hours"),
                   (86400, "1 day"), (7 * 86400, "1 week"), (28 * 86400, "4 weeks")]

    def open_conversation_menu(self) -> None:
        conv = self.active()
        items: list[tuple[str, str]] = []
        if conv:
            is_group = conv.get("kind") == "group"
            exp = next((label for secs, label in self.EXPIRATIONS if secs == int(conv.get("expiration", 0) or 0)), f"{conv.get('expiration')}s")
            items.append(("expiration", f"Disappearing messages: {exp}"))
            items.append(("mute", "Unmute" if conv.get("muted") else "Mute notifications"))
            items.append(("archive", "Unarchive" if conv.get("archived") else "Archive conversation"))
            if is_group:
                items.append(("members", "Group members"))
                items.append(("rename", "Rename group"))
                items.append(("leave", "Leave group"))
            else:
                items.append(("safety", "View safety number"))
                items.append(("request", "Accept message request"))
            items.append(("block", "Unblock" if conv.get("blocked") else "Block"))
        items.append(("newgroup", "New group…"))
        items.append(("archived", "Show archived conversations"))
        self.menu_items = items
        self.menu_index = 0
        self.open_overlay("convmenu")

    async def _menu_key(self, key: Key) -> None:
        n = len(self.menu_items)
        if key.name == "down" or (key.name == "char" and key.char == "j"):
            self.menu_index = (self.menu_index + 1) % n
        elif key.name == "up" or (key.name == "char" and key.char == "k"):
            self.menu_index = (self.menu_index - 1) % n
        elif key.name == "enter" or (key.name == "char" and key.char in (" ", "l")) or key.name == "right":
            await self._menu_activate(self.menu_items[self.menu_index][0], 1)
        elif key.name == "left" or (key.name == "char" and key.char == "h"):
            await self._menu_activate(self.menu_items[self.menu_index][0], -1)

    async def _menu_activate(self, action: str, delta: int) -> None:
        conv = self.active()
        if not self.client:
            return
        try:
            if action == "expiration" and conv:
                current = int(conv.get("expiration", 0) or 0)
                secs = [x for x, _ in self.EXPIRATIONS]
                idx = secs.index(current) if current in secs else 0
                new = secs[(idx + delta) % len(secs)]
                await self.client.request("setExpiration", conversation=conv["key"], seconds=new)
                conv["expiration"] = new
                self.open_conversation_menu()
                self.menu_index = 0
            elif action == "mute" and conv:
                await self._toggle_mute()
                self.open_conversation_menu()
                self.menu_index = 1
            elif action == "archive" and conv:
                new = not conv.get("archived")
                await self.client.request("archive", conversation=conv["key"], archived=new)
                conv["archived"] = new
                self.show_toast("archived" if new else "unarchived")
                self.close_overlay()
            elif action == "block" and conv:
                new = not conv.get("blocked")
                await self.client.request("block", conversation=conv["key"], blocked=new)
                conv["blocked"] = new
                self.show_toast("blocked" if new else "unblocked")
                self.close_overlay()
            elif action == "request" and conv:
                await self.client.request("messageRequest", conversation=conv["key"], accept=True)
                self.show_toast("message request accepted")
                self.close_overlay()
            elif action == "safety" and conv:
                ids = await self.client.request("identities", conversation=conv["key"])
                if not ids:
                    self.info_lines = ["No safety number yet: exchange a message first."]
                else:
                    cur = ids[0]
                    groups = cur["safetyNumber"].split()
                    rows = [" ".join(groups[i:i + 4]) for i in range(0, len(groups), 4)] or [cur["safetyNumber"]]
                    self.info_lines = [f"Safety number with {conv.get('name', '')}", ""] + rows + ["",
                                       f"Trust: {cur['trustLevel'].replace('_', ' ').lower()}",
                                       "Compare with your contact's screen. If the numbers match, press v to mark it verified.",
                                       "A changed number means a new phone or a new install; verify again before trusting."]
                self.pick_action = "safety"
                self.overlay = "info"
            elif action == "members" and conv:
                info = await self.client.request("groupInfo", conversation=conv["key"])
                self.info_lines = [f"{info['name']} · {len(info['members'])} members", ""] + [f"  {m['name']}" for m in info["members"]]
                self.pick_action = "members-info"
                self.overlay = "info"
            elif action == "rename" and conv:
                self.pick_action = "rename"
                self.overlay = "prompt"
                self.overlay_query = conv.get("name", "")
                self.overlay_message = "new group name"
            elif action == "leave" and conv:
                await self.client.request("leaveGroup", conversation=conv["key"])
                self.show_toast("left the group")
                self.conversations = [c for c in self.conversations if c["key"] != conv["key"]]
                self.active_key = ""
                self._select_index(0)
                self.close_overlay()
            elif action == "newgroup":
                self.pick_action = "newgroup"
                self.overlay = "prompt"
                self.overlay_query = ""
                self.overlay_message = "group name"
            elif action == "archived":
                self.conversations = await self.client.request("conversations", includeArchived=True)
                self.show_toast("showing archived conversations too")
                self.close_overlay()
        except BridgeError as exc:
            self.show_toast(f"{exc}", 6)
            self.close_overlay()

    async def _members_key(self, key: Key) -> None:
        if key.name == "down" or (key.ctrl and key.char == "n"):
            self.overlay_index = min(max(0, len(self.overlay_results) - 1), self.overlay_index + 1)
        elif key.name == "up" or (key.ctrl and key.char == "p"):
            self.overlay_index = max(0, self.overlay_index - 1)
        elif key.name == "tab" or (key.name == "char" and key.char == " " and not self.overlay_query):
            if self.overlay_results:
                k = self.overlay_results[self.overlay_index]["key"]
                self.group_members ^= {k}
        elif key.name == "enter":
            if not self.group_members or not self.client:
                self.show_toast("Tab or Space marks members; Enter creates the group")
                return
            name = self.overlay_message
            try:
                made = await self.client.request("createGroup", name=name, members=sorted(self.group_members))
                self.close_overlay()
                if made.get("key"):
                    self._ensure_conversation(made["key"], made["name"], "group")
                    self._select_key(made["key"])
                self.show_toast(f"group {name} created")
            except BridgeError as exc:
                self.show_toast(f"{exc}", 6)
        elif key.name == "backspace":
            self.overlay_query = self.overlay_query[:-1]
            self._filter_overlay()
        elif key.name == "char" and not key.ctrl:
            self.overlay_query += key.char
            self._filter_overlay()

    # ------------------------------------------------------------------ settings

    def _editing_path(self) -> bool:
        spec = next((x for x in SETTINGS if x["key"] == self.settings_edit_key), None)
        return bool(spec and spec["type"] == "path")

    def _settings_apply(self, key: str, raw) -> None:
        try:
            value = coerce_setting(key, raw)
        except ValueError as exc:
            self.overlay_message = str(exc)
            return
        setattr(self.cfg, key, value)
        self.cfg = Config.from_dict({k: getattr(self.cfg, k) for k in self.cfg.__dataclass_fields__})
        try:
            save_config(self.cfg, self.paths)
        except OSError as exc:
            self.overlay_message = f"could not save: {exc.strerror}"
            return
        self.overlay_message = ""
        if key in RESTART_REQUIRED:
            self.settings_pending.add(key)
        else:
            self.settings_pending.discard(key)
            if self.client:
                asyncio.ensure_future(self._reload_bridge_config())
        if key == "terminal_images":
            self.graphics = self.cfg.terminal_images == "on" or (
                self.cfg.terminal_images == "auto" and kitty.terminal_supports_graphics(probe=False))
        self.dirty = True

    async def _reload_bridge_config(self) -> None:
        if not self.client:
            return
        try:
            res = await self.client.request("reloadConfig")
        except BridgeError as exc:
            if exc.code == "bad_request":
                self.show_toast("the running bridge predates this setting: systemctl --user restart omarchy-signal", 8)
            else:
                self.show_toast(f"bridge did not reload settings: {exc}", 6)
            return
        if res.get("restartRequired"):
            self.settings_pending.update(res["restartRequired"])

    def _settings_cycle(self, spec: dict, delta: int) -> None:
        key = spec["key"]
        current = getattr(self.cfg, key)
        if spec["type"] == "bool":
            self._settings_apply(key, not current)
        elif spec["type"] == "choice":
            choices = spec["choices"]
            idx = choices.index(current) if current in choices else 0
            self._settings_apply(key, choices[(idx + delta) % len(choices)])
        elif spec["type"] == "int":
            self._settings_apply(key, int(current) + delta * spec.get("step", 1))
        else:
            self.settings_edit_key = key
            self.overlay_query = str(current)
            self.overlay = "setting-text"
            self.overlay_message = ""
            self.overlay_results = []
            self.overlay_index = 0
            if spec["type"] == "path":
                self._path_candidates()

    async def _settings_key(self, key: Key) -> None:
        n = len(SETTINGS)
        spec = SETTINGS[self.settings_index]
        if key.name == "down" or (key.name == "char" and key.char == "j"):
            self.settings_index = (self.settings_index + 1) % n
        elif key.name == "up" or (key.name == "char" and key.char == "k"):
            self.settings_index = (self.settings_index - 1) % n
        elif key.name in ("enter", "right") or (key.name == "char" and key.char in (" ", "l")):
            self._settings_cycle(spec, 1)
        elif key.name == "left" or (key.name == "char" and key.char == "h"):
            self._settings_cycle(spec, -1)
        elif key.name == "char" and key.char == "r" and self.settings_pending:
            subprocess.Popen(["systemctl", "--user", "restart", "omarchy-signal"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.settings_pending.clear()
            self.show_toast("bridge restarting; reopen the client in a few seconds", 6)
            self.running = False
        elif key.name == "char" and key.char == "q":
            self.close_overlay()

    def open_attachment_menu(self, atts: list[dict], index: int = 0) -> None:
        atts = [a for a in atts if a.get("path")]
        if not atts:
            self.show_toast("that attachment has not been downloaded")
            return
        self.att_items = atts
        self.att_index = max(0, min(len(atts) - 1, index))
        self.open_overlay("attachment")

    def _path_candidates(self) -> None:
        _, cands = pathcomplete.complete(self.overlay_query)
        self.overlay_results = cands
        self.overlay_index = 0

    def close_overlay(self) -> None:
        if self.overlay == "link" and self.link_shell_ok:
            qr.shell_hide_qr()
        self.overlay = ""
        self.focus = "composer"
        self.link_uri = ""
        self.dirty = True

    def _filter_overlay(self) -> None:
        q = self.overlay_query.lower().strip()
        if self.overlay in ("contacts", "forward", "members"):
            items = self.overlay_items
            if q:
                items = [i for i in items if q in i["name"].lower() or q in i["sub"].lower()]
            self.overlay_results = items[:200]
            self.overlay_index = min(self.overlay_index, max(0, len(self.overlay_results) - 1))

    async def _start_link(self) -> None:
        if not self.client:
            self.overlay_message = "The bridge is not running."
            return
        try:
            res = await self.client.request("link", deviceName=self.cfg.device_name)
            self.link_uri = res.get("uri", "")
            self.link_started = time.monotonic()
            self.link_png_id = 0
            self.link_shell_shown = False
            self.link_shell_ok = False
            self.overlay_message = "Signal on your phone → Settings → Linked devices → Link new device → scan"
            self.dirty = True
            fin = await self.client.request("linkFinish", timeout=620)
            if self.link_shell_ok:
                qr.shell_hide_qr()
            if fin.get("linked"):
                self.status["linked"] = True
                self.link_uri = ""
                self.overlay_message = "Linked. Syncing contacts…"
                self.dirty = True
                for _ in range(60):
                    await asyncio.sleep(1)
                    with contextlib.suppress(BridgeError):
                        self.contacts = await self.client.request("contacts")
                        self.groups = await self.client.request("groups")
                    if self.contacts or self.groups:
                        break
                self.overlay_message = f"Linked. {len(self.contacts)} contacts, {len(self.groups)} groups."
                self.close_overlay()
                self.show_toast(f"Linked. {len(self.contacts)} contacts synced. Ctrl-U to start a conversation.", 6)
        except BridgeError as exc:
            self.overlay_message = f"Linking failed: {exc}"
        self.dirty = True

    # ------------------------------------------------------------------ keys

    async def handle_key(self, key: Key) -> None:
        if key.name == "mouse":
            await self._handle_mouse(key)
            return
        if key.ctrl and key.char == "c":
            if self.overlay:
                self.close_overlay()
                return
            self.open_overlay("quit")
            return
        if key.ctrl and key.char == "l":
            self.images.clear()
            self.reset_images = True
            self.dirty = True
            return
        if self.overlay:
            await self._overlay_key(key)
            return
        if self.emoji_suggestions and self.focus == "composer" and key.name in ("tab", "enter", "up", "down", "escape"):
            await self._composer_key(key)   # the picker owns these keys while it is open
            return
        if key.name == "f1" or (key.name == "char" and key.char == "?" and not self.composer.text):
            self.open_overlay("help")
            return
        if key.name == "f2" or (key.ctrl and key.char == "s"):
            self.open_overlay("settings")
            return
        if key.name == "tab":
            self.focus = "list" if self.focus == "composer" else "composer"
            return
        if key.name == "pageup":
            self.scroll += max(1, self._pane_rows() - 2)
            if self.active_key and self.scroll > len(self._render_lines()) - self._pane_rows():
                await self._load_history(self.active_key, older=True)
            return
        if key.name == "pagedown":
            self.scroll = max(0, self.scroll - max(1, self._pane_rows() - 2))
            return
        if key.alt and key.name in ("up", "down"):
            self._select_index(self.selected + (1 if key.name == "down" else -1))
            return
        if key.alt and key.name == "char" and key.char.isdigit() and key.char != "0":
            self._select_index(int(key.char) - 1)
            return
        if key.ctrl and key.char in ("n", "p") and self.focus == "composer" and not self.composer.text:
            self._select_index(self.selected + (1 if key.char == "n" else -1))
            return
        if key.ctrl and key.char == "u" and not self.composer.text:
            self.open_overlay("contacts")
            return
        if key.ctrl and key.char == "g":
            self.open_message_picker()
            return
        if key.ctrl and key.char == "t":
            self.open_conversation_menu()
            return
        if key.ctrl and key.char == "a":
            self.open_overlay("attach")
            return
        if key.ctrl and key.char == "r":
            self.open_overlay("react")
            return
        if key.ctrl and key.char == "o":
            self._open_last()
            return
        if key.ctrl and key.char == "q":
            self.composer.quote = self._last_incoming()
            if self.composer.quote is None:
                self.show_toast("nothing to quote")
            return
        if key.ctrl and key.char == "e":
            await self._toggle_mute()
            return
        if key.ctrl and key.char == "x":
            self.composer.clear()
            return
        if key.ctrl and key.char == "z":
            self._restore_failed()
            return
        if key.name == "char" and key.char == "/" and not self.composer.text and self.focus == "composer":
            self.open_overlay("search")
            return
        if self.focus == "list":
            await self._list_key(key)
        else:
            await self._composer_key(key)

    async def _list_key(self, key: Key) -> None:
        if key.name in ("down",) or (key.name == "char" and key.char == "j"):
            self._select_index(self.selected + 1)
        elif key.name in ("up",) or (key.name == "char" and key.char == "k"):
            self._select_index(self.selected - 1)
        elif key.name == "home" or (key.name == "char" and key.char == "g"):
            self._select_index(0)
        elif key.name == "end" or (key.name == "char" and key.char == "G"):
            self._select_index(len(self.conversations) - 1)
        elif key.name == "enter" or key.name == "right":
            self.focus = "composer"
        elif key.name == "escape":
            self.focus = "composer"
        elif key.name == "char" and key.char == "q":
            self.open_overlay("quit")
        elif key.name == "char" and key.char == "m":
            await self._toggle_mute()
        elif key.name == "char" and key.char == "c":
            self.open_overlay("contacts")

    def _update_emoji_suggestions(self) -> None:
        partial = emoji.partial_at(self.composer.text, self.composer.cursor)
        self.emoji_suggestions = emoji.search(partial[1]) if partial else []
        self.emoji_index = 0

    def _accept_emoji(self, index: int | None = None) -> bool:
        partial = emoji.partial_at(self.composer.text, self.composer.cursor)
        if not partial or not self.emoji_suggestions:
            return False
        start, _ = partial
        code, glyph = self.emoji_suggestions[index if index is not None else self.emoji_index]
        c = self.composer
        c.text = c.text[:start] + glyph + c.text[c.cursor:]
        c.cursor = start + len(glyph)
        self.emoji_suggestions = []
        return True

    async def _composer_key(self, key: Key) -> None:
        c = self.composer
        if self.emoji_suggestions:
            if key.name in ("down",) or (key.ctrl and key.char == "n"):
                self.emoji_index = (self.emoji_index + 1) % len(self.emoji_suggestions)
                return
            if key.name in ("up",) or (key.ctrl and key.char == "p"):
                self.emoji_index = (self.emoji_index - 1) % len(self.emoji_suggestions)
                return
            if key.name in ("tab", "enter"):
                self._accept_emoji()
                return
            if key.name == "escape":
                self.emoji_suggestions = []
                return
        if key.name == "enter" and not key.alt and not key.shift:
            await self._send()
        elif key.name == "enter":
            c.insert("\n")
        elif key.name == "escape":
            if c.quote:
                c.quote = None
            elif c.attachments:
                c.attachments.clear()
            else:
                self.focus = "list"
        elif key.name == "backspace":
            if key.alt or key.ctrl:
                c.delete_word()
            else:
                c.backspace()
        elif key.name == "delete":
            c.delete()
        elif key.name == "left":
            c.cursor = max(0, c.cursor - 1)
        elif key.name == "right":
            c.cursor = min(len(c.text), c.cursor + 1)
        elif key.name == "home" or (key.ctrl and key.char == "a"):
            c.cursor = 0
        elif key.name == "end" or (key.ctrl and key.char == "e"):
            c.cursor = len(c.text)
        elif key.ctrl and key.char == "w":
            c.delete_word()
        elif key.ctrl and key.char == "k":
            c.text = c.text[:c.cursor]
        elif key.name == "up" and not c.text:
            self.scroll += 1
        elif key.name == "down" and not c.text:
            self.scroll = max(0, self.scroll - 1)
        elif key.name == "char" and not key.ctrl:
            if len(c.text) < 60000:
                if key.char == ":":
                    # Closing colon on a known code converts it right away.
                    partial = emoji.partial_at(c.text, c.cursor)
                    glyph = emoji.lookup(partial[1]) if partial else None
                    if glyph:
                        c.text = c.text[:partial[0]] + glyph + c.text[c.cursor:]
                        c.cursor = partial[0] + len(glyph)
                        self.emoji_suggestions = []
                        return
                c.insert(key.char)
                await self._maybe_typing()
        elif key.name == "paste":
            c.insert(key.char)
        if key.name in ("char", "backspace", "delete", "left", "right", "home", "end"):
            self._update_emoji_suggestions()

    async def _overlay_key(self, key: Key) -> None:
        name = self.overlay
        if key.name == "escape":
            self.close_overlay()
            return
        if name == "help":
            if key.name in ("enter", "char"):
                self.close_overlay()
            return
        if name == "quit":
            if key.name == "char" and key.char in ("y", "q") or key.name == "enter":
                self.running = False
            else:
                self.close_overlay()
            return
        if name == "link":
            if key.name == "char" and key.char == "q":
                self.close_overlay()
            return
        if name == "contacts":
            if key.name == "down" or (key.ctrl and key.char == "n"):
                self.overlay_index = min(len(self.overlay_results) - 1, self.overlay_index + 1)
            elif key.name == "up" or (key.ctrl and key.char == "p"):
                self.overlay_index = max(0, self.overlay_index - 1)
            elif key.name == "enter":
                if self.overlay_results:
                    item = self.overlay_results[self.overlay_index]
                    self._ensure_conversation(item["key"], item["name"], item["kind"] if item["kind"] == "group" else "")
                    self._select_key(item["key"])
                    self.close_overlay()
                elif self.overlay_query.strip() and self.client:
                    res = await self.client.request("resolve", query=self.overlay_query.strip())
                    if "key" in res:
                        self._ensure_conversation(res["key"], res.get("name", ""), res.get("kind", ""))
                        self._select_key(res["key"])
                        self.close_overlay()
            elif key.name == "backspace":
                self.overlay_query = self.overlay_query[:-1]
                self._filter_overlay()
            elif key.name == "char" and not key.ctrl:
                self.overlay_query += key.char
                self._filter_overlay()
            return
        if name == "settings":
            await self._settings_key(key)
            return
        if name == "pick":
            await self._pick_key(key)
            return
        if name == "convmenu":
            await self._menu_key(key)
            return
        if name == "info":
            if key.name in ("enter", "char"):
                if key.name == "char" and key.char == "v" and self.pick_action == "safety" and self.client:
                    await self.client.request("trust", conversation=self.active_key)
                    self.show_toast("marked verified")
                self.close_overlay()
            return
        if name == "prompt":
            if key.name == "enter":
                await self._prompt_submit()
            elif key.name == "backspace":
                self.overlay_query = self.overlay_query[:-1]
            elif key.name == "char" and not key.ctrl:
                self.overlay_query += key.char
            return
        if name == "members":
            await self._members_key(key)
            return
        if name == "forward":
            # contacts-style list; Enter forwards to the highlighted conversation
            if key.name == "down" or (key.ctrl and key.char == "n"):
                self.overlay_index = min(len(self.overlay_results) - 1, self.overlay_index + 1)
            elif key.name == "up" or (key.ctrl and key.char == "p"):
                self.overlay_index = max(0, self.overlay_index - 1)
            elif key.name == "enter" and self.overlay_results and self.forward_payload and self.client:
                item = self.overlay_results[self.overlay_index]
                payload = self.forward_payload
                self.close_overlay()
                try:
                    await self.client.request("send", conversation=item["key"], text=payload.get("body", ""),
                                              attachments=[a["path"] for a in payload.get("attachments", []) if a.get("path")])
                    self.show_toast(f"forwarded to {item['name']}")
                except BridgeError as exc:
                    self.show_toast(f"forward failed: {exc}", 6)
            elif key.name == "backspace":
                self.overlay_query = self.overlay_query[:-1]
                self._filter_overlay()
            elif key.name == "char" and not key.ctrl:
                self.overlay_query += key.char
                self._filter_overlay()
            return
        if name == "setting-text":
            if key.name == "enter":
                self._settings_apply(self.settings_edit_key, self.overlay_query)
                if not self.overlay_message:
                    self.open_overlay("settings")
            elif key.name == "tab" and self.overlay_results:
                self.overlay_query = pathcomplete.accept(self.overlay_query, self.overlay_results[self.overlay_index])
                self._path_candidates()
            elif key.name == "backspace":
                self.overlay_query = self.overlay_query[:-1]
                if self._editing_path():
                    self._path_candidates()
            elif key.name == "down" and self.overlay_results:
                self.overlay_index = min(len(self.overlay_results) - 1, self.overlay_index + 1)
            elif key.name == "up":
                self.overlay_index = max(0, self.overlay_index - 1)
            elif key.name == "char" and not key.ctrl:
                self.overlay_query += key.char
                if self._editing_path():
                    self._path_candidates()
            return
        if name == "attachment":
            n = len(self.att_items)
            if key.name == "down" or (key.name == "char" and key.char == "j"):
                self.att_index = min(n - 1, self.att_index + 1)
            elif key.name == "up" or (key.name == "char" and key.char == "k"):
                self.att_index = max(0, self.att_index - 1)
            elif key.name == "enter" or (key.name == "char" and key.char == "o"):
                self._open_href("file://" + self.att_items[self.att_index]["path"])
                self.close_overlay()
            elif key.name == "char" and key.char == "s":
                self._save_attachment(self.att_items[self.att_index], os.path.expanduser(self.cfg.save_dir))
                self.close_overlay()
            elif key.name == "char" and key.char == "a":
                att = self.att_items[self.att_index]
                self.open_overlay("saveas", message=self.cfg.save_dir.rstrip("/") + "/" + self._attachment_filename(att))
            elif key.name == "char" and key.char == "c":
                self._copy_to_clipboard(self.att_items[self.att_index]["path"])
                self.close_overlay()
            elif key.name == "char" and key.char == "v":
                att = self.att_items[self.att_index]
                if str(att.get("contentType", "")).startswith("image/") and self.graphics:
                    self.toggle_inline(att["path"])
                    self.close_overlay()
                else:
                    self.show_toast("inline view needs an image and a graphics terminal")
            return
        if name in ("search", "attach", "saveas", "react"):
            path_mode = name in ("attach", "saveas")
            if key.name == "enter":
                await self._overlay_submit()
            elif key.name == "tab" and path_mode:
                if self.overlay_results:
                    self.overlay_query = pathcomplete.accept(self.overlay_query, self.overlay_results[self.overlay_index])
                    self._path_candidates()
                    self.overlay_message = ""
            elif key.name == "backspace":
                self.overlay_query = self.overlay_query[:-1]
                if path_mode:
                    self._path_candidates()
            elif key.ctrl and key.char == "u" and path_mode:
                self.overlay_query = "~/"
                self._path_candidates()
            elif key.ctrl and key.char == "w" and path_mode:
                q = self.overlay_query.rstrip("/")
                self.overlay_query = q[:q.rfind("/") + 1] if "/" in q else "~/"
                self._path_candidates()
            elif key.name == "down" and name in ("search", "attach", "saveas"):
                self.overlay_index = min(max(0, len(self.overlay_results) - 1), self.overlay_index + 1)
            elif key.name == "up" and name in ("search", "attach", "saveas"):
                self.overlay_index = max(0, self.overlay_index - 1)
            elif key.name == "pagedown" and path_mode:
                self.overlay_index = min(max(0, len(self.overlay_results) - 1), self.overlay_index + 10)
            elif key.name == "pageup" and path_mode:
                self.overlay_index = max(0, self.overlay_index - 10)
            elif key.name == "char" and not key.ctrl:
                self.overlay_query += key.char
                if path_mode:
                    self._path_candidates()
                    self.overlay_message = ""
                if name == "search" and self.client and len(self.overlay_query) >= 2:
                    with contextlib.suppress(BridgeError):
                        self.overlay_results = await self.client.request("search", query=self.overlay_query, limit=30)
                        self.overlay_index = 0
            return

    async def _overlay_submit(self) -> None:
        name = self.overlay
        q = self.overlay_query.strip()
        if name in ("attach", "saveas"):
            if not q:
                self.close_overlay()
                return
            expanded = Path(os.path.expanduser(q))
            # A directory, or a candidate that is a directory: descend instead of submitting.
            if self.overlay_results and not expanded.is_file():
                cand = self.overlay_results[self.overlay_index]
                self.overlay_query = pathcomplete.accept(self.overlay_query, cand)
                if cand.is_dir:
                    self._path_candidates()
                    return
                if name == "attach":
                    q = self.overlay_query
                    expanded = Path(os.path.expanduser(q))
            if name == "saveas":
                att = self.att_items[self.att_index] if self.att_items else None
                if att is None:
                    self.close_overlay()
                    return
                target = Path(os.path.expanduser(self.overlay_query))
                if target.is_dir():
                    self._save_attachment(att, str(target))
                else:
                    self._save_attachment(att, str(target.parent), filename=target.name)
                self.close_overlay()
                return
            try:
                path = safe_attachment_path(q)
            except InvalidAttachment as exc:
                self.overlay_message = str(exc)
                return
            if len(self.composer.attachments) >= 8:
                self.overlay_message = "at most 8 attachments per message"
                return
            if str(path) not in self.composer.attachments:
                self.composer.attachments.append(str(path))
            self.close_overlay()
        elif name == "react":
            target = self._last_incoming()
            if not target or not self.client:
                self.overlay_message = "nothing to react to"
                return
            emoji = clean_name(q, max_length=8)
            if not emoji:
                self.close_overlay()
                return
            await self.client.request("react", conversation=self.active_key, ts=target["ts"], author=target["sender"], emoji=emoji)
            self.close_overlay()
        elif name == "search":
            if self.overlay_results:
                hit = self.overlay_results[self.overlay_index]
                key = hit.get("conversation", "")
                self._ensure_conversation(key, "", "")
                self._select_key(key)
                self.close_overlay()
            elif q and self.client:
                self.overlay_results = await self.client.request("search", query=q, limit=30)

    async def _handle_mouse(self, key: Key) -> None:
        if key.release:
            return
        if key.button in (64, 65):
            if key.x <= LIST_WIDTH:
                self._select_index(self.selected + (1 if key.button == 65 else -1))
            else:
                self.scroll = max(0, self.scroll + (3 if key.button == 64 else -3))
            return
        if key.button == 0 and key.x <= LIST_WIDTH and 3 <= key.y and self.term:
            two_line = len(self.conversations) * 2 <= self.term.rows - 4
            index = (key.y - 3) // 2 if two_line else key.y - 3
            if 0 <= index < len(self.conversations):
                self._select_index(index)
                self.focus = "composer"
            return
        if key.button == 0:
            for start, end, href in self.link_map.get(key.y, []):
                if start <= key.x <= end:
                    if href.startswith("file://"):
                        found = self._attachment_for_path(href[7:])
                        if found:
                            atts, i = found
                            att = atts[i]
                            # A hidden image reveals itself on click; anything else gets the menu.
                            if (str(att.get("contentType", "")).startswith("image/") and self.graphics
                                    and not self._inline_wanted(att["path"])):
                                self.toggle_inline(att["path"])
                                return
                            self.open_attachment_menu(atts, i)
                            return
                    self._open_href(href)
                    return

    async def _maybe_typing(self) -> None:
        now = time.monotonic()
        if self.client and self.active_key and now - self.last_typing_sent > 8:
            self.last_typing_sent = now

            async def fire():
                with contextlib.suppress(BridgeError, BridgeUnavailable):
                    await self.client.request("typing", conversation=self.active_key)
            asyncio.ensure_future(fire())

    async def _toggle_mute(self) -> None:
        conv = self.active()
        if conv and self.client:
            muted = not conv.get("muted")
            await self.client.request("mute", conversation=conv["key"], muted=muted)
            conv["muted"] = muted
            self.show_toast("muted" if muted else "unmuted")

    async def _send(self) -> None:
        if not self.client:
            self.show_toast("not connected to the bridge")
            return
        if not self.active_key:
            self.open_overlay("contacts")
            return
        text = self.composer.text
        attachments = list(self.composer.attachments)
        if not text.strip() and not attachments:
            return
        key = self.active_key
        params = dict(conversation=key, text=text, attachments=attachments)
        if self.composer.quote:
            params.update(quoteTs=self.composer.quote["ts"], quoteAuthor=self.composer.quote["sender"],
                          quoteText=self.composer.quote.get("body", ""))
        # Show it at once and clear the composer, so a slow network never
        # looks like a dead Enter key (and a second Enter can not resend it).
        self._pending_seq += 1
        local_ts = int(time.time() * 1000) + self._pending_seq
        pending = {"conversation": key, "ts": local_ts, "sender": "me", "senderName": "You", "outgoing": True,
                   "body": text, "attachments": [{"filename": Path(a).name, "contentType": _guess_mime(Path(a)),
                                                  "size": Path(a).stat().st_size if Path(a).exists() else 0, "path": a} for a in attachments],
                   "status": "sending", "reactions": {}, "quoteText": params.get("quoteText", ""), "quoteAuthor": "", "_pending": True}
        self.messages.setdefault(key, []).append(pending)
        self.composer.clear()
        self.scroll = 0
        self.dirty = True
        asyncio.ensure_future(self._deliver(key, pending, params))

    async def _deliver(self, key: str, pending: dict, params: dict) -> None:
        assert self.client
        try:
            res = await self.client.request("send", **params)
        except BridgeError as exc:
            pending["status"] = "failed"
            pending["_error"] = str(exc)
            self.show_toast(f"send failed: {exc}  (Ctrl-Z restores the text)", 8)
            self._last_failed = pending
            self.dirty = True
            return
        ts = int(res.get("ts", 0) or 0)
        pending["ts"] = ts or pending["ts"]
        pending["status"] = res.get("status", "sent")
        pending.pop("_pending", None)
        if res.get("failures"):
            self.show_toast("delivered with errors: " + ", ".join(res["failures"])[:80], 5)
        # If the bridge's own "sent" event already added this message, drop the duplicate.
        msgs = self.messages.get(key, [])
        dupes = [m for m in msgs if m is not pending and m.get("outgoing") and m.get("ts") == pending["ts"]]
        for m in dupes:
            msgs.remove(m)
        self.dirty = True

    def _restore_failed(self) -> None:
        failed = getattr(self, "_last_failed", None)
        if not failed:
            self.show_toast("nothing to restore")
            return
        self.composer.text = failed.get("body", "")
        self.composer.cursor = len(self.composer.text)
        self.composer.attachments = [a["path"] for a in failed.get("attachments", []) if a.get("path")]
        msgs = self.messages.get(failed["conversation"], [])
        if failed in msgs:
            msgs.remove(failed)
        self._last_failed = None
        self.dirty = True

    def _last_incoming(self) -> dict | None:
        for m in reversed(self.messages.get(self.active_key, [])):
            if not m.get("outgoing") and not m.get("deleted"):
                return m
        return None

    def _open_last(self) -> None:
        atts = self._all_attachments()
        if atts:
            self.open_attachment_menu(atts, 0)
            return
        for m in reversed(self.messages.get(self.active_key, [])):
            for _, _, url in reversed(links.find_urls(m.get("body", ""))):
                self._open_href(url)
                return
        self.show_toast("nothing to open")

    def _all_attachments(self) -> list[dict]:
        """Every downloaded attachment in the open conversation, newest first,
        each tagged with the message time and sender for the picker."""
        out: list[dict] = []
        for m in reversed(self.messages.get(self.active_key, [])):
            who = "You" if m.get("outgoing") else (clean_name(m.get("senderName")) or "?")
            for a in reversed(m.get("attachments", [])):
                if a.get("path"):
                    tagged = dict(a)
                    tagged["_ts"] = m.get("ts", 0)
                    tagged["_who"] = who
                    out.append(tagged)
        return out

    def _attachment_for_path(self, path: str) -> tuple[list[dict], int] | None:
        atts = self._all_attachments()
        for i, a in enumerate(atts):
            if a.get("path") == path:
                return atts, i
        return None

    @staticmethod
    def _attachment_filename(att: dict) -> str:
        import mimetypes
        name = safe_filename(att.get("filename", ""), fallback="")
        ctype = str(att.get("contentType", "") or "")
        if not name:
            stem = "signal-" + str(att.get("id", "") or "attachment")
            name = safe_filename(stem, fallback="attachment")
        if "." not in name and ctype:
            ext = mimetypes.guess_extension(ctype) or ""
            if ext == ".jpe":
                ext = ".jpg"
            name += ext
        return name

    def _save_attachment(self, att: dict, directory: str, *, filename: str = "") -> None:
        src = Path(str(att.get("path", "")))
        if not src.is_file():
            self.show_toast("attachment file is gone")
            return
        name = safe_filename(filename, fallback="") or self._attachment_filename(att)
        dest_dir = Path(directory)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.show_toast(f"cannot create {dest_dir}: {exc.strerror}")
            return
        dest = dest_dir / name
        stem, ext = os.path.splitext(name)
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}-{n}{ext}"
            n += 1
        try:
            shutil.copyfile(src, dest)
        except OSError as exc:
            self.show_toast(f"save failed: {exc.strerror}")
            return
        self.show_toast(f"saved {dest}", 6)

    def _copy_to_clipboard(self, text: str) -> None:
        exe = shutil.which("wl-copy")
        if not exe:
            self.show_toast("wl-copy not found")
            return
        try:
            subprocess.run([exe, "--", text], input=None, timeout=3, check=False)
            self.show_toast("path copied")
        except (OSError, subprocess.SubprocessError):
            self.show_toast("copy failed")

    def _open_href(self, href: str) -> None:
        opener = shutil.which("xdg-open")
        if not opener:
            self.show_toast("xdg-open not found")
            return
        target = href
        if href.startswith("file://"):
            target = href[7:]
            if not Path(target).is_file():
                self.show_toast("file is gone")
                return
        elif links.safe_href(href) is None:
            self.show_toast("refusing to open that link")
            return
        try:
            subprocess.Popen([opener, target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.show_toast("opening…", 1.5)
        except OSError as exc:
            self.show_toast(f"open failed: {exc}")

    # ------------------------------------------------------------------ drawing

    def _pane_rows(self) -> int:
        assert self.term
        return max(1, self.term.rows - 3 - self._composer_rows() - 2)

    def _composer_width(self) -> int:
        assert self.term
        return max(10, self.term.cols - LIST_WIDTH - 6)

    def _composer_rows(self) -> int:
        assert self.term
        width = self._composer_width()
        lines = wrap(self.composer.text or " ", width)
        extra = (1 if self.composer.quote else 0) + (1 if self.composer.attachments else 0)
        return min(6, max(1, len(lines))) + extra

    def _draw_boot(self, step: str) -> None:
        assert self.term
        t, th = self.term, self.theme
        t.measure()
        w = min(60, t.cols - 4)
        filled = int(w * self.boot_progress)
        bar = "▰" * filled + "▱" * (w - filled)
        row = t.rows // 2 - 3
        col = max(1, (t.cols - w) // 2)
        out = [T.bg(th.background) + T.CLEAR, T.move(row, col) + T.fg(th.accent) + T.BOLD + pad("◢ SIGNAL // OMARCHY", w, align="center") + T.RESET,
               T.move(row + 2, col) + T.fg(th.glow) + bar + T.RESET,
               T.move(row + 4, col) + T.fg(th.dim) + pad(step.upper() + "…", w, align="center") + T.RESET,
               T.move(row + 6, col) + T.fg(th.muted) + pad(f"v{__version__} · {th.name} theme · e2ee by libsignal", w, align="center") + T.RESET]
        t.write("".join(out))
        t.flush()

    def draw(self) -> None:
        assert self.term
        t, th = self.term, self.theme
        self.frame += 1
        self.link_map = {}
        if t.cols < MIN_COLS or t.rows < MIN_ROWS:
            t.write(T.bg(th.background) + "".join(T.move(r, 1) + T.CLEAR_LINE for r in range(1, t.rows + 1))
                    + T.move(1, 1) + T.fg(th.red) + "terminal too small" + T.RESET)
            self.reset_images = True
            t.flush()
            return
        # Never ESC[2J here: Ghostty (and kitty) drop every image *and its
        # data* on a full clear, so pictures would vanish on the next frame.
        # Erasing line by line leaves placements alone.
        out = ["\x1b[?2026h", T.bg(th.background)] + [T.move(r, 1) + T.CLEAR_LINE for r in range(1, t.rows + 1)]
        if self.graphics and self.reset_images:
            out.append(kitty.encode_delete_all())
            self.placed.clear()
            for slot in self.images.values():
                slot.transmitted = False
            self.reset_images = False
        out.append(self._draw_header())
        out.append(self._draw_list())
        out.append(self._draw_messages())
        out.append(self._draw_composer())
        out.append(self._draw_emoji_picker())
        out.append(self._draw_footer())
        if self.overlay:
            out.append(self._draw_overlay())
        out.append(self._cursor())
        out.append("\x1b[?2026l")
        t.write("".join(out))
        t.flush()

    def _cursor(self) -> str:
        """Position (and shape) the hardware cursor. A blinking bar while
        composing; hidden when nothing is being typed."""
        assert self.term
        t = self.term
        if self.overlay in ("contacts", "search", "attach", "saveas", "react", "setting-text", "prompt", "members", "forward"):
            return "\x1b[5 q" + T.SHOW_CURSOR
        if self.focus != "composer" or self.overlay:
            return T.HIDE_CURSOR
        width = self._composer_width()
        crows = self._composer_rows()
        top = t.rows - 1 - crows
        extra = (1 if self.composer.quote else 0) + (1 if self.composer.attachments else 0)
        text_rows = crows - extra
        text = self.composer.text
        all_lines = wrap(text, width) if text else [""]
        before = text[:self.composer.cursor]
        # A zero-width marker keeps a trailing space from being trimmed by wrap().
        before_lines = wrap(before + "\u200b", width) if before else [""]
        line_idx = len(before_lines) - 1
        col_w = str_width(before_lines[-1].replace("\u200b", ""))
        first_visible = max(0, len(all_lines) - text_rows)
        row = top + extra + max(0, line_idx - first_visible)
        col = LIST_WIDTH + 4 + col_w
        return T.move(row, col) + "\x1b[5 q" + T.SHOW_CURSOR

    def _hue(self, key: str):
        th = self.theme
        palette = [th.blue, th.green, th.cyan, th.magenta, th.yellow, th.orange, th.bright_green, th.bright_magenta, th.bright_cyan]
        h = int(hashlib.blake2b(key.encode(), digest_size=2).hexdigest(), 16)
        return palette[h % len(palette)]

    def _draw_header(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        account = self.status.get("account") or ""
        unread = sum(c.get("unread", 0) for c in self.conversations if not c.get("muted"))
        state = ("◉ SECURE" if self.connected and self.status.get("linked") else
                 "◌ UNLINKED" if self.connected else "◌ OFFLINE")
        state_color = th.green if self.connected and self.status.get("linked") else th.red
        left = f" ◢ SIGNAL // OMARCHY "
        mid = f" {state} "
        right = f" {unread} unread ▸ {th.name} ▸ {'IMG' if self.graphics else 'TXT'} "
        space = t.cols - str_width(left) - str_width(mid) - str_width(right)
        line1 = (T.move(1, 1) + T.bg(th.panel) + T.fg(th.accent) + T.BOLD + left + T.RESET + T.bg(th.panel)
                 + T.fg(state_color) + mid + T.RESET + T.bg(th.panel) + " " * max(0, space)
                 + T.fg(th.dim) + right + T.RESET)
        # gradient scanline
        segs = []
        n = t.cols
        for i in range(n):
            c = mix(th.accent, th.muted, i / max(1, n - 1))
            segs.append(T.fg(c) + ("━" if (i + self.frame // 8) % 9 else "╍"))
        line2 = T.move(2, 1) + "".join(segs) + T.RESET
        return line1 + line2

    def _draw_list(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        out = []
        rows = t.rows - 4
        width = LIST_WIDTH - 1
        visible = self.conversations[:rows]
        if self.selected >= rows:
            visible = self.conversations[self.selected - rows + 1:self.selected + 1]
        for i in range(rows):
            row = 3 + i
            sep = T.fg(th.border) + "│" + T.RESET
            if i < len(visible):
                c = visible[i]
                is_active = c["key"] == self.active_key
                idx = self.conversations.index(c)
                unread = c.get("unread", 0)
                name = truncate(clean_name(c.get("name")) or clean_name(c["key"]).split(":", 1)[-1], width - 8)
                if c.get("muted"):
                    name = "󰖁 " + truncate(name, width - 10)
                badge = f" {unread:>2} " if unread else "    "
                when = self._fmt_time(c.get("lastTs", 0), short=True)
                typing_name = self.typing.get(c["key"])
                preview = "typing…" if typing_name else clean_text(c.get("preview", ""), single_line=True)
                preview = truncate(preview, width - 2)
                if is_active:
                    bg = T.bg(th.selection)
                    marker = T.fg(th.accent) + ("▶" if self.focus == "list" else "▌")
                else:
                    bg = T.bg(th.background)
                    marker = " "
                name_style = T.BOLD + T.fg(th.bright_foreground if unread else th.foreground)
                badge_style = T.bg(th.accent) + T.fg(th.background) + T.BOLD if unread else ""
                line = (T.move(row, 1) + bg + marker + name_style + " " + pad(name, width - 8) + T.RESET + bg
                        + (badge_style + badge + T.RESET + bg if unread else T.fg(th.dim) + pad(when, 4, align="right"))
                        + " " + T.RESET + sep)
                out.append(line)
                if i + 1 < rows and (unread or is_active or preview) and False:
                    pass
                # preview goes on a second physical line only when there is room
            else:
                out.append(T.move(row, 1) + " " * width + sep)
        # Two-line entries: name row + preview row, when few conversations.
        if len(self.conversations) * 2 <= rows:
            out = []
            for i in range(rows):
                row = 3 + i
                sep = T.fg(th.border) + "│" + T.RESET
                j = i // 2
                if j < len(self.conversations):
                    c = self.conversations[j]
                    is_active = c["key"] == self.active_key
                    unread = c.get("unread", 0)
                    bg = T.bg(th.selection) if is_active else T.bg(th.background)
                    if i % 2 == 0:
                        marker = T.fg(th.accent) + ("▶" if self.focus == "list" and is_active else ("▌" if is_active else " "))
                        name = clean_name(c.get("name")) or clean_name(c["key"]).split(":", 1)[-1]
                        if c.get("muted"):
                            name = "󰖁 " + name
                        name = truncate(name, width - 8)
                        when = self._fmt_time(c.get("lastTs", 0), short=True)
                        badge = f" {unread:>2} " if unread else pad(when, 4, align="right")
                        badge_style = (T.bg(th.accent) + T.fg(th.background) + T.BOLD) if unread else T.fg(th.dim)
                        hue = self._hue(c["key"])
                        line = (T.move(row, 1) + bg + marker + T.BOLD + T.fg(th.bright_foreground if unread else th.foreground)
                                + " " + T.fg(hue) + "●" + T.fg(th.bright_foreground if unread else th.foreground) + " "
                                + pad(name, width - 9) + T.RESET + bg + badge_style + badge + T.RESET + bg + " " + T.RESET + sep)
                    else:
                        typing_name = self.typing.get(c["key"])
                        preview = "✎ typing…" if typing_name else clean_text(c.get("preview", ""), single_line=True)
                        preview = truncate(preview, width - 5)
                        color = th.accent if typing_name else th.dim
                        line = T.move(row, 1) + bg + "    " + T.fg(color) + pad(preview, width - 4) + T.RESET + sep
                    out.append(line)
                else:
                    out.append(T.move(row, 1) + " " * width + sep)
        if not self.conversations:
            hint = "Ctrl-U to pick a contact"
            out.append(T.move(4, 2) + T.fg(th.dim) + truncate(hint, width - 2) + T.RESET)
        return "".join(out)

    def _fmt_time(self, ts_ms: int, *, short: bool = False) -> str:
        if not ts_ms:
            return ""
        dt = datetime.fromtimestamp(ts_ms / 1000)
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        if (now - dt).days < 7:
            return dt.strftime("%a") if short else dt.strftime("%a %H:%M")
        return dt.strftime("%m/%d") if short else dt.strftime("%Y-%m-%d %H:%M")

    def _render_lines(self) -> list[Line]:
        assert self.term
        t, th = self.term, self.theme
        width = t.cols - LIST_WIDTH - 2
        inner = max(20, width - 4)
        bubble_w = max(16, min(inner - 4, int(inner * 0.78)))
        msgs = self.messages.get(self.active_key, [])
        lines: list[Line] = []
        last_day = None
        for m in msgs:
            ts = m.get("ts", 0)
            day = datetime.fromtimestamp(ts / 1000).date() if ts else None
            if day != last_day and day is not None:
                label = day.strftime("%A, %d %B %Y")
                deco = f"┈┈ {label} ┈┈"
                lines.append(Line(T.fg(th.muted) + pad(deco, inner, align="center") + T.RESET, inner))
                last_day = day
            lines.extend(self._render_message(m, bubble_w, inner))
            lines.append(Line("", 0))
        if self.typing.get(self.active_key):
            name = clean_name(self.typing[self.active_key][0]) or "someone"
            dots = "●○○ ○●○ ○○●".split()[(self.frame // 10) % 3]
            lines.append(Line(T.fg(th.accent) + f"  {name} {dots}" + T.RESET, len(name) + 6))
        return lines

    def _render_message(self, m: dict, bubble_w: int, inner: int) -> list[Line]:
        th = self.theme
        out: list[Line] = []
        outgoing = bool(m.get("outgoing"))
        align_right = outgoing and self.cfg.message_layout == "bubbles"
        # The bridge sanitises everything it forwards; do it again here so a
        # bug on the other side of the socket can never reach the terminal.
        who = "You" if outgoing else (clean_name(m.get("senderName")) or clean_name(m.get("sender", "?")).split(":", 1)[-1])
        hue = th.accent if outgoing else self._hue(m.get("sender", ""))
        status = m.get("status", "")
        tick = {"sending": "◌ sending", "sent": "✓", "delivered": "✓✓", "read": "✓✓", "viewed": "✓✓", "failed": "✗ failed"}.get(status, "")
        tick_color = th.red if status == "failed" else (th.accent if status in ("read", "viewed") else th.dim)
        header = (T.fg(hue) + T.BOLD + who + T.RESET + T.fg(th.dim) + " · " + self._fmt_time(m.get("ts", 0)) + T.RESET
                  + ("  " + T.fg(th.dim) + "(edited)" + T.RESET if m.get("edited") else "")
                  + ("  " + T.fg(tick_color) + tick + T.RESET if tick else ""))
        header_w = str_width(who) + 3 + str_width(self._fmt_time(m.get("ts", 0))) + (10 if m.get("edited") else 0) + (2 + str_width(tick) if tick else 0)
        out.append(Line(header, header_w, right=align_right))
        indent = "  "
        if m.get("deleted"):
            out.append(Line(indent + T.fg(th.dim) + T.ITALIC + "message deleted" + T.RESET, 17, right=align_right))
            return out
        if m.get("quoteText"):
            q = truncate(clean_text(m["quoteText"], single_line=True), bubble_w - 4)
            out.append(Line(indent + T.fg(th.dim) + T.ITALIC + "↩ " + q + T.RESET, str_width(q) + 4, right=align_right))
        body = clean_text(m.get("body", ""))
        if body:
            for raw in wrap(body, bubble_w - 2):
                out.append(Line(indent + self._linkify(raw) + T.RESET, str_width(raw) + 2, right=align_right))
        for a in m.get("attachments", [])[:8]:
            out.extend(self._render_attachment(a, bubble_w, right=align_right, indent=indent))
        reactions = m.get("reactions") or {}
        if reactions:
            counts: dict[str, int] = {}
            for e in reactions.values():
                counts[e] = counts.get(e, 0) + 1
            text = "  ".join(f"{clean_name(e, max_length=8)}{n if n > 1 else ''}" for e, n in counts.items())
            out.append(Line(T.fg(th.yellow) + "  " + text + T.RESET, str_width(text) + 2, right=align_right))
        if m.get("expiresIn"):
            out.append(Line(T.fg(th.dim) + f"  ⏱ {self._fmt_duration(m['expiresIn'])}" + T.RESET, 12, right=align_right))
        if align_right and len(out) > 1:
            # Right-aligned bubbles move as one block: every line below the
            # header is padded to the widest line, so the border bar forms a
            # straight edge and wrapped text stays left-aligned inside it.
            block_w = max(line.width for line in out[1:])
            for line in out[1:]:
                if line.width < block_w:
                    line.text += " " * (block_w - line.width)
                    line.width = block_w
        return out

    @staticmethod
    def _fmt_duration(seconds: int) -> str:
        for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
            if seconds >= size:
                return f"{seconds // size}{unit}"
        return f"{seconds}s"

    def _linkify(self, text: str) -> str:
        th = self.theme
        spans = links.find_urls(text)
        if not spans:
            return T.fg(th.foreground) + text
        out = []
        pos = 0
        for start, end, url in spans:
            out.append(T.fg(th.foreground) + text[pos:start])
            href = links.safe_href(url)
            shown = text[start:end]
            if href:
                out.append(T.fg(th.cyan) + T.UNDERLINE + links.osc8(href, shown) + T.RESET)
            else:
                out.append(T.fg(th.foreground) + shown)
            pos = end
        out.append(T.fg(th.foreground) + text[pos:])
        return "".join(out)

    def _inline_wanted(self, path: str) -> bool:
        mode = self.cfg.inline_images
        if path in self.hidden:
            return False
        if path in self.revealed:
            return True
        return mode == "always"

    def toggle_inline(self, path: str) -> None:
        if self._inline_wanted(path):
            self.hidden.add(path)
            self.revealed.discard(path)
        else:
            self.revealed.add(path)
            self.hidden.discard(path)
        self.dirty = True

    def _render_attachment(self, a: dict, bubble_w: int, *, right: bool, indent: str = "  ") -> list[Line]:
        th = self.theme
        ctype = str(a.get("contentType", ""))
        name = clean_name(a.get("filename", "")) or "attachment"
        size = int(a.get("size", 0) or 0)
        path = str(a.get("path", "") or "")
        label = f"{name} · {self._fmt_size(size)}" if size else name
        icon = {"image": "🖼", "video": "🎞", "audio": "🎤" if a.get("voiceNote") else "🎵"}.get(ctype.split("/")[0], "📎")
        out: list[Line] = []
        is_image = ctype.startswith("image/") and bool(path)
        if is_image and self.graphics and not self._inline_wanted(path):
            label += "  · click to show"
        if is_image and self.graphics and self._inline_wanted(path):
            slot = self._image_slot(path, bubble_w)
            if slot and not slot.failed:
                for r in range(slot.rows):
                    out.append(Line(indent + " " * slot.cols, slot.cols + 2, right=right,
                                    image=(slot.image_id, slot.cols, slot.rows) if r == 0 else None))
        text = f"{icon} {truncate(label, bubble_w - 6)}"
        href = links.file_href(path) if path else None
        shown = T.fg(th.cyan) + T.UNDERLINE + links.osc8(href, text) + T.RESET if href else T.fg(th.dim) + text + T.RESET
        out.append(Line(indent + shown, str_width(text) + 2, right=right))
        if a.get("caption"):
            for raw in wrap(clean_text(a["caption"]), bubble_w - 2):
                out.append(Line(indent + T.fg(th.foreground) + raw + T.RESET, str_width(raw) + 2, right=right))
        return out

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
            n /= 1024
        return f"{n:.1f}GB"

    def _image_slot(self, path: str, bubble_w: int) -> ImageSlot | None:
        assert self.term
        slot = self.images.get(path)
        max_rows = self.cfg.image_max_rows
        if slot is None:
            info = kitty.probe_dimensions(Path(path))
            if info is None:
                slot = ImageSlot(path, 0, 0, 0, failed=True)
            else:
                cols, rows = kitty.fit_cells(info.width, info.height, max_cols=bubble_w - 4, max_rows=max_rows,
                                             cell_w=self.term.cell_w, cell_h=self.term.cell_h)
                slot = ImageSlot(path, kitty.image_id_for(path), cols, rows)
            self.images[path] = slot
        return slot

    def _draw_messages(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        rows = self._pane_rows()
        top = 3
        left = LIST_WIDTH + 2
        width = t.cols - LIST_WIDTH - 2
        out = []
        conv = self.active()
        if conv is None:
            msg = "No conversation selected. Press Ctrl-U to pick a contact, or Tab to browse."
            out.append(T.move(top + rows // 2, left + max(0, (width - len(msg)) // 2)) + T.fg(th.dim) + msg + T.RESET)
            return "".join(out)
        title = clean_name(conv.get("name")) or clean_name(conv["key"]).split(":", 1)[-1]
        sub = clean_name(conv["key"]).split(":", 1)[-1] if conv.get("kind") != "group" else "group"
        if conv.get("muted"):
            sub += " · muted"
        if conv.get("expiration"):
            sub += " · ⏱ " + self._fmt_duration(int(conv["expiration"]))
        if conv.get("blocked"):
            sub += " · blocked"
        out.append(T.move(top, left) + T.fg(self._hue(conv["key"])) + T.BOLD + truncate(title, width // 2) + T.RESET
                   + T.fg(th.dim) + "  " + truncate(sub, width // 2 - 4) + T.RESET)
        out.append(T.move(top + 1, left) + T.fg(th.border) + "╌" * (width - 1) + T.RESET)
        lines = self._render_lines()
        area = rows - 2
        max_scroll = max(0, len(lines) - area)
        self.scroll = min(self.scroll, max_scroll)
        end = len(lines) - self.scroll
        start = max(0, end - area)
        shown = lines[start:end]
        y = top + 2 + (area - len(shown))
        placements = []
        for line in shown:
            col = left + 1
            if line.right:
                col = left + max(1, width - line.width - 2)
            out.append(T.move(y, col) + line.text + T.RESET)
            self._record_links(y, col, line.text)
            if line.image and self.graphics:
                placements.append((y, col + 2, line.image))
                image_id, icols, irows = line.image
                slot = next((sl for sl in self.images.values() if sl.image_id == image_id), None)
                if slot is not None:
                    # Clicking anywhere on the picture opens its menu, like the name line.
                    for r in range(irows):
                        self.link_map.setdefault(y + r, []).append((col + 2, col + 2 + icols, "file://" + slot.path))
            y += 1
        if self.scroll:
            out.append(T.move(top + 2, left + width - 8) + T.fg(th.accent) + f"↑ {self.scroll}" + T.RESET)
        wanted: dict[int, tuple[int, int, int, int]] = {}
        for y, col, (image_id, cols, irows) in placements:
            wanted[image_id] = (y, col, cols, irows)
        if self.graphics:
            # Images that scrolled away or were hidden: drop their placement.
            # Some terminals also free the data then, so mark them for a fresh
            # upload the next time they are shown.
            for image_id in list(self.placed):
                if image_id not in wanted:
                    out.append(kitty.encode_delete(image_id))
                    del self.placed[image_id]
                    for slot in self.images.values():
                        if slot.image_id == image_id:
                            slot.transmitted = False
            for image_id, (y, col, cols, irows) in wanted.items():
                slot = next((sl for sl in self.images.values() if sl.image_id == image_id), None)
                if slot is None or slot.failed:
                    continue
                if not slot.transmitted:
                    png = kitty.to_png(Path(slot.path))
                    if png is None:
                        slot.failed = True
                        continue
                    out.append(kitty.encode_transmit_only(png, image_id))
                    slot.transmitted = True
                if self.placed.get(image_id) != (y, col, cols, irows):
                    # Same placement id: the terminal moves the picture instead of stacking a copy.
                    out.append(T.move(y, col) + kitty.encode_place(image_id, cols=cols, rows=irows))
                    self.placed[image_id] = (y, col, cols, irows)
        return "".join(out)

    def _record_links(self, row: int, col: int, text: str) -> None:
        """Track OSC 8 spans so a mouse click can open them."""
        import re
        visible = 0
        pos = 0
        pattern = re.compile(r"\x1b\]8;[^;]*;([^\x1b]*)\x1b\\(.*?)\x1b\]8;;\x1b\\|\x1b\[[0-9;?]*[A-Za-z]|\x1b_G[^\x1b]*\x1b\\")
        for m in pattern.finditer(text):
            visible += str_width(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text[pos:m.start()]))
            if m.group(1) is not None:
                w = str_width(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", m.group(2)))
                self.link_map.setdefault(row, []).append((col + visible, col + visible + w, m.group(1)))
                visible += w
            pos = m.end()

    def _draw_composer(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        crows = self._composer_rows()
        top = t.rows - 1 - crows
        left = LIST_WIDTH + 2
        width = t.cols - LIST_WIDTH - 2
        out = [T.move(top - 1, left) + T.fg(th.border) + "╌" * (width - 1) + T.RESET]
        y = top
        if self.composer.quote:
            q = self.composer.quote
            text = truncate(clean_text(f"↩ {q.get('senderName', '?')}: {q.get('body', '')}", single_line=True), width - 4)
            out.append(T.move(y, left) + T.fg(th.dim) + T.ITALIC + text + T.RESET)
            y += 1
        if self.composer.attachments:
            names = ", ".join(Path(p).name for p in self.composer.attachments)
            out.append(T.move(y, left) + T.fg(th.yellow) + truncate(f"📎 {names}", width - 4) + T.RESET)
            y += 1
        focused = self.focus == "composer" and not self.overlay
        prompt_color = th.accent if focused else th.muted
        lines = wrap(self.composer.text, self._composer_width()) if self.composer.text else [""]
        text_rows = crows - (1 if self.composer.quote else 0) - (1 if self.composer.attachments else 0)
        visible = lines[-text_rows:] if len(lines) > text_rows else lines
        for i in range(text_rows):
            out.append(T.move(y + i, left) + T.fg(prompt_color) + ("▌ " if i == 0 else "  ") + T.RESET)
            if i < len(visible):
                out.append(T.fg(th.bright_foreground) + visible[i] + T.RESET)
            elif i == 0 and not self.composer.text:
                placeholder = "Message… (Enter to send, Alt-Enter for a new line)" if self.active_key else "Pick a conversation first"
                out.append(T.fg(th.dim) + truncate(placeholder, width - 4) + T.RESET)
        return "".join(out)

    def _draw_emoji_picker(self) -> str:
        """Small completion box just above the composer, nvim style."""
        if not self.emoji_suggestions or self.overlay or self.focus != "composer":
            return ""
        assert self.term
        t, th = self.term, self.theme
        rows = len(self.emoji_suggestions)
        width = max(len(code) for code, _ in self.emoji_suggestions) + 8
        bottom = t.rows - 1 - self._composer_rows() - 2
        top = max(3, bottom - rows + 1)
        left = LIST_WIDTH + 4
        out = []
        for i, (code, glyph) in enumerate(self.emoji_suggestions[:bottom - top + 1]):
            selected = i == self.emoji_index
            style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else T.bg(th.panel) + T.fg(th.foreground)
            out.append(T.move(top + i, left) + style + (" ▶ " if selected else "   ") + glyph + " " + pad(":" + code + ":", width - 5) + T.RESET)
        return "".join(out)

    def _draw_footer(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        # Writing into the very last cell of the last row makes terminals
        # scroll the whole screen up a line, which shifts everything above
        # and leaves the cursor a row below the text. Stay one cell short.
        usable = t.cols - 1
        if self.toast:
            text = " " + truncate(self.toast, usable - 2) + " "
            return T.move(t.rows, 1) + T.bg(th.accent) + T.fg(th.background) + T.BOLD + pad(text, usable) + T.RESET
        hints = [("Tab", "list"), ("Ctrl-U", "contacts"), ("/", "search"), ("Ctrl-A", "attach"), ("Ctrl-G", "message"),
                 ("Ctrl-T", "chat"), ("Ctrl-O", "open"), ("Ctrl-S", "settings"), ("?", "help"), ("Ctrl-C", "quit")]
        parts: list[str] = []
        width = 1
        for k, v in hints:
            piece_w = len(k) + 1 + len(v) + (2 if parts else 0)
            if width + piece_w > usable:
                break
            parts.append(T.fg(th.accent) + k + T.fg(th.dim) + " " + v)
            width += piece_w
        line = (T.fg(th.dim) + "  ").join(parts)
        return T.move(t.rows, 1) + T.bg(th.panel) + " " * usable + T.move(t.rows, 2) + T.bg(th.panel) + line + T.RESET

    def _draw_overlay(self) -> str:
        assert self.term
        t, th = self.term, self.theme
        name = self.overlay
        w = min(t.cols - 6, 84 if self.overlay == "settings" else 72)
        rows_needed = {"contacts": min(t.rows - 6, 20), "search": min(t.rows - 6, 18), "help": 20, "link": min(t.rows - 4, 32),
                       "quit": 5, "attach": min(t.rows - 6, 5 + min(12, len(self.overlay_results))),
                       "saveas": min(t.rows - 6, 5 + min(12, len(self.overlay_results))),
                       "react": 6, "attachment": min(t.rows - 6, 9 + min(10, len(self.att_items))),
                       "settings": min(t.rows - 4, len(SETTINGS) + len({x["section"] for x in SETTINGS}) * 2 + 5),
                       "pick": min(t.rows - 4, 6 + min(14, len(self.pick_items))), "convmenu": len(self.menu_items) + 5,
                       "info": min(t.rows - 4, len(self.info_lines) + 5), "prompt": 6,
                       "members": min(t.rows - 6, 20), "forward": min(t.rows - 6, 20),
                       "setting-text": min(t.rows - 6, 5 + min(10, len(self.overlay_results)))}.get(name, 8)
        h = min(t.rows - 4, rows_needed)
        top = max(2, (t.rows - h) // 2)
        left = max(2, (t.cols - w) // 2)
        out = []
        bg = T.bg(th.panel)
        for i in range(h):
            out.append(T.move(top + i, left) + bg + " " * w + T.RESET)
        out.append(T.move(top, left) + bg + T.fg(th.accent) + "╭" + "─" * (w - 2) + "╮" + T.RESET)
        out.append(T.move(top + h - 1, left) + bg + T.fg(th.accent) + "╰" + "─" * (w - 2) + "╯" + T.RESET)
        for i in range(1, h - 1):
            out.append(T.move(top + i, left) + bg + T.fg(th.accent) + "│" + T.RESET)
            out.append(T.move(top + i, left + w - 1) + bg + T.fg(th.accent) + "│" + T.RESET)
        titles = {"contacts": "NEW CONVERSATION", "search": "SEARCH HISTORY", "help": "KEYS", "link": "LINK THIS DEVICE",
                  "quit": "DISCONNECT?", "attach": "ATTACH FILE", "saveas": "SAVE AS", "react": "REACT",
                  "attachment": "ATTACHMENT", "settings": "SETTINGS", "setting-text": "EDIT SETTING",
                  "pick": "MESSAGE ACTIONS", "convmenu": "CONVERSATION", "info": "INFO", "prompt": "INPUT",
                  "members": "NEW GROUP · MEMBERS", "forward": "FORWARD TO"}
        title = f" {titles.get(name, name.upper())} "
        out.append(T.move(top, left + 3) + bg + T.fg(th.bright_foreground) + T.BOLD + title + T.RESET)
        body_left = left + 2
        body_w = w - 4
        if name == "settings":
            out.extend(self._draw_settings(top, left, w, h))
        elif name == "pick":
            visible = h - 5
            first = max(0, min(self.pick_index - visible + 1, len(self.pick_items) - visible))
            y = top + 1
            for i, m in enumerate(self.pick_items[first:first + visible], start=first):
                selected = i == self.pick_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                who = "You" if m.get("outgoing") else clean_name(m.get("senderName") or "?")
                body = clean_text(m.get("body", ""), single_line=True) or ("[" + ", ".join(self._attachment_filename(a) for a in m.get("attachments", [])) + "]" if m.get("attachments") else "")
                if m.get("deleted"):
                    body = "(deleted)"
                text = ("▶ " if selected else "  ") + f"{who}: {body}"
                out.append(T.move(y, body_left) + style + pad(truncate(text, body_w - 8), body_w - 6)
                           + T.fg(th.dim if not selected else th.bright_foreground) + pad(self._fmt_time(m.get("ts", 0), short=True), 6, align="right") + T.RESET)
                y += 1
            hints = "r react · q quote · e edit · d delete · f forward · c copy · o open · i info · Esc"
            out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + truncate(hints, body_w) + T.RESET)
        elif name == "convmenu":
            for i, (_, label) in enumerate(self.menu_items):
                selected = i == self.menu_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                out.append(T.move(top + 1 + i, body_left) + style + ("▶ " if selected else "  ") + pad(truncate(label, body_w - 2), body_w - 2) + T.RESET)
            out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + truncate("Enter/→ choose or next value · ← previous · Esc", body_w) + T.RESET)
        elif name == "info":
            for i, line in enumerate(self.info_lines[:h - 3]):
                out.append(T.move(top + 1 + i, body_left) + bg + T.fg(th.foreground if i else th.bright_foreground) + truncate(clean_text(line, single_line=True), body_w) + T.RESET)
            foot = "v verify · Esc" if self.pick_action == "safety" else "any key closes"
            out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + foot + T.RESET)
        elif name == "prompt":
            prompt = "▸ "
            out.append(T.move(top + 1, body_left) + bg + T.fg(th.dim) + truncate(clean_text(self.overlay_message, single_line=True), body_w) + T.RESET)
            out.append(T.move(top + 2, body_left) + bg + T.fg(th.accent) + prompt + T.fg(th.bright_foreground)
                       + truncate(clean_text(self.overlay_query, single_line=True), body_w - 2) + T.RESET)
            out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + "Enter confirms · Esc cancels" + T.RESET)
            out.append(T.move(top + 2, body_left + len(prompt) + str_width(self.overlay_query)))
        elif name in ("members", "forward"):
            prompt = "filter ▸ "
            head = (f"Group: {self.overlay_message}   {len(self.group_members)} selected" if name == "members" else "Send this message to…")
            out.append(T.move(top + 1, body_left) + bg + T.fg(th.dim) + truncate(head, body_w) + T.RESET)
            out.append(T.move(top + 2, body_left) + bg + T.fg(th.accent) + prompt + T.fg(th.bright_foreground)
                       + truncate(self.overlay_query, body_w - len(prompt)) + T.RESET)
            first = max(0, self.overlay_index - (h - 6))
            for i, item in enumerate(self.overlay_results[first:first + h - 5]):
                y = top + 3 + i
                selected = (first + i) == self.overlay_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                mark = ("◉ " if item["key"] in self.group_members else "○ ") if name == "members" else "  "
                out.append(T.move(y, body_left) + style + ("▶ " if selected else "  ") + mark + pad(truncate(item["name"], body_w - 30), body_w - 28)
                           + T.fg(th.dim) + pad(truncate(item["sub"], 22), 22, align="right") + T.RESET)
            foot = "Tab/Space select · Enter create · Esc" if name == "members" else "Enter forward · Esc"
            out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + foot + T.RESET)
            out.append(T.move(top + 2, body_left + len(prompt) + str_width(self.overlay_query)))
        elif name == "setting-text":
            spec = next((x for x in SETTINGS if x["key"] == self.settings_edit_key), {"label": self.settings_edit_key, "help": ""})
            prompt = spec["label"] + " ▸ "
            out.append(T.move(top + 1, body_left) + bg + T.fg(th.accent) + prompt + T.fg(th.bright_foreground)
                       + truncate(clean_text(self.overlay_query, single_line=True), body_w - len(prompt)) + T.RESET)
            out.append(T.move(top + 2, body_left) + bg + T.fg(th.red if self.overlay_message else th.dim)
                       + truncate(self.overlay_message or (spec.get("help", "") + "  · Enter saves · Esc cancels"), body_w) + T.RESET)
            first = max(0, self.overlay_index - (h - 6))
            for i, cand in enumerate(self.overlay_results[first:first + h - 5]):
                y = top + 3 + i
                selected = (first + i) == self.overlay_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.accent if cand.is_dir else th.foreground)
                out.append(T.move(y, body_left) + style + ("▶ " if selected else "  ") + cand.icon + " " + pad(truncate(cand.name, body_w - 6), body_w - 6) + T.RESET)
            out.append(T.move(top + 1, body_left + len(prompt) + str_width(self.overlay_query)))
        elif name in ("contacts", "search", "attach", "saveas", "react"):
            prompt = {"contacts": "name or number ▸ ", "search": "text ▸ ", "attach": "path ▸ ", "saveas": "save to ▸ ", "react": "emoji ▸ "}[name]
            out.append(T.move(top + 1, body_left) + bg + T.fg(th.accent) + prompt + T.fg(th.bright_foreground)
                       + truncate(clean_text(self.overlay_query, single_line=True), body_w - len(prompt)) + T.RESET)
            if self.overlay_message:
                out.append(T.move(top + 2, body_left) + bg + T.fg(th.red) + truncate(clean_text(self.overlay_message, single_line=True), body_w) + T.RESET)
            elif name in ("attach", "saveas"):
                hint = "Tab completes · ↑↓ pick · Enter " + ("attaches a file / opens a folder" if name == "attach" else "saves here") + " · Ctrl-W up one folder"
                out.append(T.move(top + 2, body_left) + bg + T.fg(th.dim) + truncate(hint, body_w) + T.RESET)
            elif name == "react":
                target = self._last_incoming()
                hint = clean_text(f"to {target.get('senderName', '?')}: {target.get('body', '')[:40]}", single_line=True) if target else "nothing to react to"
                out.append(T.move(top + 2, body_left) + bg + T.fg(th.dim) + truncate(hint, body_w) + T.RESET)
            items = self.overlay_results if name in ("contacts", "search", "attach", "saveas") else []
            first = max(0, self.overlay_index - (h - 5))
            for i, item in enumerate(items[first:first + h - 4]):
                y = top + 3 + i
                selected = (first + i) == self.overlay_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                if name in ("attach", "saveas"):
                    cand = item
                    color = th.accent if cand.is_dir else (th.cyan if cand.kind == "image" else th.foreground)
                    right = ""
                    if not cand.is_dir:
                        right = self._fmt_size(cand.size)
                        if cand.kind == "image":
                            info = kitty.probe_dimensions(Path(cand.path))
                            if info and info.width:
                                right = f"{info.width}×{info.height} · " + right
                    out.append(T.move(y, body_left) + style + (T.fg(th.bright_foreground) if selected else T.fg(color))
                               + ("▶ " if selected else "  ") + cand.icon + " " + pad(truncate(cand.name, body_w - 26), body_w - 26)
                               + T.fg(th.dim if not selected else th.bright_foreground) + pad(right, 22, align="right") + T.RESET)
                    continue
                if name == "contacts":
                    hue = self._hue(item["key"])
                    text = ("▶ " if selected else "  ") + clean_name(item["name"])
                    right = truncate(clean_name(item["sub"]), 22)
                    out.append(T.move(y, body_left) + style + T.fg(hue if not selected else th.bright_foreground) + "● "
                               + pad(truncate(text, body_w - 26), body_w - 24) + T.fg(th.dim) + pad(right, 22, align="right") + T.RESET)
                else:
                    who = clean_name(item.get("senderName", "?"))
                    text = ("▶ " if selected else "  ") + clean_text(f"{who}: {item.get('body', '')}", single_line=True)
                    out.append(T.move(y, body_left) + style + pad(truncate(text, body_w - 8), body_w - 6)
                               + T.fg(th.dim) + pad(self._fmt_time(item.get("ts", 0), short=True), 6, align="right") + T.RESET)
            self._overlay_cursor = (top + 1, body_left + len(prompt) + str_width(self.overlay_query))
            out.append(T.move(*self._overlay_cursor))
        elif name == "help":
            keys = [("Enter", "send message"), ("Alt-Enter", "new line"), ("Tab", "focus conversation list"),
                    ("Alt-↑/↓, Ctrl-N/P", "switch conversation"), ("Alt-1…9", "jump to conversation"),
                    ("PgUp/PgDn, wheel", "scroll history (loads older messages)"), ("Ctrl-U", "contacts & groups"),
                    ("/", "search history"), ("Ctrl-A", "attach a file"), ("Ctrl-R", "react to last message"),
                    ("Ctrl-Q", "quote last message"), ("Ctrl-O / click", "open, save or copy an attachment (or open a link)"),
                    ("Ctrl-G", "message actions: react, quote, edit, delete, forward, copy, info"),
                    ("Ctrl-T", "conversation: disappearing messages, mute, archive, block, safety number, groups"),
                    ("Ctrl-E / m", "mute conversation"), ("Ctrl-S / F2", "settings"), ("Ctrl-L", "redraw"), ("Ctrl-X", "clear composer"),
                    ("Ctrl-Z", "put a failed message back in the composer"), (":smile:", "emoji shortcodes; a picker opens as you type"),
                    ("Ctrl-C", "quit")]
            for i, (k, v) in enumerate(keys[:h - 3]):
                out.append(T.move(top + 1 + i, body_left) + bg + T.fg(th.accent) + pad(k, 22) + T.fg(th.foreground) + truncate(v, body_w - 22) + T.RESET)
        elif name == "quit":
            out.append(T.move(top + 2, body_left) + bg + T.fg(th.foreground) + pad("Leave the channel? [y/N]", body_w, align="center") + T.RESET)
        elif name == "attachment":
            y = top + 1
            visible = min(10, len(self.att_items))
            first = max(0, min(self.att_index - visible + 1, len(self.att_items) - visible))
            for i, att in enumerate(self.att_items[first:first + visible], start=first):
                selected = i == self.att_index
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                label = self._attachment_filename(att)
                ctype = str(att.get("contentType", "") or "")
                right = self._fmt_size(int(att.get("size", 0) or 0))
                if ctype.startswith("image/"):
                    info = kitty.probe_dimensions(Path(str(att.get("path", ""))))
                    if info and info.width:
                        right = f"{info.width}×{info.height} · " + right
                icon = {"image": "󰋩", "video": "󰕧", "audio": "󰎈"}.get(ctype.split("/")[0], "󰈔")
                when = self._fmt_time(int(att.get("_ts", 0) or 0))
                who = truncate(str(att.get("_who", "")), 14)
                meta = f"{who} · {when}" if who and when else (who or when)
                name_w = max(10, body_w - 24 - 28)
                out.append(T.move(y, body_left) + style + ("▶ " if selected else "  ") + icon + " " + pad(truncate(label, name_w), name_w)
                           + T.fg(th.accent if selected else th.dim) + pad(truncate(meta, 27), 28)
                           + T.fg(th.bright_foreground if selected else th.dim) + pad(right, 22, align="right") + T.RESET)
                y += 1
            if len(self.att_items) > visible:
                out.append(T.move(y, body_left) + bg + T.fg(th.dim) + f"{self.att_index + 1} / {len(self.att_items)}" + T.RESET)
            y += 1
            cur = self.att_items[self.att_index] if self.att_items else {}
            is_img = str(cur.get("contentType", "")).startswith("image/") and self.graphics
            view_label = ("hide from the chat" if self._inline_wanted(str(cur.get("path", ""))) else "show in the chat") if is_img else "show in the chat (images only)"
            actions = [("Enter / o", "open (larger view)"), ("v", view_label), ("s", f"save to {self.cfg.save_dir}"), ("a", "save as…"), ("c", "copy path"), ("Esc", "back")]
            for k, v in actions:
                if y < top + h - 1:
                    out.append(T.move(y, body_left) + bg + T.fg(th.accent) + pad(k, 12) + T.fg(th.foreground) + truncate(v, body_w - 12) + T.RESET)
                    y += 1
        elif name == "link":
            out.extend(self._draw_link(top, left, w, h))
        return "".join(out)

    def _draw_settings(self, top: int, left: int, w: int, h: int) -> list[str]:
        th = self.theme
        bg = T.bg(th.panel)
        body_left = left + 2
        body_w = w - 4
        out: list[str] = []
        rows: list[tuple[str, dict | None]] = []
        section = None
        for spec in SETTINGS:
            if spec["section"] != section:
                section = spec["section"]
                rows.append((section, None))
            rows.append(("", spec))
        # keep the selected row visible
        sel_row = next(i for i, (_, sp) in enumerate(rows) if sp is not None and SETTINGS.index(sp) == self.settings_index)
        avail = h - 4
        first = max(0, min(sel_row - avail // 2, len(rows) - avail))
        y = top + 1
        for label, spec in rows[first:first + avail]:
            if spec is None:
                out.append(T.move(y, body_left) + bg + T.fg(th.accent) + T.BOLD + label.upper() + T.RESET)
            else:
                selected = SETTINGS.index(spec) == self.settings_index
                value = getattr(self.cfg, spec["key"])
                if spec["type"] == "bool":
                    shown = "● on " if value else "○ off"
                else:
                    shown = str(value)
                if spec["key"] in self.settings_pending:
                    shown += "  ⟳ restart"
                style = T.bg(th.selection) + T.fg(th.bright_foreground) if selected else bg + T.fg(th.foreground)
                out.append(T.move(y, body_left) + style + ("▶ " if selected else "  ") + pad(truncate(spec["label"], 30), 30)
                           + T.fg(th.bright_foreground if selected else th.cyan) + pad(truncate(shown, body_w - 34), body_w - 34) + T.RESET)
            y += 1
        spec = SETTINGS[self.settings_index]
        help_text = self.overlay_message or spec.get("help", "")
        out.append(T.move(top + h - 3, body_left) + bg + T.fg(th.red if self.overlay_message else th.dim) + truncate(help_text, body_w) + T.RESET)
        footer = "↑↓ choose · Enter/→ next value · ← previous · Esc close"
        if self.settings_pending:
            footer += " · r restart bridge now"
        out.append(T.move(top + h - 2, body_left) + bg + T.fg(th.dim) + truncate(footer, body_w) + T.RESET)
        return out

    def _draw_link(self, top: int, left: int, w: int, h: int) -> list[str]:
        assert self.term
        th = self.theme
        bg = T.bg(th.panel)
        out = [T.move(top + 1, left + 2) + bg + T.fg(th.foreground)
               + truncate(clean_text(self.overlay_message or "Requesting a link from signal-cli…", single_line=True), w - 4) + T.RESET]
        if not self.link_uri:
            out.append(T.move(top + 3, left + 2) + bg + T.fg(th.dim) + "q to cancel" + T.RESET)
            return out
        y = top + 3
        drawn = False
        if self.cfg.qr_style in ("auto", "shell") and not self.link_shell_shown:
            self.link_shell_shown = True
            try:
                self.link_shell_ok = qr.shell_show_qr(qr.qr_png_file(self.link_uri, self.paths.run_dir))
            except qr.QrUnavailable:
                self.link_shell_ok = False
        if self.link_shell_ok:
            for line in ("The QR code is showing on your screen (Omarchy shell).",
                         "Esc there only hides it; this window keeps waiting."):
                out.append(T.move(y, left + 2) + bg + T.fg(th.foreground) + truncate(line, w - 4) + T.RESET)
                y += 1
            drawn = True
        if not drawn and self.graphics and self.cfg.qr_style in ("auto", "image"):
            try:
                if not self.link_png_id:
                    png = qr.qr_png(self.link_uri)
                    self.link_png_id = kitty.image_id_for("link:" + self.link_uri)
                    out.append(kitty.encode_transmit_only(png, self.link_png_id))
                cols, rows = qr.qr_cells(self.term.cell_w, self.term.cell_h, rows=min(self.cfg.qr_rows, h - 8))
                x = left + max(2, (w - cols) // 2)
                out.append(T.move(y, x) + kitty.encode_place(self.link_png_id, cols=cols, rows=rows))
                y += rows
                drawn = True
            except qr.QrUnavailable:
                drawn = False
        if not drawn:
            try:
                text_style = self.cfg.qr_style if self.cfg.qr_style in qr.TEXT_STYLES else "half"
                qr_lines = qr.qr_text_lines(self.link_uri, text_style)
            except qr.QrUnavailable:
                qr_lines = []
            if qr_lines:
                qw = max(str_width(l) for l in qr_lines)
                x = left + max(2, (w - qw) // 2)
                for line in qr_lines[:h - 8]:
                    out.append(T.move(y, x) + T.bg((255, 255, 255)) + T.fg((0, 0, 0)) + line + T.RESET)
                    y += 1
            else:
                out.append(T.move(y, left + 2) + bg + T.fg(th.red) + "qrencode is not installed; copy the URI instead:" + T.RESET)
                y += 1
                for line in wrap(self.link_uri, w - 4)[:6]:
                    out.append(T.move(y, left + 2) + bg + T.fg(th.foreground) + line + T.RESET)
                    y += 1
        y += 1
        note = ["After the scan the phone shows nothing until the link completes (10–60 s):",
                "keys are exchanged and contacts sync. Pull to refresh Linked devices later."]
        for line in note:
            if y < top + h - 2:
                out.append(T.move(y, left + 2) + bg + T.fg(th.dim) + truncate(line, w - 4) + T.RESET)
                y += 1
        elapsed = int(time.monotonic() - self.link_started) if self.link_started else 0
        spinner = "◐◓◑◒"[(self.frame // 4) % 4]
        out.append(T.move(top + h - 2, left + 2) + bg + T.fg(th.accent) + f"{spinner} waiting for your phone… {elapsed}s" + T.fg(th.dim) + "   q to cancel" + T.RESET)
        return out


def run_tui(cfg: Config, paths: Paths, initial: str = "") -> int:
    import sys
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("omarchy-signal tui needs a terminal", file=sys.stderr)
        return 2
    app = App(cfg, paths, initial=initial)
    try:
        return asyncio.run(app.run())
    except KeyboardInterrupt:
        return 0
