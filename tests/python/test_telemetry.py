# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal terminal telemetry tests
## Date Created: 2026-11-13
## Description: Unit tests for the terminal client telemetry figures and readout line.
"""The terminal client's telemetry: the figures (held to the vectors the chat
window's Telemetry.js is held to) and the readout line that carries them."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import _helpers  # noqa: F401
from omarchy_signal import telemetry, tui
from omarchy_signal.config import SETTINGS, Config, Paths
from omarchy_signal.term import Key, screen_cells, str_width
from test_tui_render import make_app, seed

VECTORS = json.loads((_helpers.ROOT / "tests" / "vectors" / "telemetry.json").read_text(encoding="utf-8"))


def at(hour, minute, second=0, day=13):
    """Local time on purpose: "today" and the hour of the day are the reader's."""
    return int(datetime(2026, 11, day, hour, minute, second).timestamp() * 1000)


NOW = at(21, 30)


def msg(ts, outgoing, **extra):
    row = {"ts": ts, "outgoing": outgoing, "sender": "" if outgoing else "number:+15550002222",
           "senderName": "" if outgoing else "Trinity", "body": "", "status": "sent" if outgoing else ""}
    row.update(extra)
    return row


class FigureTests(unittest.TestCase):
    def test_shared_vectors_hold(self):
        self.assertGreaterEqual(len(VECTORS["threadStats"]), 5)
        for case in VECTORS["threadStats"]:
            stats = telemetry.thread_stats(case["rows"], case["now"])
            for key, want in case["expect"].items():
                self.assertEqual(stats[key], want, f"{case['name']}: {key}")
        for case in VECTORS["shortDuration"]:
            self.assertEqual(telemetry.short_duration(case["ms"]), case["out"], f"short_duration({case['ms']})")
        for case in VECTORS["median"]:
            self.assertEqual(telemetry.median(case["in"]), case["out"], f"median({case['in']})")

    def test_nothing_is_all_zeroes_with_24_empty_hours(self):
        for rows in ([], None, [{"ts": 0}, None, {"body": "no timestamp"}, {"ts": True}]):
            stats = telemetry.thread_stats(rows, NOW)
            self.assertEqual(stats["total"], 0)
            self.assertEqual(stats["hours"], [0] * 24)
            self.assertEqual(stats["peakHour"], -1)
            self.assertEqual(stats["replyTheirs"], -1)

    def test_today_and_the_24_hour_graph_end_with_the_hour_running_now(self):
        rows = [msg(at(21, 5), False), msg(at(21, 20), True),          # this hour: the last bucket
                msg(at(20, 59), False),                                  # the hour before
                msg(at(22, 0, 0, 12), False),                            # 23 hours back: the first bucket
                msg(at(21, 59, 0, 12), False),                           # just outside the window
                msg(at(21, 10, 0, 10), False), msg(at(21, 15, 0, 9), False)]
        stats = telemetry.thread_stats(rows, NOW)
        self.assertEqual(stats["hours"][23], 2)
        self.assertEqual(stats["hours"][22], 1)
        self.assertEqual(stats["hours"][0], 1)
        self.assertEqual(sum(stats["hours"]), 4)
        self.assertEqual(stats["hoursMax"], 2)
        self.assertEqual(stats["peakHour"], 21)
        self.assertEqual(stats["today"], 3)

    def test_bad_durations_say_nothing(self):
        for bad in (-1, None, "soon", float("nan"), True):
            self.assertEqual(telemetry.short_duration(bad), "")

    def test_spark_and_meter(self):
        self.assertEqual(telemetry.spark([0, 0, 0]), "▁▁▁")
        self.assertEqual(telemetry.spark([0, 1, 4, 8]), "▁▂▅█")
        self.assertEqual(telemetry.spark([1, 1000])[0], "▂")            # it happened: it is not drawn as nothing
        self.assertEqual(telemetry.spark([]), "")
        self.assertEqual(telemetry.meter(0, 10), "▱▱▱▱▱▱▱▱")
        self.assertEqual(telemetry.meter(5, 10), "▰▰▰▰▱▱▱▱")
        self.assertEqual(telemetry.meter(1, 1000), "▰▱▱▱▱▱▱▱")
        self.assertEqual(telemetry.meter(50, 10), "▰▰▰▰▰▰▰▰")
        self.assertEqual(telemetry.meter(3, 0), "▱▱▱▱▱▱▱▱")

    def test_readout_puts_a_failure_ahead_of_everything_but_traffic(self):
        rows = [msg(at(21, 0), True, status="read"), msg(at(21, 2), False), msg(at(21, 3), True, status="failed")]
        segments = telemetry.readout(telemetry.thread_stats(rows, NOW), NOW)
        self.assertEqual([s[0] for s in segments[:2]], ["TX", "FAILED"])
        self.assertEqual(segments[1][2], "alarm")
        labels = [s[0] for s in segments]
        self.assertNotIn("READ", labels)
        self.assertIn("ANSWERS", labels)
        self.assertEqual(dict((s[0], s[1]) for s in segments)["LAST IN"], "28m")
        # Nothing sent, nothing answered: only what is known is shown.
        only_theirs = telemetry.readout(telemetry.thread_stats([msg(at(21, 0), False)], NOW), NOW)
        self.assertEqual([s[0] for s in only_theirs], ["TX", "LAST IN", "24H"])


class IdentityDateTests(unittest.TestCase):
    """The key age in the telemetry rail comes from this. It was always 0 on a
    real account: the bridge read addedDate, signal-cli sends addedTimestamp."""

    def test_reads_the_field_signal_cli_sends(self):
        from omarchy_signal.bridge import identity_added
        self.assertEqual(identity_added({"addedTimestamp": 1700000000000}), 1700000000000)
        self.assertEqual(identity_added({"addedDate": 1600000000000}), 1600000000000)      # an older answer
        self.assertEqual(identity_added({"addedTimestamp": 1700000000000, "addedDate": 5}), 1700000000000)
        self.assertEqual(identity_added({"addedTimestamp": "1700000000000"}), 1700000000000)

    def test_nonsense_is_undated_not_a_crash(self):
        from omarchy_signal.bridge import identity_added
        for item in ({}, {"addedTimestamp": None}, {"addedTimestamp": -5}, {"addedTimestamp": True},
                     {"addedTimestamp": "soon"}, {"addedTimestamp": 1.5e12}, {"addedTimestamp": 0}):
            self.assertEqual(identity_added(item), 0, item)


class ReadoutLineTests(unittest.TestCase):
    """The row under the conversation's title in the terminal client."""

    def rule_row(self, app):
        app.term.out.clear()
        app.draw()
        grid = screen_cells(app.term.text(), app.term.cols, app.term.rows)
        return "".join(grid[3]).rstrip(), grid          # row 4 on screen: the title is on row 3

    def test_setting_exists_and_is_live(self):
        spec = next(s for s in SETTINGS if s["key"] == "telemetry")
        self.assertEqual(spec["type"], "bool")
        self.assertFalse(spec.get("restart"))
        self.assertTrue(Config().telemetry)
        self.assertFalse(Config.from_dict({"telemetry": False}).telemetry)

    def test_line_carries_the_readings_and_keeps_its_width(self):
        app = make_app()
        seed(app)
        row, grid = self.rule_row(app)
        pane = row[tui.LIST_WIDTH + 1:]
        self.assertIn("◢ TX 1", pane)
        self.assertIn("2 RX", pane)
        self.assertIn("READ 1/1", pane)
        # The same row, the same extent as the plain rule it replaces.
        app.status["telemetry"] = False
        plain, _ = self.rule_row(app)
        self.assertEqual(set(plain[tui.LIST_WIDTH + 1:]), {"╌"})
        self.assertEqual(str_width(plain), str_width(row))

    def test_a_narrow_terminal_drops_readings_instead_of_overflowing(self):
        for cols in (70, 76, 84, 96, 120, 200):
            app = make_app(cols=cols, rows=24)
            seed(app)
            row, _ = self.rule_row(app)
            self.assertLessEqual(str_width(row), cols - 1, f"{cols} columns")
            self.assertTrue(row.endswith("╌"), f"{cols} columns: {row[-12:]!r}")

    def test_no_messages_no_readings(self):
        app = make_app()
        seed(app)
        app.messages[app.active_key] = []
        row, _ = self.rule_row(app)
        self.assertEqual(set(row[tui.LIST_WIDTH + 1:]), {"╌"})

    def test_the_bridge_announcing_the_setting_wins_over_the_file(self):
        app = make_app()
        self.assertTrue(app.telemetry_on())
        app.status["telemetry"] = False              # switched off in the chat window
        self.assertFalse(app.telemetry_on())
        app.status["telemetry"] = "yes"              # nonsense from a bridge is not a setting
        self.assertTrue(app.telemetry_on())

    def test_alt_t_switches_it_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            app = make_app()
            seed(app)
            app.paths = Paths(config_dir=Path(d) / "cfg")
            asyncio.run(app.handle_key(Key("char", char="t", alt=True)))
            self.assertFalse(app.telemetry_on())
            self.assertFalse(Config.load(app.paths).telemetry)
            row, _ = self.rule_row(app)
            self.assertNotIn("TX", row)
            asyncio.run(app.handle_key(Key("char", char="t", alt=True)))
            self.assertTrue(app.telemetry_on())
            self.assertTrue(Config.load(app.paths).telemetry)


if __name__ == "__main__":
    unittest.main()
