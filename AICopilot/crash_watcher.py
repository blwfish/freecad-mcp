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
import re
import time

import tmp_safety

# Per-instance file: each FreeCAD process writes to its own path so that
# multiple concurrent instances don't clobber one another's crash context.
LAST_OP_FILE = f"/tmp/freecad_mcp_last_op_{os.getpid()}.json"
_MAX_ARG_BYTES = 1500   # truncate large args (e.g. long Python scripts)

# args (e.g. execute_python's `code`) can legitimately contain a real
# credential a user embedded to call an external service from inside
# FreeCAD. This file lives under the shared, world-writable/world-
# traversable /tmp, so its content is redacted best-effort before being
# persisted -- see _redact_secrets(). This is NOT a general-purpose
# secrets scanner: it recognizes structurally distinctive token formats
# and common key=value/key: "value" credential assignments. A bespoke
# token with no recognizable shape will still be written verbatim; the
# file's own permissions (see _open_owner_only below) are the primary
# control, this is defense in depth on top of that.
_FULL_MATCH_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                                       # AWS access key ID
    re.compile(r"ASIA[0-9A-Z]{16}"),                                       # AWS temporary access key ID
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                                 # Google API key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                                    # OpenAI-style secret key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),                             # GitHub tokens
    re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),                          # Slack tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),   # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_.=]+"),                       # Authorization: Bearer <token>
]
# key=value / key: "value" style credential assignments. Group 1 = key
# name, 2 = separator, 3 = optional quote, 4 = the secret value (redacted).
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret(?:[_-]?key)?|"
    r"password|passwd|pwd|token|auth[_-]?token|client[_-]?secret)"
    r"(\s*[:=]\s*)(['\"]?)([^\s'\",;]{6,})\3"
)
# Credentials embedded in a URL, e.g. postgres://user:hunter2@host/db.
# Group 1 = "scheme://user:", 2 = password (redacted), 3 = "@".
_URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/\s@]+:)([^@/\s]+)(@)"
)


def _redact_secrets(text: str) -> str:
    """Best-effort redaction of common secret/credential shapes.

    See the module-level comment above the pattern lists for scope and
    limits -- this narrows the exposure window, it does not guarantee no
    secret ever reaches disk.
    """
    for pattern in _FULL_MATCH_SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = _KEY_VALUE_SECRET_PATTERN.sub(r"\1\2\3[REDACTED]\3", text)
    text = _URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED]\3", text)
    return text


def _open_owner_only(path: str, flags: int) -> int:
    """Opener (for use with the builtin open()) that creates the file with
    mode 0600 -- owner read/write only -- regardless of the process umask.

    LAST_OP_FILE's fixed, predictable name sits directly under /tmp, whose
    directory permissions (1777) can't be tightened here without moving the
    file (out of scope for this fix -- see crash_watcher's read/write path,
    used as-is by the bridge-side freecad_crash_report.py). Hardening the
    file's own mode is what keeps its content -- which can include a
    redacted-but-imperfect copy of tool args -- unreadable to every other
    local account, instead of landing at the open()-default mode (typically
    0644 under a standard 022 umask).

    POSIX-only: os.open's mode argument has no meaningful effect on
    Windows, where NTFS ACLs (not Unix permission bits) govern access.
    """
    return os.open(path, flags, 0o600)


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
        s = _redact_secrets(str(v))
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
        # LAST_OP_FILE is a fixed, predictable /tmp path — refuse to
        # follow a symlink an attacker with local access could have
        # planted there before this process started. Raises straight
        # into the except below, which already handles/logs it.
        tmp_safety.refuse_if_symlink(tmp)
        # opener=_open_owner_only creates the file at mode 0600 (owner-only)
        # regardless of umask, instead of the open()-default mode (typically
        # 0644) -- /tmp is world-readable/traversable, so without this the
        # file's content is readable by any other local account.
        with open(tmp, "wb", opener=_open_owner_only) as f:
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
