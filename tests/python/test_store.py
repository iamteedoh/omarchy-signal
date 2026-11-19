# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _helpers  # noqa: F401
from omarchy_signal.envelope import Attachment, Event
from omarchy_signal.sanitize import Recipient
from omarchy_signal.store import Store

ALICE = Recipient("number", "+15550002222")
ME = Recipient("number", "+15550001111")
GROUP = Recipient("group", "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYg==")


def msg(ts, text="hi", conv=ALICE, sender=ALICE, outgoing=False, **kw):
    return Event(kind="message", conversation=conv, timestamp=ts, sender=sender, sender_name="Alice",
                 outgoing=outgoing, text=text, **kw)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "h.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_file_permissions_are_owner_only(self):
        path = Path(self.tmp.name) / "sub" / "h.sqlite3"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_insert_and_history_order(self):
        self.assertTrue(self.store.add_event_message(msg(3, "c")))
        self.assertTrue(self.store.add_event_message(msg(1, "a")))
        self.assertTrue(self.store.add_event_message(msg(2, "b")))
        self.assertFalse(self.store.add_event_message(msg(2, "b")))  # duplicate
        hist = self.store.history(ALICE.key)
        self.assertEqual([m.body for m in hist], ["a", "b", "c"])
        self.assertEqual([m.body for m in self.store.history(ALICE.key, before_ts=3, limit=1)], ["b"])
        conv = self.store.conversation(ALICE.key)
        self.assertEqual(conv.unread, 3)
        self.assertEqual(conv.last_preview, "c")
        self.assertEqual(conv.name, "Alice")
        self.assertEqual(self.store.total_unread(), 3)

    def test_mark_read_and_mute(self):
        self.store.add_event_message(msg(1))
        self.store.set_muted(ALICE.key, True)
        self.assertEqual(self.store.total_unread(), 0)
        self.store.set_muted(ALICE.key, False)
        self.assertEqual(self.store.total_unread(), 1)
        self.assertEqual(self.store.mark_read(ALICE.key), 1)
        self.assertEqual(self.store.total_unread(), 0)

    def test_outgoing_does_not_count_unread(self):
        self.store.add_event_message(msg(1, outgoing=True, sender=ME))
        self.assertEqual(self.store.total_unread(), 0)
        self.store.add_outgoing(ALICE, 2, ME.key, "sent it", status="sent")
        self.assertEqual(self.store.history(ALICE.key)[-1].status, "sent")

    def test_receipts_promote_monotonically(self):
        self.store.add_outgoing(ALICE, 10, ME.key, "x", status="sent")
        self.assertEqual(self.store.apply_receipt(ALICE, "read", [10]), 1)
        self.assertEqual(self.store.history(ALICE.key)[0].status, "read")
        self.assertEqual(self.store.apply_receipt(ALICE, "delivery", [10]), 0)  # no downgrade
        self.assertEqual(self.store.history(ALICE.key)[0].status, "read")
        # A receipt from someone else for a direct conversation is ignored.
        self.store.add_outgoing(ALICE, 11, ME.key, "y", status="sent")
        self.assertEqual(self.store.apply_receipt(Recipient("number", "+15550003333"), "read", [11]), 0)

    def test_reactions_and_delete(self):
        self.store.add_event_message(msg(1))
        ev = Event(kind="reaction", conversation=ALICE, timestamp=2, sender=ME, emoji="👍", reaction_target_ts=1)
        self.assertTrue(self.store.apply_reaction(ev))
        self.assertEqual(self.store.history(ALICE.key)[0].reactions, {ME.key: "👍"})
        ev.reaction_removed = True
        self.store.apply_reaction(ev)
        self.assertEqual(self.store.history(ALICE.key)[0].reactions, {})
        dl = Event(kind="remote_delete", conversation=ALICE, timestamp=3, sender=ALICE, delete_target_ts=1)
        self.assertTrue(self.store.apply_remote_delete(dl))
        self.assertTrue(self.store.history(ALICE.key)[0].deleted)
        self.assertEqual(self.store.history(ALICE.key)[0].body, "")

    def test_edit(self):
        self.store.add_event_message(msg(1, "typo"))
        self.store.add_event_message(msg(2, "fixed", edit_target_ts=1))
        hist = self.store.history(ALICE.key)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].body, "fixed")
        self.assertTrue(hist[0].edited)

    def test_group_names_and_attachments(self):
        att = Attachment(id="a1", content_type="image/png", filename="x.png", size=3)
        self.store.add_event_message(msg(1, "", conv=GROUP, group_name="Crew", attachments=[att]))
        conv = self.store.conversation(GROUP.key)
        self.assertEqual(conv.name, "Crew")
        self.assertEqual(conv.last_preview, "[image]")
        self.store.set_attachment_path(GROUP.key, 1, "a1", "/tmp/x.png")
        self.assertEqual(self.store.history(GROUP.key)[0].attachments[0].path, "/tmp/x.png")

    def test_contacts_groups_and_display_names(self):
        self.store.replace_contacts([{"key": ALICE.key, "number": ALICE.value, "name": "", "profileName": "Trinity"},
                                     {"key": "number:+15550009999", "number": "+15550009999", "name": "B", "blocked": True}])
        self.assertEqual([c["displayName"] for c in self.store.contacts()], ["Trinity"])
        self.assertEqual(self.store.display_name(ALICE.key), "Trinity")
        self.assertEqual(self.store.display_name("number:+15550004444"), "+15550004444")
        self.store.replace_groups([{"key": GROUP.key, "groupId": GROUP.value, "name": "Crew", "members": [ALICE.key]}])
        self.assertEqual(self.store.display_name(GROUP.key), "Crew")

    def test_search_escapes_like_wildcards(self):
        self.store.add_event_message(msg(1, "100% done"))
        self.store.add_event_message(msg(2, "100 done"))
        self.assertEqual(len(self.store.search("100%")), 1)
        self.assertEqual(self.store.search(""), [])
        self.assertEqual(self.store.search("'; DROP TABLE messages; --"), [])
        self.assertEqual(len(self.store.history(ALICE.key)), 2)

    def test_prune(self):
        self.store.add_event_message(msg(1, "ancient"))
        self.assertEqual(self.store.prune(30), 1)
        self.assertEqual(self.store.prune(0), 0)

    def test_extreme_timestamp_keeps_identity_but_uses_receipt_chronology(self):
        now = 1_800_000_000_000
        old_receipt = now - 31 * 86400 * 1000
        extreme = 2 ** 63 - 1
        with mock.patch("omarchy_signal.store._now_ms", return_value=now):
            self.assertTrue(self.store.add_event_message(
                msg(extreme, "hostile", received_at=old_receipt)))
            self.assertTrue(self.store.apply_reaction(Event(
                kind="reaction", conversation=ALICE, timestamp=now, sender=ALICE,
                reaction_target_ts=extreme, emoji="👍")))
            self.assertTrue(self.store.add_event_message(
                msg(now - 1000, "ordinary", received_at=now)))

            hist = self.store.history(ALICE.key)
            self.assertEqual([m.body for m in hist], ["hostile", "ordinary"])
            self.assertEqual(hist[0].ts, extreme, "Signal identity remains available to reactions and receipts")
            self.assertEqual(hist[0].timeline_ts, old_receipt)
            self.assertEqual(hist[0].to_json()["displayTs"], old_receipt)
            self.assertEqual(self.store.conversation(ALICE.key).last_ts, now - 1000)
            self.assertEqual(self.store.prune(30), 1)
            self.assertEqual([m.body for m in self.store.history(ALICE.key)], ["ordinary"])

    def test_migration_repairs_persisted_extreme_conversation_time(self):
        now = 1_800_000_000_000
        received = now - 1000
        extreme = 2 ** 63 - 1
        with mock.patch("omarchy_signal.store._now_ms", return_value=now):
            self.store.add_event_message(msg(extreme, "persisted", received_at=received))
        self.store.close()
        db = sqlite3.connect(self.path)
        db.execute("DROP INDEX messages_conv_timeline")
        db.execute("ALTER TABLE messages DROP COLUMN timeline_ts")
        db.execute("UPDATE conversations SET last_ts = ?", (extreme,))
        db.commit()
        db.close()

        with mock.patch("omarchy_signal.store._now_ms", return_value=now):
            self.store = Store(self.path)
        row = self.store.history(ALICE.key)[0]
        self.assertEqual((row.ts, row.timeline_ts), (extreme, received))
        self.assertEqual(self.store.conversation(ALICE.key).last_ts, received)

    def test_memory_store(self):
        s = Store(":memory:")
        s.add_event_message(msg(1))
        self.assertEqual(len(s.history(ALICE.key)), 1)
        s.close()


if __name__ == "__main__":
    unittest.main()
