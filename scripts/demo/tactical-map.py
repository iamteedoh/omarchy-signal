#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: tactical-map.py
## Date Created: 2026-11-12
## Description: Draws the demo's "map" attachment as an SVG: a tunnel-grid
##              tactical display with a locked route. Everything on it is
##              generated from a fixed seed, so the picture is the same on
##              every run and contains nothing from the machine it runs on.
"""Usage: tactical-map.py > map.svg   (standard library only)"""
import math
import random
import sys
from collections import deque

W, H = 1440, 900
CYAN, ICE, DIM = "#39d0ff", "#b8f1ff", "#1c6f8f"
RED, AMBER = "#ff4058", "#ffb347"
FONT = "JetBrainsMono Nerd Font, JetBrains Mono, DejaVu Sans Mono, monospace"

rng = random.Random(7)
out = []
add = out.append


def text(x, y, s, size=13, fill=CYAN, op=0.9, anchor="start", weight="400", spacing=1.5):
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
        f'fill-opacity="{op}" text-anchor="{anchor}" font-weight="{weight}" letter-spacing="{spacing}" '
        f'paint-order="stroke" stroke="#03121b" stroke-width="5" stroke-linejoin="round">{s}</text>')


# ---------------------------------------------------------------- the network
COLS, ROWS, STEP = 19, 12, 54
X0, Y0 = 96, 150
nodes = {(c, r) for c in range(COLS) for r in range(ROWS) if rng.random() > 0.2}
edges = set()
for (c, r) in sorted(nodes):
    for dc, dr, p in ((1, 0, 0.72), (0, 1, 0.6), (1, 1, 0.13), (1, -1, 0.1)):
        n = (c + dc, r + dr)
        if n in nodes and rng.random() < p:
            edges.add(((c, r), n))
adj = {}
for a, b in edges:
    adj.setdefault(a, []).append(b)
    adj.setdefault(b, []).append(a)


def reach(start):
    seen, todo = {start: None}, deque([start])
    while todo:
        cur = todo.popleft()
        for n in sorted(adj.get(cur, [])):
            if n not in seen:
                seen[n] = cur
                todo.append(n)
    return seen


# Keep the largest connected piece, then route across it corner to corner.
best = {}
for n in sorted(adj):
    if n not in best:
        got = reach(n)
        if len(got) > len(best):
            best = got
live = set(best)
edges = {(a, b) for a, b in edges if a in live and b in live}
start = min(live, key=lambda n: (n[0] - 1) ** 2 + (n[1] - (ROWS - 2)) ** 2)
goal = min(live, key=lambda n: (n[0] - (COLS - 2)) ** 2 + (n[1] - 1) ** 2)
came = reach(start)
route, cur = [], goal
while cur is not None:
    route.append(cur)
    cur = came[cur]
route.reverse()
on_route = set(zip(route, route[1:])) | set(zip(route[1:], route))


def pos(n):
    return X0 + n[0] * STEP, Y0 + n[1] * STEP


# ------------------------------------------------------------------ the frame
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add(f'''<defs>
<radialGradient id="bg" cx="42%" cy="52%" r="75%">
  <stop offset="0" stop-color="#0a2a3a"/><stop offset="0.55" stop-color="#041521"/><stop offset="1" stop-color="#01070c"/>
</radialGradient>
<radialGradient id="vig" cx="50%" cy="50%" r="72%">
  <stop offset="0.6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.75"/>
</radialGradient>
<linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="{CYAN}" stop-opacity="0"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0.28"/>
</linearGradient>
<filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="5"/></filter>
<filter id="haze" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/></filter>
<pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="#000" fill-opacity="0.28"/></pattern>
<pattern id="fine" width="27" height="27" patternUnits="userSpaceOnUse"><path d="M27 0H0V27" fill="none" stroke="{CYAN}" stroke-opacity="0.07" stroke-width="1"/></pattern>
<pattern id="major" width="108" height="108" patternUnits="userSpaceOnUse"><path d="M108 0H0V108" fill="none" stroke="{CYAN}" stroke-opacity="0.13" stroke-width="1"/></pattern>
</defs>''')
add(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
add(f'<rect width="{W}" height="{H}" fill="url(#fine)"/><rect width="{W}" height="{H}" fill="url(#major)"/>')

# ------------------------------------------------- depth contours under it all
for cx, cy, base in ((330, 600, 60), (820, 330, 70), (1010, 690, 45)):
    harm = [(k, rng.uniform(0.03, 0.11), rng.uniform(0, 6.28)) for k in (2, 3, 5, 7)]
    for level in range(7):
        rad = base + level * 38
        pts = []
        for i in range(0, 361, 4):
            t = math.radians(i)
            rr = rad * (1 + sum(a * math.sin(k * t + ph) for k, a, ph in harm))
            pts.append(f"{cx + rr * math.cos(t):.1f},{cy + rr * math.sin(t) * 0.72:.1f}")
        add(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{DIM}" stroke-opacity="{0.8 - level * 0.07:.2f}" '
            f'stroke-width="1" {"stroke-dasharray=\'2 5\'" if level % 2 else ""}/>')

# ------------------------------------------------------------------- tunnels
def segs(pairs):
    return " ".join(f"M{pos(a)[0]} {pos(a)[1]}L{pos(b)[0]} {pos(b)[1]}" for a, b in sorted(pairs))


plain = {e for e in edges if e not in on_route}
trunk = {e for e in plain if e[0][1] == e[1][1] and e[0][1] % 3 == 1 or e[0][0] == e[1][0] and e[0][0] % 4 == 2}
add(f'<path d="{segs(plain)}" stroke="{CYAN}" stroke-width="7" stroke-opacity="0.35" fill="none" filter="url(#glow)" stroke-linecap="round"/>')
add(f'<path d="{segs(plain - trunk)}" stroke="{CYAN}" stroke-width="1.4" stroke-opacity="0.75" fill="none" stroke-linecap="round"/>')
add(f'<path d="{segs(trunk)}" stroke="{CYAN}" stroke-width="5" stroke-opacity="0.3" fill="none" stroke-linecap="round"/>')
add(f'<path d="{segs(trunk)}" stroke="{ICE}" stroke-width="1.6" stroke-opacity="0.95" fill="none" stroke-linecap="round"/>')

labelled = 0
for n in sorted(live):
    x, y = pos(n)
    deg = len(adj[n])
    if n in route:
        continue
    if deg >= 4:
        add(f'<rect x="{x - 5}" y="{y - 5}" width="10" height="10" fill="#041521" stroke="{ICE}" stroke-width="1.4"/>')
        if labelled < 9 and rng.random() < 0.3:
            labelled += 1
            text(x + 9, y - 9, f"J-{n[0]:02d}{n[1]:02d}", 10, CYAN, 0.7)
    elif deg == 1:
        add(f'<circle cx="{x}" cy="{y}" r="3.5" fill="none" stroke="{CYAN}" stroke-opacity="0.8" stroke-width="1.2"/>')
    else:
        add(f'<circle cx="{x}" cy="{y}" r="2" fill="{ICE}" fill-opacity="0.85"/>')

# ---------------------------------------------------- range rings and a sweep
gx, gy = pos(goal)
sx, sy = pos(start)
for i, rad in enumerate((70, 140, 210, 280)):
    add(f'<circle cx="{gx}" cy="{gy}" r="{rad}" fill="none" stroke="{CYAN}" stroke-opacity="{0.34 - i * 0.06:.2f}" '
        f'stroke-width="1" stroke-dasharray="{"1 6" if i % 2 else "none"}"/>')
for deg in range(0, 360, 10):
    t = math.radians(deg)
    r1, r2 = 280, 288 if deg % 30 else 298
    add(f'<line x1="{gx + r1 * math.cos(t):.1f}" y1="{gy + r1 * math.sin(t):.1f}" x2="{gx + r2 * math.cos(t):.1f}" '
        f'y2="{gy + r2 * math.sin(t):.1f}" stroke="{CYAN}" stroke-opacity="0.45" stroke-width="1"/>')
a0, a1 = math.radians(118), math.radians(162)
add(f'<path d="M{gx} {gy}L{gx + 280 * math.cos(a0):.1f} {gy + 280 * math.sin(a0):.1f}A280 280 0 0 1 '
    f'{gx + 280 * math.cos(a1):.1f} {gy + 280 * math.sin(a1):.1f}Z" fill="url(#sweep)"/>')

# ------------------------------------------------------------------ the route
rp = " ".join(("M" if i == 0 else "L") + f"{pos(n)[0]} {pos(n)[1]}" for i, n in enumerate(route))
add(f'<path d="{rp}" stroke="{RED}" stroke-width="22" stroke-opacity="0.35" fill="none" filter="url(#haze)" stroke-linejoin="round" stroke-linecap="round"/>')
add(f'<path d="{rp}" stroke="{RED}" stroke-width="8" stroke-opacity="0.75" fill="none" filter="url(#glow)" stroke-linejoin="round" stroke-linecap="round"/>')
add(f'<path d="{rp}" stroke="{RED}" stroke-width="3.2" fill="none" stroke-linejoin="round" stroke-linecap="round"/>')
add(f'<path d="{rp}" stroke="#ffd9de" stroke-width="1" stroke-opacity="0.9" fill="none" stroke-linejoin="round" stroke-dasharray="10 12"/>')
for i, n in enumerate(route[1:-1], 1):
    x, y = pos(n)
    add(f'<circle cx="{x}" cy="{y}" r="3.2" fill="#041521" stroke="{RED}" stroke-width="1.6"/>')
    if i % 5 == 0:
        text(x + 10, y + 16, f"WP {i // 5:02d}", 10, "#ff9aa8", 0.9)

# start: where we are
add(f'<circle cx="{sx}" cy="{sy}" r="20" fill="none" stroke="{AMBER}" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="3 4"/>')
add(f'<circle cx="{sx}" cy="{sy}" r="11" fill="none" stroke="{AMBER}" stroke-width="1.6"/>')
add(f'<circle cx="{sx}" cy="{sy}" r="4" fill="{AMBER}"/>')
add(f'<path d="M{sx - 34} {sy + 36}H{sx - 16}L{sx - 6} {sy + 14}" fill="none" stroke="{AMBER}" stroke-opacity="0.8" stroke-width="1"/>')
text(sx - 34, sy + 52, "ORIGIN", 12, AMBER, 1, weight="700")
text(sx - 34, sy + 67, "HARD LINE 0311", 10, AMBER, 0.75)

# goal: the exit
add(f'<circle cx="{gx}" cy="{gy}" r="30" fill="{RED}" fill-opacity="0.25" filter="url(#glow)"/>')
add(f'<path d="M{gx} {gy - 15}L{gx + 15} {gy}L{gx} {gy + 15}L{gx - 15} {gy}Z" fill="#1a0509" stroke="{RED}" stroke-width="2.2"/>')
add(f'<circle cx="{gx}" cy="{gy}" r="3.5" fill="#ffd9de"/>')
for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
    add(f'<path d="M{gx + dx * 30} {gy + dy * 20}V{gy + dy * 30}H{gx + dx * 20}" fill="none" stroke="{RED}" stroke-width="1.6"/>')
add(f'<path d="M{gx + 18} {gy - 18}L{gx + 40} {gy - 46}H{gx + 150}" fill="none" stroke="{RED}" stroke-opacity="0.9" stroke-width="1"/>')
text(gx + 44, gy - 54, "EXIT 04", 15, "#ffd9de", 1, weight="700", spacing=2.5)
text(gx + 44, gy - 30, "ROUTE LOCKED", 10, "#ff9aa8", 0.9)

# hostiles, off the route
placed = 0
for n in sorted(live, key=lambda _: rng.random()):
    x, y = pos(n)
    if n in route or min(abs(n[0] - m[0]) + abs(n[1] - m[1]) for m in route) < 3 or placed == 3:
        continue
    placed += 1
    add(f'<circle cx="{x}" cy="{y}" r="24" fill="none" stroke="{AMBER}" stroke-opacity="0.55" stroke-width="1" stroke-dasharray="4 4"/>')
    add(f'<path d="M{x} {y - 9}L{x + 8} {y + 6}H{x - 8}Z" fill="{AMBER}" fill-opacity="0.25" stroke="{AMBER}" stroke-width="1.4"/>')
    text(x + 28, y + 4, f"CONTACT {placed:02d}", 10, AMBER, 0.85)

# ----------------------------------------------------------------- the readout
add(f'<rect x="28" y="28" width="{W - 56}" height="{H - 56}" fill="none" stroke="{CYAN}" stroke-opacity="0.22" stroke-width="1"/>')
for x, y, dx, dy in ((28, 28, 1, 1), (W - 28, 28, -1, 1), (W - 28, H - 28, -1, -1), (28, H - 28, 1, -1)):
    add(f'<path d="M{x} {y + dy * 46}V{y}H{x + dx * 46}" fill="none" stroke="{ICE}" stroke-width="2.4"/>')
for i, x in enumerate(range(96, 1100, 27)):
    add(f'<line x1="{x}" y1="28" x2="{x}" y2="{38 if i % 4 else 46}" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
    if i % 8 == 0:
        text(x + 4, 58, f"{310 + i * 2:04d}", 9, CYAN, 0.6, spacing=1)
for i, y in enumerate(range(150, 800, 27)):
    add(f'<line x1="28" y1="{y}" x2="{38 if i % 4 else 46}" y2="{y}" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
    if i % 8 == 0:
        text(52, y + 3, f"{72 + i:03d}", 9, CYAN, 0.6, spacing=1)

text(96, 96, "TUNNEL GRID 07", 30, ICE, 1, weight="700", spacing=6)
text(98, 122, "SUBLEVEL 4  /  DEPTH 4.2 KM  /  LIVE FEED", 12, CYAN, 0.8, spacing=3)
add(f'<rect x="96" y="132" width="330" height="2" fill="{CYAN}" fill-opacity="0.8"/>')
add(f'<rect x="96" y="132" width="74" height="2" fill="{RED}"/>')

# right-hand panel
px, pw = 1150, 240
add(f'<rect x="{px}" y="150" width="{pw}" height="620" fill="#03121b" fill-opacity="0.82" stroke="{CYAN}" stroke-opacity="0.4" stroke-width="1"/>')
add(f'<rect x="{px}" y="150" width="{pw}" height="30" fill="{CYAN}" fill-opacity="0.16"/>')
text(px + 14, 170, "ROUTE ANALYSIS", 12, ICE, 1, weight="700", spacing=3)
hops = len(route) - 1
rows = (("WAYPOINTS", f"{hops:02d}"), ("DISTANCE", f"{hops * 0.14:.2f} KM"), ("ETA", "00:04:12"),
        ("JUNCTIONS", f"{sum(1 for n in live if len(adj[n]) >= 4):03d}"), ("SEGMENTS", f"{len(edges):03d}"), ("SIGNAL", "LOCKED"))
for i, (k, v) in enumerate(rows):
    y = 208 + i * 30
    text(px + 14, y, k, 11, CYAN, 0.7, spacing=2)
    text(px + pw - 14, y, v, 13, RED if k == "ETA" else ICE, 1, anchor="end", weight="700")
    add(f'<line x1="{px + 14}" y1="{y + 9}" x2="{px + pw - 14}" y2="{y + 9}" stroke="{CYAN}" stroke-opacity="0.16"/>')
text(px + 14, 412, "DEPTH PROFILE", 10, CYAN, 0.7, spacing=2)
prof, val = [], 0.5
for i in range(54):
    val = min(0.95, max(0.08, val + rng.uniform(-0.16, 0.16)))
    prof.append(val)
    add(f'<rect x="{px + 14 + i * 4}" y="{500 - val * 70:.1f}" width="2" height="{val * 70:.1f}" fill="{CYAN}" fill-opacity="{0.35 + val * 0.5:.2f}"/>')
line = " ".join(f"{px + 15 + i * 4},{500 - v * 70 - 4:.1f}" for i, v in enumerate(prof))
add(f'<polyline points="{line}" fill="none" stroke="{ICE}" stroke-width="1.2"/>')
text(px + 14, 536, "SECTOR LOAD", 10, CYAN, 0.7, spacing=2)
for i in range(5):
    y, frac = 550 + i * 22, rng.uniform(0.25, 0.95)
    text(px + 14, y + 9, f"S{i + 1}", 10, CYAN, 0.8)
    add(f'<rect x="{px + 40}" y="{y}" width="{pw - 96}" height="9" fill="none" stroke="{CYAN}" stroke-opacity="0.4"/>')
    add(f'<rect x="{px + 42}" y="{y + 2}" width="{(pw - 100) * frac:.1f}" height="5" fill="{AMBER if frac > 0.8 else CYAN}" fill-opacity="0.9"/>')
    text(px + pw - 14, y + 9, f"{frac * 100:.0f}%", 10, ICE, 0.9, anchor="end")
for i in range(6):
    text(px + 14, 690 + i * 14, " ".join(f"{rng.randrange(65536):04X}" for _ in range(5)), 9, CYAN, 0.42, spacing=1)

text(96, H - 46, "GRID REF 0311-072", 11, CYAN, 0.8, spacing=2)
text(330, H - 46, "SCALE 1 : 2400", 11, CYAN, 0.8, spacing=2)
text(520, H - 46, "BEARING 047", 11, CYAN, 0.8, spacing=2)
add(f'<rect x="700" y="{H - 57}" width="10" height="10" fill="{RED}"/>')
text(718, H - 46, "ROUTE LOCKED", 11, "#ffd9de", 1, weight="700", spacing=2)

add(f'<rect width="{W}" height="{H}" fill="url(#scan)"/>')
add(f'<rect width="{W}" height="{H}" fill="url(#vig)"/>')
add("</svg>")
sys.stdout.write("\n".join(out) + "\n")
