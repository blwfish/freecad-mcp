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
  • Whether saved files are intact  (FCStd ZIP integrity check)
  • Whether a crash loop is in progress  (corrupt recovery file detection)

Works for both interactive FreeCAD (user-launched GUI) and headless instances
(spawned via spawn_freecad_instance).

WHY MCP CRASHES CORRUPT STATE (but manual crashes often recover cleanly)
------------------------------------------------------------------------
FreeCAD maintains two saving mechanisms:
  1. Explicit saves  (doc.saveAs) — atomic, completed before crash, file is fine
  2. FreeCAD's own autosave/recovery files  (~Library/Application Support/FreeCAD/)

With manual work, FreeCAD's autosave timer has been running for minutes; the
recovery files are self-consistent. On crash, FreeCAD restores cleanly.

With MCP, operations are fast and programmatic. The crash (often during
saveImage() blocking the GUI thread) happens seconds after the document was
built — before FreeCAD's autosave has run. Recovery files are either stale
(from a previous session) or partially written mid-crash.

On restart FreeCAD tries to auto-restore from those partial files → second
immediate crash → crash loop. The socket file exists (left from the prior run
or created briefly before the second crash), but connections are immediately
refused. This is the "stickup" pattern.

The fix: detect corrupt recovery files and remove them before restarting FC.
The explicitly-saved FCStd file is usually intact and can be opened normally
once the recovery loop is broken.
"""

__version__ = "1.1.0"

import glob
import json
import os
import platform
import subprocess
import time
import zipfile
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
# FCStd file integrity
# ─────────────────────────────────────────────────────────────────────────────

def validate_fcstd(path: str) -> dict:
    """Check whether an FCStd file is an intact ZIP archive.

    FCStd is a ZIP containing Document.xml plus geometry blobs.  A truncated
    or partially-written file will fail ZipFile construction or testzip().

    Returns {"valid": bool, "error": str|None, "size_bytes": int}
    """
    result: dict = {"valid": False, "error": None, "size_bytes": 0}
    try:
        result["size_bytes"] = os.path.getsize(path)
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad:
                result["error"] = f"Corrupt member: {bad}"
            else:
                result["valid"] = True
    except zipfile.BadZipFile as exc:
        result["error"] = f"Bad ZIP: {exc}"
    except FileNotFoundError:
        result["error"] = "File not found"
    except Exception as exc:
        result["error"] = str(exc)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# FreeCAD recovery / autosave file management
# ─────────────────────────────────────────────────────────────────────────────

def _fc_user_data_dir() -> str:
    """Platform-specific FreeCAD user-data directory (no FreeCAD process needed)."""
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/FreeCAD")
    if system == "Windows":
        return os.path.join(os.environ.get("APPDATA", ""), "FreeCAD")
    # Linux / BSD
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(xdg, "FreeCAD")


def find_recovery_files(max_age_s: int = 7200) -> list:
    """Locate FreeCAD autosave / recovery files written recently.

    Returns a list of dicts sorted by age (newest first):
      {"path", "age_s", "size_bytes", "valid", "error"}

    These are the files FreeCAD tries to restore on startup.  If any are
    corrupt (failed ZIP validation), they will trigger an immediate second
    crash on startup — the "stickup" / crash-loop pattern.
    """
    data_dir = _fc_user_data_dir()
    patterns = [
        os.path.join(data_dir, "*.FCBak"),
        os.path.join(data_dir, "*.FCStd"),
        os.path.join(data_dir, "saved",  "*.FCStd"),
        os.path.join(data_dir, "saved",  "*.FCBak"),
        os.path.join(data_dir, "Backup", "*.FCStd"),
        os.path.join(data_dir, "Backup", "*.FCBak"),
    ]
    now, seen, found = time.time(), set(), []
    for pat in patterns:
        for path in glob.glob(pat):
            if path in seen:
                continue
            seen.add(path)
            age = now - os.path.getmtime(path)
            if age > max_age_s:
                continue
            v = validate_fcstd(path)
            found.append({
                "path":       path,
                "age_s":      age,
                "size_bytes": v["size_bytes"],
                "valid":      v["valid"],
                "error":      v.get("error"),
            })
    return sorted(found, key=lambda x: x["age_s"])


def clear_recovery_files(dry_run: bool = False) -> list:
    """Remove FreeCAD recovery files that fail ZIP validation.

    Only deletes files that are provably corrupt — valid FCBak/FCStd files
    are left untouched.  Call this to break the crash loop caused by FreeCAD
    trying to restore a corrupt session on every startup.

    Returns list of paths removed (or would-remove if dry_run=True).
    """
    removed = []
    for info in find_recovery_files():
        if not info["valid"]:
            if not dry_run:
                try:
                    os.unlink(info["path"])
                    removed.append(info["path"])
                except Exception:
                    pass
            else:
                removed.append(info["path"])
    return removed


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

    # ── 6. Recovery file state (crash-loop diagnosis) ─────────────────────────
    recovery_files = find_recovery_files()
    corrupt = [f for f in recovery_files if not f["valid"]]
    valid   = [f for f in recovery_files if f["valid"]]

    if recovery_files:
        parts.append("\n**FreeCAD recovery files** (files FC tries to restore on startup):")
        for f in recovery_files:
            age_str  = f"{f['age_s']:.0f}s ago"
            size_str = f"{f['size_bytes']:,} bytes"
            status   = "✓ valid" if f["valid"] else f"✗ CORRUPT — {f['error']}"
            parts.append(f"  - `{f['path']}` ({size_str}, {age_str}) — {status}")

    if corrupt:
        parts.append(
            "\n⚠️  **Crash loop likely**: FreeCAD has corrupt recovery file(s) and will "
            "crash immediately on every startup until they are removed.\n"
            "Run `freecad_crash_report.clear_recovery_files()` to remove them, "
            "then restart FreeCAD."
        )

    # ── 7. Next steps ─────────────────────────────────────────────────────────
    parts.append("\n**What to do:**")
    if proc is not None and proc.poll() is not None:
        parts.append(
            "- Headless FreeCAD has exited — call `spawn_freecad_instance` to get a fresh one\n"
            "- For heavy geometry (many booleans), consider doing operations in batches"
        )
    elif corrupt:
        parts.append(
            "- **Crash loop detected** — corrupt recovery files will crash FC on every restart\n"
            "  1. Call `clear_recovery_files()` to remove corrupt FC session files\n"
            "  2. Then relaunch FreeCAD — it will start fresh without session restore\n"
            "  3. Open your explicitly-saved FCStd file (that file is likely intact)\n"
            f"  Valid saved files you can reopen: "
            + (", ".join(f'`{v["path"]}`' for v in valid) or "(none found in recovery dir)")
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
