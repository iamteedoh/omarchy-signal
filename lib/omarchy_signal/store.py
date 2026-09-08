"""Local message history and contact cache (SQLite, owner-only permissions).

Signal has no server-side history and ``signal-cli`` keeps none, so the bridge
records what it sees. This is the same trade-off Signal Desktop makes; the
database sits next to ``signal-cli``'s own (equally plaintext) key store under
the user's home directory and is created ``0600``. History can be disabled or
time-limited from ``config.toml``; :meth:`Store.prune` enforces the limit.

Every string that enters the store has already been through
:mod:`omarchy_signal.sanitize`; the store trusts its callers on that and only
enforces types and lengths as a backstop.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .envelope import Attachment, Event
from .sanitize import Recipient, clean_name, clean_text

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  key TEXT PRIMARY KEY,          -- number:+1..., uuid:..., group:...
  kind TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  last_ts INTEGER NOT NULL DEFAULT 0,
  last_preview TEXT NOT NULL DEFAULT '',
  unread INTEGER NOT NULL DEFAULT 0,
  muted INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  typing_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  conversation TEXT NOT NULL,
  ts INTEGER NOT NULL,           -- Signal sent-timestamp (message identity)
  sender TEXT NOT NULL,          -- recipient key of the author
  sender_name TEXT NOT NULL DEFAULT '',
  outgoing INTEGER NOT NULL DEFAULT 0,
  body TEXT NOT NULL DEFAULT '',
  attachments TEXT NOT NULL DEFAULT '[]',
  quote_text TEXT NOT NULL DEFAULT '',
  quote_author TEXT NOT NULL DEFAULT '',
  quote_ts INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',   -- '', sending, sent, delivered, read, failed
  reactions TEXT NOT NULL DEFAULT '{}',
  deleted INTEGER NOT NULL DEFAULT 0,
  edited INTEGER NOT NULL DEFAULT 0,
  expires_in INTEGER NOT NULL DEFAULT 0,
  received_at INTEGER NOT NULL,
  PRIMARY KEY (conversation, ts, sender)
);
CREATE INDEX IF NOT EXISTS messages_conv_ts ON messages(conversation, ts);
CREATE TABLE IF NOT EXISTS contacts (
  key TEXT PRIMARY KEY,
  number TEXT NOT NULL DEFAULT '',
  uuid TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  profile_name TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '',
  blocked INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '',
  updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS groups (
  key TEXT PRIMARY KEY,
  group_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  members TEXT NOT NULL DEFAULT '[]',
  blocked INTEGER NOT NULL DEFAULT 0,
  is_member INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class Conversation:
    key: str
    kind: str
    name: str
    last_ts: int
    last_preview: str
    unread: int
    muted: bool = False
    archived: bool = False
    typing: bool = False

    def to_json(self) -> dict:
        return {"key": self.key, "kind": self.kind, "name": self.name, "lastTs": self.last_ts,
                "preview": self.last_preview, "unread": self.unread, "muted": self.muted,
                "archived": self.archived, "typing": self.typing}


@dataclass
class Message:
    conversation: str
    ts: int
    sender: str
    sender_name: str
    outgoing: bool
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    quote_text: str = ""
    quote_author: str = ""
    quote_ts: int = 0
    status: str = ""
    reactions: dict[str, str] = field(default_factory=dict)   # sender key -> emoji
    deleted: bool = False
    edited: bool = False
    expires_in: int = 0
    received_at: int = 0

    def to_json(self) -> dict:
        return {"conversation": self.conversation, "ts": self.ts, "sender": self.sender,
                "senderName": self.sender_name, "outgoing": self.outgoing, "body": self.body,
                "attachments": [a.to_json() for a in self.attachments], "quoteText": self.quote_text,
                "quoteAuthor": self.quote_author, "quoteTs": self.quote_ts, "status": self.status,
                "reactions": dict(self.reactions), "deleted": self.deleted, "edited": self.edited,
                "expiresIn": self.expires_in, "receivedAt": self.received_at}


def _now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        is_memory = str(path) == ":memory:"
        if not is_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            # Create the file with owner-only permissions before SQLite opens it,
            # so there is no window in which it is world-readable.
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(":memory:" if is_memory else str(self.path), isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        if not is_memory:
            for suffix in ("-wal", "-shm"):
                side = Path(str(self.path) + suffix)
                if side.exists():
                    os.chmod(side, 0o600)

    def close(self) -> None:
        self.db.close()

    # -- conversations -----------------------------------------------------

    def upsert_conversation(self, rec: Recipient, *, name: str = "", last_ts: int = 0,
                            preview: str = "", unread_delta: int = 0) -> None:
        name = clean_name(name)
        preview = clean_text(preview, max_length=200, single_line=True)
        self.db.execute(
            """INSERT INTO conversations(key, kind, name, last_ts, last_preview, unread)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 name = CASE WHEN excluded.name != '' THEN excluded.name ELSE conversations.name END,
                 last_ts = MAX(conversations.last_ts, excluded.last_ts),
                 last_preview = CASE WHEN excluded.last_ts >= conversations.last_ts AND excluded.last_ts > 0
                                     THEN excluded.last_preview ELSE conversations.last_preview END,
                 unread = MAX(0, conversations.unread + ?)""",
            (rec.key, rec.kind, name, last_ts, preview, max(0, unread_delta), unread_delta))

    def set_typing(self, key: str, until_ms: int) -> None:
        self.db.execute("UPDATE conversations SET typing_until = ? WHERE key = ?", (until_ms, key))

    def mark_read(self, key: str) -> int:
        cur = self.db.execute("UPDATE conversations SET unread = 0 WHERE key = ? AND unread > 0", (key,))
        return cur.rowcount

    def set_muted(self, key: str, muted: bool) -> None:
        self.db.execute("UPDATE conversations SET muted = ? WHERE key = ?", (1 if muted else 0, key))

    def set_archived(self, key: str, archived: bool) -> None:
        self.db.execute("UPDATE conversations SET archived = ? WHERE key = ?", (1 if archived else 0, key))

    def conversation(self, key: str) -> Conversation | None:
        row = self.db.execute("SELECT * FROM conversations WHERE key = ?", (key,)).fetchone()
        return self._conv(row) if row else None

    def conversations(self, *, include_archived: bool = False, limit: int = 500) -> list[Conversation]:
        sql = "SELECT * FROM conversations"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY last_ts DESC, key ASC LIMIT ?"
        return [self._conv(r) for r in self.db.execute(sql, (limit,))]

    def total_unread(self) -> int:
        row = self.db.execute("SELECT COALESCE(SUM(unread), 0) AS n FROM conversations WHERE muted = 0").fetchone()
        return int(row["n"])

    def _conv(self, row: sqlite3.Row) -> Conversation:
        name = row["name"]
        if not name:
            name = self.display_name(row["key"])
        return Conversation(key=row["key"], kind=row["kind"], name=name, last_ts=row["last_ts"],
                            last_preview=row["last_preview"], unread=row["unread"],
                            muted=bool(row["muted"]), archived=bool(row["archived"]),
                            typing=row["typing_until"] > _now_ms())

    # -- messages ----------------------------------------------------------

    def add_event_message(self, ev: Event, *, count_unread: bool = True) -> bool:
        """Insert a message event. Returns True if it was new."""
        if ev.kind != "message" or ev.sender is None:
            return False
        sender = ev.sender.key
        exists = self.db.execute("SELECT 1 FROM messages WHERE conversation = ? AND ts = ? AND sender = ?",
                                 (ev.conversation.key, ev.timestamp, sender)).fetchone()
        if exists and not ev.edit_target_ts:
            return False
        if ev.edit_target_ts:
            cur = self.db.execute(
                "UPDATE messages SET body = ?, edited = 1 WHERE conversation = ? AND ts = ? AND sender = ?",
                (ev.text, ev.conversation.key, ev.edit_target_ts, sender))
            if cur.rowcount:
                return False
        body = clean_text(ev.text)
        self.db.execute(
            """INSERT OR REPLACE INTO messages(conversation, ts, sender, sender_name, outgoing, body, attachments,
               quote_text, quote_author, quote_ts, status, expires_in, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ev.conversation.key, ev.timestamp, sender, clean_name(ev.sender_name), 1 if ev.outgoing else 0,
             body, json.dumps([a.to_json() for a in ev.attachments]), ev.quote_text, ev.quote_author,
             ev.quote_timestamp, "sent" if ev.outgoing else "", ev.expires_in, _now_ms()))
        preview = body if body else ("[" + (ev.attachments[0].content_type.split("/")[0] if ev.attachments else "attachment") + "]")
        self.upsert_conversation(ev.conversation, name=ev.group_name or (ev.sender_name if not ev.outgoing and ev.conversation.kind != "group" else ""),
                                 last_ts=ev.timestamp, preview=preview,
                                 unread_delta=(1 if (count_unread and not ev.outgoing) else 0))
        if ev.sender_name and ev.sender.kind in ("number", "uuid"):
            self.remember_contact_name(ev.sender, ev.sender_name)
        return True

    def add_outgoing(self, conversation: Recipient, ts: int, sender_key: str, body: str,
                     attachments: Iterable[Attachment] = (), *, status: str = "sending",
                     quote_text: str = "", quote_author: str = "", quote_ts: int = 0) -> None:
        body = clean_text(body)
        atts = [a.to_json() for a in attachments]
        self.db.execute(
            """INSERT OR REPLACE INTO messages(conversation, ts, sender, sender_name, outgoing, body, attachments,
               quote_text, quote_author, quote_ts, status, received_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation.key, ts, sender_key, "", body, json.dumps(atts), quote_text, quote_author, quote_ts,
             status, _now_ms()))
        preview = body if body else "[attachment]"
        self.upsert_conversation(conversation, last_ts=ts, preview=preview)

    def set_status(self, conversation_key: str, ts: int, status: str) -> None:
        self.db.execute("UPDATE messages SET status = ? WHERE conversation = ? AND ts = ? AND outgoing = 1",
                        (status, conversation_key, ts))

    def apply_receipt(self, sender: Recipient, receipt_type: str, timestamps: Iterable[int]) -> int:
        """Receipts arrive from a person, for messages we sent to them (or to
        a group they are in). Promote status monotonically."""
        order = {"": 0, "sending": 1, "sent": 2, "delivered": 3, "read": 4, "viewed": 5, "failed": -1}
        target = {"delivery": "delivered", "read": "read", "viewed": "viewed"}.get(receipt_type, "")
        if not target:
            return 0
        n = 0
        for ts in timestamps:
            rows = self.db.execute("SELECT conversation, status FROM messages WHERE ts = ? AND outgoing = 1", (ts,)).fetchall()
            for row in rows:
                conv = row["conversation"]
                if not (conv == sender.key or conv.startswith("group:")):
                    continue
                if order.get(row["status"], 0) < order[target]:
                    self.db.execute("UPDATE messages SET status = ? WHERE conversation = ? AND ts = ? AND outgoing = 1",
                                    (target, conv, ts))
                    n += 1
        return n

    def apply_reaction(self, ev: Event) -> bool:
        if ev.kind != "reaction" or ev.sender is None:
            return False
        row = self.db.execute("SELECT reactions FROM messages WHERE conversation = ? AND ts = ?",
                              (ev.conversation.key, ev.reaction_target_ts)).fetchone()
        if not row:
            return False
        try:
            reactions = json.loads(row["reactions"]) or {}
        except ValueError:
            reactions = {}
        if not isinstance(reactions, dict):
            reactions = {}
        if ev.reaction_removed:
            reactions.pop(ev.sender.key, None)
        else:
            reactions[ev.sender.key] = ev.emoji
        self.db.execute("UPDATE messages SET reactions = ? WHERE conversation = ? AND ts = ?",
                        (json.dumps(reactions), ev.conversation.key, ev.reaction_target_ts))
        return True

    def apply_remote_delete(self, ev: Event) -> bool:
        if ev.kind != "remote_delete" or ev.sender is None:
            return False
        cur = self.db.execute(
            "UPDATE messages SET deleted = 1, body = '', attachments = '[]' WHERE conversation = ? AND ts = ? AND sender = ?",
            (ev.conversation.key, ev.delete_target_ts, ev.sender.key))
        return cur.rowcount > 0

    def set_attachment_path(self, conversation_key: str, ts: int, attachment_id: str, path: str) -> None:
        row = self.db.execute("SELECT attachments, sender FROM messages WHERE conversation = ? AND ts = ?",
                              (conversation_key, ts)).fetchone()
        if not row:
            return
        try:
            atts = json.loads(row["attachments"])
        except ValueError:
            return
        changed = False
        for a in atts:
            if isinstance(a, dict) and a.get("id") == attachment_id:
                a["path"] = path
                changed = True
        if changed:
            self.db.execute("UPDATE messages SET attachments = ? WHERE conversation = ? AND ts = ? AND sender = ?",
                            (json.dumps(atts), conversation_key, ts, row["sender"]))

    def history(self, conversation_key: str, *, before_ts: int = 0, limit: int = 50) -> list[Message]:
        limit = max(1, min(500, int(limit)))
        if before_ts > 0:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE conversation = ? AND ts < ? ORDER BY ts DESC LIMIT ?",
                (conversation_key, before_ts, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM messages WHERE conversation = ? ORDER BY ts DESC LIMIT ?",
                (conversation_key, limit)).fetchall()
        return [self._msg(r) for r in reversed(rows)]

    def message(self, conversation_key: str, ts: int) -> Message | None:
        row = self.db.execute("SELECT * FROM messages WHERE conversation = ? AND ts = ? ORDER BY outgoing DESC LIMIT 1",
                              (conversation_key, ts)).fetchone()
        return self._msg(row) if row else None

    def search(self, query: str, *, limit: int = 50) -> list[Message]:
        query = clean_text(query, max_length=200, single_line=True).strip()
        if not query:
            return []
        like = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.db.execute(
            "SELECT * FROM messages WHERE body LIKE ? ESCAPE '\\' AND deleted = 0 ORDER BY ts DESC LIMIT ?",
            (like, max(1, min(200, limit)))).fetchall()
        return [self._msg(r) for r in rows]

    def _msg(self, row: sqlite3.Row) -> Message:
        try:
            atts = [Attachment.from_json(a) for a in json.loads(row["attachments"]) if isinstance(a, dict)]
        except ValueError:
            atts = []
        try:
            reactions = json.loads(row["reactions"]) or {}
        except ValueError:
            reactions = {}
        sender_name = row["sender_name"] or self.display_name(row["sender"])
        return Message(conversation=row["conversation"], ts=row["ts"], sender=row["sender"],
                       sender_name=sender_name, outgoing=bool(row["outgoing"]), body=row["body"],
                       attachments=atts, quote_text=row["quote_text"], quote_author=row["quote_author"],
                       quote_ts=row["quote_ts"], status=row["status"],
                       reactions=reactions if isinstance(reactions, dict) else {},
                       deleted=bool(row["deleted"]), edited=bool(row["edited"]),
                       expires_in=row["expires_in"], received_at=row["received_at"])

    # -- contacts / groups -------------------------------------------------

    def remember_contact_name(self, rec: Recipient, profile_name: str) -> None:
        profile_name = clean_name(profile_name)
        if not profile_name:
            return
        self.db.execute(
            """INSERT INTO contacts(key, number, uuid, profile_name, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET profile_name = excluded.profile_name, updated_at = excluded.updated_at""",
            (rec.key, rec.value if rec.kind == "number" else "", rec.value if rec.kind == "uuid" else "",
             profile_name, _now_ms()))

    def replace_contacts(self, contacts: Iterable[dict]) -> int:
        """``contacts`` are already-sanitised dicts from the signal-cli listContacts result."""
        n = 0
        now = _now_ms()
        for c in contacts:
            key = c.get("key")
            if not key:
                continue
            self.db.execute(
                """INSERT INTO contacts(key, number, uuid, name, profile_name, username, blocked, color, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET number = excluded.number, uuid = excluded.uuid,
                     name = excluded.name, profile_name = excluded.profile_name, username = excluded.username,
                     blocked = excluded.blocked, color = excluded.color, updated_at = excluded.updated_at""",
                (key, c.get("number", ""), c.get("uuid", ""), c.get("name", ""), c.get("profileName", ""),
                 c.get("username", ""), 1 if c.get("blocked") else 0, c.get("color", ""), now))
            n += 1
        return n

    def replace_groups(self, groups: Iterable[dict]) -> int:
        n = 0
        now = _now_ms()
        for g in groups:
            key = g.get("key")
            if not key:
                continue
            self.db.execute(
                """INSERT INTO groups(key, group_id, name, members, blocked, is_member, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET name = excluded.name, members = excluded.members,
                     blocked = excluded.blocked, is_member = excluded.is_member, updated_at = excluded.updated_at""",
                (key, g.get("groupId", ""), g.get("name", ""), json.dumps(g.get("members", [])),
                 1 if g.get("blocked") else 0, 0 if g.get("isMember") is False else 1, now))
            if g.get("name"):
                self.db.execute("UPDATE conversations SET name = ? WHERE key = ?", (g["name"], key))
            n += 1
        return n

    def contacts(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM contacts WHERE blocked = 0 ORDER BY LOWER(COALESCE(NULLIF(name,''), NULLIF(profile_name,''), number, uuid))").fetchall()
        return [{"key": r["key"], "number": r["number"], "uuid": r["uuid"], "name": r["name"],
                 "profileName": r["profile_name"], "username": r["username"], "color": r["color"],
                 "displayName": r["name"] or r["profile_name"] or r["username"] or r["number"] or r["uuid"]}
                for r in rows]

    def groups(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM groups WHERE blocked = 0 AND is_member = 1 ORDER BY LOWER(name)").fetchall()
        out = []
        for r in rows:
            try:
                members = json.loads(r["members"])
            except ValueError:
                members = []
            out.append({"key": r["key"], "groupId": r["group_id"], "name": r["name"], "members": members})
        return out

    def display_name(self, key: str) -> str:
        row = self.db.execute("SELECT name, profile_name, username, number FROM contacts WHERE key = ?", (key,)).fetchone()
        if row:
            for candidate in (row["name"], row["profile_name"], row["username"], row["number"]):
                if candidate:
                    return candidate
        if key.startswith("uuid:"):
            # A uuid-only sender may be known by number under a different key.
            row = self.db.execute("SELECT name, profile_name, number FROM contacts WHERE uuid = ?", (key[5:],)).fetchone()
            if row:
                for candidate in (row["name"], row["profile_name"], row["number"]):
                    if candidate:
                        return candidate
        grp = self.db.execute("SELECT name FROM groups WHERE key = ?", (key,)).fetchone()
        if grp and grp["name"]:
            return grp["name"]
        _, _, value = key.partition(":")
        return value

    # -- maintenance -------------------------------------------------------

    def prune(self, retain_days: int) -> int:
        if retain_days <= 0:
            return 0
        cutoff = _now_ms() - retain_days * 86400 * 1000
        cur = self.db.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        return cur.rowcount

    def clear_history(self) -> None:
        self.db.execute("DELETE FROM messages")
        self.db.execute("UPDATE conversations SET unread = 0, last_preview = ''")
