#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: demo-art.py
## Date Created: 2026-11-12
## Description: Draws the demo's other pictures (the two capsules and the
##              ship) as SVG readouts in the same style as tactical-map.py.
##              Fixed seed, standard library only, nothing read from the
##              machine it runs on.
"""Usage: demo-art.py red-pill|blue-pill|ship > picture.svg"""
import math
import random
import sys

W, H = 1200, 900
CYAN, ICE, DIM = "#39d0ff", "#b8f1ff", "#1c6f8f"
AMBER = "#ffb347"
FONT = "JetBrainsMono Nerd Font, JetBrains Mono, DejaVu Sans Mono, monospace"

out = []
add = out.append


def text(x, y, s, size=13, fill=CYAN, op=0.9, anchor="start", weight="400", spacing=1.5):
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
        f'fill-opacity="{op}" text-anchor="{anchor}" font-weight="{weight}" letter-spacing="{spacing}" '
        f'paint-order="stroke" stroke="#03121b" stroke-width="5" stroke-linejoin="round">{s}</text>')


def begin(extra_defs=""):
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
    add(f'''<defs>
<radialGradient id="bg" cx="50%" cy="50%" r="75%">
  <stop offset="0" stop-color="#0a2a3a"/><stop offset="0.55" stop-color="#041521"/><stop offset="1" stop-color="#01070c"/>
</radialGradient>
<radialGradient id="vig" cx="50%" cy="50%" r="72%">
  <stop offset="0.6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.75"/>
</radialGradient>
<filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>
<filter id="haze" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="22"/></filter>
<filter id="soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="9"/></filter>
<pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="1" fill="#000" fill-opacity="0.28"/></pattern>
<pattern id="fine" width="25" height="25" patternUnits="userSpaceOnUse"><path d="M25 0H0V25" fill="none" stroke="{CYAN}" stroke-opacity="0.07" stroke-width="1"/></pattern>
<pattern id="major" width="100" height="100" patternUnits="userSpaceOnUse"><path d="M100 0H0V100" fill="none" stroke="{CYAN}" stroke-opacity="0.13" stroke-width="1"/></pattern>
{extra_defs}
</defs>''')
    add(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    add(f'<rect width="{W}" height="{H}" fill="url(#fine)"/><rect width="{W}" height="{H}" fill="url(#major)"/>')


def frame(title, sub, accent):
    add(f'<rect x="28" y="28" width="{W - 56}" height="{H - 56}" fill="none" stroke="{CYAN}" stroke-opacity="0.22" stroke-width="1"/>')
    for x, y, dx, dy in ((28, 28, 1, 1), (W - 28, 28, -1, 1), (W - 28, H - 28, -1, -1), (28, H - 28, 1, -1)):
        add(f'<path d="M{x} {y + dy * 46}V{y}H{x + dx * 46}" fill="none" stroke="{ICE}" stroke-width="2.4"/>')
    for i, x in enumerate(range(100, W - 90, 25)):
        add(f'<line x1="{x}" y1="28" x2="{x}" y2="{38 if i % 4 else 46}" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
    for i, y in enumerate(range(150, H - 90, 25)):
        add(f'<line x1="28" y1="{y}" x2="{38 if i % 4 else 46}" y2="{y}" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
    text(96, 100, title, 30, ICE, 1, weight="700", spacing=6)
    text(98, 126, sub, 12, CYAN, 0.8, spacing=3)
    add(f'<rect x="96" y="136" width="330" height="2" fill="{CYAN}" fill-opacity="0.8"/>')
    add(f'<rect x="96" y="136" width="74" height="2" fill="{accent}"/>')


def end():
    add(f'<rect width="{W}" height="{H}" fill="url(#scan)"/>')
    add(f'<rect width="{W}" height="{H}" fill="url(#vig)"/>')
    add("</svg>")
    sys.stdout.write("\n".join(out) + "\n")


def rows(x, y, w, pairs, accent):
    for i, (k, v) in enumerate(pairs):
        yy = y + i * 30
        text(x, yy, k, 11, CYAN, 0.7, spacing=2)
        text(x + w, yy, v, 13, accent if i == 0 else ICE, 1, anchor="end", weight="700")
        add(f'<line x1="{x}" y1="{yy + 9}" x2="{x + w}" y2="{yy + 9}" stroke="{CYAN}" stroke-opacity="0.16"/>')


def callout(x, y, tx, ty, label, sub, colour=ICE):
    """A leader from a point on the subject to a label."""
    side = 1 if tx > x else -1
    add(f'<circle cx="{x}" cy="{y}" r="4" fill="#03121b" stroke="{colour}" stroke-width="1.5"/>')
    add(f'<path d="M{x} {y}L{tx} {ty}H{tx + side * 150}" fill="none" stroke="{colour}" stroke-opacity="0.8" stroke-width="1"/>')
    anchor = "start" if side > 0 else "end"
    text(tx + side * 6, ty - 8, label, 12, colour, 1, anchor=anchor, weight="700", spacing=2)
    text(tx + side * 6, ty + 16, sub, 10, CYAN, 0.75, anchor=anchor)


def pill(name, code, light, mid, dark):
    rng = random.Random(len(name) * 31 + 5)
    begin(f'''<linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{light}"/><stop offset="0.45" stop-color="{mid}"/><stop offset="1" stop-color="{dark}"/>
</linearGradient>
<linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#fff" stop-opacity="0.85"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
</linearGradient>
<clipPath id="cap"><rect x="-230" y="-82" width="460" height="164" rx="82"/></clipPath>''')
    cx, cy = 560, 480
    for i, rad in enumerate((150, 230, 310)):
        add(f'<circle cx="{cx}" cy="{cy}" r="{rad}" fill="none" stroke="{CYAN}" stroke-opacity="{0.4 - i * 0.09:.2f}" '
            f'stroke-width="1" stroke-dasharray="{"2 7" if i == 1 else "none"}"/>')
    for deg in range(0, 360, 6):
        t = math.radians(deg)
        r1, r2 = 310, 318 if deg % 30 else 330
        add(f'<line x1="{cx + r1 * math.cos(t):.1f}" y1="{cy + r1 * math.sin(t):.1f}" x2="{cx + r2 * math.cos(t):.1f}" '
            f'y2="{cy + r2 * math.sin(t):.1f}" stroke="{CYAN}" stroke-opacity="0.5" stroke-width="1"/>')
    add(f'<path d="M{cx - 360} {cy}H{cx - 250}M{cx + 250} {cy}H{cx + 360}M{cx} {cy - 360}V{cy - 190}M{cx} {cy + 190}V{cy + 360}" '
        f'stroke="{CYAN}" stroke-opacity="0.45" stroke-width="1"/>')
    # arcs that read as a scan in progress
    for rad, a0, a1 in ((270, 200, 250), (190, 20, 75), (350, 300, 340)):
        p0 = (cx + rad * math.cos(math.radians(a0)), cy + rad * math.sin(math.radians(a0)))
        p1 = (cx + rad * math.cos(math.radians(a1)), cy + rad * math.sin(math.radians(a1)))
        add(f'<path d="M{p0[0]:.1f} {p0[1]:.1f}A{rad} {rad} 0 0 1 {p1[0]:.1f} {p1[1]:.1f}" fill="none" stroke="{ICE}" stroke-width="3" stroke-opacity="0.85"/>')

    # the capsule: a lit, glossy solid over its own glow and shadow
    add(f'<ellipse cx="{cx + 14}" cy="{cy + 150}" rx="230" ry="26" fill="#000" fill-opacity="0.55" filter="url(#soft)"/>')
    add(f'<g transform="translate({cx} {cy}) rotate(-24)">')
    add(f'<rect x="-230" y="-82" width="460" height="164" rx="82" fill="{mid}" fill-opacity="0.55" filter="url(#haze)"/>')
    add('<rect x="-230" y="-82" width="460" height="164" rx="82" fill="url(#body)"/>')
    add('<g clip-path="url(#cap)">')
    add(f'<rect x="-230" y="30" width="460" height="60" fill="{dark}" fill-opacity="0.55" filter="url(#soft)"/>')
    add(f'<rect x="0" y="-82" width="230" height="164" fill="#000" fill-opacity="0.13"/>')
    add(f'<ellipse cx="0" cy="62" rx="200" ry="14" fill="{light}" fill-opacity="0.35" filter="url(#soft)"/>')
    add('</g>')
    add('<rect x="-196" y="-66" width="392" height="34" rx="17" fill="url(#shine)" fill-opacity="0.9"/>')
    add('<ellipse cx="-176" cy="-30" rx="14" ry="26" fill="#fff" fill-opacity="0.35" filter="url(#glow)"/>')
    add(f'<line x1="0" y1="-82" x2="0" y2="82" stroke="{dark}" stroke-opacity="0.8" stroke-width="2"/>')
    add('<line x1="3" y1="-80" x2="3" y2="80" stroke="#fff" stroke-opacity="0.25" stroke-width="1"/>')
    add(f'<rect x="-230" y="-82" width="460" height="164" rx="82" fill="none" stroke="{light}" stroke-opacity="0.7" stroke-width="1.5"/>')
    add('</g>')

    callout(cx - 150, cy - 10, cx - 300, cy - 230, "SHELL", "GELATIN  0.11 MM", ICE)
    callout(cx + 120, cy - 130, cx + 250, cy - 250, "PAYLOAD", code, light)
    callout(cx + 60, cy + 55, cx + 220, cy + 170, "SEAM", "FUSED  NO TAMPER")

    frame(name, "COMPOUND ANALYSIS  /  SAMPLE " + code, mid)
    px = 96
    text(px, 690, "SPECTRUM", 10, CYAN, 0.7, spacing=2)
    for i in range(44):
        v = max(0.06, math.exp(-((i - 14) / 5.0) ** 2) * 0.9 + math.exp(-((i - 31) / 3.0) ** 2) * 0.55 + rng.uniform(0, 0.1))
        add(f'<rect x="{px + i * 6}" y="{790 - v * 80:.1f}" width="3" height="{v * 80:.1f}" fill="{mid if 9 < i < 20 else CYAN}" fill-opacity="{0.4 + v * 0.55:.2f}"/>')
    rows(880, 716, 224, (("MATCH", "99.7%"), ("MASS", "0.50 G"), ("LENGTH", "21.4 MM"), ("STATE", "STABLE")), light)
    text(96, H - 46, "SCAN COMPLETE", 11, ICE, 1, weight="700", spacing=2)
    text(300, H - 46, "CHAMBER 02", 11, CYAN, 0.8, spacing=2)
    end()


def ship():
    rng = random.Random(11)
    hull = [(150, 470), (215, 418), (330, 384), (470, 360), (640, 348), (800, 352), (930, 372), (1020, 404), (1062, 446),
            (1050, 486), (990, 520), (880, 540), (700, 552), (500, 552), (330, 540), (220, 516)]
    pts = " ".join(f"{x},{y}" for x, y in hull)
    begin(f'<clipPath id="hull"><polygon points="{pts}"/></clipPath>')
    # the field under the pads
    pads = (300, 450, 600, 750, 900)
    for x in pads:
        add(f'<ellipse cx="{x}" cy="640" rx="70" ry="12" fill="{CYAN}" fill-opacity="0.35" filter="url(#soft)"/>')
        for k in range(-3, 4):
            add(f'<line x1="{x + k * 9}" y1="582" x2="{x + k * 17}" y2="{640 - abs(k) * 3}" stroke="{CYAN}" stroke-opacity="{0.5 - abs(k) * 0.1:.2f}" stroke-width="1"/>')
        for i, ry in enumerate((7, 11, 15)):
            add(f'<ellipse cx="{x}" cy="{604 + i * 14}" rx="{34 + i * 14}" ry="{ry}" fill="none" stroke="{ICE}" stroke-opacity="{0.8 - i * 0.22:.2f}" stroke-width="1.2"/>')
        add(f'<path d="M{x - 30} 552L{x - 22} 582H{x + 22}L{x + 30} 552" fill="#03121b" stroke="{ICE}" stroke-width="1.5"/>')
    # hull: glow, fill, then the structure inside it
    add(f'<polygon points="{pts}" fill="none" stroke="{CYAN}" stroke-width="10" stroke-opacity="0.4" filter="url(#glow)"/>')
    add(f'<polygon points="{pts}" fill="#06202d" fill-opacity="0.85"/>')
    add('<g clip-path="url(#hull)">')
    for x in range(170, 1070, 30):
        add(f'<line x1="{x}" y1="330" x2="{x}" y2="570" stroke="{CYAN}" stroke-opacity="{0.55 if x % 150 == 20 else 0.22}" stroke-width="1"/>')
    for y, op in ((400, 0.3), (440, 0.55), (486, 0.55), (520, 0.3)):
        add(f'<path d="M140 {y}Q600 {y - 26} 1070 {y}" fill="none" stroke="{CYAN}" stroke-opacity="{op}" stroke-width="1"/>')
    for x0 in (380, 560, 740):
        add(f'<rect x="{x0}" y="446" width="130" height="36" fill="{CYAN}" fill-opacity="0.08" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
        for k in range(5):
            add(f'<rect x="{x0 + 10 + k * 24}" y="456" width="14" height="{rng.randrange(6, 18)}" fill="{ICE}" fill-opacity="0.55"/>')
    add(f'<path d="M880 392L990 420L1010 452L900 452Z" fill="{CYAN}" fill-opacity="0.22" stroke="{ICE}" stroke-width="1.4"/>')
    add(f'<path d="M905 400L925 452M940 408L955 452M975 418L983 452" stroke="{ICE}" stroke-opacity="0.7" stroke-width="1"/>')
    add(f'<circle cx="250" cy="468" r="34" fill="none" stroke="{ICE}" stroke-width="1.4"/>')
    add(f'<circle cx="250" cy="468" r="20" fill="{CYAN}" fill-opacity="0.3" stroke="{CYAN}" stroke-width="1"/>')
    add(f'<circle cx="250" cy="468" r="50" fill="{CYAN}" fill-opacity="0.25" filter="url(#soft)"/>')
    add('</g>')
    add(f'<polygon points="{pts}" fill="none" stroke="{ICE}" stroke-width="2"/>')
    # upper works
    add(f'<path d="M520 356L540 322H700L724 350" fill="#06202d" stroke="{ICE}" stroke-width="1.6"/>')
    add(f'<path d="M560 322V286M600 322V262M640 322V296" stroke="{ICE}" stroke-width="1.4"/>')
    add(f'<circle cx="600" cy="258" r="4" fill="{AMBER}"/><circle cx="600" cy="258" r="12" fill="{AMBER}" fill-opacity="0.4" filter="url(#glow)"/>')
    add(f'<path d="M330 384L300 350H400L420 372" fill="#06202d" stroke="{ICE}" stroke-width="1.4"/>')

    # dimension line
    add(f'<path d="M150 720H1062M150 708V732M1062 708V732" stroke="{CYAN}" stroke-opacity="0.8" stroke-width="1"/>')
    for x in range(150, 1063, 38):
        add(f'<line x1="{x}" y1="714" x2="{x}" y2="726" stroke="{CYAN}" stroke-opacity="0.6" stroke-width="1"/>')
    text(606, 752, "LENGTH OVERALL  62.4 M", 11, ICE, 0.95, anchor="middle", spacing=3)

    callout(948, 420, 1010, 250, "BRIDGE", "OPERATOR STATION")
    callout(250, 468, 190, 270, "MAIN DRIVE", "OUTPUT 71%", AMBER)
    callout(445, 464, 330, 220, "CORE DECK", "BROADCAST CHAIRS")
    add(f'<circle cx="750" cy="604" r="4" fill="#03121b" stroke="{ICE}" stroke-width="1.5"/>')
    add(f'<path d="M750 604L840 800H1000" fill="none" stroke="{ICE}" stroke-opacity="0.8" stroke-width="1"/>')
    text(846, 792, "HOVER PADS", 12, ICE, 1, weight="700", spacing=2)
    text(846, 816, "5 OF 5 ONLINE", 10, CYAN, 0.75)

    frame("NEBUCHADNEZZAR", "HOVERCRAFT  /  HULL SCHEMATIC  /  PORT ELEVATION", AMBER)
    rows(96, 780, 240, (("STATUS", "UNDER WAY"), ("DEPTH", "4.2 KM")), AMBER)
    end()


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if kind == "red-pill":
        pill("RED CAPSULE", "R-01  TRACE PROGRAM", "#ff8a98", "#e02540", "#6d0a1c")
    elif kind == "blue-pill":
        pill("BLUE CAPSULE", "B-02  SEDATIVE", "#8fc4ff", "#2a6fe0", "#0b2a6d")
    elif kind == "ship":
        ship()
    else:
        sys.exit(__doc__)
