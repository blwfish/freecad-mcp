"""
Integration test configuration — auto-connects to GUI FreeCAD or spawns headless.

Priority:
  1. Connect to existing FreeCAD at FREECAD_MCP_SOCKET (default /tmp/freecad_mcp.sock)
  2. If no socket, spawn a headless FreeCADCmd instance on a unique socket

The active socket path is stored in the module-level SOCKET_PATH variable,
which test_e2e_workflows.py imports.
"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest

# Default socket for GUI-mode FreeCAD
_DEFAULT_SOCKET = os.environ.get("FREECAD_MCP_SOCKET", "/tmp/freecad_mcp.sock")

# Will be set by the session fixture — tests import this
_active_socket_path: str | None = None
_spawned_proc: subprocess.Popen | None = None
_spawned_stdout_drain: "_PipeDrain | None" = None
_spawned_stderr_drain: "_PipeDrain | None" = None


class _PipeDrain:
    """Continuously drain a subprocess pipe into a bounded in-memory
    buffer, so a high-volume writer on the other end can never fill the
    OS pipe buffer and block forever on write().

    This is the actual, confirmed root cause of the 1.1-stable CI hangs
    (see CLAUDE.md's Known Issues) -- live gdb thread dump (2026-08-21,
    CI run 32462244329) caught a "freecadcmd" thread stuck in a raw
    write(fd=1, ...) from Base::SequencerLauncher::next(), FreeCAD's own
    console progress-bar output ("\\t\\t\\t(66 %)\\t\\r...") during
    recompute(). _spawn_headless() pipes stdout/stderr
    (stdout=subprocess.PIPE) but nothing ever read them while tests ran
    -- only diagnose_dead_spawned_process() did, via proc.communicate(),
    and only after the process had already died. Once cumulative
    progress-tick output across ~250+ recomputes filled the 64KB pipe
    buffer, that write() blocked forever: the process couldn't die (it
    was stuck on the write, not exiting), so nothing would ever read and
    unblock it either -- every later socket request piled up behind the
    same permanently-stuck thread. A background reader that drains as
    output arrives, same as subprocess.communicate() does internally via
    select/poll, just running for the process's whole lifetime instead of
    a one-shot call at the end.
    """

    def __init__(self, pipe, max_bytes: int = 200_000):
        self._buf = bytearray()
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, args=(pipe,), daemon=True)
        self._thread.start()

    def _run(self, pipe) -> None:
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                with self._lock:
                    self._buf.extend(chunk)
                    overflow = len(self._buf) - self._max_bytes
                    if overflow > 0:
                        del self._buf[:overflow]
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        with self._lock:
            return bytes(self._buf).decode("utf-8", errors="replace")

    def join(self, timeout: float = 1.0) -> None:
        self._thread.join(timeout=timeout)


def _socket_responds(path: str, timeout: float = 2.0) -> bool:
    """Try connecting to a Unix socket. Returns True if it accepts."""
    if not os.path.exists(path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path)
        s.close()
        return True
    except (socket.error, OSError):
        return False


def _find_freecadcmd() -> str | None:
    """Locate FreeCADCmd binary (same logic as freecad_mcp_server.py)."""
    override = os.environ.get("FREECAD_MCP_FREECAD_BIN")
    if override and os.path.isfile(override):
        return override

    for name in ("FreeCADCmd", "freecadcmd", "FreeCAD", "freecad"):
        path = shutil.which(name)
        if path:
            return path

    mac_candidates = [
        "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.0.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.1.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 1.2.app/Contents/MacOS/FreeCADCmd",
        # Weekly / renumbered (26.x) builds ship under these bundle names
        "/Applications/FreeCAD weekly-builds.app/Contents/MacOS/FreeCADCmd",
        "/Applications/FreeCAD 26.app/Contents/MacOS/FreeCADCmd",
        # Local build (FC-clone) -- kept in sync with freecad_mcp_server.py's
        # own _find_freecadcmd(); this repo's dev environment builds FreeCAD
        # here rather than installing an /Applications bundle.
        os.path.expanduser("~/Documents/FC-clone/build/release/bin/FreeCADCmd"),
        "/Volumes/Files/claude/FC-clone/build/release/bin/FreeCADCmd",
    ]
    for p in mac_candidates:
        if os.path.isfile(p):
            return p

    return None


def _find_headless_script() -> str | None:
    """Locate headless_server.py (same logic as freecad_mcp_server.py)."""
    override_dir = os.environ.get("FREECAD_MCP_MODULE_DIR")
    if override_dir:
        p = os.path.join(override_dir, "headless_server.py")
        if os.path.isfile(p):
            return p

    # Relative to this repo
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(repo_root, "AICopilot", "headless_server.py"),
        os.path.expanduser("~/.freecad-mcp/AICopilot/headless_server.py"),
        "/Volumes/Files/claude/FreeCAD-prefs/Mod/AICopilot/headless_server.py",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p

    return None


def _spawn_headless(timeout: float = 30.0) -> tuple[subprocess.Popen, str]:
    """Spawn a headless FreeCADCmd and wait for its socket.

    Returns (process, socket_path) or raises RuntimeError.
    """
    freecadcmd = _find_freecadcmd()
    if not freecadcmd:
        raise RuntimeError(
            "Cannot find FreeCADCmd binary. "
            "Set FREECAD_MCP_FREECAD_BIN env var to its path."
        )

    headless_script = _find_headless_script()
    if not headless_script:
        raise RuntimeError(
            "Cannot find headless_server.py. "
            "Set FREECAD_MCP_MODULE_DIR env var or deploy AICopilot."
        )

    sock_path = f"/tmp/freecad_mcp_test_{uuid.uuid4().hex[:8]}.sock"

    env = os.environ.copy()
    env["FREECAD_MCP_SOCKET"] = sock_path

    # Pass socket path via env var only — some FreeCADCmd builds (e.g. AppImage)
    # reject unknown CLI flags before the script can parse them.
    #
    # When the user has an installed AICopilot addon (e.g. local dev
    # workstation), FreeCAD's addon loader puts that copy on sys.path before
    # our --module-path arg can take effect, and `from freecad_mcp_handler
    # import ...` inside the script ends up with the installed code rather
    # than the worktree's. Disabling the installed addon and explicitly
    # adding the worktree's AICopilot via -M keeps the wiring honest. The
    # script also self-installs at sys.path[0] as a belt-and-braces measure.
    aicopilot_dir = os.path.dirname(os.path.abspath(headless_script))
    extra_flags = ["--disable-addon", "AICopilot", "-M", aicopilot_dir]

    cmd = [freecadcmd, *extra_flags, headless_script]

    if os.environ.get("FREECAD_MCP_TEST_GDB_TRAP"):
        # Debugging aid for the CAM create_tool SIGSEGV (KNOWN_ISSUES.md):
        # FreeCAD installs its own SIGSEGV handler that prints a one-frame,
        # symbol-less backtrace and exits cleanly (returncode 1, not a
        # signal death) -- the OS never sees an *unhandled* fatal signal,
        # so ulimit -c / core_pattern never produce a core file to inspect
        # afterward. Running under gdb intercepts the signal before
        # FreeCAD's own handler gets it (gdb's default signal disposition
        # is to stop first), so a real multi-frame, per-thread backtrace
        # can be captured at the actual fault site instead.
        #
        # gdb's `--args` loads its first argument as an executable image
        # for symbols -- it can't do that for a shebang script (the
        # freecadcmd-wrapper.sh CI writes, or any local equivalent), only
        # for a real binary. Detect a shebang and launch the interpreter
        # explicitly instead; `follow-exec-mode same` then follows the
        # wrapper's own `exec` (and any further re-exec, e.g. AppRun into
        # the real freecadcmd ELF) as ordinary exec() events in one gdb
        # session, all the way to the binary that actually segfaults.
        gdb_target = list(cmd)
        try:
            with open(cmd[0], "rb") as f:
                is_script = f.read(2) == b"#!"
            if is_script:
                with open(cmd[0]) as f:
                    interpreter = f.readline().strip()[2:].split()
                gdb_target = [*interpreter, *cmd]
        except OSError:
            pass
        cmd = [
            # Iteration history (see KNOWN_ISSUES.md): "thread apply all bt
            # full" with no time bound hung the whole run (~81s, process
            # never registered as dead). Passing everything but SIGSEGV
            # through ("handle all nostop noprint pass" / "handle SIGSEGV
            # stop print nopass") stopped a repeat of the wrong-signal
            # theory, but "thread apply all bt" (even without locals) still
            # ran past a 45s timeout with only a single idle thread's
            # frames captured -- FreeCAD apparently has enough threads (or
            # poor-enough unwind info) that enumerating *all* of them is
            # itself slow. Drop "thread apply all" entirely: "info program"
            # confirms *why* gdb actually stopped (in case it's still
            # stopping on the wrong thing) and "bt full" backtraces just
            # the one thread gdb auto-selects -- the one that received the
            # signal -- which is the only thread that actually matters
            # here. The "===GDB-STOPPED===" echo is an unambiguous anchor
            # for diagnose_dead_spawned_process() to search from, instead
            # of guessing from gdb's own free-text output.
            "timeout", "-s", "KILL", "90",
            "gdb", "-batch",
            "-ex", "set follow-exec-mode same",
            "-ex", "handle all nostop noprint pass",
            "-ex", "handle SIGSEGV stop print nopass",
            "-ex", "run",
            "-ex", "echo \\n===GDB-STOPPED===\\n",
            "-ex", "info program",
            "-ex", "bt full",
            "-ex", "quit",
            "--args", *gdb_target,
        ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    # Drain immediately, not just on death -- see _PipeDrain's docstring
    # for why an undrained pipe deadlocks the whole process eventually.
    global _spawned_stdout_drain, _spawned_stderr_drain
    _spawned_stdout_drain = _PipeDrain(proc.stdout)
    _spawned_stderr_drain = _PipeDrain(proc.stderr)

    # Poll for readiness
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"FreeCADCmd exited prematurely with code {proc.returncode}\n"
                f"  cmd: {cmd}\n"
                f"  socket: {sock_path}\n"
                f"  stdout: {_spawned_stdout_drain.text()[-500:]}\n"
                f"  stderr: {_spawned_stderr_drain.text()[-500:]}"
            )
        if _socket_responds(sock_path):
            return proc, sock_path
        time.sleep(0.5)

    proc.kill()
    raise RuntimeError(
        f"Headless FreeCAD did not become ready within {timeout}s "
        f"(socket: {sock_path})"
    )


def _stop_headless(proc: subprocess.Popen, sock_path: str):
    """Gracefully stop a headless FreeCAD instance."""
    try:
        proc.terminate()
        # This fixture is session-scoped -- this wait runs once per CI job,
        # not once per test -- and wait() returns the instant the process
        # actually exits, not after the full timeout. On a healthy process
        # SIGTERM is near-instant regardless of this ceiling; the ceiling
        # only matters when FREECAD_MCP_TEST_GDB_TRAP is active and gdb is
        # still mid-"bt full" (KNOWN_ISSUES.md's CAM SIGSEGV investigation
        # hit exactly this: a 5s ceiling killed gdb before it could print
        # anything, discarding the diagnostic). Set above gdb's own
        # `timeout -s KILL 90` backstop so we never truncate a real
        # backtrace; proc.kill() below is still the final backstop for a
        # genuine hang.
        proc.wait(timeout=95)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass

    # Clean up socket file
    try:
        if os.path.exists(sock_path):
            os.remove(sock_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Session-scoped fixture: ensure a FreeCAD instance is available
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def freecad_instance():
    """Provide a FreeCAD socket for integration tests.

    Strategy:
      1. If a GUI FreeCAD is already running (socket responds), use it.
      2. Otherwise, spawn a headless FreeCADCmd instance.
      3. On teardown, stop the headless instance if we spawned one.
    """
    global _active_socket_path, _spawned_proc

    # Mode 1: Try existing GUI FreeCAD
    if _socket_responds(_DEFAULT_SOCKET):
        _active_socket_path = _DEFAULT_SOCKET
        yield {"mode": "gui", "socket_path": _DEFAULT_SOCKET}
        return

    # Mode 2: Spawn headless
    try:
        proc, sock_path = _spawn_headless()
    except RuntimeError as e:
        pytest.skip(str(e))
        return

    _active_socket_path = sock_path
    _spawned_proc = proc

    yield {
        "mode": "headless",
        "socket_path": sock_path,
        "pid": proc.pid,
    }

    # Teardown
    _stop_headless(proc, sock_path)
    _active_socket_path = None
    _spawned_proc = None


def get_socket_path() -> str:
    """Return the active FreeCAD socket path. Called by test modules."""
    return _active_socket_path or _DEFAULT_SOCKET


_death_diagnostics: str | None = None
_hang_diagnostics: str | None = None

# gdb's crash backtrace (KNOWN_ISSUES.md's create_tool SIGSEGV) starts at
# one of these; a plain trailing slice missed it entirely in an earlier
# iteration (a deep CPython eval-loop stack from a single "bt full" ran the
# total past a 2000-char tail, keeping only the *outer* frames near main()
# and losing the actual fault site at frame #0). "===GDB-STOPPED===" is our
# own unambiguous echo'd marker (see _spawn_headless's gdb-trap branch);
# the rest are fallbacks for gdb's own free-text output.
_CRASH_ANCHORS = ("===GDB-STOPPED===", "Program received signal", "Thread 1 ", "#0 ")


def _extract_relevant(text: str, max_chars: int = 30000) -> str:
    for anchor in _CRASH_ANCHORS:
        idx = text.find(anchor)
        if idx != -1:
            return text[idx : idx + max_chars]
    return text[-max_chars:]


def _find_descendant_pids(root_pid: int) -> list[int]:
    """Return every live descendant of root_pid, via /proc/<pid>/task/<pid>/children.

    The Popen'd process is the AppImage wrapper script (freecadcmd-wrapper.sh
    -> AppRun), NOT the real FreeCAD binary -- confirmed live (2026-08-21):
    a first version of this tool that only attached to root_pid dumped a
    thread named "AppRun" sitting in a completely ordinary bash wait4()
    for its child (wait_for/execute_command/reader_loop -- bash's own
    interpreter loop, ../sysdeps wait4.c), telling us nothing about the
    real hang. AppRun forks and waits rather than exec'ing into the real
    binary (if it exec'd, it would keep the same pid and this walk would
    be unnecessary) -- so the process actually holding whatever lock is
    deadlocked lives one or more fork() hops below root_pid. Returns [] on
    any /proc access failure (e.g. not Linux) rather than raising --
    caller falls back to dumping root_pid alone in that case.
    """
    descendants: list[int] = []
    frontier = [root_pid]
    seen = {root_pid}
    while frontier:
        pid = frontier.pop()
        try:
            with open(f"/proc/{pid}/task/{pid}/children") as f:
                children = [int(p) for p in f.read().split()]
        except (OSError, ValueError):
            continue
        for child in children:
            if child not in seen:
                seen.add(child)
                descendants.append(child)
                frontier.append(child)
    return descendants


def _gdb_attach_dump(root_pid: int, run_timeout: int = 25) -> str:
    """Attach gdb to a live (presumably hung) process tree and dump every
    thread's backtrace from every descendant, then detach without killing
    any of them.

    This is deliberately reactive -- attach only once a real timeout has
    already happened -- not the whole-session gdb-wrap the CAM SIGSEGV
    investigation used (FREECAD_MCP_TEST_GDB_TRAP): that approach is known
    to introduce its own ~20s stall mid-run (see integration-tests.yml's
    comment on why that flag is off for the routine suite). A hang has no
    signal to catch anyway -- there's nothing for a whole-session wrapper
    to intercept -- so a live `gdb -p <pid>` attach at the moment of
    failure is the only way to see what every thread is actually blocked
    on. "thread apply all bt" (no "full") mirrors the SIGSEGV trap's own
    lesson that dumping locals across many/deep-stacked threads can itself
    run long; if this needs revisiting, check that comment first.

    root_pid itself is the AppImage wrapper, not the real binary (see
    _find_descendant_pids) -- dump every live descendant too, since we
    don't know in advance how many fork hops separate the wrapper from
    the real FreeCAD process, or whether more than one of them matters.

    Best-effort and silent on any failure (gdb missing, ptrace denied,
    sudo not configured passwordless) -- this only ever runs after a test
    has already failed, so it must never raise or itself hang the run.
    Requires sudo: gdb is a fresh sibling process, not an ancestor of the
    FreeCAD process, so default Yama ptrace_scope=1 (Ubuntu's default)
    denies a plain attach.
    """
    if not shutil.which("gdb"):
        return "(gdb not available -- skipping live thread dump)"
    targets = [root_pid] + _find_descendant_pids(root_pid)
    chunks = []
    for pid in targets:
        cmd = [
            "timeout", "-s", "KILL", str(run_timeout),
            "sudo", "-n", "gdb", "-p", str(pid), "-batch",
            "-ex", "echo \\n===GDB-ATTACHED===\\n",
            "-ex", "thread apply all bt",
            "-ex", "detach",
            "-ex", "quit",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=run_timeout + 5,
            )
            chunks.append(
                f"--- gdb live thread dump (pid={pid}, rc={result.returncode}) ---\n"
                f"{result.stdout}\n{result.stderr}"
            )
        except Exception as e:
            chunks.append(f"(gdb attach to pid={pid} failed: {e})")
    return "\n".join(chunks)


def diagnose_dead_spawned_process() -> str:
    """If we spawned a headless instance, return a diagnostic string:
    exit code + captured stdout/stderr if it died, or a live gdb thread
    dump if it's still running (hung rather than crashed).

    _PipeDrain threads (started in _spawn_headless) continuously drain
    _spawned_proc's stdout/stderr while tests run -- both because that's
    what prevents the pipe-full deadlock (see _PipeDrain's docstring) and
    as a side benefit, it means a mid-run crash's output is already
    captured here rather than sitting unread in a pipe buffer. Call this
    from a connection-failure or timeout handler to surface it.

    Both branches' results are cached after the first call -- every later
    cascading failure in the same session reuses the cached diagnostic
    instead of re-attaching gdb or re-reading the drains.
    """
    global _death_diagnostics, _hang_diagnostics
    if _death_diagnostics is not None:
        return _death_diagnostics
    if _hang_diagnostics is not None:
        return _hang_diagnostics
    proc = _spawned_proc
    if proc is None:
        return ""
    if proc.poll() is None:
        _hang_diagnostics = (
            f"\nSpawned FreeCAD process (pid={proc.pid}) is still alive but "
            f"unresponsive -- likely hung/deadlocked, not crashed.\n"
            f"{_gdb_attach_dump(proc.pid)}"
        )
        return _hang_diagnostics
    if _spawned_stdout_drain is not None:
        _spawned_stdout_drain.join(timeout=1.0)
    if _spawned_stderr_drain is not None:
        _spawned_stderr_drain.join(timeout=1.0)
    stdout = _spawned_stdout_drain.text() if _spawned_stdout_drain else ""
    stderr = _spawned_stderr_drain.text() if _spawned_stderr_drain else ""
    _death_diagnostics = (
        f"\nSpawned FreeCAD process died unexpectedly: returncode={proc.returncode}\n"
        f"--- stdout (from crash, if found, else tail) ---\n{_extract_relevant(stdout)}\n"
        f"--- stderr (from crash, if found, else tail) ---\n{_extract_relevant(stderr)}\n"
    )
    return _death_diagnostics
