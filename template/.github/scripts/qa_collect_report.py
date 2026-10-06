#!/usr/bin/env python3
"""Copy the agent's report out of its sandbox-writable directory.

Usage: qa_collect_report.py [<src> <dst>]
       (defaults: /tmp/qa-agent/qa-report.md -> /tmp/qa-report.md)

The agent's sandboxed Bash can write only /tmp/qa-agent, so the skill builds
its report there.  The steps after it — the report-missing fallback, the
telemetry footer, the artifact upload — run UNSANDBOXED and use the
job-owned /tmp/qa-report.md, which the agent cannot touch.  This script is
the one crossing between the two, so it treats the source as hostile:

  * the source is opened with O_NOFOLLOW (a symlink is refused, never read
    through — it could point at a file holding a secret) and O_NONBLOCK (a
    FIFO cannot hang the step), then must fstat as a regular file;
  * at most MAX_BYTES are copied, cut at a line boundary;
  * an existing destination is unlinked and the copy created with
    O_CREAT|O_EXCL|O_NOFOLLOW, so it lands in a fresh file of our own;
  * a non-empty regular destination is left alone, so the workflow can call
    this twice (right after the agent, and again in an always() step for a
    run killed at the timeout) without the second call clobbering the first.

Always exits 0 and says what it did: a missing report is the workflow's
report-missing fallback to handle, not a reason to fail the step.
"""

import os
import stat
import sys

SRC = "/tmp/qa-agent/qa-report.md"
DST = "/tmp/qa-report.md"
MAX_BYTES = 512 * 1024
TRUNCATED_NOTE = b"\n\n_(report truncated by qa_collect_report.py at 512 KB)_\n"


def _read_capped(fd, cap):
    chunks, total = [], 0
    while total <= cap:
        chunk = os.read(fd, min(65536, cap + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def collect(src=SRC, dst=DST, cap=MAX_BYTES):
    """-> a one-line description of the outcome (never raises OSError)."""
    try:
        st = os.lstat(dst)
        if stat.S_ISREG(st.st_mode) and st.st_size > 0:
            return f"kept: {dst} already holds a report"
    except FileNotFoundError:
        pass
    except OSError as e:
        return f"error: cannot stat {dst}: {e}"

    try:
        fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return f"missing: {src} does not exist"
    except OSError as e:
        return f"refused: cannot open {src} without following links: {e}"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return f"refused: {src} is not a regular file"
        data = _read_capped(fd, cap)
    except OSError as e:
        return f"error: reading {src}: {e}"
    finally:
        os.close(fd)

    if not data:
        return f"missing: {src} is empty"
    truncated = len(data) > cap
    if truncated:
        data = data[:cap]
        data = data[: data.rfind(b"\n") + 1] or data
        data += TRUNCATED_NOTE

    try:
        try:
            os.unlink(dst)
        except FileNotFoundError:
            pass
        out = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(out, view):]
        finally:
            os.close(out)
    except OSError as e:
        return f"error: writing {dst}: {e}"
    return f"copied: {len(data)} bytes {src} -> {dst}" + (" (truncated)" if truncated else "")


def main(argv):
    if len(argv) not in (1, 3):
        print("usage: qa_collect_report.py [<src> <dst>]", file=sys.stderr)
        return 0
    src, dst = (argv[1], argv[2]) if len(argv) == 3 else (SRC, DST)
    print(f"qa_collect_report: {collect(src, dst)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
