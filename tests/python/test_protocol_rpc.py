# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json
import unittest

import _helpers  # noqa: F401
from omarchy_signal import protocol
from omarchy_signal.rpc import JsonRpcClient, RpcClosed, RpcError


class ProtocolTests(unittest.TestCase):
    def test_valid_request(self):
        rid, op, params = protocol.validate_request({"id": 1, "op": "history", "conversation": "number:+1", "limit": 5, "junk": 1})
        self.assertEqual((rid, op, params), (1, "history", {"conversation": "number:+1", "limit": 5}))

    def test_rejects_bad_shapes(self):
        bad = [
            [], "x", None, {"op": "ping"}, {"id": True, "op": "ping"}, {"id": 1.5, "op": "ping"},
            {"id": "x" * 65, "op": "ping"}, {"id": 1, "op": "nope"}, {"id": 1, "op": "__class__"},
            {"id": 1, "op": "history"}, {"id": 1, "op": "history", "conversation": 5},
            {"id": 1, "op": "history", "conversation": "k", "limit": True},
            {"id": 1, "op": "send", "conversation": "k", "attachments": [1]},
            {"id": 1, "op": "send", "conversation": "k", "attachments": ["x"] * 33},
            {"id": 1, "op": "send", "conversation": "k", "text": "x" * (64 * 1024 + 1)},
        ]
        for msg in bad:
            with self.subTest(msg=str(msg)[:60]):
                self.assertRaises(protocol.ProtocolError, protocol.validate_request, msg)

    def test_every_op_has_a_handler_name(self):
        from omarchy_signal.bridge import Bridge
        for op in protocol.OPS:
            if op in ("subscribe", "unsubscribe"):
                continue
            self.assertTrue(hasattr(Bridge, f"op_{op}"), op)

    def test_encode_decode(self):
        self.assertEqual(protocol.decode(protocol.encode({"a": "ü"})), {"a": "ü"})
        self.assertRaises(protocol.ProtocolError, protocol.decode, b"{")


class RpcTests(unittest.IsolatedAsyncioTestCase):
    async def _pair(self):
        reader = asyncio.StreamReader()
        sent = []

        class W:
            def write(self, b):
                sent.append(b)

            async def drain(self):
                pass
        notes = []
        client = JsonRpcClient(reader, W(), on_notification=lambda m, p: notes.append((m, p)), timeout=1)
        client.start()
        return reader, sent, notes, client

    async def test_out_of_order_responses(self):
        reader, sent, notes, client = await self._pair()
        t1 = asyncio.create_task(client.call("a"))
        t2 = asyncio.create_task(client.call("b"))
        await asyncio.sleep(0)
        ids = [json.loads(s)["id"] for s in sent]
        reader.feed_data(json.dumps({"jsonrpc": "2.0", "result": "B", "id": ids[1]}).encode() + b"\n")
        reader.feed_data(json.dumps({"jsonrpc": "2.0", "result": "A", "id": ids[0]}).encode() + b"\n")
        self.assertEqual(await t1, "A")
        self.assertEqual(await t2, "B")
        client.close()

    async def test_error_and_notification_and_garbage(self):
        reader, sent, notes, client = await self._pair()
        t = asyncio.create_task(client.call("x"))
        await asyncio.sleep(0)
        rid = json.loads(sent[0])["id"]
        reader.feed_data(b"not json\n[1,2]\n")
        reader.feed_data(json.dumps({"jsonrpc": "2.0", "method": "receive", "params": {"k": 1}}).encode() + b"\n")
        reader.feed_data(json.dumps({"jsonrpc": "2.0", "error": {"code": -32601, "message": "nope"}, "id": rid}).encode() + b"\n")
        with self.assertRaises(RpcError) as ctx:
            await t
        self.assertEqual(ctx.exception.code, -32601)
        self.assertEqual(notes, [("receive", {"k": 1})])
        client.close()

    async def test_timeout_and_close(self):
        reader, sent, notes, client = await self._pair()
        with self.assertRaises(RpcError):
            await client.call("slow", timeout=0.05)
        t = asyncio.create_task(client.call("y"))
        await asyncio.sleep(0)
        reader.feed_eof()
        with self.assertRaises(RpcClosed):
            await t
        self.assertTrue(client.closed)

    async def test_oversized_line_closes(self):
        reader = asyncio.StreamReader(limit=64)
        client = JsonRpcClient(reader, None)
        client.start()
        reader.feed_data(b"x" * 200)
        await asyncio.sleep(0.05)
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
