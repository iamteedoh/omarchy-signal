"""Normalise ``signal-cli`` receive envelopes into plain, sanitised records.

``signal-cli`` emits one ``receive`` notification per envelope. The envelope is
a nested, optional-everywhere structure. This module flattens it into
:class:`Event` objects the rest of the bridge understands, running every
remote-supplied string through :mod:`omarchy_signal.sanitize` on the way.
Anything unexpected (wrong types, missing keys) yields ``None`` rather than an
exception so a hostile or novel envelope can never crash the receive loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .sanitize import (GROUP_ID, E164, UUID, Recipient, clean_name, clean_text,
                       safe_filename)


@dataclass
class Attachment:
    id: str
    content_type: str
    filename: str
    size: int
    path: str = ""          # local path signal-cli stored it at (may be empty)
    width: int = 0
    height: int = 0
    caption: str = ""
    voice_note: bool = False

    def to_json(self) -> dict:
        return {
            "id": self.id, "contentType": self.content_type, "filename": self.filename,
            "size": self.size, "path": self.path, "width": self.width, "height": self.height,
            "caption": self.caption, "voiceNote": self.voice_note,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Attachment":
        return cls(
            id=str(d.get("id", "")), content_type=str(d.get("contentType", "")),
            filename=str(d.get("filename", "")), size=int(d.get("size", 0) or 0),
            path=str(d.get("path", "")), width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0), caption=str(d.get("caption", "")),
            voice_note=bool(d.get("voiceNote", False)),
        )


@dataclass
class Event:
    """One thing that happened. ``kind`` selects which fields matter.

    kind = "message"   incoming (or synced outgoing) data message
    kind = "receipt"   delivery/read/viewed receipt for our own messages
    kind = "typing"    typing started/stopped
    kind = "reaction"  emoji reaction to a message
    kind = "remote_delete"
    kind = "call"      (informational only)
    """
    kind: str
    conversation: Recipient
    timestamp: int                      # ms since epoch (Signal's message id)
    sender: Recipient | None = None
    sender_name: str = ""
    outgoing: bool = False              # True for sync messages sent from our other devices
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    group_name: str = ""
    quote_text: str = ""
    quote_author: str = ""
    quote_timestamp: int = 0
    mentions: list[str] = field(default_factory=list)
    expires_in: int = 0
    view_once: bool = False
    # receipts
    receipt_type: str = ""              # delivery | read | viewed
    receipt_timestamps: list[int] = field(default_factory=list)
    # typing
    typing: str = ""                    # started | stopped
    # reaction
    emoji: str = ""
    reaction_target_ts: int = 0
    reaction_target_author: str = ""
    reaction_removed: bool = False
    # remote delete
    delete_target_ts: int = 0
    # edits
    edit_target_ts: int = 0
    story: bool = False


def _int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _party(number: Any, uuid: Any) -> Recipient | None:
    num = _str(number)
    if num and E164.match(num):
        return Recipient("number", num)
    uid = _str(uuid)
    if uid and UUID.match(uid):
        return Recipient("uuid", uid.lower())
    return None


def _group_key(group_info: Any) -> tuple[Recipient | None, str]:
    if not isinstance(group_info, dict):
        return None, ""
    gid = _str(group_info.get("groupId"))
    if not gid or not GROUP_ID.match(gid):
        return None, ""
    return Recipient("group", gid), clean_name(group_info.get("groupName"))


def _attachments(items: Any) -> list[Attachment]:
    out: list[Attachment] = []
    if not isinstance(items, list):
        return out
    for item in items[:32]:
        if not isinstance(item, dict):
            continue
        att_id = _str(item.get("id"))
        content_type = clean_name(item.get("contentType"), max_length=100).lower()
        if not content_type or "/" not in content_type:
            content_type = "application/octet-stream"
        out.append(Attachment(
            id=safe_filename(att_id, fallback=""),
            content_type=content_type,
            filename=safe_filename(item.get("filename"), fallback=att_id or "attachment"),
            size=max(0, _int(item.get("size"))),
            path="",
            width=max(0, _int(item.get("width"))),
            height=max(0, _int(item.get("height"))),
            caption=clean_text(item.get("caption"), max_length=2000),
            voice_note=bool(item.get("voiceNote", False)),
        ))
    return out


def parse_receive(params: Any, own_account: str = "") -> list[Event]:
    """Turn the ``params`` of one ``receive`` notification into events."""
    if not isinstance(params, dict):
        return []
    envelope = params.get("envelope")
    if not isinstance(envelope, dict):
        return []
    if params.get("exception") or envelope.get("exception"):
        return []
    if not own_account:
        own_account = _str(params.get("account"))

    sender = _party(envelope.get("sourceNumber"), envelope.get("sourceUuid"))
    if sender is None:
        sender = _party(envelope.get("source"), None)
    sender_name = clean_name(envelope.get("sourceName"))
    env_ts = _int(envelope.get("timestamp"))
    events: list[Event] = []

    data = envelope.get("dataMessage")
    edit = envelope.get("editMessage")
    edit_target = 0
    if isinstance(edit, dict) and isinstance(edit.get("dataMessage"), dict):
        data = edit["dataMessage"]
        edit_target = _int(edit.get("targetSentTimestamp"))
    if isinstance(data, dict) and sender is not None:
        ev = _data_message(data, sender, sender_name, env_ts, outgoing=False)
        if ev is not None:
            ev.edit_target_ts = edit_target
            events.append(ev)

    sync = envelope.get("syncMessage")
    if isinstance(sync, dict):
        sent = sync.get("sentMessage")
        if isinstance(sent, dict):
            inner = sent.get("dataMessage") if isinstance(sent.get("dataMessage"), dict) else sent
            dest = _party(sent.get("destinationNumber") or sent.get("destination"), sent.get("destinationUuid"))
            me = _party(own_account, None) if own_account else sender
            ev = _data_message(inner, me or sender, "", _int(sent.get("timestamp"), env_ts),
                               outgoing=True, destination=dest)
            if ev is not None:
                events.append(ev)
        read = sync.get("readMessages")
        if isinstance(read, list):
            # Messages we read on another device: mark them read here too.
            for item in read[:256]:
                if not isinstance(item, dict):
                    continue
                who = _party(item.get("senderNumber") or item.get("sender"), item.get("senderUuid"))
                if who is None:
                    continue
                events.append(Event(kind="read_sync", conversation=who, timestamp=_int(item.get("timestamp")),
                                    sender=who))

    receipt = envelope.get("receiptMessage")
    if isinstance(receipt, dict) and sender is not None:
        rtype = _str(receipt.get("type")).lower()
        if receipt.get("isDelivery"):
            rtype = "delivery"
        elif receipt.get("isRead"):
            rtype = "read"
        elif receipt.get("isViewed"):
            rtype = "viewed"
        stamps = [_int(t) for t in receipt.get("timestamps", []) if _int(t) > 0] \
            if isinstance(receipt.get("timestamps"), list) else []
        if rtype in ("delivery", "read", "viewed") and stamps:
            events.append(Event(kind="receipt", conversation=sender, timestamp=env_ts, sender=sender,
                                receipt_type=rtype, receipt_timestamps=stamps[:256]))

    typing = envelope.get("typingMessage")
    if isinstance(typing, dict) and sender is not None:
        action = _str(typing.get("action")).upper()
        conv, gname = _group_key(typing.get("groupInfo") or {"groupId": typing.get("groupId")})
        events.append(Event(kind="typing", conversation=conv or sender, timestamp=env_ts, sender=sender,
                            sender_name=sender_name, group_name=gname,
                            typing="started" if action == "STARTED" else "stopped"))

    return events


def _data_message(data: dict, sender: Recipient, sender_name: str, fallback_ts: int, *,
                  outgoing: bool, destination: Recipient | None = None) -> Event | None:
    group, group_name = _group_key(data.get("groupInfo"))
    if group is not None:
        conversation = group
    elif outgoing:
        if destination is None:
            return None
        conversation = destination
    else:
        conversation = sender

    ts = _int(data.get("timestamp"), fallback_ts)
    if ts <= 0:
        ts = int(time.time() * 1000)

    reaction = data.get("reaction")
    if isinstance(reaction, dict):
        target = _party(reaction.get("targetAuthorNumber") or reaction.get("targetAuthor"),
                        reaction.get("targetAuthorUuid"))
        return Event(kind="reaction", conversation=conversation, timestamp=ts, sender=sender,
                     sender_name=sender_name, outgoing=outgoing, group_name=group_name,
                     emoji=clean_name(reaction.get("emoji"), max_length=16),
                     reaction_target_ts=_int(reaction.get("targetSentTimestamp")),
                     reaction_target_author=target.key if target else "",
                     reaction_removed=bool(reaction.get("isRemove", False)))

    remote_delete = data.get("remoteDelete")
    if isinstance(remote_delete, dict):
        return Event(kind="remote_delete", conversation=conversation, timestamp=ts, sender=sender,
                     sender_name=sender_name, outgoing=outgoing, group_name=group_name,
                     delete_target_ts=_int(remote_delete.get("timestamp")))

    text = clean_text(data.get("message"))
    attachments = _attachments(data.get("attachments"))
    sticker = data.get("sticker")
    if not text and not attachments and isinstance(sticker, dict):
        text = "[sticker]"
    if not text and not attachments:
        # Group updates, expiration-timer changes, profile key updates, etc.
        if data.get("groupInfo") and isinstance(data["groupInfo"], dict) and \
                _str(data["groupInfo"].get("type")).upper() == "UPDATE":
            text = "[group updated]"
        elif _int(data.get("expiresInSeconds")) and data.get("isExpirationUpdate"):
            text = "[disappearing message timer changed]"
        else:
            return None

    quote = data.get("quote")
    quote_text = quote_author = ""
    quote_ts = 0
    if isinstance(quote, dict):
        quote_text = clean_text(quote.get("text"), max_length=2000, single_line=True)
        qa = _party(quote.get("authorNumber") or quote.get("author"), quote.get("authorUuid"))
        quote_author = qa.key if qa else ""
        quote_ts = _int(quote.get("id"))

    mentions: list[str] = []
    if isinstance(data.get("mentions"), list):
        placeholders = []
        for m in data["mentions"][:64]:
            if isinstance(m, dict):
                who = _party(m.get("number"), m.get("uuid"))
                if who:
                    mentions.append(who.key)
                label = clean_name(m.get("name")) or (who.value if who else "")
                placeholders.append((_int(m.get("start"), -1), label))
        # Signal sends U+FFFC where a mention sits; show it as @name.
        if "\ufffc" in text and placeholders:
            placeholders.sort()
            parts = text.split("\ufffc")
            rebuilt = parts[0]
            for i, part in enumerate(parts[1:]):
                label = placeholders[i][1] if i < len(placeholders) else ""
                rebuilt += ("@" + label if label else "@?") + part
            text = rebuilt

    return Event(kind="message", conversation=conversation, timestamp=ts, sender=sender,
                 sender_name=sender_name, outgoing=outgoing, text=text, attachments=attachments,
                 group_name=group_name, quote_text=quote_text, quote_author=quote_author,
                 quote_timestamp=quote_ts, mentions=mentions,
                 expires_in=max(0, _int(data.get("expiresInSeconds"))),
                 view_once=bool(data.get("viewOnce", False)),
                 story=isinstance(data.get("storyContext"), dict))
