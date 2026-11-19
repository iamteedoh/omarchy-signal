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
* Notifications (no ``id``) pass through one ordered, bounded worker.  The
  connection closes on overflow instead of retaining unbounded remote input.
* Every request has a timeout; a stuck backend can not wedge the bridge.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_LINE_BYTES = 32 * 1024 * 1024  # getAttachment can return base64 bodies.
MAX_PENDING_NOTIFICATIONS = 64
MAX_PENDING_NOTIFICATION_BYTES = 64 * 1024 * 1024
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
        self._notification_task: asyncio.Task | None = None
        self._notification_queue: asyncio.Queue[tuple[str, Any, int]] = asyncio.Queue(
            maxsize=MAX_PENDING_NOTIFICATIONS)
        self._notification_bytes = 0
        self._write_lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> asyncio.Task:
        if self._on_notification is not None:
            self._notification_task = asyncio.create_task(
                self._notification_loop(), name="jsonrpc-notifications")
        self._reader_task = asyncio.create_task(self._read_loop(), name="jsonrpc-reader")
        return self._reader_task

    @property
    def closed(self) -> bool:
        return self._closed

    async def wait_closed(self) -> None:
        current = asyncio.current_task()
        for task in (self._reader_task, self._notification_task):
            if task is None or task is current:
                continue
            try:
                await task
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
        current = asyncio.current_task()
        # Either owned task can close the client.  Do not cancel that task out
        # from under itself; it will return after observing ``_closed``.
        for task in (self._reader_task, self._notification_task):
            if task is not None and not task.done() and task is not current:
                task.cancel()
        self._discard_notifications()

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
                if self._closed:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("jsonrpc: reader crashed")
        finally:
            self.close()

    def _handle_line(self, raw: bytes) -> None:
        raw_size = len(raw)
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
            self._enqueue_notification(method, msg.get("params"), raw_size)
        elif "error" in msg:
            # id-less error: signal-cli reports parse errors this way.
            log.warning("jsonrpc: backend error without id: %s", msg.get("error"))

    def _enqueue_notification(self, method: str, params: Any, raw_size: int) -> None:
        if (self._notification_queue.full()
                or self._notification_bytes + raw_size > MAX_PENDING_NOTIFICATION_BYTES):
            log.error("jsonrpc: notification backlog exceeded its limit; closing")
            self.close()
            return
        self._notification_bytes += raw_size
        self._notification_queue.put_nowait((method, params, raw_size))

    async def _notification_loop(self) -> None:
        while not self._closed:
            method, params, raw_size = await self._notification_queue.get()
            try:
                result = self._on_notification(method, params) if self._on_notification else None
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("jsonrpc: notification handler failed for %s", method)
            finally:
                self._notification_bytes -= raw_size
                self._notification_queue.task_done()

    def _discard_notifications(self) -> None:
        while True:
            try:
                _method, _params, raw_size = self._notification_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._notification_bytes -= raw_size
            self._notification_queue.task_done()
