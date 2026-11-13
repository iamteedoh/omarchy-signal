# SPDX-License-Identifier: GPL-3.0-or-later
"""Asyncio client for the bridge socket, used by the CLI, the TUI and the
``events`` stream the Quickshell plugin consumes."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import struct
from typing import Any, Awaitable, Callable

from . import protocol
from .config import Paths, private_dir


class BridgeError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class BridgeUnavailable(BridgeError):
    pass


def server_is_us(writer: asyncio.StreamWriter) -> bool:
    """Whether the process at the other end of the socket runs as this user (SO_PEERCRED)."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return False
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
    except OSError:
        return False
    return uid == os.getuid()


EventHandler = Callable[[str, Any], Awaitable[None] | None]


class BridgeClient:
    def __init__(self, paths: Paths | None = None, *, on_event: EventHandler | None = None):
        self.paths = paths or Paths()
        self.on_event = on_event
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._task: asyncio.Task | None = None
        self.hello: dict = {}
        self.closed = asyncio.Event()

    async def connect(self, *, timeout: float = 5.0) -> dict:
        # Only talk to a socket in a private directory of ours, served by a
        # process of ours: anything else could be another user posing as the
        # bridge to read what is sent through it.
        run_dir = self.paths.run_dir
        if (run_dir.exists() or run_dir.is_symlink()) and not private_dir(run_dir):
            raise BridgeUnavailable(
                f"refusing to use {self.paths.run_dir}: not a private directory owned by this user", code="untrusted")
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.paths.socket), limit=protocol.MAX_EVENT_BYTES * 2), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise BridgeUnavailable(
                f"the omarchy-signal bridge is not running ({self.paths.socket}); "
                "start it with `systemctl --user start omarchy-signal`", code="offline") from exc
        if not server_is_us(self.writer):
            self.writer.close()
            raise BridgeUnavailable(f"refusing {self.paths.socket}: it is served by another user", code="untrusted")
        self._task = asyncio.create_task(self._read_loop())
        # First line is always the hello event.
        try:
            await asyncio.wait_for(self._hello_received.wait(), timeout)
        except asyncio.TimeoutError:
            raise BridgeUnavailable("bridge did not answer", code="offline") from None
        return self.hello

    _hello_received: asyncio.Event

    def __post_init__(self):  # pragma: no cover - dataclass-free helper
        pass

    def __new__(cls, *a, **kw):
        self = super().__new__(cls)
        self._hello_received = asyncio.Event()
        return self

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.closed.set()

    async def request(self, op: str, *, timeout: float = 120.0, **params: Any) -> Any:
        if not self.writer or self.writer.is_closing():
            raise BridgeUnavailable("not connected", code="offline")
        self._next += 1
        req_id = self._next
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        payload = {"id": req_id, "op": op}
        payload.update(params)
        for k, v in params.items():
            if isinstance(v, str) and len(v) > protocol.MAX_STRING_LENGTH:
                self._pending.pop(req_id, None)
                raise BridgeError(f"{k} is too long ({len(v):,} characters; limit {protocol.MAX_STRING_LENGTH:,})", code="bad_request")
        try:
            self.writer.write(protocol.encode(payload))
            await self.writer.drain()
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise BridgeError(f"{op} timed out", code="timeout") from None
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise BridgeUnavailable("bridge connection lost", code="offline") from exc
        finally:
            self._pending.pop(req_id, None)

    async def _read_loop(self) -> None:
        assert self.reader
        try:
            while True:
                try:
                    raw = await self.reader.readuntil(b"\n")
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                    break
                try:
                    msg = protocol.decode(raw)
                except protocol.ProtocolError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "event" in msg:
                    if msg["event"] == "hello" and isinstance(msg.get("data"), dict):
                        self.hello = msg["data"]
                        self._hello_received.set()
                    if self.on_event:
                        try:
                            res = self.on_event(str(msg["event"]), msg.get("data"))
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:  # pragma: no cover - handler bug must not kill the loop
                            pass
                    continue
                req_id = msg.get("id")
                fut = self._pending.get(req_id) if isinstance(req_id, int) else None
                if fut is None or fut.done():
                    continue
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(BridgeError(str(msg.get("error", "error")), str(msg.get("code", "error"))))
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BridgeUnavailable("bridge connection closed", code="offline"))
            self._pending.clear()
            self.closed.set()
            self._hello_received.set()
            if self.on_event and self.hello:
                # Tell the owner the stream ended so it can reconnect.
                with contextlib.suppress(Exception):
                    res = self.on_event("__closed__", None)
                    if asyncio.iscoroutine(res):
                        await res
