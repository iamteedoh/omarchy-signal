"""Wire format between the bridge and its local clients (TUI, CLI, Quickshell).

Newline-delimited JSON over a Unix socket. Requests carry an ``id`` and an
``op``; replies echo the ``id``. Unsolicited ``event`` objects are pushed to
clients that sent ``subscribe``.

    -> {"id": 1, "op": "conversations"}
    <- {"id": 1, "ok": true, "result": [...]}
    <- {"event": "message", "data": {...}}

Limits are enforced on both sides: a request line longer than
:data:`MAX_REQUEST_BYTES` closes the connection, and every op has an explicit
parameter schema (see :func:`validate_request`) so unknown keys are ignored
and wrong types are rejected before any code runs on them.
"""

from __future__ import annotations

import json
from typing import Any

MAX_REQUEST_BYTES = 1 * 1024 * 1024
MAX_EVENT_BYTES = 8 * 1024 * 1024
PROTOCOL_VERSION = 1

# op -> {param: (type, required)}
OPS: dict[str, dict[str, tuple[type | tuple[type, ...], bool]]] = {
    "ping": {},
    "status": {},
    "subscribe": {},
    "unsubscribe": {},
    "conversations": {"includeArchived": (bool, False)},
    "history": {"conversation": (str, True), "before": (int, False), "limit": (int, False)},
    "message": {"conversation": (str, True), "ts": (int, True)},
    "search": {"query": (str, True), "limit": (int, False)},
    "contacts": {},
    "groups": {},
    "refresh": {},
    "resolve": {"query": (str, True)},
    "send": {"conversation": (str, True), "text": (str, False), "attachments": (list, False),
             "quoteTs": (int, False), "quoteAuthor": (str, False), "quoteText": (str, False)},
    "markRead": {"conversation": (str, True)},
    "typing": {"conversation": (str, True), "stop": (bool, False)},
    "react": {"conversation": (str, True), "ts": (int, True), "author": (str, True), "emoji": (str, True),
              "remove": (bool, False)},
    "mute": {"conversation": (str, True), "muted": (bool, True)},
    "archive": {"conversation": (str, True), "archived": (bool, True)},
    "attachment": {"conversation": (str, True), "ts": (int, True), "id": (str, True)},
    "link": {"deviceName": (str, False)},
    "linkFinish": {},
    "clearHistory": {},
    "demo": {"text": (str, False)},
    "shutdown": {},
}


class ProtocolError(ValueError):
    pass


def encode(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ProtocolError("malformed JSON") from exc


def validate_request(msg: Any) -> tuple[Any, str, dict]:
    """Return ``(id, op, params)`` for a well-formed request or raise."""
    if not isinstance(msg, dict):
        raise ProtocolError("request must be an object")
    req_id = msg.get("id")
    if not isinstance(req_id, (int, str)) or isinstance(req_id, bool):
        raise ProtocolError("request id must be an int or string")
    if isinstance(req_id, str) and len(req_id) > 64:
        raise ProtocolError("request id too long")
    op = msg.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise ProtocolError("unknown op")
    schema = OPS[op]
    params: dict = {}
    for name, (typ, required) in schema.items():
        if name in msg:
            value = msg[name]
            if isinstance(value, bool) and typ is int:
                raise ProtocolError(f"{name} must be an integer")
            if not isinstance(value, typ):
                raise ProtocolError(f"{name} has the wrong type")
            if isinstance(value, str) and len(value) > 64 * 1024:
                raise ProtocolError(f"{name} is too long")
            if isinstance(value, list):
                if len(value) > 32 or not all(isinstance(v, str) and len(v) < 4096 for v in value):
                    raise ProtocolError(f"{name} must be a short list of strings")
            params[name] = value
        elif required:
            raise ProtocolError(f"{name} is required")
    return req_id, op, params


def reply(req_id: Any, result: Any = None) -> dict:
    return {"id": req_id, "ok": True, "result": result}


def error(req_id: Any, message: str, *, code: str = "error") -> dict:
    return {"id": req_id, "ok": False, "error": str(message)[:500], "code": code}


def event(name: str, data: Any = None) -> dict:
    return {"event": name, "data": data}
