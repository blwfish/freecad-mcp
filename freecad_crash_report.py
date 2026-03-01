"""
FreeCAD Crash Diagnostics — Bridge-side
========================================
Tracks operations sent to FreeCAD and produces rich crash reports.

Files involved:
  /tmp/freecad_mcp_last_op.json  — written by crash_watcher.py *inside* FreeCAD
                                    before each operation; survives crashes
  /tmp/freecad_mcp_oplog.json    — bridge-side ring buffer of recent ops

Used by freecad_mcp_server.py to replace opaque "Connection refused" errors
with actionable crash reports that answer:
  • That it crashed          (process / socket state)
  • Why it crashed           (macOS crash report, exit signal, stderr)
  • What it was doing        (last_op file + bridge op log)

Works for both interactive FreeCAD (user-launched GUI) and headless instances
(spawned via spawn_freecad_instance).
"""

__version__ = "1.0.0"

import glob
import json
import os
import platform
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional

LAST_OP_FILE = "/tmp/freecad_mcp_last_op.json"
OPLOG_FILE   = "/tmp/freecad_mcp_oplog.json"
MAX_OPLOG    = 10

# ─────────────────────────────────────────────────────────────────────────────
# Operation log (bridge-side ring buffer)
# ─────────────────────────────────────────────────────────────────────────────

class OpLog:
    """Ring buffer of recent operations sent to FreeCAD.

    record() is called *before* sending; complete() after a successful reply.
    If FreeCAD crashes, the last record() without a complete() identifies the
    culprit operation.
    """

    def __init__(self):
        self._ops: deque = deque(maxlen=MAX_OPLOG)

    def record(self, tool: str, args: dict) -> None:
        entry = {
            "tool":        tool,
            "summary":     _summarize(tool, args),
            "sent_at":     time.time(),
            "completed":   False,
        }
        self._ops.append(entry)
        self._flush()

    def complete(self) -> None:
        if self._ops:
            self._ops[-1]["completed"]    = True
            self._ops[-1]["completed_at"] = time.time()
        self._flush()

    def last_incomplete(self) -> Optional[dict]:
        for op in reversed(self._ops):
            if not op.get("completed"):
                return op
        return None

    def recent(self, n: int = 5) -> list:
        return list(self._ops)[-n:]

    def _flush(self) -> None:
        try:
            with open(OPLOG_FILE, "w") as f:
                json.dump(list(self._ops), f, indent=2)
        except Exception:
            pass


def _summarize(tool: str, args: dict) -> str:
    """One-line summary of what an operation was doing."""
    if tool in ("execute_python", "execute_python_async"):
        code  = args.get("code", "")
        lines = [l for l in code.strip().splitlines() if l.strip()]
        first = "\n".join(lines[:6])
        tail  = f"\n… (+{len(lines)-6} lines)" if len(lines) > 6 else ""
        return f"{tool}:\n{first}{tail}"
    op = args.get("operation", "")
    if op:
        return f"{tool}(operation={op})"
    return f"{tool}({list(args.keys())})"


# ─────────────────────────────────────────────────────────────────────────────
# Crash diagnosis helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_last_op() -> Optional[dict]:
    """Read what FreeCAD was executing (written by crash_watcher inside FC)."""
    try:
        with open(LAST_OP_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _find_macos_crash_report(max_age_s: int = 180) -> Optional[str]:
    """Return a summary of the most recent FreeCAD crash report (macOS only)."""
    if platform.system() != "Darwin":
        return None

    patterns = [
        os.path.expanduser("~/Library/Logs/DiagnosticReports/FreeCAD*.crash"),
        os.path.expanduser("~/Library/Logs/DiagnosticReports/FreeCAD*.ips"),
        "/Library/Logs/DiagnosticReports/FreeCAD*.crash",
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))

    now = time.time()
    recent = [p for p in candidates if (now - os.path.getmtime(p)) <= max_age_s]
    if not recent:
        return None

    report_path = max(recent, key=os.path.getmtime)
    try:
        with open(report_path) as f:
            content = f.read()
        return _parse_crash_report(content, report_path)
    except Exception as e:
        return f"[crash report at {report_path} — unreadable: {e}]"


def _parse_crash_report(content: str, path: str) -> str:
    """Extract the signal, exception, and crashed-thread backtrace."""
    lines = content.splitlines()
    meta, thread0, in_thread0 = [], [], False

    for line in lines:
        for prefix in ("Exception Type:", "Exception Codes:", "Exception Subtype:",
                       "Termination Signal:", "Termination Reason:", "Crashed Thread:"):
            if line.startswith(prefix):
                meta.append(line)

        if line.startswith("Thread 0 Crashed:") or line.startswith("Thread 0 name:"):
            in_thread0 = True
        if in_thread0:
            thread0.append(line)
            if line == "" and len(thread0) > 3:
                break          # end of thread 0 block

    summary = f"[{Path(path).name}]\n"
    summary += "\n".join(meta)
    if thread0:
        summary += "\n\nCrashed thread (first 20 frames):\n"
        summary += "\n".join(thread0[:20])
    return summary


def _fc_process_info() -> dict:
    """Check whether any FreeCAD process is running (interactive mode)."""
    info = {"running": False, "pid": None, "processes": []}
    try:
        r = subprocess.run(
            ["pgrep", "-lf", "FreeCAD"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            procs = [l for l in r.stdout.strip().splitlines()
                     if "FreeCAD" in l and "grep" not in l]
            if procs:
                info["running"]   = True
                info["processes"] = procs
                try:
                    info["pid"] = int(procs[0].split()[0])
                except ValueError:
                    pass
    except Exception:
        pass
    return info


_SIGNALS = {-11: "SIGSEGV", -6: "SIGABRT", -4: "SIGILL", -8: "SIGFPE", -10: "SIGBUS"}


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def diagnose(
    *,
    socket_path: str = "/tmp/freecad_mcp.sock",
    proc=None,              # subprocess.Popen for headless instances
    op_log: Optional[OpLog] = None,
    error: Optional[Exception] = None,
) -> str:
    """Return a human-readable markdown crash report.

    Call this when send_to_freecad() raises an exception or times out.
    """
    parts = ["## ⚠️ FreeCAD Crash / Disconnect\n"]

    # ── 1. What was the bridge sending? ──────────────────────────────────────
    if op_log:
        incomplete = op_log.last_incomplete()
        if incomplete:
            elapsed = time.time() - incomplete["sent_at"]
            parts.append(
                f"**Operation in flight** (sent {elapsed:.0f}s ago, no reply received):\n"
                f"```\n{incomplete['summary']}\n```"
            )
        recent = op_log.recent(5)
        prev   = [op for op in recent if op.get("completed")][-3:]
        if prev:
            parts.append("**Previously completed:**")
            for op in prev:
                parts.append(f"  - ✓ `{op['tool']}`")

    # ── 2. What was FreeCAD executing? (written by crash_watcher inside FC) ──
    last_op = _read_last_op()
    if last_op:
        age  = time.time() - last_op.get("started_at", time.time())
        tool = last_op.get("tool", "?")
        args = last_op.get("args", {})
        parts.append(f"\n**FreeCAD was executing** `{tool}` ({age:.0f}s ago):")
        if tool in ("execute_python_async", "execute_python"):
            code = args.get("code", "")
            parts.append(f"```python\n{code[:800]}\n```")
        else:
            parts.append(f"```\n{json.dumps(args, indent=2)[:500]}\n```")

    # ── 3. Process state ─────────────────────────────────────────────────────
    if proc is not None:
        # Headless (managed) instance — we have the subprocess object
        rc = proc.poll()
        if rc is None:
            parts.append("\n**Process status:** still running (GUI thread hung / socket not responding)")
        else:
            sig = _SIGNALS.get(rc, "")
            sig_str = f" ({sig})" if sig else ""
            parts.append(f"\n**Process exited** with code **{rc}**{sig_str}")
            try:
                stderr = proc.stderr.read(4096).decode(errors="replace") if proc.stderr else ""
                if stderr.strip():
                    parts.append(f"**stderr:**\n```\n{stderr[-2000:]}\n```")
            except Exception:
                pass
    else:
        # Interactive (user-launched) FreeCAD
        info = _fc_process_info()
        if info["running"]:
            parts.append(
                f"\n**FreeCAD process:** running (PID {info['pid']}) — "
                "likely hung (GUI thread blocked by heavy operation)"
            )
        else:
            parts.append("\n**FreeCAD process:** **not found** — crashed and exited")

    # ── 4. macOS crash report ─────────────────────────────────────────────────
    cr = _find_macos_crash_report()
    if cr:
        parts.append(f"\n**macOS crash report:**\n```\n{cr}\n```")

    # ── 5. Raw error ──────────────────────────────────────────────────────────
    if error:
        parts.append(f"\n**Socket error:** `{type(error).__name__}: {error}`")

    # ── 6. Next steps ─────────────────────────────────────────────────────────
    parts.append("\n**What to do:**")
    if proc is not None and proc.poll() is not None:
        parts.append(
            "- Headless FreeCAD has exited — call `spawn_freecad_instance` to get a fresh one\n"
            "- For heavy geometry (many booleans), consider doing operations in batches"
        )
    else:
        parts.append(
            "- If FreeCAD crashed: restart it and wait for AICopilot to load\n"
            "- If FreeCAD is hung: the Qt GUI thread is blocked (common with large boolean ops)\n"
            "  → Break heavy operations into smaller batches\n"
            "  → Or use `spawn_freecad_instance` (headless) + `execute_python_async`"
        )

    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_op_log = OpLog()

def get_op_log() -> OpLog:
    return _op_log
