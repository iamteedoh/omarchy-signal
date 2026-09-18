# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse omarchy-menu.jsonc the way a tolerant JSONC reader does, and assert keys."""
import json
import re
import sys

from omarchy_signal import confedit

src = open(sys.argv[1], encoding="utf-8").read()
kept = {confedit.CODE, confedit.STRING}
bare = "".join(c if k in kept else (" " if c != "\n" else c) for _i, c, k in confedit.scan(src))
data = json.loads(re.sub(r",(\s*[}\]])", r"\1", bare))
missing = [k for k in sys.argv[2:] if k not in data]
if missing:
    raise SystemExit(f"missing keys {missing} in {sorted(data)}")
