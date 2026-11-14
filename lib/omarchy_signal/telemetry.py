# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal terminal telemetry
## Date Created: 2026-11-13
## Description: Works out the terminal client telemetry readout from history rows.
"""Telemetry for the terminal client's readout line.

The same figures the chat window draws beside a conversation (Telemetry.js):
traffic in each direction, how far your own messages got, how quickly each
side answers. Pure functions over the bridge's history rows; nothing here
talks to the network. Both implementations are held to
``tests/vectors/telemetry.json``, so the two clients cannot disagree about a
conversation.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

HOUR_MS = 3_600_000
# A reply that takes longer than this is a new conversation, not an answer.
REPLY_WINDOW_MS = 12 * HOUR_MS
# The chat window computes over the same number of messages.
WINDOW = 240

_LINK_RE = re.compile(r"""https?://[^\s<>"']+""")
_DELIVERY = ("sending", "sent", "delivered", "read", "failed")
_BARS = "▁▂▃▄▅▆▇█"


def median(values) -> int:
    """The middle value, the rounded mean of the middle two, or -1 for none."""
    nums = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not nums:
        return -1
    mid = len(nums) // 2
    if len(nums) % 2:
        return int(nums[mid])
    return int((nums[mid - 1] + nums[mid]) / 2 + 0.5)


def short_duration(ms) -> str:
    """``42s``, ``3m``, ``2h 05m``, ``3d 4h``, ``45d``, ``3.0y``; "" for no duration."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms != ms or ms < 0:
        return ""
    if ms < 1000:
        return "<1s"
    s = int(ms // 1000)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h {m % 60:02d}m"
    d = h // 24
    if d < 14:
        return f"{d}d {h % 24}h"
    if d < 365:
        return f"{d}d"
    return f"{d / 365:.1f}y"


def empty_stats() -> dict:
    return {
        "total": 0, "tx": 0, "rx": 0, "today": 0,
        "delivery": {k: 0 for k in _DELIVERY},
        "lastRx": 0, "lastTx": 0, "lastTxStatus": "",
        "media": 0, "links": 0, "reactions": 0, "edited": 0, "expiring": 0,
        "replyTheirs": -1, "replyYours": -1, "transit": -1,
        "hours": [0] * 24, "hoursMax": 0, "peakHour": -1,
        "senders": [], "firstTs": 0,
    }


def _number(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def thread_stats(rows, now_ms: int | None = None) -> dict:
    """Everything the readout shows about one conversation.

    ``rows`` are the bridge's history rows in any order. ``now_ms`` is passed
    in so "today" and the 24 hour window are testable."""
    s = empty_stats()
    msgs = [m for m in (rows or []) if isinstance(m, dict) and _number(m.get("ts")) > 0]
    if not msgs:
        return s
    msgs.sort(key=lambda m: m["ts"])
    now = int(now_ms) if now_ms else int(time.time() * 1000)
    today = datetime.fromtimestamp(now / 1000)
    day_start = int(today.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    # The 24 buckets end with the hour that is running now.
    window_end = int(today.replace(minute=0, second=0, microsecond=0).timestamp() * 1000) + HOUR_MS
    window_start = window_end - 24 * HOUR_MS
    by_hour = [0] * 24
    theirs: list[int] = []
    yours: list[int] = []
    transit: list[int] = []
    senders: dict[str, int] = {}
    prev = None

    for m in msgs:
        ts = int(m["ts"])
        outgoing = m.get("outgoing") is True
        status = m.get("status") if isinstance(m.get("status"), str) else ""
        s["total"] += 1
        if outgoing:
            s["tx"] += 1
            s["lastTx"] = ts
            s["lastTxStatus"] = status
            if status in s["delivery"]:
                s["delivery"][status] += 1
            elif status == "viewed":
                s["delivery"]["read"] += 1
        else:
            s["rx"] += 1
            s["lastRx"] = ts
            got = int(_number(m.get("receivedAt")))
            # Sender's clock to ours: only believable when it is small and positive.
            if got >= ts and got - ts < 5 * 60000:
                transit.append(got - ts)
        if ts >= day_start:
            s["today"] += 1
        if m.get("edited") is True:
            s["edited"] += 1
        if _number(m.get("expiresIn")) > 0:
            s["expiring"] += 1
        if m.get("deleted") is not True:
            if isinstance(m.get("attachments"), list):
                s["media"] += len(m["attachments"])
            if isinstance(m.get("body"), str):
                s["links"] += len(_LINK_RE.findall(m["body"]))
        if isinstance(m.get("reactions"), dict):
            s["reactions"] += len(m["reactions"])

        if window_start <= ts < window_end:
            s["hours"][(ts - window_start) // HOUR_MS] += 1
        by_hour[datetime.fromtimestamp(ts / 1000).hour] += 1

        # The first message after the other side spoke is the reply to it.
        if prev is not None and (prev.get("outgoing") is True) != outgoing:
            gap = ts - int(prev["ts"])
            if 0 <= gap <= REPLY_WINDOW_MS:
                (yours if outgoing else theirs).append(gap)
        prev = m

        who = "You" if outgoing else str(m.get("senderName") or m.get("sender") or "?")
        senders[who] = senders.get(who, 0) + 1

    s["firstTs"] = int(msgs[0]["ts"])
    s["replyTheirs"] = median(theirs)
    s["replyYours"] = median(yours)
    s["transit"] = median(transit)
    s["hoursMax"] = max(s["hours"])
    best = max(by_hour)
    s["peakHour"] = by_hour.index(best) if best > 0 else -1
    s["senders"] = [{"name": name, "count": count}
                    for name, count in sorted(senders.items(), key=lambda kv: (-kv[1], kv[0]))[:4]]
    return s


def spark(values, peak: int = 0) -> str:
    """A row of block characters, one a value: ``▁▁▃█``."""
    nums = [max(0, int(_number(v))) for v in (values or [])]
    top = peak if peak > 0 else max(nums, default=0)
    if top <= 0:
        return _BARS[0] * len(nums)
    return "".join(_BARS[0] if v <= 0 else _BARS[max(1, min(7, round(v / top * 7)))] for v in nums)


def meter(value: int, total: int, cells: int = 8) -> str:
    """``▰▰▰▱▱▱▱▱``: the share of ``total`` that ``value`` is. Something that
    happened at all lights at least one cell."""
    cells = max(1, cells)
    if value <= 0 or total <= 0:
        lit = 0
    else:
        lit = max(1, min(cells, int(value / total * cells + 0.5)))
    return "▰" * lit + "▱" * (cells - lit)


def readout(stats: dict, now_ms: int | None = None) -> list[tuple[str, str, str]]:
    """The segments of the readout line, most useful first, as
    ``(label, value, tone)``. ``tone`` is "" | "accent" | "alarm"; the caller
    draws as many as fit."""
    now = int(now_ms) if now_ms else int(time.time() * 1000)
    out: list[tuple[str, str, str]] = []
    out.append(("TX", f"{stats['tx']} {meter(stats['tx'], stats['total'])} {stats['rx']} RX", "accent"))
    delivery = stats["delivery"]
    if delivery["failed"] > 0:
        out.append(("FAILED", str(delivery["failed"]), "alarm"))
    elif stats["tx"] > 0:
        out.append(("READ", f"{delivery['read']}/{stats['tx']}", ""))
    if stats["replyTheirs"] >= 0:
        out.append(("ANSWERS", "~" + short_duration(stats["replyTheirs"]), ""))
    if stats["lastRx"] > 0:
        out.append(("LAST IN", short_duration(max(0, now - stats["lastRx"])), ""))
    if stats["hoursMax"] > 0:
        out.append(("24H", spark(stats["hours"]), "accent"))
    if stats["replyYours"] >= 0:
        out.append(("YOU", "~" + short_duration(stats["replyYours"]), ""))
    return out
