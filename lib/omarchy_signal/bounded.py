# SPDX-License-Identifier: GPL-3.0-or-later
## Author: Tito Valentin
## Name of Program: omarchy-signal bounded subprocess runner
## Date Created: 2026-11-13
## Description: Runs a helper program with a size limit on what it may print and a deadline for the whole run, killing its process group past either.
"""Running a helper whose output we do not control.

``subprocess.run(capture_output=True)`` reads everything a program prints
before anything can be checked, and its ``timeout`` only starts counting
between reads. For output that comes from somewhere else (the clipboard's
owner through wl-paste, a git remote, a picture somebody sent) that is not
good enough: a producer that never stops, or keeps trickling, holds memory and
time without limit.

:func:`run` counts the bytes as they arrive and keeps one deadline for the
whole run. Past either it kills the program's whole process group (it runs in
a session of its own) and reports what it had.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass
class Result:
    returncode: int          # -1 when it was killed (too much output, or out of time)
    stdout: bytes            # at most limit + 1 bytes: one more than the limit means there was more
    stderr: bytes            # at most stderr_limit bytes
    overflow: bool = False
    timed_out: bool = False

    def text(self, encoding: str = "utf-8") -> str:
        return self.stdout.decode(encoding, "replace")


def run(argv: list[str], *, limit: int, timeout: float, stderr_limit: int = 8192,
        cwd: str | None = None, env: dict | None = None) -> Result:
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd, env=env, start_new_session=True)
    assert proc.stdout is not None and proc.stderr is not None
    deadline = time.monotonic() + timeout
    out, err = bytearray(), bytearray()
    streams = {proc.stdout.fileno(): out, proc.stderr.fileno(): err}
    overflow = timed_out = False
    try:
        while streams:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            ready, _, _ = select.select(list(streams), [], [], left)
            for fd in ready:
                buf = streams[fd]
                cap = limit + 1 if buf is out else stderr_limit
                chunk = os.read(fd, 65536)
                if not chunk:
                    del streams[fd]
                    continue
                room = cap - len(buf)
                buf += chunk[:max(0, room)]
                if buf is out and len(out) > limit:
                    overflow = True
            if overflow:
                break
        if not overflow and not timed_out:
            try:
                return Result(proc.wait(max(0.01, deadline - time.monotonic())), bytes(out), bytes(err))
            except subprocess.TimeoutExpired:
                timed_out = True
        return Result(-1, bytes(out), bytes(err), overflow, timed_out)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
