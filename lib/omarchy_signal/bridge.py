"""The bridge daemon: supervises ``signal-cli``, records history, serves clients.

Run with ``omarchy-signal bridge`` (normally from the ``omarchy-signal``
systemd user unit). One instance per user; a second instance refuses to start
if the socket is live.

Trust model
-----------
* ``signal-cli`` is the only process holding Signal keys; the bridge talks to
  it over a private stdio pipe, never over TCP/HTTP.
* The bridge's own socket lives in ``$XDG_RUNTIME_DIR/omarchy-signal`` (0700)
  with mode 0600 and every connection is checked with ``SO_PEERCRED``: a peer
  whose uid differs from ours is dropped before a byte is parsed.
* Text from the network is sanitised in :mod:`envelope` before it is stored or
  forwarded; text from clients is validated in :mod:`protocol` and
  :mod:`sanitize` before it is handed to ``signal-cli``.
* Logs never contain message bodies, names, numbers or attachment names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, emoji, protocol
from .config import Config, Paths
from .envelope import Attachment, Event, parse_receive
from .rpc import JsonRpcClient, RpcClosed, RpcError
from .sanitize import (E164, InvalidAttachment, InvalidRecipient, Recipient, classify_recipient,
                       clean_name, clean_text, parse_conversation_key, safe_attachment_path)
from .store import Store

log = logging.getLogger("omarchy_signal.bridge")

TYPING_TTL_MS = 6000
CONTACT_REFRESH_S = 600
RESTART_BACKOFF = (1, 2, 5, 10, 30, 60)


class SignalCliSupervisor:
    """Keeps one ``signal-cli jsonRpc`` process alive and exposes its RPC."""

    def __init__(self, cfg: Config, paths: Paths, on_notification):
        self.cfg = cfg
        self.paths = paths
        self.on_notification = on_notification
        self.proc: asyncio.subprocess.Process | None = None
        self.rpc: JsonRpcClient | None = None
        self._stop = False
        self._restarts = 0
        self.ready = asyncio.Event()
        self.last_error = ""
        self._task: asyncio.Task | None = None

    def command(self) -> list[str]:
        exe = self.cfg.signal_cli
        if "/" not in exe:
            found = shutil.which(exe)
            if not found:
                raise FileNotFoundError(f"{exe} not found on PATH")
            exe = found
        cmd = [exe, "--data-dir", str(self.paths.signal_cli_data),
               "--trust-new-identities", self.cfg.trust_new_identities]
        if self.cfg.log_level == "debug":
            cmd.append("--verbose")
        cmd += ["jsonRpc", "--receive-mode", "on-start", "--ignore-stories"]
        if not self.cfg.download_attachments:
            cmd.append("--ignore-attachments")
        return cmd

    async def run(self) -> None:
        self._task = asyncio.current_task()
        while not self._stop:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except FileNotFoundError as exc:
                self.last_error = str(exc)
                log.error("%s", exc)
            except Exception as exc:  # pragma: no cover - defensive
                self.last_error = str(exc)
                log.exception("signal-cli supervisor error")
            if self._stop:
                break
            delay = RESTART_BACKOFF[min(self._restarts, len(RESTART_BACKOFF) - 1)]
            self._restarts += 1
            log.warning("signal-cli exited; restarting in %ss", delay)
            await asyncio.sleep(delay)

    async def _run_once(self) -> None:
        cmd = self.command()
        log.info("starting %s", os.path.basename(cmd[0]))
        self.paths.signal_cli_data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.paths.signal_cli_data, 0o700)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=protocol.MAX_EVENT_BYTES * 4,
            start_new_session=True)
        assert self.proc.stdout and self.proc.stdin and self.proc.stderr
        self.rpc = JsonRpcClient(self.proc.stdout, self.proc.stdin, on_notification=self.on_notification)
        reader = self.rpc.start()
        stderr_task = asyncio.create_task(self._drain_stderr(self.proc.stderr))
        started = time.monotonic()
        try:
            version = await self.rpc.call("version", timeout=30)
            log.info("signal-cli %s ready", (version or {}).get("version", "?") if isinstance(version, dict) else "?")
            self.ready.set()
            self.last_error = ""
            await reader
        finally:
            self.ready.clear()
            self.rpc.close()
            stderr_task.cancel()
            if self.proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.proc.kill()
            if time.monotonic() - started > 120:
                self._restarts = 0

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip()
                if text and not text.startswith("Fatal error: java.lang.InterruptedException"):
                    log.debug("signal-cli: %s", text[:300])
        except asyncio.CancelledError:
            pass

    async def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        if not self.rpc or self.rpc.closed:
            raise RpcClosed("signal-cli is not running")
        started = time.monotonic()
        try:
            return await self.rpc.call(method, params, timeout=timeout)
        finally:
            took = time.monotonic() - started
            if took > 5:
                log.warning("signal-cli %s took %.1fs", method, took)
            else:
                log.debug("signal-cli %s took %.2fs", method, took)

    async def restart(self) -> None:
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()

    async def stop(self) -> None:
        self._stop = True
        await self.restart()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


class Bridge:
    def __init__(self, cfg: Config, paths: Paths):
        self.cfg = cfg
        self.paths = paths
        self.store = Store(paths.database if cfg.history_enabled else ":memory:")
        self.supervisor = SignalCliSupervisor(cfg, paths, self._on_notification)
        self.account = cfg.account
        self.accounts: list[str] = []
        self.subscribers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None
        self._shutdown = asyncio.Event()
        self._link_uri = ""
        self._link_task: asyncio.Task | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._contacts_refreshed = 0.0
        self.started_at = time.time()

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        self.paths.ensure()
        sock_path = self.paths.socket
        if sock_path.exists():
            if await self._socket_alive(sock_path):
                raise SystemExit(f"another bridge is already listening on {sock_path}")
            sock_path.unlink()
        self.server = await asyncio.start_unix_server(self._handle_client, path=str(sock_path))
        os.chmod(sock_path, 0o600)
        log.info("omarchy-signal bridge %s listening", __version__)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)

        sup_task = asyncio.create_task(self.supervisor.run(), name="signal-cli")
        boot_task = asyncio.create_task(self._bootstrap_loop(), name="bootstrap")
        prune_task = asyncio.create_task(self._prune_loop(), name="prune")
        try:
            await self._shutdown.wait()
        finally:
            log.info("shutting down")
            for t in (boot_task, prune_task):
                t.cancel()
            await self.supervisor.stop()
            sup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sup_task
            for w in list(self.subscribers):
                w.close()
            self.server.close()
            await self.server.wait_closed()
            with contextlib.suppress(OSError):
                sock_path.unlink()
            self.store.close()

    @staticmethod
    async def _socket_alive(path: Path) -> bool:
        try:
            _, w = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 2)
        except (OSError, asyncio.TimeoutError):
            return False
        w.close()
        return True

    async def _bootstrap_loop(self) -> None:
        """After every signal-cli (re)start: discover accounts, refresh contacts."""
        while True:
            await self.supervisor.ready.wait()
            try:
                await self._discover_accounts()
                if self.account:
                    await self.refresh_directory()
                    await self._broadcast("status", self.status())
            except (RpcError, RpcClosed) as exc:
                log.warning("bootstrap: %s", exc)
            # Wait for a restart (ready cleared) or the periodic refresh.
            while self.supervisor.ready.is_set():
                await asyncio.sleep(5)
                if self.account and time.monotonic() - self._contacts_refreshed > CONTACT_REFRESH_S:
                    with contextlib.suppress(RpcError, RpcClosed):
                        await self.refresh_directory()
            await self._broadcast("status", self.status())

    async def _prune_loop(self) -> None:
        while True:
            if self.cfg.history_retain_days > 0:
                n = self.store.prune(self.cfg.history_retain_days)
                if n:
                    log.info("pruned %d old messages", n)
            await asyncio.sleep(3600)

    async def _discover_accounts(self) -> None:
        result = await self.supervisor.call("listAccounts", timeout=30)
        numbers: list[str] = []
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict) and isinstance(item.get("number"), str):
                    numbers.append(item["number"])
        self.accounts = numbers
        if self.cfg.account and self.cfg.account in numbers:
            self.account = self.cfg.account
        elif numbers and not self.cfg.account:
            self.account = numbers[0]
        elif self.cfg.account:
            log.warning("configured account is not registered locally")
            self.account = ""
        else:
            self.account = ""
        log.info("%d local account(s); %s", len(numbers), "active" if self.account else "not linked")

    def status(self) -> dict:
        return {
            "version": __version__, "protocol": protocol.PROTOCOL_VERSION,
            "connected": self.supervisor.ready.is_set(), "linked": bool(self.account),
            "accountCount": len(self.accounts), "unread": self.store.total_unread(),
            "historyEnabled": self.cfg.history_enabled, "notifications": self.cfg.notifications,
            "notificationPreview": self.cfg.notification_preview,
            "notificationContent": self.cfg.notification_content,
            "respectDnd": self.cfg.respect_dnd,
            "notificationSound": self.cfg.notification_sound if self.cfg.notification_sound and os.path.isfile(os.path.expanduser(self.cfg.notification_sound)) else "",
            "account": self.account,
            "notificationTimeoutMs": self.cfg.notification_timeout_ms,
            "linking": self._link_task is not None and not self._link_task.done(),
            "error": self.supervisor.last_error, "uptime": int(time.time() - self.started_at),
        }

    # -- signal-cli notifications ----------------------------------------------

    async def _on_notification(self, method: str, params: Any) -> None:
        if method != "receive":
            return
        if isinstance(params, dict) and isinstance(params.get("account"), str):
            if self.account and params["account"] != self.account:
                return
        for ev in parse_receive(params, self.account):
            try:
                await self._handle_event(ev)
            except Exception:  # pragma: no cover - defensive
                log.exception("failed to handle %s event", ev.kind)

    async def _handle_event(self, ev: Event) -> None:
        if ev.kind == "message":
            conv = ev.conversation
            muted = self.store.conversation(conv.key)
            is_new = self.store.add_event_message(ev, count_unread=True)
            if not is_new:
                return
            if ev.attachments and self.cfg.download_attachments:
                self._locate_attachments(conv.key, ev.timestamp, ev.attachments)
            if not ev.outgoing:
                self.store.set_typing(conv.key, 0)
            payload = self._message_payload(ev, muted=bool(muted and muted.muted))
            await self._broadcast("message", payload)
            await self._broadcast("unread", {"total": self.store.total_unread()})
        elif ev.kind == "receipt" and ev.sender is not None:
            n = self.store.apply_receipt(ev.sender, ev.receipt_type, ev.receipt_timestamps)
            if n:
                await self._broadcast("receipt", {"sender": ev.sender.key, "type": ev.receipt_type,
                                                  "timestamps": ev.receipt_timestamps[:64]})
        elif ev.kind == "typing" and ev.sender is not None and self.cfg.typing_indicators:
            until = int(time.time() * 1000) + TYPING_TTL_MS if ev.typing == "started" else 0
            self.store.upsert_conversation(ev.conversation, name=ev.group_name)
            self.store.set_typing(ev.conversation.key, until)
            await self._broadcast("typing", {"conversation": ev.conversation.key, "sender": ev.sender.key,
                                             "senderName": ev.sender_name or self.store.display_name(ev.sender.key),
                                             "typing": ev.typing == "started"})
        elif ev.kind == "reaction":
            if self.store.apply_reaction(ev):
                await self._broadcast("reaction", {"conversation": ev.conversation.key, "ts": ev.reaction_target_ts,
                                                   "sender": ev.sender.key if ev.sender else "", "emoji": ev.emoji,
                                                   "removed": ev.reaction_removed})
        elif ev.kind == "remote_delete":
            if self.store.apply_remote_delete(ev):
                await self._broadcast("deleted", {"conversation": ev.conversation.key, "ts": ev.delete_target_ts})
        elif ev.kind == "read_sync":
            if self.store.mark_read(ev.conversation.key):
                await self._broadcast("unread", {"total": self.store.total_unread(),
                                                 "conversation": ev.conversation.key})

    def _message_payload(self, ev: Event, *, muted: bool) -> dict:
        conv_name = self.store.display_name(ev.conversation.key) if not ev.group_name else ev.group_name
        sender_name = ev.sender_name or (self.store.display_name(ev.sender.key) if ev.sender else "")
        content = self.cfg.notification_content
        preview = ev.text if content == "name-and-message" else "New message"
        if not ev.text and ev.attachments and content == "name-and-message":
            kind = ev.attachments[0].content_type.split("/")[0]
            preview = {"image": "📷 Photo", "video": "🎞 Video", "audio": "🎤 Voice message"}.get(kind, "📎 Attachment")
        # Like Signal's "No name or content": the popup says only that
        # something arrived. Clients that show the thread still get real names
        # through history; only the notification fields are masked.
        popup_sender = sender_name if content != "none" else "Signal"
        popup_conv = conv_name if content != "none" else "New message"
        return {
            "conversation": ev.conversation.key, "conversationName": popup_conv, "isGroup": ev.conversation.kind == "group" and content != "none",
            "sender": ev.sender.key if ev.sender else "", "senderName": popup_sender, "ts": ev.timestamp,
            "text": ev.text, "preview": clean_text(preview, max_length=300, single_line=True),
            "attachments": [a.to_json() for a in ev.attachments], "outgoing": ev.outgoing,
            "muted": muted, "quoteText": ev.quote_text, "expiresIn": ev.expires_in,
            "viewOnce": ev.view_once, "notify": (not ev.outgoing) and (not muted) and self.cfg.notifications != "off",
        }

    def _locate_attachments(self, conv_key: str, ts: int, attachments: list[Attachment]) -> None:
        """signal-cli writes attachments to <data-dir>/attachments/<id>. Record
        the path when the file exists (it usually does by the time the receive
        notification is emitted)."""
        base = self.paths.signal_cli_data / "attachments"
        with contextlib.suppress(OSError):
            os.chmod(base, 0o700)
        for att in attachments:
            if not att.id:
                continue
            candidate = base / att.id
            try:
                real = candidate.resolve(strict=True)
            except OSError:
                continue
            if base.resolve() not in real.parents or not real.is_file():
                continue
            with contextlib.suppress(OSError):
                os.chmod(real, 0o600)
            att.path = str(real)
            self.store.set_attachment_path(conv_key, ts, att.id, str(real))

    # -- client connections ---------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self._peer_is_us(writer):
            log.warning("rejected client with foreign uid")
            writer.close()
            return
        subscribed = False
        write_lock = asyncio.Lock()
        inflight: set[asyncio.Task] = set()

        async def answer(req_id, op, params):
            # Requests run concurrently: a slow receipt or typing call must
            # never hold up the send behind it on the same connection.
            response = await self._dispatch(req_id, op, params)
            async with write_lock:
                writer.write(protocol.encode(response))
                with contextlib.suppress(ConnectionResetError, BrokenPipeError):
                    await writer.drain()

        try:
            writer.write(protocol.encode(protocol.event("hello", self.status())))
            await writer.drain()
            while True:
                try:
                    raw = await reader.readuntil(b"\n")
                except (asyncio.LimitOverrunError, ValueError):
                    writer.write(protocol.encode(protocol.error(None, "request too large", code="too_large")))
                    break
                except asyncio.IncompleteReadError:
                    break
                if len(raw) > protocol.MAX_REQUEST_BYTES:
                    break
                if not raw.strip():
                    continue
                try:
                    req_id, op, params = protocol.validate_request(protocol.decode(raw))
                except protocol.ProtocolError as exc:
                    writer.write(protocol.encode(protocol.error(None, str(exc), code="bad_request")))
                    await writer.drain()
                    continue
                if op == "subscribe":
                    self.subscribers.add(writer)
                    subscribed = True
                    response = protocol.reply(req_id, {"subscribed": True})
                elif op == "unsubscribe":
                    self.subscribers.discard(writer)
                    subscribed = False
                    response = protocol.reply(req_id, {"subscribed": False})
                else:
                    if len(inflight) >= 64:
                        writer.write(protocol.encode(protocol.error(req_id, "too many requests in flight", code="busy")))
                        await writer.drain()
                        continue
                    task = asyncio.create_task(answer(req_id, op, params))
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)
                    continue
                async with write_lock:
                    writer.write(protocol.encode(response))
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("client handler crashed")
        finally:
            for task in inflight:
                task.cancel()
            if subscribed:
                self.subscribers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    def _peer_is_us(writer: asyncio.StreamWriter) -> bool:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return False
        try:
            creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", creds)
        except OSError:
            return False
        return uid == os.getuid()

    async def _broadcast(self, name: str, data: Any) -> None:
        if not self.subscribers:
            return
        payload = protocol.encode(protocol.event(name, data))
        if len(payload) > protocol.MAX_EVENT_BYTES:
            return
        dead = []
        for w in list(self.subscribers):
            try:
                w.write(payload)
                await asyncio.wait_for(w.drain(), 5)
            except (ConnectionResetError, BrokenPipeError, asyncio.TimeoutError, RuntimeError):
                dead.append(w)
        for w in dead:
            self.subscribers.discard(w)
            w.close()

    # -- request dispatch -------------------------------------------------------

    async def _dispatch(self, req_id: Any, op: str, p: dict) -> dict:
        try:
            handler = getattr(self, f"op_{op}")
            result = await handler(p)
            return protocol.reply(req_id, result)
        except (InvalidRecipient, InvalidAttachment, protocol.ProtocolError, ValueError) as exc:
            return protocol.error(req_id, str(exc), code="invalid")
        except RpcError as exc:
            log.warning("%s failed: %s", op, exc.message)
            return protocol.error(req_id, exc.message, code="signal")
        except RpcClosed:
            return protocol.error(req_id, "signal-cli is not running", code="offline")
        except asyncio.TimeoutError:
            return protocol.error(req_id, "timed out", code="timeout")

    def _require_account(self) -> str:
        if not self.account:
            raise protocol.ProtocolError("no Signal account is linked yet; run `omarchy-signal link`")
        return self.account

    def _target_params(self, rec: Recipient) -> dict:
        if rec.kind == "group":
            return {"groupId": rec.value}
        if rec.kind == "username":
            return {"username": [rec.value]}
        return {"recipient": [rec.value]}

    async def op_ping(self, p: dict) -> str:
        return "pong"

    async def op_status(self, p: dict) -> dict:
        return self.status()

    async def op_conversations(self, p: dict) -> list[dict]:
        return [c.to_json() for c in self.store.conversations(include_archived=p.get("includeArchived", False))]

    async def op_history(self, p: dict) -> list[dict]:
        rec = parse_conversation_key(p["conversation"])
        msgs = self.store.history(rec.key, before_ts=max(0, p.get("before", 0)), limit=p.get("limit", 50))
        return [m.to_json() for m in msgs]

    async def op_message(self, p: dict) -> dict | None:
        rec = parse_conversation_key(p["conversation"])
        m = self.store.message(rec.key, p["ts"])
        return m.to_json() if m else None

    async def op_search(self, p: dict) -> list[dict]:
        return [m.to_json() for m in self.store.search(p["query"], limit=p.get("limit", 50))]

    async def op_contacts(self, p: dict) -> list[dict]:
        return self.store.contacts()

    async def op_groups(self, p: dict) -> list[dict]:
        return self.store.groups()

    async def op_refresh(self, p: dict) -> dict:
        self._require_account()
        return await self.refresh_directory()

    async def op_resolve(self, p: dict) -> dict:
        """Find a conversation by name fragment, number, username or key."""
        query = clean_name(p["query"], max_length=200)
        if not query:
            raise ValueError("empty query")
        try:
            rec = parse_conversation_key(query)
            return {"key": rec.key, "name": self.store.display_name(rec.key), "kind": rec.kind}
        except InvalidRecipient:
            pass
        try:
            rec = classify_recipient(query)
            return {"key": rec.key, "name": self.store.display_name(rec.key), "kind": rec.kind}
        except InvalidRecipient:
            pass
        q = query.lower()
        matches = []
        for c in self.store.contacts():
            hay = " ".join(str(c.get(k, "")) for k in ("name", "profileName", "username", "number")).lower()
            if q in hay:
                matches.append({"key": c["key"], "name": c["displayName"], "kind": c["key"].split(":")[0]})
        for g in self.store.groups():
            if q in g["name"].lower():
                matches.append({"key": g["key"], "name": g["name"], "kind": "group"})
        for conv in self.store.conversations(include_archived=True):
            if q in conv.name.lower() and not any(m["key"] == conv.key for m in matches):
                matches.append({"key": conv.key, "name": conv.name, "kind": conv.kind})
        if not matches:
            raise ValueError("no contact, group or conversation matches")
        exact = [m for m in matches if m["name"].lower() == q]
        return exact[0] if len(exact) == 1 else (matches[0] if len(matches) == 1 else {"candidates": matches[:20]})

    async def op_send(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        text = emoji.replace_shortcodes(clean_text(p.get("text", "")))
        paths = [str(safe_attachment_path(a)) for a in p.get("attachments", [])]
        if not text.strip() and not paths:
            raise ValueError("nothing to send")
        params: dict[str, Any] = {"account": account, "message": text}
        if rec.kind == "number" and rec.value == account:
            params["noteToSelf"] = True          # Signal's "Note to Self" conversation
        else:
            params.update(self._target_params(rec))
        if paths:
            params["attachments"] = paths
        quote_ts = p.get("quoteTs", 0)
        if quote_ts > 0:
            quote_author = p.get("quoteAuthor", "")
            try:
                qa = parse_conversation_key(quote_author)
            except InvalidRecipient:
                qa = None
            if qa is not None and qa.kind in ("number", "uuid"):
                params["quoteTimestamp"] = quote_ts
                params["quoteAuthor"] = qa.value
                params["quoteMessage"] = clean_text(p.get("quoteText", ""), max_length=2000, single_line=True)
        result = await self.supervisor.call("send", params, timeout=120)
        ts = int(result.get("timestamp", 0)) if isinstance(result, dict) else 0
        if ts <= 0:
            ts = int(time.time() * 1000)
        failures = []
        if isinstance(result, dict) and isinstance(result.get("results"), list):
            for r in result["results"]:
                if isinstance(r, dict) and r.get("type") not in (None, "SUCCESS"):
                    failures.append(str(r.get("type")))
        atts = []
        for path in paths:
            pth = Path(path)
            atts.append(Attachment(id="", content_type=_guess_mime(pth), filename=pth.name,
                                   size=pth.stat().st_size, path=str(pth)))
        status = "failed" if failures and len(failures) >= max(1, len(result.get("results", [1]))) else "sent"
        self.store.add_outgoing(rec, ts, f"number:{account}", text, atts, status=status,
                                quote_text=params.get("quoteMessage", ""), quote_author=p.get("quoteAuthor", ""),
                                quote_ts=quote_ts if "quoteTimestamp" in params else 0)
        self.store.mark_read(rec.key)
        self._cancel_typing(rec.key)
        await self._broadcast("sent", {"conversation": rec.key, "ts": ts, "status": status,
                                       "text": text, "attachments": [a.to_json() for a in atts]})
        await self._broadcast("unread", {"total": self.store.total_unread(), "conversation": rec.key})
        return {"ts": ts, "status": status, "failures": failures}

    async def op_markRead(self, p: dict) -> dict:
        rec = parse_conversation_key(p["conversation"])
        unread_msgs = []
        if self.cfg.send_read_receipts and self.account:
            for m in self.store.history(rec.key, limit=50):
                if not m.outgoing and m.status != "read":
                    unread_msgs.append(m)
        changed = self.store.mark_read(rec.key)
        for m in unread_msgs:
            self.store.db.execute("UPDATE messages SET status = 'read' WHERE conversation = ? AND ts = ? AND sender = ?",
                                  (m.conversation, m.ts, m.sender))
        # Read receipts go to the author of each message, even in groups.
        by_sender: dict[str, list[int]] = {}
        for m in unread_msgs:
            by_sender.setdefault(m.sender, []).append(m.ts)
        for sender_key, stamps in by_sender.items():
            try:
                sender = parse_conversation_key(sender_key)
            except InvalidRecipient:
                continue
            if sender.kind not in ("number", "uuid"):
                continue
            # sendReceipt takes ONE recipient (signal-cli reads it with getString);
            # a list is rejected. The read receipt doubles as the read-sync that
            # clears the notification on the phone and other linked devices.
            try:
                await self.supervisor.call("sendReceipt", {"account": self.account, "recipient": sender.value,
                                                           "targetTimestamp": stamps[-32:], "type": "read"}, timeout=30)
            except (RpcError, RpcClosed, asyncio.TimeoutError) as exc:
                log.warning("read receipt failed: %s", exc)
        await self._broadcast("unread", {"total": self.store.total_unread(), "conversation": rec.key})
        return {"changed": changed, "receipts": len(unread_msgs)}

    async def op_typing(self, p: dict) -> dict:
        account = self._require_account()
        if not self.cfg.typing_indicators:
            return {"sent": False}
        rec = parse_conversation_key(p["conversation"])
        params: dict[str, Any] = {"account": account}
        params.update(self._target_params(rec))
        if p.get("stop"):
            params["stop"] = True
            self._cancel_typing(rec.key)
        else:
            self._schedule_typing_stop(rec.key)
        with contextlib.suppress(RpcError):
            await self.supervisor.call("sendTyping", params, timeout=20)
        return {"sent": True}

    def _schedule_typing_stop(self, key: str) -> None:
        self._cancel_typing(key)

        async def stop_later():
            await asyncio.sleep(10)
            rec = parse_conversation_key(key)
            params: dict[str, Any] = {"account": self.account, "stop": True}
            params.update(self._target_params(rec))
            with contextlib.suppress(RpcError, RpcClosed, asyncio.TimeoutError):
                await self.supervisor.call("sendTyping", params, timeout=20)
        self._typing_tasks[key] = asyncio.create_task(stop_later())

    def _cancel_typing(self, key: str) -> None:
        task = self._typing_tasks.pop(key, None)
        if task:
            task.cancel()

    async def op_react(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        author = parse_conversation_key(p["author"])
        if author.kind not in ("number", "uuid"):
            raise ValueError("reaction author must be a person")
        emoji = clean_name(p["emoji"], max_length=16)
        if not emoji or len(emoji) > 8 or any(ch.isascii() and ch.isalnum() for ch in emoji):
            raise ValueError("emoji must be a single emoji")
        params: dict[str, Any] = {"account": account, "emoji": emoji, "targetAuthor": author.value,
                                  "targetTimestamp": p["ts"], "remove": bool(p.get("remove", False))}
        params.update(self._target_params(rec))
        await self.supervisor.call("sendReaction", params, timeout=60)
        ev = Event(kind="reaction", conversation=rec, timestamp=int(time.time() * 1000),
                   sender=Recipient("number", account), emoji=emoji, reaction_target_ts=p["ts"],
                   reaction_target_author=author.key, reaction_removed=bool(p.get("remove", False)))
        self.store.apply_reaction(ev)
        await self._broadcast("reaction", {"conversation": rec.key, "ts": p["ts"], "sender": f"number:{account}",
                                           "emoji": emoji, "removed": ev.reaction_removed})
        return {"ok": True}

    async def op_mute(self, p: dict) -> dict:
        rec = parse_conversation_key(p["conversation"])
        self.store.upsert_conversation(rec)
        self.store.set_muted(rec.key, p["muted"])
        await self._broadcast("unread", {"total": self.store.total_unread()})
        return {"muted": p["muted"]}

    async def op_archive(self, p: dict) -> dict:
        rec = parse_conversation_key(p["conversation"])
        self.store.set_archived(rec.key, p["archived"])
        return {"archived": p["archived"]}

    async def op_attachment(self, p: dict) -> dict:
        """Return the local path of a received attachment, locating it on disk
        if the receive-time lookup missed it."""
        rec = parse_conversation_key(p["conversation"])
        msg = self.store.message(rec.key, p["ts"])
        if not msg:
            raise ValueError("no such message")
        for att in msg.attachments:
            if att.id == p["id"]:
                if not att.path:
                    self._locate_attachments(rec.key, msg.ts, [att])
                if not att.path:
                    raise ValueError("attachment has not been downloaded")
                return att.to_json()
        raise ValueError("no such attachment")

    async def op_link(self, p: dict) -> dict:
        """Start linking this machine as a new device. Returns the URI to show
        as a QR code; :meth:`op_linkFinish` waits for the phone to scan it."""
        if self._link_task and not self._link_task.done():
            return {"uri": self._link_uri, "pending": True}
        name = clean_name(p.get("deviceName") or self.cfg.device_name, max_length=50) or "omarchy-signal"
        result = await self.supervisor.call("startLink", {}, timeout=60)
        uri = result.get("deviceLinkUri") if isinstance(result, dict) else None
        if not isinstance(uri, str) or not uri.startswith("sgnl://"):
            raise ValueError("signal-cli did not return a link URI")
        self._link_uri = uri
        self._link_task = asyncio.create_task(self._finish_link(uri, name))
        return {"uri": uri, "pending": True}

    async def _finish_link(self, uri: str, name: str) -> dict:
        try:
            result = await self.supervisor.call("finishLink", {"deviceLinkUri": uri, "deviceName": name}, timeout=600)
        finally:
            self._link_uri = ""
        number = result.get("number") if isinstance(result, dict) else None
        log.info("device linked")
        if isinstance(number, str) and E164.match(number):
            if not self.cfg.account:
                self.cfg.account = number
            self.account = number
            if number not in self.accounts:
                self.accounts.append(number)
        # A fresh account only starts receiving after a restart of the RPC process.
        await self.supervisor.restart()
        await self._broadcast("status", self.status())
        return {"linked": True, "number": number if isinstance(number, str) else ""}

    async def op_linkFinish(self, p: dict) -> dict:
        if not self._link_task:
            raise ValueError("no link in progress")
        try:
            return await asyncio.wait_for(asyncio.shield(self._link_task), 600)
        finally:
            if self._link_task.done():
                self._link_task = None

    async def op_demo(self, p: dict) -> dict:
        """Broadcast a synthetic incoming message so the popup and bar widget
        can be previewed (theme work, first-run check) without an account.
        Nothing is stored and nothing is sent."""
        text = clean_text(p.get("text") or "This is what an incoming message looks like. Click to reply.",
                          max_length=300, single_line=True)
        payload = {
            "conversation": "number:+15550000000", "conversationName": "Demo contact", "isGroup": False,
            "sender": "number:+15550000000", "senderName": "Demo contact", "ts": int(time.time() * 1000),
            "text": text, "preview": text, "attachments": [], "outgoing": False, "muted": False,
            "quoteText": "", "expiresIn": 0, "viewOnce": False, "notify": True, "demo": True,
        }
        await self._broadcast("message", payload)
        return {"sent": True}

    async def op_delete(self, p: dict) -> dict:
        """Delete one of our own messages for everyone (Signal's remote delete)."""
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        msg = self.store.message(rec.key, p["ts"])
        if not msg or not msg.outgoing:
            raise ValueError("you can only delete your own messages")
        params: dict[str, Any] = {"account": account, "targetTimestamp": p["ts"]}
        params.update(self._target_params(rec))
        await self.supervisor.call("remoteDelete", params, timeout=60)
        self.store.delete_own(rec.key, p["ts"])
        await self._broadcast("deleted", {"conversation": rec.key, "ts": p["ts"]})
        return {"deleted": True}

    async def op_edit(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        msg = self.store.message(rec.key, p["ts"])
        if not msg or not msg.outgoing:
            raise ValueError("you can only edit your own messages")
        text = emoji.replace_shortcodes(clean_text(p["text"]))
        if not text.strip():
            raise ValueError("nothing to send")
        params: dict[str, Any] = {"account": account, "message": text, "editTimestamp": p["ts"]}
        if rec.kind == "number" and rec.value == account:
            params["noteToSelf"] = True
        else:
            params.update(self._target_params(rec))
        await self.supervisor.call("send", params, timeout=120)
        self.store.edit_message(rec.key, p["ts"], text)
        await self._broadcast("edited", {"conversation": rec.key, "ts": p["ts"], "text": text})
        return {"edited": True}

    async def op_setExpiration(self, p: dict) -> dict:
        """Disappearing messages timer for a conversation (seconds, 0 = off)."""
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        seconds = max(0, min(int(p["seconds"]), 4 * 7 * 86400))
        if rec.kind == "group":
            await self.supervisor.call("updateGroup", {"account": account, "groupId": rec.value, "expiration": seconds}, timeout=60)
        elif rec.kind in ("number", "uuid"):
            await self.supervisor.call("updateContact", {"account": account, "recipient": rec.value, "expiration": seconds}, timeout=60)
        else:
            raise ValueError("timers are per contact or group")
        self.store.upsert_conversation(rec)
        self.store.set_expiration(rec.key, seconds)
        await self._broadcast("conversation", {"conversation": rec.key, "expiration": seconds})
        return {"expiration": seconds}

    async def op_block(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        method = "block" if p["blocked"] else "unblock"
        params: dict[str, Any] = {"account": account}
        if rec.kind == "group":
            params["groupId"] = [rec.value]
        elif rec.kind in ("number", "uuid"):
            params["recipient"] = [rec.value]
        else:
            raise ValueError("blocking is per contact or group")
        await self.supervisor.call(method, params, timeout=60)
        self.store.upsert_conversation(rec)
        self.store.set_blocked(rec.key, bool(p["blocked"]))
        await self._broadcast("conversation", {"conversation": rec.key, "blocked": bool(p["blocked"])})
        return {"blocked": bool(p["blocked"])}

    async def op_messageRequest(self, p: dict) -> dict:
        """Accept or delete a message request from someone not in your contacts."""
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        params: dict[str, Any] = {"account": account, "type": "accept" if p["accept"] else "delete"}
        params.update(self._target_params(rec))
        await self.supervisor.call("sendMessageRequestResponse", params, timeout=60)
        if not p["accept"]:
            self.store.set_archived(rec.key, True)
        return {"accepted": bool(p["accept"])}

    async def op_identities(self, p: dict) -> list[dict]:
        """Safety numbers for a contact, newest first, as Signal shows them."""
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        if rec.kind not in ("number", "uuid"):
            raise ValueError("safety numbers are per contact")
        result = await self.supervisor.call("listIdentities", {"account": account, "number": rec.value}, timeout=60)
        out = []
        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                sn = clean_name(item.get("safetyNumber"), max_length=120)
                out.append({"safetyNumber": sn, "trustLevel": clean_name(item.get("trustLevel"), max_length=40),
                            "addedDate": int(item.get("addedDate") or 0) if str(item.get("addedDate", "")).lstrip("-").isdigit() else 0,
                            "fingerprint": clean_name(item.get("fingerprint"), max_length=200)})
        out.sort(key=lambda x: -x["addedDate"])
        return out

    async def op_trust(self, p: dict) -> dict:
        """Mark a contact's current safety number verified (or trust all known keys)."""
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        if rec.kind not in ("number", "uuid"):
            raise ValueError("safety numbers are per contact")
        params: dict[str, Any] = {"account": account, "recipient": rec.value}
        sn = clean_name(p.get("safetyNumber") or "", max_length=120).replace(" ", "")
        if sn:
            if not sn.isdigit():
                raise ValueError("a safety number is digits only")
            params["verifiedSafetyNumber"] = sn
        else:
            params["trustAllKnownKeys"] = True
        await self.supervisor.call("trust", params, timeout=60)
        return {"trusted": True}

    async def op_groupInfo(self, p: dict) -> dict:
        rec = parse_conversation_key(p["conversation"])
        if rec.kind != "group":
            raise ValueError("not a group")
        for g in self.store.groups():
            if g["key"] == rec.key:
                members = [{"key": m, "name": self.store.display_name(m)} for m in g.get("members", [])]
                conv = self.store.conversation(rec.key)
                return {"key": rec.key, "name": g["name"], "members": members,
                        "expiration": conv.expiration if conv else 0}
        raise ValueError("unknown group (try `refresh`)")

    async def op_leaveGroup(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        if rec.kind != "group":
            raise ValueError("not a group")
        await self.supervisor.call("quitGroup", {"account": account, "groupId": rec.value}, timeout=60)
        self.store.set_archived(rec.key, True)
        with contextlib.suppress(RpcError, RpcClosed):
            await self.refresh_directory()
        return {"left": True}

    async def op_createGroup(self, p: dict) -> dict:
        account = self._require_account()
        name = clean_name(p["name"], max_length=64)
        if not name:
            raise ValueError("a group needs a name")
        members = []
        for m in p["members"]:
            rec = parse_conversation_key(m) if ":" in m else classify_recipient(m)
            if rec.kind not in ("number", "uuid", "username"):
                raise ValueError("group members must be contacts")
            members.append(rec.value)
        if not members:
            raise ValueError("pick at least one member")
        result = await self.supervisor.call("updateGroup", {"account": account, "name": name, "members": members}, timeout=120)
        gid = result.get("groupId") if isinstance(result, dict) else None
        with contextlib.suppress(RpcError, RpcClosed):
            await self.refresh_directory()
        if isinstance(gid, str):
            rec = Recipient("group", gid)
            self.store.upsert_conversation(rec, name=name)
            await self._broadcast("directory", {"contacts": 0, "groups": 1})
            return {"key": rec.key, "name": name}
        return {"key": "", "name": name}

    async def op_renameGroup(self, p: dict) -> dict:
        account = self._require_account()
        rec = parse_conversation_key(p["conversation"])
        if rec.kind != "group":
            raise ValueError("not a group")
        name = clean_name(p["name"], max_length=64)
        if not name:
            raise ValueError("a group needs a name")
        await self.supervisor.call("updateGroup", {"account": account, "groupId": rec.value, "name": name}, timeout=60)
        self.store.upsert_conversation(rec, name=name)
        with contextlib.suppress(RpcError, RpcClosed):
            await self.refresh_directory()
        return {"name": name}

    async def op_reloadConfig(self, p: dict) -> dict:
        """Re-read config.toml and apply everything that does not need a
        restart. Returns the keys that do."""
        fresh = Config.load(self.paths)
        from .config import LIVE_KEYS, RESTART_REQUIRED
        changed = []
        pending = []
        for key in fresh.__dataclass_fields__:
            old, new = getattr(self.cfg, key), getattr(fresh, key)
            if old == new:
                continue
            if key in LIVE_KEYS or key == "notification_preview":
                setattr(self.cfg, key, new)
                changed.append(key)
            elif key in RESTART_REQUIRED:
                pending.append(key)
        await self._broadcast("status", self.status())
        return {"applied": changed, "restartRequired": pending}

    async def op_clearHistory(self, p: dict) -> dict:
        self.store.clear_history()
        await self._broadcast("unread", {"total": 0})
        return {"cleared": True}

    async def op_shutdown(self, p: dict) -> dict:
        self._shutdown.set()
        return {"stopping": True}

    # -- directory ----------------------------------------------------------------

    async def refresh_directory(self) -> dict:
        account = self._require_account()
        contacts_raw = await self.supervisor.call("listContacts", {"account": account}, timeout=60)
        groups_raw = await self.supervisor.call("listGroups", {"account": account}, timeout=60)
        contacts = []
        if isinstance(contacts_raw, list):
            for c in contacts_raw:
                if not isinstance(c, dict):
                    continue
                number = c.get("number") if isinstance(c.get("number"), str) else ""
                uuid = c.get("uuid") if isinstance(c.get("uuid"), str) else ""
                try:
                    rec = classify_recipient(number or uuid)
                except InvalidRecipient:
                    continue
                if number == account:
                    continue
                profile = c.get("profile") if isinstance(c.get("profile"), dict) else {}
                pname = " ".join(x for x in (clean_name(profile.get("givenName")), clean_name(profile.get("familyName"))) if x)
                contacts.append({
                    "key": rec.key, "number": number, "uuid": uuid.lower() if uuid else "",
                    "name": clean_name(c.get("name")), "profileName": pname or clean_name(c.get("profileName")),
                    "username": clean_name(c.get("username"), max_length=64),
                    "blocked": bool(c.get("isBlocked")), "color": clean_name(c.get("color"), max_length=32),
                })
        groups = []
        if isinstance(groups_raw, list):
            for g in groups_raw:
                if not isinstance(g, dict):
                    continue
                gid = g.get("id") if isinstance(g.get("id"), str) else ""
                try:
                    rec = classify_recipient("group:" + gid)
                except InvalidRecipient:
                    continue
                members = []
                if isinstance(g.get("members"), list):
                    for m in g["members"][:512]:
                        if isinstance(m, dict):
                            who = m.get("number") if isinstance(m.get("number"), str) else m.get("uuid")
                            try:
                                members.append(classify_recipient(who).key)
                            except InvalidRecipient:
                                continue
                groups.append({"key": rec.key, "groupId": gid, "name": clean_name(g.get("name")),
                               "members": members, "blocked": bool(g.get("isBlocked")),
                               "isMember": g.get("isMember", True)})
        nc = self.store.replace_contacts(contacts)
        ng = self.store.replace_groups(groups)
        self._contacts_refreshed = time.monotonic()
        log.info("directory refreshed: %d contacts, %d groups", nc, ng)
        await self._broadcast("directory", {"contacts": nc, "groups": ng})
        return {"contacts": nc, "groups": ng}


def _guess_mime(path: Path) -> str:
    import mimetypes
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def setup_logging(paths: Paths, level: str, *, to_stderr: bool = False) -> None:
    lvl = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
           "error": logging.ERROR}.get(level, logging.INFO)
    handlers: list[logging.Handler] = []
    if to_stderr or os.environ.get("INVOCATION_ID"):
        handlers.append(logging.StreamHandler(sys.stderr))
    else:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(paths.log_file)
        os.chmod(paths.log_file, 0o600)
        handlers.append(handler)
    logging.basicConfig(level=lvl, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    paths = Paths()
    cfg = Config.load(paths)
    setup_logging(paths, cfg.log_level, to_stderr="--stderr" in (argv or []))
    bridge = Bridge(cfg, paths)
    try:
        asyncio.run(bridge.run())
    except SystemExit as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        pass
    return 0
