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
import time
import uuid

import pytest

# Default socket for GUI-mode FreeCAD
_DEFAULT_SOCKET = os.environ.get("FREECAD_MCP_SOCKET", "/tmp/freecad_mcp.sock")

# Will be set by the session fixture — tests import this
_active_socket_path: str | None = None
_spawned_proc: subprocess.Popen | None = None


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
    """Locate FreeCADCmd binary (same logic as working_bridge.py)."""
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
    ]
    for p in mac_candidates:
        if os.path.isfile(p):
            return p

    return None


def _find_headless_script() -> str | None:
    """Locate headless_server.py (same logic as working_bridge.py)."""
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

    proc = subprocess.Popen(
        [freecadcmd, headless_script, "--socket-path", sock_path],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Poll for readiness
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"FreeCADCmd exited prematurely with code {proc.returncode}"
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
        proc.wait(timeout=5)
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
