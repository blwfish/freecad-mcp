"""Instance discovery registry for FreeCAD MCP.

Each AICopilot instance (GUI or headless) writes a JSON discovery file at
startup and removes it at shutdown. The bridge scans this directory to find
all live FreeCAD instances, regardless of who launched them.

Both AICopilot side (this module) and bridge side (freecad_mcp_server.py)
must agree on:
  - DISCOVERY_DIR location
  - JSON schema (see write_discovery)
  - the pid-liveness fallback in scan_discovery's pruning decision (see
    is_pid_alive) -- a socket that fails a single quick connect probe is
    not proof the process is dead, only that it isn't accepting
    connections *right now*; pruning must not destroy a busy-but-alive
    instance's connectivity.
"""

__version__ = "1.1.0"

import glob
import json
import os
import socket
import sys
import time
import uuid

DISCOVERY_DIR = os.path.expanduser("~/.cache/freecad-mcp/instances")


def ensure_dir() -> str:
    os.makedirs(DISCOVERY_DIR, mode=0o700, exist_ok=True)
    return DISCOVERY_DIR


def generate_uuid() -> str:
    """Short hex UUID used as the instance identifier."""
    return uuid.uuid4().hex[:12]


def discovery_path(instance_uuid: str) -> str:
    return os.path.join(DISCOVERY_DIR, f"{instance_uuid}.json")


def default_socket_path(instance_uuid: str) -> str:
    return f"/tmp/freecad_mcp_{instance_uuid}.sock"


def write_discovery(
    instance_uuid: str,
    socket_path: str,
    *,
    gui: bool,
    label: str | None = None,
    freecad_version: str | None = None,
    freecad_binary: str | None = None,
    pid: int | None = None,
) -> str:
    """Atomically write the discovery file for this instance.

    `pid` defaults to the current process's own pid, which is always
    correct for real callers (an AICopilot instance always writes its own
    discovery record). The override exists solely so tests can simulate a
    specific — notably an already-dead — pid without duplicating this
    function's JSON-construction logic; production code should never pass
    it explicitly.

    Returns the absolute path of the written file.
    """
    ensure_dir()
    data = {
        "uuid": instance_uuid,
        "pid": pid if pid is not None else os.getpid(),
        "socket_path": socket_path,
        "gui": gui,
        "label": label or instance_uuid,
        "started_at": time.time(),
        "freecad_version": freecad_version,
        "freecad_binary": freecad_binary,
    }
    path = discovery_path(instance_uuid)
    tmp = path + ".tmp"
    # os.open with an explicit mode creates the file at 0o600 atomically —
    # no window where it briefly exists at the process umask's default
    # (typically 0o644/0o664, group/world-readable) before a later chmod()
    # tightens it. Discovery files can contain freecad_binary/socket_path,
    # so that window is real information exposure, not just cosmetic.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:  # takes ownership of fd; closes it on any exit path
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    # Belt-and-suspenders: os.replace preserves the source file's mode, so
    # this should already be a no-op — but if the destination path somehow
    # pre-existed with looser permissions from an older version of this
    # code, os.replace still adopts the SOURCE's mode on POSIX, so this
    # stays defensive rather than load-bearing.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def remove_discovery(instance_uuid: str) -> None:
    """Remove this instance's discovery file. Safe to call multiple times."""
    try:
        os.unlink(discovery_path(instance_uuid))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_socket_alive(socket_path: str, timeout: float = 0.5) -> bool:
    """Return True if a Unix socket at socket_path accepts connections."""
    if not os.path.exists(socket_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.close()
        return True
    except OSError:
        return False


def is_pid_alive(pid) -> bool:
    """Best-effort check for whether `pid` still refers to a running process.

    Returns False (i.e. "can't confirm alive") for anything that isn't a
    plausible pid -- missing/None/non-int/non-positive -- since a record
    in that shape predates this check or is otherwise malformed, not
    evidence of a genuinely live-but-busy process. That's a deliberate
    asymmetry: a confirmed-alive pid should block destructive pruning
    (see scan_discovery), but an unconfirmable one should not block it
    forever, or a record with a missing/garbled pid field would become
    permanently unprunable.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but isn't signalable by us (different user) --
        # existence is still confirmed, so treat as alive.
        return True
    except OSError:
        return False


def _is_pid_alive_windows(pid: int) -> bool:
    """Windows-specific process-existence check.

    os.kill(pid, 0) is NOT a liveness probe on Windows: signal 0 equals
    CTRL_C_EVENT, so CPython routes it through GenerateConsoleCtrlEvent,
    which requires sharing a console with the target process group and
    otherwise raises OSError regardless of whether the process is
    actually alive -- so a genuinely-alive, busy process would be
    misreported as dead here (an OSError caught by the same broad
    `except OSError: return False` used for real process-lookup
    failures), defeating the exact busy-vs-dead protection this function
    exists to provide. OpenProcess is a real existence check instead.
    """
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    return False


def scan_discovery(prune_stale: bool = True) -> list[dict]:
    """Return list of live instance discovery records.

    A record is considered live if its socket_path is connectable.  If
    prune_stale is True, records whose sockets cannot be reached are deleted
    from the discovery directory.

    Forward-compatibility note: a record that's parseable JSON but missing
    `socket_path` (e.g. a newer bridge version using a renamed key) is NOT
    deleted, even when prune_stale=True.  Mass-deleting unrecognized records
    would silently kill discovery for any future schema migration — older
    AICopilots scanning newer files would nuke them.  Instead we log a
    warning and leave the record in place so the newer process can still
    rely on it.

    A record that carries socket_path but whose socket fails to connect is
    pruned only if its pid is ALSO confirmed dead (see is_pid_alive). A
    failed connect alone is not proof of death — it just means the socket
    isn't accepting connections *right now*, which a live process can
    produce for reasons other than having exited (most notably: the GUI
    thread, and with it the accept loop, blocked for minutes by a heavy
    OCCT boolean operation — see freecad_mcp_handler.py's own GUI-thread
    heartbeat/timeout handling, built for exactly that scenario). Pruning
    on the connect probe alone would delete a live instance's discovery
    record and unlink its still-listening socket file permanently, even
    though it would have recovered on its own once the operation finished.
    """
    try:
        entries = os.listdir(DISCOVERY_DIR)
    except FileNotFoundError:
        return []

    live = []
    for name in entries:
        if not name.endswith(".json"):
            continue
        path = os.path.join(DISCOVERY_DIR, name)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # Corrupt or unreadable — drop it. Logged (not just silently
            # continue'd) so a directory full of corrupted records doesn't
            # look identical to "no live instances" with zero visibility
            # into why.
            _log_dropped_record(path, f"unreadable/corrupt JSON: {e}")
            if prune_stale:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            continue
        if not isinstance(data, dict):
            # Valid JSON but not an object (list/number/null) — not a
            # discovery record we understand. Skip without deleting or
            # crashing the rest of the scan (data.get below would raise
            # AttributeError on a list/etc otherwise, aborting discovery
            # for every other instance in this directory).
            _log_dropped_record(path, f"not a JSON object (got {type(data).__name__})")
            continue
        sock_path = data.get("socket_path")
        if sock_path is None:
            # Schema mismatch — likely a future-version record we don't
            # know how to interpret.  Don't delete; log and skip.
            _log_unknown_schema(path, data)
            continue
        if is_socket_alive(sock_path):
            live.append(data)
        elif prune_stale:
            if is_pid_alive(data.get("pid")):
                # The socket isn't accepting connections right now, but
                # the process that wrote this record is still running --
                # most likely busy with a long-running FreeCAD operation
                # (heavy OCCT booleans can block the GUI thread, and with
                # it the socket accept loop, for minutes; see
                # freecad_mcp_handler.py's own GUI-thread-heartbeat/
                # timeout handling, which exists for exactly this reason).
                # Deleting the discovery record and unlinking the live
                # socket file out from under a running process destroys
                # its connectivity permanently -- it would otherwise have
                # recovered on its own once the operation finished.
                # Confirmed 2026-09-13: a ~13-minute recompute() call
                # triggered exactly this, reported as "no live instances"
                # by the bridge, wiping the socket file mid-recompute
                # while FreeCAD was still working (and later hit a
                # "Broken pipe" trying to report success back over it).
                # Leave the record and socket file in place; just don't
                # report this instance as live for this scan.
                continue
            # Socket is dead AND the owning process is confirmed gone --
            # safe to remove both the discovery record and the orphaned
            # socket file itself. The socket file matters too: nothing
            # else will ever revisit it, since every future instance
            # picks a fresh random UUID path.
            try:
                os.unlink(path)
            except OSError:
                pass
            try:
                os.remove(sock_path)
            except OSError:
                pass
    return live


def sweep_stale_sockets(directory: str = "/tmp") -> int:
    """Remove orphaned instance socket files with no listener.

    Complements scan_discovery's pruning above. Call this once at startup
    (GUI and headless) to sweep up sockets from an instance that crashed,
    was force-killed, or exited before the GUI quit-cleanup hook existed/
    worked (see AICopilot/InitGui.py's _connect_quit_cleanup) — every
    future instance picks a fresh random UUID path, so nothing else will
    ever probe that exact path again.

    Applies the same busy-vs-dead protection as scan_discovery: a socket
    that fails a connect probe is only removed if either (a) no discovery
    record names it at all (nothing to check liveness against — the
    original "fully orphaned socket" case this function exists for), or
    (b) a discovery record does name it but the record's pid is confirmed
    dead. A socket whose owning process is still alive per a live
    discovery record — most likely busy with a long-running FreeCAD
    operation, the exact scenario scan_discovery was hardened against — is
    left in place, matching scan_discovery's behavior instead of
    destroying it out from under a running instance.

    Only matches this project's own `freecad_mcp_<uuid>.sock` naming
    (default_socket_path) — never the legacy single-instance
    `freecad_mcp.sock` path, which doesn't fit the glob and is out of
    scope here regardless.

    Returns the number of files removed.
    """
    pid_by_socket: dict[str, object] = {}
    try:
        entries = os.listdir(DISCOVERY_DIR)
    except FileNotFoundError:
        entries = []
    for name in entries:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(DISCOVERY_DIR, name)) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        sock_path = data.get("socket_path")
        if sock_path:
            pid_by_socket[sock_path] = data.get("pid")

    removed = 0
    for path in glob.glob(os.path.join(directory, "freecad_mcp_*.sock")):
        if is_socket_alive(path):
            continue
        if is_pid_alive(pid_by_socket.get(path)):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def _emit_warning(msg: str) -> None:
    """Write a one-line warning to stderr, and to FreeCAD's GUI console
    when running inside FreeCAD.  Never raises — used purely so a scan
    problem isn't *invisible*; the caller has already decided how to
    handle the record either way.
    """
    if not msg.endswith("\n"):
        msg += "\n"
    sys.stderr.write(msg)
    try:
        import FreeCAD  # type: ignore
        FreeCAD.Console.PrintWarning(msg)
    except Exception:
        pass


def _log_unknown_schema(path: str, data: dict) -> None:
    """Emit a one-line warning about a discovery record we don't understand."""
    keys = sorted(data.keys()) if isinstance(data, dict) else []
    _emit_warning(
        f"instance_registry.scan_discovery: skipping record without "
        f"socket_path: {os.path.basename(path)} (keys: {keys}). "
        f"Possibly a newer schema; record preserved."
    )


def _log_dropped_record(path: str, reason: str) -> None:
    """Emit a one-line warning about a discovery record dropped from a
    scan (unreadable/corrupt JSON, or valid JSON that isn't an object).

    Without this, a directory full of corrupted records is indistinguishable
    from "no live instances" -- scan_discovery would just return an empty
    list either way, with nothing to tell the two cases apart.
    """
    _emit_warning(
        f"instance_registry.scan_discovery: dropping {os.path.basename(path)}: {reason}."
    )
