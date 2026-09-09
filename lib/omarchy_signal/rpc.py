# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON-RPC 2.0 over a line-oriented byte stream (signal-cli ``jsonRpc`` mode).

The client is transport agnostic: it is fed an ``asyncio.StreamReader`` and a
writer-like object with ``write(bytes)``/``drain()``. That keeps it trivially
testable with in-memory pipes and lets the bridge speak to ``signal-cli`` over
stdio today or a Unix socket tomorrow without touching this file.

Hardening notes:

* Every inbound line is capped at :data:`MAX_LINE_BYTES`; a longer line is a
  protocol violation and closes the stream instead of growing memory forever.
* Responses are matched to requests by ``id`` only. ``signal-cli`` answers out
  of order (observed in 0.14.6), so positional matching would be a bug.
* Notifications (no ``id``) are dispatched to a handler and never awaited.
* Every request has a timeout; a stuck backend can not wedge the bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 32 * 1024 * 1024  # getAttachment can return base64 bodies.
DEFAULT_TIMEOUT = 60.0


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message
        self.data = data


class RpcClosed(Exception):
    """The stream ended before a response arrived."""


NotificationHandler = Callable[[str, Any], Awaitable[None] | None]


class JsonRpcClient:
    def __init__(self, reader: asyncio.StreamReader, writer: Any, *,
                 on_notification: NotificationHandler | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self._reader = reader
        self._writer = writer
        self._on_notification = on_notification
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future] = {}
        self._next_id = 0
        self._closed = False
        self._reader_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> asyncio.Task:
        self._reader_task = asyncio.create_task(self._read_loop(), name="jsonrpc-reader")
        return self._reader_task

    @property
    def closed(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        if self._reader_task:
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RpcClosed("connection closed"))
        self._pending.clear()
        # The reader closes the client from inside its own task when the
        # stream ends; cancelling ourselves there would surface as a spurious
        # CancelledError in whoever awaits the task.
        if self._reader_task and not self._reader_task.done() and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()

    # -- requests ----------------------------------------------------------

    async def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        if self._closed:
            raise RpcClosed("client is closed")
        self._next_id += 1
        req_id = str(self._next_id)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params:
            payload["params"] = params
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            async with self._write_lock:
                self._writer.write(line.encode("utf-8"))
                await self._writer.drain()
            return await asyncio.wait_for(fut, timeout or self._timeout)
        except asyncio.TimeoutError:
            raise RpcError(-32000, f"{method} timed out") from None
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            self._writer.write(line.encode("utf-8"))
            await self._writer.drain()

    # -- inbound -----------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    raw = await self._reader.readuntil(b"\n")
                except asyncio.LimitOverrunError:
                    log.error("jsonrpc: line exceeds %d bytes; closing", MAX_LINE_BYTES)
                    break
                except asyncio.IncompleteReadError as exc:
                    if exc.partial.strip():
                        self._handle_line(exc.partial)
                    break
                self._handle_line(raw)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("jsonrpc: reader crashed")
        finally:
            self.close()

    def _handle_line(self, raw: bytes) -> None:
        raw = raw.strip()
        if not raw:
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("jsonrpc: dropping non-JSON line (%d bytes)", len(raw))
            return
        if not isinstance(msg, dict):
            log.warning("jsonrpc: dropping non-object message")
            return
        if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
            fut = self._pending.get(str(msg["id"]))
            if fut is None or fut.done():
                log.debug("jsonrpc: response for unknown id %r", msg.get("id"))
                return
            if "error" in msg and msg["error"] is not None:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                fut.set_exception(RpcError(int(err.get("code", -32000) or -32000),
                                           str(err.get("message", "error")), err.get("data")))
            else:
                fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        if isinstance(method, str) and self._on_notification is not None:
            try:
                result = self._on_notification(method, msg.get("params"))
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                log.exception("jsonrpc: notification handler failed for %s", method)
        elif "error" in msg:
            # id-less error: signal-cli reports parse errors this way.
            log.warning("jsonrpc: backend error without id: %s", msg.get("error"))
