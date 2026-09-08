#!/usr/bin/env python3
"""A stand-in for ``signal-cli jsonRpc`` used by the integration tests.

It speaks the same line-JSON-RPC dialect, answers the handful of methods the
bridge uses, and can be told (through a control file named in
``FAKE_SIGNAL_EVENTS``) to emit ``receive`` notifications. Nothing here talks
to the network. It also deliberately answers requests out of order to prove
the client correlates by id.
"""

import json
import os
import sys
import threading
import time

ACCOUNT = os.environ.get("FAKE_SIGNAL_ACCOUNT", "+15550001111")
EVENTS_FILE = os.environ.get("FAKE_SIGNAL_EVENTS", "")
SENT_LOG = os.environ.get("FAKE_SIGNAL_SENT_LOG", "")

out_lock = threading.Lock()


def emit(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def log_sent(method, params):
    if SENT_LOG:
        with open(SENT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"method": method, "params": params}) + "\n")


def event_pump():
    """Tail the events file; each line is a full receive params object."""
    if not EVENTS_FILE:
        return
    seen = 0
    while True:
        try:
            with open(EVENTS_FILE, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except FileNotFoundError:
            lines = []
        for line in lines[seen:]:
            seen += 1
            if line.strip():
                try:
                    params = json.loads(line)
                except ValueError:
                    continue
                params.setdefault("account", ACCOUNT)
                emit({"jsonrpc": "2.0", "method": "receive", "params": params})
        time.sleep(0.05)


def handle(req):
    method = req.get("method")
    params = req.get("params") or {}
    rid = req.get("id")
    if method == "version":
        return {"jsonrpc": "2.0", "result": {"version": "0.14.6-fake"}, "id": rid}
    if method == "listAccounts":
        accounts = [] if os.environ.get("FAKE_SIGNAL_UNLINKED") else [{"number": ACCOUNT, "uuid": "11111111-2222-3333-4444-555555555555"}]
        return {"jsonrpc": "2.0", "result": accounts, "id": rid}
    if method in ("send", "sendTyping", "sendReceipt", "sendReaction"):
        if params.get("account") != ACCOUNT:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Method requires valid account parameter", "data": None}, "id": rid}
        log_sent(method, params)
        if method == "send":
            if params.get("message") == "FAIL":
                return {"jsonrpc": "2.0", "error": {"code": -1, "message": "Failed to send message", "data": None}, "id": rid}
            ts = int(time.time() * 1000)
            return {"jsonrpc": "2.0", "result": {"timestamp": ts, "results": [{"recipientAddress": {"number": (params.get("recipient") or ["group"])[0]}, "type": "SUCCESS"}]}, "id": rid}
        return {"jsonrpc": "2.0", "result": {"timestamp": int(time.time() * 1000)}, "id": rid}
    if method == "listContacts":
        return {"jsonrpc": "2.0", "result": [
            {"number": "+15550002222", "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "name": "Trinity", "color": "blue",
             "isBlocked": False, "profile": {"givenName": "Trin", "familyName": "\x1b[31mEvil"}},
            {"number": "+15550003333", "uuid": "", "name": "", "isBlocked": False, "profile": {"givenName": "Morpheus"}},
            {"number": "+15550009999", "name": "Blocked Guy", "isBlocked": True},
            {"number": "not-a-number", "name": "Garbage"},
        ], "id": rid}
    if method == "listGroups":
        return {"jsonrpc": "2.0", "result": [
            {"id": "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==", "name": "Nebuchadnezzar ‮crew",
             "isMember": True, "isBlocked": False, "members": [{"number": "+15550002222"}, {"number": ACCOUNT}]},
            {"id": "bad", "name": "Broken"},
        ], "id": rid}
    if method == "startLink":
        return {"jsonrpc": "2.0", "result": {"deviceLinkUri": "sgnl://linkdevice?uuid=abc&pub_key=def"}, "id": rid}
    if method == "finishLink":
        time.sleep(0.2)
        return {"jsonrpc": "2.0", "result": {"number": ACCOUNT}, "id": rid}
    return {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not implemented", "data": None}, "id": rid}


def main():
    threading.Thread(target=event_pump, daemon=True).start()
    delayed = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            emit({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error", "data": None}, "id": None})
            continue
        resp = handle(req)
        # Answer every other request late, to exercise out-of-order matching.
        if delayed is None and req.get("method") not in ("version",):
            delayed = resp
            threading.Timer(0.05, emit, args=(resp,)).start()
            continue
        emit(resp)
        delayed = None
    if os.environ.get("FAKE_SIGNAL_CRASH_ON_EOF"):
        sys.exit(3)


if __name__ == "__main__":
    main()
