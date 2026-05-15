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


def set_current_op(tool: str, args: dict) -> None:
    """Write current operation to disk BEFORE executing it.

    Truncates large args so the file stays readable.
    Safe to call from any thread — os.write is atomic for small payloads.
    """
    safe_args = {}
    for k, v in args.items():
        s = str(v)
        safe_args[k] = (s[:_MAX_ARG_BYTES] + " … [truncated]") if len(s) > _MAX_ARG_BYTES else s

    data = {
        "tool":       tool,
        "args":       safe_args,
        "started_at": time.time(),
        "pid":        os.getpid(),
    }
    try:
        payload = json.dumps(data).encode()
        # Write to a temp file then rename for atomicity
        tmp = LAST_OP_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, LAST_OP_FILE)
    except Exception:
        pass    # never crash the crash watcher


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
