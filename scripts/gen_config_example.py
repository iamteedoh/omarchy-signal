#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate docs/config.example.toml from the settings schema, so the
example can never drift from the keys the code understands."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from omarchy_signal.config import Config, Paths, save_config  # noqa: E402

docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
with tempfile.TemporaryDirectory() as tmp:
    written = save_config(Config(), Paths(config_dir=__import__("pathlib").Path(tmp)))
    text = open(written, encoding="utf-8").read()
out = os.path.join(docs, "config.example.toml")
with open(out, "w", encoding="utf-8") as fh:
    fh.write(text)
os.chmod(out, 0o644)
print("wrote", os.path.relpath(out))
