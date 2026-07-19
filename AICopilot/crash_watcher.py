"""
FreeCAD Crash Watcher (runs *inside* FreeCAD)
==============================================
Writes the currently-executing operation to /tmp before it runs.
If FreeCAD crashes mid-operation, this file survives and the bridge-side
freecad_crash_report.py reads it to answer "what was it doing?"

Usage (called by freecad_mcp_handler.py):
    from crash_watcher import set_current_op, clear_current_op

    set_current_op("execute_python_async", {"code": "..."})
    try:
        result = do_the_thing()
    finally:
        clear_current_op()

The file is intentionally NOT cleared on crash — that's the whole point.
"""

__version__ = "1.1.0"

import json
import os
import time

# Per-instance file: each FreeCAD process writes to its own path so that
# multiple concurrent instances don't clobber one another's crash context.
LAST_OP_FILE = f"/tmp/freecad_mcp_last_op_{os.getpid()}.json"
_MAX_ARG_BYTES = 1500   # truncate large args (e.g. long Python scripts)

# A persistent write failure (disk full, permissions, /tmp not writable)
# used to be completely invisible — the whole crash-diagnosis mechanism
# this module exists for would silently stop working with no counter, no
# log line, nothing. Tracked here so it's at least observable to anything
# that checks; a best-effort console warning fires once per failure
# streak (not every call) so a full disk doesn't spam the Report View.
_write_failures = 0
_last_write_failed = False


def set_current_op(tool: str, args: dict) -> None:
    """Write current operation to disk BEFORE executing it.

    Truncates large args so the file stays readable.
    Safe to call from any thread — os.write is atomic for small payloads.
    """
    safe_args = {}
    for k, v in args.items():
        s = str(v)
        # _MAX_ARG_BYTES is a BYTE limit — truncate on the encoded bytes, not the
        # character count, so multibyte UTF-8 args don't blow past it.
        b = s.encode("utf-8")
        if len(b) > _MAX_ARG_BYTES:
            safe_args[k] = b[:_MAX_ARG_BYTES].decode("utf-8", errors="ignore") + " … [truncated]"
        else:
            safe_args[k] = s

    data = {
        "tool":       tool,
        "args":       safe_args,
        "started_at": time.time(),
        "pid":        os.getpid(),
    }
    global _write_failures, _last_write_failed
    try:
        payload = json.dumps(data).encode()
        # Write to a temp file then rename for atomicity
        tmp = LAST_OP_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, LAST_OP_FILE)
        _last_write_failed = False
    except Exception as e:
        # Never crash the crash watcher — but don't let the failure vanish
        # either. _write_failures is always safe to increment; the console
        # warning is best-effort and independently guarded so a problem
        # writing it can't become a new crash source.
        _write_failures += 1
        if not _last_write_failed:
            _last_write_failed = True
            try:
                import FreeCAD
                FreeCAD.Console.PrintWarning(
                    f"[MCP] crash_watcher: failed to write last-op file "
                    f"({_write_failures} failure(s) so far): {e}\n"
                )
            except Exception:
                pass


def get_write_failure_count() -> int:
    """Number of times set_current_op's atomic write has failed since this
    process started. Persistent failures (disk full, permissions, /tmp not
    writable) were previously invisible — nothing counted or logged them."""
    return _write_failures


def clear_current_op() -> None:
    """Remove the last-op file after a successful operation.

    If the operation crashed, this is never called — leaving the file
    in place for post-mortem analysis.
    """
    try:
        os.unlink(LAST_OP_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def read_current_op() -> dict | None:
    """Read the last-op file (useful for in-process diagnostics)."""
    try:
        with open(LAST_OP_FILE) as f:
            return json.load(f)
    except Exception:
        return None
