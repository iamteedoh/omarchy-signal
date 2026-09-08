import unittest

import _helpers  # noqa: F401
from omarchy_signal.envelope import parse_receive

GID = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg=="


def env(**over):
    base = {"source": "+15550002222", "sourceNumber": "+15550002222",
            "sourceUuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "sourceName": "Trinity",
            "sourceDevice": 1, "timestamp": 1700000000000}
    base.update(over)
    return {"envelope": base, "account": "+15550001111"}


class DataMessageTests(unittest.TestCase):
    def test_direct_message(self):
        evs = parse_receive(env(dataMessage={"timestamp": 1700000000000, "message": "hi \x1b[31mthere",
                                             "expiresInSeconds": 0, "viewOnce": False, "attachments": []}))
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev.kind, "message")
        self.assertEqual(ev.conversation.key, "number:+15550002222")
        self.assertEqual(ev.sender.key, "number:+15550002222")
        self.assertEqual(ev.text, "hi [31mthere")
        self.assertFalse(ev.outgoing)
        self.assertEqual(ev.sender_name, "Trinity")

    def test_group_message_keys_on_group(self):
        evs = parse_receive(env(dataMessage={"timestamp": 1, "message": "yo",
                                             "groupInfo": {"groupId": GID, "groupName": "Crew‮", "type": "DELIVER"}}))
        self.assertEqual(evs[0].conversation.key, "group:" + GID)
        self.assertEqual(evs[0].group_name, "Crew")

    def test_bad_group_id_falls_back_to_sender(self):
        evs = parse_receive(env(dataMessage={"timestamp": 1, "message": "yo",
                                             "groupInfo": {"groupId": "../../x", "groupName": "Crew"}}))
        self.assertEqual(evs[0].conversation.kind, "number")

    def test_attachments_are_sanitised(self):
        evs = parse_receive(env(dataMessage={"timestamp": 1, "message": "", "attachments": [
            {"contentType": "image/png", "filename": "../../../.bashrc", "id": "1234-abc", "size": 10, "width": 4, "height": 4},
            {"contentType": "\x1b[1m", "filename": None, "id": "x/y", "size": -5},
            "garbage",
        ]}))
        ev = evs[0]
        self.assertEqual(len(ev.attachments), 2)
        self.assertEqual(ev.attachments[0].filename, "bashrc")
        self.assertEqual(ev.attachments[1].content_type, "application/octet-stream")
        self.assertEqual(ev.attachments[1].id, "y")
        self.assertEqual(ev.attachments[1].size, 0)

    def test_empty_message_without_attachments_is_ignored(self):
        self.assertEqual(parse_receive(env(dataMessage={"timestamp": 1, "message": None})), [])

    def test_sync_sent_message_is_outgoing(self):
        evs = parse_receive(env(syncMessage={"sentMessage": {
            "destination": "+15550003333", "destinationNumber": "+15550003333", "timestamp": 5,
            "message": "from my phone", "expiresInSeconds": 0}}))
        self.assertEqual(len(evs), 1)
        self.assertTrue(evs[0].outgoing)
        self.assertEqual(evs[0].conversation.key, "number:+15550003333")
        self.assertEqual(evs[0].sender.key, "number:+15550001111")

    def test_sync_read_messages(self):
        evs = parse_receive(env(syncMessage={"readMessages": [{"sender": "+15550003333", "senderNumber": "+15550003333", "timestamp": 9}]}))
        self.assertEqual(evs[0].kind, "read_sync")
        self.assertEqual(evs[0].conversation.key, "number:+15550003333")

    def test_receipt(self):
        evs = parse_receive(env(receiptMessage={"when": 1, "isDelivery": True, "isRead": False, "timestamps": [1, 2, "x", -1]}))
        self.assertEqual(evs[0].kind, "receipt")
        self.assertEqual(evs[0].receipt_type, "delivery")
        self.assertEqual(evs[0].receipt_timestamps, [1, 2])

    def test_typing(self):
        evs = parse_receive(env(typingMessage={"action": "STARTED", "timestamp": 1}))
        self.assertEqual(evs[0].kind, "typing")
        self.assertEqual(evs[0].typing, "started")
        evs = parse_receive(env(typingMessage={"action": "STOPPED", "timestamp": 1, "groupId": GID}))
        self.assertEqual(evs[0].conversation.kind, "group")

    def test_reaction_and_delete(self):
        evs = parse_receive(env(dataMessage={"timestamp": 3, "reaction": {
            "emoji": "🔥", "targetAuthor": "+15550001111", "targetAuthorNumber": "+15550001111",
            "targetSentTimestamp": 2, "isRemove": False}}))
        self.assertEqual(evs[0].kind, "reaction")
        self.assertEqual(evs[0].emoji, "🔥")
        self.assertEqual(evs[0].reaction_target_author, "number:+15550001111")
        evs = parse_receive(env(dataMessage={"timestamp": 4, "remoteDelete": {"timestamp": 1}}))
        self.assertEqual(evs[0].kind, "remote_delete")
        self.assertEqual(evs[0].delete_target_ts, 1)

    def test_edit(self):
        evs = parse_receive(env(editMessage={"targetSentTimestamp": 1, "dataMessage": {"timestamp": 8, "message": "fixed"}}))
        self.assertEqual(evs[0].edit_target_ts, 1)
        self.assertEqual(evs[0].text, "fixed")

    def test_hostile_shapes_never_raise(self):
        for params in (None, [], "x", {"envelope": None}, {"envelope": []}, {"envelope": {}},
                       {"envelope": {"dataMessage": "str"}}, {"envelope": {"source": 5, "dataMessage": {"message": "x"}}},
                       {"envelope": {"sourceNumber": "+15550002222", "dataMessage": {"message": "x", "timestamp": "abc",
                                                                                     "quote": {"id": [], "text": 5},
                                                                                     "mentions": "no", "attachments": {"a": 1}}}},
                       {"envelope": {"sourceNumber": "+15550002222", "syncMessage": {"sentMessage": "str", "readMessages": "str"}}},
                       {"envelope": {"sourceNumber": "+15550002222", "receiptMessage": {"timestamps": "str"}}},
                       {"envelope": {"sourceNumber": "+15550002222", "typingMessage": []}},
                       {"envelope": {"sourceNumber": "+15550002222", "exception": {"message": "boom"}}}):
            with self.subTest(params=params):
                parse_receive(params, "+15550001111")

    def test_unknown_sender_ignored(self):
        self.assertEqual(parse_receive({"envelope": {"source": "not a number", "dataMessage": {"message": "x", "timestamp": 1}}}), [])


if __name__ == "__main__":
    unittest.main()
